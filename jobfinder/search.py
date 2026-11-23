"""Run an on-demand search covering everything posted since the last successful one."""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import SearchConfig
from .db import set_job_stack
from .dedup import is_possible_duplicate
from .enrich import _clean, detect_salary_basis, detect_work_mode, extract_stack, parse_salary

STALE_RUN_AFTER = timedelta(hours=2)
DUPLICATE_LOOKBACK = timedelta(days=90)


class SearchAlreadyRunning(RuntimeError):
    pass


@dataclass
class SearchResult:
    run_id: int
    status: str                      # success | partial | failed
    new_count: int = 0
    seen_count: int = 0
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LastSearch:
    run_id: int
    started_at: datetime
    finished_at: datetime
    status: str
    new_count: int
    errors: str | None


@dataclass(frozen=True)
class LatestRun:
    """Most recent finished run, including failed ones, for the search panel."""
    status: str                      # success | partial | failed
    new_count: int
    seen_count: int
    errors: dict[str, str]           # site -> raw error, from run_sites
    raw_error: str | None            # runs.error as stored


_SITE_NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor", "manual": "Manual"}
_RATE_LIMIT_HINTS = ("429", "blocked", "too many requests", "rate limit", "rate-limit")


def site_label(site: str) -> str:
    return _SITE_NAMES.get(site, site.title())


def humanize_error(raw: str | None) -> str:
    """Plain words for a technical scrape error: "rate-limited" for 429/blocked, otherwise "failed"."""
    low = (raw or "").lower()
    return "rate-limited" if any(hint in low for hint in _RATE_LIMIT_HINTS) else "failed"


def site_problems(errors: dict[str, str]) -> str:
    """"LinkedIn was rate-limited; Indeed failed" """
    return "; ".join(
        f"{site_label(site)} was rate-limited" if humanize_error(raw) == "rate-limited" else f"{site_label(site)} failed"
        for site, raw in errors.items()
    )


def describe_when(started: datetime, now: datetime) -> tuple[str, bool]:
    """("Searched today at 09:12", True) or ("Last searched Nov 9 at 18:30 (1 day ago)", False)."""
    local = started.astimezone(now.tzinfo)
    at = local.strftime("%H:%M")
    if local.date() == now.date():
        return f"Searched today at {at}", True
    days = (now.date() - local.date()).days
    year = f", {local.year}" if local.year != now.year else ""
    ago = f"{days} day{'' if days == 1 else 's'} ago"
    return f"Last searched {local:%b} {local.day}{year} at {at} ({ago})", False


def describe_outcome(run: LatestRun) -> dict[str, str | None]:
    """Panel copy for a finished run: kind (ok | partial | failed), headline, hint, raw details."""
    counts = f"{run.new_count} new · {run.seen_count} already seen"
    if run.status == "success":
        return {"kind": "ok", "headline": f"✓ {counts}", "hint": None, "details": None}
    problems = site_problems(run.errors)
    retry = "The next search retries what was missed."
    if run.status == "partial":
        return {"kind": "partial", "headline": f"! Partial: {problems}. {counts}.", "hint": retry,
                "details": run.raw_error}
    if not problems:
        return {"kind": "failed", "headline": "× The last search was interrupted.",
                "hint": "Press SEARCH to run it again.", "details": run.raw_error}
    return {"kind": "failed", "headline": f"× Search failed: {problems}.", "hint": retry, "details": run.raw_error}


