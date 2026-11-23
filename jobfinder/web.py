"""Local-only web page: list, filter, mark status, search and export."""
from __future__ import annotations

import hashlib
import os
import threading
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from . import applications, cvs, goal
from .config import Config, load_config
from .db import connect
from .describe import normalize_description, parse_description
from .enrich import _CANONICAL, stack_key
from .export import export_day
from .format import format_basis, format_salary
from .search import (
    SearchAlreadyRunning,
    SearchResult,
    describe_outcome,
    describe_when,
    last_search,
    latest_run,
    local_now,
    run_search,
)

HERE = Path(__file__).parent
MANIFEST = {
    "id": "/",
    "name": "job_finder",
    "short_name": "job_finder",
    "description": "Private local job search: listings, applications and daily goal.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#000000",
    "theme_color": "#000000",
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}
STATUSES = ("new", "seen", "applied", "discarded")
WORK_MODES = ("remote", "hybrid", "onsite")
POSTED_WEEKS = ("2", "3", "4")   # "posted in the last N weeks" filter choices
PAGE_SIZE = 20
NEWEST_FIRST = "first_seen_at DESC, date_posted DESC, id DESC"
VISIBLE_CHIPS = 20          # besides my stack, the most common techs shown before "more"

# Descriptions are untrusted text: raw HTML is escaped, images are never fetched
# and links with unsafe schemes (javascript:, data:, ...) are dropped by
# markdown-it's validateLink.
_markdown = MarkdownIt("commonmark", {"html": False, "breaks": True}).disable("image")


def _open_in_new_tab(self, tokens, idx, options, env):
    tokens[idx].attrSet("target", "_blank")
    tokens[idx].attrSet("rel", "noopener noreferrer")
    return self.renderToken(tokens, idx, options, env)


_markdown.add_render_rule("link_open", _open_in_new_tab)


def render_description(text: str | None) -> str:
    """Safe HTML for a scraped description: tidied into Markdown first (describe.py), the meta
    lines found at its top shown as a compact list, then rendered with raw HTML escaped."""
    parsed = parse_description(text)
    meta = ""
    if parsed.meta:
        items = "".join(f"<div><dt>{escape(key)}</dt><dd>{escape(value)}</dd></div>" for key, value in parsed.meta)
        meta = f'<dl class="desc-meta">{items}</dl>\n'
    return meta + _markdown.render(parsed.body)


def copy_text(job: dict[str, Any]) -> str:
    """The plain-text block COPY FOR RESUME puts on the clipboard: the facts the resume writer needs,
    then the tidied (Markdown) description. Missing facts read "—" / "not published"."""
    location = " · ".join(p for p in (job["location"], job["work_mode"]) if p) or "—"
    source = " · ".join(p for p in (job["site"], job["posting_url"]) if p)
    salary = f"{job['salary_text']} · {job['basis_text']}" if job["has_salary"] else "not published"
    return (
        f"Company: {job['company'] or '—'}\n"
        f"Role: {job['title'] or '—'}\n"
        f"Location: {location}\n"
        f"Source: {source or '—'}\n"
        f"Posted: {job['date_posted'] or '—'}\n"
        f"Salary: {salary}\n"
        "\n"
        "Job description:\n"
        f"{normalize_description(job['description']) or '—'}"
    )


def _with_job(url: str, job_id: int | None) -> str:
    """The same list URL with `job` set to `job_id` (or removed when None); every other parameter stays."""
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "job"]
    if job_id is not None:
        pairs.append(("job", str(job_id)))
    return parts.path + (f"?{urlencode(pairs)}" if pairs else "")


def _attachment(name: str, disposition: str = "attachment") -> str:
    """Content-Disposition with a quoted ASCII fallback, plus the UTF-8 name when it differs."""
    fallback = "".join(c if c.isascii() and c.isprintable() and c not in '"\\;' else "_" for c in name)
    header = f'{disposition}; filename="{fallback}"'
    return header if fallback == name else f"{header}; filename*=utf-8''{quote(name, safe='')}"


def _default_runner(conn, search_cfg, on_progress):
    return run_search(conn, search_cfg, on_progress=on_progress)


def _filter_clause(f: dict[str, str]) -> tuple[list[str], list[Any]]:
    where, params = [], []
    status = f.get("status") or "open"
    if status == "open":
        where.append("status != 'discarded'")
    elif status in STATUSES:
        where.append("status = ?")
        params.append(status)
    if f.get("site"):
        where.append("site = ?")
        params.append(f["site"])
    if f.get("q"):
        where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    keys = _stack_keys(f.get("stack"))
    if keys:
        # Any of the selected techs; served by the (tech, job_id) index of job_stack.
        where.append(f"id IN (SELECT job_id FROM job_stack WHERE tech IN ({_marks(keys)}))")
        params += keys
    if f.get("currency"):
        where.append("salary_currency = ?")
        params.append(f["currency"])
    # Several modes combine with OR; "unknown" means not specified. Unknown values are ignored.
    raw_modes = f.get("mode") or []
    modes = [m for m in ([raw_modes] if isinstance(raw_modes, str) else raw_modes) if m in (*WORK_MODES, "unknown")]
    if modes:
        known = [m for m in modes if m != "unknown"]
        parts = [f"work_mode IN ({_marks(known)})"] if known else []
        if "unknown" in modes:
            parts.append("work_mode IS NULL")
        where.append(f"({' OR '.join(parts)})")
        params.extend(known)
    if f.get("with_salary"):
        where.append("(salary_min IS NOT NULL OR salary_max IS NOT NULL)")
    if f.get("only_new"):
        where.append(
            "first_seen_run = (SELECT max(id) FROM runs WHERE status IN ('success', 'partial'))"
        )
    if f.get("day"):
        where.append("substr(first_seen_at, 1, 10) = ?")
        params.append(f["day"])
    if f.get("_posted_since"):
        # Offers without a posting date count from the day they were first seen.
        where.append("coalesce(date_posted, substr(first_seen_at, 1, 10)) >= ?")
        params.append(f["_posted_since"])
    return where, params


