import re
import pytest
from fastapi.testclient import TestClient

from jobfinder import applications as apps
from jobfinder.db import connect
from jobfinder.web import create_app
from test_web import NOW, make_cfg
from test_applications import make_job

DEFAULTS = ["Applied", "Recruiter screen", "Hiring manager", "Technical", "Final", "Offer"]


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    return client, conn


def phases(conn):
    return [(r["id"], r["name"], r["position"]) for r in conn.execute("SELECT * FROM phases ORDER BY position")]


def names(conn):
    return [p[1] for p in phases(conn)]


def pid(conn, name):
    return conn.execute("SELECT id FROM phases WHERE name = ?", (name,)).fetchone()[0]


# --- page and nav ------------------------------------------------------------------

def test_settings_page_lists_phases_in_order_with_counts(env):
    client, conn = env
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW.isoformat())
    conn.commit()
    html = client.get("/applications/phases").text
    positions = [html.index(f">{n}</span>") for n in DEFAULTS]
    assert positions == sorted(positions)
    assert "1 application<" in html and "0 applications" in html


def test_header_nav_links_jobs_and_applications(env):
    client, conn = env
    make_job(conn)
    for url in ("/", "/jobs/1", "/applications/phases"):
        html = client.get(url).text
        assert 'class="main-nav"' in html
        assert re.search(r'<a href="/" data-keep-filters( aria-current="page")?>Jobs</a>', html)
        assert 'href="/applications"' in html


# --- add ------------------------------------------------------------------------------

def test_add_phase_appends_and_redirects(env):
    client, conn = env
    r = client.post("/applications/phases", data={"name": "  Take-home  "})
    assert (r.status_code, r.headers["location"]) == (303, "/applications/phases")
    assert phases(conn)[-1][1:] == ("Take-home", 7)


@pytest.mark.parametrize("name", ["", "   ", "x" * 41, "applied", "  OFFER "])
def test_add_phase_rejects_invalid_names_and_leaves_db_unchanged(env, name):
    client, conn = env
    before = phases(conn)
    r = client.post("/applications/phases", data={"name": name})
    assert r.status_code == 400
    assert 'role="alert"' in r.text
    assert phases(conn) == before


def test_add_phase_accepts_40_chars(env):
    client, conn = env
    assert client.post("/applications/phases", data={"name": "x" * 40}).status_code == 303
    assert names(conn)[-1] == "x" * 40


def test_add_phase_missing_field_is_400(env):
    client, conn = env
    assert client.post("/applications/phases", data={}).status_code == 400
    assert names(conn) == DEFAULTS


# --- delete ---------------------------------------------------------------------------

def test_delete_unused_phase_compacts_positions(env):
    client, conn = env
    r = client.post(f"/applications/phases/{pid(conn, 'Hiring manager')}/delete")
    assert (r.status_code, r.headers["location"]) == (303, "/applications/phases")
    assert [(p[1], p[2]) for p in phases(conn)] == [
        ("Applied", 1), ("Recruiter screen", 2), ("Technical", 3), ("Final", 4), ("Offer", 5)]


def test_delete_phase_in_use_is_refused_with_message(env):
    client, conn = env
    for ext in ("a", "b"):
        apps.create_application_for_job(conn, make_job(conn, ext), NOW.isoformat())
    conn.commit()
    before = phases(conn)
    r = client.post(f"/applications/phases/{pid(conn, 'Applied')}/delete")
    assert r.status_code == 409
    assert "Move the 2 applications in Applied to another phase first" in r.text
    assert phases(conn) == before


def test_delete_phase_keeps_timeline_phase_names(env):
    client, conn = env
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW.isoformat())
    apps.move_application(conn, app_id, pid(conn, "Technical"), NOW.isoformat())
    apps.move_application(conn, app_id, pid(conn, "Offer"), NOW.isoformat())
    conn.commit()
    assert client.post(f"/applications/phases/{pid(conn, 'Technical')}/delete").status_code == 303
    assert "Technical" not in names(conn)
    entries = [r[0] for r in conn.execute("SELECT phase_name FROM timeline_entries ORDER BY id")]
    assert entries == ["Applied", "Technical", "Offer"]


def test_delete_unknown_phase_is_404(env):
    client, conn = env
    assert client.post("/applications/phases/999/delete").status_code == 404
    assert names(conn) == DEFAULTS


def test_phases_can_all_be_removed_and_are_not_reseeded(env, tmp_path):
    client, conn = env
    for p in phases(conn):
        assert client.post(f"/applications/phases/{p[0]}/delete").status_code == 303
    assert phases(connect(conn.execute("PRAGMA database_list").fetchone()[2])) == []
    assert "No phases yet" in client.get("/applications/phases").text


