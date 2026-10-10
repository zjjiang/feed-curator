"""进程内中文词法检索索引:分词、构建、检索、生命周期。

索引是纯派生数据(可随时全量重建),与 MySQL 主存储解耦:
- 语料字段:doc.title + 实体内容预览(article/paper/repo 见 doc_text);
- 分词:jieba cut_for_search,索引与查询同构;
- 排序:bm25s(BM25,纯 numpy/scipy 实现);
- 落盘:<index_dir>/indexbm25 + doc_ids.json,目录默认 data/search_index。

生命周期采用「脏标记 + 节流全量重建」:写入侧只 mark_dirty(),实际重建
由 maybe_rebuild 在节流窗口(默认 60s)到期或检索前触发,避免管道抓取
风暴下每批次都白建索引。重建失败一律吞掉记日志,绝不阻塞文档写入。
"""

import json
import logging
import os
import re
import threading
import time

import bm25s
import jieba

from app.models import Article, Doc, Paper, Repo

log = logging.getLogger(__name__)

DEFAULT_INDEX_DIR = os.environ.get("SEARCH_INDEX_DIR", "data/search_index")
PREVIEW_CHARS = 4000  # 正文/README 进索引的预览上限,控制体积与内存

# 常用虚词/口语停用词默认兜底:词面检索里只会造成全员误命中(如「的」
# 出现在几乎每篇文档),对区分性毫无贡献。取最小集合,宁缺勿滥——误杀
# 实词比留噪音更糟。可用 <index_dir 同级的 data>/search_stopwords.txt
# (每行一词,# 注释)追加,改完删索引或 mark_dirty 即生效。
_DEFAULT_STOPWORDS = {
    "的", "了", "在", "是", "和", "与", "及", "对", "从", "等", "被", "把",
    "向", "于", "或", "并", "而", "个", "这", "那", "就", "都", "也", "很",
    "会", "要", "去", "说", "看", "不", "有", "我", "你", "他", "她", "它",
    "我们", "自己", "一个", "一种", "没有", "可以", "以及", "通过", "使用",
    "关于", "进行", "支持", "并且", "但是", "如果", "因为", "所以",
}
_STOPWORDS_FILE = os.environ.get(
    "SEARCH_STOPWORDS_FILE", "data/search_stopwords.txt")
_WORD_CHAR = re.compile(r"[\w一-鿿]")  # 含字母/数字/下划线/中日韩文字


