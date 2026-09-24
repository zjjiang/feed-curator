"""库内仓库星标刷新:每日一轮,计算相对上次刷新的增量。

检索接口查不了「近期涨星」,只能对库内仓库本地采样。刷新走
writer.refresh_repo 单一写入口,不触碰判定结果;run_log kind='refresh'
供运维看板。收到限流即停轮(剩余仓库下轮再续)。无凭据时单轮上限
DEFAULT_BATCH_LIMIT 个,按最久未刷新优先轮转,多轮逐步覆盖全部仓库。
"""

import os
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Repo, RunLog
from app.services.github_client import GitHubClient, RateLimitError
from app.writer import refresh_repo

REFRESH_INTERVAL_SECONDS = 24 * 3600
DEFAULT_BATCH_LIMIT = 50

_refresh_lock = threading.Lock()


def _token() -> str | None:
    return (os.environ.get("GITHUB_TOKEN") or "").strip() or None


def refresh_due(db: Session, *, interval_seconds: int = REFRESH_INTERVAL_SECONDS,
                now: int | None = None) -> bool:
    """距上次完成的刷新超过周期(或从未跑过),且当前没有刷新在跑。"""
    now = now or int(time.time())
    running = db.query(RunLog).filter(
        RunLog.kind == "refresh", RunLog.status == "running").first()
    if running is not None:
        return False
    last = (
        db.query(func.max(RunLog.created_at))
        .filter(RunLog.kind == "refresh", RunLog.status == "done")
        .scalar()
    )
    return last is None or now - last >= interval_seconds


def repo_ids_for_refresh(db: Session, limit: int | None = None) -> list[int]:
    """待刷新 repo id:最久未刷新(NULL 视为最久)优先。"""
    q = db.query(Repo.id).order_by(
        func.coalesce(Repo.refreshed_at, 0).asc(), Repo.id.asc())
    if limit:
        q = q.limit(limit)
    return [r[0] for r in q.all()]


def _parse_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def run_refresh(db: Session | None = None, *,
                client: GitHubClient | None = None,
                sleep_seconds: float = 0.5,
                limit: int | None = None,
                trigger: str = "auto",
                log=print) -> dict:
    """跑一轮刷新。无凭据时默认单轮 DEFAULT_BATCH_LIMIT 个。返回统计。"""
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        if limit is None:
            limit = None if _token() else DEFAULT_BATCH_LIMIT
        ids = repo_ids_for_refresh(db, limit)
        run = RunLog(kind="refresh", pipe_name="星标刷新", trigger=trigger,
                     status="running", total=len(ids), created_at=int(time.time()))
        db.add(run)
        db.commit()

        stats = {"total": len(ids), "succeeded": 0, "failed": 0, "skipped": 0}
        owns_client = client is None
        client = client or GitHubClient()
        try:
            for i, doc_id in enumerate(ids, 1):
                repo = db.get(Repo, doc_id)
                if repo is None or not repo.owner or not repo.name:
                    stats["skipped"] += 1
                else:
                    try:
                        _refresh_one(db, client, doc_id, repo)
                        stats["succeeded"] += 1
                    except RateLimitError as e:
                        stats["failed"] += 1
                        log(f"[repo-refresh] 限流,本轮提前结束: {e}")
                        db.rollback()
                        db.query(RunLog).filter(RunLog.id == run.id).update({
                            RunLog.processed: i,
                            RunLog.succeeded: stats["succeeded"],
                            RunLog.failed: stats["failed"],
                            RunLog.error: f"限流,剩余仓库下轮再续: {e}",
                        })
                        db.commit()
                        break
                    except Exception as e:  # noqa: BLE001 — 单仓库失败不阻塞
                        stats["failed"] += 1
                        log(f"[repo-refresh] #{doc_id} 失败: "
                            f"{type(e).__name__}: {e}")
                        db.rollback()
                db.query(RunLog).filter(RunLog.id == run.id).update({
                    RunLog.processed: i,
                    RunLog.succeeded: stats["succeeded"],
                    RunLog.failed: stats["failed"],
                })
                db.commit()
                if sleep_seconds and i < len(ids):
                    time.sleep(sleep_seconds)
        finally:
            if owns_client:
                client.close()

        db.query(RunLog).filter(RunLog.id == run.id).update({
            RunLog.status: "done",
            RunLog.finished_at: int(time.time()),
        })
        db.commit()
        return stats
    finally:
        if owns_db:
            db.close()


def _refresh_one(db: Session, client: GitHubClient, doc_id: int, repo: Repo) -> None:
    data = client.get_repo(repo.owner, repo.name)
    stars = data.get("stargazers_count")
    kwargs: dict = {
        "stars": stars,
        "forks": data.get("forks_count"),
        "open_issues": data.get("open_issues_count"),
        "pushed_at": _parse_epoch(data.get("pushed_at")),
    }
    if repo.stars is not None and stars is not None:
        kwargs["stars_prev"] = repo.stars
        kwargs["stars_gained"] = stars - repo.stars
    refresh_repo(db, doc_id, **kwargs)


def maybe_start_refresh(trigger: str = "auto") -> bool:
    """到期则在后台线程启动一轮刷新;已有刷新在跑或未到期时不启动。

    进程内锁 + run_log 状态双保险(与判定作业同风格)。返回是否启动。
    """
    if not _refresh_lock.acquire(blocking=False):
        return False
    db = SessionLocal()
    try:
        if not refresh_due(db):
            _refresh_lock.release()
            return False
    except Exception:  # noqa: BLE001 — 检查失败也要放锁
        _refresh_lock.release()
        raise
    finally:
        db.close()

    def worker():
        try:
            run_refresh(trigger=trigger)
        finally:
            _refresh_lock.release()

    threading.Thread(target=worker, daemon=True, name="repo-refresh").start()
    return True
