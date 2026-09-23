# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI-driven personal reading pipeline. Pulls content from RSS/WeChat/arXiv pipes into **MySQL**, classifies every document into one of three entity types by URL (`article` / `paper` / `repo`), runs an LLM over each document to produce summary + keypoints + domain membership + tech/business tag + 1-5 star rating, and serves a server-rendered web UI. FastAPI + APScheduler, single process, no separate worker. Runs natively at `:9003` (port 9000 is taken by system php-fpm).

The 2026-09 rebuild (`openspec/changes/rebuild-domain-model/`) replaced the old flat `Source`/`Item` tables with a class-table-inheritance model (`doc` identity layer + `paper`/`repo`/`article` entity tables), replaced free-text categories with `domain` (AI picks domains per doc), and renamed sources to `pipe` (shared vs domain-derived). The old SQLite database was migrated and deleted; the only rollback is `data/feed-curator.db.bak-20260923`.

## Workflow rules

- **Development goes through OpenSpec.** Non-trivial work starts as an OpenSpec
  change (`openspec new change "<name>"` → proposal → specs → design → tasks) and
  gets implemented by working through its task list (`/opsx:apply`). Do not
  implement features ad hoc outside a change.
- **Never push directly to `main`.** Push a feature branch, open a PR, merge it,
  then sync local `main`. `git push origin main` (or any direct push to `main`)
  is forbidden — even when the user says "push 一个版本", do it via PR.

## Commands

```bash
uv sync                                    # install deps (Python 3.14+)
uv run pytest                              # test suite (150 tests, in-memory SQLite)
uv run pytest --cov=app                    # coverage (threshold: 80%)
```

Native run (the current deployment mode):

```bash
# .env (gitignored) provides DATABASE_URL and DEEPSEEK_API_KEY
set -a && source .env && set +a
uv run --no-sync uvicorn app.main:app --port 9003 --host 127.0.0.1
curl http://localhost:9003/health          # → {"status":"ok"}
```

`DATABASE_URL` targets the **local Homebrew MySQL** (not the old `db-mp` container):
`mysql+pymysql://root:...@127.0.0.1:3306/feed_curator?charset=utf8mb4`. If unset,
`app/db.py` falls back to SQLite at `data/feed-curator.db` (that file was deleted
after migration, so the fallback yields an empty DB — do not rely on it).

`DEEPSEEK_API_KEY` enables AI analysis; without it the app runs except analysis
(scheduler cycle no-ops, `/api/analyze/run` reports "未配置").

There is no linter or build step configured. Historical docker-compose/db-mp
topology docs in `docs/deployment.md` predate the rebuild; the containers
(`feed-curator`, `we-mp-rss`, `rsshub`, `db-mp`) were all stopped/absent at
rebuild time.

## Database

MySQL via SQLAlchemy + pymysql, 13 tables defined in `app/models/`:

- **Identity + entities**: `doc` (id, kind, url_key UNIQUE, url, title, sort_time,
  first_seen_at, last_modified_at) + `paper` / `repo` / `article` whose `id` is
  both PK and FK to `doc.id`. Entity type is decided by URL
  (`app/utils/doc_kind.py`): arxiv.org abs/pdf/html → paper; github.com with
  exactly 2 path segments → repo; everything else → article.
- **Pipeline**: `domain` (keywords as JSON array — feeds the LLM and generates
  derived-pipe queries), `pipe` (`domain_id` NULL = shared, set = derived),
  `discovery` (UNIQUE(pipe_id, external_id), per-pipe ingestion record).
- **Analysis**: `analysis` (append-only; effective judgment = latest
  `status='ok'` row per doc), `membership` (PK (doc_id, domain_id),
  `assigned_by` ai|manual).
- **Ops**: `reading` (1:1 with doc, lazy-created), `document_link` + `suggestion`
  (reserved for future agents), `run_log` (kind `fetch|analyze|fulltext|agent` —
  the old Job+SyncLog merge).

Dedup key is the **normalized URL** (`app/utils/url_key.py`: lowercase host,
strip trailing slash, strip fragment, strip only `utm_*` params). All writers
must go through `app/writer.py:upsert_doc()` — one transaction writes doc +
entity row + discovery; nothing else may insert into these tables directly.

