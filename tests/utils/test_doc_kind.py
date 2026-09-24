from app.utils.doc_kind import detect_kind


def test_arxiv_abs_page_is_paper():
    assert detect_kind("https://arxiv.org/abs/2606.02578v1") == "paper"


def test_arxiv_pdf_page_is_paper():
    # pdf 与 abs 指向同一篇论文，判成 article 会导致同论文两个 doc
    assert detect_kind("https://arxiv.org/pdf/2606.02578") == "paper"


def test_arxiv_homepage_is_not_paper():
    assert detect_kind("https://arxiv.org/list/cs.AI/recent") == "article"


def test_arxiv_subdomain_is_not_paper():
    assert detect_kind("https://blog.arxiv.org/2026/06/notes/") == "article"


def test_arxiv_mirrored_hosts_are_paper():
    # 身份规范化的前置:www/export 主机的 abs/pdf/html 同样判为 paper
    assert detect_kind("https://www.arxiv.org/abs/2606.02578v1") == "paper"
    assert detect_kind("https://export.arxiv.org/pdf/2606.02578") == "paper"
    assert detect_kind("https://export.arxiv.org/list/cs.AI/recent") == "article"


def test_github_repo_root_is_repo():
    assert detect_kind("https://github.com/viggy28/streambed") == "repo"


def test_github_repo_root_with_trailing_slash_is_repo():
    assert detect_kind("https://github.com/viggy28/streambed/") == "repo"


def test_github_issue_is_article():
    assert detect_kind("https://github.com/jqwik-team/jqwik/issues/708") == "article"


def test_github_file_page_is_article():
    assert detect_kind("https://github.com/stanford-cs336/assignment1-basics/blob/main/CLAUDE.md") == "article"


def test_github_tree_page_is_article():
    assert detect_kind("https://github.com/owner/repo/tree/main/src") == "article"


def test_github_gist_is_article():
    assert detect_kind("https://gist.github.com/skipcloud/f1033afb4fa5681d69fa63458cc95928") == "article"


def test_github_owner_profile_is_article():
    # 只有 owner 没有仓库名，不是仓库
    assert detect_kind("https://github.com/torvalds") == "article"


def test_github_homepage_is_article():
    assert detect_kind("https://github.com/") == "article"


def test_regular_article_url_is_article():
    assert detect_kind("https://36kr.com/p/3851350342325504?f=rss") == "article"
    assert detect_kind("https://simonwillison.net/2026/Jun/11/fable-is-relentlessly-proactive/") == "article"
    assert detect_kind("https://news.ycombinator.com/item?id=48356312") == "article"
