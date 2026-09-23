import time

import pytest
from sqlalchemy import insert
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateTable

from app.models import (
    Analysis,
    Article,
    Discovery,
    Doc,
    Domain,
    DocumentLink,
    Membership,
    Paper,
    Pipe,
    Reading,
    Repo,
    RunLog,
    Suggestion,
)

NOW = int(time.time())


def _doc(session, kind: str = "article", url_key: str = "https://example.com/a") -> Doc:
    doc = Doc(kind=kind, url_key=url_key, url=url_key, title="t",
              first_seen_at=NOW, last_modified_at=NOW)
    session.add(doc)
    session.flush()
    return doc


def _pipe(session, name: str = "p1") -> Pipe:
    pipe = Pipe(type="rss", name=name, config="{}", created_at=NOW, updated_at=NOW)
    session.add(pipe)
    session.flush()
    return pipe


def _domain(session, name: str = "d1") -> Domain:
    domain = Domain(name=name, created_at=NOW, updated_at=NOW)
    session.add(domain)
    session.flush()
    return domain


class TestThirteenTables:
    def test_all_tables_registered(self):
        names = {t for t in Doc.metadata.tables}
        assert names == {
            "doc", "paper", "repo", "article",
            "domain", "pipe", "discovery",
            "analysis", "membership", "document_link",
            "reading", "suggestion", "run_log",
        }


class TestEntityTables:
    def test_entity_tables_created_via_fixture(self, db_session):
        doc = _doc(db_session)
        db_session.add(Article(id=doc.id, content_text="hello"))
        db_session.flush()

        assert db_session.query(Doc).count() == 1
        assert db_session.query(Article).count() == 1

    def test_doc_kind_rejects_unknown_value(self, db_session):
        with pytest.raises(Exception):
            db_session.add(Doc(kind="photo", url_key="x", url="x", title="t"))
            db_session.flush()

    def test_url_key_unique(self, db_session):
        _doc(db_session, url_key="https://example.com/dup")
        with pytest.raises(Exception):
            _doc(db_session, url_key="https://example.com/dup")

    def test_all_three_entity_tables_work(self, db_session):
        for kind, cls in (("paper", Paper), ("repo", Repo), ("article", Article)):
            doc = _doc(db_session, kind=kind, url_key=f"https://example.com/{kind}")
            db_session.add(cls(id=doc.id))
        db_session.flush()
        assert db_session.query(Paper).count() == 1
        assert db_session.query(Repo).count() == 1
        assert db_session.query(Article).count() == 1


class TestPipeAndDiscovery:
    def test_pipe_domain_id_nullable(self, db_session):
        pipe = _pipe(db_session)
        assert pipe.domain_id is None

    def test_pipe_domain_id_fk(self, db_session):
        domain = _domain(db_session, "具身智能")
        pipe = Pipe(type="github", name="gh", config="{}", domain_id=domain.id,
                    created_at=NOW, updated_at=NOW)
        db_session.add(pipe)
        db_session.flush()
        assert pipe.domain_id == domain.id

    def test_discovery_unique_pipe_external(self, db_session):
        pipe = _pipe(db_session)
        doc = _doc(db_session)
        db_session.add(Discovery(pipe_id=pipe.id, external_id="e1", doc_id=doc.id, first_seen_at=NOW))
        db_session.flush()
        with pytest.raises(Exception):
            db_session.add(Discovery(pipe_id=pipe.id, external_id="e1", doc_id=doc.id, first_seen_at=NOW))
            db_session.flush()

    def test_discovery_same_external_different_pipes_ok(self, db_session):
        pipe_a = _pipe(db_session, "a")
        pipe_b = _pipe(db_session, "b")
        doc = _doc(db_session)
        db_session.add(Discovery(pipe_id=pipe_a.id, external_id="e1", doc_id=doc.id, first_seen_at=NOW))
        db_session.add(Discovery(pipe_id=pipe_b.id, external_id="e1", doc_id=doc.id, first_seen_at=NOW))
        db_session.flush()
        assert db_session.query(Discovery).count() == 2


