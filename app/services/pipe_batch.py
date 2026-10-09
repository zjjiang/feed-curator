"""批量拉取:后台线程顺序执行选中管道的采集(类似 yum update)。

单飞锁防重入;每个管道一个独立 session,单管失败记 last_error 并继续。
结果照常落 run_log(kind='fetch'),不额外记总账。
"""

import threading

from app.db import SessionLocal
from app.jobs.fetcher import fetch_source
from app.models import Pipe

_BATCH_LOCK = threading.Lock()


def start_batch_fetch(pipe_ids: list[int]) -> str:
    """返回 'started' | 'busy' | 'empty'。"""
    ids = [int(i) for i in pipe_ids if i]
    if not ids:
        return "empty"
    if not _BATCH_LOCK.acquire(blocking=False):
        return "busy"

    def worker():
        try:
            run_batch_fetch(ids)
        finally:
            _BATCH_LOCK.release()

    threading.Thread(target=worker, daemon=True, name="pipe-batch").start()
    return "started"


def run_batch_fetch(pipe_ids: list[int]) -> dict:
    """顺序拉取;每管独立 session,失败隔离。"""
    ok = failed = 0
    for pid in pipe_ids:
        db = SessionLocal()
        try:
            pipe = db.get(Pipe, pid)
            if pipe is None:
                continue
            try:
                fetch_source(db, pipe, trigger="manual")
                ok += 1
            except Exception as e:  # noqa: BLE001 — 单管失败不中断批次
                failed += 1
                pipe.last_error = f"{type(e).__name__}: {e}"[:500]
                db.commit()
        finally:
            db.close()
    return {"ok": ok, "failed": failed}
