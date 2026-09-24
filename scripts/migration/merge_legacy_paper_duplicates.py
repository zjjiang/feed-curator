"""一次性清理:身份规范化上线接缝的论文重复 doc 并归。

规范化(paper_identity)上线前,旧代码以带版本 URL(…/abs/xxx.v1)入库;
上线后同一论文被 HF/arXiv 以规范 URL 再次采到时,因 url_key 不同而形成
第二个 doc。本脚本按 paper.arxiv_id 分组,保留 url_key 为规范 abs 的
canonical doc,把 legacy doc 的 discovery / analysis / membership /
reading 迁到 canonical 上,再删除 legacy 行。幂等:无重复时零改动。

用法:
    set -a && source .env && set +a
    uv run python scripts/migration/merge_legacy_paper_duplicates.py [--apply]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import bindparam, text

from app.db import engine
from app.utils.paper_identity import canonical_url_from_id

APPLY = "--apply" in sys.argv


def merge_group(conn, arxiv_id: str, doc_ids: list[int]) -> bool:
    """把 legacy doc 并入 canonical doc。返回是否发生合并。"""
    canonical_key = canonical_url_from_id(arxiv_id)
    rows = conn.execute(
        text("SELECT id, url_key FROM doc WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)),
        {"ids": doc_ids}).fetchall()
    canonicals = [r[0] for r in rows if r[1] == canonical_key]
    legacies = [r[0] for r in rows if r[1] != canonical_key]
    if len(canonicals) != 1 or not legacies:
        print(f"  {arxiv_id}: 组内无唯一规范 doc(canonical={canonicals}),跳过,需人工核对")
        return False
    keep = canonicals[0]
    for legacy in legacies:
        conn.execute(text(
            "UPDATE discovery SET doc_id = :keep WHERE doc_id = :legacy"),
            {"keep": keep, "legacy": legacy})
        conn.execute(text(
            "UPDATE analysis SET doc_id = :keep WHERE doc_id = :legacy"),
            {"keep": keep, "legacy": legacy})
        # membership 主键 (doc_id, domain_id):canonical 已有的域直接弃 legacy 行
        conn.execute(text(
            "DELETE FROM membership WHERE doc_id = :legacy AND domain_id IN "
            "(SELECT domain_id FROM (SELECT domain_id FROM membership "
            "WHERE doc_id = :keep) t)"),
            {"keep": keep, "legacy": legacy})
        conn.execute(text(
            "UPDATE membership SET doc_id = :keep WHERE doc_id = :legacy"),
            {"keep": keep, "legacy": legacy})
        # reading 1:1:legacy 有而 canonical 没有 → 迁移;都有 → 弃 legacy(保留用户在 canonical 上的状态)
        legacy_reading = conn.execute(text(
            "SELECT COUNT(*) FROM reading WHERE doc_id = :d"),
            {"d": legacy}).scalar()
        keep_reading = conn.execute(text(
            "SELECT COUNT(*) FROM reading WHERE doc_id = :d"),
            {"d": keep}).scalar()
        if legacy_reading and not keep_reading:
            conn.execute(text(
                "UPDATE reading SET doc_id = :keep WHERE doc_id = :legacy"),
                {"keep": keep, "legacy": legacy})
        else:
            conn.execute(text("DELETE FROM reading WHERE doc_id = :d"), {"d": legacy})
        conn.execute(text("DELETE FROM paper WHERE id = :d"), {"d": legacy})
        conn.execute(text("DELETE FROM doc WHERE id = :d"), {"d": legacy})
        print(f"  {arxiv_id}: doc {legacy} → {keep} 已并归")
    return True


def main() -> int:
    mode = "APPLY" if APPLY else "DRY-RUN(加 --apply 生效)"
    conn = engine.connect()
    trans = conn.begin()
    try:
        groups = conn.execute(text(
            "SELECT arxiv_id, GROUP_CONCAT(id) FROM paper "
            "WHERE arxiv_id IS NOT NULL AND arxiv_id != '' "
            "GROUP BY arxiv_id HAVING COUNT(*) > 1")).fetchall()
        if not groups:
            print("无重复 arxiv_id,零改动")
            trans.rollback()
            return 0
        print(f"{mode}: {len(groups)} 组重复")
        for arxiv_id, id_csv in groups:
            merge_group(conn, arxiv_id, [int(x) for x in id_csv.split(",")])
        if APPLY:
            trans.commit()
        else:
            trans.rollback()
            print("(dry-run,已回滚)")
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
