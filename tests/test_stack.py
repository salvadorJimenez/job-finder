"""My stack: job_stack table, best-match ordering, multi-select stack chips and their efficiency."""
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from jobfinder import web
from jobfinder.config import Config, SearchConfig
from jobfinder.db import connect, set_job_stack
from jobfinder.search import run_search
from jobfinder.web import create_app

TZ = timezone(timedelta(hours=-6))
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=TZ)
MY_STACK = ["Kotlin", "Spring", "Node.js", "Express", "TypeScript", "PostgreSQL", "Rust"]
CSS = (Path(__file__).parent.parent / "jobfinder/static/style.css").read_text()
JS = (Path(__file__).parent.parent / "jobfinder/static/app.js").read_text()


def make_cfg(tmp_path: Path, stack=None) -> Config:
    return Config(
        search=SearchConfig(terms=["backend developer"], sites=["linkedin"], location="Mexico",
                            country_indeed="Mexico", pause_seconds=0),
        database=tmp_path / "jobs.db",
        exports_dir=tmp_path / "exports",
        stack=MY_STACK if stack is None else stack,
    )


def add_run(conn, started=NOW):
    return conn.execute(
        "INSERT INTO runs (started_at, finished_at, status) VALUES (?, ?, 'success')",
        (started.isoformat(), started.isoformat()),
    ).lastrowid


def add_job(conn, run_id, ext_id, stack="", minutes=0, **kw):
    seen = NOW - timedelta(minutes=minutes)
    job = dict(
        site="linkedin", external_id=ext_id, url=f"https://www.linkedin.com/jobs/view/{ext_id}",
        title=f"Role-{ext_id}", company="Acme", location="CDMX", stack=stack, description="",
        first_seen_run=run_id, first_seen_at=seen.isoformat(), last_seen_at=seen.isoformat(),
    )
    job.update(kw)
    cols = ", ".join(job)
    marks = ", ".join(f":{c}" for c in job)
    with conn:
        job_id = conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", job).lastrowid
        set_job_stack(conn, job_id, [s for s in stack.split(", ") if s])
    return job_id


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW))
    return client, conn


def section(html, start, end):
    i = html.index(start)
    return html[i: html.index(end, i)]


def listed_titles(html):
    body = section(html, '<ol class="jobs" id="job-list">', "</ol>")
    return re.findall(r'class="title"[^>]*>([^<]+)<', body)


def chips(html):
    """[(value, checked, label, count)] for every stack chip, in page order."""
    out = []
    for body in re.findall(r'<label class="chip[^"]*">(.*?)</label>', html, re.S):
        value = re.search(r'name="stack" value="([^"]*)"', body).group(1)
        label = re.search(r'class="chip-name">([^<]*)<', body).group(1)
        count = int(re.search(r'class="chip-count">(\d+)<', body).group(1))
        out.append((value, " checked" in body, label, count))
    return out


def links(html):
    nav = section(html, '<nav class="pager"', "</nav>")
    return {
        rel: parse_qs(urlparse(unescape(href)).query)
        for rel, href in re.findall(r'rel="(prev|next)" href="([^"]+)"', nav)
    }


# --- S3: job_stack storage ---------------------------------------------------------

