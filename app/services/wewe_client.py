"""we-mp-rss HTTP 客户端封装。

we-mp-rss(微信公众号 RSS，默认 http://localhost:9001）的管理 API 需要登录拿
token。这里把"登录 / 搜索公众号 / 订阅 / 触发抓取"这几步封装起来，供 MCP 工具
和其他服务复用。凭据从环境变量读，token 带过期缓存、过期自动重登。

注意：本模块只与 we-mp-rss 通信，不碰 feed-curator 的 DB。
"""

import os
import time
import urllib.parse
from dataclasses import dataclass

import httpx

# 默认配置，可被环境变量覆盖
DEFAULT_BASE_URL = "http://localhost:9001"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin@123"

# token 提前过期的安全余量（秒）：实际过期前就主动重登，避免临界失败
_TOKEN_EXPIRY_MARGIN = 60
_HTTP_TIMEOUT = 30.0


class WeweError(Exception):
    """we-mp-rss 交互失败时抛出，message 面向调用方友好可读。"""


@dataclass(frozen=True)
class WechatCandidate:
    """搜索公众号返回的候选项（尚未订阅）。"""

    fakeid: str
    nickname: str
    alias: str
    signature: str

    def to_dict(self) -> dict:
        return {
            "fakeid": self.fakeid,
            "nickname": self.nickname,
            "alias": self.alias,
            "signature": self.signature,
        }


class WeweClient:
    """we-mp-rss 客户端。无状态对外，内部仅缓存 token。"""

    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("WEWE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._username = username or os.environ.get("WEWE_USERNAME", DEFAULT_USERNAME)
        self._password = password or os.environ.get("WEWE_PASSWORD", DEFAULT_PASSWORD)
        self._token: str | None = None
        self._token_expire_at: int = 0

    # ---- 鉴权 ----

    def _ensure_token(self) -> str:
        now = int(time.time())
        if self._token and now < self._token_expire_at - _TOKEN_EXPIRY_MARGIN:
            return self._token
        return self._login()

    def _login(self) -> str:
        url = f"{self.base_url}/api/v1/wx/auth/token"
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                # 注意：登录接口要 form-urlencoded，不是 JSON
                resp = client.post(
                    url,
                    data={"username": self._username, "password": self._password},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as e:
            raise WeweError(f"无法连接 we-mp-rss（{self.base_url}）：{e}") from e

        if resp.status_code != 200:
            raise WeweError(
                f"we-mp-rss 登录失败（HTTP {resp.status_code}），请检查 WEWE_USERNAME/WEWE_PASSWORD"
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise WeweError(f"we-mp-rss 登录未返回 token：{data}")

        self._token = token
        expires_in = int(data.get("expires_in") or 0)
        self._token_expire_at = int(time.time()) + (expires_in if expires_in > 0 else 3600)
        return token

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._ensure_token()}"}

    # ---- 公众号操作 ----

    def search(self, keyword: str, limit: int = 8) -> list[WechatCandidate]:
        """按关键词搜索公众号，返回候选列表（含 fakeid，用于后续订阅）。"""
        if not keyword or not keyword.strip():
            raise WeweError("搜索关键词不能为空")

        enc = urllib.parse.quote(keyword.strip())
        url = f"{self.base_url}/api/v1/wx/mps/search/{enc}"
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                resp = client.get(url, headers=self._auth_headers())
        except httpx.HTTPError as e:
            raise WeweError(f"搜索公众号失败：{e}") from e

        if resp.status_code != 200:
            raise WeweError(f"搜索公众号失败（HTTP {resp.status_code}）")

        payload = resp.json()
        rows = _extract_list(payload)
        candidates: list[WechatCandidate] = []
        for row in rows[:limit]:
            fakeid = row.get("fakeid")
            if not fakeid:
                continue
            candidates.append(
                WechatCandidate(
                    fakeid=str(fakeid),
                    nickname=(row.get("nickname") or "").strip(),
                    alias=(row.get("alias") or "").strip(),
                    signature=(row.get("signature") or "").strip(),
                )
            )
        return candidates

    def subscribe(self, mp_name: str, fakeid: str) -> str:
        """订阅一个公众号，返回 we-mp-rss 库内 id（MP_WXS_xxx），
        feed-curator 的 wechat adapter 用这个 id 拉 feed。"""
        if not mp_name or not mp_name.strip():
            raise WeweError("公众号名称不能为空")
        if not fakeid or not fakeid.strip():
            raise WeweError("fakeid 不能为空")

        url = f"{self.base_url}/api/v1/wx/mps"
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                resp = client.post(
                    url,
                    json={"mp_name": mp_name.strip(), "mp_id": fakeid.strip()},
                    headers={**self._auth_headers(), "Content-Type": "application/json"},
                )
        except httpx.HTTPError as e:
            raise WeweError(f"订阅公众号失败：{e}") from e

        if resp.status_code != 200:
            raise WeweError(f"订阅公众号失败（HTTP {resp.status_code}）")

        payload = resp.json()
        if payload.get("code") not in (0, None):
            raise WeweError(f"订阅公众号失败：{payload.get('message')}")

        data = payload.get("data") or {}
        mp_id = data.get("id")
        if not mp_id:
            raise WeweError(f"订阅未返回库内 id：{payload}")
        return str(mp_id)

    def trigger_update(self, mp_id: str) -> None:
        """触发 we-mp-rss 抓取某公众号的最新文章。限流错误（40402）静默忽略，
        因为订阅时通常已触发过一次抓取。"""
        url = f"{self.base_url}/api/v1/wx/mps/update/{mp_id}"
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                resp = client.get(url, headers=self._auth_headers())
        except httpx.HTTPError:
            # 抓取触发是尽力而为，失败不阻断主流程
            return
        if resp.status_code != 200:
            return
        # code 40402 = "请不要频繁更新操作"，属正常限流，无需处理


def _extract_list(payload) -> list:
    """we-mp-rss 接口返回结构不统一（有时 data 直接是 list，有时是 {list:[...]}）。
    这里统一抽出列表。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "items", "rows"):
            if isinstance(data.get(key), list):
                return data[key]
    return []