# --- move up/down --------------------------------------------------------------------

def test_move_down_and_up_swap_with_neighbour(env):
    client, conn = env
    r = client.post(f"/applications/phases/{pid(conn, 'Applied')}/move", data={"direction": "down"})
    assert (r.status_code, r.headers["location"]) == (303, "/applications/phases")
    assert names(conn)[:3] == ["Recruiter screen", "Applied", "Hiring manager"]
    client.post(f"/applications/phases/{pid(conn, 'Applied')}/move", data={"direction": "up"})
    assert names(conn) == DEFAULTS
    assert [p[2] for p in phases(conn)] == [1, 2, 3, 4, 5, 6]


def test_move_at_the_ends_is_a_noop(env):
    client, conn = env
    before = phases(conn)
    assert client.post(f"/applications/phases/{pid(conn, 'Applied')}/move", data={"direction": "up"}).status_code == 303
    assert client.post(f"/applications/phases/{pid(conn, 'Offer')}/move", data={"direction": "down"}).status_code == 303
    assert phases(conn) == before


def test_move_with_bad_direction_or_unknown_phase(env):
    client, conn = env
    before = phases(conn)
    assert client.post(f"/applications/phases/{pid(conn, 'Final')}/move", data={"direction": "left"}).status_code == 400
    assert client.post(f"/applications/phases/{pid(conn, 'Final')}/move", data={}).status_code == 400
    assert client.post("/applications/phases/999/move", data={"direction": "up"}).status_code == 404
    assert phases(conn) == before


# --- order ----------------------------------------------------------------------------

def test_order_accepts_an_exact_permutation(env):
    client, conn = env
    ids = [p[0] for p in phases(conn)]
    new = list(reversed(ids))
    r = client.post("/applications/phases/order", data={"order": ",".join(map(str, new))})
    assert (r.status_code, r.headers["location"]) == (303, "/applications/phases")
    assert [p[0] for p in phases(conn)] == new
    assert [p[2] for p in phases(conn)] == [1, 2, 3, 4, 5, 6]


def bad_orders(ids):
    return [
        ",".join(map(str, ids[:-1])),                      # missing one
        ",".join(map(str, ids + [999])),                   # unknown id
        ",".join(map(str, ids[:-1] + [ids[0]])),           # duplicate
        ",".join(map(str, ids + [ids[0]])),                # extra duplicate
        "a,b",                                              # not numbers
        "",                                                 # empty
    ]


def test_order_rejects_anything_but_a_permutation(env):
    client, conn = env
    before = phases(conn)
    for order in bad_orders([p[0] for p in before]):
        r = client.post("/applications/phases/order", data={"order": order})
        assert r.status_code == 400, order
        assert phases(conn) == before, order
    assert client.post("/applications/phases/order", data={}).status_code == 400


# --- rename ----------------------------------------------------------------------------

def entries(conn):
    return [(r["id"], r["application_id"], r["phase_name"], r["phase_id"])
            for r in conn.execute("SELECT * FROM timeline_entries ORDER BY id")]


def rename(client, conn, old, name, **kw):
    return client.post(f"/applications/phases/{pid(conn, old)}/rename", data={"name": name}, **kw)


@pytest.fixture
def history(env):
    """Two applications moved around, plus a note on a phase and an entry not linked to any phase."""
    client, conn = env
    a = apps.create_application_for_job(conn, make_job(conn, "a"), NOW.isoformat())
    b = apps.create_application_for_job(conn, make_job(conn, "b"), NOW.isoformat())
    apps.move_application(conn, a, pid(conn, "Technical"), NOW.isoformat())
    apps.move_application(conn, b, pid(conn, "Final"), NOW.isoformat())
    apps.add_entry(conn, a, str(pid(conn, "Technical")), "2026-11-05T10:00", "call went well")
    conn.execute("INSERT INTO timeline_entries (application_id, phase_name, happened_at, notes, kind) "
                 "VALUES (?, 'Technical', '2026-11-06T09:00:00', 'legacy note', 'note')", (a,))
    conn.commit()
    return client, conn, a, b


def test_phases_page_has_a_rename_form_per_phase(env):
    client, conn = env
    html = client.get("/applications/phases").text
    for p in phases(conn):
        form = re.search(rf'<form[^>]*action="/applications/phases/{p[0]}/rename".*?</form>', html, re.S)
        assert form, p
        assert 'method="post"' in form.group(0)
        assert f'value="{p[1]}"' in form.group(0)
        assert 'name="name"' in form.group(0) and 'maxlength="40"' in form.group(0)
        assert "SAVE" in form.group(0)


