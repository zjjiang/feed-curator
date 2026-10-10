"""search_index 单元测试:分词、索引构建/加载、生命周期钩子。

索引是派生数据:测试里索引目录用 tmp_path,与 data/search_index 隔离。
"""

import time

import pytest

from app.models import Analysis, Article, Doc, Paper, Repo
from app.services import search_index as si
from app.writer import upsert_doc

NOW = int(time.time())


def _mk_doc(db, *, kind="article", url=None, title="标题", detail=None,
            pipe_id=1, external_id=None, sort_time=None):
    url = url or f"https://example.com/{kind}/{external_id or 'x'}"
    if detail is None:
        detail = {"content_text": "正文内容"} if kind == "article" else {}
    return upsert_doc(db, kind=kind, url=url, title=title, detail=detail,
                      pipe_id=pipe_id, external_id=external_id or f"e{pipe_id}",
                      sort_time=sort_time)


@pytest.fixture()
def corpus(db_session):
    """三篇典型文档:多智能体文章 / transformer 论文 / 仓库。"""
    _mk_doc(db_session, kind="article", title="多智能体协作框架综述",
            detail={"content_text": "本文综述多智能体协作的最新进展,涵盖智能体间通信与任务分配。"})
    _mk_doc(db_session, kind="paper", external_id="p1",
            title="Attention Is All You Need",
            detail={"abstract": "We propose the Transformer, a sequence transduction model."})
    _mk_doc(db_session, kind="repo", external_id="r1",
            title="streambed",
            detail={"owner": "viggy28", "name": "streambed",
                    "description": "Rust 流处理框架",
                    "readme_text": "streambed 是一个用 Rust 写的流处理引擎,支持窗口聚合。"})
    return db_session


# ============ 分词 ============


class TestTokenize:
    def test_chinese_phrase_split(self):
        toks = si.tokenize("多智能体协作与代码仓库检索")
        assert toks and all(t for t in toks)
        assert "协作" in toks and "检索" in toks

    def test_lowercase_and_mixed(self):
        toks = si.tokenize("Rust 流处理 Engine")
        assert "rust" in toks and "engine" in toks

    def test_whitespace_only_empty(self):
        assert si.tokenize("  \t\n ") == []

    def test_query_keeps_owner_slash_name(self):
        toks = si.tokenize_query("viggy28/streambed")
        assert "viggy28/streambed" in toks

    def test_query_without_slash_no_raw(self):
        plain = "多智能体协作"
        assert si.tokenize_query(plain) == si.tokenize(plain)


# ============ 索引字段组装 ============


class TestDocText:
    def test_article_title_plus_content(self, db_session):
        _mk_doc(db_session, title="大标题",
                detail={"content_text": "正文内容足够长才能命中"})
        doc = db_session.query(Doc).one()
        text = si.doc_text(db_session, doc)
        assert "大标题" in text and "正文内容足够长" in text

    def test_article_content_capped_at_preview(self, db_session):
        _mk_doc(db_session, detail={"content_text": "字" * 9000})
        doc = db_session.query(Doc).one()
        text = si.doc_text(db_session, doc)
        assert len(text) < 9000  # 预览截断生效

    def test_paper_uses_abstract(self, db_session):
        _mk_doc(db_session, kind="paper",
                detail={"abstract": "摘要内容关于强化学习"})
        doc = db_session.query(Doc).one()
        assert "强化学习" in si.doc_text(db_session, doc)

    def test_repo_owner_name_description_readme(self, db_session):
        _mk_doc(db_session, kind="repo",
                detail={"owner": "foo", "name": "bar",
                        "description": "向量数据库",
                        "readme_text": "README 讲解安装方法"})
        doc = db_session.query(Doc).one()
        text = si.doc_text(db_session, doc)
        assert "foo/bar" in text and "向量数据库" in text and "安装方法" in text


# ============ 构建 / 检索 / 持久化 ============


