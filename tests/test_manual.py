import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from jobfinder.config import SearchConfig
from jobfinder.db import connect
from jobfinder.export import export_day
from jobfinder.search import last_search, run_search
from jobfinder.web import create_app
from test_board import columns, pid, snapshot
from test_web import NOW, add_job, add_run, make_cfg


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    return client, conn


def form(**over):
    data = {"title": "Platform Engineer", "company": "Initech", "link": "https://initech.example/jobs/9",
            "location": "Monterrey", "work_mode": "hybrid", "salary": "MXN 90k net / month",
            "applied_at": "2026-11-02"}
    data.update(over)
    return data


def counts(conn):
    return tuple(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("jobs", "applications", "timeline_entries", "runs"))


# --- form --------------------------------------------------------------------------------------

def test_form_page_has_all_fields_and_defaults_the_date_to_today(env):
    client, _ = env
    html = client.get("/applications/new").text
    for name in ("title", "company", "link", "location", "work_mode", "salary", "applied_at"):
        assert f'name="{name}"' in html
    assert re.search(r'<input[^>]*type="date"[^>]*name="applied_at"[^>]*value="2026-11-04"|'
                     r'<input[^>]*name="applied_at"[^>]*type="date"[^>]*value="2026-11-04"', html)
    assert re.findall(r'<option value="([^"]*)"', re.search(r'<select name="work_mode".*?</select>', html, re.S).group(0)) \
        == ["", "remote", "hybrid", "onsite"]
    assert 'maxlength="200"' in html


def test_new_is_not_captured_by_the_application_id_route(env):
    client, _ = env
    assert client.get("/applications/new").status_code == 200
    assert client.get("/applications/phases").status_code == 200


# --- create -----------------------------------------------------------------------------------------

def test_creates_manual_job_and_application_and_redirects(env):
    client, conn = env
    r = client.post("/applications/new", data=form())
    assert r.status_code == 303
    app = conn.execute("SELECT * FROM applications").fetchone()
    assert r.headers["location"] == f"/applications/{app['id']}"
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert (job["site"], job["title"], job["company"], job["url"], job["location"], job["work_mode"], job["status"]) == (
        "manual", "Platform Engineer", "Initech", "https://initech.example/jobs/9", "Monterrey", "hybrid", "applied")
    assert re.fullmatch(r"manual-[0-9a-f]{32}", job["external_id"])
    assert job["description"] == "Salary: MXN 90k net / month"
    assert (job["salary_min"], job["salary_max"], job["salary_currency"], job["salary_basis"]) == (None,) * 4
    assert job["stack"] == "" and job["dup_group"] is None and job["pinned_at"] is None
    assert job["first_seen_at"] == job["last_seen_at"] == NOW.isoformat()
    first = conn.execute("SELECT id FROM phases ORDER BY position LIMIT 1").fetchone()[0]
    assert (app["job_id"], app["phase_id"], app["outcome"], app["cv_id"]) == (job["id"], first, "active", None)
    assert app["applied_at"].startswith("2026-11-02T00:00:00")
    entry = conn.execute("SELECT * FROM timeline_entries").fetchone()
    assert (entry["application_id"], entry["kind"], entry["phase_name"]) == (app["id"], "move", "Applied")
    assert entry["happened_at"] == app["applied_at"]


def test_optional_fields_may_be_blank(env):
    client, conn = env
    r = client.post("/applications/new", data=form(link="", location="", work_mode="", salary=""))
    assert r.status_code == 303
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert (job["url"], job["location"], job["work_mode"], job["description"]) == ("", None, None, "")


def test_values_are_trimmed_and_the_same_job_can_be_added_twice(env):
    client, conn = env
    client.post("/applications/new", data=form(title="  Dev  ", company="  Acme "))
    client.post("/applications/new", data=form(title="  Dev  ", company="  Acme "))
    rows = conn.execute("SELECT title, company, external_id FROM jobs").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("Dev", "Acme")] * 2
    assert rows[0]["external_id"] != rows[1]["external_id"]


