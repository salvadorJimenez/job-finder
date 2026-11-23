"""D3 — split view on the home list: the job list on the left, the selected job's description and
actions in a sticky pane on the right (LinkedIn style)."""
import re
from dataclasses import replace
from datetime import timedelta
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from jobfinder.db import connect
from jobfinder.web import create_app
from test_web import NOW, add_job, add_run, make_cfg

ROOT = Path(__file__).resolve().parent.parent / "jobfinder"
APP_JS = (ROOT / "static" / "app.js").read_text()
CSS = (ROOT / "static" / "style.css").read_text()
SEEN = NOW - timedelta(days=1)


@pytest.fixture
def env(tmp_path):
    cfg = replace(make_cfg(tmp_path), stack=["Kotlin", "Spring"])
    conn = connect(cfg.database)
    run = add_run(conn, SEEN)
    conn.commit()
    return TestClient(create_app(cfg, clock=lambda: NOW)), conn, run


def job(env, ext="1", minutes=0, **kw):
    client, conn, run = env
    job_id = add_job(conn, run, NOW - timedelta(minutes=minutes), ext, **kw)
    conn.commit()
    return job_id


def status_of(env, job_id):
    return env[1].execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]


def pane_of(html):
    start = html.index('id="job-pane"')
    return html[start:html.index("</aside>", start)]


def nexts(html):
    return [unescape(v) for v in re.findall(r'name="next" value="([^"]*)"', html)]


# --- the pane fragment ----------------------------------------------------------------------

