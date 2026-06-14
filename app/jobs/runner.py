"""异步 AI 处理任务执行器。

- 全局单任务:同一时间只允许一个处理任务在跑(内存锁 + DB 状态双保险)。
- 线程池并发:默认 5 个 worker 并发调 LLM 处理文章。
- 进度可见:每处理完一篇就更新 Job 的计数,前端轮询 /api/jobs/{id} 即可看进度。
- 启动即返回:start_process_job 把活儿丢给后台线程,不阻塞调用方(API/调度器)。
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.db import SessionLocal
from app.models import Item, Job
from app.ai.scorer import get_categories

MAX_WORKERS = 5

# 全局单任务锁:保护"是否已有任务在跑"的判断与建任务这一段临界区。
_start_lock = threading.Lock()
# 当前活跃任务的取消标志,key=job_id -> threading.Event
_cancel_flags: dict[int, threading.Event] = {}


def get_active_job_id() -> int | None:
    """返回当前正在跑的任务 id(DB 视角),没有则 None。"""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.status == "running").order_by(Job.id.desc()).first()
        return job.id if job else None
    finally:
        db.close()


def start_process_job(trigger: str = "manual") -> tuple[int | None, bool]:
    """启动一个处理任务。

    返回 (job_id, created):
      created=True  表示新建并启动了任务;
      created=False 表示已有任务在跑,job_id 是那个在跑的(复用,不新建)。
    若没有待处理文章,返回 (None, False)。
    """
    with _start_lock:
        existing = get_active_job_id()
        if existing is not None:
            return existing, False

        db = SessionLocal()
        try:
            pending = (
                db.query(Item.id)
                .filter(Item.ai_score.is_(None))
                .filter(Item.content_text.isnot(None))
                .filter(Item.content_text != "")
                .all()
            )
            item_ids = [r[0] for r in pending]
            if not item_ids:
                return None, False

            job = Job(
                status="running",
                trigger=trigger,
                total=len(item_ids),
                processed=0,
                succeeded=0,
                failed=0,
                created_at=int(time.time()),
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = job.id
        finally:
            db.close()

        cancel_flag = threading.Event()
        _cancel_flags[job_id] = cancel_flag
        worker = threading.Thread(
            target=_run_job, args=(job_id, item_ids, cancel_flag), daemon=True
        )
        worker.start()
        return job_id, True


def cancel_job(job_id: int) -> bool:
    """请求取消一个正在跑的任务。已处理的文章保留,未处理的不再处理。"""
    flag = _cancel_flags.get(job_id)
    if flag is None:
        return False
    flag.set()
    return True


def _run_job(job_id: int, item_ids: list[int], cancel_flag: threading.Event):
    """后台线程:用线程池并发处理 item_ids,持续更新 Job 进度。"""
    from app.main import _get_llm

    llm = _get_llm()
    if llm is None:
        _finalize(job_id, status="failed", error="未配置 DEEPSEEK_API_KEY")
        _cancel_flags.pop(job_id, None)
        return

    db = SessionLocal()
    try:
        categories = get_categories(db)
    finally:
        db.close()

    # 进度计数器(内存),由锁保护;定期 flush 到 DB
    counter_lock = threading.Lock()
    state = {"processed": 0, "succeeded": 0, "failed": 0}

    def handle(item_id: int):
        if cancel_flag.is_set():
            return
        ok = _process_one(llm, item_id, categories)
        with counter_lock:
            state["processed"] += 1
            if ok:
                state["succeeded"] += 1
            else:
                state["failed"] += 1
            snapshot = dict(state)
        _update_progress(job_id, snapshot)

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(handle, iid) for iid in item_ids]
            for f in futures:
                f.result()
    except Exception as e:
        _finalize(job_id, status="failed", error=f"{type(e).__name__}: {e}", counts=state)
        _cancel_flags.pop(job_id, None)
        return

    final_status = "cancelled" if cancel_flag.is_set() else "done"
    _finalize(job_id, status=final_status, counts=state)
    _cancel_flags.pop(job_id, None)


def _process_one(llm, item_id: int, categories) -> bool:
    """处理单篇文章,各 worker 用独立 session 避免 SQLite 跨线程冲突。"""
    db = SessionLocal()
    try:
        item = db.query(Item).filter(Item.id == item_id).first()
        if item is None or item.ai_score is not None:
            return False  # 已被处理或不存在,跳过

        result = llm.process_article(
            title=item.title,
            description=item.description or "",
            content_preview=item.content_text or "",
            categories=categories,
        )
        if result:
            item.ai_score = result["stars"]
            item.ai_summary = result["summary"]
            item.ai_keypoints = json.dumps(result["keypoints"], ensure_ascii=False)
            item.ai_tags = json.dumps(result["categories"], ensure_ascii=False)
            item.ai_scored_at = int(time.time())
            db.commit()
            return True
        else:
            item.ai_score = -1
            item.ai_scored_at = int(time.time())
            item.ai_summary = "处理失败"
            db.commit()
            return False
    except Exception as e:
        db.rollback()
        print(f"[job] item {item_id} 处理异常: {type(e).__name__}: {e}")
        return False
    finally:
        db.close()


def _update_progress(job_id: int, counts: dict):
    db = SessionLocal()
    try:
        db.query(Job).filter(Job.id == job_id).update({
            Job.processed: counts["processed"],
            Job.succeeded: counts["succeeded"],
            Job.failed: counts["failed"],
        })
        db.commit()
    finally:
        db.close()


def _finalize(job_id: int, status: str, error: str | None = None, counts: dict | None = None):
    db = SessionLocal()
    try:
        values = {Job.status: status, Job.finished_at: int(time.time())}
        if error:
            values[Job.error] = error
        if counts:
            values[Job.processed] = counts["processed"]
            values[Job.succeeded] = counts["succeeded"]
            values[Job.failed] = counts["failed"]
        db.query(Job).filter(Job.id == job_id).update(values)
        db.commit()
    finally:
        db.close()
