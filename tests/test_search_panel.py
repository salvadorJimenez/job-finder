"""Search panel (status + SEARCH), inline confirm, blocking overlay and labelled filters."""
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobfinder.config import Config, SearchConfig
from jobfinder.db import connect
from jobfinder.search import SearchResult, humanize_error
from jobfinder.web import create_app

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 11, 10, 14, 0, tzinfo=TZ)
ROOT = Path(__file__).parent.parent / "jobfinder"
CSS = (ROOT / "static/style.css").read_text()
JS = (ROOT / "static/app.js").read_text()
RAW_429 = "linkedin: fullstack: response status code 429 (blocked: use a proxy)"


def make_cfg(tmp_path: Path) -> Config:
    return Config(
        search=SearchConfig(terms=["a", "b"], sites=["linkedin", "indeed"], location="Mexico",
                            country_indeed="Mexico", pause_seconds=0),
        database=tmp_path / "jobs.db",
        exports_dir=tmp_path / "exports",
    )


def add_run(conn, started, status="success", new=12, seen=30, site_errors=None, error=None):
    with conn:
        run = conn.execute(
            "INSERT INTO runs (started_at, finished_at, status, new_count, seen_count, error) VALUES (?, ?, ?, ?, ?, ?)",
            (started.isoformat(), started.isoformat(), status, new, seen, error),
        ).lastrowid
        for site, err in (site_errors or {}).items():
            conn.execute("INSERT INTO run_sites (run_id, site, hours_old, status, error) VALUES (?, ?, 24, 'failed', ?)",
                         (run, site, err))
    return run


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    app = create_app(cfg, clock=lambda: NOW)
    return TestClient(app), conn, app


def panel(html: str) -> str:
    return html[html.index('<section class="search-panel"'):html.index("</section>", html.index('<section class="search-panel"'))]


# --- human messages -----------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("response status code 429 (blocked: use a proxy)", "rate-limited"),
    ("fullstack: 429 Too Many Requests", "rate-limited"),
    ("Blocked by the site", "rate-limited"),
    ("connection reset by peer", "failed"),
    ("boom", "failed"),
    ("", "failed"),
])
def test_error_messages_are_mapped_to_rate_limited_or_failed(raw, expected):
    assert humanize_error(raw) == expected


# --- panel states -------------------------------------------------------------------

def test_never_searched(env):
    client, _, _ = env
    html = client.get("/").text
    p = panel(html)
    assert "Never searched" in p
    assert re.search(r'<button id="search-btn"[^>]*>\s*SEARCH\s*</button>', p)
    assert "badge-today" not in p and 'class="search-outcome' not in p


def test_searched_today_shows_a_calm_badge_and_the_outcome(env):
    client, conn, _ = env
    add_run(conn, NOW - timedelta(hours=5))                                  # 09:00 local
    p = panel(client.get("/").text)
    assert "Searched today at 09:00" in p
    assert re.search(r'<span class="badge badge-today">today ✓</span>', p)
    assert re.search(r'class="search-outcome ok"[^>]*>✓ 12 new · 30 already seen', p)
    assert "partial" not in p and "failed" not in p


def test_today_state_is_not_amber_but_warnings_are():
    assert not re.search(r"\.badge-today[^{]*\{[^}]*var\(--warn\)", CSS)
    assert re.search(r"\.search-outcome\.partial\s*\{[^}]*var\(--warn\)", CSS)
    assert re.search(r"\.search-outcome\.failed\s*\{[^}]*var\(--error\)", CSS)


@pytest.mark.parametrize("days, ago", [(1, "1 day ago"), (3, "3 days ago")])
def test_older_search_says_when_and_how_long_ago(env, days, ago):
    client, conn, _ = env
    add_run(conn, datetime(2026, 11, 10 - days, 18, 30, tzinfo=TZ))
    p = panel(client.get("/").text)
    assert f"Last searched Nov {10 - days} at 18:30 ({ago})" in p
    assert "badge-today" not in p


def test_partial_outcome_is_human_with_raw_error_collapsed(env):
    client, conn, _ = env
    add_run(conn, NOW - timedelta(hours=1), status="partial", site_errors={"linkedin": "fullstack: 429 (blocked: use a proxy)"},
            error=RAW_429)
    p = panel(client.get("/").text)
    assert re.search(r'class="search-outcome partial"[^>]*>! Partial: LinkedIn was rate-limited', p)
    assert "The next search retries what was missed." in p
    details = re.search(r"<details class=\"search-details\">(.*?)</details>", p, re.S).group(1)
    assert "<summary>details</summary>" in details and "response status code 429" in details
    assert "response status code 429" not in p.replace(details, "")          # the raw text lives only inside <details>
    assert 'class="notice' not in client.get("/").text                      # one panel, no separate notice


