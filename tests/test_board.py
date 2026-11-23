import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobfinder import applications as apps
from jobfinder.db import connect
from jobfinder.web import create_app
from test_applications import make_job
from test_web import NOW, make_cfg

APPLIED = "2026-11-01T09:00:00-06:00"   # 3 days before NOW (2026-11-04 10:00 -06:00)


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    return client, conn


def pid(conn, name):
    return conn.execute("SELECT id FROM phases WHERE name = ?", (name,)).fetchone()[0]


def make_app(conn, ext, title="Dev", company="Acme", phase="Applied", applied=APPLIED,
             outcome="active", mode=None, url="https://x"):
    job = make_job(conn, ext)
    conn.execute("UPDATE jobs SET title = ?, company = ?, work_mode = ?, url = ? WHERE id = ?",
                 (title, company, mode, url, job))
    app_id = apps.create_application_for_job(conn, job, applied)
    conn.execute("UPDATE applications SET phase_id = ?, outcome = ? WHERE id = ?",
                 (pid(conn, phase) if phase else None, outcome, app_id))
    conn.commit()
    return app_id


def add_note(conn, app_id, happened_at, notes="", phase="Technical", kind="note"):
    conn.execute(
        "INSERT INTO timeline_entries (application_id, phase_name, happened_at, notes, kind) VALUES (?, ?, ?, ?, ?)",
        (app_id, phase, happened_at, notes, kind))
    conn.commit()


def columns(html):
    """{column key: section html} in page order."""
    parts = re.split(r'(?=<section class="column)', html)[1:]
    return {re.search(r'data-column="([^"]+)"', p).group(1): p for p in parts}


def column_titles(html):
    return [re.sub(r"<[^>]+>", "", re.search(r'<h3[^>]*>(.*?)</h3>', c, re.S).group(1)).strip().rsplit(" ", 1)[0]
            for c in columns(html).values()]


def snapshot(conn):
    return (
        [tuple(r) for r in conn.execute("SELECT id, phase_id, outcome FROM applications ORDER BY id")],
        [tuple(r) for r in conn.execute("SELECT * FROM timeline_entries ORDER BY id")],
    )


# --- nav ------------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["/", "/jobs/1", "/applications", "/applications/phases"])
def test_header_nav_applications_points_to_the_board(env, url):
    client, conn = env
    make_job(conn)
    html = client.get(url).text
    assert re.search(r'<nav class="main-nav">.*?<a href="/applications"[^>]*>Applications</a>', html, re.S)
    assert re.search(r'<a href="/" data-keep-filters( aria-current="page")?>Jobs</a>', html)


def test_board_links_to_phase_settings_and_manual_add(env):
    client, conn = env
    make_app(conn, "a")
    html = client.get("/applications").text
    assert re.search(r'<a [^>]*href="/applications/phases"[^>]*>Phases</a>', html)
    assert 'href="/applications/new"' in html


# --- empty state ------------------------------------------------------------------------

def test_empty_board_points_to_applied_jobs_or_manual_add(env):
    client, conn = env
    html = client.get("/applications").text
    assert 'class="board-empty"' in html
    assert "marking a job as applied" in html.lower()
    assert 'href="/applications/new"' in html
    assert 'class="column' not in html


# --- columns ----------------------------------------------------------------------------

def test_one_column_per_phase_in_order_then_closed(env):
    client, conn = env
    make_app(conn, "a")
    html = client.get("/applications").text
    assert column_titles(html) == [
        "Applied", "Recruiter screen", "Hiring manager", "Technical", "Final", "Offer", "Closed"]
    assert list(columns(html))[-1] == "closed"


def test_columns_follow_phase_reordering_and_custom_phases(env):
    client, conn = env
    make_app(conn, "a")
    client.post(f"/applications/phases/{pid(conn, 'Offer')}/move", data={"direction": "up"})
    client.post("/applications/phases", data={"name": "Take-home"})
    titles = column_titles(client.get("/applications").text)
    assert titles[3:] == ["Technical", "Offer", "Final", "Take-home", "Closed"]