def test_job_stack_table_and_index_exist(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(job_stack)")}
    assert set(cols) == {"job_id", "tech"}
    assert cols["job_id"]["pk"] == 1 and cols["tech"]["pk"] == 2
    index = [r for r in conn.execute("PRAGMA index_list(job_stack)") if r["name"] == "job_stack_tech"]
    assert index
    assert [r["name"] for r in conn.execute("PRAGMA index_info(job_stack_tech)")] == ["tech", "job_id"]


def test_set_job_stack_normalizes_keys_and_replaces(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    job = add_job(conn, add_run(conn), "1")
    set_job_stack(conn, job, ["Node.js", "Rust", "node.js", " PostgreSQL "])
    keys = {r[0] for r in conn.execute("SELECT tech FROM job_stack WHERE job_id = ?", (job,))}
    assert keys == {"node.js", "rust", "postgresql"}
    set_job_stack(conn, job, ["Rust"])
    assert [r[0] for r in conn.execute("SELECT tech FROM job_stack WHERE job_id = ?", (job,))] == ["rust"]


def test_new_jobs_from_a_search_get_job_stack_rows(tmp_path):
    conn = connect(tmp_path / "jobs.db")

    class Frame:
        attrs: dict = {}

        def __init__(self, rows):
            self.rows = rows

        def to_dict(self, _):
            return self.rows

    desc = "Node.js with Express and PostgreSQL"
    scrape = lambda **kw: Frame([{"id": "1", "job_url": "https://x/1", "title": "Dev", "company": "Acme",
                                  "description": desc, "skills": "Agile"}])
    cfg = SearchConfig(terms=["backend developer"], sites=["linkedin"], location="Mexico",
                       country_indeed="Mexico", pause_seconds=0)
    run_search(conn, cfg, scrape=scrape, now=NOW)
    (job,) = conn.execute("SELECT id, stack FROM jobs").fetchall()
    assert job["stack"] == "Node.js, Express, PostgreSQL, Agile"
    keys = {r[0] for r in conn.execute("SELECT tech FROM job_stack WHERE job_id = ?", (job["id"],))}
    assert keys == {"node.js", "express", "postgresql", "agile"}
    run_search(conn, cfg, scrape=scrape, now=NOW)       # a re-seen job adds nothing
    assert conn.execute("SELECT count(*) FROM job_stack").fetchone()[0] == 4


OLD_SCHEMA = """
CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, new_count INTEGER NOT NULL DEFAULT 0,
    seen_count INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY, site TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT NOT NULL,
    url_direct TEXT, title TEXT NOT NULL, company TEXT, location TEXT, is_remote INTEGER,
    date_posted TEXT, job_type TEXT, salary_min REAL, salary_max REAL, salary_currency TEXT,
    salary_interval TEXT, salary_source TEXT, salary_basis TEXT,
    stack TEXT NOT NULL DEFAULT '', description TEXT, search_terms TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new', dup_group INTEGER,
    first_seen_run INTEGER NOT NULL REFERENCES runs(id),
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    pinned_at TEXT, work_mode TEXT, UNIQUE (site, external_id));
INSERT INTO runs (id, started_at, status) VALUES (1, '2026-11-01T10:00:00', 'success');
INSERT INTO jobs (site, external_id, url, title, status, stack, description, first_seen_run, first_seen_at, last_seen_at)
VALUES ('linkedin', 'a', 'https://x/a', 'Node API', 'applied', 'Node.js, PostgreSQL',
        'Build APIs with Node.js and Express. Skills: PostgreSQL.', 1, '2026-11-01', '2026-11-01');
INSERT INTO jobs (site, external_id, url, title, status, stack, description, first_seen_run, first_seen_at, last_seen_at)
VALUES ('indeed', 'b', 'https://x/b', 'Kotlin dev', 'new', 'Kotlin, Agile',
        'We love Kotlin and Spring Boot.', 1, '2026-11-01', '2026-11-01');
INSERT INTO jobs (site, external_id, url, title, status, stack, description, first_seen_run, first_seen_at, last_seen_at)
VALUES ('indeed', 'c', 'https://x/c', 'Nothing', 'seen', '', NULL, 1, '2026-11-01', '2026-11-01');
"""


def old_db(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(OLD_SCHEMA)
    raw.commit()
    raw.close()
    return path


def test_migration_reextracts_stack_and_fills_job_stack(tmp_path):
    path = old_db(tmp_path)
    conn = connect(path)
    rows = {r["external_id"]: r for r in conn.execute("SELECT * FROM jobs")}
    assert rows["a"]["stack"] == "Node.js, Express, PostgreSQL"      # Express picked up, order kept
    assert rows["b"]["stack"] == "Kotlin, Spring, Agile"                # extra source skills survive
    assert rows["c"]["stack"] == ""
    by_job = {}
    for r in conn.execute("SELECT job_id, tech FROM job_stack"):
        by_job.setdefault(r["job_id"], set()).add(r["tech"])
    assert by_job[rows["a"]["id"]] == {"node.js", "express", "postgresql"}
    assert by_job[rows["b"]["id"]] == {"kotlin", "spring", "agile"}
    assert rows["c"]["id"] not in by_job


def test_migration_only_rewrites_the_stack_column(tmp_path):
    path = old_db(tmp_path)
    before = sqlite3.connect(path)
    before.row_factory = sqlite3.Row
    old = {r["id"]: dict(r) for r in before.execute("SELECT * FROM jobs")}
    before.close()
    conn = connect(path)
    for r in conn.execute("SELECT * FROM jobs"):
        new = dict(r)
        for column, value in old[r["id"]].items():
            if column not in ("stack", "work_mode"):
                assert new[column] == value, column
    assert conn.execute("SELECT status FROM jobs WHERE external_id = 'a'").fetchone()[0] == "applied"


def test_migration_runs_once(tmp_path):
    path = old_db(tmp_path)
    conn = connect(path)
    assert conn.execute("SELECT 1 FROM meta WHERE key = 'job_stack_built'").fetchone()
    with conn:
        conn.execute("UPDATE jobs SET stack = 'Custom' WHERE external_id = 'a'")
        conn.execute("DELETE FROM job_stack")
    conn2 = connect(path)
    assert conn2.execute("SELECT stack FROM jobs WHERE external_id = 'a'").fetchone()[0] == "Custom"
    assert conn2.execute("SELECT count(*) FROM job_stack").fetchone()[0] == 0


def test_migration_on_a_fresh_database_is_a_noop(tmp_path):
    conn = connect(tmp_path / "new.db")
    assert conn.execute("SELECT count(*) FROM job_stack").fetchone()[0] == 0
    connect(tmp_path / "new.db")


# --- S4: best match ordering ------------------------------------------------------------

def seed_ranking(conn):
    run = add_run(conn)
    add_job(conn, run, "none-new", "Python, Django", minutes=0, title="No match, newest")
    add_job(conn, run, "one", "Kotlin, Python", minutes=1, title="One match")
    add_job(conn, run, "three", "Kotlin, Spring, PostgreSQL, React", minutes=5, title="Three matches")
    add_job(conn, run, "two-new", "Rust, TypeScript", minutes=2, title="Two matches newer")
    add_job(conn, run, "two-old", "node.js, Express", minutes=4, title="Two matches older")
    add_job(conn, run, "none-old", "", minutes=6, title="No stack at all")


def test_default_order_is_best_match_then_newest(env):
    client, conn = env
    seed_ranking(conn)
    expected = ["Three matches", "Two matches newer", "Two matches older", "One match",
                "No match, newest", "No stack at all"]
    assert listed_titles(client.get("/").text) == expected
    assert listed_titles(client.get("/", params={"sort": "match"}).text) == expected
    assert listed_titles(client.get("/", params={"sort": "bogus"}).text) == expected


def test_sort_newest_keeps_the_old_order(env):
    client, conn = env
    seed_ranking(conn)
    assert listed_titles(client.get("/", params={"sort": "newest"}).text) == [
        "No match, newest", "One match", "Two matches newer", "Two matches older",
        "Three matches", "No stack at all"]


def test_no_configured_stack_means_newest_first(tmp_path):
    cfg = make_cfg(tmp_path, stack=[])
    conn = connect(cfg.database)
    seed_ranking(conn)
    html = TestClient(create_app(cfg, clock=lambda: NOW)).get("/").text
    assert listed_titles(html)[:2] == ["No match, newest", "One match"]
    assert 'name="sort"' not in html


def test_sort_select_offers_best_match_and_newest(env):
    client, conn = env
    seed_ranking(conn)
    html = client.get("/", params={"sort": "newest"}).text
    select = section(html, '<select name="sort">', "</select>")
    assert re.search(r'<option value="match"\s*>', select)
    assert re.search(r'<option value="newest" selected>', select)
    assert "best match" in select and "newest" in select


def test_ranked_pagination_is_stable_and_complete(env):
    client, conn = env
    run = add_run(conn)
    techs = ["Kotlin", "Spring", "Rust", "Python", "Java", "Node.js"]
    rng = random.Random(7)
    for i in range(65):
        stack = ", ".join(rng.sample(techs, rng.randint(0, 4)))
        add_job(conn, run, str(i), stack, minutes=i // 3, title=f"Role-{i:02d}")   # ties on first_seen_at
    seen, pages = [], []
    for p in (1, 2, 3, 4):
        page_titles = listed_titles(client.get("/", params={"page": p}).text)
        pages.append(len(page_titles))
        seen += page_titles
    assert pages == [20, 20, 20, 5]
    assert len(seen) == len(set(seen)) == 65

    def score(title):
        stack = conn.execute("SELECT stack FROM jobs WHERE title = ?", (title,)).fetchone()[0]
        return len({s for s in stack.split(", ")} & set(MY_STACK))

    scores = [score(t) for t in seen]
    assert scores == sorted(scores, reverse=True)
    assert client.get("/", params={"page": 1}).text.count("65 offers") == 1


def test_pinned_section_is_unchanged_by_ranking(env):
    client, conn = env
    seed_ranking(conn)
    conn.execute("UPDATE jobs SET pinned_at = ? WHERE title = 'No stack at all'", (NOW.isoformat(),))
    conn.commit()
    html = client.get("/").text
    assert "No stack at all" not in listed_titles(html)
    assert "No stack at all" in section(html, 'id="pinned"', "</section>")
    assert "5 offers" in html


def test_my_stack_tags_are_marked_in_rows(env):
    client, conn = env
    add_job(conn, add_run(conn), "1", "Kotlin, Python, Rust", title="Mixed")
    html = client.get("/").text
    row = section(html, '<li class="job', '<form class="pin"')
    mine = re.findall(r'<li class="mine"[^>]*>([^<]*)<', row)
    assert mine == ["Kotlin", "Rust"]
    assert "<li>Python</li>" in row
    assert "2/7 match" in row
    assert ".stack li.mine" in CSS and "font-weight: 700" in section(CSS, ".stack li.mine", "}")


def test_rows_without_matches_show_no_match_counter(env):
    client, conn = env
    add_job(conn, add_run(conn), "1", "Python", title="Plain")
    assert "match" not in section(client.get("/").text, '<li class="job', '<form class="pin"')


# --- S5: stack chips ------------------------------------------------------------------

def seed_chips(conn):
    run = add_run(conn)
    add_job(conn, run, "1", "Kotlin, Spring, Python", minutes=1, title="RR")
    add_job(conn, run, "2", "Rust, Python", minutes=2, title="RustPy")
    add_job(conn, run, "3", "Java, Agile", minutes=3, title="JavaOnly")
    add_job(conn, run, "4", "Python", minutes=4, title="PyOnly")
    add_job(conn, run, "5", "", minutes=5, title="NoStack")


def test_chips_list_my_stack_first_then_alphabetical_with_counts(env):
    client, conn = env
    seed_chips(conn)
    assert chips(client.get("/").text) == [
        ("kotlin", False, "Kotlin", 1), ("spring", False, "Spring", 1), ("rust", False, "Rust", 1),
        ("agile", False, "agile", 1), ("java", False, "Java", 1), ("python", False, "Python", 3),
    ]


def test_old_single_stack_text_input_is_gone(env):
    client, conn = env
    seed_chips(conn)
    html = client.get("/").text
    assert 'list="stack-options"' not in html
    assert not re.search(r'<input[^>]*type="text"[^>]*name="stack"', html)
    assert not re.search(r'<input(?![^>]*checkbox)[^>]*name="stack"', html)


def test_multiple_selected_stacks_show_jobs_with_any(env):
    client, conn = env
    seed_chips(conn)
    html = client.get("/", params=[("stack", "kotlin"), ("stack", "rust")]).text
    assert set(listed_titles(html)) == {"RR", "RustPy"}
    assert "2 offers" in html
    assert [(v, c) for v, c, _, _ in chips(html) if c] == [("kotlin", True), ("rust", True)]


def test_one_job_matching_several_selected_stacks_appears_once(env):
    client, conn = env
    seed_chips(conn)
    html = client.get("/", params=[("stack", "kotlin"), ("stack", "spring"), ("stack", "python")]).text
    titles = listed_titles(html)
    assert sorted(titles) == ["PyOnly", "RR", "RustPy"] and "3 offers" in html


def test_legacy_single_stack_value_is_case_insensitive(env):
    client, conn = env
    seed_chips(conn)
    assert listed_titles(client.get("/", params={"stack": "Spring"}).text) == ["RR"]
    assert listed_titles(client.get("/", params={"stack": "SPRING"}).text) == ["RR"]


def test_unknown_stack_values_match_nothing_and_known_ones_still_work(env):
    client, conn = env
    seed_chips(conn)
    assert listed_titles(client.get("/", params={"stack": "cobol"}).text) == []
    assert listed_titles(client.get("/", params=[("stack", "cobol"), ("stack", "rust")]).text) == ["RustPy"]


def test_stack_values_with_sql_metacharacters_are_harmless(env):
    client, conn = env
    seed_chips(conn)
    r = client.get("/", params=[("stack", "x') OR 1=1 --"), ("stack", "%")])
    assert r.status_code == 200 and listed_titles(r.text) == []


def test_page_links_keep_every_repeated_stack_param(env):
    client, conn = env
    run = add_run(conn)
    for i in range(45):
        add_job(conn, run, str(i), "Kotlin, Rust", minutes=i)
    html = client.get("/", params=[("stack", "kotlin"), ("stack", "rust"), ("page", "2")]).text
    for rel, n in (("prev", "1"), ("next", "3")):
        assert links(html)[rel] == {"stack": ["kotlin", "rust"], "page": [n]}


def test_selected_chip_is_filled_and_not_colour_alone(env):
    client, conn = env
    seed_chips(conn)
    html = client.get("/", params={"stack": "kotlin"}).text
    assert re.search(r'<label class="chip[^"]*">\s*<input type="checkbox" name="stack" value="kotlin" checked', html)
    assert ".chip input:checked" in CSS
    on = section(CSS, ".chip input:checked", "}")
    assert "background" in on and "color: #000" in on          # inverted fill
    assert "✓" in CSS                                             # marker, not colour alone
    assert ".chip input:focus-visible" in CSS


def test_clear_link_only_when_something_is_selected(env):
    client, conn = env
    seed_chips(conn)
    assert 'class="clear-stack"' not in client.get("/").text
    html = client.get("/", params=[("stack", "rust"), ("site", "linkedin"), ("page", "2")]).text
    href = unescape(re.search(r'class="clear-stack" href="([^"]+)"', html).group(1))
    assert parse_qs(urlparse(href).query) == {"site": ["linkedin"]}


def test_long_chip_lists_collapse_into_more(env):
    client, conn = env
    run = add_run(conn)
    for i in range(30):
        add_job(conn, run, str(i), f"Tech{i:02d}, Tech{(i + 1) % 30:02d}", minutes=i)
    add_job(conn, run, "mine", "Rust", minutes=40)
    html = client.get("/").text
    assert "<details" in html
    visible = html[: html.index("<details", html.index('class="stack-filter"'))]
    assert len(chips(visible)) <= 21
    assert chips(visible)[0][0] == "rust"                       # my stack always visible
    assert len(chips(html)) == 31                             # everything is still reachable


def test_selected_chip_in_more_opens_the_details(env):
    client, conn = env
    run = add_run(conn)
    for i in range(30):
        add_job(conn, run, str(i), f"Tech{i:02d}", minutes=0)
    # equal counts: alphabetical, so Tech29 falls in the collapsed part
    assert not re.search(r"<details[^>]*\bopen\b", client.get("/").text)
    assert re.search(r"<details[^>]*\bopen\b", client.get("/", params={"stack": "tech29"}).text)


def test_autosubmit_keeps_repeated_stack_params(env):
    client, conn = env
    assert "params.append(key, value)" in JS      # FormData entries are appended, so repeats survive
    assert 'input[name="stack"]' in JS


# --- S6: efficiency -------------------------------------------------------------------

def plan(conn, sql, params):
    return "\n".join(r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_stack_filter_and_ranking_use_the_job_stack_index(env):
    client, conn = env
    seed_chips(conn)
    f = {"stack": ["kotlin", "rust"]}
    sql, params = web._list_sql(f, [k.lower() for k in MY_STACK], limit_offset="LIMIT 20 OFFSET 0")
    text = plan(conn, sql, params)
    assert "job_stack_tech" in text, text
    assert "stack LIKE" not in sql and "lower(stack)" not in sql
    sql, params = web._count_sql(f)
    assert "job_stack_tech" in plan(conn, sql, params)


def seed_synthetic(conn, n=20_000):
    rng = random.Random(42)
    techs = [t.lower() for t in MY_STACK] + [f"tech{i}" for i in range(40)]
    run = add_run(conn)
    rows, stack_rows = [], []
    for i in range(n):
        picked = rng.sample(techs, rng.randint(2, 8))
        stack = ", ".join(picked)
        seen = (NOW - timedelta(minutes=i)).isoformat()
        rows.append((i + 1, "linkedin", str(i), f"https://x/{i}", f"Role {i}", stack, run, seen, seen))
        stack_rows += [(i + 1, t) for t in picked]
    with conn:
        conn.executemany(
            "INSERT INTO jobs (id, site, external_id, url, title, stack, first_seen_run, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.executemany("INSERT INTO job_stack (job_id, tech) VALUES (?, ?)", stack_rows)


def timed(fn, repeat=3):
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return best, result


def test_filtered_and_ranked_page_is_fast_on_20k_jobs(tmp_path):
    conn = connect(tmp_path / "big.db")
    seed_synthetic(conn)
    my_keys = [k.lower() for k in MY_STACK]
    f = {"stack": ["kotlin", "rust", "node.js"], "page": "3"}
    after, (jobs, total, page, pages, _) = timed(lambda: web._query_jobs(conn, f, my_keys))
    assert page == 3 and len(jobs) == web.PAGE_SIZE and total > 20 * web.PAGE_SIZE
    scores = [len({s.lower() for s in j["stack_list"]} & set(my_keys)) for j in jobs]
    assert scores == sorted(scores, reverse=True)

    # The old approach (LIKE over the stack text, old ordering) on the same data, for comparison only.
    def old():
        clauses = " OR ".join(["(', ' || lower(stack) || ',') LIKE ?"] * 3)
        sql = (f"SELECT * FROM jobs WHERE pinned_at IS NULL AND ({clauses}) "
               "ORDER BY first_seen_at DESC, date_posted DESC, id DESC LIMIT 20 OFFSET 40")
        n = conn.execute(f"SELECT count(*) FROM jobs WHERE pinned_at IS NULL AND ({clauses})",
                         [f"%, {t},%" for t in f["stack"]]).fetchone()[0]
        return conn.execute(sql, [f"%, {t},%" for t in f["stack"]]).fetchall(), n

    before, _ = timed(old)
    print(f"\n[stack timing] 20k jobs: old LIKE approach {before * 1000:.1f} ms, job_stack ranked page {after * 1000:.1f} ms")
    assert after < 1.0


# --- chip counts follow the other active filters ----------------------------------

def counts(html):
    return {v: n for v, _, _, n in chips(html)}


def test_chip_counts_follow_the_other_filters(env):
    client, conn = env
    run = add_run(conn)
    add_job(conn, run, "1", "Kotlin, Spring", title="OldSpring", first_seen_at="2026-11-05T09:00:00-06:00")
    add_job(conn, run, "2", "Rust", title="NewRust", work_mode="remote")
    add_job(conn, run, "3", "Kotlin", title="NewKotlinOnsite", work_mode="onsite")
    day = NOW.date().isoformat()
    assert counts(client.get("/").text) == {"kotlin": 2, "spring": 1, "rust": 1}
    # the bug: with a day filter the chips still showed the global counts
    assert counts(client.get("/", params={"day": day}).text) == {"kotlin": 1, "spring": 0, "rust": 1}
    assert counts(client.get("/", params={"day": day, "mode": "remote"}).text) == {"kotlin": 0, "spring": 0, "rust": 1}


def test_chip_count_equals_the_offers_shown_when_selecting_it(env):
    client, conn = env
    run = add_run(conn)
    add_job(conn, run, "1", "Kotlin, Spring", title="A", site="indeed")
    add_job(conn, run, "2", "Kotlin", title="B")
    add_job(conn, run, "3", "Kotlin", title="C", status="discarded")
    pinned = add_job(conn, run, "4", "Kotlin", title="D")
    conn.execute("UPDATE jobs SET pinned_at = ? WHERE id = ?", (NOW.isoformat(), pinned)); conn.commit()
    base = {"site": "linkedin"}
    shown = counts(client.get("/", params=base).text)["kotlin"]
    listed = client.get("/", params={**base, "stack": "kotlin"}).text
    assert shown == 1 and re.search(r"\b1 offer\b", listed)


def test_selected_stacks_do_not_change_the_chip_counts(env):
    client, conn = env
    run = add_run(conn)
    add_job(conn, run, "1", "Kotlin", title="A")
    add_job(conn, run, "2", "Rust", title="B")
    html = client.get("/", params=[("stack", "kotlin")]).text
    assert counts(html) == {"kotlin": 1, "rust": 1}


def test_zero_count_chips_stay_visible_and_are_marked_empty(env):
    client, conn = env
    run = add_run(conn)
    add_job(conn, run, "1", "Rust", title="A", site="indeed")
    add_job(conn, run, "2", "Kotlin", title="B")
    html = client.get("/", params=[("site", "indeed"), ("stack", "kotlin")]).text
    assert [(v, c, n) for v, c, _, n in chips(html)] == [("kotlin", True, 0), ("rust", False, 1)]
    assert re.search(r'<label class="chip[^"]*\bchip-empty\b[^"]*">(?:(?!</label>).)*value="kotlin"', html, re.S)
