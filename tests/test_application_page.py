import re

import pytest
from fastapi.testclient import TestClient

from jobfinder import applications as apps
from jobfinder.db import connect
from jobfinder.web import create_app
from test_board import APPLIED, add_note, columns, make_app, pid, snapshot
from test_web import NOW, make_cfg


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    return client, conn


def entries(conn, app_id=None):
    sql = "SELECT * FROM timeline_entries"
    args = ()
    if app_id:
        sql += " WHERE application_id = ?"
        args = (app_id,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


def note_ids(conn):
    return [e["id"] for e in entries(conn) if e["kind"] == "note"]


def good(conn, **over):
    data = {"phase_id": pid(conn, "Technical"), "happened_at": "2026-11-10T14:00", "notes": "Pairing round"}
    data.update(over)
    return data


# --- page ---------------------------------------------------------------------------------

def test_unknown_application_is_404(env):
    client, _ = env
    assert client.get("/applications/999").status_code == 404


def test_header_shows_title_company_job_link_posting_link_and_date(env):
    client, conn = env
    app_id = make_app(conn, "a", title="Spring Engineer", company="Globex", url="https://jobs.example/1")
    job_id = conn.execute("SELECT job_id FROM applications").fetchone()[0]
    html = client.get(f"/applications/{app_id}").text
    assert "Spring Engineer" in html and "Globex" in html
    assert f'href="/jobs/{job_id}"' in html
    assert re.search(r'<a [^>]*href="https://jobs.example/1"[^>]*>Open posting ↗</a>', html)
    assert "2026-11-01" in html


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "ftp://x/y", "data:text/html,hi"])
def test_open_posting_only_for_http_urls(env, url):
    client, conn = env
    app_id = make_app(conn, "a", url=url)
    html = client.get(f"/applications/{app_id}").text
    assert "Open posting" not in html
    assert "javascript:alert" not in html


def test_page_has_phase_and_outcome_selectors_with_current_values(env):
    client, conn = env
    app_id = make_app(conn, "a", phase="Technical", outcome="ghosted")
    html = client.get(f"/applications/{app_id}").text
    phase_form = re.search(r'<form[^>]*action="/applications/%d/move".*?</form>' % app_id, html, re.S).group(0)
    assert f'<option value="{pid(conn, "Technical")}" selected>' in phase_form
    assert f'name="next" value="/applications/{app_id}"' in phase_form
    outcome_form = re.search(r'<form[^>]*action="/applications/%d/outcome".*?</form>' % app_id, html, re.S).group(0)
    assert re.findall(r'<option value="([^"]+)"', outcome_form) == list(apps.OUTCOMES)
    assert '<option value="ghosted" selected>' in outcome_form


def test_page_for_unassigned_application_renders(env):
    client, conn = env
    app_id = make_app(conn, "a", phase=None)
    assert client.get(f"/applications/{app_id}").status_code == 200


# --- phase selector (same move route) --------------------------------------------------------

def test_phase_selector_moves_and_returns_to_the_page(env):
    client, conn = env
    app_id = make_app(conn, "a")
    r = client.post(f"/applications/{app_id}/move",
                    data={"phase_id": pid(conn, "Final"), "next": f"/applications/{app_id}"})
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{app_id}")
    assert "Final" in client.get(f"/applications/{app_id}").text


# --- outcome ----------------------------------------------------------------------------------

def test_outcome_updates_and_redirects_to_the_page(env):
    client, conn = env
    app_id = make_app(conn, "a", phase="Technical")
    r = client.post(f"/applications/{app_id}/outcome", data={"outcome": "rejected"})
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{app_id}")
    row = conn.execute("SELECT phase_id, outcome FROM applications").fetchone()
    assert (row["phase_id"], row["outcome"]) == (pid(conn, "Technical"), "rejected")
    assert "Dev" in columns(client.get("/applications").text)["closed"]


@pytest.mark.parametrize("outcome", ["hired", "", "ACTIVE"])
def test_invalid_outcome_is_400_and_unchanged(env, outcome):
    client, conn = env
    app_id = make_app(conn, "a")
    before = snapshot(conn)
    r = client.post(f"/applications/{app_id}/outcome", data={"outcome": outcome})
    assert r.status_code == 400
    assert 'role="alert"' in r.text
    assert client.post(f"/applications/{app_id}/outcome").status_code == 400
    assert snapshot(conn) == before