def test_pane_fragment_is_only_the_job_view_not_a_page(env):
    jid = job(env, title="Senior Spring Engineer", company="Acme Corp", location="CDMX", work_mode="hybrid",
              stack="Kotlin, Rust", salary_min=50000, salary_max=60000, salary_currency="MXN", salary_interval="monthly",
              salary_basis="net", date_posted="2026-11-02", description="**Requisitos:**\n* Python\n* SQL\n")
    r = env[0].get(f"/jobs/{jid}", params={"pane": "1"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "<html" not in html and "<!doctype" not in html.lower() and "<script" not in html and "<header class=\"top\"" not in html
    for text in ("Senior Spring Engineer", "Acme Corp", "CDMX", "HYBRID", "MXN 50,000–60,000 / month", "net",
                 "2026-11-02", "About the job"):
        assert text in html
    assert "<h3>Requisitos</h3>" in html and "<li>Python</li>" in html
    assert r.headers["cache-control"] == "no-store"


def test_pane_fragment_marks_a_new_job_seen_and_leaves_other_statuses(env):
    new, applied = job(env, "1"), job(env, "2", status="applied")
    assert env[0].get(f"/jobs/{new}", params={"pane": "1"}).status_code == 200
    assert env[0].get(f"/jobs/{applied}", params={"pane": "1"}).status_code == 200
    assert status_of(env, new) == "seen" and status_of(env, applied) == "applied"


def test_pane_fragment_for_unknown_job_is_404_and_changes_nothing(env):
    jid = job(env)
    r = env[0].get("/jobs/999", params={"pane": "1"})
    assert r.status_code == 404 and "<html" not in r.text
    assert status_of(env, jid) == "new"


def test_pane_fragment_carries_the_status_for_the_list_to_update(env):
    jid = job(env)
    html = env[0].get(f"/jobs/{jid}", params={"pane": "1"}).text
    assert re.search(rf'<div class="job-view status-seen" data-job-id="{jid}" data-status="seen"', html)


def test_pane_forms_return_to_the_list_with_the_same_filters_and_the_job(env):
    jid = job(env)
    back = "/?site=linkedin&stack=spring&page=2"
    html = env[0].get(f"/jobs/{jid}", params={"pane": "1", "back": back}).text
    found = nexts(html)
    assert len(found) == 2                                        # status form + pin form
    for n in found:
        url = urlparse(n)
        assert url.path == "/"
        assert parse_qs(url.query) == {"site": ["linkedin"], "stack": ["spring"], "page": ["2"], "job": [str(jid)]}


def test_pane_forms_replace_a_stale_job_param_and_reject_unsafe_back(env):
    jid = job(env)
    html = env[0].get(f"/jobs/{jid}", params={"pane": "1", "back": "/?q=x&job=77"}).text
    assert all(parse_qs(urlparse(n).query) == {"q": ["x"], "job": [str(jid)]} for n in nexts(html))
    for bad in ("https://evil.example", "//evil.example", "javascript:alert(1)"):
        html = env[0].get(f"/jobs/{jid}", params={"pane": "1", "back": bad}).text
        assert nexts(html) == [f"/?job={jid}"] * 2 and "evil" not in html


def test_posting_to_a_pane_url_redirects_back_to_it(env):
    jid = job(env)
    target = f"/?site=linkedin&job={jid}"
    r = env[0].post(f"/jobs/{jid}/status", data={"status": "discarded", "next": target}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == target
    r = env[0].post(f"/jobs/{jid}/pin", data={"pinned": "1", "next": target}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == target


def test_applied_from_the_pane_still_goes_to_the_application_page(env):
    jid = job(env)
    r = env[0].post(f"/jobs/{jid}/status", data={"status": "applied", "next": f"/?job={jid}"}, follow_redirects=False)
    app_id = env[1].execute("SELECT id FROM applications WHERE job_id = ?", (jid,)).fetchone()[0]
    assert r.status_code == 303 and r.headers["location"] == f"/applications/{app_id}"


def test_pane_actions_open_posting_in_a_new_tab_and_link_to_the_full_page(env):
    jid = job(env, "42")
    html = env[0].get(f"/jobs/{jid}", params={"pane": "1", "back": "/?site=linkedin&job=1"}).text
    link = re.search(r'<a [^>]*>Open posting ↗</a>', html).group(0)
    assert 'href="https://www.linkedin.com/jobs/view/42"' in link and 'target="_blank"' in link
    assert 'rel="noopener noreferrer"' in link
    full = re.search(r'<a [^>]*>Open full page</a>', html).group(0)
    assert f'href="/jobs/{jid}?back=' in unescape(full)


def test_pane_omits_open_posting_for_unsafe_urls(env):
    jid = job(env, url="javascript:alert(1)")
    html = env[0].get(f"/jobs/{jid}", params={"pane": "1"}).text
    assert "javascript:" not in html and "Open posting ↗" not in html


def test_pane_highlights_my_stack_and_shows_possible_duplicates(env):
    a = job(env, "1", title="Spring Dev", site="linkedin", stack="Kotlin, Rust")
    b = job(env, "2", title="Spring Dev (copy)", site="indeed", stack="Kotlin")
    env[1].execute("UPDATE jobs SET dup_group = ? WHERE id IN (?, ?)", (a, a, b))
    env[1].commit()
    html = env[0].get(f"/jobs/{a}", params={"pane": "1"}).text
    assert '<li class="mine"' in html and "<li>Rust</li>" in html
    dups = html[html.index('class="pane-dups"'):]
    assert "Possible duplicates" in dups and "indeed" in dups and f'/jobs/{b}' in dups and "Spring Dev (copy)" in dups
    assert f'/jobs/{a}?' not in dups                     # not listed against itself


def test_pane_without_duplicates_has_no_duplicates_block(env):
    assert "pane-dups" not in env[0].get(f"/jobs/{job(env)}", params={"pane": "1"}).text


def test_description_is_rendered_from_the_normalized_text_and_stays_safe(env):
    jid = job(env, description="Ubicación: Monterrey\n\n**REQUISITOS**\n\n• Python\n\n• SQL\n\n• Docker\n\n<script>alert(1)</script>")
    html = env[0].get(f"/jobs/{jid}", params={"pane": "1"}).text
    assert '<dl class="desc-meta">' in html and "<dt>Ubicación</dt>" in html and "<dd>Monterrey</dd>" in html
    assert "<h3>Requisitos</h3>" in html
    assert "<li>Python</li>" in html and "<li>Docker</li>" in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_stored_description_is_not_rewritten(env):
    raw = "**REQUISITOS**\n\n• Python\n"
    jid = job(env, description=raw)
    env[0].get(f"/jobs/{jid}", params={"pane": "1"})
    env[0].get(f"/jobs/{jid}")
    assert env[1].execute("SELECT description FROM jobs WHERE id = ?", (jid,)).fetchone()[0] == raw


# --- /?job=<id>: the same split rendered by the server ---------------------------------------------

def test_index_with_job_renders_it_in_the_pane_selects_the_row_and_marks_it_seen(env):
    jid = job(env, "1", title="Pane Role", description="Hello **world**")
    other = job(env, "2", title="Other Role", minutes=5)
    html = env[0].get("/", params={"job": str(jid)}).text
    pane = pane_of(html)
    assert "Pane Role" in pane and "<strong>world</strong>" in pane and "About the job" in pane
    assert status_of(env, jid) == "seen" and status_of(env, other) == "new"
    row = re.search(r'<li class="job status-seen selected" aria-current="true">.*?</li>', html, re.S).group(0)
    assert "Pane Role" in row
    assert html.count('aria-current="true"') == 1
    assert "unread-dot" not in row                                  # the list shows it already read


def test_index_without_job_shows_the_empty_state_and_marks_nothing(env):
    jid = job(env)
    html = env[0].get("/").text
    assert "Select a job to see its description" in pane_of(html)
    assert "selected" not in re.search(r'<li class="job[^"]*"', html).group(0)
    assert status_of(env, jid) == "new"


@pytest.mark.parametrize("bad", ["abc", "999", "-1", "", "1.5"])
def test_index_ignores_an_invalid_or_unknown_job(env, bad):
    jid = job(env)
    r = env[0].get("/", params={"job": bad})
    assert r.status_code == 200
    assert "Select a job to see its description" in pane_of(r.text)
    assert status_of(env, jid) == "new"


def test_job_param_is_a_selection_not_a_filter(env):
    jid = job(env)
    html = env[0].get("/", params={"job": str(jid)}).text
    assert "Reset filters" not in html
    assert 'name="job"' not in html


def test_row_forms_and_pager_keep_the_selection(env):
    ids = [job(env, str(n), minutes=n) for n in range(25)]             # two pages
    html = env[0].get("/", params={"job": str(ids[0])}).text
    row_nexts = nexts(html[html.index('id="job-list"'):html.index('id="job-pane"')])
    assert row_nexts and all(n == f"/?job={ids[0]}" for n in row_nexts)
    pager = html[html.index('<nav class="pager"'):]
    assert unescape(re.search(r'rel="next" href="([^"]+)"', pager).group(1)) == f"/?page=2&job={ids[0]}"


def test_a_selected_group_member_opens_its_group_and_is_marked(env):
    a = job(env, "1", title="Dev A", site="linkedin", minutes=1)
    b = job(env, "2", title="Dev B", site="indeed", minutes=5)
    env[1].execute("UPDATE jobs SET dup_group = ? WHERE id IN (?, ?)", (a, a, b))
    env[1].commit()
    html = env[0].get("/", params={"job": str(b)}).text
    assert re.search(r'<details class="dups" open>', html)
    member = re.search(r'<li class="member status-seen selected" aria-current="true">.*?</li>', html, re.S).group(0)
    assert "Dev B" in member
    assert "<details class=\"dups\">" in env[0].get("/").text            # closed when nothing in it is selected


def test_list_links_carry_the_job_id_for_the_script_and_still_point_to_the_detail_page(env):
    a = job(env, "1", title="Dev A", minutes=1)
    b = job(env, "2", title="Dev B", minutes=5)
    env[1].execute("UPDATE jobs SET dup_group = ? WHERE id IN (?, ?)", (a, a, b))
    env[1].commit()
    html = env[0].get("/").text
    assert re.search(rf'<a class="title" href="/jobs/{a}\?back=[^"]*" data-job-id="{a}">Dev A</a>', html)
    assert re.search(rf'<a class="m-title" href="/jobs/{b}\?back=[^"]*" data-job-id="{b}">Dev B</a>', html)


def test_split_layout_lives_inside_the_inert_results_area_and_keeps_the_filters_on_top(env):
    job(env)
    html = env[0].get("/").text
    area = html[html.index('<div id="results-area"'):html.index('<div id="search-overlay"')]
    assert area.index('<form class="filters"') < area.index('id="split"') < area.index('id="job-list"') < area.index('id="job-pane"')
    assert 'class="pane"' in area and 'aria-label="Job description"' in area


def test_pinned_job_can_be_selected_too(env):
    jid = job(env, title="Pinned Role")
    env[1].execute("UPDATE jobs SET pinned_at = ?", (NOW.isoformat(),))
    env[1].commit()
    html = env[0].get("/", params={"job": str(jid)}).text
    assert "Pinned Role" in pane_of(html)
    assert re.search(r'<li class="job status-seen selected"', html[html.index('id="pinned"'):html.index('id="job-list"')])


# --- script and style hooks -------------------------------------------------------------------

def test_app_js_loads_the_pane_without_leaving_the_page():
    for needle in ("history.pushState", "popstate", "pane=1", "min-width: 1100px", "data-job-id", "job-pane",
                   "Escape", "ArrowDown", "closest"):
        assert needle in APP_JS, needle
    assert "fetch(" in APP_JS


def test_app_js_keeps_the_selection_out_of_the_saved_filters():
    assert 'searchParams.delete("job")' in APP_JS


def test_css_has_the_wide_split_with_a_sticky_scrolling_pane_and_a_highlighted_row():
    assert re.search(r"@media \(min-width: 1100px\)\s*\{", CSS)
    wide = CSS[CSS.index("@media (min-width: 1100px)"):]
    pane = re.search(r"\.pane\s*\{([^}]*)\}", wide).group(1)
    assert "position: sticky" in pane and "overflow-y: auto" in pane and "100vh" in pane
    assert re.search(r"\.job\.selected, \.member\.selected \{[^}]*outline[^}]*background", CSS)
    assert re.search(r"\.description h3\s*\{", CSS) and "72ch" in CSS


def test_page_title_names_the_selected_job(env):
    jid = job(env, title="Pane Role")
    assert "<title>Pane Role · job_finder</title>" in env[0].get("/", params={"job": str(jid)}).text
    assert "<title>job_finder</title>" in env[0].get("/").text


def test_empty_state_is_also_available_as_a_template_for_the_script_to_restore(env):
    html = env[0].get("/", params={"job": str(job(env))}).text
    assert re.search(r'<template id="pane-empty-tpl">\s*<div class="pane-empty">.*Select a job to see its description', html, re.S)
    assert "pane-empty-tpl" in APP_JS


def test_clicking_a_job_scrolls_the_pane_into_view():
    assert "bringPaneIntoView" in APP_JS


def test_selected_row_is_distinct_from_unread_rows():
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "jobfinder" / "static" / "style.css").read_text()
    rule = css[css.index(".job.selected,"):].split("}", 1)[0]
    # unread rows use a thin left bar; the selection is a full white frame plus a "VIEWING" marker
    assert "outline: 2px solid var(--fg)" in rule and "inset 2px 0 0" not in rule
    assert '.job.selected::after' in css and 'content: "VIEWING ▸"' in css


def test_job_header_with_title_and_actions_stays_on_top_while_scrolling(env):
    from pathlib import Path
    client = env[0]
    job_id = job(env)
    for url in (f"/jobs/{job_id}?pane=1", f"/jobs/{job_id}"):
        html = client.get(url).text
        head = re.search(r'<header class="job-head">(.*?)</header>', html, re.S).group(1)
        assert 'class="detail-title"' in head and 'class="actions"' in head and 'action="/jobs/' in head, url
        assert "ABOUT THE JOB" not in head.upper() and "<dl" not in head, url
    css = (Path(__file__).resolve().parent.parent / "jobfinder" / "static" / "style.css").read_text()
    rule = css[css.index(".job-head {"):].split("}", 1)[0]
    assert "position: sticky" in rule and "background" in rule
