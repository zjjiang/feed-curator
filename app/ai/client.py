import json
import httpx
from typing import Any


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

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.3) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 800,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def process_article(
        self,
        title: str,
        description: str,
        content_preview: str,
        categories: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        """让 AI 阅读一篇文章,一次产出:摘要 + 要点 + 分类标签 + 1-5 星评级。

        categories: 用户预定义的分类表,每项 {"name": ..., "desc": ...}。
        AI 只能从这份表里多选;表为空则不分类。
        返回 {"summary", "keypoints": [...], "categories": [...], "stars": 1-5} 或 None。
        """
        category_block = _build_category_block(categories or [])

        prompt = PROCESS_PROMPT.format(
            category_block=category_block,
            title=title,
            description=description or "(无摘要)",
            content_preview=content_preview[:3000] if content_preview else "(无正文)",
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = self.chat(messages)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(raw)
        except (json.JSONDecodeError, httpx.HTTPError, KeyError) as e:
            print(f"[ai] 处理失败: {type(e).__name__}: {e}")
            return None

        # 规整 + 兜底,避免脏数据写库
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

        cats = data.get("categories") or []
        if not isinstance(cats, list):
            cats = [str(cats)]
        # 只保留预定义表里存在的分类(防止 AI 自创)
        allowed = {c["name"] for c in (categories or [])}
        cats = [str(c).strip() for c in cats if str(c).strip() and (not allowed or str(c).strip() in allowed)]

        return {
            "summary": str(data.get("summary", "")).strip(),
            "keypoints": keypoints,
            "categories": cats,
            "stars": stars,
        }


def _build_category_block(categories: list[dict[str, str]]) -> str:
    if not categories:
        return "（用户未定义任何分类，categories 返回空数组 []。）"
    lines = ["可选分类（只能从下列里选,可多选,不要自创分类名）："]
    for c in categories:
        name = c.get("name", "").strip()
        if not name:
            continue
        desc = (c.get("desc") or "").strip()
        lines.append(f"- {name}" + (f"：{desc}" if desc else ""))
    return "\n".join(lines)


SYSTEM_PROMPT = """你是一个专业的文章阅读助手。你的任务是认真阅读文章，然后完成四件事：

1. 摘要：用 2-3 句话浓缩文章核心内容，让人不读全文也能掌握讲了什么。
2. 要点：提炼 3-5 条关键信息点，每条一句话，简洁有信息量。
3. 分类：从用户给定的分类列表中，选出最贴切的一个或多个（只能从列表里选）。
4. 评级：按下列质量标准给文章打 1-5 星。

评级标准（星级越高越值得读）：
- 信息密度：是否提供了足够多的新信息、数据或观点？
- 原创性：是否有独到见解，还是人云亦云/搬运？
- 实用性：读完能否学到东西、改变认知或指导行动？
- 深度：是浅尝辄止还是有深入分析？
- 反套路：是否避免了标题党、鸡汤、营销话术？

星级参考：5=极有价值必读，4=值得一读，3=普通可看，2=价值不高，1=标题党/营销/无营养。

你必须返回严格的 JSON，不要有任何多余文字或解释。"""

PROCESS_PROMPT = """请阅读以下文章并处理。

{category_block}

标题：{title}
摘要：{description}
正文（前3000字）：
{content_preview}

请严格返回如下 JSON 格式：
{{"summary": "<2-3句话的摘要>", "keypoints": ["<要点1>", "<要点2>", "<要点3>"], "categories": ["<选中的分类名>"], "stars": <1-5整数>}}"""
