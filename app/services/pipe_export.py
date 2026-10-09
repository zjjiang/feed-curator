"""订阅源清单导出:按平台类型分大类生成 Markdown,提交并 push 到 GitHub。

生成是纯读;推送只操作 docs/pipes.md 一个文件,无变化跳过提交,
非 main 分支跳过导出。失败记 run_log(kind='export'),不影响主流程。
"""

import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Domain, Pipe, RunLog

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_FILES = ("docs/pipes.md", "docs/pipes.json")
AUTO_INTERVAL = 86400
JSON_VERSION = 1

# 内容大类:有序规则,先命中先归类;rss 兜底科技媒体。
# pipe.type 是接入方式不是内容类别(论文源多是 rss),故按 URL/名称匹配。
_CATEGORY_RULES = (
    ("论文与研究",
     lambda u, n: any(k in u for k in ("arxiv", "daily-papers", "papers"))
     or any(k in n for k in ("论文", "Research", "DeepMind", "BAIR"))),
    ("厂商官方",
     lambda u, n: any(k in u for k in ("openai.com", "anthropic.com",
                                       "huggingface.co"))
     or any(k in n for k in ("OpenAI", "Anthropic", "HuggingFace"))),
    ("开发者与独立博客",
     lambda u, n: any(k in u for k in ("hackernews", "lobste.rs", "github",
                                       "simonwillison"))),
)
DEFAULT_RSS_CATEGORY = "科技媒体"
WECHAT_CATEGORY = "微信公众号"
MANUAL_CATEGORY = "手工存入"


def _category_of(pipe: Pipe) -> str:
    override = _safe_config(pipe).get("export_category")
    if override:
        return override
    if pipe.type == "manual":
        return MANUAL_CATEGORY
    if pipe.type == "wechat":
        return WECHAT_CATEGORY
    if pipe.type in ("arxiv", "hf_papers"):
        return "论文与研究"
    if pipe.type == "github":
        return "开发者与独立博客"
    url = _config_summary(pipe)
    for label, match in _CATEGORY_RULES:
        if match(url, pipe.name):
            return label
    return DEFAULT_RSS_CATEGORY


_EXPORT_LOCK = threading.Lock()


class ExportError(RuntimeError):
    pass


def render_pipes_md(db: Session, now: int | None = None) -> str:
    """按内容大类分组的订阅源 Markdown;config 只出摘要字段,不泄漏完整 JSON。"""
    now_ts = now if now is not None else int(time.time())
    domains = {d.id: d.name for d in db.query(Domain).all()}
    pipes = db.query(Pipe).all()

    groups: dict[str, list[Pipe]] = {}
    for p in pipes:
        groups.setdefault(_category_of(p), []).append(p)

    enabled = sum(1 for p in pipes if p.enabled)
    lines = [
        "# feed-curator 订阅源清单",
        "",
        f"> 生成时间 {datetime.fromtimestamp(now_ts).strftime('%Y-%m-%d %H:%M')}"
        f" · 共 {len(pipes)} 个源(启用 {enabled})"
        f" · 机器可读版 [pipes.json](pipes.json)",
        "",
    ]
    for label in CATEGORY_ORDER:
        plist = groups.get(label)
        if not plist:
            continue
        lines += [
            f"## {label}({len(plist)})",
            "",
            "| 名称 | 领域 | 状态 | 间隔 | 最近拉取 | 地址/查询 |",
            "|---|---|---|---|---|---|",
        ]
        for p in sorted(plist, key=lambda x: x.name):
            domain = domains.get(p.domain_id, "共享") if p.domain_id else "共享"
            status = "启用" if p.enabled else "停用"
            lines.append(
                f"| {p.name} | {domain} | {status} | {p.fetch_interval_min}min"
                f" | {_fmt_ts(p.last_fetched_at)} | {_config_summary(p)} |")
        lines.append("")
    return "\n".join(lines) + "\n"


CATEGORY_ORDER = ("论文与研究", "厂商官方", "开发者与独立博客", "科技媒体",
                  "微信公众号", "手工存入")


