"""UI contract: search progress JSON, read/unread markup hooks and form labels."""
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobfinder.config import Config, SearchConfig
from jobfinder.db import connect, set_job_stack
from jobfinder.search import SearchResult
from jobfinder.web import create_app

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=TZ)
CSS = (Path(__file__).parent.parent / "jobfinder/static/style.css").read_text()


def make_cfg(tmp_path: Path, sites=("linkedin", "indeed"), terms=("a", "b", "c")) -> Config:
    return Config(
        search=SearchConfig(terms=list(terms), sites=list(sites), location="Mexico",
                            country_indeed="Mexico", pause_seconds=0),
        database=tmp_path / "jobs.db",
        exports_dir=tmp_path / "exports",
    )


def add_job(conn, ext_id, status="new", title=None):
    run = conn.execute("SELECT id FROM runs").fetchone()
    run = run[0] if run else conn.execute(
        "INSERT INTO runs (started_at, finished_at, status) VALUES (?, ?, 'success')",
        (NOW.isoformat(), NOW.isoformat()),
    ).lastrowid
    job = dict(
        site="linkedin", external_id=ext_id, url=f"https://example.com/{ext_id}",
        title=title or f"Role {ext_id}", company="Acme", location="CDMX", stack="", description="",
        first_seen_run=run, first_seen_at=NOW.isoformat(), last_seen_at=NOW.isoformat(), status=status,
    )
    cols = ", ".join(job)
    marks = ", ".join(f":{c}" for c in job)
    with conn:
        return conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", job).lastrowid


def row_for(html: str, title: str) -> str:
    (row,) = [r for r in re.findall(r'<li class="job status-\w+">.*?</li>', html, re.S) if title in r]
    return row


def run_search_with(tmp_path, runner, **kw):
    app = create_app(make_cfg(tmp_path), runner=runner, clock=lambda: NOW, **kw)
    client = TestClient(app)
    client.post("/search", data={"confirm": "1"})
    app.state.search_thread.join(timeout=5)
    return app, client


# --- R1 search progress JSON ----------------------------------------------------

def test_status_is_idle_with_zero_step_and_total(tmp_path):
    client = TestClient(create_app(make_cfg(tmp_path), clock=lambda: NOW))
    assert client.get("/search/status").json() == {
        "running": False, "progress": "", "step": 0, "total": 0, "result": None, "error": None,
    }


def test_status_reports_step_and_total_while_running(tmp_path):
    reached, release = threading.Event(), threading.Event()

    def runner(conn, search_cfg, on_progress):
        on_progress("linkedin: a")
        on_progress("linkedin: b")
        reached.set()
        assert release.wait(5)
        return SearchResult(run_id=1, status="success", new_count=12, seen_count=30)

    app = create_app(make_cfg(tmp_path), runner=runner, clock=lambda: NOW)   # 2 sites x 3 terms
    client = TestClient(app)
    assert client.post("/search").status_code == 202
    assert reached.wait(5)
    data = client.get("/search/status").json()
    assert data["running"] is True
    assert (data["step"], data["total"]) == (2, 6)
    assert data["progress"] == "linkedin: b"
    assert data["result"] is None

    release.set()
    app.state.search_thread.join(timeout=5)
    done = client.get("/search/status").json()
    assert done["running"] is False
    assert (done["step"], done["total"], done["progress"]) == (0, 0, "")
    assert done["result"] == {"new": 12, "seen": 30, "status": "success", "errors": {}}


def test_total_comes_from_the_config_read_for_this_search(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[search]\nterms = ["x", "y"]\nsites = ["linkedin", "indeed", "manual"]\n'
        'location = "Mexico"\ncountry_indeed = "Mexico"\n'
        '[storage]\ndatabase = "jobs.db"\n'
    )
    seen = []

    def runner(conn, search_cfg, on_progress):
        on_progress("linkedin: x")
        seen.append(client.get("/search/status").json())

    # built with a different config on purpose: the file is re-read per search, like the real run
    app = create_app(make_cfg(tmp_path), runner=runner, clock=lambda: NOW, config_path=config_path)
    client = TestClient(app)
    client.post("/search")
    app.state.search_thread.join(timeout=5)
    assert (seen[0]["step"], seen[0]["total"]) == (1, 6)