def _marks(values: list[Any]) -> str:
    return ", ".join("?" * len(values))


def _stack_keys(raw: Any) -> list[str]:
    """Normalized, de-duplicated stack keys from a repeated `stack` param (or a legacy single value)."""
    values = [raw] if isinstance(raw, str) else (raw or [])
    return list(dict.fromkeys(k for v in values if (k := stack_key(v))))


def _parse_page(raw: str | None) -> int:
    try:
        return max(int(raw or 1), 1)
    except ValueError:
        return 1


def _jobs_from_rows(conn, rows, my_keys: list[str]) -> list[dict[str, Any]]:
    latest = conn.execute("SELECT max(id) FROM runs WHERE status IN ('success', 'partial')").fetchone()[0]
    mine = set(my_keys)
    jobs = []
    for row in rows:
        job = dict(row)
        job["salary_text"] = format_salary(row)
        job["has_salary"] = row["salary_min"] is not None or row["salary_max"] is not None
        job["basis_text"] = format_basis(row)
        job["stack_list"] = [s for s in (row["stack"] or "").split(", ") if s]
        job["stack_tags"] = [(s, stack_key(s) in mine) for s in job["stack_list"]]
        job["match_count"] = len({stack_key(s) for s in job["stack_list"]} & mine)
        job["is_new"] = row["first_seen_run"] == latest
        jobs.append(job)
    return jobs


_SELECT_JOBS = "SELECT jobs.*, (SELECT count(*) FROM jobs j3 WHERE j3.dup_group = jobs.dup_group) AS dup_count FROM jobs"


_GROUP_KEY = "coalesce(dup_group, -id)"   # a job with no dup_group is a group of one (ids are positive)


def _order_sql(f: dict[str, Any], my_keys: list[str]) -> tuple[str, str, str, list[Any]]:
    """(extra select column, JOIN, ORDER BY, params): best match (my stack) first unless
    `sort=newest`. The match count comes from one grouped pass over the job_stack index; ties
    fall back to the newest-first order, which ends in the unique id, so pages never overlap."""
    if my_keys and f.get("sort") != "newest":
        join = (f" LEFT JOIN (SELECT job_id, count(*) AS n FROM job_stack WHERE tech IN ({_marks(my_keys)})"
                " GROUP BY job_id) AS m ON m.job_id = jobs.id")
        return ", coalesce(m.n, 0) AS match_n", join, f"coalesce(m.n, 0) DESC, {NEWEST_FIRST}", list(my_keys)
    return "", "", NEWEST_FIRST, []


def _list_sql(f: dict[str, Any], my_keys: list[str], limit_offset: str = "") -> tuple[str, list[Any]]:
    """The page query: one representative per group of possible duplicates, in list order.
    Unpinned jobs matching the filters are numbered inside their group by the list order
    (ROW_NUMBER) and the first of each group is its representative, so when sorting by match
    the best match represents its group."""
    where, params = _filter_clause(f)
    where.append("pinned_at IS NULL")
    column, join, order, order_params = _order_sql(f, my_keys)
    outer_order = order.replace("coalesce(m.n, 0)", "match_n")
    sql = (
        f"WITH ranked AS (SELECT jobs.*{column}, ROW_NUMBER() OVER (PARTITION BY {_GROUP_KEY} ORDER BY {order}) AS rn"
        f" FROM jobs{join} WHERE {' AND '.join(where)})"
        " SELECT ranked.*, (SELECT count(*) FROM jobs j3 WHERE j3.dup_group = ranked.dup_group) AS dup_count"
        f" FROM ranked WHERE rn = 1 ORDER BY {outer_order} {limit_offset}"
    )
    return sql, [*order_params, *params]            # the JOIN's marks come before the WHERE's


def _count_sql(f: dict[str, Any]) -> tuple[str, list[Any]]:
    """(groups, postings) matching the filters, pinned jobs excluded."""
    where, params = _filter_clause(f)
    where.append("pinned_at IS NULL")
    return f"SELECT count(DISTINCT {_GROUP_KEY}), count(*) FROM jobs WHERE {' AND '.join(where)}", params


def _members_sql(f: dict[str, Any], my_keys: list[str], groups: list[int], reps: list[int]) -> tuple[str, list[Any]]:
    """The other postings of the page's groups that match the filters, in list order. One query."""
    where, params = _filter_clause(f)
    where += ["pinned_at IS NULL", f"dup_group IN ({_marks(groups)})", f"id NOT IN ({_marks(reps)})"]
    _, join, order, order_params = _order_sql(f, my_keys)
    return (f"SELECT jobs.* FROM jobs{join} WHERE {' AND '.join(where)} ORDER BY {order}",
            [*order_params, *params, *groups, *reps])


