# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI-driven personal reading pipeline. Pulls articles from multiple sources (RSS, WeChat public accounts, arXiv), stores them in **MySQL**, and has an LLM **read each article** to produce a summary, key points, category tags, and a 1–5 star quality rating. Serves a server-rendered web UI. FastAPI + APScheduler, single process, no separate worker. Runs locally at `:9003` (port 9000 is taken by system php-fpm, so this app uses 9003 — not the 8002 some older docs mention).

The earlier design scored articles 1–10 against a free-text *preference* and behavior-mined few-shot examples. That was removed. The pipeline is now: read → summarize → extract key points → tag from a user-defined category list → rate 1–5 stars. There is no preference/few-shot learning anymore.

## Commands

```bash
docker compose up -d --build                                     # preferred: containerized, auto-restarts on reboot
docker compose logs -f                                           # tail container logs
curl http://localhost:9003/health                                # health check → {"status":"ok"}
```

feed-curator runs as a Docker container (`docker-compose.yml`), connecting to the
`db-mp` MySQL container over the `feed-net` network. Config comes from `.env`
(gitignored; copy from `.env.example`). See `docs/deployment.md` for the full
architecture and the one-time setup (db migration, `feed-net`, registry mirrors).

Running natively with uv still works for dev, but you must export `DATABASE_URL`
first (or have it in `.env`) or the app silently falls back to local SQLite:

```bash
uv sync                                                          # install deps (Python 3.14+)
export DATABASE_URL="mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/feed_curator?charset=utf8mb4"
export DEEPSEEK_API_KEY="sk-..."   # optional; without it, fetch works but AI processing is disabled
uv run --no-sync uvicorn app.main:app --port 9003 --host 0.0.0.0
```

The container has `restart: unless-stopped` + a `/health` healthcheck, so it
auto-starts on reboot. Native runs are `nohup` and do NOT auto-start.

There is no test suite, linter, or build step configured. The project ships as-is.

## Database

**MySQL** via SQLAlchemy + `pymysql`. The app connects to the **`db-mp` Docker MySQL
container** (database `feed_curator`, account `feed_curator`). This is the *same*
MySQL instance that backs we-mp-rss (`we_mp_rss` db) — both DBs now live in one
container after the consolidation (see `docs/deployment.md`).

**Connection differs by where the app runs:**
- **In container (default):** `DATABASE_URL` targets the service name `db-mp:3306`
  over the `feed-net` network — no host port, no IPv4/IPv6 ambiguity.
  `mysql+pymysql://feed_curator:PASSWORD@db-mp:3306/feed_curator?charset=utf8mb4`
- **Native dev (legacy Homebrew MySQL, now stopped):** if you start the old
  Homebrew `mysqld`, use `127.0.0.1` not `localhost` — historically two MySQLs
  shared 3306 (Homebrew on IPv4, db-mp on `*` incl. IPv6) and `localhost` could
  hit the wrong one. After consolidation only db-mp listens on 3306.

`app/db.py` reads `DATABASE_URL` from the environment. If set → MySQL. If unset →
falls back to local SQLite at `data/feed-curator.db`. (History: SQLite → db-mp's
MySQL → local Homebrew MySQL → **back to db-mp** (consolidated, container-native).
The Homebrew MySQL is stopped but its data is retained for rollback.)

