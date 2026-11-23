import re
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from jobfinder.config import Config, SearchConfig
from jobfinder.db import connect, set_job_stack
from jobfinder.web import create_app

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=TZ)


def make_cfg(tmp_path: Path) -> Config:
    return Config(
        search=SearchConfig(terms=["backend developer"], sites=["linkedin"], location="Mexico",
                            country_indeed="Mexico", pause_seconds=0),
        database=tmp_path / "jobs.db",
        exports_dir=tmp_path / "exports",
    )


def add_run(conn, started, status="success"):
    return conn.execute(
        "INSERT INTO runs (started_at, finished_at, status) VALUES (?, ?, ?)",
        (started.isoformat(), started.isoformat(), status),
    ).lastrowid


def add_job(conn, run_id, seen_at, ext_id, title="Backend Developer", company="Acme", **kw):
    job = dict(
        site="linkedin", external_id=ext_id, url=f"https://www.linkedin.com/jobs/view/{ext_id}",
        title=title, company=company, location="CDMX", stack="", description="",
        first_seen_run=run_id, first_seen_at=seen_at.isoformat(), last_seen_at=seen_at.isoformat(),
    )
    job.update(kw)
    cols = ", ".join(job)
    marks = ", ".join(f":{c}" for c in job)
    with conn:
        job_id = conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", job).lastrowid
        set_job_stack(conn, job_id, [s for s in job["stack"].split(", ") if s])
        return job_id


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    calls = []

    def runner(conn, search_cfg, on_progress):
        calls.append(search_cfg)
        on_progress("linkedin: backend developer")

    app = create_app(cfg, runner=runner, clock=lambda: NOW)
    return TestClient(app), conn, calls, app


# --- S6 / S12 listing ---------------------------------------------------------