def test_rename_updates_the_name_and_redirects(env):
    client, conn = env
    before = phases(conn)
    r = rename(client, conn, "Technical", "  Coding interview  ")
    assert (r.status_code, r.headers["location"]) == (303, "/applications/phases")
    after = phases(conn)
    assert [(p[0], p[2]) for p in after] == [(p[0], p[2]) for p in before]  # same ids and order
    assert names(conn) == ["Applied", "Recruiter screen", "Hiring manager", "Coding interview", "Final", "Offer"]
    html = client.get("/applications/phases").text
    assert ">Coding interview</span>" in html and ">Technical</span>" not in html


def test_rename_shows_the_new_name_everywhere_including_past_entries(history):
    client, conn, a, b = history
    assert rename(client, conn, "Technical", "Coding").status_code == 303
    page = client.get(f"/applications/{a}").text
    shown = re.findall(r'class="entry-phase">([^<]*)<', page)
    assert sorted(shown) == ["Coding", "Technical"]   # notes: only the unlinked legacy one keeps the old name
    assert re.search(r"Moved to </span>Coding", page)             # the move entry
    assert page.count("Coding") >= 3            # Move to selector, move entry, note entry
    assert conn.execute("SELECT notes FROM timeline_entries WHERE kind = 'move' AND phase_id = ?",
                        (pid(conn, "Coding"),)).fetchone()[0] == "Moved to Coding"
    board = client.get("/applications").text
    assert "Coding" in board and ">Technical<" not in board
    assert 'name="phase"' in board and "Coding" in board   # filter chip
    # stored entries: this phase's linked entries rewritten, nothing else
    got = entries(conn)
    tech = pid(conn, "Coding")
    assert [e[2] for e in got if e[3] == tech] == ["Coding", "Coding"]
    assert (a, "Technical", None) in [(e[1], e[2], e[3]) for e in got]   # unlinked entry untouched


def test_rename_leaves_other_phases_and_unlinked_entries_alone(history):
    client, conn, a, b = history
    before = entries(conn)
    final = pid(conn, "Final")
    rename(client, conn, "Technical", "Coding")
    after = entries(conn)
    assert len(after) == len(before)
    for old, new in zip(before, after):
        if old[3] == pid(conn, "Coding"):
            assert new == (old[0], old[1], "Coding", old[3])
        else:
            assert new == old
    assert [e[2] for e in after if e[3] == final] == ["Final"]
    unlinked = [e for e in after if e[3] is None]
    assert [(e[1], e[2]) for e in unlinked] == [(a, "Technical")]


def test_rename_to_own_name_or_only_capitalization_is_allowed(history):
    client, conn, a, b = history
    assert rename(client, conn, "Technical", "Technical").status_code == 303
    assert "Technical" in names(conn)
    assert rename(client, conn, "Technical", " TECHNICAL ").status_code == 303
    assert "TECHNICAL" in names(conn) and "Technical" not in names(conn)
    assert [e[2] for e in entries(conn) if e[3] == pid(conn, "TECHNICAL")] == ["TECHNICAL", "TECHNICAL"]


def test_rename_accepts_40_chars(env):
    client, conn = env
    assert rename(client, conn, "Final", "y" * 40).status_code == 303
    assert "y" * 40 in names(conn)


@pytest.mark.parametrize("name,message", [
    ("", "Phase name is required"),
    ("   ", "Phase name is required"),
    ("x" * 41, "Phase name must be at most 40 characters"),
    ("applied", "A phase named applied already exists"),
    ("  OFFER ", "A phase named OFFER already exists"),
])
def test_rename_refuses_invalid_names_and_changes_nothing(history, name, message):
    client, conn, a, b = history
    before_phases, before_entries = phases(conn), entries(conn)
    r = rename(client, conn, "Technical", name)
    assert r.status_code == 400
    assert 'role="alert"' in r.text and message in r.text
    assert phases(conn) == before_phases
    assert entries(conn) == before_entries


def test_rename_missing_name_is_400(history):
    client, conn, a, b = history
    before = (phases(conn), entries(conn))
    r = client.post(f"/applications/phases/{pid(conn, 'Technical')}/rename", data={})
    assert r.status_code == 400
    assert (phases(conn), entries(conn)) == before


def test_rename_unknown_phase_is_404(history):
    client, conn, a, b = history
    before = (phases(conn), entries(conn))
    r = client.post("/applications/phases/999/rename", data={"name": "Whatever"})
    assert r.status_code == 404
    assert (phases(conn), entries(conn)) == before


