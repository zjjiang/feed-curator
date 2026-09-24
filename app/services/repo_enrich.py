"""repo README 补全:新入库仓库在采集周期内同步填充;运维可手动分批补抓。

补全独立于采集(pipeline-ingestion 语义):不写 discovery、不改采集记录。
readme_text 三态:NULL=未抓取(失败或未轮到)、""=已确认仓库无 README、
非空=已抓取正文。失败不阻塞入库,留待补抓。
"""

import time

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Doc, Repo, RunLog
from app.services.github_client import GitHubClient


def enrich_new_repos(db: Session, entries: list[tuple[int, str, str]],
                     client: GitHubClient | None = None,
                     sleep_seconds: float = 0.0) -> int:
    """为 (doc_id, owner, name) 列表抓取 README 并填充。

    单条失败只打印,不阻塞其余条目,也不向上抛。返回成功处理的条数
    (含「确认无 README」——那也是一次完成的确认)。
    """
    if not entries:
        return 0
    owns_client = client is None
    client = client or GitHubClient()
    ok = 0
    try:
        for i, (doc_id, owner, name) in enumerate(entries):
            try:
                repo = db.get(Repo, doc_id)
                if repo is None or repo.readme_text is not None:
                    continue
                readme = client.get_readme(owner, name)
                repo.readme_text = readme or ""
                doc = db.get(Doc, doc_id)
                if doc is not None:
                    doc.last_modified_at = int(time.time())
                db.commit()
                ok += 1
            except Exception as e:  # noqa: BLE001 — 单条失败不阻塞其余
                print(f"[repo-enrich] {owner}/{name} README 抓取失败: "
                      f"{type(e).__name__}: {e}")
                db.rollback()
            if sleep_seconds and i < len(entries) - 1:
                time.sleep(sleep_seconds)
    finally:
        if owns_client:
            client.close()
    return ok


def pending_readme_repo_ids(db: Session, limit: int | None = None) -> list[int]:
    """readme_text 为 NULL(未抓取过)的 repo id,按 id 稳定排序。"""
    q = (
        db.query(Repo.id)
        .join(Doc, Doc.id == Repo.id)
        .filter(Doc.kind == "repo")
        .filter(Repo.readme_text.is_(None))
        .order_by(Repo.id)
    )
    if limit:
        q = q.limit(limit)
    return [r[0] for r in q.all()]


def run_readme_backfill(db: Session | None = None, *,
                        sleep_seconds: float = 1.0,
                        limit: int = 50,
                        client: GitHubClient | None = None) -> dict:
    """跑一轮 README 补抓(运维触发)。限速分批,进度刷进 run_log(kind='readme')。"""
    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        ids = pending_readme_repo_ids(db, limit=limit)
        owner_names = {
            r.id: (r.owner, r.name)
            for r in db.query(Repo).filter(Repo.id.in_(ids)).all()
        }
        run = RunLog(kind="readme", pipe_name="README 补全", trigger="manual",
                     status="running", total=len(ids), created_at=int(time.time()))
        db.add(run)
        db.commit()

        stats = {"total": len(ids), "succeeded": 0, "failed": 0}
        entries = [(i, *owner_names[i]) for i in ids if i in owner_names]
        owns_client = client is None
        client = client or GitHubClient()
        try:
            for i, (doc_id, owner, name) in enumerate(entries, 1):
                ok = enrich_new_repos(db, [(doc_id, owner, name)], client=client)
                if ok:
                    stats["succeeded"] += 1
                else:
                    stats["failed"] += 1
                db.query(RunLog).filter(RunLog.id == run.id).update({
                    RunLog.processed: i,
                    RunLog.succeeded: stats["succeeded"],
                    RunLog.failed: stats["failed"],
                })
                db.commit()
                if sleep_seconds and i < len(entries):
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
