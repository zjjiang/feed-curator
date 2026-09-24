"""出网 HTTP 统一收口:代理探测 + 境内外差异化的连接顺序 + 一次路径回退。

代理从环境读取:OUTBOUND_PROXY(完整地址)> CLASH_PROXY_PORT(本机
Clash 端口)> 标准 HTTPS_PROXY/HTTP_PROXY/ALL_PROXY > 无代理全直连。
境外源(arxiv.org 等)代理优先、连接失败直连回退;国内可达源
(hf-mirror.com 等)直连优先、失败代理回退。回退只针对连接类错误
(拒绝/超时/SSL 握手),HTTP 状态错误与路径无关、不重试。
"""

import os
from urllib.parse import urlsplit

import httpx

_DEFAULT_TIMEOUT = 30.0

# 实测国内直连可达的 host 后缀(hf-mirror 完整代理 HF API,代理反而绕路)
_DOMESTIC_SUFFIXES = ("hf-mirror.com",)


def resolve_proxy() -> str | None:
    """按优先级探测出网代理;未配置返回 None(全直连,与历史行为一致)。"""
    dedicated = os.environ.get("OUTBOUND_PROXY", "").strip()
    if dedicated:
        return dedicated
    clash_port = os.environ.get("CLASH_PROXY_PORT", "").strip()
    if clash_port:
        return f"http://127.0.0.1:{clash_port}"
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def is_domestic(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in _DOMESTIC_SUFFIXES)


def request(
    url: str,
    *,
    domestic: bool | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Response:
    """带路径回退的单次 GET。连接类错误切换代理/直连重试一次。

    - domestic=None 时按 host 推断;显式传入覆盖推断(适配器自知的源)。
    - 状态错误(4xx/5xx)直接抛 HTTPStatusError,不切换路径。
    - transport 仅供测试注入(MockTransport)。
    """
    proxy = resolve_proxy()
    if domestic is None:
        domestic = is_domestic(url)
    if proxy is None:
        attempts: list[bool] = [False]
    elif domestic:
        attempts = [False, True]
    else:
        attempts = [True, False]

    client_kwargs: dict = {"timeout": timeout, "trust_env": False,
                           "follow_redirects": follow_redirects}
    if transport is not None:
        client_kwargs["transport"] = transport

    last_error: httpx.TransportError | None = None
    for use_proxy in attempts:
        try:
            with httpx.Client(proxy=proxy if use_proxy else None,
                              **client_kwargs) as client:
                resp = client.get(url, headers=headers)
            if resp.status_code < 400:
                # <400 原样返回:3xx 留给调用方逐跳处理(fulltext SSRF 复检),
                # 304 是条件请求的正常结果;httpx 的 raise_for_status 对 3xx 也抛错
                return resp
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError:
            raise
        except httpx.TransportError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error