**SQLite → MySQL gotchas already handled in `models.py`** (don't reintroduce them):
- `Item.content_text` / `content_html` / `description` use `LongText` (= `LONGTEXT` on MySQL, `TEXT` on SQLite). Plain `Text` maps to MySQL `TEXT` (64 KB cap) and truncates long articles — one Anthropic post is ~80 KB of HTML.
- `Item.external_id` is `String(255)` (was 512) so it fits MySQL's utf8mb4 index byte limit inside the `(source_id, external_id)` unique constraint.
- `Item.author` is `Text`, not `String(255)` — arXiv author lists overflow 255 chars.
- Tables are `utf8mb4_unicode_ci`. (When inspecting via `docker exec ... mysql`, pass `--default-character-set=utf8mb4` or Chinese shows as `???` — that's a client display issue, not corruption.)

## Directory layout

```
app/
├── main.py            # FastAPI app, JSON API, lifespan; two APScheduler jobs (fetch + auto-process); mounts MCP at /mcp
├── db.py              # engine/session; DATABASE_URL → MySQL else SQLite; init_db + light migration / zombie-job cleanup
├── models.py          # Source, Item, Setting, Job tables; LongText variant helper
├── mcp_server.py      # MCP server mounted at /mcp — tools to add/search/subscribe sources (used by local MCP clients)
├── adapters/          # source-type plugins (extension point)
│   ├── base.py        # SourceAdapter ABC + FetchedItem dataclass
│   ├── rss.py         # feedparser-based; also reaches RSSHub-bridged sources
│   ├── wechat.py      # HTTP JSON feed from we-mp-rss
│   └── arxiv.py       # arXiv Atom API XML
├── ai/
│   ├── client.py      # DeepSeek-compatible chat client; process_article() → {summary, keypoints, categories, stars}
│   └── scorer.py      # get/save_categories, reset_failed_scores, rescore_all (run_scoring_batch is legacy, unused by scheduler)
├── jobs/
│   ├── fetcher.py     # fetch_source + _upsert_item (dedup)
│   └── runner.py      # async processing job: thread-pool worker, single-job lock, progress tracking
├── services/          # support layer for mcp_server
│   ├── source_service.py  # create_source / create_rss_source / create_wechat_source
│   └── wewe_client.py     # we-mp-rss HTTP client (search/subscribe WeChat accounts)
├── utils/
│   └── html_clean.py  # HTML → text, word-count estimate
└── web/
    ├── pages.py       # server-rendered Jinja2 routes (separate from JSON API)
    └── templates/     # items.html, sources.html, settings.html, jobs.html, layout.html
```

## Architecture

The pipeline is **source → adapter → fetcher → DB → AI processing → web**, driven by background jobs in `app/main.py`:

- `_run_fetch_cycle` runs every 60s. Checks each enabled `Source` against its `fetch_interval_min` and calls `fetch_source` when due.
- `_run_scoring_cycle` runs every 300s. If a key is configured and there are unprocessed items **and no job is already running**, it creates an `auto`-triggered processing Job. No-ops without `DEEPSEEK_API_KEY`.

### Async processing jobs (`app/jobs/runner.py`)

Article processing is modeled as a **Job**. Both the manual "全量处理" button and the auto scheduler go through the same `start_process_job(trigger)`:

- **Single global job**: an in-memory lock + a DB `status='running'` check ensure only one job runs at a time. A second trigger reuses the running job rather than creating a duplicate (returns `created=False`).
- **Thread-pool concurrency**: a daemon thread drives a `ThreadPoolExecutor` (`MAX_WORKERS = 5`) calling the LLM in parallel. Each worker uses its own `SessionLocal` (SQLite/MySQL cross-thread safety).
- **Progress**: each finished article updates the Job's `processed/succeeded/failed` counts, so the frontend can poll `/api/jobs/{id}` and animate a progress bar.
- **Cancel**: `cancel_job(id)` sets a `threading.Event`; in-flight items finish, remaining ones are skipped, status → `cancelled`.
- **Crash recovery**: on startup `init_db()` marks any leftover `running` job as `failed` (the thread died with the process), so the lock can't get stuck.

The fetch/process jobs are the only writers in normal operation; API/web endpoints provide manual triggers and user actions (favorite, read).

### Adapters (the extension point)

`app/adapters/` is where new source types plug in. Every adapter subclasses `SourceAdapter` (`base.py`), implements `fetch(config: dict) -> list[FetchedItem]`, and is registered by `type` string in `ADAPTERS` (`__init__.py`). `FetchedItem` is the normalized shape every adapter must produce — the fetcher and DB layer know nothing about RSS/WeChat/arXiv specifics. To add a source type: write the adapter, register it, done. Existing types: `rss` (feedparser, also used to reach RSSHub-bridged sources like 虎嗅 via `http://localhost:9002`), `wechat` (HTTP JSON feed from we-mp-rss at `http://localhost:9001`), `arxiv` (Atom API XML).

### Data model

`app/models.py` — four tables:
- `Source` — config stored as a JSON string in a Text column.
- `Item` — articles. AI output split across `ai_summary` (text), `ai_keypoints` (JSON array string), `ai_tags` (JSON array of selected category names), `ai_score` (star rating).
- `Setting` — key/value. Currently holds `categories`: a JSON array of `{"name", "desc"}` the user manages in the UI. The LLM picks tags only from this list; `desc` is fed to the LLM to improve tagging. (Empty list = no tagging, summary/keypoints/stars still produced.)
- `Job` — one processing run: `status` (running/done/failed/cancelled), `trigger` (manual/auto), `total/processed/succeeded/failed`, timestamps, `error`.

All timestamps are **Unix epoch integers**, not datetimes. Dedup is enforced by the `(source_id, external_id)` unique constraint; `_upsert_item` in `fetcher.py` treats `IntegrityError` as "already exists, skip."

`ai_score` semantics: `None` = not yet processed (eligible for next job), `-1` = processing failed (excluded from score-sorted views; resettable via `/api/score/reset-failed`), `1-5` = star rating. Items with empty `content_text` are never picked up.

### AI processing

`app/ai/client.py` talks to a DeepSeek-compatible chat API (OpenAI-style `/chat/completions`, expects strict JSON back). `process_article()` does one call returning `{summary, keypoints[], categories[], stars}`. It defends against bad LLM output: strips markdown fences, drops categories the user didn't define, and fails the item if `stars` is missing or out of 1–5 range. `app/ai/scorer.py` provides the category get/save helpers and the `reset_failed_scores` / `rescore_all` maintenance ops. Use `rescore_all` to clear all AI output and let the scheduler reprocess from scratch after changing the category list.

### Web layer

`app/web/pages.py` — server-rendered Jinja2 (templates in `app/web/templates/`), separate from the JSON API in `main.py`. Form posts use POST-redirect-GET. Pages: items (`sort=time` default or `sort=score`, filterable by source and category), sources, settings (category management), jobs (progress bars, polls running jobs every ~2.5s).

### MCP server

`app/mcp_server.py` mounts a streamable-http MCP server at `/mcp` (started inside the FastAPI lifespan). It exposes tools to manage sources — `add_rss`, `search_wechat`, `subscribe_wechat` (chains we-mp-rss → feed-curator), `list_sources` — backed by `app/services/`. Like the JSON API, `/mcp` is unauthenticated and on the same port; fine for local use, add a token / bind to 127.0.0.1 if exposed.

## Deployment topology

This app is one of four components running on this Mac. **feed-curator and RSSHub
are Docker containers; we-mp-rss is a local process; the database is shared in
the `db-mp` container.**

| Component | Port | How it runs | Auto-restart? |
|-----------|------|-------------|---------------|
| feed-curator | 9003 | Docker `feed-curator` (`restart: unless-stopped`, on `feed-net`) | Yes |
| we-mp-rss | 9001 | local uv (Python 3.11) at `~/Projects/we-mp-rss`, `nohup` | No — manual on reboot |
| RSSHub | 9002 | Docker `rsshub` (`--restart always`) | Yes |
| MySQL (docker) | 3306 (\*, incl. IPv6) | Docker `db-mp` (`--restart always`) — backs **both** `feed_curator` and `we_mp_rss` dbs; also joined to `feed-net` | Yes |

Only db-mp listens on 3306 now (Homebrew MySQL stopped). The feed-curator
container reaches db-mp by service name over `feed-net`; it reaches we-mp-rss and
RSSHub (still on the host) via `host.docker.internal`.

`DEEPSEEK_API_KEY` enables AI processing; absent it, the app runs fully except processing is disabled. `DATABASE_URL` selects the database (see Database section). `.env` (via compose `env_file`) sets both up.

**Source wiring:** in-container, `wechat` sources use `wewe_base_url:
http://host.docker.internal:9001` and `rss` sources point at
`http://host.docker.internal:9002/<rsshub-route>` (the container can't reach the
host's `localhost`). Existing source rows were rewritten during migration.

**Gotchas across the stack (not specific to this repo, but you'll hit them):**
- ghcr.io / docker.io are unreachable here. Docker daemon `registry-mirrors` is
  configured (`~/.docker/daemon.json`: 1ms.run, daocloud, etc.) so plain `docker
  pull` / `compose build` route through domestic mirrors — large base layers are
  slow but complete. The Dockerfile also swaps apt to the Tsinghua Debian mirror
  and installs Python deps via Tsinghua PyPI. GitHub clone over SSH (HTTPS times
  out). playwright kernels via `npmmirror.com/mirrors/playwright`.
- we-mp-rss must connect to MySQL via `localhost`, NOT `127.0.0.1` (reverse-DNS makes MySQL match the wrong grant → `Access denied`). It also needs the playwright **webkit** kernel for WeChat QR login, and `env -u USERNAME` on launch (system `USERNAME` overrides the admin login name otherwise).

## Conventions specific to this codebase

- **Epoch-int timestamps everywhere.** When adding fields or queries, follow `int(time.time())`, not `datetime`.
- **`Source.config` and `Item.meta` (and `Setting` values like `categories`) are JSON serialized into Text columns** via `json.dumps(..., ensure_ascii=False)`. Preserve `ensure_ascii=False` (content is Chinese).
- **Long text columns must use the `LongText` variant**, not plain `Text`, or MySQL truncates at 64 KB. See the Database section.
- **Adapters must not touch the DB** — they return `FetchedItem`s; persistence is the fetcher's job.
- **Each processing worker uses its own session.** Don't share a `Session` across threads in `runner.py`.
- Heavy/optional imports (`LLMClient`, scorer/runner functions) are imported lazily inside functions so the app starts without an API key and without paying import cost on every request.
