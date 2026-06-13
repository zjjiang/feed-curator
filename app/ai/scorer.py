import json
import time

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Item, Setting
from app.ai.client import LLMClient


def get_user_preferences(db: Session) -> str:
    s = db.query(Setting).filter(Setting.key == "preferences").first()
    return s.value if s and s.value else ""


def get_few_shot_examples(db: Session, max_each: int = 3) -> tuple[list[Item], list[Item]]:
    positives = (
        db.query(Item)
        .filter(Item.is_favorite == 1)
        .filter(Item.title.isnot(None))
        .order_by(Item.fetched_at.desc())
        .limit(max_each)
        .all()
    )
    negatives = (
        db.query(Item)
        .filter(Item.user_rating.isnot(None))
        .filter(Item.user_rating <= 2)
        .order_by(Item.fetched_at.desc())
        .limit(max_each)
        .all()
    )
    return positives, negatives


def build_few_shot_text(positives: list[Item], negatives: list[Item]) -> str:
    if not positives and not negatives:
        return ""
    parts = ["\n\n=== 用户已标注的偏好示例 ==="]
    if positives:
        parts.append("用户喜欢的文章（高分参考）:")
        for p in positives:
            desc = (p.description or p.content_text or "")[:120]
            parts.append(f"- 标题: {p.title}\n  摘要: {desc}")
    if negatives:
        parts.append("\n用户不喜欢的文章（低分参考）:")
        for n in negatives:
            desc = (n.description or n.content_text or "")[:120]
            parts.append(f"- 标题: {n.title}\n  摘要: {desc}")
    parts.append("=== 偏好示例结束 ===\n")
    return "\n".join(parts)


def run_scoring_batch(llm: LLMClient, batch_size: int = 10):
    db = SessionLocal()
    try:
        preferences = get_user_preferences(db)
        positives, negatives = get_few_shot_examples(db)
        few_shot = build_few_shot_text(positives, negatives)

        unscored = (
            db.query(Item)
            .filter(Item.ai_score.is_(None))
            .filter(Item.content_text.isnot(None))
            .filter(Item.content_text != "")
            .order_by(Item.published_at.desc())
            .limit(batch_size)
            .all()
        )

        if not unscored:
            return 0

        scored = 0
        for item in unscored:
            result = llm.score_article(
                title=item.title,
                description=item.description or "",
                content_preview=item.content_text or "",
                user_preferences=preferences,
                few_shot_text=few_shot,
            )
            if result and "score" in result:
                item.ai_score = float(result["score"])
                item.ai_summary = result.get("reason", "")
                item.ai_tags = json.dumps(result.get("tags", []), ensure_ascii=False)
                item.ai_scored_at = int(time.time())
                scored += 1
                print(f"[ai] {item.ai_score:.0f}分 | {item.title[:40]}")
            else:
                item.ai_score = -1
                item.ai_scored_at = int(time.time())
                item.ai_summary = "评分失败"

        db.commit()
        return scored
    finally:
        db.close()


def reset_failed_scores(db: Session) -> int:
    n = db.query(Item).filter(Item.ai_score == -1).count()
    db.query(Item).filter(Item.ai_score == -1).update({
        Item.ai_score: None,
        Item.ai_summary: None,
        Item.ai_scored_at: None,
    })
    db.commit()
    return n


def rescore_all(db: Session) -> int:
    n = db.query(Item).filter(Item.ai_score.isnot(None)).count()
    db.query(Item).filter(Item.ai_score.isnot(None)).update({
        Item.ai_score: None,
        Item.ai_summary: None,
        Item.ai_tags: None,
        Item.ai_scored_at: None,
    })
    db.commit()
    return n
