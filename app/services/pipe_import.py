"""导入 pipes.json:交换格式的另一半(导出见 pipe_export)。

边界严格校验;语义不明的条目跳过并报告,绝不静默降级;
解析失败直接抛错,零写入。
"""

import json

from sqlalchemy.orm import Session

from app.models import Domain, Pipe
from app.services.source_service import create_pipe

VALID_TYPES = ("rss", "arxiv", "wechat", "github", "hf_papers")
DEFAULT_INTERVAL = 30


def import_pipes(db: Session, raw: str) -> dict:
    """解析 pipes.json 并导入。返回 {"imported": N, "skipped": [{name, reason}]}。

    结构非法(ValueError)时零写入;单条不合法只跳过该条。
    """
    data = json.loads(raw)
    if isinstance(data, dict):
        entries = data.get("pipes")
    else:
        entries = data
    if not isinstance(entries, list):
        raise ValueError("结构不符:应为 {pipes: [...]} 或管道数组")

    domains = {d.name: d.id for d in db.query(Domain).all()}
    existing = {(p.type, p.name) for p in db.query(Pipe).all()}

    imported = 0
    skipped: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            skipped.append({"name": str(entry)[:40], "reason": "条目不是对象"})
            continue
        name = entry.get("name", "")
        if entry.get("type") not in VALID_TYPES:
            skipped.append({"name": name, "reason": f"类型不合法: {entry.get('type')}"})
            continue
        if not isinstance(name, str) or not name.strip():
            skipped.append({"name": name, "reason": "名称为空"})
            continue
        name = name.strip()
        config = entry.get("config")
        if config is None:
            config = {}
        if not isinstance(config, dict):
            skipped.append({"name": name, "reason": "config 不是对象"})
            continue
        if (entry["type"], name) in existing:
            skipped.append({"name": name, "reason": "已存在(type+name 重复)"})
            continue
        domain_name = entry.get("domain")
        domain_id = None
        if domain_name:
            domain_id = domains.get(domain_name)
            if domain_id is None:
                skipped.append({"name": name,
                                "reason": f"领域不存在: {domain_name}"})
                continue

        try:
            pipe = create_pipe(db, entry["type"], name, config,
                               int(entry.get("fetch_interval_min")
                                   or DEFAULT_INTERVAL),
                               domain_id)
        except ValueError as e:  # github 配置校验等业务拒绝
            db.rollback()
            skipped.append({"name": name, "reason": str(e)[:100]})
            continue
        if not entry.get("enabled", 1):
            pipe.enabled = 0
            db.commit()
        existing.add((entry["type"], name))
        imported += 1
    return {"imported": imported, "skipped": skipped}