def build_pipes_json(db: Session, now: int | None = None) -> str:
    """交换格式:导出生成它,导入消费它;domain 存名字以跨库迁移。"""
    now_ts = now if now is not None else int(time.time())
    domains = {d.id: d.name for d in db.query(Domain).all()}
    pipes = db.query(Pipe).order_by(Pipe.type, Pipe.name).all()
    data = {
        "version": JSON_VERSION,
        "exported_at": now_ts,
        "pipes": [
            {
                "type": p.type,
                "name": p.name,
                "config": _safe_config(p),
                "domain": domains.get(p.domain_id) if p.domain_id else None,
                "enabled": p.enabled,
                "fetch_interval_min": p.fetch_interval_min,
            }
            for p in pipes
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _safe_config(pipe: Pipe) -> dict:
    try:
        cfg = json.loads(pipe.config) if pipe.config else {}
    except (ValueError, TypeError):
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def _config_summary(pipe: Pipe) -> str:
    try:
        cfg = json.loads(pipe.config) if pipe.config else {}
    except (ValueError, TypeError):
        return "-"
    if pipe.type == "rss":
        return cfg.get("feed_url") or "-"
    if pipe.type == "arxiv":
        return cfg.get("category") or "-"
    if pipe.type == "github":
        if pipe.domain_id:
            return "按领域关键词自动生成"
        return cfg.get("query") or "-"
    if pipe.type == "hf_papers":
        return cfg.get("base_url") or "-"
    if pipe.type == "wechat":
        return cfg.get("mp_id") or "-"
    return "手工存入"


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _git(args: list[str], timeout: int = 15, check: bool = True):
    proc = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise ExportError(f"git {args[0]} 失败: {(proc.stderr or '').strip()[:200]}")
    return proc


def run_export(db: Session, trigger: str = "manual") -> None:
    """生成 + git 同步(同步执行,由 maybe_start_export 放在后台线程调用)。"""
    log = RunLog(kind="export", trigger=trigger, status="running",
                 total=1, processed=0, created_at=int(time.time()))
    db.add(log)
    db.commit()
    try:
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        if branch != "main":
            log.status = "cancelled"
            log.error = f"当前分支 {branch} 非 main,跳过导出"
            db.commit()
            return

        for rel_path, content in (
            (EXPORT_FILES[0], render_pipes_md(db)),
            (EXPORT_FILES[1], build_pipes_json(db)),
        ):
            path = REPO_ROOT / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _git(["add", *EXPORT_FILES])
        if _git(["diff", "--cached", "--quiet", "--", *EXPORT_FILES],
                check=False).returncode == 0:
            log.status = "done"
            log.error = "内容无变化,跳过提交"
            log.processed = 1
            db.commit()
            return

        _git(["commit", "-m", "docs(pipes): 更新订阅源清单",
              "-m", "自动生成,内容随管道配置变化。"])
        try:
            _git(["push"], timeout=30)
        except ExportError:
            _git(["pull", "--rebase"], timeout=30)
            _git(["push"], timeout=30)
        log.status = "done"
        log.processed = 1
    except Exception as e:  # noqa: BLE001 — 导出失败只记日志
        log.status = "failed"
        log.error = f"{type(e).__name__}: {e}"[:500]
    finally:
        db.commit()


def export_due() -> bool:
    """距上次成功导出是否已超过 24 小时。"""
    db = SessionLocal()
    try:
        last = (
            db.query(RunLog)
            .filter(RunLog.kind == "export", RunLog.status == "done")
            .order_by(RunLog.created_at.desc())
            .first()
        )
        return last is None or int(time.time()) - last.created_at >= AUTO_INTERVAL
    finally:
        db.close()


def maybe_start_export(trigger: str = "manual") -> bool:
    """单飞启动后台导出;auto 触发受 24h 到期检查约束,manual 无视到期。"""
    if not _EXPORT_LOCK.acquire(blocking=False):
        return False
    if trigger == "auto" and not export_due():
        _EXPORT_LOCK.release()
        return False

    def worker():
        db = SessionLocal()
        try:
            run_export(db, trigger=trigger)
        finally:
            db.close()
            _EXPORT_LOCK.release()

    threading.Thread(target=worker, daemon=True, name="pipe-export").start()
    return True
