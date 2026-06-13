import json
import httpx
from typing import Any


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout: float = 30.0,
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
            "max_tokens": 300,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def score_article(
        self,
        title: str,
        description: str,
        content_preview: str,
        user_preferences: str = "",
        few_shot_text: str = "",
    ) -> dict[str, Any] | None:
        system = SYSTEM_PROMPT
        if user_preferences:
            system += f"\n\n用户偏好补充：\n{user_preferences}"

        prompt = SCORE_PROMPT.format(
            few_shot=few_shot_text,
            title=title,
            description=description or "(无摘要)",
            content_preview=content_preview[:1500] if content_preview else "(无正文)",
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = self.chat(messages)
            raw = raw.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(raw)
        except (json.JSONDecodeError, httpx.HTTPError, KeyError) as e:
            print(f"[ai] 评分失败: {type(e).__name__}: {e}")
            return None


SYSTEM_PROMPT = """你是一个内容质量评估助手。你的任务是对文章进行客观质量评分。
评分标准：
- 信息密度：文章是否提供了足够多的新信息、数据或观点？
- 原创性：是否有独到见解，还是人云亦云/搬运？
- 实用性：读完能否学到东西、改变认知或指导行动？
- 深度：是浅尝辄止还是有深入分析？
- 反套路：是否避免了标题党、鸡汤、营销话术？

如果用户提供了偏好示例，请充分参考——和用户喜欢的文章风格相近的应该打高分。

你必须返回严格的 JSON 格式，不要有多余文字。"""

SCORE_PROMPT = """请评估以下文章的质量：
{few_shot}
标题：{title}
摘要：{description}
正文（前1500字）：
{content_preview}

请返回 JSON：
{{"score": <1-10整数>, "reason": "<不超过50字的评价>", "tags": [<1-3个主题标签>]}}"""
