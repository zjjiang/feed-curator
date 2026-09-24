"""手工存入 URL:manual 管道类型。

复用 fulltext 服务的解析与 SSRF 防护;article 抓正文,repo/paper 只从
URL 提取结构化字段(字段留空待补全,见 design 决策 9)。
"""

from sqlalchemy.orm import Session

from app.adapters.arxiv import fetch_paper_by_id
from app.models import Doc, Paper, Pipe, Repo
from app.services import fulltext
from app.services.doc_fields import (build_detail, detect_doc_kind, parse_arxiv_id,
                                     parse_github_owner_name)
from app.services.fulltext import ArchiveError
from app.utils.html_clean import estimate_word_count
from app.utils.paper_identity import canonical_paper_url
from app.utils.url_key import normalize_url
from app.writer import upsert_doc

MANUAL_PIPE_NAME = "手工存入"


def get_manual_pipe(db: Session) -> Pipe:
    """单一手工管道,懒创建。"""
    pipe = db.query(Pipe).filter(Pipe.type == "manual").first()
    if pipe is None:
        import time

        now = int(time.time())
        pipe = Pipe(type="manual", name=MANUAL_PIPE_NAME, config="{}",
                    enabled=0, created_at=now, updated_at=now)
        db.add(pipe)
        db.commit()
    return pipe


def _repo_readme_pending(db: Session, doc_url: str) -> bool:
    """仓库是否需要抓 README:文档不存在(首次入库)或 readme_text 为 NULL。

    readme_text 三态(NULL 未抓 / "" 已确认无 / 非空已有)决定重试语义:
    只有 NULL 值得重试,"" 与非空都不再发请求。
    """
    doc = db.query(Doc).filter(Doc.url_key == normalize_url(doc_url)).first()
    if doc is None:
        return True
    repo = db.get(Repo, doc.id)
    return repo is None or repo.readme_text is None


def _fetch_repo_readme(url: str) -> tuple[str | None, str | None]:
    """抓取 README;返回 (readme_text, error)。

    404 → ("", None):确认仓库无 README,不再重试;网络失败 → (None, 原因):
    readme_text 保持 NULL,留待再次提交或 /ops 补抓。
    """
    from app.services.github_client import GitHubClient  # 惰性:httpx 连接池

    owner, name = parse_github_owner_name(url)
    if not owner:
        return None, "无法从 URL 解析仓库 owner/name"
    client = GitHubClient()
    try:
        readme = client.get_readme(owner, name)
    except Exception as e:  # noqa: BLE001 — 网络边界,失败降级
        return None, f"README 抓取失败:{type(e).__name__}: {e}"
    finally:
        client.close()
    if readme is None:
        return "", None
    return readme, None


def _paper_abstract_pending(db: Session, doc_url: str) -> bool:
    """论文是否需要抓摘要:文档不存在(首次入库)或 abstract 为空。"""
    doc = db.query(Doc).filter(Doc.url_key == normalize_url(doc_url)).first()
    if doc is None:
        return True
    paper = db.get(Paper, doc.id)
    return paper is None or not paper.abstract


def _paper_detail(db: Session, doc_url: str, url: str) -> tuple[dict, str | None]:
    """论文实体 detail:已有 abstract 不重复抓;否则按 id 经 arXiv API 补全。

    返回 (detail, error);任何抓取失败都降级为仅建档(字段留空待重试)。
    """
    if not _paper_abstract_pending(db, doc_url):
        return build_detail("paper", url=url), None
    arxiv_id, _ = parse_arxiv_id(url)
    if not arxiv_id:
        return build_detail("paper", url=url), None
    try:
        item = fetch_paper_by_id(arxiv_id)
    except Exception as e:  # noqa: BLE001 — 网络边界,失败降级
        return build_detail("paper", url=url), f"摘要抓取失败:{type(e).__name__}: {e}"
    if item is None:
        return build_detail("paper", url=url), "arXiv API 未返回该论文"
    detail = build_detail("paper", url=url, description=item.description,
                          content_text=item.content_text,
                          published_at=item.published_at, meta=item.meta)
    return detail, None


def save_url(db: Session, url: str, note: str | None = None) -> dict:
    """存入一个 URL。返回 {doc_id, kind, created, error, title}。

    抓取失败不阻塞入库:文档以 URL 为标题、空正文先落库,留待补全重试。
    """
    pipe = get_manual_pipe(db)
    normalized = fulltext.normalize_input_url(url)
    kind = detect_doc_kind(normalized)
    # 论文入库前归一为规范身份 URL(剥版本/归一 host),与管道侧同规则
    doc_url = canonical_paper_url(normalized) if kind == "paper" else normalized

    title = normalized
    detail: dict = {}
    fetch_error = None
    if kind == "article":
        try:
            parsed = fulltext.fetch_and_parse(normalized)
            title = parsed["title"] or normalized
            detail = build_detail(
                "article",
                url=parsed["url"],
                author=parsed["author"],
                description=parsed["description"] or None,
                content_text=parsed["content_text"] or None,
                content_html=parsed["content_html"] or None,
                cover_image_url=parsed["cover_image_url"],
                word_count=estimate_word_count(parsed["content_text"] or ""),
                meta={"archived_from": "manual", "note": note} if note
                else {"archived_from": "manual"},
            )
        except ArchiveError as exc:
            fetch_error = str(exc)
            detail = build_detail("article", url=normalized, meta={"archive_error": fetch_error})
    elif kind == "repo":
        if _repo_readme_pending(db, doc_url):
            readme, fetch_error = _fetch_repo_readme(normalized)
        else:
            readme = None  # 已持有 README,不重复抓
        detail = build_detail("repo", url=normalized, content_text=readme)
    elif kind == "paper":
        detail, fetch_error = _paper_detail(db, doc_url, normalized)
    else:
        detail = build_detail(kind, url=normalized)

    result = upsert_doc(
        db,
        kind=kind,
        url=doc_url,
        title=title,
        detail=detail,
        pipe_id=pipe.id,
        external_id=normalized,
    )
    return {
        "doc_id": result.doc_id,
        "kind": kind,
        "created": result.doc_created,
        "error": fetch_error,
        "title": db.get(Doc, result.doc_id).title,
    }
