import json

from app.utils.json_str import json_dump


def test_ensure_ascii_false_chinese_roundtrip():
    authors = ["张三", "Li Wei", "пётр"]
    serialized = json_dump(authors)
    assert "张三" in serialized
    assert json.loads(serialized) == authors


def test_ensure_ascii_false_not_escaped():
    serialized = json_dump({"kw": "具身智能"})
    assert "\\u" not in serialized