def latest_run(conn: sqlite3.Connection) -> LatestRun | None:
    r = conn.execute(
        "SELECT * FROM runs WHERE status IN ('success', 'partial', 'failed') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return None
    errors = {
        s["site"]: s["error"] or ""
        for s in conn.execute(
            "SELECT site, error FROM run_sites WHERE run_id = ? AND status = 'failed' ORDER BY rowid", (r["id"],)
        )
    }
    return LatestRun(r["status"], r["new_count"], r["seen_count"], errors, r["error"])


def local_now() -> datetime:
    return datetime.now().astimezone()


def last_search(conn: sqlite3.Connection) -> LastSearch | None:
    """Most recent search that brought results from at least one site."""
    r = conn.execute(
        "SELECT * FROM runs WHERE status IN ('success', 'partial') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return None
    return LastSearch(
        run_id=r["id"],
        started_at=datetime.fromisoformat(r["started_at"]),
        finished_at=datetime.fromisoformat(r["finished_at"]),
        status=r["status"],
        new_count=r["new_count"],
        errors=r["error"],
    )


def running_search(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()


def hours_since_last_success(conn: sqlite3.Connection, site: str, now: datetime, cfg: SearchConfig) -> int:
    r = conn.execute(
        """SELECT runs.started_at FROM run_sites JOIN runs ON runs.id = run_sites.run_id
           WHERE run_sites.site = ? AND run_sites.status = 'success'
           ORDER BY runs.id DESC LIMIT 1""",
        (site,),
    ).fetchone()
    if r is None:
        return cfg.first_search_days * 24
    elapsed = now - datetime.fromisoformat(r["started_at"])
    return max(1, math.ceil(elapsed.total_seconds() / 3600)) + cfg.overlap_hours


def _start_run(conn: sqlite3.Connection, now: datetime) -> int:
    with conn:
        running = running_search(conn)
        if running is not None:
            if now - datetime.fromisoformat(running["started_at"]) < STALE_RUN_AFTER:
                raise SearchAlreadyRunning("A search is already running.")
            conn.execute(
                "UPDATE runs SET status = 'failed', error = 'interrupted' WHERE id = ?", (running["id"],)
            )
        cur = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (now.isoformat(),))
        return cur.lastrowid


def _text(value: Any) -> str | None:
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(getattr(value, "value", value))


def _assign_duplicate_group(conn: sqlite3.Connection, job_id: int, job: dict[str, Any], now: datetime) -> None:
    since = (now - DUPLICATE_LOOKBACK).isoformat()
    for other in conn.execute(
        "SELECT id, title, company, location, dup_group FROM jobs WHERE id != ? AND last_seen_at >= ?",
        (job_id, since),
    ):
        if is_possible_duplicate(job, dict(other)):
            group = other["dup_group"] or other["id"]
            conn.execute("UPDATE jobs SET dup_group = ? WHERE id IN (?, ?)", (group, job_id, other["id"]))
            return


def _upsert(conn: sqlite3.Connection, run_id: int, raw: dict[str, Any], term: str, now: datetime) -> bool:
    """Store one posting. Returns True when it was never seen before."""
    site, external_id = _text(raw.get("site")), _text(raw.get("id")) or _text(raw.get("job_url"))
    existing = conn.execute(
        "SELECT id, search_terms FROM jobs WHERE site = ? AND external_id = ?", (site, external_id)
    ).fetchone()
    if existing is not None:
        terms = [t for t in existing["search_terms"].split(", ") if t]
        if term not in terms:
            terms.append(term)
        conn.execute(
            "UPDATE jobs SET last_seen_at = ?, search_terms = ? WHERE id = ?",
            (now.isoformat(), ", ".join(terms), existing["id"]),
        )
        return False

    description = _text(raw.get("description")) or ""
    stack = extract_stack(description, _text(raw.get("skills")))
    salary = parse_salary(raw, description)
    is_remote = None if _clean(raw.get("is_remote")) is None else bool(raw.get("is_remote"))
    title, location = _text(raw.get("title")) or "(untitled)", _text(raw.get("location"))
    job = {
        "site": site,
        "external_id": external_id,
        "url": _text(raw.get("job_url")),
        "url_direct": _text(raw.get("job_url_direct")),
        "title": title,
        "company": _text(raw.get("company")),
        "location": location,
        "is_remote": None if is_remote is None else int(is_remote),
        "work_mode": detect_work_mode(title, location, description, is_remote),
        "date_posted": _text(raw.get("date_posted")),
        "job_type": _text(raw.get("job_type")),
        "salary_min": salary.min_amount if salary else None,
        "salary_max": salary.max_amount if salary else None,
        "salary_currency": salary.currency if salary else None,
        "salary_interval": salary.interval if salary else None,
        "salary_source": salary.source if salary else None,
        "salary_basis": detect_salary_basis(description) if salary else None,
        "stack": ", ".join(stack),
        "description": description,
        "search_terms": term,
        "first_seen_run": run_id,
        "first_seen_at": now.isoformat(),
        "last_seen_at": now.isoformat(),
    }
    cols = ", ".join(job)
    marks = ", ".join(f":{c}" for c in job)
    job_id = conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", job).lastrowid
    set_job_stack(conn, job_id, stack)
    _assign_duplicate_group(conn, job_id, job, now)
    return True


def run_search(
    conn: sqlite3.Connection,
    cfg: SearchConfig,
    scrape: Callable[..., Any] | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[str], None] = lambda message: None,
) -> SearchResult:
    if scrape is None:
        from jobspy import scrape_jobs as scrape
    started = now or local_now()
    run_id = _start_run(conn, started)
    result = SearchResult(run_id=run_id, status="failed")

    for site in cfg.sites:
        hours_old = hours_since_last_success(conn, site, started, cfg)
        site_error = None
        queries = [(location, term) for location in cfg.locations for term in cfg.terms]
        for i, (location, term) in enumerate(queries):
            if i and cfg.pause_seconds:
                sleep(cfg.pause_seconds)
            on_progress(f"{site}: {term} · {location}")
            try:
                df = scrape(
                    site_name=[site],
                    search_term=term,
                    location=location,
                    country_indeed=cfg.country_indeed,
                    results_wanted=cfg.results_wanted,
                    hours_old=hours_old,
                    fetch_description=True,
                    description_format="markdown",
                    verbose=0,
                )
                errors = getattr(df, "attrs", {}).get("errors") or {}
                if errors:
                    raise RuntimeError("; ".join(str(e) for e in errors.values()))
            except Exception as exc:  # one failing term must not lose the others
                site_error = f"{term} · {location}: {exc}"
                continue
            with conn:
                for raw in df.to_dict("records"):
                    raw["site"] = site
                    if _upsert(conn, run_id, raw, term, started):
                        result.new_count += 1
                    else:
                        result.seen_count += 1
        with conn:
            conn.execute(
                "INSERT INTO run_sites (run_id, site, hours_old, status, error) VALUES (?, ?, ?, ?, ?)",
                (run_id, site, hours_old, "failed" if site_error else "success", site_error),
            )
        if site_error:
            result.errors[site] = site_error

    ok_sites = len(cfg.sites) - len(result.errors)
    result.status = "success" if not result.errors else ("partial" if ok_sites else "failed")
    finished = started if now is not None else local_now()
    with conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, new_count = ?, seen_count = ?, error = ? WHERE id = ?",
            (
                finished.isoformat(),
                result.status,
                result.new_count,
                result.seen_count,
                "; ".join(f"{s}: {e}" for s, e in result.errors.items()) or None,
                run_id,
            ),
        )
    return result
