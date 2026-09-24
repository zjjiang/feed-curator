from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# 白名单剥离：只剥这些追踪参数，其余一律保留（包括未见过的陌生参数）。
# 依据 dry-run 实际数据（design.md 决策 2）：363 条带 query 的 URL 里，
# utm_* 是唯一稳定重复、跨源出现的追踪参数；id/v/p/t/page 等均是承载身份的参数，MUST NOT 剥。
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES: frozenset[str] = frozenset()

# host 级身份参数 keep-list：每分享会更换参数的平台，只保留承载身份的参数
# 并按参数名排序（分享参数的顺序也不稳定）。特例仅作用于列出的 host，
# 其余 host 的归一化行为不受影响。
# 微信文章身份四参：__biz=公众号、mid=推送、idx=多文序号、sn=签名；
# chksm/scene 等每分享变化，全部剥离。
_HOST_IDENTITY_PARAMS: dict[str, frozenset[str]] = {
    "mp.weixin.qq.com": frozenset({"__biz", "idx", "mid", "sn"}),
}


def normalize_url(url: str) -> str:
    """归一化 URL 作为全局去重键。

    规则：小写 scheme 与 host、去掉 fragment、去掉末尾斜杠（根路径 "/" 保留)、
    按白名单剥离追踪参数，其余 query 参数原样保留且顺序不变；
    命中 host 级 keep-list 的链接只保留身份参数（排序后重编码）。
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    host = (parts.hostname or "").lower()

    kept_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    ]
    identity_params = _HOST_IDENTITY_PARAMS.get(host)
    if identity_params is not None:
        kept_params = sorted(
            ((k, v) for k, v in kept_params if k in identity_params),
            key=lambda kv: kv[0],
        )
    query = urlencode(kept_params)

    return urlunsplit((scheme, netloc, path, query, ""))


def _is_tracking_param(key: str) -> bool:
    if key in _TRACKING_PARAM_NAMES:
        return True
    return key.startswith(_TRACKING_PARAM_PREFIXES)