def test_list_shows_title_salary_stack_and_detail_link(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    add_job(conn, run, NOW - timedelta(days=1), "42", title="Senior Spring Engineer",
            salary_min=50000, salary_max=60000, salary_currency="MXN", salary_interval="monthly",
            salary_basis="gross", stack="Kotlin, Spring, PostgreSQL")
    conn.commit()
    html = client.get("/").text
    assert "Senior Spring Engineer" in html
    assert "MXN 50,000–60,000 / month" in html
    assert "gross" in html
    assert "PostgreSQL" in html
    assert 'href="/jobs/1?back=' in html


def test_salary_not_published_and_basis_not_specified(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    add_job(conn, run, NOW - timedelta(days=1), "1", salary_min=30000, salary_interval="monthly")
    add_job(conn, run, NOW - timedelta(days=1), "2", title="Fullstack Dev")
    conn.commit()
    html = client.get("/").text
    assert "? 30,000 / month" in html          # currency not stated is never guessed
    assert "gross/net not specified" in html
    assert "not published" in html


# --- S13 style ----------------------------------------------------------------

def test_page_uses_local_geist_fonts_on_black(env):
    client, _, _, _ = env
    css = client.get("/static/style.css").text
    assert "GeistPixel-Square.woff2" in css and "GeistMono-Variable.woff2" in css
    assert "background: #000" in css
    assert client.get("/static/fonts/GeistMono-Variable.woff2").status_code == 200
    assert "fonts.googleapis" not in client.get("/").text


# --- filters ------------------------------------------------------------------

def test_filters(env):
    client, conn, _, _ = env
    old = add_run(conn, NOW - timedelta(days=5))
    new = add_run(conn, NOW - timedelta(days=1))
    add_job(conn, old, NOW - timedelta(days=5), "1", title="Spring Dev Old", stack="Kotlin, Spring",
            salary_min=1, salary_currency="USD", status="applied")
    add_job(conn, new, NOW - timedelta(days=1), "2", title="Python Dev New", stack="Python",
            site="indeed", salary_min=1, salary_currency="MXN")
    add_job(conn, new, NOW - timedelta(days=1), "3", title="Rust Dev New", stack="Rust", status="discarded")
    conn.commit()

    def titles(**params):
        html = client.get("/", params=params).text
        return {t for t in ("Spring Dev Old", "Python Dev New", "Rust Dev New") if t in html}

    assert titles() == {"Spring Dev Old", "Python Dev New"}               # discarded hidden by default
    assert titles(status="all") == {"Spring Dev Old", "Python Dev New", "Rust Dev New"}
    assert titles(status="applied") == {"Spring Dev Old"}
    assert titles(site="indeed") == {"Python Dev New"}
    assert titles(stack="spring") == {"Spring Dev Old"}
    assert titles(currency="USD") == {"Spring Dev Old"}
    assert titles(q="python") == {"Python Dev New"}
    assert titles(only_new="1", status="all") == {"Python Dev New", "Rust Dev New"}
    assert titles(day=(NOW - timedelta(days=5)).date().isoformat()) == {"Spring Dev Old"}
    assert titles(with_salary="1") == {"Spring Dev Old", "Python Dev New"}


def test_duplicates_are_grouped_and_old_hide_dups_links_still_work(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    a = add_job(conn, run, NOW - timedelta(days=1), "1", title="Backend Dev A")
    add_job(conn, run, NOW - timedelta(days=1), "2", title="Backend Dev B", dup_group=a)
    conn.execute("UPDATE jobs SET dup_group = ? WHERE id = ?", (a, a))
    conn.commit()
    html = client.get("/").text
    assert listed_titles(html) == ["Backend Dev B"]                   # one row per group (newest id wins the tie)
    assert "Backend Dev A" in html and "1 more posting" in html       # the other one sits in the disclosure
    old = client.get("/", params={"hide_dups": "1"})
    assert old.status_code == 200 and listed_titles(old.text) == ["Backend Dev B"]
    assert "Backend Dev A" in old.text and "1 more posting" in old.text


# --- S4 status ----------------------------------------------------------------

def test_change_status(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    job_id = add_job(conn, run, NOW - timedelta(days=1), "1")
    conn.commit()
    r = client.post(f"/jobs/{job_id}/status", data={"status": "seen", "next": "/?status=all"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?status=all"
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "seen"


def test_invalid_status_is_rejected_and_unchanged(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    job_id = add_job(conn, run, NOW - timedelta(days=1), "1")
    conn.commit()
    r = client.post(f"/jobs/{job_id}/status", data={"status": "hacked"}, follow_redirects=False)
    assert r.status_code == 400
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "new"


# --- S3 / S10 search ----------------------------------------------------------

def test_last_searched_notice(env):
    client, conn, _, _ = env
    assert "Never searched" in client.get("/").text
    add_run(conn, datetime(2026, 11, 2, 18, 30, tzinfo=TZ))
    conn.commit()
    assert "Last searched Nov 2 at 18:30 (2 days ago)" in client.get("/").text


def test_search_starts_when_not_searched_today(env):
    client, conn, calls, app = env
    add_run(conn, NOW - timedelta(days=2))
    conn.commit()
    r = client.post("/search")
    assert r.status_code == 202
    app.state.search_thread.join(timeout=5)
    assert len(calls) == 1
    assert client.get("/search/status").json()["running"] is False


def test_search_already_done_today_needs_confirmation(env):
    client, conn, calls, app = env
    add_run(conn, NOW - timedelta(hours=2))
    conn.commit()
    r = client.post("/search")
    assert r.status_code == 409
    assert r.json() == {"reason": "already_searched_today", "last_searched": "2026-11-04 08:00"}
    assert calls == []
    assert "today ✓" in client.get("/").text

    r = client.post("/search", data={"confirm": "1"})
    assert r.status_code == 202
    app.state.search_thread.join(timeout=5)
    assert len(calls) == 1


# --- S8 export ----------------------------------------------------------------

def test_export_day_downloads_markdown(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    add_job(conn, run, NOW - timedelta(days=1), "1", title="Spring Dev", description="Full description here")
    conn.commit()
    day = (NOW - timedelta(days=1)).date().isoformat()
    r = client.get("/export", params={"day": day})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert f'filename="jobs-{day}.md"' in r.headers["content-disposition"]
    assert "Spring Dev" in r.text and "Full description here" in r.text


def test_export_rejects_bad_date(env):
    client, _, _, _ = env
    assert client.get("/export", params={"day": "yesterday"}).status_code == 400


# --- pagination ---------------------------------------------------------------

def section(html, marker, end):
    start = html.index(marker)
    return html[start:html.index(end, start)]


def listed_titles(html):
    """Titles in the normal (paginated) list, in order."""
    body = section(html, '<ol class="jobs" id="job-list">', "</ol>")
    return re.findall(r'class="title"[^>]*>([^<]+)<', body)


def pinned_titles(html):
    if 'id="pinned"' not in html:
        return []
    body = section(html, 'id="pinned"', "</section>")
    return re.findall(r'class="title"[^>]*>([^<]+)<', body)


def pager_links(html):
    nav = section(html, '<nav class="pager"', "</nav>")
    return {
        rel: parse_qs(urlparse(unescape(href)).query)
        for rel, href in re.findall(r'rel="(prev|next)" href="([^"]+)"', nav)
    }


def seed_many(conn, n, **kw):
    run = add_run(conn, NOW - timedelta(days=1))
    ids = []
    for i in range(n):
        seen = NOW - timedelta(minutes=i)   # Role-00 is the newest
        ids.append(add_job(conn, run, seen, str(i), title=f"Role-{i:02d}", **kw))
    conn.commit()
    return ids


def test_first_page_has_20_and_total_counts_everything(env):
    client, conn, _, _ = env
    seed_many(conn, 25)
    html = client.get("/").text
    assert listed_titles(html) == [f"Role-{i:02d}" for i in range(20)]
    assert "25 offers" in html
    assert "Page 1 of 2" in html


def test_second_page_has_the_remaining_5(env):
    client, conn, _, _ = env
    seed_many(conn, 25)
    html = client.get("/", params={"page": "2"}).text
    assert listed_titles(html) == [f"Role-{i:02d}" for i in range(20, 25)]
    assert "25 offers" in html
    assert "Page 2 of 2" in html


def test_single_page_has_no_pager(env):
    client, conn, _, _ = env
    seed_many(conn, 20)
    html = client.get("/").text
    assert len(listed_titles(html)) == 20
    assert '<nav class="pager"' not in html


def test_page_links_keep_every_filter(env):
    client, conn, _, _ = env
    seed_many(conn, 45, site="indeed", stack="Kotlin, Spring")
    html = client.get("/", params={"site": "indeed", "stack": "spring", "page": "2"}).text
    links = pager_links(html)
    assert links["prev"] == {"site": ["indeed"], "stack": ["spring"], "page": ["1"]}
    assert links["next"] == {"site": ["indeed"], "stack": ["spring"], "page": ["3"]}


def test_pager_omits_prev_on_first_and_next_on_last_page(env):
    client, conn, _, _ = env
    seed_many(conn, 25)
    assert set(pager_links(client.get("/").text)) == {"next"}
    assert set(pager_links(client.get("/", params={"page": "2"}).text)) == {"prev"}


@pytest.mark.parametrize("raw", ["abc", "0", "-3", "1.5", ""])
def test_invalid_page_falls_back_to_first(env, raw):
    client, conn, _, _ = env
    seed_many(conn, 25)
    html = client.get("/", params={"page": raw}).text
    assert listed_titles(html)[0] == "Role-00" and len(listed_titles(html)) == 20


def test_page_beyond_last_clamps_to_last(env):
    client, conn, _, _ = env
    seed_many(conn, 25)
    html = client.get("/", params={"page": "99"}).text
    assert listed_titles(html) == [f"Role-{i:02d}" for i in range(20, 25)]
    assert "Page 2 of 2" in html


def test_page_with_no_results_is_page_one(env):
    client, _, _, _ = env
    r = client.get("/", params={"page": "7"})
    assert r.status_code == 200 and "0 offers" in r.text and "No offers yet" in r.text


def test_status_form_next_keeps_the_current_page(env):
    client, conn, _, _ = env
    seed_many(conn, 25)
    html = client.get("/", params={"page": "2"}).text
    assert 'name="next" value="/?page=2"' in html


# --- pinning ------------------------------------------------------------------

def pin(client, job_id, value="1", next="/"):
    return client.post(f"/jobs/{job_id}/pin", data={"pinned": value, "next": next},
                       follow_redirects=False)


def test_pin_redirects_back_and_stores_clock_time(env):
    client, conn, _, _ = env
    ids = seed_many(conn, 3)
    r = pin(client, ids[1], next="/?page=2&site=indeed")
    assert r.status_code == 303 and r.headers["location"] == "/?page=2&site=indeed"
    row = conn.execute("SELECT pinned_at, status FROM jobs WHERE id = ?", (ids[1],)).fetchone()
    assert row["pinned_at"] == NOW.isoformat() and row["status"] == "new"


def test_pin_redirect_rejects_external_targets(env):
    client, conn, _, _ = env
    ids = seed_many(conn, 1)
    assert pin(client, ids[0], next="//evil.example").headers["location"] == "/"
    assert pin(client, ids[0], next="https://evil.example").headers["location"] == "/"


def test_pinned_job_shows_in_pinned_section_on_every_page(env):
    client, conn, _, _ = env
    ids = seed_many(conn, 25)
    pin(client, ids[22])    # Role-22 would live on page 2
    for page in ("1", "2"):
        html = client.get("/", params={"page": page}).text
        assert pinned_titles(html) == ["Role-22"]
        assert "Role-22" not in listed_titles(html)
    html = client.get("/").text
    assert "24 offers" in html and len(listed_titles(html)) == 20
    assert "Page 1 of 2" in html
    assert listed_titles(client.get("/", params={"page": "2"}).text) == \
        [f"Role-{i:02d}" for i in (20, 21, 23, 24)]


def test_pinned_ignores_filters(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    seen = NOW - timedelta(days=1)
    pinned = add_job(conn, run, seen, "1", title="Pinned Spring", site="linkedin",
                     stack="Spring", status="applied")
    other = add_job(conn, run, seen, "2", title="Plain Rust", site="indeed", stack="Rust")
    conn.commit()
    pin(client, pinned)
    html = client.get("/", params={"site": "indeed", "status": "new"}).text
    assert pinned_titles(html) == ["Pinned Spring"]
    assert listed_titles(html) == ["Plain Rust"]
    assert "1 offer</p>" in html
    # even a discarded pinned job stays on top, and the old hide_dups param cannot drop it
    conn.execute("UPDATE jobs SET status = 'discarded', dup_group = ? WHERE id IN (?, ?)",
                 (other, pinned, other))
    conn.commit()
    html = client.get("/", params={"hide_dups": "1"}).text
    assert pinned_titles(html) == ["Pinned Spring"]
    assert "Pinned Spring" not in listed_titles(html)


def test_pinned_ordered_most_recently_pinned_first(env):
    client, conn, _, _ = env
    ids = seed_many(conn, 3)
    t = [NOW + timedelta(minutes=m) for m in (0, 5, 10)]
    for job_id, when in zip(ids, t):
        conn.execute("UPDATE jobs SET pinned_at = ? WHERE id = ?", (when.isoformat(), job_id))
    conn.commit()
    assert pinned_titles(client.get("/").text) == ["Role-02", "Role-01", "Role-00"]


def test_no_pinned_section_without_pins(env):
    client, conn, _, _ = env
    seed_many(conn, 2)
    assert 'id="pinned"' not in client.get("/").text


def test_unpin_returns_job_to_the_normal_list(env):
    client, conn, _, _ = env
    ids = seed_many(conn, 3)
    pin(client, ids[0])
    assert pinned_titles(client.get("/").text) == ["Role-00"]
    pin(client, ids[0], value="0")
    html = client.get("/").text
    assert pinned_titles(html) == []
    assert listed_titles(html) == ["Role-00", "Role-01", "Role-02"]
    assert conn.execute("SELECT pinned_at FROM jobs WHERE id = ?", (ids[0],)).fetchone()[0] is None


def test_pin_buttons_post_the_opposite_value(env):
    client, conn, _, _ = env
    ids = seed_many(conn, 2)
    pin(client, ids[0])
    html = client.get("/").text
    assert html.count('name="pinned" value="0"') == 1
    assert html.count('name="pinned" value="1"') == 1


def test_invalid_pinned_value_is_rejected_and_unchanged(env):
    client, conn, _, _ = env
    ids = seed_many(conn, 1)
    assert pin(client, ids[0], value="yes").status_code == 400
    assert conn.execute("SELECT pinned_at FROM jobs").fetchone()[0] is None
    pin(client, ids[0])
    assert pin(client, ids[0], value="maybe").status_code == 400
    assert conn.execute("SELECT pinned_at FROM jobs").fetchone()[0] == NOW.isoformat()


def test_pin_unknown_job_is_404(env):
    client, _, _, _ = env
    assert pin(client, 999).status_code == 404


# --- work mode ------------------------------------------------------------------

def test_work_mode_tag_is_shown_next_to_the_title(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    seen = NOW - timedelta(days=1)
    add_job(conn, run, seen, "1", title="Remote Job", work_mode="remote")
    add_job(conn, run, seen, "2", title="Hybrid Job", work_mode="hybrid")
    add_job(conn, run, seen, "3", title="Onsite Job", work_mode="onsite")
    add_job(conn, run, seen, "4", title="Unknown Job")
    conn.commit()
    body = section(client.get("/").text, '<ol class="jobs" id="job-list">', "</ol>")
    rows = {t: r for t, r in zip(re.findall(r'class="title"[^>]*>([^<]+)<', body), body.split('<li class="job')[1:])}
    assert 'class="tag mode mode-remote">REMOTE<' in rows["Remote Job"]
    assert 'class="tag mode mode-hybrid">HYBRID<' in rows["Hybrid Job"]
    assert 'class="tag mode mode-onsite">ONSITE<' in rows["Onsite Job"]
    assert "tag mode" not in rows["Unknown Job"]


def mode_chips(html):
    """[(value, checked, label)] for the work mode chips."""
    box = section(html, '<fieldset class="field field-modes">', "</fieldset>")
    found = re.findall(r'name="mode" value="([^"]*)"( checked)?><span class="chip-name">([^<]*)<', box)
    return [(value, bool(checked), label) for value, checked, label in found]


def test_mode_filter_is_a_set_of_toggle_chips(env):
    client, _, _, _ = env
    html = client.get("/", params=[("mode", "hybrid"), ("mode", "remote")]).text
    assert '<select name="mode">' not in html
    assert mode_chips(html) == [("remote", True, "remote"), ("hybrid", True, "hybrid"),
                                ("onsite", False, "onsite"), ("unknown", False, "not specified")]


def test_several_modes_show_jobs_with_any_of_them_and_pages_keep_them(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    seen = NOW - timedelta(days=1)
    add_job(conn, run, seen, "1", title="Remote Job", work_mode="remote")
    add_job(conn, run, seen, "2", title="Hybrid Job", work_mode="hybrid")
    add_job(conn, run, seen, "3", title="Onsite Job", work_mode="onsite")
    add_job(conn, run, seen, "4", title="Unknown Job")
    for i in range(25):
        add_job(conn, run, seen - timedelta(minutes=i + 1), f"r{i}", title=f"Remote {i}", work_mode="remote")
    conn.commit()
    html = client.get("/", params=[("mode", "remote"), ("mode", "hybrid")]).text
    assert "27 offers" in html
    assert "Onsite Job" not in html and "Unknown Job" not in html
    next_link = re.search(r'href="(/\?[^"]*page=2[^"]*)"', html).group(1)
    assert parse_qs(urlparse(unescape(next_link)).query)["mode"] == ["remote", "hybrid"]
    html = client.get("/", params=[("mode", "onsite"), ("mode", "unknown")]).text
    assert "Onsite Job" in html and "Unknown Job" in html and "Hybrid Job" not in html
    html = client.get("/", params=[("mode", "bogus")]).text            # unknown values are ignored
    assert "29 offers" in html


def test_mode_filter(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    seen = NOW - timedelta(days=1)
    add_job(conn, run, seen, "1", title="Remote Job", work_mode="remote", site="indeed")
    add_job(conn, run, seen, "2", title="Hybrid Job", work_mode="hybrid")
    add_job(conn, run, seen, "3", title="Onsite Job", work_mode="onsite")
    add_job(conn, run, seen, "4", title="Unknown Job")
    conn.commit()

    def titles(**params):
        return set(listed_titles(client.get("/", params=params).text))

    assert titles() == {"Remote Job", "Hybrid Job", "Onsite Job", "Unknown Job"}
    assert titles(mode="remote") == {"Remote Job"}
    assert titles(mode="hybrid") == {"Hybrid Job"}
    assert titles(mode="onsite") == {"Onsite Job"}
    assert titles(mode="unknown") == {"Unknown Job"}
    assert titles(mode="bogus") == {"Remote Job", "Hybrid Job", "Onsite Job", "Unknown Job"}
    assert titles(mode="remote", site="linkedin") == set()        # combines with other filters
    assert titles(mode="remote", site="indeed") == {"Remote Job"}
    assert "1 offer</p>" in client.get("/", params={"mode": "remote"}).text


def test_mode_filter_does_not_hide_pinned_jobs(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    seen = NOW - timedelta(days=1)
    add_job(conn, run, seen, "1", title="Pinned Onsite", work_mode="onsite", pinned_at=NOW.isoformat())
    add_job(conn, run, seen, "2", title="Remote Job", work_mode="remote")
    conn.commit()
    html = client.get("/", params={"mode": "remote"}).text
    assert pinned_titles(html) == ["Pinned Onsite"]
    assert listed_titles(html) == ["Remote Job"]


def test_page_links_keep_mode(env):
    client, conn, _, _ = env
    seed_many(conn, 45, work_mode="remote")
    html = client.get("/", params={"mode": "remote", "page": "2"}).text
    links = pager_links(html)
    assert links["prev"] == {"mode": ["remote"], "page": ["1"]}
    assert links["next"] == {"mode": ["remote"], "page": ["3"]}


# --- the title opens the in-app detail page -----------------------------------

def test_title_link_goes_to_detail_page_with_back_url(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    job_id = add_job(conn, run, NOW - timedelta(days=1), "42")
    conn.commit()
    html = client.get("/", params={"site": "linkedin", "page": "1"}).text
    href = re.search(r'class="title" href="([^"]+)"', html).group(1)
    url = urlparse(unescape(href))
    assert url.path == f"/jobs/{job_id}"
    assert parse_qs(url.query) == {"back": ["/?site=linkedin&page=1"]}
    assert "data-seen-url" not in html
    assert "linkedin.com/jobs/view/42" not in html


def test_seen_beacon_route_and_script_are_gone(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    job_id = add_job(conn, run, NOW - timedelta(days=1), "1")
    conn.commit()
    assert client.post(f"/jobs/{job_id}/seen").status_code in (404, 405)
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "new"
    assert "sendBeacon" not in client.get("/static/app.js").text


def test_filter_form_applies_on_change(env):
    client, _, _, _ = env
    html = client.get("/").text
    assert '<form class="filters" method="get" action="/" data-autosubmit>' in html
    assert '<noscript><button class="btn" type="submit">FILTER</button></noscript>' in html


# --- S2: applications created when a job becomes applied --------------------------

def application_rows(conn):
    return conn.execute(
        """SELECT a.job_id, a.applied_at, a.outcome, p.name AS phase FROM applications a
           LEFT JOIN phases p ON p.id = a.phase_id"""
    ).fetchall()


def test_marking_applied_creates_application_in_first_phase_with_move_entry(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    job = add_job(conn, run, NOW - timedelta(days=1), "1")
    r = client.post(f"/jobs/{job}/status", data={"status": "applied", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    (row,) = application_rows(conn)
    assert (row["job_id"], row["phase"], row["applied_at"], row["outcome"]) == (job, "Applied", NOW.isoformat(), "active")
    entry = conn.execute("SELECT phase_name, kind, notes, happened_at FROM timeline_entries").fetchone()
    assert tuple(entry) == ("Applied", "move", "Applied", NOW.isoformat())


def test_marking_applied_twice_does_not_duplicate(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1")
    for status in ("applied", "seen", "applied"):
        client.post(f"/jobs/{job}/status", data={"status": status}, follow_redirects=False)
    assert len(application_rows(conn)) == 1
    assert conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == 1


def test_changing_status_away_from_applied_keeps_application_and_entries(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1")
    client.post(f"/jobs/{job}/status", data={"status": "applied"}, follow_redirects=False)
    client.post(f"/jobs/{job}/status", data={"status": "discarded"}, follow_redirects=False)
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "discarded"
    assert len(application_rows(conn)) == 1
    assert conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == 1


def test_other_statuses_and_invalid_status_create_no_application(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1")
    for status in ("seen", "discarded", "bogus"):
        client.post(f"/jobs/{job}/status", data={"status": status}, follow_redirects=False)
    assert application_rows(conn) == []


def test_applied_from_detail_page_and_unknown_job(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1")
    r = client.post(f"/jobs/{job}/status", data={"status": "applied", "next": f"/jobs/{job}"}, follow_redirects=False)
    assert r.status_code == 303 and len(application_rows(conn)) == 1
    r = client.post("/jobs/999/status", data={"status": "applied"}, follow_redirects=False)
    assert r.status_code == 303 and len(application_rows(conn)) == 1


def test_applied_with_no_phases_is_unassigned(env):
    client, conn, _, _ = env
    with conn:
        conn.execute("DELETE FROM phases")
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1")
    client.post(f"/jobs/{job}/status", data={"status": "applied"}, follow_redirects=False)
    (row,) = application_rows(conn)
    assert row["phase"] is None


def test_header_is_identical_on_every_page_and_search_bar_sits_below_it(env):
    client, _, _, _ = env
    html = client.get("/").text
    header = html.split('<header class="top">', 1)[1].split("</header>", 1)[0]
    assert "search-btn" not in header and "Last searched" not in header and "Never searched" not in header
    assert '<a href="/" data-keep-filters aria-current="page">Jobs</a>' in header
    after = html.split("</header>", 1)[1]
    assert after.index('class="search-panel"') < after.index('class="filters"')


# --- marking applied opens the application page to attach the CV ----------------

def app_id_for(conn, job):
    return conn.execute("SELECT id FROM applications WHERE job_id = ?", (job,)).fetchone()[0]


@pytest.mark.parametrize("next_url", ["/?page=2", "/jobs/1?back=/"])
def test_marking_applied_redirects_to_the_application_page(env, next_url):
    client, conn, _, _ = env
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1")
    r = client.post(f"/jobs/{job}/status", data={"status": "applied", "next": next_url}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{app_id_for(conn, job)}")


def test_reapplying_redirects_to_the_existing_application(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1")
    client.post(f"/jobs/{job}/status", data={"status": "applied"}, follow_redirects=False)
    client.post(f"/jobs/{job}/status", data={"status": "seen"}, follow_redirects=False)
    r = client.post(f"/jobs/{job}/status", data={"status": "applied", "next": "/"}, follow_redirects=False)
    assert r.headers["location"] == f"/applications/{app_id_for(conn, job)}"
    assert len(application_rows(conn)) == 1


def test_already_applied_and_other_statuses_keep_next(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW)
    job = add_job(conn, run, NOW, "1", status="applied")
    r = client.post(f"/jobs/{job}/status", data={"status": "applied", "next": "/?page=2"}, follow_redirects=False)
    assert r.headers["location"] == "/?page=2"
    r = client.post(f"/jobs/{job}/status", data={"status": "discarded", "next": "/?page=2"}, follow_redirects=False)
    assert r.headers["location"] == "/?page=2"
    r = client.post("/jobs/999/status", data={"status": "applied", "next": "/?page=2"}, follow_redirects=False)
    assert r.headers["location"] == "/?page=2"


# --- sources: configured sites (e.g. glassdoor) appear in the source filter -------

def source_options(html):
    import re as _re
    select = _re.search(r'<select name="site"[^>]*>(.*?)</select>', html, _re.S).group(1)
    return _re.findall(r'<option value="([^"]*)"', select)


def test_source_filter_lists_configured_sites_jobs_sites_and_manual(tmp_path):
    from dataclasses import replace
    cfg = make_cfg(tmp_path)
    cfg = replace(cfg, search=replace(cfg.search, sites=["linkedin", "indeed", "glassdoor"]))
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, runner=lambda *a: None, clock=lambda: NOW))
    assert source_options(client.get("/").text) == ["", "linkedin", "indeed", "glassdoor", "manual"]
    run = add_run(conn, NOW)
    add_job(conn, run, NOW, "1", site="glassdoor", title="Glassdoor Dev")
    add_job(conn, run, NOW, "2", site="ziprecruiter", title="Old Source Dev")
    conn.commit()
    html = client.get("/", params={"site": "glassdoor"}).text
    assert "Glassdoor Dev" in html and "Old Source Dev" not in html
    assert source_options(html) == ["", "linkedin", "indeed", "glassdoor", "ziprecruiter", "manual"]


def test_glassdoor_has_a_readable_name_in_search_messages():
    from jobfinder.search import site_problems
    assert site_problems({"glassdoor": "glassdoor: 429 blocked"}) == "Glassdoor was rate-limited"


def test_posted_within_weeks_filter(env):
    client, conn, _, _ = env
    run = add_run(conn, NOW - timedelta(days=1))
    day = lambda n: (NOW - timedelta(days=n)).date().isoformat()
    add_job(conn, run, NOW - timedelta(days=1), "1", title="Posted 10d", date_posted=day(10))
    add_job(conn, run, NOW - timedelta(days=1), "2", title="Posted 18d", date_posted=day(18))
    add_job(conn, run, NOW - timedelta(days=1), "3", title="Posted 25d", date_posted=day(25))
    add_job(conn, run, NOW - timedelta(days=1), "4", title="Posted 40d", date_posted=day(40))
    add_job(conn, run, NOW - timedelta(days=20), "5", title="No date seen 20d")
    conn.commit()
    all_titles = ("Posted 10d", "Posted 18d", "Posted 25d", "Posted 40d", "No date seen 20d")

    def titles(**params):
        html = client.get("/", params=params).text
        return {t for t in all_titles if t in html.split('class="count')[1]}

    assert titles() == set(all_titles)
    assert titles(posted_weeks="2") == {"Posted 10d"}
    assert titles(posted_weeks="3") == {"Posted 10d", "Posted 18d", "No date seen 20d"}
    assert titles(posted_weeks="4") == {"Posted 10d", "Posted 18d", "No date seen 20d", "Posted 25d"}
    assert titles(posted_weeks="9") == set(all_titles)                 # unknown values are ignored
    html = client.get("/", params={"posted_weeks": "3"}).text
    assert '<option value="3" selected>last 3 weeks</option>' in html


def test_pin_keeps_the_scroll_position():
    js = (Path(__file__).resolve().parents[1] / "jobfinder/static/app.js").read_text()
    block = js[js.index('"job-finder:pin-scroll"'):]
    assert 'form.matches("form.pin")' in block and "sessionStorage.setItem(KEY" in block
    assert "window.scrollBy(0, anchor.getBoundingClientRect().top - saved.top)" in block