def test_rename_cross_site_post_is_refused(history):
    client, conn, a, b = history
    before = (phases(conn), entries(conn))
    r = rename(client, conn, "Technical", "Hacked", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert (phases(conn), entries(conn)) == before
    ok = rename(client, conn, "Technical", "Fine", headers={"origin": "http://testserver"})
    assert ok.status_code == 303


def test_rename_a_removed_then_reused_name_does_not_touch_old_history(env):
    """A removed phase's entries keep their name and are never linked to a new phase that reuses it."""
    client, conn = env
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW.isoformat())
    apps.move_application(conn, app_id, pid(conn, "Technical"), NOW.isoformat())
    apps.move_application(conn, app_id, pid(conn, "Offer"), NOW.isoformat())
    conn.commit()
    client.post(f"/applications/phases/{pid(conn, 'Technical')}/delete")
    client.post("/applications/phases", data={"name": "Technical"})
    rename(client, conn, "Technical", "Take-home")
    assert [e[2] for e in entries(conn)] == ["Applied", "Technical", "Offer"]


# --- history survives deletes, other phase actions keep working -------------------------------

def test_delete_phase_keeps_entries_and_name_and_unlinks_them(history):
    client, conn, a, b = history
    tech = pid(conn, "Technical")
    apps.move_application(conn, a, pid(conn, "Offer"), NOW.isoformat())
    conn.commit()
    before = entries(conn)
    assert client.post(f"/applications/phases/{tech}/delete").status_code == 303
    after = entries(conn)
    assert len(after) == len(before)
    assert [(e[0], e[2]) for e in after] == [(e[0], e[2]) for e in before]
    assert not [e for e in after if e[3] == tech]
    # a new phase that reuses the freed id never inherits the old history
    new_id = apps.add_phase(conn, "Another")
    conn.commit()
    assert not [e for e in entries(conn) if e[3] == new_id]
    rename_r = client.post(f"/applications/phases/{new_id}/rename", data={"name": "Technical"})
    assert rename_r.status_code == 303
    assert [e[2] for e in entries(conn)] == [e[2] for e in before]


def test_writers_record_the_phase_id_with_the_name(env):
    client, conn = env
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW.isoformat())
    apps.move_application(conn, app_id, pid(conn, "Final"), NOW.isoformat())
    apps.add_entry(conn, app_id, str(pid(conn, "Offer")), "2026-11-05T10:00", "n")
    note = conn.execute("SELECT max(id) FROM timeline_entries").fetchone()[0]
    assert [(e[2], e[3]) for e in entries(conn)] == [
        ("Applied", pid(conn, "Applied")), ("Final", pid(conn, "Final")), ("Offer", pid(conn, "Offer"))]
    apps.update_entry(conn, app_id, note, str(pid(conn, "Hiring manager")), "2026-11-05T10:00", "n")
    assert entries(conn)[-1][2:] == ("Hiring manager", pid(conn, "Hiring manager"))


def test_editing_a_note_of_a_removed_phase_keeps_name_and_no_link(env):
    client, conn = env
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW.isoformat())
    apps.add_entry(conn, app_id, str(pid(conn, "Technical")), "2026-11-05T10:00", "n")
    note = conn.execute("SELECT max(id) FROM timeline_entries").fetchone()[0]
    conn.commit()
    client.post(f"/applications/phases/{pid(conn, 'Technical')}/delete")
    apps.update_entry(conn, app_id, note, "keep", "2026-11-05T11:00", "edited")
    conn.commit()
    assert entries(conn)[-1][2:] == ("Technical", None)


def test_add_remove_reorder_and_move_still_work_after_a_rename(env):
    client, conn = env
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW.isoformat())
    conn.commit()
    assert rename(client, conn, "Final", "Last round").status_code == 303
    assert client.post("/applications/phases", data={"name": "Take-home"}).status_code == 303
    ids = [p[0] for p in phases(conn)]
    assert client.post("/applications/phases/order", data={"order": ",".join(map(str, reversed(ids)))}).status_code == 303
    assert names(conn)[0] == "Take-home" and names(conn)[-1] == "Applied"
    assert client.post(f"/applications/phases/{pid(conn, 'Take-home')}/move", data={"direction": "down"}).status_code == 303
    assert client.post(f"/applications/phases/{pid(conn, 'Take-home')}/delete").status_code == 303
    assert "Take-home" not in names(conn)
    r = client.post(f"/applications/{app_id}/move", data={"phase_id": str(pid(conn, "Last round")), "next": "/applications"})
    assert r.status_code == 303
    assert entries(conn)[-1][2:] == ("Last round", pid(conn, "Last round"))