def test_rate_limit_stop_is_readable_in_the_panel(env):
    from jobfinder.search import run_search
    client, conn, _ = env

    def scrape(**kw):
        if kw["site_name"] == ["linkedin"]:
            raise RuntimeError("response status code 429 (blocked: use a proxy)")
        import pandas as pd
        return pd.DataFrame([])

    run_search(conn, make_cfg(Path(".")).search, scrape=scrape, now=NOW - timedelta(minutes=5))
    p = panel(client.get("/").text)
    assert re.search(r'class="search-outcome partial"[^>]*>! Partial: LinkedIn was rate-limited\. 0 new', p)
    details = re.search(r"<details class=\"search-details\">(.*?)</details>", p, re.S).group(1)
    assert "(stopped after a rate limit; 1 request skipped)" in details
    assert "stopped after a rate limit" not in p.replace(details, "")


def test_failed_outcome_keeps_the_last_good_search_as_title(env):
    client, conn, _ = env
    add_run(conn, datetime(2026, 11, 8, 9, 0, tzinfo=TZ))
    add_run(conn, NOW - timedelta(hours=1), status="failed", new=0, seen=0,
            site_errors={"linkedin": "a: 429", "indeed": "a: connection reset"}, error="linkedin: a: 429; indeed: a: connection reset")
    p = panel(client.get("/").text)
    assert "Last searched Nov 8 at 09:00 (2 days ago)" in p
    assert re.search(r'class="search-outcome failed"[^>]*>× Search failed: LinkedIn was rate-limited; Indeed failed', p)
    assert "<details" in p


def test_failed_run_without_any_good_search_still_says_never_searched(env):
    client, conn, _ = env
    add_run(conn, NOW - timedelta(hours=1), status="failed", new=0, seen=0, error="interrupted")
    p = panel(client.get("/").text)
    assert "Never searched" in p and "search-outcome failed" in p


def test_a_crashed_search_is_reported_inside_the_panel(env):
    client, _, app = env
    app.state.last_error = "Search failed: boom"
    p = panel(client.get("/").text)
    assert 'class="search-outcome failed"' in p and "boom" in p.split("<details", 1)[1]


def test_panel_sits_right_under_the_header_before_the_filters(env):
    client, _, _ = env
    after = client.get("/").text.split("</header>", 1)[1]
    assert after.index('class="search-panel"') < after.index('class="filters"')


# --- inline confirm -----------------------------------------------------------------

def test_inline_confirm_markup_and_hooks(env):
    client, conn, _ = env
    add_run(conn, NOW - timedelta(hours=5))
    html = client.get("/").text
    p = panel(html)
    assert 'data-searched-today="1"' in p
    assert re.search(r'<div id="search-confirm"[^>]*\bhidden\b', p)
    assert "You already searched today at 09:00. Search again?" in p
    assert re.search(r'<button id="search-confirm-yes"[^>]*>SEARCH AGAIN</button>', p)
    assert re.search(r'<button id="search-confirm-no"[^>]*>CANCEL</button>', p)
    assert "window.confirm" not in JS and "confirm" in JS and 'body.set("confirm", "1")' in JS


# --- blocking while searching -------------------------------------------------------

def test_idle_page_is_not_blocked(env):
    client, _, _ = env
    html = client.get("/").text
    area = re.search(r'<div id="results-area"[^>]*>', html).group(0)
    assert "inert" not in area and "aria-busy" not in area
    assert re.search(r'<div id="search-overlay"[^>]*\bhidden\b', html)
    assert re.search(r'<button id="search-btn"[^>]*>\s*SEARCH\s*</button>', html) and "SEARCHING" not in panel(html)


