"""管理后台统计:窗口解析、每日总览、渠道/领域转化聚合。"""

from datetime import datetime

import pytest

from app.models import (Analysis, Discovery, Doc, Domain, Membership, Pipe,
                        Reading)
from app.services.stats_service import (DEFAULT_DAYS, MAX_SPAN_DAYS,
                                        collect_stats, parse_window)

DAY1 = datetime(2026, 10, 1, 12, 0).timestamp()     # 各日本地正午,无边界歧义
DAY2 = datetime(2026, 10, 2, 12, 0).timestamp()
DAY3 = datetime(2026, 10, 3, 12, 0).timestamp()
LATE_NIGHT_D1 = datetime(2026, 10, 1, 23, 50).timestamp()


def _mk_doc(db, title, first_seen, kind="article"):
    url = f"https://example.com/{title}"
    doc = Doc(kind=kind, url_key=url, url=url, title=title, sort_time=first_seen,
              first_seen_at=first_seen, last_modified_at=first_seen)
    db.add(doc)
    db.flush()
    return doc


def _analyze(db, doc_id, stars, status="ok", created_at=None):
    db.add(Analysis(doc_id=doc_id, status=status, summary="s", keypoints="[]",
                    domains="[]", stars=stars if status == "ok" else None,
                    model="m", prompt_version="v1",
                    created_at=created_at or int(DAY1)))


def _read(db, doc_id, *, is_read=0, is_favorite=0):
    db.add(Reading(doc_id=doc_id, is_read=is_read, is_favorite=is_favorite,
                   updated_at=int(DAY2)))


def _pipe(db, name, domain_id=None):
    p = Pipe(type="rss", name=name, config="{}", domain_id=domain_id,
             enabled=1, fetch_interval_min=30,
             created_at=int(DAY1), updated_at=int(DAY1))
    db.add(p)
    db.flush()
    return p


def _discover(db, pipe_id, doc_id):
    db.add(Discovery(pipe_id=pipe_id, external_id=f"e{pipe_id}-{doc_id}",
                     doc_id=doc_id, first_seen_at=int(DAY1)))


def _domain(db, name):
    d = Domain(name=name, keywords='["k"]', enabled=1,
               created_at=int(DAY1), updated_at=int(DAY1))
    db.add(d)
    db.flush()
    return d


# ============ 窗口解析 ============


class TestParseWindow:
    def test_default_is_recent_days(self):
        start_ts, end_ts = parse_window(None, None, None,
                                        today=datetime(2026, 10, 5).date())
        span_days = (end_ts - start_ts) / 86400
        assert span_days == DEFAULT_DAYS

    def test_invalid_days_falls_back(self):
        for bad in ("abc", "0", "-5", str(MAX_SPAN_DAYS + 1)):
            start_ts, end_ts = parse_window(bad, None, None,
                                            today=datetime(2026, 10, 5).date())
            assert (end_ts - start_ts) == DEFAULT_DAYS * 86400

    def test_custom_range_inclusive_boundaries(self):
        start_ts, end_ts = parse_window(
            None, "2026-10-01", "2026-10-03",
            today=datetime(2026, 10, 5).date())
        assert start_ts == datetime(2026, 10, 1).timestamp()
        assert end_ts == datetime(2026, 10, 4).timestamp()  # 含 end 全天,右开

    def test_custom_span_over_cap_falls_back(self):
        start_ts, end_ts = parse_window(
            None, "2026-01-01", "2026-10-05",
            today=datetime(2026, 10, 5).date())
        assert (end_ts - start_ts) == DEFAULT_DAYS * 86400

    def test_bad_custom_dates_fall_back(self):
        start_ts, end_ts = parse_window(None, "oops", "2026-10-03",
                                        today=datetime(2026, 10, 5).date())
        assert (end_ts - start_ts) == DEFAULT_DAYS * 86400
        start_ts, end_ts = parse_window(None, "2026-10-03", "2026-10-01",
                                        today=datetime(2026, 10, 5).date())
        assert (end_ts - start_ts) == DEFAULT_DAYS * 86400

    def test_days_window_ends_at_tomorrow_start(self):
        start_ts, end_ts = parse_window("7", None, None,
                                        today=datetime(2026, 10, 5).date())
        assert end_ts == datetime(2026, 10, 6).timestamp()
        assert start_ts == datetime(2026, 9, 29).timestamp()


# ============ 每日总览 ============


