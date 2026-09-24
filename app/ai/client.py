import json
from typing import Any

import httpx

PROMPT_VERSION = "v1"


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout: float = 60.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.3,
             max_tokens: int = 800) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def analyze(
        self,
        *,
        kind: str,
        title: str,
        description: str,
        content_preview: str,
        content_sufficient: bool,
        domains: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """AI 判定一篇文档,返回 {summary, keypoints, domains, article_kind, stars}。

        domains: 候选领域表 [{"name","description","keywords"}],AI 只能从中多选。
        content_sufficient=False 时强制 article_kind 为空(不凭标题猜子类)。
        无法解析或星级越界返回 None(调用方记为失败)。
        """
        prompt = ANALYZE_PROMPT.format(
            domain_block=_build_domain_block(domains),
            kind_block=_build_kind_block(kind, content_sufficient),
            title=title,
            description=description or "(无摘要)",
            content_preview=content_preview[:3000] if content_preview else "(无正文)",
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = self.chat(messages, max_tokens=2000)   # 800 会截断中文 JSON(summary+keypoints)
            data = _parse_json(raw)
        except (json.JSONDecodeError, httpx.HTTPError, KeyError) as e:
            print(f"[ai] 判定失败: {type(e).__name__}: {e}")
            return None

        try:
            stars = int(round(float(data.get("stars", 0))))
        except (TypeError, ValueError):
            return None
        if not 1 <= stars <= 5:
            return None

        keypoints = data.get("keypoints") or []
        if not isinstance(keypoints, list):
            keypoints = [str(keypoints)]
        keypoints = [str(k).strip() for k in keypoints if str(k).strip()]

        allowed = {d["name"] for d in domains}
        picked = data.get("domains") or []
        if not isinstance(picked, list):
            picked = [str(picked)]
        picked = [str(d).strip() for d in picked if str(d).strip() in allowed]

        article_kind = str(data.get("article_kind") or "").strip()
        if article_kind not in ("tech", "business"):
            article_kind = ""    # 非法子类置空,不判失败
        if kind != "article" or not content_sufficient:
            article_kind = ""    # 非文章或正文不足:子类必须留空

        return {
            "summary": str(data.get("summary", "")).strip(),
            "keypoints": keypoints,
            "domains": picked,
            "article_kind": article_kind or None,
            "stars": stars,
        }


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def _build_domain_block(domains: list[dict[str, Any]]) -> str:
    if not domains:
        return "（当前没有可选领域，domains 返回空数组 []。）"
    lines = ["可选领域（只能从下列里选，可多选，没有合适的就返回空数组 []）："]
    for d in domains:
        keywords = d.get("keywords") or []
        kw = "、".join(keywords[:8])
        lines.append(f"- {d['name']}：{(d.get('description') or '').strip()}"
                     + (f"（关键词：{kw}）" if kw else ""))
    return "\n".join(lines)


def _build_kind_block(kind: str, content_sufficient: bool) -> str:
    if kind != "article":
        return "本文档不是文章（是论文或工程项目），article_kind 返回空字符串 \"\"。"
    if not content_sufficient:
        return ("本文正文内容不足，无法可靠判断技术/商业属性，"
                "article_kind 必须返回空字符串 \"\"，不要凭标题猜测。")
    return ("article_kind 从二选一：\"tech\"（技术向：开发、论文解读、工具、架构、数据、"
            "AI 技术进展等）或 \"business\"（商业向：公司、市场、融资、行业动态、商业模式等）。")


SYSTEM_PROMPT = """你是一个专业的阅读助手。认真阅读给定内容后完成五件事：

1. 摘要：2-3 句话浓缩核心内容。
2. 要点：3-5 条关键信息，每条一句话。
3. 领域归属：从用户给定的领域列表中多选；列表为空或都不合适则返回 []。
4. 子类：仅当是文章且正文充分时判断技术/商业属性，否则留空。
5. 评级：1-5 星。信息密度、原创性、实用性、深度、是否反套路。
   5=极有价值必读，4=值得一读，3=普通可看，2=价值不高，1=标题党/营销/无营养。

你必须返回严格的 JSON，不要有任何多余文字或解释。"""

ANALYZE_PROMPT = """请阅读以下内容并判定。

{domain_block}

内容类型：{kind_block}

标题：{title}
摘要：{description}
正文（前3000字）：
{content_preview}

请严格返回如下 JSON 格式：
{{"summary": "<2-3句话摘要>", "keypoints": ["<要点1>", "<要点2>", "<要点3>"], "domains": ["<领域名>"], "article_kind": "<tech|business|空字符串>", "stars": <1-5整数>}}"""