def test_total_counts_every_place_in_a_location_list(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[search]\nterms = ["x", "y"]\nsites = ["linkedin", "indeed", "glassdoor"]\n'
        'location = ["Monterrey, Nuevo León, Mexico", "Mexico"]\ncountry_indeed = "Mexico"\n'
        '[storage]\ndatabase = "jobs.db"\n'
    )
    seen = []

    def runner(conn, search_cfg, on_progress):
        on_progress("linkedin: x · Monterrey, Nuevo León, Mexico")
        seen.append(client.get("/search/status").json())

    app = create_app(make_cfg(tmp_path), runner=runner, clock=lambda: NOW, config_path=config_path)
    client = TestClient(app)
    client.post("/search")
    app.state.search_thread.join(timeout=5)
    assert (seen[0]["step"], seen[0]["total"]) == (1, 12)   # 3 sites x 2 terms x 2 places


def test_partial_result_is_reported(tmp_path):
    def partial(conn, search_cfg, on_progress):
        return SearchResult(run_id=1, status="partial", new_count=1, seen_count=2, errors={"linkedin": "429"})

    _, client = run_search_with(tmp_path, partial)
    assert client.get("/search/status").json()["result"] == {
        "new": 1, "seen": 2, "status": "partial", "errors": {"linkedin": "429"},
    }


def test_failure_is_reported_without_a_result(tmp_path):
    def boom(conn, search_cfg, on_progress):
        raise RuntimeError("boom")

    _, client = run_search_with(tmp_path, boom)
    data = client.get("/search/status").json()
    assert data["running"] is False and data["result"] is None
    assert data["error"] == "Search failed: boom"
    assert (data["step"], data["total"]) == (0, 0)


def test_a_new_search_clears_the_previous_result(tmp_path):
    results = iter([SearchResult(run_id=1, status="success", new_count=5), None])
    app, client = run_search_with(tmp_path, lambda conn, cfg, progress: next(results))
    assert client.get("/search/status").json()["result"]["new"] == 5
    client.post("/search", data={"confirm": "1"})
    app.state.search_thread.join(timeout=5)
    assert client.get("/search/status").json()["result"] is None


def test_search_panel_has_button_status_and_live_region(tmp_path):
    html = TestClient(create_app(make_cfg(tmp_path), clock=lambda: NOW)).get("/").text
    bar = html[html.index('<section class="search-panel"'):html.index('<form class="filters"')]
    for hook in ('id="search-btn"', 'id="search-last"', 'id="search-live"', 'role="status"', 'id="search-confirm"'):
        assert hook in bar
    overlay = html[html.index('<div id="search-overlay"'):]
    for hook in ('id="search-progress-text"', 'id="search-meter"', 'role="progressbar"', 'id="search-step"'):
        assert hook in overlay
    assert re.search(r'<div id="search-overlay"[^>]*\bhidden\b', html)          # idle: hidden


def test_idle_page_is_not_in_the_searching_state(tmp_path):
    html = TestClient(create_app(make_cfg(tmp_path), clock=lambda: NOW)).get("/").text
    button = re.search(r'<button id="search-btn".*?</button>', html, re.S).group(0)
    assert "disabled" not in button and "SEARCHING" not in button


# --- R2 read vs unread markup ---------------------------------------------------

@pytest.fixture
def jobs_client(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    ids = {s: add_job(conn, s, status=s, title=f"{s.title()} Role") for s in ("new", "seen", "applied", "discarded")}
    conn.commit()
    return TestClient(create_app(cfg, clock=lambda: NOW)), ids


def test_each_status_has_its_row_class_and_marker(jobs_client):
    client, _ = jobs_client
    html = client.get("/", params={"status": "all"}).text
    new, seen = row_for(html, "New Role"), row_for(html, "Seen Role")
    applied, discarded = row_for(html, "Applied Role"), row_for(html, "Discarded Role")
    for status, row in (("new", new), ("seen", seen), ("applied", applied), ("discarded", discarded)):
        assert row.startswith(f'<li class="job status-{status}">')

    assert 'class="unread-dot"' in new and 'aria-label="Unread"' in new      # a marker, not colour alone
    for other in (seen, applied, discarded):
        assert "unread-dot" not in other
    assert "tag state-applied" in applied and "APPLIED" in applied
    assert "tag state-discarded" in discarded and "DISCARDED" in discarded
    assert "state-applied" not in new + seen + discarded
    assert "state-discarded" not in new + seen + applied


def test_new_tag_is_only_shown_while_unread(jobs_client):
    client, _ = jobs_client
    html = client.get("/", params={"status": "all"}).text
    assert 'class="tag new"' in row_for(html, "New Role")
    for title in ("Seen Role", "Applied Role", "Discarded Role"):
        assert 'class="tag new"' not in row_for(html, title)


def test_opening_a_job_removes_its_unread_marker_in_the_list(jobs_client):
    client, ids = jobs_client
    assert "unread-dot" in row_for(client.get("/").text, "New Role")
    client.get(f"/jobs/{ids['new']}")
    row = row_for(client.get("/", params={"status": "all"}).text, "New Role")
    assert row.startswith('<li class="job status-seen">') and "unread-dot" not in row


def test_detail_page_tags_applied_and_discarded_jobs(jobs_client):
    client, ids = jobs_client
    assert "tag state-applied" in client.get(f"/jobs/{ids['applied']}").text
    assert "tag state-discarded" in client.get(f"/jobs/{ids['discarded']}").text
    assert "state-" not in client.get(f"/jobs/{ids['seen']}").text.split("<main")[1].split("</h2>")[0]


def test_css_distinguishes_read_from_unread_without_colour_alone():
    def rule(selector):
        return re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS).group(1)

    assert "font-weight: 700" in rule(".job.status-new .title")
    assert "font-weight: 400" in rule(".job.status-seen .title")
    assert "box-shadow" in rule(".job.status-new")                          # unread bar
    assert "line-through" in rule(".job.status-discarded .title")