class TestDailyOverview:
    def test_attribution_by_first_seen_local_day(self, db_session):
        """分析/阅读发生在 2 日,但 cohort 归 1 日(23:50 入库)。"""
        doc = _mk_doc(db_session, "夜入库", LATE_NIGHT_D1)
        _analyze(db_session, doc.id, stars=5)
        _read(db_session, doc.id, is_read=1, is_favorite=1)
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        d1 = next(r for r in out["daily"] if r["date"] == "2026-10-01")
        d2 = next(r for r in out["daily"] if r["date"] == "2026-10-02")
        assert d1["ingested"] == 1
        assert d1["analyzed"] == 1 and d1["read"] == 1 and d1["favorited"] == 1
        assert d1["ai_high"] == 1
        assert d2["ingested"] == 0 and d2["read"] == 0

    def test_kind_breakdown(self, db_session):
        _mk_doc(db_session, "a1", DAY1, kind="article")
        _mk_doc(db_session, "p1", DAY1, kind="paper")
        _mk_doc(db_session, "r1", DAY1, kind="repo")
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        d1 = next(r for r in out["daily"] if r["date"] == "2026-10-01")
        assert (d1["ingested"], d1["papers"], d1["repos"], d1["articles"]) == (3, 1, 1, 1)

    def test_failed_analysis_not_counted(self, db_session):
        doc = _mk_doc(db_session, "只失败", DAY1)
        _analyze(db_session, doc.id, stars=None, status="failed")
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        d1 = next(r for r in out["daily"] if r["date"] == "2026-10-01")
        assert d1["ingested"] == 1 and d1["analyzed"] == 0 and d1["ai_high"] == 0

    def test_rejudgment_takes_latest(self, db_session):
        doc = _mk_doc(db_session, "重判", DAY1)
        _analyze(db_session, doc.id, stars=2, created_at=int(DAY1))
        _analyze(db_session, doc.id, stars=5, created_at=int(DAY2))
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        d1 = next(r for r in out["daily"] if r["date"] == "2026-10-01")
        assert d1["ai_high"] == 1  # 最新判定 5 星

    def test_missing_reading_row_counts_zero(self, db_session):
        _mk_doc(db_session, "无阅读行", DAY1)
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        d1 = next(r for r in out["daily"] if r["date"] == "2026-10-01")
        assert d1["ingested"] == 1 and d1["read"] == 0

    def test_dismissed_doc_still_counted(self, db_session):
        doc = _mk_doc(db_session, "被清除", DAY1)
        db_session.add(Reading(doc_id=doc.id, is_dismissed=1, updated_at=int(DAY2)))
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        d1 = next(r for r in out["daily"] if r["date"] == "2026-10-01")
        assert d1["ingested"] == 1

    def test_empty_window_renders_zero_rows(self, db_session):
        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        dates = [r["date"] for r in out["daily"]]
        assert dates == ["2026-10-03", "2026-10-02", "2026-10-01"]  # 倒序全零
        assert all(r["ingested"] == 0 for r in out["daily"])
        assert out["pipes"] == [] and out["domains"] == []


# ============ 渠道转化 ============


class TestPipeConversion:
    def test_multi_pipe_double_count(self, db_session):
        pa, pb = _pipe(db_session, "渠道A"), _pipe(db_session, "渠道B")
        doc = _mk_doc(db_session, "双渠道", DAY1)
        _discover(db_session, pa.id, doc.id)
        _discover(db_session, pb.id, doc.id)
        _read(db_session, doc.id, is_read=1)
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        by_name = {p["name"]: p for p in out["pipes"]}
        assert by_name["渠道A"]["ingested"] == 1
        assert by_name["渠道B"]["ingested"] == 1
        assert by_name["渠道A"]["read"] == 1 and by_name["渠道B"]["read"] == 1

    def test_read_rate(self, db_session):
        p = _pipe(db_session, "渠道")
        docs = [_mk_doc(db_session, f"文{i}", DAY1) for i in range(10)]
        for i, d in enumerate(docs):
            _discover(db_session, p.id, d.id)
            if i < 4:
                _read(db_session, d.id, is_read=1)
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        assert out["pipes"][0]["read_rate"] == 40.0

    def test_zero_ingest_pipes_absent(self, db_session):
        _pipe(db_session, "空渠道")
        doc = _mk_doc(db_session, "别家", DAY1)
        _discover(db_session, _pipe(db_session, "有货").id, doc.id)
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        assert [p["name"] for p in out["pipes"]] == ["有货"]


# ============ 领域转化 ============


class TestDomainConversion:
    def test_multi_domain_double_count(self, db_session):
        la, lb = _domain(db_session, "LLM"), _domain(db_session, "Agent")
        doc = _mk_doc(db_session, "跨域", DAY1)
        db_session.add_all([Membership(doc_id=doc.id, domain_id=la.id,
                                       assigned_by="ai", created_at=int(DAY2)),
                            Membership(doc_id=doc.id, domain_id=lb.id,
                                       assigned_by="ai", created_at=int(DAY2))])
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        by_name = {d["name"]: d for d in out["domains"]}
        assert by_name["LLM"]["ingested"] == 1
        assert by_name["Agent"]["ingested"] == 1

    def test_zero_membership_domains_absent(self, db_session):
        _domain(db_session, "空领域")
        la = _domain(db_session, "有货")
        doc = _mk_doc(db_session, "归属文", DAY1)
        db_session.add(Membership(doc_id=doc.id, domain_id=la.id,
                                  assigned_by="ai", created_at=int(DAY2)))
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        assert [d["name"] for d in out["domains"]] == ["有货"]

    def test_sorted_by_ingested_desc(self, db_session):
        big, small = _domain(db_session, "大户"), _domain(db_session, "小户")
        for i in range(3):
            doc = _mk_doc(db_session, f"文{i}", DAY1)
            db_session.add(Membership(doc_id=doc.id, domain_id=big.id,
                                      assigned_by="ai", created_at=int(DAY2)))
        doc = _mk_doc(db_session, "独苗", DAY1)
        db_session.add(Membership(doc_id=doc.id, domain_id=small.id,
                                  assigned_by="ai", created_at=int(DAY2)))
        db_session.commit()

        out = collect_stats(db_session, DAY1 - 43200, DAY3 + 43200)
        assert [d["name"] for d in out["domains"]] == ["大户", "小户"]
