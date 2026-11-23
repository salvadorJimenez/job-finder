"""Applications: board/list views, filters shared by both, sorting, counts and the board fit."""
import re
import sqlite3
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from jobfinder import applications as apps
from jobfinder.db import connect
from test_board import add_note, columns, env, make_app, pid  # noqa: F401  (env is a fixture)
from test_web import NOW

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "jobfinder/static/style.css").read_text()
JS = (ROOT / "jobfinder/static/app.js").read_text()


def make(conn, ext, title, company, phase, applied, outcome="active", site="linkedin", cv=None, mode=None):
    app_id = make_app(conn, ext, title=title, company=company, phase=phase, applied=applied,
                      outcome=outcome, mode=mode)
    conn.execute("UPDATE jobs SET site = ? WHERE id = (SELECT job_id FROM applications WHERE id = ?)", (site, app_id))
    if cv:
        cv_id = conn.execute("INSERT INTO cv_files (original_name, sha256, stored_path, uploaded_at) VALUES (?, ?, ?, ?)",
                             (cv, f"sha-{cv}", f"{cv}.pdf", "2026-11-01")).lastrowid
        conn.execute("UPDATE applications SET cv_id = ? WHERE id = ?", (cv_id, app_id))
    conn.commit()
    return app_id


@pytest.fixture
def world(env):
    """Five applications: ids 1..5 in the order below. NOW is 2026-11-04 10:00."""
    client, conn = env
    ids = {
        "spring": make(conn, "a", "Spring Engineer", "Acme", "Applied", "2026-11-03T09:00:00-06:00", cv="cv-a.pdf"),
        "rust": make(conn, "b", "Rust Developer", "Globex", "Technical", "2026-10-20T09:00:00-06:00",
                   outcome="rejected", site="indeed"),
        "front": make(conn, "c", "Frontend Dev", "Initech", "Final", "2026-08-01T09:00:00-06:00",
                      site="glassdoor", cv="cv-c.pdf"),
        "back": make(conn, "d", "Backend Dev", "Hooli", None, "2026-10-01T09:00:00-06:00", site="manual"),
        "ops": make(conn, "e", "Ops Engineer", "Umbrella", "Offer", "2026-10-30T09:00:00-06:00",
                    outcome="accepted", cv="cv-e.pdf"),
    }
    add_note(conn, ids["spring"], "2026-11-10T14:00", "Interview with Ana", phase="Applied")
    add_note(conn, ids["rust"], "2026-10-23T09:00", "Rejected after case study", phase="Technical")
    add_note(conn, ids["front"], "2026-11-02T09:00", "Sénior panel done", phase="Final")
    return client, conn, ids


def rows(html):
    """Role titles of the list rows, in order."""
    return [unescape(t) for t in re.findall(r'class="row-title" href="/applications/\d+"[^>]*>([^<]*)<', html)]


def list_titles(client, **params):
    return rows(client.get("/applications", params={"view": "list", **params}).text)


def board_titles(client, **params):
    html = client.get("/applications", params=params).text.split('id="board"')[-1]
    return [unescape(t) for t in re.findall(r'<a class="title" href="/applications/\d+">([^<]*)<', html)]


ALL = ["Spring Engineer", "Ops Engineer", "Rust Developer", "Backend Dev", "Frontend Dev"]   # applied, newest first


def chips(html, name):
    """{value: (count, checked)} of the toggle chips called `name`."""
    found = re.findall(
        rf'<input type="checkbox" name="{name}" value="([^"]*)"( checked)?><span class="chip-name">[^<]*</span>'
        rf'<span class="chip-count">(\d+)</span>', html)
    return {value: (int(count), bool(checked)) for value, checked, count in found}


def query(href):
    return parse_qs(urlparse(unescape(href)).query)


# --- A2: view switch ------------------------------------------------------------------------

def test_board_is_the_default_view_with_a_segmented_switch(world):
    client, _, _ = world
    html = client.get("/applications").text
    assert 'id="board"' in html and 'id="app-list"' not in html
    switch = re.search(r'<nav class="view-switch" aria-label="View">(.*?)</nav>', html, re.S).group(1)
    assert re.search(r'<a [^>]*href="/applications"[^>]*aria-current="true"[^>]*>BOARD</a>', switch)
    assert re.search(r'<a [^>]*href="/applications\?view=list"[^>]*>LIST</a>', switch)
    assert "aria-current" not in switch.split("LIST")[0].split("BOARD")[1]


def test_list_view_shows_a_table_instead_of_the_board_and_marks_list_current(world):
    client, _, _ = world
    html = client.get("/applications?view=list").text
    assert 'id="app-list"' in html and 'id="board"' not in html and 'class="column' not in html
    switch = re.search(r'<nav class="view-switch" aria-label="View">(.*?)</nav>', html, re.S).group(1)
    assert re.search(r'<a [^>]*aria-current="true"[^>]*>LIST</a>', switch)
    assert re.search(r'<a [^>]*href="/applications"[^>]*>BOARD</a>', switch)


@pytest.mark.parametrize("view", ["", "grid", "LIST", "board", "1"])
def test_unknown_view_falls_back_to_the_board(world, view):
    client, _, _ = world
    html = client.get("/applications", params={"view": view}).text
    assert 'id="board"' in html and 'id="app-list"' not in html