class TestBuildAndSearch:
    def test_build_returns_loaded_index(self, corpus, tmp_path):
        idx = si.build_index(corpus, str(tmp_path / "idx"))
        assert not idx.is_empty
        assert idx.size == 3

    def test_chinese_query_hits_right_doc_first(self, corpus, tmp_path):
        idx = si.build_index(corpus, str(tmp_path / "idx"))
        hits = idx.search("多智能体协作", top_k=3)
        assert hits
        assert hits[0][0] == 1  # article 的 doc_id
        assert hits[0][1] > 0

    def test_english_query_hits_paper(self, corpus, tmp_path):
        idx = si.build_index(corpus, str(tmp_path / "idx"))
        hits = idx.search("Transformer sequence model", top_k=3)
        assert hits and hits[0][0] == 2

    def test_owner_slash_name_hits_repo(self, corpus, tmp_path):
        idx = si.build_index(corpus, str(tmp_path / "idx"))
        hits = idx.search("viggy28/streambed", top_k=3)
        assert hits and hits[0][0] == 3

    def test_no_overlap_returns_empty(self, corpus, tmp_path):
        idx = si.build_index(corpus, str(tmp_path / "idx"))
        assert idx.search("完全不相关的词汇量子纠缠", top_k=3) == []

    def test_empty_query_raises(self, corpus, tmp_path):
        idx = si.build_index(corpus, str(tmp_path / "idx"))
        with pytest.raises(ValueError):
            idx.search("   ", top_k=3)

    def test_empty_corpus_builds_and_searches(self, db_session, tmp_path):
        idx = si.build_index(db_session, str(tmp_path / "idx"))
        assert idx.is_empty
        assert idx.search("任何词", top_k=3) == []

    def test_save_load_roundtrip(self, corpus, tmp_path):
        d = str(tmp_path / "idx")
        si.build_index(corpus, d).search("多智能体", top_k=1)
        loaded = si.load_index(d)
        assert loaded.size == 3
        assert loaded.search("多智能体", top_k=1)[0][0] == 1

    def test_load_missing_raises_file_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            si.load_index(str(tmp_path / "nope"))


# ============ 生命周期:脏标记 / 节流重建 / 自愈 ============


class TestLifecycle:
    def test_maybe_rebuild_builds_when_missing(self, corpus, tmp_path):
        idx = si.maybe_rebuild(corpus, str(tmp_path / "idx"))
        assert idx is not None and idx.size == 3

    def test_maybe_rebuild_fresh_index_not_rebuilt(self, corpus, tmp_path):
        d = str(tmp_path / "idx")
        si.build_index(corpus, d)
        idx = si.maybe_rebuild(corpus, d)
        assert idx.size == 3  # 复用已加载,不报错即为未重建

    def test_dirty_flag_forces_rebuild(self, corpus, tmp_path):
        d = str(tmp_path / "idx")
        si.build_index(corpus, d)
        si.mark_dirty()
        # 新文档入库后未进索引,脏标记触发重建后可命中
        _mk_doc(corpus, kind="article", url="https://example.com/article/new-embodied",
                title="新增的具身智能文章",
                detail={"content_text": "具身智能机器人在仓储场景落地"}, pipe_id=2,
                external_id="e-new")
        idx = si.maybe_rebuild(corpus, d)
        assert any(h[0] == 4 for h in idx.search("具身智能", top_k=5))

    def test_self_heal_on_corrupt_index(self, corpus, tmp_path):
        d = tmp_path / "idx"
        d.mkdir()
        (d / "indexbm25").write_text("garbage")  # 模拟损坏
        idx = si.load_or_build(corpus, str(d))
        assert idx.size == 3

    def test_safe_rebuild_swallows_errors(self, corpus, tmp_path, monkeypatch):
        d = str(tmp_path / "idx")
        monkeypatch.setattr(si, "build_index",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert si.safe_rebuild(corpus, d) is None  # 不抛出,写入不受影响


class TestGetIndexText:
    """doc_text 的公共入口:不存在实体行时回退到仅标题。"""

    def test_missing_entity_row_falls_back_to_title(self, db_session):
        _mk_doc(db_session, kind="paper", detail={})
        doc = db_session.query(Doc).one()
        assert si.doc_text(db_session, doc)  # 不抛异常,含标题


# ============ 索引状态(index_stats,供运维界面展示) ============


class TestIndexStats:
    def test_full_coverage(self, corpus, tmp_path):
        d = str(tmp_path / "idx")
        si.build_index(corpus, d)
        st = si.index_stats(corpus, d)
        assert st["exists"] and st["total"] == 3 and st["indexed"] == 3
        assert st["uncovered"] == [] and st["built_at"] > 0

    def test_uncovered_docs_listed(self, corpus, tmp_path):
        d = str(tmp_path / "idx")
        si.build_index(corpus, d)
        _mk_doc(corpus, kind="article", url="https://example.com/article/new-2",
                title="后来新增的文档", pipe_id=2, external_id="e-new2")
        st = si.index_stats(corpus, d)
        assert st["indexed"] == 3 and st["total"] == 4
        assert st["uncovered"][0]["doc_id"] == 4
        assert st["uncovered"][0]["title"] == "后来新增的文档"
        assert st["uncovered"][0]["kind"] == "article"

    def test_missing_index(self, db_session, tmp_path):
        st = si.index_stats(db_session, str(tmp_path / "nope"))
        assert not st["exists"] and st["indexed"] == 0 and st["built_at"] is None
        assert st["uncovered"] == []
