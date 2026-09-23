"""网页正文抓取与解析。

article 正文补全与手工存入 URL 两处共用(见 design 决策 11)。
解析逻辑按已丢失的 archive_service.py 字节码重建:优先 <main>/<article> 容器、
剥除噪音标签、回退读 OG 与 Twitter meta、校验 content-type 为网页。
SSRF 四项防护 MUST 保留:拒绝内网与保留地址、拒绝本机、拒绝含凭据 URL、
域名解析失败即拒;重定向逐跳复检,强于原实现。
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from app.utils.html_clean import html_to_text


class ArchiveError(Exception):
    """抓取/解析失败,消息面向用户展示。"""


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FeedCurator/0.2; personal archive)",
    "Accept": "text/html,application/xhtml+xml",
}

_STRIP_TAGS = ("script", "style", "noscript", "nav", "footer", "aside", "form")


def normalize_input_url(value: str) -> str:
    """校验并规整用户输入的 URL:必须 http(s)、有主机名、不含凭据。"""
    value = (value or "").strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ArchiveError("请输入完整的 http:// 或 https:// 文章链接")
    if parts.username or parts.password:
        raise ArchiveError("文章链接不能包含用户名或密码")
    # 凭据已在上方拒绝,netloc 小写只影响 host(端口为数字不受影响)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path or "/", parts.query, ""))


def assert_public_url(url: str) -> None:
    """SSRF 防护:只允许解析到公网地址的主机名。"""
    host = urlsplit(url).hostname
    if not host:
        raise ArchiveError("链接缺少主机名")
    if host.lower() == "localhost":
        raise ArchiveError("不允许归档本机地址")
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ArchiveError(f"无法解析域名:{host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ArchiveError("不允许归档内网或保留地址")


def fetch_html(url: str, timeout: float = 15.0, max_redirects: int = 5,
               transport: httpx.BaseTransport | None = None) -> tuple[str, str]:
    """抓取网页,返回 (html, final_url)。重定向手动逐跳跟随,每跳都过 SSRF 校验。"""
    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False,
                      headers=_HEADERS, transport=transport) as client:
        for _ in range(max_redirects + 1):
            assert_public_url(current)
            try:
                resp = client.get(current)
            except httpx.HTTPError as exc:
                raise ArchiveError(f"网页抓取失败:{exc}") from exc
            if resp.is_redirect:
                location = resp.headers.get("location", "")
                if not location:
                    raise ArchiveError("重定向缺少目标地址")
                current = urljoin(current, location)
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise ArchiveError(f"网页抓取失败:{exc}") from exc
            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type.lower():
                raise ArchiveError(f"暂只支持网页文章,返回类型为 {content_type or '未知'}")
            return resp.text, str(resp.url)
    raise ArchiveError("重定向次数过多")


def _meta(soup: BeautifulSoup, attr: str, value: str) -> str:
    tag = soup.find("meta", attrs={attr: value})
    if tag is None:
        return ""
    return tag.get("content") or ""


def parse_page(html: str, base_url: str) -> dict:
    """HTML → 标题/摘要/作者/封面/正文。结构化容器优先,meta 兜底。"""
    soup = BeautifulSoup(html, "lxml")

    title = _meta(soup, "property", "og:title") or _meta(soup, "name", "twitter:title")
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    description = (_meta(soup, "property", "og:description")
                   or _meta(soup, "name", "description") or "")
    author = _meta(soup, "name", "author") or _meta(soup, "property", "article:author")
    cover = _meta(soup, "property", "og:image") or _meta(soup, "name", "twitter:image")
    if cover:
        cover = urljoin(base_url, cover)

    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    content_node = soup.find("article") or soup.find("main") or soup.body or soup
    content_html = str(content_node)
    content_text = html_to_text(content_html)

    return {
        "title": (title or "").strip()[:1000],
        "description": description.strip()[:2000],
        "author": author.strip()[:1000] if author else None,
        "cover_image_url": cover,
        "content_html": content_html,
        "content_text": content_text,
    }


def fetch_and_parse(url: str, transport: httpx.BaseTransport | None = None) -> dict:
    """完整流程:校验 → 抓取 → 解析。返回的 dict 额外带 url(重定向后的最终地址)。"""
    url = normalize_input_url(url)
    assert_public_url(url)
    html, final_url = fetch_html(url, transport=transport)
    parsed = parse_page(html, final_url)
    parsed["url"] = final_url
    return parsed
