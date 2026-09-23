from sqlalchemy import Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# 长文本类型:SQLite 下是 TEXT(无长度限制),MySQL 下 TEXT 仅 64KB 不够装长正文,
# 故用 LONGTEXT(最大 4GB)。with_variant 让两个方言各取所需。
LongText = Text().with_variant(LONGTEXT, "mysql")