def test_page_loaded_mid_search_is_blocked_with_overlay(tmp_path):
    cfg = make_cfg(tmp_path)
    gate = threading.Event()
    app = create_app(cfg, clock=lambda: NOW, runner=lambda conn, c, progress: (progress("linkedin: a"), gate.wait(5),
                                                                          SearchResult(run_id=1, status="success"))[-1])
    client = TestClient(app)
    assert client.post("/search").status_code == 202
    try:
        html = client.get("/").text
        area = re.search(r'<div id="results-area"[^>]*>', html).group(0)
        assert " inert" in area and 'aria-busy="true"' in area
        assert not re.search(r'<div id="search-overlay"[^>]*\bhidden\b', html)
        assert re.search(r'<button id="search-btn"[^>]*\bdisabled\b[^>]*>\s*SEARCHING…\s*</button>', html)
        # the area holds filters, export, pinned and list; the overlay sits outside it so it stays readable
        inner = html[html.index('<div id="results-area"'):html.index('<div id="search-overlay"')]
        assert '<form class="filters"' in inner and '<form class="export"' in inner and 'id="job-list"' in inner
        assert "<header" not in inner and "main-nav" not in inner
    finally:
        gate.set()
        app.state.search_thread.join(timeout=5)


def test_overlay_has_loader_step_site_term_and_progress_bar(env):
    client, _, _ = env
    html = client.get("/").text
    overlay = html[html.index('<div id="search-overlay"'):]
    for hook in ('class="pixel-loader"', 'id="search-step"', 'id="search-progress-text"', 'id="search-meter"',
                 'role="progressbar"', 'tabindex="-1"'):
        assert hook in overlay, hook
    assert 'id="search-live"' in panel(html) and 'aria-live="polite"' in panel(html)


def test_js_blocks_with_inert_and_aria_busy_and_moves_focus():
    assert ".inert = " in JS and 'setAttribute("aria-busy", "true")' in JS and 'removeAttribute("aria-busy")' in JS
    assert "lost connection, retrying…" in JS
    assert re.search(r"\.focus\(\)", JS) and "Escape" in JS


def test_css_dims_blocked_area_and_respects_reduced_motion():
    assert re.search(r'#results-area\[aria-busy="true"\]\s*\{[^}]*opacity', CSS)
    reduced = CSS[CSS.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".pixel-loader" in reduced and "animation: none" in reduced


# --- labelled filters ---------------------------------------------------------------

def filter_form(html):
    return html[html.index('<form class="filters"'):html.index("</form>", html.index('<form class="filters"'))]


def test_every_filter_has_a_visible_label_that_focuses_its_control(env):
    client, conn, _ = env
    form = filter_form(client.get("/").text)
    for label, control in [("Search", '<input type="search" name="q"'), ("Status", '<select name="status">'),
                           ("Source", '<select name="site">'), ("Currency", '<select name="currency">'),
                           ("First seen", '<select name="day">')]:
        assert re.search(rf'<label class="field[^"]*"><span class="field-label">{label}</span>{re.escape(control)}', form), label
    assert '<fieldset class="field field-modes"><legend class="field-label">Work mode</legend>' in form
    assert not re.search(r'class="sr-only"', form)                              # no more hidden-only names


def test_sort_is_labelled_when_a_stack_is_configured(tmp_path):
    cfg = replace(make_cfg(tmp_path), stack=["Kotlin"])
    html = TestClient(create_app(cfg, clock=lambda: NOW)).get("/").text
    assert re.search(r'<span class="field-label">Sort</span><select name="sort">', filter_form(html))


def test_checkboxes_are_grouped_under_a_label_and_stack_keeps_its_own(env):
    client, _, _ = env
    form = filter_form(client.get("/").text)
    assert re.search(r'<fieldset class="field field-checks">\s*<legend>Options</legend>', form)
    for name in ("with salary", "only new"):
        assert name in form
    assert "hide duplicates" not in form                                          # grouping replaced it


def test_reset_filters_only_shows_when_a_filter_is_active(env):
    client, _, _ = env
    assert "Reset filters" not in client.get("/").text
    assert "Reset filters" not in client.get("/", params={"page": 2}).text
    form = filter_form(client.get("/", params={"site": "linkedin"}).text)
    assert re.search(r'<a class="reset" href="/" data-reset-filters>Reset filters</a>', form)
    assert form.index("Reset filters") < form.index('name="q"')                  # top of the block


def test_filters_are_a_responsive_grid_and_day_select_is_wide_enough():
    rule = re.search(r"\.filters\s*\{([^}]*)\}", CSS).group(1)
    assert "display: grid" in rule and re.search(r"repeat\(auto-fill,\s*minmax\(1[6-9]\dpx,\s*1fr\)\)", rule)
    assert re.search(r"@media \(max-width: 5\d\dpx\)\s*\{[^@]*\.filters[^}]*repeat\(2,", CSS)
