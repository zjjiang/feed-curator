"""迁移核对:对 MySQL 目标库跑全部计数断言,全部通过才允许进入切换步骤。

用法(项目根目录):
    DATABASE_URL="mysql+pymysql://..." uv run python -m scripts.migration.verify_migration

期望值来自迁移前对源库的 dry-run 实测(design.md 迁移计划):
- items 2335 行,11 组 23 行重复 → doc 2323;合并管道撞 (pipe_id, external_id) 4 行 → discovery 2331
- pipe 16 → 13;membership 92 行去重;reading 1 行;run_log(sync_logs)= 35
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import json  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    Analysis, Article, Discovery, Doc, Domain, DocumentLink,
    Membership, Paper, Pipe, Reading, Repo, RunLog, Suggestion,
)
from app.writer import check_orphans  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual, expect) -> None:
    ok = actual == expect
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: {actual!r}" + ("" if ok else f" != 期望 {expect!r}"))
    if not ok:
        FAILURES.append(name)


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith("mysql"):
        sys.exit("拒绝执行:必须显式设置 DATABASE_URL 指向 MySQL 目标库")
    engine = create_engine(database_url, future=True)
    db = sessionmaker(bind=engine, future=True)()

    print("== 表计数 ==")
    check("doc", db.query(Doc).count(), 2323)
    check("paper", db.query(Paper).count(), db.query(Doc).filter_by(kind="paper").count())
    check("repo", db.query(Repo).count(), db.query(Doc).filter_by(kind="repo").count())
    check("article", db.query(Article).count(), db.query(Doc).filter_by(kind="article").count())
    check("实体表之和 = doc", db.query(Paper).count() + db.query(Repo).count()
          + db.query(Article).count(), db.query(Doc).count())
    check("discovery", db.query(Discovery).count(), 2331)
    check("membership", db.query(Membership).count(), 88)
    check("reading", db.query(Reading).count(), 1)
    check("run_log", db.query(RunLog).count(), 35)
    check("analysis(空起点)", db.query(Analysis).count(), 0)
    check("document_link(空起点)", db.query(DocumentLink).count(), 0)
    check("suggestion(空起点)", db.query(Suggestion).count(), 0)
    check("孤儿 doc", check_orphans(db), 0)

    print("== 孤儿引用 ==")
    orphan_disc = db.query(Discovery).outerjoin(Doc, Doc.id == Discovery.doc_id) \
        .filter(Doc.id.is_(None)).count()
    check("discovery 无悬空 doc", orphan_disc, 0)
    orphan_mem = db.query(Membership).outerjoin(Doc, Doc.id == Membership.doc_id) \
        .filter(Doc.id.is_(None)).count()
    check("membership 无悬空 doc", orphan_mem, 0)

    print("== domain ==")
    d = db.query(Domain).one()
    check("domain 名", d.name, "具身智能")
    check("domain keywords 完整", len(json.loads(d.keywords)), 10)
    check("含「具身智能」", "具身智能" in json.loads(d.keywords), True)

    print("== pipe(16→13) ==")
    check("pipe 数", db.query(Pipe).count(), 13)
    names = {p.name for p in db.query(Pipe).all()}
    check("36Kr 派生已并入", "具身文章 · 36Kr" in names, False)
    check("虎嗅派生已并入", "具身文章 · 虎嗅" in names, False)
    check("arXiv AI 派生已并入", "具身论文 · AI" in names, False)
    check("cs.RO 转共享", db.query(Pipe).filter_by(name="arXiv cs.RO").one().domain_id, None)
    gh = db.query(Pipe).filter(Pipe.type == "github").all()
    check("github 派生管道保留 2 个", len(gh), 2)
    check("github 管道已停用", all(p.enabled == 0 for p in gh), True)
    check("github 管道有停用说明", all(p.last_error for p in gh), True)
    check("机器之心保持停用",
          db.query(Pipe).filter_by(name="机器之心").one().enabled, 0)
    shared = db.query(Pipe).filter(Pipe.domain_id.is_(None)).count()
    derived = db.query(Pipe).filter(Pipe.domain_id.isnot(None)).count()
    check("共享 + 派生", (shared, derived), (11, 2))

    print("== 抽查 ==")
    # simonwillison 同文带/不带锚点 → 应只有一条 doc
    sw = db.query(Doc).filter(Doc.url_key.like("https://simonwillison.net%")).count()
    check("simonwillison 文档存在", sw > 100, True)
    # HN 抓回的 github 链接应为 repo 实体
    hn_repo = db.query(Doc).filter(
        Doc.kind == "repo", Doc.url_key.like("https://github.com/%")).count()
    check("github 链接判为 repo(含 HN 改判)", hn_repo > 100, True)
    # arxiv 链接应为 paper
    arxiv_paper = db.query(Doc).filter(
        Doc.kind == "paper", Doc.url_key.like("https://arxiv.org/%")).count()
    check("arxiv 链接判为 paper", arxiv_paper > 250, True)
    # 唯一有评分的文档保留了评分
    r = db.query(Reading).filter(Reading.rating.isnot(None)).one()
    check("用户评分未丢失", r.rating, 4)
    check("run_log 全为 fetch", db.query(RunLog).filter(RunLog.kind != "fetch").count(), 0)
    # membership 全部指向 domain 1、来源 ai
    check("membership assigned_by",
          {m.assigned_by for m in db.query(Membership).all()}, {"ai"})

    db.close()
    engine.dispose()

    print()
    if FAILURES:
        print(f"核对失败 {len(FAILURES)} 项: {FAILURES}")
        sys.exit(1)
    print("全部断言通过 ✓")


if __name__ == "__main__":
    main()
