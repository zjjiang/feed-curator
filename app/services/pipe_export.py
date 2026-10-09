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
EXPORT_REL = "docs/pipes.md"
AUTO_INTERVAL = 86400

TYPE_ORDER = ("rss", "arxiv", "github", "hf_papers", "wechat", "manual")
TYPE_LABELS = {
    "rss": "RSS 订阅",
    "arxiv": "arXiv 论文",
    "github": "GitHub 仓库",
    "hf_papers": "HuggingFace 论文",
    "wechat": "微信公众号",
    "manual": "手工存入",
}

_EXPORT_LOCK = threading.Lock()


class ExportError(RuntimeError):
    pass


def render_pipes_md(db: Session, now: int | None = None) -> str:
    """按平台类型分大类的订阅源 Markdown;config 只出摘要字段,不泄漏完整 JSON。"""
    now_ts = now if now is not None else int(time.time())
    domains = {d.id: d.name for d in db.query(Domain).all()}
    pipes = db.query(Pipe).all()

    groups: dict[str, list[Pipe]] = {}
    for p in pipes:
        groups.setdefault(p.type, []).append(p)

    enabled = sum(1 for p in pipes if p.enabled)
    lines = [
        "# feed-curator 订阅源清单",
        "",
        f"> 生成时间 {datetime.fromtimestamp(now_ts).strftime('%Y-%m-%d %H:%M')}"
        f" · 共 {len(pipes)} 个源(启用 {enabled})",
        "",
    ]
    for type_ in TYPE_ORDER:
        plist = groups.get(type_)
        if not plist:
            continue
        lines += [
            f"## {TYPE_LABELS[type_]}({len(plist)})",
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

        path = REPO_ROOT / EXPORT_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_pipes_md(db), encoding="utf-8")
        _git(["add", EXPORT_REL])
        if _git(["diff", "--cached", "--quiet", "--", EXPORT_REL],
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