def test_outcome_unknown_application_is_404(env):
    client, _ = env
    assert client.post("/applications/999/outcome", data={"outcome": "active"}).status_code == 404


# --- add entry --------------------------------------------------------------------------------

def test_add_entry_snapshots_phase_name_and_redirects(env):
    client, conn = env
    app_id = make_app(conn, "a")
    r = client.post(f"/applications/{app_id}/entries", data=good(conn))
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{app_id}")
    e = entries(conn)[-1]
    assert (e["application_id"], e["phase_name"], e["happened_at"], e["notes"], e["kind"]) == (
        app_id, "Technical", "2026-11-10T14:00:00", "Pairing round", "note")
    assert conn.execute("SELECT phase_id FROM applications").fetchone()[0] == pid(conn, "Applied")  # no move


def test_several_entries_on_the_same_phase_are_allowed(env):
    client, conn = env
    app_id = make_app(conn, "a")
    for notes in ("round 1", "round 2"):
        assert client.post(f"/applications/{app_id}/entries", data=good(conn, notes=notes)).status_code == 303
    assert [e["notes"] for e in entries(conn) if e["kind"] == "note"] == ["round 1", "round 2"]


def test_entry_notes_are_optional_and_crlf_is_normalised(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/entries", data=good(conn, notes=""))
    client.post(f"/applications/{app_id}/entries", data=good(conn, notes="a\r\nb"))
    assert [e["notes"] for e in entries(conn) if e["kind"] == "note"] == ["", "a\nb"]


def test_notes_limit_is_5000_characters(env):
    client, conn = env
    app_id = make_app(conn, "a")
    assert client.post(f"/applications/{app_id}/entries", data=good(conn, notes="x" * 5000)).status_code == 303
    before = entries(conn)
    assert client.post(f"/applications/{app_id}/entries", data=good(conn, notes="x" * 5001)).status_code == 400
    assert entries(conn) == before


@pytest.mark.parametrize("over", [
    {"phase_id": ""}, {"phase_id": "999"}, {"phase_id": "abc"},
    {"happened_at": ""}, {"happened_at": "garbage"}, {"happened_at": "2026-13-45T10:00"},
    {"happened_at": "2026-11-10"},
])
def test_add_entry_invalid_input_is_400_and_unchanged(env, over):
    client, conn = env
    app_id = make_app(conn, "a")
    before = entries(conn)
    r = client.post(f"/applications/{app_id}/entries", data=good(conn, **over))
    assert r.status_code == 400
    assert 'role="alert"' in r.text
    assert entries(conn) == before


def test_add_entry_missing_fields_is_400_and_unknown_application_404(env):
    client, conn = env
    app_id = make_app(conn, "a")
    before = entries(conn)
    assert client.post(f"/applications/{app_id}/entries", data={}).status_code == 400
    assert client.post("/applications/999/entries", data=good(conn)).status_code == 404
    assert entries(conn) == before


def test_entry_keeps_phase_name_after_the_phase_is_removed(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/entries", data=good(conn, phase_id=pid(conn, "Offer")))
    client.post(f"/applications/phases/{pid(conn, 'Offer')}/delete")
    html = client.get(f"/applications/{app_id}").text
    assert "Offer" in html and "Pairing round" in html


# --- timeline display -----------------------------------------------------------------------

def test_timeline_is_newest_first_with_phase_date_and_notes(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-02T10:00", "FIRSTNOTE", phase="Recruiter screen")
    add_note(conn, app_id, "2026-11-09T15:30", "LASTNOTE", phase="Technical")
    add_note(conn, app_id, "2026-11-05T11:00", "MIDNOTE", phase="Final")
    html = client.get(f"/applications/{app_id}").text
    order = [html.index(n) for n in ("LASTNOTE", "MIDNOTE", "FIRSTNOTE")]
    assert order == sorted(order)
    entry = re.search(r'<li class="entry[^"]*"[^>]*>(?:(?!</li>).)*LASTNOTE.*?</li>', html, re.S).group(0)
    assert "Technical" in entry and "2026-11-09 15:30" in entry


def test_move_entries_are_muted_and_note_entries_are_not(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/move", data={"phase_id": pid(conn, "Final")})
    client.post(f"/applications/{app_id}/entries", data=good(conn))
    html = client.get(f"/applications/{app_id}").text
    items = re.findall(r'<li class="(entry[^"]*)"', html)
    assert sorted(items) == ["entry entry-move", "entry entry-move", "entry entry-note"]


def test_notes_are_escaped_plain_text_with_line_breaks_preserved(env):
    client, conn = env
    app_id = make_app(conn, "a")
    notes = "<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n**bold** [l](javascript:alert(3))"
    client.post(f"/applications/{app_id}/entries", data=good(conn, notes=notes))
    html = client.get(f"/applications/{app_id}").text
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;\n&lt;img src=x onerror=alert(2)&gt;" in html
    assert "**bold**" in html and "<strong>" not in html and "<a href=\"javascript" not in html
    assert re.search(r'class="entry-notes"[^>]*>&lt;script&gt;', html)


def test_job_title_and_company_are_escaped_on_the_page(env):
    client, conn = env
    app_id = make_app(conn, "a", title="<b>t</b>", company="<i>c</i>")
    html = client.get(f"/applications/{app_id}").text
    assert "<b>t</b>" not in html and "<i>c</i>" not in html


def test_edit_form_only_for_notes_and_delete_for_every_entry(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/entries", data=good(conn))
    move_id, note_id = entries(conn)[0]["id"], entries(conn)[-1]["id"]
    html = client.get(f"/applications/{app_id}").text
    assert f'action="/applications/{app_id}/entries/{note_id}"' in html
    assert f'action="/applications/{app_id}/entries/{move_id}"' not in html
    assert f'action="/applications/{app_id}/entries/{note_id}/delete"' in html
    assert f'action="/applications/{app_id}/entries/{move_id}/delete"' in html
    assert 'type="datetime-local"' in html and 'maxlength="5000"' in html


def test_edit_form_prefills_datetime_local_value(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/entries", data=good(conn, happened_at="2026-11-10T14:05"))
    assert 'value="2026-11-10T14:05:00"' in client.get(f"/applications/{app_id}").text


# --- edit entry -------------------------------------------------------------------------------

def make_note(client, conn, app_id):
    client.post(f"/applications/{app_id}/entries", data=good(conn))
    return note_ids(conn)[-1]


def test_edit_entry_updates_phase_snapshot_date_and_notes(env):
    client, conn = env
    app_id = make_app(conn, "a")
    eid = make_note(client, conn, app_id)
    r = client.post(f"/applications/{app_id}/entries/{eid}",
                    data={"phase_id": pid(conn, "Final"), "happened_at": "2026-11-11T09:15", "notes": "edited"})
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{app_id}")
    e = [e for e in entries(conn) if e["id"] == eid][0]
    assert (e["phase_name"], e["happened_at"], e["notes"], e["kind"]) == (
        "Final", "2026-11-11T09:15:00", "edited", "note")


@pytest.mark.parametrize("over", [
    {"phase_id": "999"}, {"phase_id": ""}, {"happened_at": ""}, {"happened_at": "nope"}, {"notes": "x" * 5001},
])
def test_edit_entry_invalid_is_400_and_unchanged(env, over):
    client, conn = env
    app_id = make_app(conn, "a")
    eid = make_note(client, conn, app_id)
    before = entries(conn)
    assert client.post(f"/applications/{app_id}/entries/{eid}", data=good(conn, **over)).status_code == 400
    assert entries(conn) == before


def test_move_entries_cannot_be_edited(env):
    client, conn = env
    app_id = make_app(conn, "a")
    move_id = entries(conn)[0]["id"]
    before = entries(conn)
    assert client.post(f"/applications/{app_id}/entries/{move_id}", data=good(conn)).status_code == 400
    assert entries(conn) == before


def test_entry_of_another_application_is_404_for_edit_and_delete(env):
    client, conn = env
    one, two = make_app(conn, "a"), make_app(conn, "b")
    eid = make_note(client, conn, one)
    before = entries(conn)
    assert client.post(f"/applications/{two}/entries/{eid}", data=good(conn)).status_code == 404
    assert client.post(f"/applications/{two}/entries/{eid}/delete").status_code == 404
    assert client.post(f"/applications/{one}/entries/999", data=good(conn)).status_code == 404
    assert client.post(f"/applications/{one}/entries/999/delete").status_code == 404
    assert client.post(f"/applications/999/entries/{eid}", data=good(conn)).status_code == 404
    assert entries(conn) == before


# --- delete entry -----------------------------------------------------------------------------

def test_delete_note_entry(env):
    client, conn = env
    app_id = make_app(conn, "a")
    eid = make_note(client, conn, app_id)
    r = client.post(f"/applications/{app_id}/entries/{eid}/delete")
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{app_id}")
    assert eid not in [e["id"] for e in entries(conn)]


def test_delete_move_entry_keeps_the_phase(env):
    client, conn = env
    app_id = make_app(conn, "a", phase="Technical")
    move_id = entries(conn)[0]["id"]
    assert client.post(f"/applications/{app_id}/entries/{move_id}/delete").status_code == 303
    assert entries(conn) == []
    assert conn.execute("SELECT phase_id FROM applications").fetchone()[0] == pid(conn, "Technical")


def test_delete_only_touches_the_given_entry(env):
    client, conn = env
    app_id = make_app(conn, "a")
    first, second = make_note(client, conn, app_id), make_note(client, conn, app_id)
    client.post(f"/applications/{app_id}/entries/{first}/delete")
    assert second in [e["id"] for e in entries(conn)]


# --- seconds in timeline dates -------------------------------------------------------------------

@pytest.mark.parametrize("order", [("14:09:10", "14:09:40"), ("14:09:40", "14:09:10")])
def test_notes_in_the_same_minute_sort_by_their_seconds(env, order):
    client, conn = env
    app_id = make_app(conn, "a")
    names = {"14:09:10": "first", "14:09:40": "second"}
    for t in order:
        r = client.post(f"/applications/{app_id}/entries",
                        data=good(conn, happened_at=f"2026-11-10T{t}", notes=names[t]))
        assert r.status_code == 303
    stored = {e["notes"]: e["happened_at"] for e in entries(conn) if e["kind"] == "note"}
    assert stored == {"first": "2026-11-10T14:09:10", "second": "2026-11-10T14:09:40"}
    html = client.get(f"/applications/{app_id}").text
    assert html.index("second") < html.index("first")      # newest first, whatever the insertion order


def test_date_inputs_allow_seconds_and_default_to_now_with_seconds(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/entries", data=good(conn, happened_at="2026-11-10T14:05:07"))
    html = client.get(f"/applications/{app_id}").text
    inputs = re.findall(r'<input[^>]*type="datetime-local"[^>]*>', html)
    assert len(inputs) == 2                                     # composer + the note's edit form
    assert all('step="1"' in i for i in inputs)
    assert 'value="2026-11-04T10:00:00"' in inputs[0]            # now, with seconds
    assert 'value="2026-11-10T14:05:07"' in inputs[1]


def test_minutes_only_is_still_accepted_and_means_zero_seconds(env):
    client, conn = env
    app_id = make_app(conn, "a")
    assert client.post(f"/applications/{app_id}/entries", data=good(conn, happened_at="2026-11-10T14:09")).status_code == 303
    assert [e["happened_at"] for e in entries(conn) if e["kind"] == "note"] == ["2026-11-10T14:09:00"]


def test_fractional_seconds_are_truncated(env):
    client, conn = env
    app_id = make_app(conn, "a")
    assert client.post(f"/applications/{app_id}/entries",
                       data=good(conn, happened_at="2026-11-10T14:09:30.987")).status_code == 303
    assert [e["happened_at"] for e in entries(conn) if e["kind"] == "note"] == ["2026-11-10T14:09:30"]


def test_editing_a_note_can_set_seconds(env):
    client, conn = env
    app_id = make_app(conn, "a")
    eid = make_note(client, conn, app_id)
    r = client.post(f"/applications/{app_id}/entries/{eid}", data=good(conn, happened_at="2026-11-10T14:00:59"))
    assert r.status_code == 303
    assert [e["happened_at"] for e in entries(conn) if e["id"] == eid] == ["2026-11-10T14:00:59"]


@pytest.mark.parametrize("bad", ["2026-11-10T14:09:", "2026-11-10T14:09:61", "2026-11-10T14:09:5", "2026-11-10T14:09:xx",
                                 "2026-11-10T14:09:30:01", "2026-11-10T14:09:30.", "2026-11-10T14:09:30.5x"])
def test_malformed_seconds_are_refused_and_nothing_is_stored(env, bad):
    client, conn = env
    app_id = make_app(conn, "a")
    before = entries(conn)
    r = client.post(f"/applications/{app_id}/entries", data=good(conn, happened_at=bad))
    assert r.status_code == 400
    assert "Date and time are required, like 2026-11-10T14:00" in r.text
    assert entries(conn) == before
