from app.utils.url_key import normalize_url


def test_scheme_is_normalized_but_not_upgraded():
    assert normalize_url("http://example.com/a") == normalize_url("http://example.com/a")
    assert normalize_url("HTTP://example.com/a") == "http://example.com/a"


def test_host_is_lowercased():
    assert normalize_url("https://EXAMPLE.com/path") == "https://example.com/path"


def test_trailing_slash_is_stripped():
    assert normalize_url("https://example.com/path/") == normalize_url("https://example.com/path")


def test_root_path_trailing_slash_is_preserved_as_root():
    assert normalize_url("https://example.com/") == "https://example.com/"
    assert normalize_url("https://example.com") == "https://example.com/"


def test_utm_params_are_stripped():
    with_utm = "https://example.com/post?utm_source=rss&utm_medium=email&utm_campaign=weekly"
    without_utm = "https://example.com/post"
    assert normalize_url(with_utm) == normalize_url(without_utm)


def test_identity_params_are_preserved():
    # news.ycombinator.com 用 id 承载文档身份，不能剥
    assert normalize_url("https://news.ycombinator.com/item?id=1") != normalize_url(
        "https://news.ycombinator.com/item?id=2"
    )
    assert (
        normalize_url("https://news.ycombinator.com/item?id=48356312")
        == "https://news.ycombinator.com/item?id=48356312"
    )


def test_youtube_video_id_param_is_preserved():
    assert (
        normalize_url("https://www.youtube.com/watch?v=abc123")
        == "https://www.youtube.com/watch?v=abc123"
    )


def test_mixed_tracking_and_identity_params_keeps_identity_drops_tracking():
    url = "https://example.com/watch?v=abc123&utm_source=rss"
    assert normalize_url(url) == "https://www.youtube.com/watch?v=abc123".replace(
        "www.youtube.com", "example.com"
    )


def test_fragment_is_dropped():
    assert normalize_url("https://example.com/post#section") == "https://example.com/post"


def test_unknown_query_param_is_preserved_by_default():
    # 白名单剥离策略：陌生参数默认保留，避免误伤身份参数
    assert (
        normalize_url("https://example.com/post?jobRef=16449")
        == "https://example.com/post?jobRef=16449"
    )


def test_non_tracking_rss_marker_param_is_preserved():
    # 36kr 的 ?f=rss 是 host 特定约定，值恒定但不在通用白名单内，保留
    assert (
        normalize_url("https://36kr.com/p/3851350342325504?f=rss")
        == "https://36kr.com/p/3851350342325504?f=rss"
    )


def test_simonwillison_anchor_duplicates_merge():
    # dry-run 实测的真实重复形态：带/不带 #atom-everything 锚点归一为同一 url_key
    assert normalize_url(
        "https://simonwillison.net/2026/Jun/11/fable-is-relentlessly-proactive/#atom-everything"
    ) == normalize_url("https://simonwillison.net/2026/Jun/11/fable-is-relentlessly-proactive/")


# ---- 微信链接:host 级身份参数特例 ----

def test_wechat_share_param_variants_merge():
    # 同一篇文章两次分享:每分享参数(chksm/scene)不同、参数顺序也不同,
    # 归一化后必须视为同一 url_key
    a = ("https://mp.weixin.qq.com/s?__biz=MzA3MDM3NjE5Nw==&mid=2650984123"
         "&idx=1&sn=abc123&chksm=e1&scene=21#wechat_redirect")
    b = ("https://mp.weixin.qq.com/s?scene=126&sn=abc123&idx=1&mid=2650984123"
         "&__biz=MzA3MDM3NjE5Nw==&chksm=e9")
    assert normalize_url(a) == normalize_url(b)


def test_wechat_share_params_are_dropped_from_key():
    key = normalize_url(
        "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn=abc&chksm=x&scene=21")
    assert "chksm" not in key and "scene" not in key


def test_wechat_identity_param_differs_not_merged():
    base = "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&sn={}"
    a = normalize_url(base.format("aaa"))
    b = normalize_url(base.format("bbb"))
    assert a != b
    # mid / __biz 任一不同同样不归并
    assert normalize_url("https://mp.weixin.qq.com/s?__biz=A&mid=2&idx=1&sn=aaa") != a
    assert normalize_url("https://mp.weixin.qq.com/s?__biz=B&mid=1&idx=1&sn=aaa") != a


def test_wechat_short_link_without_query_unchanged():
    assert normalize_url(
        "https://mp.weixin.qq.com/s/abcDEF123") == "https://mp.weixin.qq.com/s/abcDEF123"


def test_non_wechat_host_keeps_unknown_params():
    # 特例只作用于 mp.weixin.qq.com,其他 host 的白名单行为不变
    assert normalize_url(
        "https://example.com/post?chksm=abc") == "https://example.com/post?chksm=abc"
