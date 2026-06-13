import re
from bs4 import BeautifulSoup

_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")
_CHINESE_RE = re.compile(r"[一-鿿]")
_WORD_RE = re.compile(r"[A-Za-z]+")


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    for img in soup.find_all("img"):
        alt = img.get("alt") or ""
        img.replace_with(f"[图片{f': {alt}' if alt else ''}]")

    for a in soup.find_all("a"):
        href = a.get("href")
        text = a.get_text(strip=True)
        if href and text and href != text:
            a.replace_with(f"{text}（{href}）")
        else:
            a.replace_with(text or href or "")

    text = soup.get_text("\n")
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def estimate_word_count(text: str) -> int:
    if not text:
        return 0
    chinese = len(_CHINESE_RE.findall(text))
    english = len(_WORD_RE.findall(text))
    return chinese + english
