import sqlite3

import pytest

from jobfinder import applications as apps
from jobfinder.db import connect
from test_db import OLD_JOBS

NOW = "2026-11-09T10:00:00-06:00"
LATER = "2026-11-10T09:30:00-06:00"
DEFAULTS = ["Applied", "Recruiter screen", "Hiring manager", "Technical", "Final", "Offer"]


def phase_names(conn):
    return [r["name"] for r in conn.execute("SELECT name FROM phases ORDER BY position")]


def make_job(conn, ext="j1", status="new"):
    if not conn.execute("SELECT 1 FROM runs").fetchone():
        conn.execute("INSERT INTO runs (id, started_at, finished_at, status) VALUES (1, '2026-11-01T10:00:00', '2026-11-01T10:05:00', 'success')")
    cur = conn.execute(
        """INSERT INTO jobs (site, external_id, url, title, status, first_seen_run, first_seen_at, last_seen_at)
           VALUES ('linkedin', ?, 'https://x', 'Dev', ?, 1, '2026-11-01', '2026-11-01')""",
        (ext, status),
    )
    conn.commit()
    return cur.lastrowid


def old_db(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(OLD_JOBS)
    raw.commit()
    raw.close()
    return path


# --- schema / migration ----------------------------------------------------------

def test_fresh_database_has_tables_and_default_phases(tmp_path):
    conn = connect(tmp_path / "new.db")
    assert phase_names(conn) == DEFAULTS
    assert [r["position"] for r in conn.execute("SELECT position FROM phases ORDER BY position")] == [1, 2, 3, 4, 5, 6]
    for table in ("applications", "timeline_entries", "cv_files", "meta"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 0
    assert [r[1] for r in conn.execute("PRAGMA table_info(cv_files)")] == [
        "id", "original_name", "sha256", "stored_path", "uploaded_at"]


def test_old_database_gets_application_for_applied_job_and_keeps_data(tmp_path):
    path = old_db(tmp_path)
    conn = connect(path)
    job = conn.execute("SELECT id, title, status, first_seen_at FROM jobs").fetchone()
    assert (job["title"], job["status"], job["first_seen_at"]) == ("Old Row", "applied", "2026-11-01")
    app = conn.execute("SELECT * FROM applications").fetchone()
    first = conn.execute("SELECT id FROM phases ORDER BY position LIMIT 1").fetchone()[0]
    assert (app["job_id"], app["phase_id"], app["outcome"], app["cv_id"]) == (job["id"], first, "active", None)
    assert app["applied_at"]
    entry = conn.execute("SELECT * FROM timeline_entries").fetchone()
    assert (entry["kind"], entry["phase_name"], entry["application_id"]) == ("move", "Applied", app["id"])


def test_old_database_without_applied_jobs_gets_no_application(tmp_path):
    path = old_db(tmp_path)
    raw = sqlite3.connect(path)
    raw.execute("UPDATE jobs SET status = 'new'")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 0
    assert phase_names(conn) == DEFAULTS


def test_second_connect_is_idempotent(tmp_path):
    path = old_db(tmp_path)
    connect(path).close()
    conn = connect(path)
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == 1
    assert phase_names(conn) == DEFAULTS


def test_deleted_phases_are_not_reseeded(tmp_path):
    path = tmp_path / "new.db"
    conn = connect(path)
    with conn:
        conn.execute("DELETE FROM phases")
    conn.close()
    assert phase_names(connect(path)) == []

    conn = connect(path)
    with conn:
        conn.execute("DELETE FROM phases WHERE name != 'Offer'")
    conn.close()
    assert phase_names(connect(path)) == []  # nothing came back


def test_outcome_check_constraint_rejects_unknown_values(tmp_path):
    conn = connect(tmp_path / "new.db")
    job = make_job(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO applications (job_id, outcome, applied_at) VALUES (?, 'bogus', ?)", (job, NOW))


def test_one_application_per_job(tmp_path):
    conn = connect(tmp_path / "new.db")
    job = make_job(conn)
    conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (job, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (job, NOW))


# --- create / move / outcome -----------------------------------------------------

def test_create_application_uses_first_phase_and_records_entry(tmp_path):
    conn = connect(tmp_path / "new.db")
    job = make_job(conn)
    app_id = apps.create_application_for_job(conn, job, NOW)
    row = conn.execute("SELECT a.*, p.name FROM applications a JOIN phases p ON p.id = a.phase_id").fetchone()
    assert (row["id"], row["name"], row["applied_at"], row["outcome"]) == (app_id, "Applied", NOW, "active")
    entry = conn.execute("SELECT * FROM timeline_entries").fetchone()
    assert (entry["kind"], entry["phase_name"], entry["happened_at"], entry["notes"]) == ("move", "Applied", NOW, "Applied")


def test_create_application_is_idempotent_and_ignores_unknown_jobs(tmp_path):
    conn = connect(tmp_path / "new.db")
    job = make_job(conn)
    assert apps.create_application_for_job(conn, job, NOW) is not None
    assert apps.create_application_for_job(conn, job, LATER) is None
    assert apps.create_application_for_job(conn, 999, NOW) is None
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == 1
    assert conn.execute("SELECT applied_at FROM applications").fetchone()[0] == NOW


def test_create_application_without_phases_is_unassigned(tmp_path):
    conn = connect(tmp_path / "new.db")
    with conn:
        conn.execute("DELETE FROM phases")
    job = make_job(conn)
    apps.create_application_for_job(conn, job, NOW)
    assert conn.execute("SELECT phase_id FROM applications").fetchone()[0] is None
    assert conn.execute("SELECT phase_name FROM timeline_entries").fetchone()[0] == "Unassigned"


def test_move_application_updates_phase_and_appends_entry(tmp_path):
    conn = connect(tmp_path / "new.db")
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW)
    tech = conn.execute("SELECT id FROM phases WHERE name = 'Technical'").fetchone()[0]
    apps.move_application(conn, app_id, tech, LATER)
    assert conn.execute("SELECT phase_id FROM applications").fetchone()[0] == tech
    entries = conn.execute("SELECT phase_name, happened_at, kind FROM timeline_entries ORDER BY id").fetchall()
    assert [tuple(e) for e in entries] == [("Applied", NOW, "move"), ("Technical", LATER, "move")]


def test_move_application_rejects_unknown_ids_without_changes(tmp_path):
    conn = connect(tmp_path / "new.db")
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW)
    with pytest.raises(LookupError):
        apps.move_application(conn, app_id, 999, LATER)
    with pytest.raises(LookupError):
        apps.move_application(conn, 999, 1, LATER)
    assert conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == 1


@pytest.mark.parametrize("outcome", apps.OUTCOMES)
def test_set_outcome_accepts_each_valid_outcome(tmp_path, outcome):
    conn = connect(tmp_path / "new.db")
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW)
    apps.set_outcome(conn, app_id, outcome)
    assert conn.execute("SELECT outcome FROM applications").fetchone()[0] == outcome


def test_set_outcome_keeps_the_phase_and_validates(tmp_path):
    conn = connect(tmp_path / "new.db")
    app_id = apps.create_application_for_job(conn, make_job(conn), NOW)
    tech = conn.execute("SELECT id FROM phases WHERE name = 'Technical'").fetchone()[0]
    apps.move_application(conn, app_id, tech, LATER)
    apps.set_outcome(conn, app_id, "rejected")
    row = conn.execute("SELECT phase_id, outcome FROM applications").fetchone()
    assert (row["phase_id"], row["outcome"]) == (tech, "rejected")
    with pytest.raises(ValueError):
        apps.set_outcome(conn, app_id, "hired")
    with pytest.raises(LookupError):
        apps.set_outcome(conn, 999, "active")
    assert conn.execute("SELECT outcome FROM applications").fetchone()[0] == "rejected"
