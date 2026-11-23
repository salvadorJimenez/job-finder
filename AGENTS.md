# AGENTS.md

Guide for coding agents (and humans) working on **job-finder**. Read it before changing anything. For what the product does from a user's point of view, see [README.md](README.md) and [docs/](docs/); this file is about how the code is organized and how to change it safely. When something here disagrees with the code, the code wins: fix this file.

## Project overview

job-finder is a private, local job-search tool: a FastAPI web app plus a small CLI. It runs [JobSpy](https://github.com/speedyapply/JobSpy) against LinkedIn, Indeed and Glassdoor **only when the user asks**, stores offers in SQLite, groups possible duplicates, and tracks applications through user-defined phases. It targets Mexico / Latin America by default and is configurable (see [Market assumptions](#market-assumptions)).

- Python 3.11+ (see the Gotchas note on `requires-python`), dependencies in `pyproject.toml` (`python-jobspy`, `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `rapidfuzz`, `markdown-it-py`). Extras: `dev` (pytest, httpx, reportlab) and `resume` (reportlab, for `scripts/render_resume.py` only).
- No front-end build step: server-rendered Jinja2 templates, one CSS file and one JS file (`jobfinder/static/`). JavaScript only enhances; pages work without it.
- Single user, single machine. There is no authentication because the server only listens on `127.0.0.1` (see [Security rules](#security-rules)).

## Setting up the project (for AI agents)

Follow these steps in order when a user asks you to set job-finder up. Run each check and stop to tell the user if it fails; do not work around a failed check. Use the repository root as the working directory. On Windows replace `.venv/bin/` with `.venv\Scripts\`, `cp` with `copy`, `cp -R` with `xcopy /E /I`, and `mkdir -p data exports` with `mkdir data exports` (cmd.exe has no `-p`; skip folders that already exist).

**Never overwrite or delete the user's `config.toml`, `data/`, `profile/`, `resumes/` or `exports/`, and never commit them** (they are git-ignored and private; also keep `.codegraph/` out of commits). Do not run a live search just to test the setup: it sends real requests to LinkedIn, Indeed and Glassdoor and can trigger rate limiting. Starting the server and loading the page is enough.

Choose **local Python** (steps 1-7) or **Docker** (steps 8-10); do not run both at once, they share port 8000 and the database.

1. **Check Python.** `python3 --version` must report 3.11 or newer. If not, tell the user to install it (or use Docker) and stop.
2. **Create the virtual environment**, only if `.venv/` does not exist: `python3 -m venv .venv`. Check: `.venv/bin/python --version` works.
3. **Install.** `.venv/bin/pip install -e .` to use the tool, or `.venv/bin/pip install -e '.[dev]'` to develop and run the tests. Check: `.venv/bin/job-finder --help` lists `serve`, `search` and `export`.
4. **Create the config**, only if `config.toml` does not exist: `cp config.example.toml config.toml`. If it exists, leave it as it is. Check: `config.toml` is a file, not a directory.
5. **Ask the user** for the search terms, `location` and `country_indeed`, and their stack (`[profile] stack`), and edit `config.toml` with their answers. If they have no preference, leave the example values: they work for a first try. Keep `terms` and `location` lists short (requests multiply, see Gotchas).
6. **Create the folders**: `mkdir -p data exports`. Required before Docker on Linux, harmless otherwise.
7. **Start the server**: `.venv/bin/job-finder serve`. Check: it prints `job-finder running at http://127.0.0.1:8000 (press Ctrl+C to stop)`, keeps running, and `http://127.0.0.1:8000` answers. If it instead prints `Port 8000 on 127.0.0.1 is already in use …` and exits 1, something else (often job-finder in Docker) holds the port: tell the user, and use `--port 8001` or stop the other one. It runs until Ctrl+C, so start it in the background or a separate terminal, and tell the user how to stop it.
8. **Docker only, config and folders**: steps 4 to 6 first (`config.toml` as a file, then `mkdir -p data exports`). If they are missing at the first `up`, Docker creates a *directory* named `config.toml` and root-owned `data/` and `exports/`, and the app (an unprivileged user, uid 1000) cannot write the database.
9. **Docker only, start**: `docker compose up -d --build`, then `docker compose ps` until `STATUS` says `(healthy)`; if it is `unhealthy` or restarting, `docker compose logs --tail 50`. Stop any local `serve` first (same port).
10. **Open** http://127.0.0.1:8000 and tell the user to press **SEARCH** when they are ready (a search takes several minutes; see docs/configuration.md).

Optional, only if the user asks:

- **Run the tests** (needs `.[dev]`): `.venv/bin/python -m pytest -q`. They use no network.
- **Resume tools**: `cp -R profile.example profile` only if `profile/` does not exist (never replace an existing one), then `.venv/bin/pip install -e '.[resume]'`, and read `docs/ai-agents.md` for the agents. Without `profile/` the agents must stop, not invent facts.
- **CodeGraph**: see [CodeGraph (optional)](#codegraph-optional).

## Architecture

### Modules (`jobfinder/`)

| Module | Responsibility |
|---|---|
| `cli.py` | `job-finder` entry point: `serve` (`--reload`, `--host`, `--port`), `search`, `export --date [--stdout]`; global `--config`. Missing config → one-line hint on stderr, exit 1. |
| `config.py` | `load_config()` → frozen dataclasses (`Config`, `SearchConfig`). Paths are relative to the config file's folder. Validates `location` (string or non-empty list). |
| `db.py` | SQLite schema (`SCHEMA`, `CREATE ... IF NOT EXISTS`), `connect()` and the additive migrations in `_migrate`. Registers a `casefold` SQL function for accent-insensitive search. |
| `search.py` | `run_search()`: per site, per (place, term) calls JobSpy, upserts jobs, records `runs` / `run_sites`, computes per-site time windows (`hours_since_last_success`), plus the panel copy (`describe_when`, `describe_outcome`, `humanize_error`). |
| `enrich.py` | Text extraction from postings: `parse_salary` (MXN/USD), `detect_salary_basis` (gross/net), `extract_stack` and `stack_key` (technology list), `detect_work_mode` (remote/hybrid/onsite). Spanish and English patterns. |
| `dedup.py` | `is_possible_duplicate()`: normalized company + title similarity (rapidfuzz), level words must agree. |
| `describe.py` | `normalize_description()`: tidies scraped descriptions into Markdown at render/export time. Pure text → text; never changes stored data. |
| `format.py` | Human-readable salary and gross/net text shared by the web page and the export. |
| `export.py` | `export_day()`: Markdown of the offers first seen on a day. |
| `applications.py` | Phases (add, rename, delete, reorder), applications, outcomes, timeline entries, manual applications, and the board/list queries with filters and sorts. Functions take an open connection and **never commit**: the caller owns the transaction (`with conn:`). |
| `goal.py` | Daily goal: per-day counts, weekday streak, header counter, 30-day totals, activity calendar. Takes the app clock's `now`. |
| `cvs.py` | CV uploads: extension + magic-byte validation, 10 MB limit, stored by SHA-256 under `cvs/` next to the database. |
| `web.py` | `create_app()`: every route, middleware, template globals, the background search thread. Also `app_from_env()` (the `--reload` factory) and the list/filter SQL builders. |
| `templates/` | Jinja2 pages (`index.html` jobs list, `board.html`, `application.html`, `phases.html`, `manual.html`, `detail.html`, `offline.html`) and `_partials`. |
| `static/` | `style.css`, `app.js`, `sw.js` (service worker), `fonts/` (Geist, OFL), `icons/` (PWA icons). |

Other top-level pieces: `scripts/render_resume.py` (resume PDF renderer), `scripts/import_applications.py` (bulk import of past applications), `scripts/make_icons.sh` (regenerates PWA icons with headless Chrome/Chromium), `scripts/demo_data.py` + `scripts/screenshots.sh` (fictional demo database and the README screenshots), `agents/` (canonical resume agent prompts) and the thin wrappers `.claude/agents/`, `.codex/agents/`, `.opencode/agents/`, `.pi/agents/`, `profile.example/` (resume knowledge-base template), `config.example.toml`, `Dockerfile`, `compose.yaml`.

### Data flow

```
JobSpy scrape ──► enrich ──► dedup ──► SQLite ──► web (list, detail, board) ──► export
 (search.py)   (salary,     (group    (jobs,       (web.py + templates)      (Markdown)
               stack,       same      job_stack,
               work mode)   posting)  runs, …)
```

1. `run_search` computes `hours_old` per site (time since that site's last successful run + `overlap_hours`, or `first_search_days` the first time) and calls `scrape_jobs(site_name=[site], search_term=..., location=..., country_indeed=..., results_wanted=..., hours_old=..., fetch_description=True, description_format="markdown")` once per (place, term).
2. Each returned posting goes through `_upsert`: if `(site, external_id)` exists only `last_seen_at` and `search_terms` change; otherwise salary, gross/net, stack and work mode are extracted (`enrich.py`), the job is inserted, `job_stack` rows are written, and `_assign_duplicate_group` compares it with jobs seen in the last 90 days.
3. A failing (place, term) is recorded on its site and does not stop the others. Run status is `success`, `partial` (some sites failed) or `failed`; the next search retries a failed site's window.
4. `web.py` reads the database on each request (a new connection per call), renders offers (descriptions tidied by `describe.py`, rendered as untrusted text) and the application tracker.

### Database

- SQLite file at `[storage] database` (default `data/jobs.db`). Main tables: `jobs`, `job_stack`, `runs`, `run_sites`, `phases`, `applications`, `timeline_entries`, `cv_files`, `meta`.
- **Migrations are additive and idempotent**, in `db._migrate` (called by every `connect()`): add a column only if `PRAGMA table_info` lacks it, create tables/indexes with `IF NOT EXISTS`. **Never** drop, rename or rewrite user data in a migration.
- **One-time backfills** are guarded by a row in `meta` (`job_stack_built`, `phases_seeded`, `timeline_phase_id_linked`) and run in the same transaction that sets the flag. The `phases_seeded` flag is also why deleted default phases never come back.
- Applications are never deleted when a job's status changes; timeline entries keep a `phase_name` snapshot **and** a nullable `phase_id` link (used so a phase rename rewrites exactly its entries; deleting a phase clears the link and keeps the name).
- Manual jobs (`site = 'manual'`) need a `first_seen_run`, so the first one creates a `runs` row with status `manual`; searches ignore it.
- Dates are stored as ISO strings. "Local" means the process time zone (see [Gotchas](#gotchas)).

### Config reload

`[search]` is re-read from `config.toml` on **every search** (`read_search_cfg` in `web.py`), so edits apply without a restart; a broken file is reported (`422` on `POST /search`, notice on the page) and the startup values stay in use. The other sections are read at startup. `serve --reload` runs `app_from_env()` in the worker and survives a missing or invalid config: `_WaitForValidConfig` answers `503` with the error until the file reads again.

### Background search and progress

`POST /search` starts `run_search` in a daemon thread (`app.state.search_thread`), guarded by a lock; only one search runs at a time (`409 running`), and a second search the same day needs `confirm=1` (`409 already_searched_today`). Progress lives in `app.state` (`progress`, `step`, `total`, `last_result`, `last_error`) and is served by `GET /search/status`; the page polls it and reloads when the search ends. `create_app` accepts `runner=` and `clock=` so tests never hit the network or the real time.

### Security rules

These already exist in the code; keep them intact when changing related code, and add a test for any change that touches them.

- **Local only**: the default host is `127.0.0.1`, `compose.yaml` publishes `127.0.0.1:8000`. Do not add a way to bind publicly by default.
- **Cross-site form posts are refused**: the `reject_cross_site_posts` middleware returns `403` when a `POST` carries an `Origin`/`Referer` whose host differs from `Host`.
- **Untrusted descriptions are rendered as text**: `render_description` (markdown-it) disables raw HTML and images, and only allows safe link schemes; links open in a new tab with `noopener noreferrer`. Never mark scraped text as safe in a template. The copy-for-resume text is escaped by the server.
- **Redirect and back URLs are local only**: `safe_next` (and the `back` parameter) accept only paths starting with a single `/`, rejecting `//`, backslashes and control characters.
- **Links from data** (posting URL, manual link) are clickable only when `http`/`https`.
- **Uploads**: `cvs.py` checks extension and magic bytes, caps size at 10 MB, stores `<sha256>.<ext>` (the user's file name is never part of a path), and CV responses carry `Content-Security-Policy: frame-ancestors 'self'`.
- **SQL**: values are always bound parameters; dynamic SQL only assembles fixed fragments (see the `_filter_clause` / `_list_sql` builders).

## Setup and commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'              # add '.[resume]' only for the resume renderer (dev already includes reportlab)
cp config.example.toml config.toml             # private, git-ignored; edit it
.venv/bin/job-finder serve --reload            # http://127.0.0.1:8000, restarts on code changes
.venv/bin/job-finder search                    # same search from the terminal
.venv/bin/job-finder export --date YYYY-MM-DD [--stdout]
.venv/bin/python -m pytest -q                  # whole suite
.venv/bin/python -m pytest tests/test_goal.py -q   # one file
docker compose up -d --build                   # rebuild and restart the container after code changes
```

On Windows replace `.venv/bin/` with `.venv\Scripts\`. The Docker image copies `pyproject.toml`, `README.md` and `jobfinder/` (so `README.md` must stay at the repository root: `pyproject.toml` uses it as the package readme) and runs `job-finder serve --host 0.0.0.0` inside the container.

`CHROME=/path/to/chrome scripts/make_icons.sh` regenerates `jobfinder/static/icons/` (needs Chrome or Chromium). `scripts/screenshots.sh` regenerates the README images in `docs/screenshots/` (needs Chrome or Chromium; `PORT` defaults to 8790, never 8000): it seeds a throwaway database with `scripts/demo_data.py --config <config> [--today YYYY-MM-DD]` (fictional data; the script refuses to write into an existing database) inside a `mktemp -d` folder, serves it, captures the pages and deletes everything. It never reads your `config.toml` or `data/`. Rerun it after a visible UI change and look at the PNGs before committing. Poppler's `pdfinfo` / `pdftotext` are optional: a few resume tests use them and are skipped without them.

## CodeGraph (optional)

[CodeGraph](https://github.com/colbymchenry/codegraph) is a local code index that answers structural questions (call paths, symbol references, what a change affects) in one call. Nothing in job-finder needs it.

- **If it is available** (the `codegraph` CLI is on `PATH`, or the `codegraph_explore` MCP tool is offered), use it **before broad file searches** for structural questions: how a feature works, who calls a function, what a change would affect. The read-only CLI commands are `codegraph explore "<question>"`, `codegraph callers <symbol>`, `codegraph callees <symbol>`, `codegraph impact <symbol>` and `codegraph status`. With the MCP server running, the index follows your edits by itself; when you only use the CLI after editing files, run `codegraph sync` first so the answers are not stale. Answer from what it returns, then read the files you will change.
- **If it is not installed**, fall back to normal search (`grep`, `find`, reading files) and, once, suggest it to the user. Do not install it without their consent.
- **How the user installs it** (see the [official README](https://github.com/colbymchenry/codegraph) and [docs](https://colbymchenry.github.io/codegraph/)): run `npx @colbymchenry/codegraph` (interactive installer that connects it to the agents they use), or install the CLI with the install script from the official README (or `npm i -g @colbymchenry/codegraph`) and then run `codegraph install`. Then, once per project, run `codegraph init` in the repository root to build the index. `codegraph init --local-only` keeps `.codegraph/` out of git.
- **Do not commit `.codegraph/`** (the index database). Check `git status` before committing. The installer may also add a marker-fenced CodeGraph block to agent instruction files such as this one: review that diff and leave it out of unrelated commits.

## Conventions

- **Test first for behavior changes.** Write the failing test, watch it fail for the right reason, then make it pass. Bug fixes start with a test that reproduces the bug.
- **Test through public interfaces** (the FastAPI app with `TestClient`, the `cli.main()` function or the console script) and **assert every observable effect**: response status and body, redirects, database rows, headers, files. Avoid testing private helpers directly when an interface exercises them.
- **No network and no real clock in tests.** Inject `create_app(cfg, runner=..., clock=...)`, `run_search(conn, cfg, scrape=..., now=..., sleep=...)`. Tests build temporary databases under `tmp_path`; shared helpers live in sibling test modules (`tests/test_web.py` has `make_cfg`, `NOW`; `tests/test_board.py` has `make_app`).
- **User-visible change ⇒ update the docs in the same change**: `README.md`, the relevant file in `docs/`, `config.example.toml` and the CLI `--help` text when they are affected.
- **English only** for code, comments, UI text, docs and commit messages.
- **Conventional Commits** (`feat(scope): ...`, `fix: ...`, `docs: ...`, `chore: ...`, `test: ...`, `refactor: ...`). Keep diffs small and reviewable: one concern per commit.
- **No new dependencies without need.** Prefer the standard library; justify any addition in the commit message and add it to `pyproject.toml`.
- **Follow the existing style**: type hints, `from __future__ import annotations`, short docstrings that say *why*, plain SQL with bound parameters, no ORM.
- **Never commit personal data.** `config.toml`, `profile/`, `resumes/`, `data/` and `exports/` are git-ignored and private (`.codegraph/` is a local index: do not commit it either): never `git add -f` them, never print their contents into commits, issues or fixtures. Use `config.example.toml` and `profile.example/` for examples, and **fictional people and companies** (Acme, Globex, "Jane Doe") in fixtures, docs and screenshots.
- **Never break the user's data.** No destructive migrations, no resetting a user's `config.toml`, database, `profile/` or `resumes/`.
- Static assets: edit `style.css` / `app.js` freely; they are versioned by content hash (see Gotchas). If you change the files precached in `sw.js`, bump its `CACHE` name.

## Gotchas

- **Request multiplication and rate limits.** One search makes `terms × places × sites` JobSpy calls, with a `pause_seconds` sleep between consecutive calls to the same site (default `3` in `config.py`, `30` in `config.example.toml`). LinkedIn answers HTTP 429 after a few pages; `humanize_error` shows 429/"blocked" as *rate-limited*. The fix is more pause, fewer `results_wanted`, fewer terms/places. Adding a term or a place multiplies requests, so keep example configs small.
- **JobSpy quirks.** Glassdoor needs a location it can resolve, and Indeed/Glassdoor take their country from `country_indeed`, not from `location`. Use the full `City, State, Country` form so ambiguous names (Córdoba exists in Mexico, Argentina and Spain) do not match. Glassdoor matches terms loosely and sometimes returns placeholder salaries (`MXN 1 / month`). JobSpy reports per-site problems through `df.attrs["errors"]`; `run_search` turns them into a site failure.
- **Docker single-file bind mount.** `compose.yaml` mounts `./config.toml` as a single file. If it does not exist at the first `up`, Docker creates a *directory* named `config.toml`. And editors that replace the file (atomic save, `sed -i`) leave the container looking at the old inode: `docker compose restart` fixes it. Keep the `cp config.example.toml config.toml` step at the top of the Docker instructions.
- **Static asset cache-busting.** `web.py` hashes `style.css` and `app.js` at startup and templates reference them through the `asset()` global (`/static/style.css?v=<hash>`). Versioned URLs are served with `immutable` long cache, unversioned `/static/` URLs with `no-cache`, and `/sw.js` is served from the root with `no-cache`. Always reference those two files with `asset(...)` in templates, never with a bare path.
- **Time zones.** "Today", the daily goal, the streak and the activity calendar use the **local calendar date** of the process (`search.local_now()`; `goal.py` and `applications.py` take `now`). The Docker image has no zone by default, so `compose.yaml` sets `TZ` (default `America/Mexico_City`, override with `TZ=... docker compose up -d --build`). Timeline times with an offset are converted with the current UTC offset; plain ones typed by the user are taken as local.
- **Timeline phase data.** `timeline_entries.phase_name` is a snapshot; `phase_id` is the link. Renaming a phase rewrites only entries with that `phase_id`. Do not "simplify" this by rewriting by name.
- **Stack detection** is a fixed list in `enrich._TECH`; case-sensitive entries exist to avoid common words (`Express`, `Node`, `React`, `Spring`). Changing the list changes derived data: the `job_stack_built` backfill runs only once per database.
- **Pagination SQL.** The jobs list picks groups of duplicates with a window function and fetches nested postings in a second query; keep list queries constant in number as data grows.
- **Minimum Python version.** Python 3.11+ (`config.py` uses the standard-library `tomllib`); `pyproject.toml` declares `requires-python = ">=3.11"` and the Docker image uses 3.13. Do not lower it without adding a `tomli` fallback.

### Market assumptions

The tool is Mexico/LatAm-first but configurable. `config.example.toml` uses `location = "Mexico"` and `country_indeed = "Mexico"`. What is tuned for that market: salary parsing in text (`enrich.py`: MXN and USD only; the currency filter offers those two), Spanish and English keywords for work mode and gross/net, Mexican legal suffixes (`S.A. de C.V.`, `S. de R.L.`) and noise words (`mexico`, `cdmx`) in `dedup.py`, and Spanish rejection phrases in `goal.REJECTION_PHRASES`. Keep the docs honest about this; if you generalize a heuristic, add tests for both the old and new behavior.

## Resume tooling (for agents)

Optional feature that turns the user's own career notes into a tailored one-page resume. Nothing here is needed to run the app.

- **`profile/`** (private, git-ignored) is the user's knowledge base, copied from `profile.example/` (`cp -R profile.example profile`). It is the **only source of facts**: `01-identity` (name, contact, PDF name pattern `Resume_<First>_<Last>_<Company>.pdf`), `02-experience`, `02a-flagship-project` (optional deep dive), `03-skills`, `04-projects`, `05-education`, `06-cv-rules`, `07-stories-and-answers`, `08-gaps`, `09-job-search`, `10-cv-structure` (layout template), `11-application-answers`, `open-questions`, and a `README.md` that lists them. If `profile/` does not exist, stop and tell the user to copy `profile.example/`; never write a resume from the templates.
- **Rule precedence**: `profile/06-cv-rules.md` holds the user's hard rules and **overrides** the agent prompts in `agents/`. When the profile and a prompt disagree, the profile wins. If memory tools exist (for example Engram, topic keys `profile/*`) and disagree with the files, the files win.
- **Never invent facts**: no numbers, titles, years, employers or technologies the profile does not state. A requirement the profile cannot support is a **gap** to report, not to claim. Do not claim management, mentoring or ownership the profile does not state.
- **Agents** (usage per tool in [docs/ai-agents.md](docs/ai-agents.md); run from the repository root with relative paths): the canonical prompts live in `agents/` and the files in `.claude/agents/`, `.codex/agents/`, `.opencode/agents/` and `.pi/agents/` are thin wrappers that point to them, so **edit the prompt in `agents/` only**. `resume-writer` writes `resumes/<company>-<role>/resume.md` and `match-report.md` (never the PDF); `resume-pdf` renders an **approved** resume and checks it. Keep these prompts free of personal data and absolute paths.
- **Renderer contract** (`scripts/render_resume.py`, needs ReportLab): `render_resume.py <resume.md> --out <file.pdf>`. Exit `0` ok; `2` content does not fit one page (nothing is written; message gives the overflow and longest block); `1` any other error (unreadable input, unsupported Markdown, missing ReportLab). The PDF title and author come from the `# Name` heading; nothing personal lives in the script.
- **Markdown shape the renderer accepts**: one `# Name`; headline and contact lines; `## Section`s with paragraphs and `- ` bullets; `### Company — Title` roles each followed by a `*dates*` line; inline `**bold**` and `*italic*` only. No tables, links, code, HTML, numbered lists or emojis; characters must exist in Windows-1252. Anything else raises an error naming the line.
- Layout numbers (US Letter, Helvetica, teal headings, margins, length budget) are specified in `profile.example/10-cv-structure.md`; the script's constants (top of the file) are the source of truth and the place to change colors, fonts and margins. Every personal value (name, contact, headline, PDF file name pattern) comes from the resume or `profile/`, never from the script or the agent prompts.
- Tests for the renderer use fictional candidates and companies (Acme, "Jane Doe"). Keep it that way.

## Where to look for what

| If you need to… | Look at |
|---|---|
| Add or change a CLI command or flag | `jobfinder/cli.py`, `tests/test_cli.py`, README *Usage* |
| Add a config key | `jobfinder/config.py`, `config.example.toml`, `docs/configuration.md`, `tests/test_config.py` |
| Change how a search runs, its time windows, or error wording | `jobfinder/search.py`, `tests/test_search.py`, `tests/test_search_panel.py` |
| Improve salary, gross/net, work mode or stack detection | `jobfinder/enrich.py`, `tests/test_enrich.py`, `tests/test_stack.py` |
| Change duplicate detection | `jobfinder/dedup.py`, `tests/test_dedup.py`, `tests/test_groups.py` |
| Change description tidying | `jobfinder/describe.py`, `tests/test_describe.py` |
| Change the schema or add a migration | `jobfinder/db.py` (`SCHEMA`, `_migrate`), `tests/test_db.py` |
| Change the jobs list, filters, pagination, pinning | `jobfinder/web.py` (`_filter_clause`, `_list_sql`, `index`), `templates/index.html`, `tests/test_web.py`, `tests/test_filter_bar.py`, `tests/test_pane.py` |
| Change the job detail page / pane | `web.py` (`job_detail`), `templates/detail.html`, `_job_pane.html`, `tests/test_detail.py`, `tests/test_copy_job.py` |
| Change phases, applications, board/list, timeline | `jobfinder/applications.py`, `web.py` (`/applications*`), `templates/board.html`, `application.html`, `phases.html`, `tests/test_applications.py`, `test_phases.py`, `test_board.py`, `test_applications_view.py`, `test_application_page.py` |
| Add an application by hand / bulk import | `applications.create_manual_application`, `templates/manual.html`, `scripts/import_applications.py`, `tests/test_manual.py`, `tests/test_import_applications.py` |
| Change the daily goal or activity calendar | `jobfinder/goal.py`, `templates/_app_activity.html`, `tests/test_goal.py`, `tests/test_activity_calendar.py` |
| Change CV uploads | `jobfinder/cvs.py`, `web.py` (`/applications/{id}/cv`, `/cvs/*`), `tests/test_cvs.py` |
| Change the Markdown export | `jobfinder/export.py`, `tests/test_export.py` |
| Change security behavior | `web.py` middleware and `safe_next`, `tests/test_hardening.py` |
| Change styles, scripts, PWA, icons | `jobfinder/static/`, `scripts/make_icons.sh`, `tests/test_assets.py`, `tests/test_pwa.py`, `tests/test_ui.py` |
| Regenerate the README screenshots or change the demo data | `scripts/screenshots.sh`, `scripts/demo_data.py`, `docs/screenshots/`, `tests/test_demo_data.py` |
| Change Docker | `Dockerfile`, `compose.yaml`, README Docker sections, `tests/test_cli.py` (checks compose assumptions) |
| Change resume tooling | `scripts/render_resume.py`, `agents/*.md` (and the wrappers in `.claude/`, `.codex/`, `.opencode/`, `.pi/agents/`), `docs/ai-agents.md`, `profile.example/`, `tests/test_render_resume.py` |
| Understand a feature as a user sees it | `docs/features.md`, `docs/applications.md` |
