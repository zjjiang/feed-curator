import json


def json_dump(value) -> str:
    """JSON 列的统一序列化:ensure_ascii=False(内容含中文,沿用项目约定)。"""
    return json.dumps(value, ensure_ascii=False)
