"""The home list shows possible duplicates (same dup_group) as one group: the representative row
plus a disclosure with the other postings. Pagination and counts work on groups."""
import re
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from jobfinder import web
from jobfinder.db import connect
from jobfinder.web import create_app
from test_web import NOW, add_job, add_run, listed_titles, make_cfg, pinned_titles, section


def make_app(tmp_path, stack=()):
    cfg = replace(make_cfg(tmp_path), stack=list(stack))
    conn = connect(cfg.database)
    return TestClient(create_app(cfg, clock=lambda: NOW)), conn


@pytest.fixture
def env(tmp_path):
    return make_app(tmp_path)


def seed(conn, ext_id, minutes_ago=0, **kw):
    """A job first seen `minutes_ago` minutes before NOW (0 = newest)."""
    run = conn.execute("SELECT id FROM runs ORDER BY id LIMIT 1").fetchone()
    run_id = run[0] if run else add_run(conn, NOW - timedelta(days=1))
    return add_job(conn, run_id, NOW - timedelta(minutes=minutes_ago), str(ext_id), **kw)


def group(conn, *ids):
    conn.execute(f"UPDATE jobs SET dup_group = ? WHERE id IN ({', '.join('?' * len(ids))})", (ids[0], *ids))
    conn.commit()


def rows(html):
    """[(representative title, [member titles])] in list order."""
    body = section(html, '<ol class="jobs" id="job-list">', "</ol>")
    out = []
    for chunk in body.split('<li class="job ')[1:]:
        (title,) = re.findall(r'class="title"[^>]*>([^<]+)<', chunk)
        out.append((title, re.findall(r'class="m-title"[^>]*>([^<]+)<', chunk)))
    return out