def test_title_boundaries(env):
    client, conn = env
    assert client.post("/applications/new", data=form(title="x" * 200)).status_code == 303
    before = counts(conn)
    assert client.post("/applications/new", data=form(title="x" * 201)).status_code == 400
    assert counts(conn) == before


def test_lands_in_first_phase_or_unassigned_when_there_are_none(env):
    client, conn = env
    with conn:
        conn.execute("DELETE FROM phases")
    assert client.post("/applications/new", data=form()).status_code == 303
    assert conn.execute("SELECT phase_id FROM applications").fetchone()[0] is None
    assert "unassigned" in columns(client.get("/applications").text)


# --- validation -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("over, message", [
    ({"title": ""}, "Title is required"),
    ({"title": "   "}, "Title is required"),
    ({"title": "x" * 201}, "at most 200"),
    ({"company": ""}, "Company is required"),
    ({"company": "x" * 201}, "at most 200"),
    ({"link": "javascript:alert(1)"}, "http"),
    ({"link": "ftp://x/y"}, "http"),
    ({"link": "initech.example/jobs"}, "http"),
    ({"link": "data:text/html,hi"}, "http"),
    ({"link": "https://" + "x" * 2100}, "at most 2000"),
    ({"location": "x" * 201}, "at most 200"),
    ({"work_mode": "moon"}, "Work mode"),
    ({"salary": "x" * 201}, "at most 200"),
    ({"applied_at": ""}, "Applied date is required"),
    ({"applied_at": "yesterday"}, "Applied date"),
    ({"applied_at": "2026-02-30"}, "Applied date"),
])
def test_invalid_input_rerenders_with_message_and_creates_nothing(env, over, message):
    client, conn = env
    before = counts(conn)
    r = client.post("/applications/new", data=form(**over))
    assert r.status_code == 400
    assert 'role="alert"' in r.text and message in r.text
    assert counts(conn) == before


def test_invalid_form_keeps_what_was_typed_escaped(env):
    client, conn = env
    r = client.post("/applications/new", data=form(title="", company='"><script>alert(1)</script>'))
    assert r.status_code == 400
    assert "<script>alert(1)" not in r.text
    assert "&lt;script&gt;" in r.text or "&#34;&gt;&lt;script&gt;" in r.text
    assert 'value="Monterrey"' in r.text


def test_all_errors_are_listed_together(env):
    client, _ = env
    r = client.post("/applications/new", data=form(title="", company="", applied_at=""))
    assert r.text.count('class="error-item"') == 3


def test_missing_fields_are_400(env):
    client, conn = env
    before = counts(conn)
    assert client.post("/applications/new", data={}).status_code == 400
    assert counts(conn) == before


# --- the sentinel run ------------------------------------------------------------------------------------

def test_manual_job_references_a_manual_run_that_is_created_once(env):
    client, conn = env
    client.post("/applications/new", data=form())
    client.post("/applications/new", data=form(title="Second"))
    runs = conn.execute("SELECT * FROM runs").fetchall()
    assert [r["status"] for r in runs] == ["manual"]
    assert {r[0] for r in conn.execute("SELECT first_seen_run FROM jobs")} == {runs[0]["id"]}
    assert runs[0]["new_count"] == 0 and runs[0]["seen_count"] == 0


def test_manual_run_does_not_count_as_a_search(env):
    client, conn = env
    client.post("/applications/new", data=form())
    assert last_search(conn) is None
    html = client.get("/").text
    assert "Never searched" in html


def test_last_search_ignores_manual_run_even_when_it_is_newer(env):
    client, conn = env
    real = add_run(conn, NOW - timedelta(days=2))
    conn.commit()
    client.post("/applications/new", data=form())
    assert last_search(conn).run_id == real


# --- listing ----------------------------------------------------------------------------------------------

def rows(html):
    """List rows, including the nested rows of a group of possible duplicates."""
    return re.findall(r'<li class="(?:job|member) status-\w+">.*?</li>', html, re.S)