class TestAnalysisAndMembership:
    def test_membership_composite_pk(self, db_session):
        domain = _domain(db_session, "d1")
        doc = _doc(db_session)
        db_session.add(Membership(doc_id=doc.id, domain_id=domain.id, assigned_by="ai", created_at=NOW))
        db_session.flush()
        with pytest.raises(Exception):
            db_session.add(Membership(doc_id=doc.id, domain_id=domain.id, assigned_by="manual", created_at=NOW))
            db_session.flush()

    def test_membership_same_doc_two_domains_ok(self, db_session):
        d1 = _domain(db_session, "d1")
        d2 = _domain(db_session, "d2")
        doc = _doc(db_session)
        db_session.add(Membership(doc_id=doc.id, domain_id=d1.id, assigned_by="ai", created_at=NOW))
        db_session.add(Membership(doc_id=doc.id, domain_id=d2.id, assigned_by="ai", created_at=NOW))
        db_session.flush()
        assert db_session.query(Membership).count() == 2

    def test_analysis_append_only_shape(self, db_session):
        doc = _doc(db_session)
        db_session.add(Analysis(doc_id=doc.id, status="ok", stars=3, created_at=NOW))
        db_session.add(Analysis(doc_id=doc.id, status="failed", error="bad json", created_at=NOW))
        db_session.flush()
        rows = db_session.query(Analysis).filter_by(doc_id=doc.id).all()
        assert len(rows) == 2

    def test_reference_tables_all_fk_to_doc(self):
        for table_name in ("analysis", "membership", "document_link", "reading", "discovery"):
            table = Doc.metadata.tables[table_name]
            fk_targets = {fk.target_fullname for fk in table.foreign_keys if "doc." in fk.target_fullname}
            assert fk_targets, f"{table_name} 没有指向 doc 的外键"


class TestDocumentLink:
    def test_unique_from_to_kind(self, db_session):
        doc_a = _doc(db_session, url_key="https://example.com/a")
        doc_b = _doc(db_session, url_key="https://example.com/b")
        db_session.add(DocumentLink(from_doc_id=doc_a.id, to_doc_id=doc_b.id, kind="implements", created_at=NOW))
        db_session.flush()
        with pytest.raises(Exception):
            db_session.add(DocumentLink(from_doc_id=doc_a.id, to_doc_id=doc_b.id, kind="implements", created_at=NOW))
            db_session.flush()

    def test_same_pair_different_kind_ok(self, db_session):
        doc_a = _doc(db_session, url_key="https://example.com/a")
        doc_b = _doc(db_session, url_key="https://example.com/b")
        db_session.add(DocumentLink(from_doc_id=doc_a.id, to_doc_id=doc_b.id, kind="implements", created_at=NOW))
        db_session.add(DocumentLink(from_doc_id=doc_a.id, to_doc_id=doc_b.id, kind="duplicate", created_at=NOW))
        db_session.flush()
        assert db_session.query(DocumentLink).count() == 2


class TestReadingOneToOne:
    def test_reading_pk_is_doc_id(self, db_session):
        doc = _doc(db_session)
        db_session.add(Reading(doc_id=doc.id, is_favorite=1, rating=4, updated_at=NOW))
        db_session.flush()
        with pytest.raises(Exception):
            db_session.add(Reading(doc_id=doc.id, is_read=1, updated_at=NOW))
            db_session.flush()


class TestDialectCompatibility:
    def test_all_ddl_compiles_on_mysql_and_sqlite(self):
        for table in Doc.metadata.sorted_tables:
            CreateTable(table).compile(dialect=mysql.dialect())
            CreateTable(table).compile(dialect=sqlite.dialect())

    def test_longtext_columns_render_as_longtext_on_mysql(self):
        for table_name, col_name in (
            ("paper", "abstract"),
            ("paper", "content_text"),
            ("repo", "readme_text"),
            ("article", "content_text"),
            ("article", "content_html"),
            ("article", "description"),
        ):
            table = Doc.metadata.tables[table_name]
            ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
            col_type = table.c[col_name].type.compile(mysql.dialect())
            assert col_type.upper() == "LONGTEXT", f"{table_name}.{col_name} 在 MySQL 下是 {col_type},不是 LONGTEXT"

    def test_url_key_index_fits_mysql_byte_limit(self):
        # utf8mb4 每字符 4 字节,InnoDB 索引上限 3072 字节 → 500*4=2000 安全
        ddl = str(CreateTable(Doc.metadata.tables["doc"]).compile(dialect=mysql.dialect()))
        assert "LONGTEXT" not in ddl  # doc 表不该有长文本列


class TestRunLogAndSuggestion:
    def test_run_log_insert(self, db_session):
        pipe = _pipe(db_session)
        db_session.add(RunLog(kind="fetch", pipe_id=pipe.id, pipe_name=pipe.name, status="done", inserted=3, created_at=NOW))
        db_session.add(RunLog(kind="analyze", status="running", total=100, created_at=NOW))
        db_session.flush()
        assert db_session.query(RunLog).count() == 2

    def test_suggestion_defaults_pending(self, db_session):
        db_session.add(Suggestion(kind="propose_keyword", payload='{"kw": "VLA"}', created_by="curator", created_at=NOW))
        db_session.flush()
        row = db_session.query(Suggestion).one()
        assert row.status == "pending"