def summary(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", re.search(r"<summary[^>]*>(.*?)</summary>", html, re.S).group(1))).strip()


# --- G1 grouping ---------------------------------------------------------------

def test_duplicates_collapse_under_the_newest_member(env):
    client, conn = env
    a = seed(conn, 1, 30, title="Dev (old)", site="indeed")
    b = seed(conn, 2, 5, title="Dev (new)", site="linkedin")
    c = seed(conn, 3, 10, title="Dev (mid)", site="glassdoor")
    solo = seed(conn, 4, 20, title="Solo")
    group(conn, a, b, c)
    html = client.get("/").text
    assert rows(html) == [("Dev (new)", ["Dev (mid)", "Dev (old)"]), ("Solo", [])]
    assert listed_titles(html) == ["Dev (new)", "Solo"]
    assert "2 more postings · glassdoor, indeed" in summary(html)
    assert f'href="/jobs/{a}?back=' in html                      # members link to their detail page
    assert html.count("<details class=\"dups\"") == 1            # the solo row has no disclosure
    assert 'class="tag dup"' not in html                      # the disclosure replaces the tag


def test_member_rows_have_their_own_status_and_pin_controls(env):
    client, conn = env
    a = seed(conn, 1, 5, title="Dev A")
    b = seed(conn, 2, 10, title="Dev B", status="applied")
    group(conn, a, b)
    html = client.get("/").text
    members = section(html, '<ul class="members">', "</ul>")
    assert f'action="/jobs/{b}/status"' in members and f'action="/jobs/{b}/pin"' in members
    assert 'class="member status-applied"' in members
    assert f'action="/jobs/{a}/status"' not in members          # the representative keeps its own row


def test_filters_apply_to_members_and_lone_survivors_are_plain_rows(env):
    client, conn = env
    a = seed(conn, 1, 5, title="Dev LI", site="linkedin")
    b = seed(conn, 2, 10, title="Dev IN", site="indeed")
    c = seed(conn, 3, 15, title="Dev IN2", site="indeed")
    group(conn, a, b, c)
    assert rows(client.get("/").text) == [("Dev LI", ["Dev IN", "Dev IN2"])]
    html = client.get("/", params={"site": "indeed"}).text
    assert rows(html) == [("Dev IN", ["Dev IN2"])]
    html = client.get("/", params={"site": "linkedin"}).text
    assert rows(html) == [("Dev LI", [])] and "<details" not in section(html, 'id="job-list"', "</ol>")
    # a discarded member is hidden by the default status filter, shown with status=all
    conn.execute("UPDATE jobs SET status = 'discarded' WHERE id = ?", (b,))
    conn.commit()
    assert rows(client.get("/").text) == [("Dev LI", ["Dev IN2"])]
    assert rows(client.get("/", params={"status": "all"}).text) == [("Dev LI", ["Dev IN", "Dev IN2"])]


def test_representative_is_the_first_matching_member_not_the_first_overall(env):
    client, conn = env
    a = seed(conn, 1, 1, title="Newest linkedin", site="linkedin")
    b = seed(conn, 2, 9, title="Older indeed", site="indeed")
    c = seed(conn, 3, 8, title="Old indeed", site="indeed")
    group(conn, a, b, c)
    got = rows(client.get("/", params={"site": "indeed"}).text)
    assert got == [("Old indeed", ["Older indeed"])]


def test_best_match_representative_is_the_member_with_most_matches(tmp_path):
    client, conn = make_app(tmp_path, stack=["Kotlin", "Spring", "Rust"])
    newest = seed(conn, 1, 1, title="Newest, no match", stack="")
    best = seed(conn, 2, 50, title="Old, best match", stack="Kotlin, Spring")
    mid = seed(conn, 3, 20, title="Mid, one match", stack="Rust")
    other = seed(conn, 4, 2, title="Other single", stack="Kotlin")
    group(conn, newest, best, mid)
    assert rows(client.get("/").text) == [
        ("Old, best match", ["Mid, one match", "Newest, no match"]), ("Other single", [])]
    # newest sort picks the newest member and orders groups by it
    assert rows(client.get("/", params={"sort": "newest"}).text) == [
        ("Newest, no match", ["Mid, one match", "Old, best match"]), ("Other single", [])]
    assert other  # silence unused


# --- G2 counts and pagination --------------------------------------------------

def test_count_line_counts_groups_and_postings(env):
    client, conn = env
    a, b, c = (seed(conn, i, i, title=f"Dev {i}") for i in (1, 2, 3))
    seed(conn, 4, 4, title="Solo")
    group(conn, a, b, c)
    html = client.get("/").text
    assert '<p class="count muted">2 offers (4 postings)</p>' in html
    solo_only = client.get("/", params={"q": "Solo"}).text
    assert '<p class="count muted">1 offer</p>' in solo_only        # no "(N postings)" when equal
    one_group = client.get("/", params={"q": "Dev"}).text
    assert '<p class="count muted">1 offer (3 postings)</p>' in one_group


def test_pagination_works_on_groups(env):
    client, conn = env
    for i in range(25):                                   # 25 groups of 2 postings, Role-00 newest
        a = seed(conn, f"a{i}", i * 2, title=f"Role-{i:02d}")
        b = seed(conn, f"b{i}", i * 2 + 1, title=f"Role-{i:02d} copy", site="indeed")
        group(conn, a, b)
    p1 = client.get("/").text
    assert '<p class="count muted">25 offers (50 postings)</p>' in p1
    assert [t for t, _ in rows(p1)] == [f"Role-{i:02d}" for i in range(20)]
    assert all(m == [f"{t} copy"] for t, m in rows(p1))
    assert "Page 1 of 2" in p1
    p2 = client.get("/", params={"page": 2}).text
    assert [t for t, _ in rows(p2)] == [f"Role-{i:02d}" for i in range(20, 25)]
    assert "Page 2 of 2" in p2
    assert 'rel="prev"' in p2 and "page=1" in section(p2, 'rel="prev"', ">")
    assert client.get("/", params={"page": 99}).text.count('<details class="dups"') == 5   # clamped to the last page


# --- G3 hide_dups --------------------------------------------------------------

def test_hide_dups_is_accepted_and_equals_the_default(env):
    client, conn = env
    a, b = seed(conn, 1, 1, title="Dev A"), seed(conn, 2, 2, title="Dev B")
    group(conn, a, b)
    plain, old = client.get("/"), client.get("/", params={"hide_dups": "1"})
    assert old.status_code == 200
    assert rows(old.text) == rows(plain.text) == [("Dev A", ["Dev B"])]
    assert "(2 postings)" in old.text
    assert "Reset filters" not in old.text                       # not an active filter any more
    assert 'name="hide_dups"' not in old.text and "hide duplicates" not in old.text


# --- G4 unread -----------------------------------------------------------------

def test_unread_member_marks_the_group(env):
    client, conn = env
    a = seed(conn, 1, 1, title="Rep", status="seen")
    b = seed(conn, 2, 2, title="Unread member", status="new")
    c = seed(conn, 3, 3, title="Read member", status="seen")
    group(conn, a, b, c)
    html = client.get("/").text
    chunk = section(html, '<li class="job ', '<details class="dups"')
    assert 'class="unread-dot"' in chunk and "status-seen" in chunk       # rep carries the group marker
    assert "2 more postings" in summary(html) and "1 unread" in summary(html)
    members = section(html, '<ul class="members">', "</ul>")
    assert members.count("unread-dot") == 1
    # opening the detail page of the unread member leaves the others alone
    client.get(f"/jobs/{b}")
    assert conn.execute("SELECT status FROM jobs WHERE id IN (?, ?) ORDER BY id", (a, c)).fetchall()[0][0] == "seen"
    html = client.get("/").text
    assert "unread" not in summary(html) and "unread-dot" not in section(html, 'id="job-list"', "</ol>")


def test_fully_read_group_has_no_marker_and_unread_rep_is_enough(env):
    client, conn = env
    a = seed(conn, 1, 1, title="Rep", status="new")
    b = seed(conn, 2, 2, title="Member", status="seen")
    group(conn, a, b)
    html = client.get("/").text
    assert "unread" not in summary(html)
    assert section(html, '<li class="job ', '<details class="dups"').count("unread-dot") == 1


# --- G5 pinned -----------------------------------------------------------------

def test_pinned_job_stays_out_of_groups_and_the_rest_still_group(env):
    client, conn = env
    a = seed(conn, 1, 1, title="Pinned dup")
    b = seed(conn, 2, 2, title="Dev B")
    c = seed(conn, 3, 3, title="Dev C")
    group(conn, a, b, c)
    client.post(f"/jobs/{a}/pin", data={"pinned": "1", "next": "/"}, follow_redirects=False)
    html = client.get("/").text
    assert pinned_titles(html) == ["Pinned dup"]
    assert rows(html) == [("Dev B", ["Dev C"])]
    assert '<p class="count muted">1 offer (2 postings)</p>' in html


# --- no N+1 ---------------------------------------------------------------------

def test_query_count_does_not_grow_with_the_number_of_groups(tmp_path, monkeypatch):
    client, conn = make_app(tmp_path, stack=["Kotlin"])
    seen = []

    def traced(path):
        c = connect(path)
        c.set_trace_callback(lambda sql: seen.append(sql))
        return c

    monkeypatch.setattr(web, "connect", traced)

    def count_queries():
        seen.clear()
        assert client.get("/").status_code == 200
        return len(seen)

    for i in range(2):
        group(conn, seed(conn, f"a{i}", i, stack="Kotlin"), seed(conn, f"b{i}", i + 50))
    few = count_queries()
    for i in range(2, 20):
        group(conn, seed(conn, f"a{i}", i, stack="Kotlin"), seed(conn, f"b{i}", i + 50), seed(conn, f"c{i}", i + 90))
    assert count_queries() == few


def test_stack_chip_counts_count_groups_not_postings(env):
    client, conn = env
    a = seed(conn, 1, 1, title="Dev A", stack="Kotlin")
    b = seed(conn, 2, 2, title="Dev B", stack="Kotlin", site="indeed")
    seed(conn, 3, 3, title="Other", stack="Kotlin")
    group(conn, a, b)
    html = client.get("/").text
    assert re.search(r'name="stack" value="kotlin"[^>]*><span class="chip-name">Kotlin</span><span class="chip-count">2</span>', html)
    assert "2 offers (3 postings)" in html
    assert "2 offers" in client.get("/", params={"stack": "kotlin"}).text     # the chip's number is what you get