def _query_jobs(conn, f: dict[str, Any], my_keys: list[str] = ()) -> tuple[list[dict[str, Any]], int, int, int, int]:
    """One page of groups of unpinned jobs matching the filters: (jobs, groups, page, pages, postings).
    Each job is a group's representative; its other postings are in job["members"]."""
    my_keys = list(my_keys)
    total, postings = conn.execute(*_count_sql(f)).fetchone()
    pages = max(-(-total // PAGE_SIZE), 1)
    page = min(_parse_page(f.get("page")), pages)
    sql, params = _list_sql(f, my_keys, f"LIMIT {PAGE_SIZE} OFFSET {(page - 1) * PAGE_SIZE}")
    jobs = _jobs_from_rows(conn, conn.execute(sql, params), my_keys)
    groups = sorted({j["dup_group"] for j in jobs if j["dup_group"]})
    members: dict[int, list[dict[str, Any]]] = {}
    if groups:
        rows = conn.execute(*_members_sql(f, my_keys, groups, [j["id"] for j in jobs])).fetchall()
        for member in _jobs_from_rows(conn, rows, my_keys):
            members.setdefault(member["dup_group"], []).append(member)
    for job in jobs:
        job["members"] = members.get(job["dup_group"], []) if job["dup_group"] else []
        job["member_sites"] = list(dict.fromkeys(m["site"] for m in job["members"]))
        job["unread_members"] = sum(m["status"] == "new" for m in job["members"])
    return jobs, total, page, pages, postings


def _stack_chips(conn, my_stack: list[str], my_keys: list[str], selected: list[str],
                 f: dict[str, Any] | None = None) -> dict[str, Any]:
    """Chips for the stack filter: my stack first (config order), then the most common techs,
    the rest under "more". Within each group the order is alphabetical.

    Which chips exist and their order come from every job, so chips don't jump around;
    each count is the number of offers the list would show for that chip alone under the
    other active filters (the stack selection itself is ignored, as the chips combine with OR)."""
    counts = dict(conn.execute("SELECT tech, count(*) FROM job_stack GROUP BY tech").fetchall())
    where, params = _filter_clause({k: v for k, v in (f or {}).items() if k not in ("stack", "page")})
    where.append("pinned_at IS NULL")
    shown_counts = dict(conn.execute(
        f"SELECT job_stack.tech, count(DISTINCT coalesce(jobs.dup_group, -jobs.id)) FROM job_stack JOIN jobs ON jobs.id = job_stack.job_id"
        f" WHERE {' AND '.join(where)} GROUP BY job_stack.tech", params,
    ).fetchall())
    names = {**_CANONICAL, **{stack_key(n): n for n in my_stack}}
    chip = lambda key: {"key": key, "label": names.get(key, key), "count": shown_counts.get(key, 0), "on": key in selected,
                        "mine": key in my_keys}
    mine = [k for k in my_keys if k in counts]
    others = [k for k in {*counts, *selected} if k not in my_keys]
    common = sorted(others, key=lambda k: (-counts.get(k, 0), k))
    shown = {*common[:VISIBLE_CHIPS], *(k for k in selected if k not in counts)}
    main = mine + sorted(k for k in others if k in shown)
    more = sorted(k for k in others if k not in shown)
    return {
        "main": [chip(k) for k in main],
        "more": [chip(k) for k in more],
        "more_open": any(k in selected for k in more),
    }


def _pinned_jobs(conn, my_keys: list[str] = ()) -> list[dict[str, Any]]:
    """Every pinned job, newest pin first, whatever the filters say."""
    rows = conn.execute(f"{_SELECT_JOBS} WHERE pinned_at IS NOT NULL ORDER BY pinned_at DESC, id DESC")
    return _jobs_from_rows(conn, rows, list(my_keys))


def _job_detail(conn, job_id: int, my_keys: list[str] = ()) -> dict[str, Any] | None:
    """One job for the detail page and the split-view pane: the row, its tidied description and the
    other postings of its duplicate group (one extra query, only when it has a group)."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    mine = set(my_keys)
    job["salary_text"] = format_salary(row)
    job["has_salary"] = row["salary_min"] is not None or row["salary_max"] is not None
    job["basis_text"] = format_basis(row)
    job["stack_list"] = [s for s in (row["stack"] or "").split(", ") if s]
    job["stack_tags"] = [(s, stack_key(s) in mine) for s in job["stack_list"]]
    job["description_html"] = render_description(row["description"])
    job["posting_url"] = row["url"] if (row["url"] or "").lower().startswith(("http://", "https://")) else None
    job["copy_text"] = copy_text(job)
    job["group_members"] = [dict(m) for m in conn.execute(
        "SELECT id, site, title, company, location, status FROM jobs WHERE dup_group = ? AND id != ? ORDER BY id",
        (row["dup_group"], job_id))] if row["dup_group"] else []
    return job


_CONFIG_ERRORS = (OSError, ValueError, TypeError, KeyError)  # TOMLDecodeError is a ValueError


class _WaitForValidConfig:
    """Stands in for the app while config.toml cannot be read, so a typo does not kill the
    --reload worker (the reloader only restarts on code changes, not on config edits).
    Each request retries the config; once it reads, the real app is built and takes over."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.app: FastAPI | None = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            try:
                self.app = create_app(load_config(self.config_path), config_path=self.config_path)
            except _CONFIG_ERRORS as exc:
                if scope["type"] == "lifespan":
                    while (message := await receive())["type"] != "lifespan.shutdown":
                        await send({"type": "lifespan.startup.complete"})
                    await send({"type": "lifespan.shutdown.complete"})
                    return
                response = PlainTextResponse(
                    f"config.toml has an error: {exc}\nFix it and reload this page.", status_code=503
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def app_from_env():
    """App factory for `job-finder serve --reload`; reads JOB_FINDER_CONFIG."""
    config_path = Path(os.environ.get("JOB_FINDER_CONFIG", "config.toml"))
    try:
        return create_app(load_config(config_path), config_path=config_path)
    except _CONFIG_ERRORS:
        return _WaitForValidConfig(config_path)


def create_app(
    cfg: Config,
    runner: Callable[..., Any] = _default_runner,
    clock: Callable[[], datetime] = local_now,
    config_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="job-finder", docs_url=None, redoc_url=None)
    my_keys = list(dict.fromkeys(stack_key(n) for n in cfg.stack if n.strip()))

    @app.middleware("http")
    async def reject_cross_site_posts(request: Request, call_next):
        """Any website open in the browser could submit a form to this local app.
        Browsers send Origin (or Referer) with form posts, so refuse foreign ones."""
        if request.method == "POST":
            source = request.headers.get("origin") or request.headers.get("referer")
            if source and urlsplit(source).netloc != request.headers.get("host", ""):
                return PlainTextResponse("Cross-site request refused", status_code=403)
        return await call_next(request)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    # Pages link style.css and app.js with a content hash (?v=…), so a rebuild reaches the
    # browser and the installed app at once; those URLs can be cached for good. Plain static
    # URLs (fonts, icons, the offline page's CSS) are revalidated on every use.
    asset_versions = {
        name: hashlib.sha256((HERE / "static" / name).read_bytes()).hexdigest()[:12]
        for name in ("style.css", "app.js")
    }

    @app.middleware("http")
    async def static_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") and response.status_code == 200:
            versioned = "v" in request.query_params
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return response

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse(MANIFEST, media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        """Served from the root so its scope is the whole app; never cached so updates apply."""
        return FileResponse(HERE / "static" / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})

    def goal_context(request: Request) -> dict[str, Any]:
        """Daily goal counter for the header, which every page renders."""
        if cfg.daily_goal <= 0:
            return {"goal": None}
        conn = db()
        try:
            return {"goal": goal.header(conn, clock(), cfg.daily_goal)}
        finally:
            conn.close()

    templates = Jinja2Templates(directory=HERE / "templates", context_processors=[goal_context])
    templates.env.globals["asset"] = lambda name: f"/static/{name}?v={asset_versions[name]}"
    app.state.search_thread = None
    app.state.progress = ""
    app.state.step = 0
    app.state.total = 0
    app.state.last_result = None
    app.state.last_error = None
    lock = threading.Lock()

    def db():
        return connect(cfg.database)

    def read_search_cfg():
        """(search config, problem). config.toml is re-read so edits apply without a restart;
        a typo in it falls back to the config loaded at startup and is reported, never raised."""
        if not config_path:
            return cfg.search, None
        try:
            return load_config(config_path).search, None
        except _CONFIG_ERRORS as exc:
            return cfg.search, f"config.toml has an error: {exc}. Fix it and reload the page."

    def search_cfg():
        return read_search_cfg()[0]

    def searched_today(conn) -> tuple[bool, str | None]:
        last = last_search(conn)
        if last is None:
            return False, None
        local = last.started_at.astimezone(clock().tzinfo)
        return local.date() == clock().date(), local.strftime("%Y-%m-%d %H:%M")

    def search_panel(conn) -> dict[str, Any]:
        """What the search panel says: when we last searched and how that run ended."""
        now = clock()
        last = last_search(conn)
        when, today = describe_when(last.started_at, now) if last else ("Never searched", False)
        panel: dict[str, Any] = {"when": when, "today": today, "outcome": None}
        if app.state.last_error:                      # the search itself crashed (not a site failure)
            panel["outcome"] = {"kind": "failed", "headline": "× Search failed.", "hint": "Try again in a moment.",
                                "details": app.state.last_error}
        elif (run := latest_run(conn)) is not None:
            panel["outcome"] = describe_outcome(run)
        panel["last_time"] = last.started_at.astimezone(now.tzinfo).strftime("%H:%M") if last else None
        return panel

    def safe_next(target: str) -> str:
        """Local paths only. Browsers read "\\" as "/" and drop tabs and newlines,
        so those could turn "/\\evil.com" into a link to another site."""
        if not target.startswith("/") or target.startswith("//"):
            return "/"
        if "\\" in target or any(ord(c) < 32 or ord(c) == 127 for c in target):
            return "/"
        return target

    def is_running() -> bool:
        t = app.state.search_thread
        return t is not None and t.is_alive()

    @app.get("/")
    def index(request: Request):
        # hide_dups is an old link's param: duplicates are always grouped now, so it is ignored.
        # `job` is the selection shown in the split-view pane, not a filter.
        f: dict[str, Any] = {k: v for k, v in request.query_params.items()
                             if v and k not in ("stack", "mode", "hide_dups", "job")}
        if selected_stack := [v for v in request.query_params.getlist("stack") if v.strip()]:
            f["stack"] = selected_stack
        if selected_modes := [v for v in request.query_params.getlist("mode") if v.strip()]:
            f["mode"] = selected_modes
        selected = _stack_keys(selected_stack)
        conn = db()
        try:
            chosen = None
            raw_job = request.query_params.get("job", "")
            if raw_job.isascii() and raw_job.isdigit() and len(raw_job) < 18:
                with conn:            # selecting a job opens it, as the detail page does
                    conn.execute("UPDATE jobs SET status = 'seen' WHERE id = ? AND status = 'new'", (int(raw_job),))
                chosen = _job_detail(conn, int(raw_job), my_keys)
            # The query copy carries the cutoff day of the "posted" filter (from the app clock);
            # f itself stays the URL's params.
            qf = dict(f)
            if f.get("posted_weeks") in POSTED_WEEKS:
                qf["_posted_since"] = (clock().date() - timedelta(weeks=int(f["posted_weeks"]))).isoformat()
            jobs, total, page, pages, postings = _query_jobs(conn, qf, my_keys)
            pinned = _pinned_jobs(conn, my_keys)
            panel = search_panel(conn)
            days = conn.execute(
                "SELECT substr(first_seen_at, 1, 10) AS day, count(*) AS n FROM jobs GROUP BY day ORDER BY day DESC"
            ).fetchall()
            chips = _stack_chips(conn, cfg.stack, my_keys, selected, qf)
            live_cfg, config_error = read_search_cfg()
            stored_sites = [r[0] for r in conn.execute("SELECT DISTINCT site FROM jobs ORDER BY site")]
        finally:
            conn.close()
        current_url = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")

        selected_id = chosen["id"] if chosen else None

        def page_url(n: int) -> str:
            query = {k: v for k, v in f.items() if k != "page"}
            return "/?" + urlencode({**query, "page": n, **({"job": selected_id} if selected_id else {})}, doseq=True)

        without_stack = {k: v for k, v in f.items() if k not in ("stack", "page")}
        clear_stack_url = "/?" + urlencode(without_stack, doseq=True) if without_stack else "/"
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "jobs": jobs,
                "pinned": pinned,
                "total": total,
                "postings": postings,
                "page": page,
                "pages": pages,
                "prev_url": page_url(page - 1) if page > 1 else None,
                "next_url": page_url(page + 1) if page < pages else None,
                "f": f,
                "status_filter": f.get("status") or "open",
                "statuses": STATUSES,
                "work_modes": WORK_MODES,
                "posted_weeks": POSTED_WEEKS,
                "panel": panel,
                "has_filters": any(k != "page" for k in f),
                "days": days,
                "chips": chips,
                # Configured sources first, then any other source already stored, manual last.
                "source_options": list(dict.fromkeys(
                    [*live_cfg.sites, *(s for s in stored_sites if s != "manual"), "manual"])),
                "config_error": config_error,
                "selected_stack": selected,
                "clear_stack_url": clear_stack_url,
                "my_stack_size": len(my_keys),
                "sort_filter": "newest" if f.get("sort") == "newest" else "match",
                "running": is_running(),
                "current_url": current_url,
                "current_url_q": quote(current_url, safe=""),
                "selected_id": selected_id,
                "pane_job": chosen,
                "pane_next_url": _with_job(current_url, selected_id) if chosen else None,
                "pane_close_url": _with_job(current_url, None),
                "pane_back_q": quote(_with_job(current_url, selected_id), safe="") if chosen else "",
            },
        )

    @app.get("/offline")
    def offline(request: Request):
        return templates.TemplateResponse(request, "offline.html")

    @app.post("/jobs/{job_id}/status")
    def set_status(job_id: int, status: str = Form(...), next: str = Form("/")):
        if status not in STATUSES:
            return PlainTextResponse("Invalid status", status_code=400)
        target = safe_next(next)
        conn = db()
        try:
            with conn:
                previous = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
                if status == "applied":
                    applications.create_application_for_job(conn, job_id, clock().isoformat())
                    # Just applied: open the application so the CV sent can be attached.
                    app_row = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
                    if previous is not None and previous["status"] != "applied" and app_row:
                        target = f"/applications/{app_row['id']}"
        finally:
            conn.close()
        return RedirectResponse(target, status_code=303)

    @app.post("/jobs/{job_id}/pin")
    def set_pinned(job_id: int, pinned: str = Form(...), next: str = Form("/")):
        if pinned not in ("0", "1"):
            return PlainTextResponse("Invalid pinned value", status_code=400)
        conn = db()
        try:
            with conn:
                found = conn.execute(
                    "UPDATE jobs SET pinned_at = ? WHERE id = ?",
                    (clock().isoformat() if pinned == "1" else None, job_id),
                ).rowcount
        finally:
            conn.close()
        if not found:
            return PlainTextResponse("Unknown job", status_code=404)
        return RedirectResponse(safe_next(next), status_code=303)

    @app.get("/jobs/{job_id}")
    def job_detail(request: Request, job_id: int, back: str = "/", pane: str = ""):
        """Opening a job promotes "new" to "seen"; any other status is left alone.
        `?pane=1` answers with just the job view for the home list's split pane."""
        conn = db()
        try:
            with conn:
                conn.execute("UPDATE jobs SET status = 'seen' WHERE id = ? AND status = 'new'", (job_id,))
            job = _job_detail(conn, job_id, my_keys)
        finally:
            conn.close()
        if job is None:
            return PlainTextResponse("Unknown job", status_code=404)
        back = safe_next(back)
        if pane == "1":
            list_url = _with_job(back, job_id)       # the list as it is now, with this job selected
            html = templates.get_template("_job_pane.html").render({
                "job": job, "statuses": STATUSES, "in_pane": True, "next_url": list_url,
                "close_url": _with_job(back, None), "back_q": quote(list_url, safe=""),
            })
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        detail_url = f"/jobs/{job_id}?back={quote(back, safe='')}"
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "job": job,
                "back": back,
                "detail_url": detail_url,
                "next_url": detail_url,
                "back_q": quote(back, safe=""),
                "in_pane": False,
                "statuses": STATUSES,
            },
        )

    def phases_page(request: Request, error: str | None = None, status_code: int = 200):
        conn = db()
        try:
            phases = applications.list_phases(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "phases.html",
            {"phases": phases, "error": error, "max_len": applications.PHASE_NAME_MAX},
            status_code=status_code,
        )

    def phase_change(request: Request, change: Callable[[Any], None]):
        """Run one phase edit in a transaction; any refusal leaves the DB unchanged."""
        conn = db()
        try:
            with conn:
                change(conn)
        except ValueError as exc:
            return phases_page(request, str(exc), 400)
        except applications.PhaseInUse as exc:
            return phases_page(request, str(exc), 409)
        except LookupError as exc:
            return phases_page(request, str(exc), 404)
        finally:
            conn.close()
        return RedirectResponse("/applications/phases", status_code=303)

    @app.get("/applications/phases")
    def phases_settings(request: Request):
        return phases_page(request)

    @app.post("/applications/phases")
    def add_phase(request: Request, name: str = Form("")):
        return phase_change(request, lambda conn: applications.add_phase(conn, name))

    @app.post("/applications/phases/order")
    def order_phases(request: Request, order: str = Form("")):
        try:
            ids = [int(part) for part in order.split(",")]
        except ValueError:
            return phases_page(request, "Order must list every phase exactly once", 400)
        return phase_change(request, lambda conn: applications.set_phase_order(conn, ids))

    @app.post("/applications/phases/{phase_id}/rename")
    def rename_phase(request: Request, phase_id: int, name: str = Form("")):
        return phase_change(request, lambda conn: applications.rename_phase(conn, phase_id, name))

    @app.post("/applications/phases/{phase_id}/delete")
    def delete_phase(request: Request, phase_id: int):
        return phase_change(request, lambda conn: applications.delete_phase(conn, phase_id))

    @app.post("/applications/phases/{phase_id}/move")
    def move_phase(request: Request, phase_id: int, direction: str = Form("")):
        return phase_change(request, lambda conn: applications.move_phase(conn, phase_id, direction))

    # --- board, application page, CVs, manual add ---------------------------------------

    cvs_dir = Path(cfg.database).parent / "cvs"

    def unknown_application():
        return PlainTextResponse("Unknown application", status_code=404)

    def applications_state(request: Request, conn) -> dict[str, Any]:
        """Filters and view from the query string; unknown values are ignored."""
        raw = {k: request.query_params.getlist(k) for k in request.query_params.keys()}
        phases = applications.list_phases(conn)
        f = applications.parse_filters(
            raw, phase_ids={p["id"] for p in phases}, sites=set(applications.application_sites(conn)))
        f["view"] = "list" if (raw.get("view") or [""])[0] == "list" else "board"
        return f

    def applications_url(state: dict[str, Any], **override: Any) -> str:
        """URL of the applications page for `state` with some parts changed. Only what differs from
        the defaults is written; repeated values (outcome, phase) stay repeated parameters."""
        s = {**state, **override}
        query: dict[str, Any] = {}
        if s["view"] == "list":
            query["view"] = "list"
        if s["q"]:
            query["q"] = s["q"]
        if s["outcome"]:
            query["outcome"] = s["outcome"]
        if s["phase"]:
            query["phase"] = s["phase"]
        for key in ("site", "cv"):
            if s[key]:
                query[key] = s[key]
        if s["interview"]:
            query["interview"] = "1"
        if s["within"]:
            query["within"] = s["within"]
        if s["from_date"]:
            query["from"] = s["from_date"]
        if s["to_date"]:
            query["to"] = s["to_date"]
        if s["day"]:
            query["day"] = s["day"]
        if s["sort"] != applications.DEFAULT_SORT:
            query["sort"] = s["sort"]
        return "/applications" + (f"?{urlencode(query, doseq=True)}" if query else "")

    @app.get("/applications")
    def board(request: Request):
        conn = db()
        try:
            now = clock()
            phases = applications.list_phases(conn)
            f = applications_state(request, conn)
            rows = applications.find_applications(conn, now, f)
            all_totals = applications.totals(conn)
            outcome_counts = applications.facet_counts(conn, now, f, "outcome")
            phase_counts = applications.facet_counts(conn, now, f, "phase")
            sites = applications.application_sites(conn)
            history = goal.history(conn, now, cfg.daily_goal)
            calendar = goal.activity(conn, now)
        finally:
            conn.close()
        current_url = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
        view = f["view"]
        outcome_chips = [{"value": o, "label": o, "count": outcome_counts.get(o, 0), "on": o in f["outcome"]}
                         for o in applications.OUTCOMES]
        phase_chips = [{"value": str(p["id"]), "label": p["name"], "count": phase_counts.get(str(p["id"]), 0),
                        "on": str(p["id"]) in f["phase"]} for p in phases]
        if phase_counts.get(applications.UNASSIGNED_KEY) or applications.UNASSIGNED_KEY in f["phase"]:
            phase_chips.append({"value": applications.UNASSIGNED_KEY, "label": applications.UNASSIGNED,
                                "count": phase_counts.get(applications.UNASSIGNED_KEY, 0),
                                "on": applications.UNASSIGNED_KEY in f["phase"]})
        has_filters = bool(f["q"] or f["outcome"] or f["phase"] or f["site"] or f["cv"] or f["interview"]
                           or f["within"] or f["from_date"] or f["to_date"] or f["day"])
        only_open = f["outcome"] == ["active"]
        url = lambda **kw: applications_url(f, **kw)
        phase_names = {str(p["id"]): p["name"] for p in phases} | {applications.UNASSIGNED_KEY: applications.UNASSIGNED}

        def pretty_day(text: str) -> str:
            d = date.fromisoformat(text)
            return f"{d:%a %b} {d.day}" + (f" {d.year}" if d.year != now.year else "")

        # One pill per active filter value; its link is the page without exactly that value.
        pills = []
        if f["q"]:
            pills.append((f"“{f['q']}”", url(q="")))
        pills += [(o, url(outcome=[v for v in f["outcome"] if v != o])) for o in f["outcome"]]
        pills += [(phase_names[p], url(phase=[v for v in f["phase"] if v != p])) for p in f["phase"]]
        if f["site"]:
            pills.append((f"Source: {f['site']}", url(site="")))
        if f["cv"]:
            pills.append((f"CV {f['cv']}", url(cv="")))
        if f["interview"]:
            pills.append(("Upcoming interview", url(interview=False)))
        if f["within"]:
            pills.append((f"Last {f['within']} days", url(within=None)))
        if f["from_date"]:
            pills.append((f"Since {f['from_date']}", url(from_date="")))
        if f["to_date"]:
            pills.append((f"Until {f['to_date']}", url(to_date="")))
        if f["day"]:
            pills.append((f"Day {pretty_day(f['day'])}", url(day="")))
        badges = {
            "outcome": len(f["outcome"]), "phase": len(f["phase"]), "site": int(bool(f["site"])),
            "date": sum(bool(f[k]) for k in ("within", "from_date", "to_date", "day")),
            "more": sum(bool(f[k]) for k in ("cv", "interview")),
        }
        # Applied toggles between newest and oldest; every other column has one natural order.
        header_sorts = {"role": "company", "phase": "phase", "activity": "activity", "interview": "interview",
                        "applied": "oldest" if f["sort"] == "newest" else "newest"}
        sort_marks = {"newest": ("applied", "descending"), "oldest": ("applied", "ascending"),
                      "company": ("role", "ascending"), "phase": ("phase", "descending"),
                      "activity": ("activity", "descending"), "interview": ("interview", "ascending")}
        return templates.TemplateResponse(
            request,
            "board.html",
            {
                **all_totals,
                "history": history,
                "phases": phases,
                "f": f,
                "view": view,
                "rows": rows,
                "columns": applications.board(
                    rows, phases, show_closed=not f["outcome"] or any(o != "active" for o in f["outcome"]))
                if view == "board" else [],
                "shown": len(rows),
                "has_filters": has_filters,
                "pills": pills,
                "badges": badges,
                "day_label": pretty_day(f["day"]) if f["day"] else "",
                "calendar": calendar,
                "day_url": lambda day: url(day="" if f["day"] == day else day),
                "clear_search_url": url(q=""),
                "outcome_chips": outcome_chips,
                "phase_chips": phase_chips,
                "sites": sites,
                "sorts": [(k, applications.SORT_LABELS[k]) for k in applications.SORTS],
                "within_options": applications.WITHIN_DAYS,
                "view_urls": {"board": url(view="board"), "list": url(view="list")},
                "reset_url": applications_url({**f, "q": "", "outcome": [], "phase": [], "site": "", "cv": "",
                                               "interview": False, "within": None, "from_date": "",
                                               "to_date": "", "day": ""}),
                "only_open": only_open,
                "toggle_url": url(outcome=[]) if only_open else url(outcome=["active"]),
                "header_urls": {col: url(sort=sort) for col, sort in header_sorts.items()},
                "sort_mark": sort_marks[f["sort"]],
                "current_url": current_url,
            },
        )

    def manual_page(request: Request, values: dict, errors: list[str], status_code: int = 200):
        return templates.TemplateResponse(
            request, "manual.html", {"v": values, "errors": errors, "work_modes": WORK_MODES},
            status_code=status_code,
        )

    @app.get("/applications/new")
    def manual_form(request: Request):
        empty = dict.fromkeys(("title", "company", "link", "location", "work_mode", "salary"), "")
        return manual_page(request, {**empty, "applied_at": clock().date().isoformat()}, [])

    @app.post("/applications/new")
    def manual_create(
        request: Request,
        title: str = Form(""),
        company: str = Form(""),
        link: str = Form(""),
        location: str = Form(""),
        work_mode: str = Form(""),
        salary: str = Form(""),
        applied_at: str = Form(""),
    ):
        values, errors = applications.validate_manual({
            "title": title, "company": company, "link": link, "location": location,
            "work_mode": work_mode, "salary": salary, "applied_at": applied_at,
        })
        if errors:
            return manual_page(request, values, errors, 400)
        conn = db()
        try:
            with conn:
                app_id = applications.create_manual_application(conn, values, clock())
        finally:
            conn.close()
        return RedirectResponse(f"/applications/{app_id}", status_code=303)

    def application_page(request: Request, app_id: int, error: str | None = None, status_code: int = 200):
        conn = db()
        try:
            now = clock()
            application = applications.get_application(conn, app_id, now)
            phases = applications.list_phases(conn)
            cv_list = cvs.list_cvs(conn)
        finally:
            conn.close()
        if application is None:
            return unknown_application()
        return templates.TemplateResponse(
            request,
            "application.html",
            {
                "a": application,
                "phases": phases,
                "cvs": cv_list,
                "outcomes": applications.OUTCOMES,
                "now_input": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "error": error,
            },
            status_code=status_code,
        )

    def application_change(request: Request, app_id: int, change: Callable[[Any], None]):
        """Run one edit of an application in a transaction; a refusal leaves the DB unchanged."""
        conn = db()
        try:
            with conn:
                if conn.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone() is None:
                    raise LookupError("Unknown application")
                change(conn)
        except LookupError as exc:
            return PlainTextResponse(str(exc), status_code=404)
        except ValueError as exc:
            return application_page(request, app_id, str(exc), 413 if isinstance(exc, cvs.CvTooLarge) else 400)
        finally:
            conn.close()
        return RedirectResponse(f"/applications/{app_id}", status_code=303)

    @app.get("/applications/{app_id}")
    def application_detail(request: Request, app_id: int):
        return application_page(request, app_id)

    @app.post("/applications/{app_id}/move")
    def move_application(app_id: int, phase_id: str = Form(""), next: str = Form("/applications")):
        conn = db()
        try:
            with conn:
                if conn.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone() is None:
                    return unknown_application()
                try:
                    applications.move_application(conn, app_id, int(phase_id), clock().isoformat())
                except (ValueError, LookupError):
                    return PlainTextResponse("Unknown phase", status_code=400)
        finally:
            conn.close()
        return RedirectResponse(safe_next(next), status_code=303)

    @app.post("/applications/{app_id}/outcome")
    def change_outcome(request: Request, app_id: int, outcome: str = Form("")):
        return application_change(request, app_id, lambda conn: applications.set_outcome(conn, app_id, outcome))

    @app.post("/applications/{app_id}/entries")
    def add_entry(
        request: Request, app_id: int,
        phase_id: str = Form(""), happened_at: str = Form(""), notes: str = Form(""),
    ):
        return application_change(
            request, app_id, lambda conn: applications.add_entry(conn, app_id, phase_id, happened_at, notes)
        )

    @app.post("/applications/{app_id}/entries/{entry_id}")
    def edit_entry(
        request: Request, app_id: int, entry_id: int,
        phase_id: str = Form(""), happened_at: str = Form(""), notes: str = Form(""),
    ):
        return application_change(
            request, app_id,
            lambda conn: applications.update_entry(conn, app_id, entry_id, phase_id, happened_at, notes),
        )

    @app.post("/applications/{app_id}/entries/{entry_id}/delete")
    def remove_entry(request: Request, app_id: int, entry_id: int):
        return application_change(request, app_id, lambda conn: applications.delete_entry(conn, app_id, entry_id))

    @app.post("/applications/{app_id}/cv")
    async def attach_cv(
        request: Request, app_id: int,
        file: UploadFile | None = File(None), cv_id: str = Form(""),
    ):
        upload = None
        if file is not None and file.filename:
            upload = (file.filename, await file.read(cvs.MAX_BYTES + 1))

        def change(conn):
            if upload is not None:
                chosen = cvs.store(conn, cvs_dir, upload[0], upload[1], clock().isoformat())
            else:
                try:
                    chosen = int(cv_id)
                except ValueError:
                    raise ValueError("Pick a CV or choose a file to upload") from None
                if conn.execute("SELECT 1 FROM cv_files WHERE id = ?", (chosen,)).fetchone() is None:
                    raise ValueError("Unknown CV")
            conn.execute("UPDATE applications SET cv_id = ? WHERE id = ?", (chosen, app_id))

        return application_change(request, app_id, change)

    @app.get("/cvs/{cv_id}/view")
    @app.get("/cvs/{cv_id}")
    def download_cv(cv_id: int):
        conn = db()
        try:
            found = cvs.locate(conn, cvs_dir, cv_id)
        finally:
            conn.close()
        if found is None:
            return PlainTextResponse("Unknown CV", status_code=404)
        path, name, media_type = found
        # Browsers display PDFs, so open them in place; Word files can only be downloaded.
        disposition = "inline" if media_type == "application/pdf" else "attachment"
        return FileResponse(path, media_type=media_type, headers={
            "Content-Disposition": _attachment(name, disposition),
            "X-Content-Type-Options": "nosniff",
            # Never reuse a cached copy: one cached as a download would keep downloading.
            "Cache-Control": "no-store",
            # The application page previews the PDF in frames of its own; nobody else may embed it.
            "Content-Security-Policy": "frame-ancestors 'self'",
            "X-Frame-Options": "SAMEORIGIN",
        })

    @app.post("/search")
    def start_search(confirm: str = Form("")):
        with lock:
            if is_running():
                return JSONResponse({"reason": "running"}, status_code=409)
            conn = db()
            try:
                today, last_text = searched_today(conn)
            finally:
                conn.close()
            problem = read_search_cfg()[1]
            if problem:
                return JSONResponse({"reason": "config_error", "message": problem}, status_code=422)
            if today and not confirm:
                return JSONResponse(
                    {"reason": "already_searched_today", "last_searched": last_text}, status_code=409
                )

            def work():
                app.state.last_error = None
                app.state.last_result = None
                conn = db()
                try:
                    used = search_cfg()
                    app.state.step, app.state.total = 0, len(used.sites) * len(used.locations) * len(used.terms)

                    def on_progress(message: str) -> None:
                        app.state.step += 1          # one call per (site, place, term), just before it runs
                        app.state.progress = message

                    result = runner(conn, used, on_progress)
                    if isinstance(result, SearchResult):
                        app.state.last_result = {
                            "new": result.new_count,
                            "seen": result.seen_count,
                            "status": result.status,
                            "errors": result.errors,
                        }
                except SearchAlreadyRunning as exc:
                    app.state.last_error = str(exc)
                except Exception as exc:  # surfaced on the page, never swallowed
                    app.state.last_error = f"Search failed: {exc}"
                finally:
                    app.state.progress = ""
                    app.state.step = app.state.total = 0
                    conn.close()

            app.state.search_thread = threading.Thread(target=work, daemon=True)
            app.state.search_thread.start()
        return JSONResponse({"started": True}, status_code=202)

    @app.get("/search/status")
    def search_status():
        return {
            "running": is_running(),
            "progress": app.state.progress,
            "step": app.state.step,
            "total": app.state.total,
            "result": app.state.last_result,
            "error": app.state.last_error,
        }

    @app.get("/export")
    def export(day: str):
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            return PlainTextResponse("Invalid date, expected YYYY-MM-DD", status_code=400)
        conn = db()
        try:
            body = export_day(conn, parsed)
        finally:
            conn.close()
        return Response(
            body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="jobs-{parsed.isoformat()}.md"'},
        )

    return app