def test_cards_sit_in_their_phase_column(env):
    client, conn = env
    make_app(conn, "a", title="In Applied")
    make_app(conn, "b", title="In Technical", phase="Technical")
    cols = columns(client.get("/applications").text)
    assert "In Applied" in cols[f"phase-{pid(conn, 'Applied')}"]
    assert "In Technical" not in cols[f"phase-{pid(conn, 'Applied')}"]
    assert "In Technical" in cols[f"phase-{pid(conn, 'Technical')}"]


def test_closed_applications_appear_only_in_the_closed_column(env):
    client, conn = env
    make_app(conn, "a", title="Still active")
    make_app(conn, "b", title="Was rejected", phase="Technical", outcome="rejected")
    html = client.get("/applications").text
    cols = columns(html)
    assert "Was rejected" in cols["closed"]
    assert "Still active" not in cols["closed"]
    assert html.count("Was rejected") == 1


def test_closed_column_can_be_hidden_and_is_shown_by_default(env):
    client, conn = env
    make_app(conn, "a", title="Was rejected", outcome="rejected")
    make_app(conn, "b", title="Active")
    assert "closed" in columns(client.get("/applications").text)
    assert "closed" in columns(client.get("/applications?closed=1").text)
    hidden = client.get("/applications?closed=0").text
    assert "closed" not in columns(hidden)
    assert "Was rejected" not in hidden
    assert "Active" in hidden


def test_toggle_link_flips_closed_visibility(env):
    client, conn = env
    make_app(conn, "a")
    # "Hide closed" is the shortcut for the outcome filter `active`; the old ?closed=0 link still works
    assert 'href="/applications?outcome=active">Hide closed<' in client.get("/applications").text
    assert 'href="/applications">Show closed<' in client.get("/applications?outcome=active").text
    assert 'href="/applications">Show closed<' in client.get("/applications?closed=0").text


def test_unassigned_column_only_when_an_application_has_no_phase(env):
    client, conn = env
    make_app(conn, "a", title="Has phase")
    assert "unassigned" not in columns(client.get("/applications").text)
    make_app(conn, "b", title="No phase", phase=None)
    cols = columns(client.get("/applications").text)
    assert "No phase" in cols["unassigned"]
    assert list(cols)[-2:] == ["unassigned", "closed"]
    assert "data-phase-id" not in cols["unassigned"]       # cannot drop into it


# --- card ---------------------------------------------------------------------------------

def test_card_shows_title_link_company_mode_and_days_since_applied(env):
    client, conn = env
    app_id = make_app(conn, "a", title="Spring Engineer", company="Globex", mode="hybrid")
    card = columns(client.get("/applications").text)[f"phase-{pid(conn, 'Applied')}"]
    assert f'<a class="title" href="/applications/{app_id}">Spring Engineer</a>' in card
    assert "Globex" in card
    assert 'class="tag mode mode-hybrid">HYBRID<' in card
    meta = re.search(r'<div class="card-meta[^>]*>(.*?)</div>', card, re.S).group(1)   # one line: company, mode, age
    assert "Globex" in meta and 'class="tag mode mode-hybrid">HYBRID<' in meta
    assert re.search(r'title="3 days since applied">3d ago<', meta)


def test_card_without_work_mode_has_no_tag(env):
    client, conn = env
    make_app(conn, "a")
    assert 'class="tag mode' not in client.get("/applications").text


@pytest.mark.parametrize("applied, text, short", [
    ("2026-11-04T08:00:00-06:00", "0 days since applied", "0d ago"),
    ("2026-11-03T23:00:00-06:00", "1 day since applied", "1d ago"),
])
def test_days_since_applied_uses_the_app_clock_dates(env, applied, text, short):
    client, conn = env
    make_app(conn, "a", applied=applied)
    html = client.get("/applications").text
    assert f'title="{text}">{short}<' in html


