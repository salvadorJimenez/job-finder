"""Fixes from the independent verification of the application tracker."""
import pytest
from fastapi.testclient import TestClient

from jobfinder.db import connect
from jobfinder.web import create_app
from test_board import add_note, make_app, pid
from test_web import NOW, make_cfg


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    return client, conn


# --- back links cannot leave the app ---------------------------------------------

@pytest.mark.parametrize("back", ["/\\evil.com", "/\t/evil.com", "/\n/evil.com", "/\x7f/evil.com"])
def test_back_link_rejects_backslash_and_control_characters(env, back):
    client, conn = env
    app_id = make_app(conn, "1")
    job_id = conn.execute("SELECT job_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
    html = client.get(f"/jobs/{job_id}", params={"back": back}).text
    assert 'class="back" href="/"' in html


def test_back_link_keeps_a_normal_list_url(env):
    client, conn = env
    app_id = make_app(conn, "1")
    job_id = conn.execute("SELECT job_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
    html = client.get(f"/jobs/{job_id}", params={"back": "/?site=indeed&page=2"}).text
    assert 'class="back" href="/?site=indeed&amp;page=2"' in html


# --- editing an entry whose phase was removed keeps its phase name -------------------

def test_edit_entry_of_removed_phase_keeps_phase_name(env):
    client, conn = env
    app_id = make_app(conn, "1")
    add_note(conn, app_id, "2026-11-10T14:00:00", "first notes", phase="Technical")
    entry_id = conn.execute("SELECT id FROM timeline_entries WHERE kind = 'note'").fetchone()[0]
    assert client.post(f"/applications/phases/{pid(conn, 'Technical')}/delete").status_code == 303

    html = client.get(f"/applications/{app_id}").text
    assert '<option value="keep" selected>Technical (removed)</option>' in html

    r = client.post(f"/applications/{app_id}/entries/{entry_id}",
                    data={"phase_id": "keep", "happened_at": "2026-11-10T14:00", "notes": "edited"})
    assert r.status_code == 303
    row = conn.execute("SELECT phase_name, notes FROM timeline_entries WHERE id = ?", (entry_id,)).fetchone()
    assert tuple(row) == ("Technical", "edited")


def test_keep_is_rejected_when_adding_a_new_entry(env):
    client, conn = env
    app_id = make_app(conn, "1")
    before = conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0]
    r = client.post(f"/applications/{app_id}/entries",
                    data={"phase_id": "keep", "happened_at": "2026-11-10T14:00", "notes": "x"})
    assert r.status_code == 400
    assert conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == before


# --- other websites cannot submit forms to the local app ---------------------------

def test_cross_site_post_is_rejected_and_changes_nothing(env):
    client, conn = env
    app_id = make_app(conn, "1")
    technical = pid(conn, "Technical")
    r = client.post(f"/applications/{app_id}/move", data={"phase_id": technical},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert conn.execute("SELECT phase_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0] != technical
    r = client.post("/applications/phases", data={"name": "Pwned"}, headers={"Referer": "https://evil.example/x"})
    assert r.status_code == 403
    assert conn.execute("SELECT count(*) FROM phases WHERE name = 'Pwned'").fetchone()[0] == 0


def test_same_origin_and_headerless_posts_still_work(env):
    client, conn = env
    r = client.post("/applications/phases", data={"name": "Take-home"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 303
    r = client.post("/applications/phases", data={"name": "Culture fit"})
    assert r.status_code == 303


def test_encoded_backslash_in_raw_query_is_rejected(env):
    client, conn = env
    app_id = make_app(conn, "1")
    job_id = conn.execute("SELECT job_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
    for raw in ("/%5Cevil.com", "/%09/evil.com"):
        html = client.get(f"/jobs/{job_id}?back={raw}").text
        assert 'class="back" href="/"' in html