**MySQL gotchas (don't reintroduce):**
- Long text columns use the `LongText` variant (`Text().with_variant(LONGTEXT,
  "mysql")`) — plain `Text` truncates at 64 KB on MySQL.
- `doc.url_key` is `String(500)` (2000 bytes in utf8mb4, under the 3072-byte
  InnoDB index limit).
- JSON columns are serialized via `app/utils/json_str.json_dump()`
  (`ensure_ascii=False` — content is Chinese).
- Schema must compile on both MySQL and SQLite (`tests/models/test_schema.py`
  asserts both dialects).

## Directory layout

```
app/
├── main.py            # FastAPI app, JSON API, lifespan; fetch + analyze scheduler cycles; mounts MCP at /mcp
├── db.py              # engine/session; init_db = create_all + zombie-run cleanup + orphan report
├── writer.py          # THE single write entry: upsert_doc() + refresh_repo() + check_orphans()
├── mcp_server.py      # MCP tools at /mcp: add_rss, list_pipes, save_url, domains, recommend, job_status
├── adapters/          # rss / arxiv / wechat — return FetchedItem, never touch the DB
├── ai/
│   ├── client.py      # LLMClient.analyze() → {summary, keypoints, domains, article_kind, stars}; output defense lives here
│   └── analyzer.py    # orchestration: select_doc_ids, analyze_doc, materialize membership + kind_tag
├── jobs/
│   ├── fetcher.py     # fetch_source (pipe → upsert_doc, run_log), resolve_fetch_config (derived pipes)
│   └── runner.py      # analyze job executor: thread pool (5 workers), single-job lock, cancel event
├── services/
│   ├── fulltext.py            # fetch+parse article pages; SSRF protection (rebuilt from lost archive_service)
│   ├── fulltext_backfill.py   # rate-limited batch backfill of short articles (run_log kind='fulltext')
│   ├── manual_service.py      # "save URL" entry (manual pipe type)
│   ├── doc_fields.py          # FetchedItem → (kind, detail) field routing, shared by all writers
│   ├── source_service.py      # create_pipe helpers
│   └── wewe_client.py         # we-mp-rss HTTP client (login/search/subscribe)
├── utils/             # url_key, doc_kind, json_str, html_clean
└── web/
    ├── pages.py       # server-rendered routes: reader (/ feed, /docs/{id}) + admin (/admin, /admin/pipes, /admin/domains; legacy paths redirect)
    └── templates/     # layout, index, doc, domains, pipes, ops
scripts/migration/     # one-shot migration + verify + fulltext backfill runner (SQLite source is deleted; kept as record)
tests/                 # pytest suite; conftest provides per-test in-memory SQLite (StaticPool)
```

## Architecture

Pipeline: **pipe → adapter → fetcher → upsert_doc → (fulltext backfill) → AI analysis → web**.

- **Fetch cycle** (every 60s): enabled pipes whose type has an adapter and whose
  interval elapsed get `fetch_source`. Each item is kind-detected by URL,
  fields routed by `doc_fields.build_detail`, and written through
  `upsert_doc`. Failures are isolated per pipe (and per item) and land in
  `pipe.last_error` + `run_log`. `github` pipes stay disabled — repo collection
  is a separate future project (contract: `upsert_doc(kind, ...)`).
- **Analyze cycle** (every 300s): if an API key is configured, docs are pending,
  and no analyze run is active → start one. `runner.py` keeps the production
  concurrency model: in-memory lock + DB status double-guard, thread pool with
  one session per worker, cancel Event, zombie-run cleanup on startup.
- **Fulltext backfill**: not part of fetch. Triggered from `/ops` (50-doc
  batches, 1s/request) or `scripts/migration/run_fulltext_backfill.py`. Only
  articles with `word_count < 500` and older than a 3-day cooldown are fetched.
  Failures keep the short content and never block anything.
- **Derived pipes**: a derived pipe's query is generated at fetch time from its
  domain's keywords (`resolve_fetch_config`) — edit domain keywords and the next
  fetch uses the new query. Shared pipes have no domain.

### AI analysis contract

`LLMClient.analyze()` sends title/description/content-preview + the domain list
and must return strict JSON `{summary, keypoints[], domains[], article_kind,
stars}`. Defense rules (all in client.py, all tested): strip markdown fences;
stars out of 1-5 or missing → failure; unknown domain names dropped; invalid
article_kind → cleared (not a failure); articles with insufficient content get
`article_kind` forced empty — never guessed from the title. Results append to
`analysis`; a successful judgment materializes into `membership` (ai rows only
— manual rows are never auto-removed) and `article.kind_tag`.

### Web layer

Server-rendered Jinja2, POST-redirect-GET, split into a reader surface and an
admin surface (separate navs in layout.html). Reader: `/` is the subscribe
feed (mixed entity stream, filters: domain/kind/tech-business/favorites, sort
by time or stars), `/docs/{id}` shows content + latest analysis + reading
controls. Admin: `/admin` overview (run_log dashboard + manual triggers),
`/admin/pipes` channels, `/admin/domains` domains. Dismissed docs vanish from
the default list but keep content and judgments.

## Conventions specific to this codebase

- **Epoch-int timestamps everywhere** (`int(time.time())`), and model columns
  have NO ORM defaults — writers fill them (writer.py, analyzer.py, fetcher.py).
- **`sort_time`** is the cross-entity sort key: published_at for articles,
  submitted_at for papers, pushed_at for repos. `refresh_repo()` keeps
  `repo.pushed_at` and `doc.sort_time` in sync.
- **JSON in Text columns** goes through `json_dump()` (ensure_ascii=False).
- **Adapters must not touch the DB** — persistence is fetcher/writer territory.
- **Each runner worker uses its own session**; never share a Session across
  threads.
- **Heavy/optional imports stay lazy** (`LLMClient`, runner functions) so the
  app starts without an API key.
- **Tests first**: every module above has unit tests (in-memory SQLite via
  `tests/conftest.py`); network-touching code is tested with
  `httpx.MockTransport` and offline HTML/fixtures. Keep coverage ≥ 80%.

## Gotchas across the stack (China network)

- ghcr.io / docker.io are unreachable directly; PyPI/GitHub raw access is slow
  or blocked. GitHub clone works over SSH, not HTTPS. `export.arxiv.org` and
  some foreign sites intermittently fail SSL handshake — the fulltext backfill
  is resumable/idempotent, just rerun it when the network cooperates.
- `we-mp-rss` (WeChat feeds, :9001) and RSSHub (:9002/:1200) were down at
  rebuild time; the wechat pipe and the 虎嗅 pipe will log fetch errors until
  those services are back.
- 机器之心 (jiqizhixin) is WAF-blocked — its pipe stays disabled.