def test_switching_views_keeps_every_filter_including_repeated_ones(world):
    client, conn, _ = world
    technical = pid(conn, "Technical")
    html = client.get("/applications", params=[
        ("outcome", "rejected"), ("outcome", "ghosted"), ("phase", str(technical)), ("q", "rust dev"), ("sort", "company")]).text
    switch = re.search(r'<nav class="view-switch" aria-label="View">(.*?)</nav>', html, re.S).group(1)
    href = re.search(r'href="([^"]*)"[^>]*>LIST</a>', switch).group(1)
    assert query(href) == {"view": ["list"], "outcome": ["rejected", "ghosted"], "phase": [str(technical)],
                           "q": ["rust dev"], "sort": ["company"]}
    back = client.get(unescape(href)).text
    href = re.search(r'href="([^"]*)"[^>]*>BOARD</a>', back).group(1)
    assert query(href) == {"outcome": ["rejected", "ghosted"], "phase": [str(technical)], "q": ["rust dev"], "sort": ["company"]}


def test_filter_form_submits_to_the_page_and_carries_the_list_view(world):
    client, _, _ = world
    board = client.get("/applications").text
    lst = client.get("/applications?view=list").text
    for html in (board, lst):
        assert re.search(r'<form class="app-filters" method="get" action="/applications" data-autosubmit', html)
    assert 'name="view"' not in board
    assert '<input type="hidden" name="view" value="list">' in lst


def test_clear_all_shows_only_with_filters_and_keeps_the_view(world):
    client, _, _ = world
    assert "Clear all</a>" not in client.get("/applications").text
    assert "Clear all</a>" not in client.get("/applications?view=list&sort=company").text   # sorting is not a filter
    html = client.get("/applications?q=rust").text
    assert re.search(r'<a class="reset" href="/applications" data-reset-apps>Clear all</a>', html)
    html = client.get("/applications?view=list&q=rust").text
    assert re.search(r'<a class="reset" href="/applications\?view=list" data-reset-apps>Clear all</a>', html)


def test_links_to_the_board_restore_the_saved_view_and_filters(world):
    client, conn, ids = world
    for url in ("/applications", "/applications?view=list", "/", "/applications/phases", "/applications/new",
                f"/applications/{ids['rust']}"):
        html = client.get(url).text
        assert re.search(r'<nav class="main-nav">.*?<a href="/applications" data-keep-apps[^>]*>Applications</a>', html, re.S), url
    for url in ("/applications/phases", "/applications/new", f"/applications/{ids['rust']}"):
        assert re.search(r'<a class="back" href="/applications" data-keep-apps>‹ Back to board</a>', client.get(url).text), url


def test_script_keeps_the_applications_query_per_tab():
    assert "job-finder:apps-query" in JS and "a[data-keep-apps]" in JS
    assert 'pathname === "/applications"' in JS
    assert "a[data-reset-apps]" in JS


def test_script_autosubmit_targets_the_form_action_and_waits_on_chips():
    assert 'form.getAttribute("action")' in JS
    assert 'input[name="outcome"]' in JS and 'input[name="phase"]' in JS      # chips debounce like stack/mode


# --- A3: list rows -----------------------------------------------------------------------------

