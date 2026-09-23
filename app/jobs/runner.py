"""异步 AI 判定任务执行器(记录进 run_log kind='analyze')。

沿用生产验证过的并发模型(design 决策 8):
- 全局单任务:内存锁 + DB 状态双保险,同一时间只跑一个判定任务;
- 线程池并发:MAX_WORKERS 个 worker 并行调 LLM,每个 worker 独立 session;
- 进度可见:每篇完成即刷新 run_log 计数,前端轮询可见;
- 取消:threading.Event,已完成结果保留,未开始的跳过;
- 启动清僵尸:init_db 把残留 running 的判定任务标为 failed。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.ai import analyzer
from app.db import SessionLocal
from app.models import RunLog

MAX_WORKERS = 5

_start_lock = threading.Lock()
_cancel_flags: dict[int, threading.Event] = {}


def get_active_run_id() -> int | None:
    """返回正在跑的判定任务 id(run_log 视角),没有则 None。"""
    db = SessionLocal()
    try:
        run = (
            db.query(RunLog)
            .filter(RunLog.kind == "analyze", RunLog.status == "running")
            .order_by(RunLog.id.desc())
            .first()
        )
        return run.id if run else None
    finally:
        db.close()


def start_analyze_job(trigger: str = "manual", force_all: bool = False) -> tuple[int | None, bool]:
    """启动判定任务。返回 (run_id, created);已有任务在跑则复用。

    没有待判定文档时返回 (None, False)。
    """
    with _start_lock:
        existing = get_active_run_id()
        if existing is not None:
            return existing, False

        db = SessionLocal()
        try:
            domains = analyzer.load_domains(db)
            doc_ids = analyzer.select_doc_ids(db, force_all=force_all)
            if not doc_ids:
                return None, False
            run = RunLog(kind="analyze", trigger=trigger, status="running",
                         total=len(doc_ids), created_at=int(time.time()))
            db.add(run)
            db.commit()
            run_id = run.id
        finally:
            db.close()

        cancel_flag = threading.Event()
        _cancel_flags[run_id] = cancel_flag
        worker = threading.Thread(
            target=_run_analyze, args=(run_id, doc_ids, cancel_flag, domains),
            daemon=True,
        )
        worker.start()
        return run_id, True


def cancel_analyze(run_id: int) -> bool:
    """请求取消。已完成文档的结果保留,未开始的不再处理。"""
    flag = _cancel_flags.get(run_id)
    if flag is None:
        return False
    flag.set()
    return True


def _run_analyze(run_id: int, doc_ids: list[int], cancel_flag: threading.Event,
                 domains: list[dict]):
    from app.main import _get_llm

    llm = _get_llm()
    if llm is None:
        _finalize(run_id, status="failed", error="未配置 DEEPSEEK_API_KEY")
        _cancel_flags.pop(run_id, None)
        return

    counter_lock = threading.Lock()
    state = {"processed": 0, "succeeded": 0, "failed": 0}

    def handle(doc_id: int):
        if cancel_flag.is_set():
            return
        ok = _analyze_one(llm, doc_id, domains)
        with counter_lock:
            state["processed"] += 1
            state["succeeded" if ok else "failed"] += 1
            snapshot = dict(state)
        _update_progress(run_id, snapshot)

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(handle, d) for d in doc_ids]
            for f in futures:
                f.result()
    except Exception as e:  # noqa: BLE001
        _finalize(run_id, status="failed", error=f"{type(e).__name__}: {e}",
                  counts=state)
        _cancel_flags.pop(run_id, None)
        return

    final_status = "cancelled" if cancel_flag.is_set() else "done"
    _finalize(run_id, status=final_status, counts=state)
    _cancel_flags.pop(run_id, None)


def _analyze_one(llm, doc_id: int, domains: list[dict]) -> bool:
    """单篇判定,worker 用独立 session。"""
    db = SessionLocal()
    try:
        return analyzer.analyze_doc(db, llm, doc_id, domains)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"[analyze] doc {doc_id} 判定异常: {type(e).__name__}: {e}")
        return False
    finally:
        db.close()


def _update_progress(run_id: int, counts: dict) -> None:
    db = SessionLocal()
    try:
        db.query(RunLog).filter(RunLog.id == run_id).update({
            RunLog.processed: counts["processed"],
            RunLog.succeeded: counts["succeeded"],
            RunLog.failed: counts["failed"],
        })
        db.commit()
    finally:
        db.close()


def _finalize(run_id: int, status: str, error: str | None = None,
              counts: dict | None = None) -> None:
    db = SessionLocal()
    try:
        values = {RunLog.status: status, RunLog.finished_at: int(time.time())}
        if error:
            values[RunLog.error] = error
        if counts:
            values[RunLog.processed] = counts["processed"]
            values[RunLog.succeeded] = counts["succeeded"]
            values[RunLog.failed] = counts["failed"]
        db.query(RunLog).filter(RunLog.id == run_id).update(values)
        db.commit()
    finally:
        db.close()