def test_manual_job_is_listed_with_source_manual_and_not_new(env):
    client, conn = env
    run = add_run(conn, NOW - timedelta(days=1))
    add_job(conn, run, NOW - timedelta(days=1), "1", title="Scraped Role")
    conn.commit()
    client.post("/applications/new", data=form())
    html = client.get("/").text
    (manual,) = [r for r in rows(html) if "Platform Engineer" in r]
    assert "manual" in manual and "Initech" in manual
    assert 'class="tag new"' not in manual
    (scraped,) = [r for r in rows(html) if "Scraped Role" in r]
    assert 'class="tag new"' in scraped          # the real new job still shows NEW


def test_manual_job_not_new_even_without_any_successful_search(env):
    client, conn = env
    client.post("/applications/new", data=form())
    assert 'class="tag new"' not in client.get("/").text
    assert "Platform Engineer" not in client.get("/", params={"only_new": "1"}).text


def test_source_filter_offers_manual_and_filters_by_it(env):
    client, conn = env
    run = add_run(conn, NOW - timedelta(days=1))
    add_job(conn, run, NOW - timedelta(days=1), "1", title="Scraped Role")
    conn.commit()
    client.post("/applications/new", data=form())
    html = client.get("/").text
    select = re.search(r'<select name="site">.*?</select>', html, re.S).group(0)
    assert re.findall(r'<option value="([^"]*)"', select) == ["", "linkedin", "manual"]  # configured sites + stored ones, manual last
    only = client.get("/", params={"site": "manual"}).text
    assert "Platform Engineer" in only and "Scraped Role" not in only


def test_manual_job_opens_on_the_detail_page_without_a_posting_link(env):
    client, conn = env
    client.post("/applications/new", data=form(link=""))
    html = client.get("/jobs/1").text
    # "Salary: …" at the top of the description is shown as a compact meta list
    assert "Platform Engineer" in html and "<dt>Salary</dt><dd>MXN 90k net / month</dd>" in html
    assert "Open posting" not in html
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "applied"


def test_manual_application_appears_on_the_board(env):
    client, conn = env
    client.post("/applications/new", data=form())
    cols = columns(client.get("/applications").text)
    assert "Platform Engineer" in cols[f"phase-{pid(conn, 'Applied')}"]
    assert "2 days since applied" in cols[f"phase-{pid(conn, 'Applied')}"]


# --- search / dedup / export unaffected ----------------------------------------------------------------------

def test_search_still_works_and_ignores_the_manual_job(env, tmp_path):
    import pandas as pd
    client, conn = env
    client.post("/applications/new", data=form(title="Backend Developer", company="Acme"))
    cfg = SearchConfig(terms=["backend"], sites=["linkedin"], location="Mexico", country_indeed="Mexico",
                       pause_seconds=0)
    df = pd.DataFrame([{"id": "li-1", "title": "Backend Developer", "company": "Acme", "location": "CDMX",
                        "job_url": "https://www.linkedin.com/jobs/view/li-1", "description": "Python"}])
    result = run_search(conn, cfg, scrape=lambda **kw: df, now=NOW)
    assert (result.status, result.new_count) == ("success", 1)
    assert last_search(conn).run_id == result.run_id
    sites = [r[0] for r in conn.execute("SELECT site FROM jobs ORDER BY id")]
    assert sites == ["manual", "linkedin"]
    html = client.get("/").text
    scraped = [r for r in rows(html) if "li-1" in r or "linkedin" in r]
    assert any('class="tag new"' in r for r in scraped)
    manual = [r for r in rows(html) if "manual" in r]
    assert manual and all('class="tag new"' not in r for r in manual)
    assert conn.execute("SELECT count(*) FROM runs WHERE status = 'manual'").fetchone()[0] == 1


def test_export_includes_manual_jobs_without_breaking(env):
    client, conn = env
    client.post("/applications/new", data=form(location="", work_mode=""))
    body = export_day(conn, NOW.date())
    assert "Platform Engineer" in body and "Initech" in body
    r = client.get("/export", params={"day": NOW.date().isoformat()})
    assert r.status_code == 200 and "Platform Engineer" in r.text
