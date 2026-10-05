"""管理后台统计:按日 × 渠道 × 领域的转化聚合。

纯只读实时聚合,不建表、不写库。窗口内 doc(至多数千行)一次拉回,
在 Python 按本地日期分组 —— 绕开 MySQL/SQLite 日期函数方言差异。

口径:cohort = doc 入库日(first_seen_at);AI≥4 星 = 该 doc 最新一条
ok 判定的 stars>=4;同一 doc 被多个渠道采集 / 属于多个领域时各计一次
(discovery / membership 本就是多对多溯源)。
"""

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Analysis, Discovery, Doc, Domain, Membership, Pipe, Reading

DEFAULT_DAYS = 14
PRESET_DAYS = (7, 14, 30)
MAX_SPAN_DAYS = 92

_KIND_FIELDS = {"paper": "papers", "repo": "repos", "article": "articles"}


def _day_start(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp())


def parse_window(days: str | None, start: str | None, end: str | None,
                 today: date | None = None) -> tuple[int, int]:
    """解析查询窗口,返回 (start_ts, end_ts),右开区间。

    自定义 start/end(YYYY-MM-DD,含两端)优先且须合法、跨度 <= MAX_SPAN_DAYS;
    否则用 days 天(非法或超限回退 DEFAULT_DAYS),窗口为含今天在内
    的最近 N 个本地日。
    """
    today = today or date.today()
    if start and end:
        try:
            s = date.fromisoformat(start)
            e = date.fromisoformat(end)
        except ValueError:
            s = e = None
        if s and e and s <= e and (e - s).days + 1 <= MAX_SPAN_DAYS:
            return _day_start(s), _day_start(e + timedelta(days=1))

    try:
        n = int(days) if days else DEFAULT_DAYS
    except (TypeError, ValueError):
        n = DEFAULT_DAYS
    if n <= 0 or n > MAX_SPAN_DAYS:
        n = DEFAULT_DAYS
    return _day_start(today - timedelta(days=n - 1)), _day_start(today + timedelta(days=1))


def collect_stats(db: Session, start_ts: int, end_ts: int) -> dict:
    """窗口内三块聚合:daily(每日总览,含全零空日)+ pipes + domains。"""
    docs = (
        db.query(Doc)
        .filter(Doc.first_seen_at >= start_ts, Doc.first_seen_at < end_ts)
        .all()
    )
    ids = [d.id for d in docs]
    analyses = _latest_ok_analyses(db, ids)
    readings = _reading_flags(db, ids)

    daily = _empty_daily(start_ts, end_ts)
    for doc in docs:
        key = datetime.fromtimestamp(doc.first_seen_at).strftime("%Y-%m-%d")
        row = daily[key]
        row["ingested"] += 1
        if doc.kind in _KIND_FIELDS:
            row[_KIND_FIELDS[doc.kind]] += 1
        funnel = _funnel_of(doc, analyses.get(doc.id), readings.get(doc.id))
        for field in ("analyzed", "read", "favorited", "ai_high"):
            row[field] += funnel[field]

    pipes = _by_pipe(db, docs, analyses, readings)
    domains = _by_domain(db, docs, analyses, readings)

    return {
        "daily": [daily[k] for k in sorted(daily, reverse=True)],
        "pipes": pipes,
        "domains": domains,
    }


def _empty_daily(start_ts: int, end_ts: int) -> dict[str, dict]:
    """窗口内每个本地日一行,倒序展示时含全零空日。"""
    out: dict[str, dict] = {}
    d = datetime.fromtimestamp(start_ts).date()
    end = datetime.fromtimestamp(end_ts).date()
    while d < end:
        out[d.strftime("%Y-%m-%d")] = {
            "date": d.strftime("%Y-%m-%d"), "ingested": 0,
            "papers": 0, "repos": 0, "articles": 0,
            "analyzed": 0, "read": 0, "favorited": 0, "ai_high": 0,
        }
        d += timedelta(days=1)
    return out


