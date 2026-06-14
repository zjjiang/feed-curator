import json
import time

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Item, Setting
from app.ai.client import LLMClient

CATEGORIES_KEY = "categories"


def get_categories(db: Session) -> list[dict[str, str]]:
    """读取用户预定义的分类表。存为 Setting 表 categories 记录(JSON 数组)。

    每项形如 {"name": "AI", "desc": "模型进展与应用"}。
    """
    s = db.query(Setting).filter(Setting.key == CATEGORIES_KEY).first()
    if not s or not s.value:
        return []
    try:
        data = json.loads(s.value)
    except json.JSONDecodeError:
        return []
    out = []
    for c in data if isinstance(data, list) else []:
        if isinstance(c, dict) and c.get("name"):
            out.append({"name": str(c["name"]).strip(), "desc": str(c.get("desc", "")).strip()})
    return out


def save_categories(db: Session, categories: list[dict[str, str]]) -> None:
    value = json.dumps(categories, ensure_ascii=False)
    s = db.query(Setting).filter(Setting.key == CATEGORIES_KEY).first()
    if not s:
        s = Setting(key=CATEGORIES_KEY, value=value, updated_at=int(time.time()))
        db.add(s)
    else:
        s.value = value
        s.updated_at = int(time.time())
    db.commit()


def run_scoring_batch(llm: LLMClient, batch_size: int = 10):
    """处理一批未处理的文章:AI 阅读 → 摘要+要点+分类+星级,写回库。"""
    db = SessionLocal()
    try:
        categories = get_categories(db)

        unprocessed = (
            db.query(Item)
            .filter(Item.ai_score.is_(None))
            .filter(Item.content_text.isnot(None))
            .filter(Item.content_text != "")
            .order_by(Item.published_at.desc())
            .limit(batch_size)
            .all()
        )

        if not unprocessed:
            return 0

        processed = 0
        for item in unprocessed:
            result = llm.process_article(
                title=item.title,
                description=item.description or "",
                content_preview=item.content_text or "",
                categories=categories,
            )
            if result:
                item.ai_score = result["stars"]
                item.ai_summary = result["summary"]
                item.ai_keypoints = json.dumps(result["keypoints"], ensure_ascii=False)
                item.ai_tags = json.dumps(result["categories"], ensure_ascii=False)
                item.ai_scored_at = int(time.time())
                processed += 1
                tags = "/".join(result["categories"]) or "无分类"
                print(f"[ai] {result['stars']}★ [{tags}] | {item.title[:40]}")
            else:
                item.ai_score = -1
                item.ai_scored_at = int(time.time())
                item.ai_summary = "处理失败"

        db.commit()
        return processed
    finally:
        db.close()


def reset_failed_scores(db: Session) -> int:
    """把处理失败(score=-1)的文章清空,让下一轮重试。"""
    n = db.query(Item).filter(Item.ai_score == -1).count()
    db.query(Item).filter(Item.ai_score == -1).update({
        Item.ai_score: None,
        Item.ai_summary: None,
        Item.ai_keypoints: None,
        Item.ai_tags: None,
        Item.ai_scored_at: None,
    })
    db.commit()
    return n


def rescore_all(db: Session) -> int:
    """清空所有已处理结果,让调度器按当前分类表重新处理全部文章。"""
    n = db.query(Item).filter(Item.ai_score.isnot(None)).count()
    db.query(Item).filter(Item.ai_score.isnot(None)).update({
        Item.ai_score: None,
        Item.ai_summary: None,
        Item.ai_keypoints: None,
        Item.ai_tags: None,
        Item.ai_scored_at: None,
    })
    db.commit()
    return n
