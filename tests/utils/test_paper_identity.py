"""论文身份规范化:arXiv 链接多形态归并为 https://arxiv.org/abs/{id}。"""

import pytest

from app.utils.paper_identity import canonical_paper_url

PAPER_ID = "2606.02578"


@pytest.mark.parametrize("section", ["abs", "pdf", "html"])
@pytest.mark.parametrize("version", ["", "v1", "v2"])
@pytest.mark.parametrize("host", ["arxiv.org", "www.arxiv.org", "export.arxiv.org"])
def test_all_url_shapes_merge_to_canonical_abs(host, version, section):
    url = f"https://{host}/{section}/{PAPER_ID}{version}"
    assert canonical_paper_url(url) == f"https://arxiv.org/abs/{PAPER_ID}"


def test_http_scheme_still_canonicalizes():
    # arXiv API 的 atom <id> 与 alternate link 是 http 形态
    assert canonical_paper_url(
        f"http://arxiv.org/abs/{PAPER_ID}v1") == f"https://arxiv.org/abs/{PAPER_ID}"


def test_query_and_fragment_are_dropped():
    url = f"https://arxiv.org/pdf/{PAPER_ID}v2?download=1#page-3"
    assert canonical_paper_url(url) == f"https://arxiv.org/abs/{PAPER_ID}"


def test_five_digit_id_supported():
    assert canonical_paper_url(
        f"https://arxiv.org/abs/2609.25804v1") == "https://arxiv.org/abs/2609.25804"


def test_non_arxiv_url_untouched():
    url = "https://example.com/pdf/2606.02578v2"
    assert canonical_paper_url(url) == url


def test_arxiv_subdomain_untouched():
    url = "https://blog.arxiv.org/2026/06/notes/"
    assert canonical_paper_url(url) == url


@pytest.mark.parametrize("url", [
    "https://arxiv.org/list/cs.AI/recent",       # 非 abs/pdf/html 段
    "https://arxiv.org/abs/",                    # 无 id 段
    "https://arxiv.org/abs/latest",              # id 形态不匹配
    "https://arxiv.org/abs/260602578",           # 缺点号
    "https://arxiv.org/help",                    # 单段路径
])
def test_unparseable_arxiv_url_untouched(url):
    assert canonical_paper_url(url) == url