def _funnel_of(doc: Doc, analysis: Analysis | None,
               reading: dict | None) -> dict:
    return {
        "analyzed": 1 if analysis else 0,
        "read": 1 if reading and reading["is_read"] else 0,
        "favorited": 1 if reading and reading["is_favorite"] else 0,
        "ai_high": 1 if analysis and (analysis.stars or 0) >= 4 else 0,
    }


def _latest_ok_analyses(db: Session, doc_ids: list[int]) -> dict[int, Analysis]:
    """每文档最新一条 ok analysis;无 ok 的文档不在结果里。"""
    if not doc_ids:
        return {}
    latest_ids = (
        db.query(func.max(Analysis.id))
        .filter(Analysis.doc_id.in_(doc_ids), Analysis.status == "ok")
        .group_by(Analysis.doc_id)
        .all()
    )
    id_list = [row[0] for row in latest_ids]
    if not id_list:
        return {}
    rows = db.query(Analysis).filter(Analysis.id.in_(id_list)).all()
    return {a.doc_id: a for a in rows}


def _reading_flags(db: Session, doc_ids: list[int]) -> dict[int, dict]:
    if not doc_ids:
        return {}
    rows = db.query(Reading).filter(Reading.doc_id.in_(doc_ids)).all()
    return {
        r.doc_id: {"is_read": r.is_read, "is_favorite": r.is_favorite}
        for r in rows
    }


def _by_pipe(db: Session, docs: list[Doc], analyses: dict,
             readings: dict) -> list[dict]:
    """按 discovery 归属渠道;同 doc 多渠道各计一次;零入库渠道不出现。"""
    if not docs:
        return []
    by_id = {d.id: d for d in docs}
    rows = (
        db.query(Discovery.doc_id, Pipe.id, Pipe.name)
        .join(Pipe, Pipe.id == Discovery.pipe_id)
        .filter(Discovery.doc_id.in_(by_id))
        .all()
    )
    buckets: dict[int, dict] = {}
    for doc_id, pipe_id, pipe_name in rows:
        bucket = buckets.setdefault(pipe_id, {
            "name": pipe_name, "ingested": 0, "analyzed": 0,
            "read": 0, "favorited": 0, "ai_high": 0,
        })
        funnel = _funnel_of(by_id[doc_id], analyses.get(doc_id),
                            readings.get(doc_id))
        bucket["ingested"] += 1
        for field in ("analyzed", "read", "favorited", "ai_high"):
            bucket[field] += funnel[field]
    return _with_rate(buckets)


def _by_domain(db: Session, docs: list[Doc], analyses: dict,
               readings: dict) -> list[dict]:
    """按 membership 归属领域;口径同渠道;零归属领域不出现。"""
    if not docs:
        return []
    by_id = {d.id: d for d in docs}
    rows = (
        db.query(Membership.doc_id, Domain.id, Domain.name)
        .join(Domain, Domain.id == Membership.domain_id)
        .filter(Membership.doc_id.in_(by_id))
        .all()
    )
    buckets: dict[int, dict] = {}
    for doc_id, domain_id, domain_name in rows:
        bucket = buckets.setdefault(domain_id, {
            "name": domain_name, "ingested": 0, "analyzed": 0,
            "read": 0, "favorited": 0, "ai_high": 0,
        })
        funnel = _funnel_of(by_id[doc_id], analyses.get(doc_id),
                            readings.get(doc_id))
        bucket["ingested"] += 1
        for field in ("analyzed", "read", "favorited", "ai_high"):
            bucket[field] += funnel[field]
    return _with_rate(buckets)


def _with_rate(buckets: dict[int, dict]) -> list[dict]:
    out = sorted(buckets.values(),
                 key=lambda b: (-b["ingested"], b["name"]))
    for b in out:
        b["read_rate"] = round(b["read"] / b["ingested"] * 100, 1) if b["ingested"] else 0.0
    return out