def test_card_shows_earliest_upcoming_interview_only(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-03T09:00", "past")                 # before NOW
    add_note(conn, app_id, "2026-11-12T16:30", "later")
    add_note(conn, app_id, "2026-11-10T14:00", "soonest")
    add_note(conn, app_id, "2026-11-05T09:00:00-06:00", "move", kind="move")  # moves never count
    html = client.get("/applications").text.split('id="board"')[1]   # the 30-day history above it lists dates too
    assert re.search(r'class="next-interview"[^>]*>[^<]*2026-11-10 14:00', html)
    assert "2026-11-12" not in html and "2026-11-03" not in html and "2026-11-05" not in html


def test_card_without_upcoming_interview_shows_nothing(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-01T09:00", "past")
    assert "next-interview" not in client.get("/applications").text


def test_interview_exactly_now_counts_as_upcoming(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-04T10:00", "right now")
    assert "2026-11-04 10:00" in client.get("/applications").text


def test_card_text_is_escaped(env):
    client, conn = env
    make_app(conn, "a", title="<script>alert(1)</script>", company="<b>x</b>")
    html = client.get("/applications").text
    assert "<script>alert(1)" not in html and "<b>x</b>" not in html
    assert "&lt;script&gt;" in html


# --- drag and drop markup ---------------------------------------------------------------

def test_phase_columns_are_drop_targets_and_open_cards_draggable(env):
    client, conn = env
    app_id = make_app(conn, "a")
    make_app(conn, "b", outcome="ghosted", title="Gone")
    cols = columns(client.get("/applications").text)
    applied = cols[f"phase-{pid(conn, 'Applied')}"]
    assert f'data-phase-id="{pid(conn, "Applied")}"' in applied
    assert f'draggable="true" data-app-id="{app_id}"' in applied
    assert "data-phase-id" not in cols["closed"]
    assert "draggable" not in cols["closed"]


def test_card_has_a_no_js_phase_form_that_returns_to_the_board(env):
    client, conn = env
    app_id = make_app(conn, "a")
    html = client.get("/applications?closed=0").text
    assert f'action="/applications/{app_id}/move"' in html
    assert 'name="next" value="/applications?closed=0"' in html
    assert 'name="phase_id"' in html


def test_script_wires_native_drag_and_drop_to_the_move_route():
    from pathlib import Path
    js = (Path(__file__).parent.parent / "jobfinder/static/app.js").read_text()
    for needle in ("dragstart", "dragover", "drop", "/move", "phase_id"):
        assert needle in js


def test_board_columns_scroll_horizontally():
    from pathlib import Path
    css = (Path(__file__).parent.parent / "jobfinder/static/style.css").read_text()
    assert re.search(r"\.board\s*\{[^}]*overflow-x:\s*auto", css)


# --- move route ---------------------------------------------------------------------------

def test_move_changes_phase_and_appends_a_move_entry(env):
    client, conn = env
    app_id = make_app(conn, "a")
    r = client.post(f"/applications/{app_id}/move", data={"phase_id": pid(conn, "Technical")})
    assert (r.status_code, r.headers["location"]) == (303, "/applications")
    assert conn.execute("SELECT phase_id FROM applications").fetchone()[0] == pid(conn, "Technical")
    last = conn.execute("SELECT phase_name, kind, happened_at FROM timeline_entries ORDER BY id DESC").fetchone()
    assert (last["phase_name"], last["kind"], last["happened_at"]) == ("Technical", "move", NOW.isoformat())


def test_move_redirects_to_a_safe_next(env):
    client, conn = env
    app_id = make_app(conn, "a")
    data = {"phase_id": pid(conn, "Final")}
    assert client.post(f"/applications/{app_id}/move", data={**data, "next": "/applications/1"}).headers["location"] == "/applications/1"
    assert client.post(f"/applications/{app_id}/move", data={**data, "next": "//evil.example"}).headers["location"] == "/"
    assert client.post(f"/applications/{app_id}/move", data={**data, "next": "https://evil.example"}).headers["location"] == "/"


def test_move_to_the_same_phase_is_a_noop(env):
    client, conn = env
    app_id = make_app(conn, "a")
    before = snapshot(conn)
    r = client.post(f"/applications/{app_id}/move", data={"phase_id": pid(conn, "Applied")})
    assert r.status_code == 303
    assert snapshot(conn) == before


def test_move_unknown_application_is_404(env):
    client, conn = env
    make_app(conn, "a")
    before = snapshot(conn)
    assert client.post("/applications/999/move", data={"phase_id": pid(conn, "Final")}).status_code == 404
    assert snapshot(conn) == before


@pytest.mark.parametrize("phase_id", ["999", "abc", "", "-1"])
def test_move_unknown_or_bad_phase_is_400_and_db_unchanged(env, phase_id):
    client, conn = env
    app_id = make_app(conn, "a")
    before = snapshot(conn)
    assert client.post(f"/applications/{app_id}/move", data={"phase_id": phase_id}).status_code == 400
    assert client.post(f"/applications/{app_id}/move").status_code == 400
    assert snapshot(conn) == before


def test_moved_card_shows_in_its_new_column(env):
    client, conn = env
    app_id = make_app(conn, "a", title="Mover")
    client.post(f"/applications/{app_id}/move", data={"phase_id": pid(conn, "Offer")})
    cols = columns(client.get("/applications").text)
    assert "Mover" in cols[f"phase-{pid(conn, 'Offer')}"]
    assert "Mover" not in cols[f"phase-{pid(conn, 'Applied')}"]


# --- compact layout ------------------------------------------------------------------------

CSS = (Path(__file__).parent.parent / "jobfinder/static/style.css").read_text()


def collapsed_columns(html):
    return {k: c for k, c in columns(html).items() if "column-collapsed" in c.split(">")[0]}


def test_empty_columns_are_collapsed_with_an_accessible_name_and_stay_drop_targets(env):
    client, conn = env
    make_app(conn, "a")
    make_app(conn, "b", phase="Final", title="Closing", outcome="active")
    html = client.get("/applications").text
    cols = columns(html)
    applied, final = cols[f"phase-{pid(conn, 'Applied')}"], cols[f"phase-{pid(conn, 'Final')}"]
    assert "column-collapsed" not in applied.split(">")[0] and 'aria-label="Applied, 1 application"' in applied
    assert "column-collapsed" not in final.split(">")[0]
    technical = cols[f"phase-{pid(conn, 'Technical')}"]
    head = technical.split(">")[0]
    assert "column-collapsed" in head and 'data-empty="true"' in head
    assert 'aria-label="Technical, 0 applications"' in head and 'role="group"' in head
    assert f'data-phase-id="{pid(conn, "Technical")}"' in head                    # still a drop target
    assert re.search(r'<h3 class="column-title"[^>]*><span class="column-name">Technical</span> <span class="count">0</span></h3>', technical)
    assert 'data-empty' not in applied


def test_only_empty_columns_collapse_and_closed_follows_the_same_rule(env):
    client, conn = env
    make_app(conn, "a")
    html = client.get("/applications").text
    assert "closed" in collapsed_columns(html) and "unassigned" not in collapsed_columns(html)
    assert 'aria-label="Closed, 0 applications"' in columns(html)["closed"]
    make_app(conn, "b", outcome="rejected", title="Gone")
    cols = columns(client.get("/applications").text)
    assert "column-collapsed" not in cols["closed"].split(">")[0]
    assert 'aria-label="Closed, 1 application"' in cols["closed"]
    assert "data-phase-id" not in collapsed_columns(html)["closed"]                # a collapsed Closed is no drop target


def test_every_phase_but_the_filled_ones_is_collapsed(env):
    client, conn = env
    make_app(conn, "a", phase="Offer")
    collapsed = collapsed_columns(client.get("/applications").text)
    assert set(collapsed) == {f"phase-{pid(conn, n)}" for n in ("Applied", "Recruiter screen", "Hiring manager", "Technical", "Final")} | {"closed"}


def test_toolbar_shows_active_and_closed_counts_even_when_closed_is_hidden(env):
    client, conn = env
    make_app(conn, "a")
    make_app(conn, "b", phase="Final")
    make_app(conn, "c", outcome="rejected", title="Gone")
    for url in ("/applications", "/applications?closed=0"):
        html = client.get(url).text
        head = html.split('class="board-head"')[1].split('class="board-tools"')[0]
        assert re.search(r'<p class="board-counts[^>]*>2 active · 1 closed</p>', head)
        assert head.index("APPLICATIONS") < head.index("board-counts")
    assert "Hide closed" in client.get("/applications").text and "ADD APPLICATION" in client.get("/applications").text


def test_toolbar_counts_are_zero_on_an_empty_board(env):
    client, _ = env
    assert "0 active · 0 closed" in client.get("/applications").text


def test_only_the_board_page_uses_the_wide_layout(env):
    client, conn = env
    app_id = make_app(conn, "a")
    assert '<body class="wide">' in client.get("/applications").text
    assert '<body class="wide">' in client.get("/applications?closed=0").text
    for url in ("/", "/jobs/1", "/applications/phases", "/applications/new"):
        html = client.get(url).text
        assert "<body>" in html and 'class="wide"' not in html, url
    # the application page (timeline + sidebar) is moderately wider, never the board's width
    assert '<body class="medium">' in client.get(f"/applications/{app_id}").text
    assert re.search(r"body\.medium\s*\{[^}]*max-width:\s*1200px", CSS)
    assert re.search(r"body\.wide\s*\{[^}]*max-width:\s*1600px", CSS)
    assert re.search(r"body\s*\{[^}]*max-width:\s*1100px", CSS)                  # the other pages keep theirs


def test_card_move_select_is_kept_with_a_quiet_move_to_label(env):
    client, conn = env
    app_id = make_app(conn, "a", title="Spring Engineer")
    card = columns(client.get("/applications").text)[f"phase-{pid(conn, 'Applied')}"]
    assert 'class="card-move-label" aria-hidden="true">Move to<' in card
    assert 'aria-label="Move Spring Engineer to phase"' in card and 'name="phase_id"' in card
    assert f'action="/applications/{app_id}/move"' in card


def test_closed_card_shows_its_outcome_and_no_move_control(env):
    client, conn = env
    make_app(conn, "a", outcome="ghosted", title="Gone")
    card = columns(client.get("/applications").text)["closed"]
    assert 'class="card-outcome muted">ghosted<' in card and "card-move" not in card


def test_stylesheet_collapses_empty_columns_and_styles_the_scrollbar():
    assert re.search(r"\.column-collapsed\s*\{[^}]*flex:\s*0 0 48px", CSS)
    assert "writing-mode: vertical-rl" in CSS.split(".column-collapsed .column-title")[1].split("}")[0]
    assert ".board.is-dragging .column-collapsed" in CSS
    board = re.search(r"\.board\s*\{[^}]*\}", CSS).group(0)
    assert "scrollbar-width: thin" in board and "scrollbar-color" in board
    assert re.search(r"\.card-move\s*\{[^}]*max-height:\s*0", CSS) and ".card:focus-within .card-move" in CSS
    assert "(hover: none)" in CSS
    assert "-webkit-line-clamp: 2" in CSS


def test_script_opens_collapsed_columns_while_dragging():
    js = (Path(__file__).parent.parent / "jobfinder/static/app.js").read_text()
    assert "is-dragging" in js