def _contrast_on_black(hex_color: str) -> float:
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b) + 0.05) / 0.05


@pytest.mark.parametrize("token", ["--muted", "--dim", "--fg", "--warn", "--error"])
def test_text_colours_keep_aa_contrast_on_black(token):
    colour = re.search(rf"{token}:\s*(#[0-9a-fA-F]{{6}})", CSS).group(1)
    assert _contrast_on_black(colour) >= 4.5


# --- R3 labels, aria and states -------------------------------------------------

def test_filter_controls_have_accessible_names(tmp_path):
    conn = connect(make_cfg(tmp_path).database)
    job_id = add_job(conn, "1")
    set_job_stack(conn, job_id, ["Kotlin"])
    conn.commit()
    html = TestClient(create_app(make_cfg(tmp_path), clock=lambda: NOW)).get("/").text
    form = html[html.index('<form class="filters"'):html.index("</form>")]
    assert 'aria-label="Search title, company or description"' in form
    assert re.search(r'<fieldset class="stack-filter">\s*<legend>Stack', form)   # the chips group is named "Stack"
    for name in ("Status", "Source", "Currency", "First seen"):
        assert f'<span class="field-label">{name}</span><select' in form, name
    assert '<legend class="field-label">Work mode</legend>' in form              # toggle chips, several at once
    assert 'aria-label="Day to export"' in html


def test_row_controls_name_the_job_they_act_on(jobs_client):
    client, _ = jobs_client
    row = row_for(client.get("/", params={"status": "all"}).text, "New Role")
    assert 'aria-label="Status of New Role"' in row
    assert 'aria-label="Pin New Role"' in row


def test_empty_list_explains_how_to_recover(tmp_path):
    client = TestClient(create_app(make_cfg(tmp_path), clock=lambda: NOW))
    assert "No offers yet" in client.get("/").text
    filtered = client.get("/", params={"q": "nothing-matches"}).text
    assert "No offers match these filters" in filtered and 'href="/" data-reset-filters>Clear all filters' in filtered


def test_singular_offer_count(jobs_client):
    client, _ = jobs_client
    assert "1 offer</p>" in client.get("/", params={"status": "applied"}).text


@pytest.fixture
def applied_client(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    job = add_job(conn, "1")
    conn.commit()
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    client.post(f"/jobs/{job}/status", data={"status": "applied"})
    return client


def test_application_page_selects_have_visible_labels(applied_client):
    html = applied_client.get("/applications/1").text
    assert re.search(r'<label class="field"><span class="field-label">Phase</span>\s*<select name="phase_id"', html)
    assert re.search(r'<label class="field"><span class="field-label">Outcome</span>\s*<select name="outcome"', html)


def test_board_empty_columns_say_so(applied_client):
    assert 'class="column-empty muted">No applications' in applied_client.get("/applications").text


def test_manual_form_marks_required_fields(tmp_path):
    html = TestClient(create_app(make_cfg(tmp_path), clock=lambda: NOW)).get("/applications/new").text
    for field in ("Title", "Company", "Applied on"):
        assert f'<span>{field} <span class="req">required</span></span>' in html
    assert html.count('class="req"') == 3


def test_css_has_focus_visible_reduced_motion_and_hidden_rules():
    assert ":focus-visible" in CSS
    assert "prefers-reduced-motion: reduce" in CSS
    assert "[hidden] { display: none !important; }" in CSS