def test_list_has_one_row_per_application_with_the_documented_columns(world):
    client, _, ids = world
    html = client.get("/applications?view=list").text
    head = re.search(r"<thead>(.*?)</thead>", html, re.S).group(1)
    labels = [re.sub(r"<[^>]+>|[▲▼]", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]
    assert labels == ["Role / company", "Phase", "Outcome", "Applied", "Last activity", "Next interview", "Source", "CV"]
    assert rows(html) == ALL
    assert len(re.findall(r'<tr class="app-row[ "]', html)) == 5


def row(html, app_id):
    return re.search(rf'<tr class="app-row[ "][^>]*data-app-id="{app_id}".*?</tr>', html, re.S).group(0)


def cell(html_row, name):
    return re.search(rf'<td class="col-{name}"[^>]*>(.*?)</td>', html_row, re.S).group(1)


def text(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def test_row_shows_company_role_link_phase_progress_and_applied_age(world):
    client, conn, ids = world
    html = client.get("/applications?view=list").text
    r = row(html, ids["rust"])
    role = cell(r, "role")
    assert f'<a class="row-title" href="/applications/{ids["rust"]}"' in role and text(role) == "Rust Developer Globex"
    assert re.search(r'<span class="row-company muted">Globex</span>', role)                  # company muted, under the role
    phase = cell(r, "phase")
    assert "Technical" in phase
    assert 'role="img" aria-label="Phase 4 of 6"' in phase                          # not colour-only: text + position
    assert phase.count('class="seg on"') == 4 and phase.count('<i class="seg"') == 2
    applied = cell(r, "applied")
    assert text(applied) == "15d ago" and '<time datetime="2026-10-20" title="2026-10-20 · 15 days ago">' in applied
    assert "Unassigned" in cell(row(html, ids["back"]), "phase") and "role=\"img\"" not in cell(row(html, ids["back"]), "phase")


@pytest.mark.parametrize("key, outcome", [("spring", "active"), ("rust", "rejected"), ("ops", "accepted")])
def test_outcome_badge_is_text_and_has_its_own_style_class(world, key, outcome):
    client, _, ids = world
    badge = cell(row(client.get("/applications?view=list").text, ids[key]), "outcome")
    assert re.search(rf'<span class="outcome outcome-{outcome}">[^<]*{outcome}</span>', badge)


def test_all_outcomes_have_distinct_styles_and_glyphs():
    for outcome in apps.OUTCOMES:
        assert f".outcome-{outcome}" in CSS


def test_last_activity_is_the_latest_entry_not_in_the_future_and_next_interview_is_the_earliest_upcoming_note(world):
    client, conn, ids = world
    html = client.get("/applications?view=list").text
    spring = row(html, ids["spring"])
    assert "→ Applied" in text(cell(spring, "activity")) and "Nov 3" in text(cell(spring, "activity"))
    assert 'title="2026-11-03"' in cell(spring, "activity")
    assert "Interview with Ana" not in cell(spring, "activity")                       # future: not activity yet
    nxt = cell(spring, "next")
    assert 'class="next-interview"' in nxt and 'title="2026-11-10 14:00"' in nxt and text(nxt) == "Tue Nov 10 · 14:00"
    assert "—" in cell(row(html, ids["rust"]), "next") and "next-interview" not in cell(row(html, ids["rust"]), "next")
    front = text(cell(row(html, ids["front"]), "activity"))
    assert "Nov 2" in front and "Sénior panel done" in front


def test_last_activity_shows_the_first_line_of_a_note_and_escapes_it(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-03T09:00", "<b>Call</b> went well\nsecond line")
    html = client.get("/applications?view=list").text
    cellhtml = cell(row(html, app_id), "activity")
    assert "&lt;b&gt;Call&lt;/b&gt; went well" in cellhtml and "second line" not in cellhtml and "<b>" not in cellhtml


def test_application_without_entries_has_no_last_activity(env):
    client, conn = env
    app_id = make_app(conn, "a")
    conn.execute("DELETE FROM timeline_entries"); conn.commit()
    assert text(cell(row(client.get("/applications?view=list").text, app_id), "activity")) == "—"


def test_source_and_cv_columns(world):
    client, _, ids = world
    html = client.get("/applications?view=list").text
    assert '<span class="tag src">indeed</span>' in cell(row(html, ids["rust"]), "source")
    assert text(cell(row(html, ids["back"]), "source")) == "manual"
    has = cell(row(html, ids["spring"]), "cv")
    assert '<span class="cv-yes" title="cv-a.pdf">' in has and "<svg" in has and text(has) == "cv-a.pdf"   # an icon, the name for readers
    none = cell(row(html, ids["rust"]), "cv")
    assert text(none) == "—" and "No CV attached" in none


def test_list_includes_closed_applications_in_the_same_table(world):
    client, _, _ = world
    assert "Rust Developer" in rows(client.get("/applications?view=list").text)


def test_list_text_is_escaped(env):
    client, conn = env
    make_app(conn, "a", title="<script>alert(1)</script>", company="<i>x</i>")
    html = client.get("/applications?view=list").text
    assert "<script>alert(1)" not in html and "<i>x</i>" not in html


def test_list_header_links_set_the_sort_and_mark_the_current_one(world):
    client, conn, _ = world
    html = client.get("/applications", params=[("view", "list"), ("outcome", "rejected"), ("outcome", "ghosted")]).text
    th = lambda label: re.search(rf'<th[^>]*>\s*<a href="([^"]*)"[^>]*>{label}[^<]*</a>', html).group(1)
    assert query(th("Role / company")) == {"view": ["list"], "outcome": ["rejected", "ghosted"], "sort": ["company"]}
    assert query(th("Applied"))["sort"] == ["oldest"]                                 # newest is the default: click flips
    assert query(th("Last activity"))["sort"] == ["activity"]
    assert query(th("Next interview"))["sort"] == ["interview"]
    assert query(th("Phase"))["sort"] == ["phase"]
    assert re.search(r'<th[^>]*aria-sort="descending"[^>]*>\s*<a[^>]*>Applied', html)
    asc = client.get("/applications?view=list&sort=oldest").text
    assert re.search(r'<th[^>]*aria-sort="ascending"[^>]*>\s*<a[^>]*>Applied', asc)
    assert "sort" not in query(re.search(r'<th[^>]*>\s*<a href="([^"]*)"[^>]*>Applied', asc).group(1))
    assert re.search(r'<th[^>]*aria-sort="ascending"[^>]*>\s*<a[^>]*>Role / company', client.get("/applications?view=list&sort=company").text)


def test_count_line_says_how_many_of_all_applications_are_shown(world):
    client, _, _ = world
    for view in ("board", "list"):
        assert '<p class="count muted" id="app-count">5 of 5 applications</p>' in client.get("/applications", params={"view": view}).text
        assert '>1 of 5 applications</p>' in client.get("/applications", params={"view": view, "outcome": "active", "site": "linkedin", "cv": "attached"}).text
        assert '>1 of 5 applications</p>' in client.get("/applications", params={"view": view, "q": "spring"}).text


def test_count_line_is_singular_for_one_application(env):
    client, conn = env
    make_app(conn, "a")
    assert '>1 of 1 application</p>' in client.get("/applications?view=list").text


def test_header_counts_are_not_affected_by_filters(world):
    client, _, _ = world
    for url in ("/applications?q=spring", "/applications?view=list&outcome=rejected", "/applications?outcome=active"):
        assert re.search(r'<p class="board-counts[^>]*>3 active · 2 closed</p>', client.get(url).text), url


def test_no_match_message_with_a_way_out(world):
    client, _, _ = world
    for view in ("board", "list"):
        html = client.get("/applications", params={"view": view, "q": "zzz-nothing"}).text
        assert "No applications match these filters" in html
        assert re.search(r'href="/applications(\?view=list)?" data-reset-apps>Clear all filters</a>', html)
        assert 'id="board"' not in html and "app-row" not in html
        assert "0 of 5 applications" in html


# --- A4: filters ----------------------------------------------------------------------------------

@pytest.mark.parametrize("q, expected", [
    ("spring", ["Spring Engineer"]),                   # role
    ("ACME", ["Spring Engineer"]),                    # company, case-insensitive
    ("  rust  ", ["Rust Developer"]),                    # trimmed
    ("case study", ["Rust Developer"]),                 # timeline notes
    ("interview with ana", ["Spring Engineer"]),      # even a future note
    ("SÉNIOR", ["Frontend Dev"]),                    # case-insensitive beyond ASCII
    ("sénior", ["Frontend Dev"]),
    ("Applied", []),                                 # automatic move entries are not notes
    ("%", []),                                       # wildcards are literal
    ("_", []),
    ("dev", ["Rust Developer", "Backend Dev", "Frontend Dev"]),
])
def test_search_matches_company_role_and_notes(world, q, expected):
    client, _, _ = world
    assert list_titles(client, q=q) == expected
    assert sorted(board_titles(client, q=q)) == sorted(expected)


def test_blank_search_means_no_search(world):
    client, _, _ = world
    assert list_titles(client, q="   ") == ALL


def test_outcome_chips_combine_with_or_and_ignore_unknown_values(world):
    client, _, _ = world
    assert list_titles(client, outcome="rejected") == ["Rust Developer"]
    assert list_titles(client, outcome=["rejected", "accepted"]) == ["Ops Engineer", "Rust Developer"]
    assert list_titles(client, outcome="active") == ["Spring Engineer", "Backend Dev", "Frontend Dev"]
    assert list_titles(client, outcome="bogus") == ALL
    assert list_titles(client, outcome=["bogus", "rejected"]) == ["Rust Developer"]
    assert list_titles(client, outcome="REJECTED") == ALL                              # exact values only


def test_legacy_closed_zero_is_the_hide_closed_shortcut(world):
    client, _, _ = world
    assert list_titles(client, closed="0") == ["Spring Engineer", "Backend Dev", "Frontend Dev"]
    assert list_titles(client, closed="0", outcome="rejected") == ["Rust Developer"]     # an explicit choice wins
    assert list_titles(client, closed="1") == ALL


def test_hide_closed_shortcut_sets_outcome_active_and_show_closed_removes_it(world):
    client, conn, _ = world
    html = client.get("/applications", params={"view": "list", "q": "dev"}).text
    href = re.search(r'<a class="muted tool-link" href="([^"]*)">Hide closed</a>', html).group(1)
    assert query(href) == {"view": ["list"], "q": ["dev"], "outcome": ["active"]}
    html = client.get(unescape(href)).text
    href = re.search(r'<a class="muted tool-link" href="([^"]*)">Show closed</a>', html).group(1)
    assert query(href) == {"view": ["list"], "q": ["dev"]}
    both = client.get("/applications?outcome=active&outcome=rejected").text
    assert "Hide closed" in both                                                       # closed outcomes are still shown


def test_phase_chips_combine_with_or_and_include_unassigned(world):
    client, conn, _ = world
    tech, final = pid(conn, "Technical"), pid(conn, "Final")
    assert list_titles(client, phase=str(tech)) == ["Rust Developer"]
    assert list_titles(client, phase=[str(tech), str(final)]) == ["Rust Developer", "Frontend Dev"]
    assert list_titles(client, phase="unassigned") == ["Backend Dev"]
    assert list_titles(client, phase=[str(tech), "unassigned"]) == ["Rust Developer", "Backend Dev"]
    for junk in ("999", "abc", "-1", "", "1.5"):
        assert list_titles(client, phase=junk) == ALL, junk
    assert list_titles(client, phase=["abc", str(tech)]) == ["Rust Developer"]


def test_source_filter_uses_sites_present_and_ignores_unknown_ones(world):
    client, _, _ = world
    assert list_titles(client, site="indeed") == ["Rust Developer"]
    assert list_titles(client, site="linkedin") == ["Spring Engineer", "Ops Engineer"]
    assert list_titles(client, site="manual") == ["Backend Dev"]
    assert list_titles(client, site="bogus") == ALL
    html = client.get("/applications?view=list").text
    select = re.search(r'<select name="site">(.*?)</select>', html, re.S).group(1)
    assert re.findall(r'<option value="([^"]*)"', select) == ["", "glassdoor", "indeed", "linkedin", "manual"]
    assert re.search(r'<option value="indeed" selected>', client.get("/applications?site=indeed").text)


def test_cv_filter(world):
    client, _, _ = world
    assert list_titles(client, cv="attached") == ["Spring Engineer", "Ops Engineer", "Frontend Dev"]
    assert list_titles(client, cv="missing") == ["Rust Developer", "Backend Dev"]
    assert list_titles(client, cv="any") == ALL and list_titles(client, cv="zzz") == ALL


def test_upcoming_interview_only(world):
    client, _, _ = world
    assert list_titles(client, interview="1") == ["Spring Engineer"]
    assert 'name="interview" value="1" checked' in client.get("/applications?interview=1").text
    assert 'name="interview" value="1" checked' not in client.get("/applications").text
    assert list_titles(client, interview="") == ALL


def test_upcoming_interview_filter_ignores_past_notes_and_moves(env):
    client, conn = env
    a = make_app(conn, "a", title="Past")
    add_note(conn, a, "2026-11-03T09:00", "done")
    b = make_app(conn, "b", title="Now")
    add_note(conn, b, "2026-11-04T10:00", "right now")
    c = make_app(conn, "c", title="Move")
    add_note(conn, c, "2026-12-01T09:00-06:00", "later move", kind="move")
    assert list_titles(client, interview="1") == ["Now"]


@pytest.mark.parametrize("within, expected", [
    ("7", ["Spring Engineer", "Ops Engineer"]),
    ("30", ["Spring Engineer", "Ops Engineer", "Rust Developer"]),
    ("90", ["Spring Engineer", "Ops Engineer", "Rust Developer", "Backend Dev"]),
])
def test_applied_within_days(world, within, expected):
    client, _, _ = world
    assert list_titles(client, within=within) == expected


@pytest.mark.parametrize("junk", ["5", "abc", "-7", "0", ""])
def test_applied_within_ignores_other_values(world, junk):
    client, _, _ = world
    assert list_titles(client, within=junk) == ALL


def test_applied_within_counts_local_days_like_the_ago_label(env):
    client, conn = env
    make_app(conn, "a", title="Seven", applied="2026-10-28T00:00:00-06:00")           # 7d ago
    make_app(conn, "b", title="Eight", applied="2026-10-27T23:59:00-06:00")           # 8d ago
    assert list_titles(client, within="7") == ["Seven"]


def test_applied_date_range_is_inclusive_and_ignores_invalid_dates(world):
    client, _, _ = world
    assert list_titles(client, **{"from": "2026-10-20", "to": "2026-10-30"}) == ["Ops Engineer", "Rust Developer"]
    assert list_titles(client, **{"from": "2026-10-30"}) == ["Spring Engineer", "Ops Engineer"]
    assert list_titles(client, to="2026-09-30") == ["Frontend Dev"]
    assert list_titles(client, **{"from": "nope", "to": "2026-13-45"}) == ALL
    assert list_titles(client, **{"from": "2026-10-30", "to": "2026-10-20"}) == []     # reversed: honestly empty
    html = client.get("/applications?from=2026-10-30").text
    assert re.search(r'<input type="date" name="from" value="2026-10-30"', html)
    assert re.search(r'<input type="date" name="to"(?! value)', html)


def test_filters_combine_with_and_across_groups(world):
    client, conn, _ = world
    tech = str(pid(conn, "Technical"))
    assert list_titles(client, outcome="rejected", phase=tech) == ["Rust Developer"]
    assert list_titles(client, outcome="active", phase=tech) == []
    assert list_titles(client, outcome=["active", "accepted"], cv="attached", site="linkedin") == ["Spring Engineer", "Ops Engineer"]
    assert list_titles(client, q="dev", outcome="rejected", within="30") == ["Rust Developer"]
    assert list_titles(client, q="dev", within="7") == []


def test_every_filter_is_validated_independently_of_the_rest(world):
    client, _, _ = world
    junk = {"outcome": "x", "phase": "y", "site": "z", "cv": "w", "within": "q", "from": "a", "to": "b", "sort": "n", "view": "v"}
    assert sorted(board_titles(client, **junk)) == sorted(ALL)


def test_board_applies_the_same_filters_and_counts_only_matches(world):
    client, conn, _ = world
    assert board_titles(client, outcome="rejected") == ["Rust Developer"]
    cols = columns(client.get("/applications?outcome=rejected").text)
    assert "Rust Developer" in cols["closed"]
    assert 'aria-label="Closed, 1 application"' in cols["closed"]
    assert 'aria-label="Applied, 0 applications"' in cols[f"phase-{pid(conn, 'Applied')}"]
    assert "column-collapsed" in cols[f"phase-{pid(conn, 'Applied')}"].split(">")[0]
    cols = columns(client.get("/applications", params={"q": "spring"}).text)
    assert 'aria-label="Applied, 1 application"' in cols[f"phase-{pid(conn, 'Applied')}"]
    assert 'aria-label="Closed, 0 applications"' in cols["closed"]
    assert "Spring Engineer" in cols[f"phase-{pid(conn, 'Applied')}"]


def test_board_closed_column_follows_the_outcome_filter(world):
    client, _, _ = world
    assert "closed" not in columns(client.get("/applications?outcome=active").text)
    assert "closed" in columns(client.get("/applications?outcome=active&outcome=ghosted").text)
    assert "closed" in columns(client.get("/applications?outcome=rejected").text)
    assert "closed" in columns(client.get("/applications").text)


def test_board_unassigned_column_follows_the_filter(world):
    client, _, _ = world
    assert "unassigned" in columns(client.get("/applications").text)
    assert "unassigned" not in columns(client.get("/applications?q=spring").text)
    assert "unassigned" in columns(client.get("/applications?phase=unassigned").text)


def test_outcome_chips_show_state_and_counts_under_the_other_filters(world):
    client, conn, _ = world
    html = client.get("/applications").text
    assert chips(html, "outcome") == {"active": (3, False), "accepted": (1, False), "rejected": (1, False),
                                      "withdrawn": (0, False), "ghosted": (0, False)}
    sel = chips(client.get("/applications", params={"outcome": ["rejected", "bogus"]}).text, "outcome")
    assert sel["rejected"] == (1, True) and sel["active"] == (3, False) and "bogus" not in sel   # own group is not applied to its counts
    under = chips(client.get("/applications", params={"phase": str(pid(conn, "Technical"))}).text, "outcome")
    assert under["rejected"] == (1, False) and under["active"] == (0, False)
    assert 'class="toggle chip chip-empty"' in client.get("/applications").text


def test_phase_chips_follow_phase_order_include_unassigned_only_when_used(world):
    client, conn, _ = world
    html = client.get("/applications").text
    got = chips(html, "phase")
    ids = [str(pid(conn, n)) for n in ("Applied", "Recruiter screen", "Hiring manager", "Technical", "Final", "Offer")]
    assert list(got) == [*ids, "unassigned"]
    assert got[ids[0]] == (1, False) and got[ids[3]] == (1, False) and got[ids[1]] == (0, False) and got["unassigned"] == (1, False)
    assert chips(client.get("/applications", params={"phase": [ids[3], "unassigned"]}).text, "phase")[ids[3]] == (1, True)
    conn.execute("UPDATE applications SET phase_id = ? WHERE phase_id IS NULL", (pid(conn, "Applied"),)); conn.commit()
    assert "unassigned" not in chips(client.get("/applications").text, "phase")
    assert "unassigned" in chips(client.get("/applications?phase=unassigned").text, "phase")   # a selected chip never vanishes


def test_phase_chip_counts_respect_the_other_filters(world):
    client, conn, _ = world
    got = chips(client.get("/applications", params={"outcome": "rejected"}).text, "phase")
    assert got[str(pid(conn, "Technical"))] == (1, False) and got[str(pid(conn, "Applied"))] == (0, False)


def test_filter_form_labels_and_controls(world):
    client, _, _ = world
    html = client.get("/applications?view=list").text
    form = re.search(r'<form class="app-filters".*?</form>', html, re.S).group(0)
    for label in ("Outcome", "Phase", "Source", "Date", "More"):
        assert re.search(rf'<summary[^>]*>\s*{label}\s*(<|$)', form), label
    for label in ("CV", "From", "To", "Sort"):
        assert re.search(rf'(field-label|legend)[^>]*>{label}<', form), label
    assert re.search(r'<input type="search" name="q"', form)
    assert re.search(r'<label[^>]*><input type="checkbox" name="interview" value="1"[^>]*> upcoming interview only</label>', form)
    assert re.findall(r'<input type="radio" name="within" value="([^"]*)"', form) == ["", "7", "30", "90"]
    assert re.findall(r'<option value="([^"]*)"', re.search(r'<select name="cv"[^>]*>(.*?)</select>', form, re.S).group(1)) == ["", "attached", "missing"]
    assert "<noscript>" in form


# --- A5: sorting ----------------------------------------------------------------------------------

SORTS = {
    "newest": ["Spring Engineer", "Ops Engineer", "Rust Developer", "Backend Dev", "Frontend Dev"],
    "oldest": ["Frontend Dev", "Backend Dev", "Rust Developer", "Ops Engineer", "Spring Engineer"],
    "activity": ["Spring Engineer", "Frontend Dev", "Ops Engineer", "Rust Developer", "Backend Dev"],
    "interview": ["Spring Engineer", "Ops Engineer", "Rust Developer", "Backend Dev", "Frontend Dev"],   # none last, newest first
    "company": ["Spring Engineer", "Rust Developer", "Backend Dev", "Frontend Dev", "Ops Engineer"],      # Acme Globex Hooli Initech Umbrella
    "phase": ["Ops Engineer", "Frontend Dev", "Rust Developer", "Spring Engineer", "Backend Dev"],       # Offer Final Technical Applied none
}


@pytest.mark.parametrize("sort", SORTS)
def test_list_sorts(world, sort):
    client, _, _ = world
    assert list_titles(client, sort=sort) == SORTS[sort]


@pytest.mark.parametrize("sort", ["", "bogus", "NEWEST", "applied", "company desc"])
def test_unknown_sort_is_the_default(world, sort):
    client, _, _ = world
    assert list_titles(client, sort=sort) == SORTS["newest"]
    assert re.search(r'<option value="newest" selected>', client.get("/applications", params={"sort": sort}).text)


def test_default_sort_is_applied_newest(world):
    client, _, _ = world
    assert list_titles(client) == SORTS["newest"]


@pytest.mark.parametrize("sort", ["oldest", "company", "activity", "interview"])
def test_board_sorts_the_cards_inside_each_column(world, sort):
    client, conn, _ = world
    make(conn, "z", "Second Applied", "Aardvark", "Applied", "2026-10-31T09:00:00-06:00")
    html = client.get("/applications", params={"sort": sort}).text
    applied = columns(html)[f"phase-{pid(conn, 'Applied')}"]
    got = [unescape(t) for t in re.findall(r'<a class="title" href="/applications/\d+">([^<]*)<', applied)]
    expected = {"oldest": ["Second Applied", "Spring Engineer"], "company": ["Second Applied", "Spring Engineer"],
                "activity": ["Spring Engineer", "Second Applied"], "interview": ["Spring Engineer", "Second Applied"]}[sort]
    assert got == expected


def test_sort_select_lists_every_order(world):
    client, _, _ = world
    select = re.search(r'<select name="sort">(.*?)</select>', client.get("/applications").text, re.S).group(1)
    assert re.findall(r'<option value="([^"]*)"', select) == list(SORTS)


def test_sort_ties_are_broken_deterministically(env):
    client, conn = env
    for ext in "abc":
        make_app(conn, ext, title=f"T-{ext}", company="Same")
    for sort in SORTS:
        assert list_titles(client, sort=sort) == ["T-c", "T-b", "T-a"], sort       # same key: newest id first


def test_company_sort_puts_unknown_companies_last_and_ignores_case(env):
    client, conn = env
    make_app(conn, "a", title="B", company="beta")
    make_app(conn, "b", title="A", company="Alpha")
    make_app(conn, "c", title="N", company="")
    conn.execute("UPDATE jobs SET company = NULL WHERE title = 'N'"); conn.commit()
    assert list_titles(client, sort="company") == ["A", "B", "N"]


# --- A6: efficiency ----------------------------------------------------------------------------------

def test_filtering_and_sorting_run_in_sql_with_a_constant_number_of_statements(env):
    client, conn = env
    for i in range(40):
        a = make_app(conn, f"j{i}", title=f"T{i}", company=f"C{i}", phase="Applied" if i % 2 else "Technical")
        add_note(conn, a, f"2026-11-{5 + i % 20:02d}T10:00", f"note {i}")
    statements = []
    probe = connect(conn.execute("PRAGMA database_list").fetchone()[2])
    probe.set_trace_callback(statements.append)
    phases = apps.list_phases(probe)
    filters = apps.parse_filters({"q": ["note"], "outcome": ["active"], "sort": ["interview"]},
                                 phase_ids={p["id"] for p in phases}, sites={"linkedin"})
    statements.clear()
    found = apps.find_applications(probe, NOW, filters)
    assert len(found) == 40
    assert len(statements) <= 2, statements
    assert found[0]["next_interview"] == "2026-11-05 10:00"


def test_timeline_index_exists_and_is_added_to_old_databases(tmp_path):
    path = tmp_path / "old.db"
    conn = connect(path)
    cols = lambda: [[c[2] for c in conn.execute(f"PRAGMA index_info({i[1]})")] for i in conn.execute("PRAGMA index_list(timeline_entries)")]
    assert ["application_id", "happened_at"] in cols()
    conn.execute("DROP INDEX timeline_application_when"); conn.commit(); conn.close()
    assert ["application_id", "happened_at"] in [[c[2] for c in conn2.execute(f"PRAGMA index_info({i[1]})")]
                                                for conn2 in [connect(path)] for i in conn2.execute("PRAGMA index_list(timeline_entries)")]


def test_mixed_timestamp_formats_sort_and_filter_by_local_time(env):
    """Notes are naive local times, moves and applied dates carry an offset; all compare in local time."""
    client, conn = env
    a = make_app(conn, "a", title="A", applied="2026-11-03T23:30:00-06:00")
    b = make_app(conn, "b", title="B", applied="2026-11-04T05:30:00+00:00")         # 2026-11-03 23:30 local
    c = make_app(conn, "c", title="C", applied="2026-11-04T06:30:00+00:00")         # 2026-11-04 00:30 local
    assert list_titles(client, sort="newest") == ["C", "B", "A"]
    assert list_titles(client, **{"from": "2026-11-04"}) == ["C"]
    assert list_titles(client, to="2026-11-03") == ["B", "A"]
    html = client.get("/applications?view=list").text
    assert 'datetime="2026-11-04"' in cell(row(html, c), "applied") and 'datetime="2026-11-03"' in cell(row(html, b), "applied")


# --- A1: board fit -----------------------------------------------------------------------------------

def rule(selector, css=CSS):
    m = re.search(rf"(?<![\w.-]){re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert m, selector
    return m.group(1)


def px(declarations, prop):
    return int(re.search(rf"(?<![\w-]){prop}:\s*(\d+)px", declarations).group(1))


def test_populated_columns_shrink_to_fit_eight_columns_at_1280():
    col = rule(".column")
    assert px(col, "min-width") <= 160 and re.search(r"flex:\s*1 1 \d+px", col)
    gaps = px(rule(".board"), "gap")
    assert 8 * px(col, "min-width") + 7 * gaps <= 1280 - 2 * 16 - 15              # viewport minus page padding and scrollbar
    assert px(col, "max-width") >= 240                                              # still grows when there is room


def test_board_still_scrolls_sideways_below_the_minimum_and_keeps_a_dark_thin_scrollbar():
    board = rule(".board")
    assert re.search(r"overflow-x:\s*auto", board) and "scrollbar-width: thin" in board and "scrollbar-color: #333 #000" in board


def test_cards_get_compact_in_narrow_columns_with_container_queries():
    assert re.search(r"\.column\s*\{[^}]*container-type:\s*inline-size", CSS)
    m = re.search(r"@container[^{]*\(max-width:\s*(\d+)px\)\s*\{(.*?)\n\}", CSS, re.S)
    assert m and int(m.group(1)) >= 160
    narrow = m.group(2)
    assert "-webkit-line-clamp: 2" in narrow and "flex-wrap" in narrow or "white-space: normal" in narrow
    assert ".tag" in narrow and "font-size" in narrow


def test_long_columns_scroll_on_their_own_instead_of_growing_the_page():
    cards = rule(".cards")
    assert re.search(r"overflow-y:\s*auto", cards) and re.search(r"max-height:[^;]*(vh|dvh)", cards)
    assert "scrollbar-width: thin" in cards and "scrollbar-color: #333 #000" in cards
    assert re.search(r"\.column\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column", CSS)


def test_collapsed_columns_keep_their_strip_and_stay_drop_targets_with_scrolling_cards():
    assert re.search(r"\.column-collapsed\s*\{[^}]*flex:\s*0 0 48px", CSS)
    assert ".column-collapsed .cards { display: none; }" in CSS
    assert ".column-collapsed.drop-target .cards { display: flex; }" in CSS


# --- list styling hooks ---------------------------------------------------------------------------------

def test_list_header_is_sticky_and_rows_are_minimal_lines():
    assert re.search(r"#app-list thead th\s*\{[^}]*position:\s*sticky[^}]*top:\s*0", CSS)
    assert re.search(r"#app-list td\s*\{[^}]*border-bottom:\s*1px solid var\(--line\)", CSS)
    section = CSS.split("/* applications list:")[1].split("/* end applications list */")[0]
    assert "nth-child" not in section and "nth-of-type" not in section              # no zebra


def test_list_rows_become_compact_cards_on_narrow_screens():
    narrow = re.search(r"@media \(max-width: (\d+)px\)\s*\{[^@]*#app-list tr\s*\{", CSS)
    assert narrow and int(narrow.group(1)) >= 700
    assert "attr(data-label)" in CSS


def test_list_cells_carry_labels_for_the_compact_card_layout(world):
    client, _, ids = world
    r = row(client.get("/applications?view=list").text, ids["rust"])
    labels = re.findall(r'<td class="col-\w+" data-label="([^"]+)"', r)
    assert labels == ["Role", "Phase", "Outcome", "Applied", "Last activity", "Next interview", "Source", "CV"]


# --- list polish: hierarchy, whole-row link, sort indicators, closed rows --------------------------------------

def test_the_role_link_is_the_one_link_per_row_and_stretches_over_the_whole_row(world):
    client, _, ids = world
    html = client.get("/applications?view=list").text
    r = row(html, ids["rust"])
    assert r.count("<a ") == 1                                                       # one tab stop per row
    assert re.search(r"\.app-row\s*\{[^}]*position:\s*relative", CSS)
    stretch = re.search(r"\.row-title::after\s*\{([^}]*)\}", CSS).group(1)
    assert 'content: ""' in stretch and "position: absolute" in stretch and "inset: 0" in stretch
    assert re.search(r"\.app-row:hover td[^{]*\{[^}]*background", CSS) and ".app-row:focus-within" in CSS


@pytest.mark.parametrize("key, closed", [("spring", False), ("rust", True), ("ops", False), ("front", False)])
def test_closed_rows_are_de_emphasised_but_accepted_ones_are_not(world, key, closed):
    client, _, ids = world
    r = row(client.get("/applications?view=list").text, ids[key])
    assert ("is-closed" in r.split(">")[0]) is closed


@pytest.mark.parametrize("outcome", ["rejected", "withdrawn", "ghosted"])
def test_every_losing_outcome_counts_as_closed_in_the_list(env, outcome):
    client, conn = env
    app_id = make_app(conn, "a", outcome=outcome)
    assert "is-closed" in row(client.get("/applications?view=list").text, app_id).split(">")[0]
    assert "is-closed" in CSS and re.search(r"\.is-closed[^{]*\{[^}]*color:\s*var\(--muted\)", CSS)


@pytest.mark.parametrize("applied, shown", [
    ("2026-11-04T08:00:00-06:00", "today"), ("2026-11-03T08:00:00-06:00", "1d ago"),
    ("2026-09-06T08:00:00-06:00", "59d ago"), ("2026-09-05T08:00:00-06:00", "2mo ago"), ("2026-02-04T08:00:00-06:00", "9mo ago"),
])
def test_applied_is_relative_with_the_exact_date_in_the_tooltip(env, applied, shown):
    client, conn = env
    app_id = make_app(conn, "a", applied=applied)
    cellhtml = cell(row(client.get("/applications?view=list").text, app_id), "applied")
    assert text(cellhtml) == shown and f'datetime="{applied[:10]}"' in cellhtml and f'title="{applied[:10]} · ' in cellhtml


def test_the_next_interview_shows_the_weekday_and_the_time_and_is_amber(world):
    client, _, ids = world
    nxt = cell(row(client.get("/applications?view=list").text, ids["spring"]), "next")
    assert text(nxt) == "Tue Nov 10 · 14:00"
    assert re.search(r"#app-list \.next-interview\s*\{[^}]*var\(--warn\)", CSS)


def test_sortable_headers_show_an_indicator_and_only_the_current_one_has_aria_sort(world):
    client, _, _ = world
    html = client.get("/applications?view=list&sort=activity").text
    assert re.findall(r'<th[^>]*aria-sort="(\w+)"', html) == ["descending"]
    assert re.search(r'<th[^>]*aria-sort="descending"[^>]*>\s*<a[^>]*>Last activity', html)
    assert 'th[aria-sort="descending"] a::after' in CSS and 'th[aria-sort="ascending"] a::after' in CSS
    assert re.search(r"#app-list thead th:not\(\[aria-sort\]\) a::after\s*\{[^}]*content", CSS)       # the quiet "↕" on the others
    for col in ("role", "phase", "applied", "activity", "next"):
        assert re.search(rf'<th class="col-{col}"[^>]*>\s*<a ', html)
    assert not re.search(r'<th class="col-(outcome|source|cv)"[^>]*>\s*<a ', html)


def test_list_header_stays_sticky_and_phase_keeps_its_stepper(world):
    client, _, ids = world
    assert re.search(r"#app-list thead th\s*\{[^}]*position:\s*sticky", CSS)
    assert 'role="img" aria-label="Phase 4 of 6"' in cell(row(client.get("/applications?view=list").text, ids["rust"]), "phase")