def _load_stopwords() -> frozenset[str]:
    words = set(_DEFAULT_STOPWORDS)
    try:
        with open(_STOPWORDS_FILE, encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if w and not w.startswith("#"):
                    words.add(w.lower())
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("停用词文件读取失败,仅用内置默认: %s", e)
    return frozenset(words)


_STOPWORDS = _load_stopwords()

_lock = threading.Lock()
_dirty = True
_loaded = None  # (index_dir, SearchIndex)


# ============ 分词 ============


def _keep(tok: str) -> bool:
    return bool(tok) and tok not in _STOPWORDS and bool(_WORD_CHAR.search(tok))


def tokenize(text: str) -> list[str]:
    """中文分词 + 小写化;停用词与无实义 token(标点/空白)丢弃。
    索引与查询都必须走这里,分词同构是命中前提。"""
    return [t.lower() for t in (s.strip() for s in jieba.cut_for_search(text)) if _keep(t)]


def tokenize_query(query: str) -> list[str]:
    """查询词分词;owner/name 形态(含 / )额外保留原文 token,
    避免 repo 的 owner/name 标识被切碎后无法命中。"""
    toks = tokenize(query)
    raw = query.strip().lower()
    if "/" in raw and raw not in toks:
        toks.append(raw)
    return toks


# ============ 语料字段 ============


def _preview(text: str | None) -> str:
    if not text:
        return ""
    return text[:PREVIEW_CHARS]


def doc_text(db, doc: Doc) -> str:
    """一篇文档进索引的全部文本:标题 + 实体内容。实体行缺失时回退仅标题。"""
    parts = [doc.title or ""]
    if doc.kind == "article":
        article = db.get(Article, doc.id)
        if article is not None:
            parts.append(_preview(article.content_text))
    elif doc.kind == "paper":
        paper = db.get(Paper, doc.id)
        if paper is not None:
            parts.append(_preview(paper.abstract))
    elif doc.kind == "repo":
        repo = db.get(Repo, doc.id)
        if repo is not None:
            parts.append(f"{repo.owner or ''}/{repo.name or ''}")
            parts.append(repo.description or "")
            parts.append(_preview(repo.readme_text))
    return "\n".join(p for p in parts if p)


# ============ 索引对象 ============


class SearchIndex:
    """已加载的 BM25 索引:doc_ids[n] 是倒排槽位 n 对应的文档 id。"""

    def __init__(self, bm25: bm25s.BM25 | None, doc_ids: list[int]):
        self.bm25 = bm25
        self.doc_ids = doc_ids

    @property
    def size(self) -> int:
        return len(self.doc_ids)

    @property
    def is_empty(self) -> bool:
        return not self.doc_ids

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """返回 [(doc_id, score)],按相关度降序;零分(无词面交集)不入结果。"""
        toks = tokenize_query(query)
        if not toks:
            raise ValueError("查询词为空或不含有效 token")
        if self.is_empty:
            return []
        k = max(1, min(top_k, self.size))
        results, scores = self.bm25.retrieve([toks], k=k)
        hits: list[tuple[int, float]] = []
        for slot, score in zip(results[0], scores[0]):
            if score <= 0:
                continue
            hits.append((self.doc_ids[int(slot)], float(score)))
        return hits

    def save(self, index_dir: str) -> None:
        os.makedirs(index_dir, exist_ok=True)
        if self.bm25 is not None:
            self.bm25.save(index_dir)
        with open(os.path.join(index_dir, "doc_ids.json"), "w", encoding="utf-8") as f:
            json.dump(self.doc_ids, f)


# ============ 构建 / 加载 ============


def build_index(db, index_dir: str = DEFAULT_INDEX_DIR) -> SearchIndex:
    """全量重建:读库 → 组装语料 → BM25 建索引 → 落盘。3k 语料秒级。"""
    docs = db.query(Doc).order_by(Doc.id).all()
    doc_ids: list[int] = []
    corpus_tokens: list[list[str]] = []
    for doc in docs:
        toks = tokenize(doc_text(db, doc))
        if not toks:
            continue
        doc_ids.append(doc.id)
        corpus_tokens.append(toks)

    if not corpus_tokens:
        index = SearchIndex(None, [])
    else:
        bm25 = bm25s.BM25()
        bm25.index(corpus_tokens)
        index = SearchIndex(bm25, doc_ids)
    index.save(index_dir)
    return index


def load_index(index_dir: str = DEFAULT_INDEX_DIR) -> SearchIndex:
    """从磁盘加载;目录缺失或文件损坏抛异常(由 load_or_build / 调用侧兜底)。"""
    bm25 = bm25s.BM25.load(index_dir)
    with open(os.path.join(index_dir, "doc_ids.json"), encoding="utf-8") as f:
        doc_ids = json.load(f)
    return SearchIndex(bm25, list(doc_ids))


def load_or_build(db, index_dir: str = DEFAULT_INDEX_DIR) -> SearchIndex:
    """自愈入口:加载失败(缺失/损坏)就地全量重建。"""
    try:
        return load_index(index_dir)
    except FileNotFoundError:
        log.info("检索索引不存在,执行首次构建: %s", index_dir)
    except Exception as e:  # noqa: BLE001 — 损坏一律重建自愈
        log.warning("检索索引加载失败(%s),执行重建自愈", e)
    return build_index(db, index_dir)


# ============ 生命周期:脏标记 + 节流重建 ============


def mark_dirty() -> None:
    """写入侧钩子:标记索引过期。任何写入路径的调用都不应抛出。"""
    global _dirty
    with _lock:
        _dirty = True


def maybe_rebuild(db, index_dir: str = DEFAULT_INDEX_DIR, *,
                  force: bool = False) -> SearchIndex:
    """按需重建:有脏标记 / 尚未加载 / 强制 时全量重建,否则复用已加载索引。

    不做时间节流:spec 要求写入完成后新文档即可被检索命中,脏标记必须
    立刻兑现;管道抓取风暴下频繁重建的开销由「写入钩子只 mark_dirty、
    重建推迟到下次检索」承担,3k 语料秒级重建在检索路径上可接受。
    """
    global _dirty, _loaded
    with _lock:
        need = force or _dirty or _loaded is None or _loaded[0] != index_dir
        if not need:
            return _loaded[1]
    # 脏标记语义是「库里有索引没有的新数据」,必须直接重建——
    # 若先尝试加载磁盘旧文件,脏标记会被旧索引架空。
    index = build_index(db, index_dir)
    with _lock:
        _dirty = False
        _loaded = (index_dir, index)
    return index


def safe_rebuild(db, index_dir: str = DEFAULT_INDEX_DIR, **kw) -> None:
    """写入侧用的安全包装:重建失败只记日志,绝不向写入主流程抛异常。"""
    try:
        mark_dirty()
        maybe_rebuild(db, index_dir)
    except Exception as e:  # noqa: BLE001 — 索引失败不影响写入(spec 场景)
        log.warning("检索索引重建失败(不影响写入): %s", e)


# ============ 索引状态(运维界面展示) ============


def index_stats(db, index_dir: str = DEFAULT_INDEX_DIR) -> dict:
    """索引覆盖状态:实时对比当前索引与数据库,不做缓存。

    返回 {exists, total, indexed, uncovered, built_at}:
    - exists=False(索引从未构建)时 uncovered 为空清单,由界面提示先重建;
    - uncovered 为未入索引文档 [{doc_id, kind, title}](内容为空或分词后
      无有效 token 的文档不会被建入索引)。
    """
    total_docs = db.query(Doc.id, Doc.kind, Doc.title).order_by(Doc.id).all()
    total = len(total_docs)
    try:
        index = load_index(index_dir)
    except (FileNotFoundError, Exception):  # noqa: BLE001 — 缺失/损坏统一按未构建展示
        return {"exists": False, "total": total, "indexed": 0,
                "uncovered": [], "built_at": None}

    indexed_ids = set(index.doc_ids)
    uncovered = [
        {"doc_id": d_id, "kind": kind, "title": title}
        for d_id, kind, title in total_docs
        if d_id not in indexed_ids
    ]
    try:
        built_at = int(os.path.getmtime(os.path.join(index_dir, "doc_ids.json")))
    except OSError:
        built_at = None
    return {"exists": True, "total": total, "indexed": len(indexed_ids),
            "uncovered": uncovered, "built_at": built_at}
