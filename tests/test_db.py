import sqlite3

from jobfinder.db import connect

OLD_JOBS = """
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
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, UNIQUE (site, external_id));
INSERT INTO runs (id, started_at, status) VALUES (1, '2026-11-01T10:00:00', 'success');
INSERT INTO jobs (site, external_id, url, title, status, first_seen_run, first_seen_at, last_seen_at)
VALUES ('linkedin', 'abc', 'https://x/abc', 'Old Row', 'applied', 1, '2026-11-01', '2026-11-01');
"""


def columns(conn):
    return [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]


def test_connect_adds_pinned_at_to_an_old_database_and_keeps_rows(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(OLD_JOBS)
    raw.commit()
    raw.close()
    assert "pinned_at" not in columns(sqlite3.connect(path))

    conn = connect(path)
    assert "pinned_at" in columns(conn)
    row = conn.execute("SELECT title, status, pinned_at FROM jobs").fetchone()
    assert (row["title"], row["status"], row["pinned_at"]) == ("Old Row", "applied", None)


def test_connect_is_idempotent_and_new_databases_have_pinned_at(tmp_path):
    path = tmp_path / "new.db"
    assert columns(connect(path)).count("pinned_at") == 1
    assert columns(connect(path)).count("pinned_at") == 1


def test_connect_adds_work_mode_and_backfills_old_rows(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(OLD_JOBS)
    raw.executescript(
        """
        INSERT INTO jobs (site, external_id, url, title, location, is_remote, status, first_seen_run, first_seen_at, last_seen_at)
        VALUES ('indeed', 'h', 'https://x/h', 'Dev (Híbrido)', 'CDMX', NULL, 'new', 1, '2026-11-01', '2026-11-01');
        INSERT INTO jobs (site, external_id, url, title, location, is_remote, status, first_seen_run, first_seen_at, last_seen_at)
        VALUES ('indeed', 'r', 'https://x/r', 'Dev', 'Desde casa, Mexico', NULL, 'new', 1, '2026-11-01', '2026-11-01');
        INSERT INTO jobs (site, external_id, url, title, location, is_remote, status, first_seen_run, first_seen_at, last_seen_at)
        VALUES ('indeed', 'f', 'https://x/f', 'Dev', 'CDMX', 1, 'new', 1, '2026-11-01', '2026-11-01');
        INSERT INTO jobs (site, external_id, url, title, location, description, status, first_seen_run, first_seen_at, last_seen_at)
        VALUES ('indeed', 'o', 'https://x/o', 'Dev', 'CDMX', 'Trabajo presencial', 'new', 1, '2026-11-01', '2026-11-01');
        """
    )
    raw.commit()
    raw.close()
    assert "work_mode" not in columns(sqlite3.connect(path))

    conn = connect(path)
    modes = {r["external_id"]: r["work_mode"] for r in conn.execute("SELECT external_id, work_mode FROM jobs")}
    assert modes == {"abc": None, "h": "hybrid", "r": "remote", "f": "remote", "o": "onsite"}
    old = conn.execute("SELECT title, status FROM jobs WHERE external_id = 'abc'").fetchone()
    assert (old["title"], old["status"]) == ("Old Row", "applied")


def test_work_mode_backfill_runs_once_and_never_overwrites(tmp_path):
    path = tmp_path / "new.db"
    conn = connect(path)
    conn.execute("INSERT INTO runs (id, started_at, status) VALUES (1, '2026-11-01T10:00:00', 'success')")
    conn.execute(
        """INSERT INTO jobs (site, external_id, url, title, work_mode, first_seen_run, first_seen_at, last_seen_at)
           VALUES ('indeed', 'k', 'https://x/k', 'Remote Dev', 'onsite', 1, '2026-11-01', '2026-11-01')"""
    )
    conn.commit()
    conn.close()
    assert columns(connect(path)).count("work_mode") == 1
    assert connect(path).execute("SELECT work_mode FROM jobs").fetchone()[0] == "onsite"


# --- timeline_entries.phase_id ---------------------------------------------------------

OLD_TIMELINE = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE phases (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, position INTEGER NOT NULL);
CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    phase_id INTEGER REFERENCES phases(id), outcome TEXT NOT NULL DEFAULT 'active', applied_at TEXT NOT NULL,
    cv_id INTEGER);
CREATE TABLE timeline_entries (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id),
    phase_name TEXT NOT NULL, happened_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'note');
INSERT INTO meta (key, value) VALUES ('phases_seeded', '1');
INSERT INTO phases (id, name, position) VALUES (1, 'Applied', 1), (2, 'Technical', 2), (3, 'Final', 3), (4, 'final', 4);
INSERT INTO applications (id, job_id, phase_id, applied_at) VALUES (1, 1, 1, '2026-11-01T10:00:00+00:00');
"""
OLD_ENTRY_NAMES = ["Applied", "  technical ", "Technical", "Gone", "Final", "Applied"]


def old_timeline_db(tmp_path):
    path = tmp_path / "old_timeline.db"
    raw = sqlite3.connect(path)
    raw.executescript(OLD_JOBS + OLD_TIMELINE)
    raw.executemany(
        "INSERT INTO timeline_entries (application_id, phase_name, happened_at) VALUES (1, ?, '2026-11-02T10:00:00')",
        [(n,) for n in OLD_ENTRY_NAMES],
    )
    raw.commit()
    raw.close()
    return path


def timeline(conn):
    return [(r["id"], r["phase_name"], r["phase_id"]) for r in conn.execute("SELECT * FROM timeline_entries ORDER BY id")]


def test_connect_links_timeline_entries_to_their_unique_phase_once(tmp_path):
    path = old_timeline_db(tmp_path)
    assert "phase_id" not in [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(timeline_entries)")]

    conn = connect(path)
    # unique names (trimmed, any capitalization) are linked; an orphan name and one matching
    # two phases ("Final" / "final") stay NULL; no phase_name is ever changed, no row is lost
    assert timeline(conn) == [
        (1, "Applied", 1), (2, "  technical ", 2), (3, "Technical", 2),
        (4, "Gone", None), (5, "Final", None), (6, "Applied", 1),
    ]

    # one-time: later opens leave everything alone, even entries that would match now
    conn.execute("UPDATE timeline_entries SET phase_id = NULL WHERE id = 1")
    conn.execute("DELETE FROM phases WHERE id = 4")
    conn.commit()
    after = connect(path)
    assert timeline(after) == [
        (1, "Applied", None), (2, "  technical ", 2), (3, "Technical", 2),
        (4, "Gone", None), (5, "Final", None), (6, "Applied", 1),
    ]
    assert [r[1] for r in after.execute("PRAGMA table_info(timeline_entries)")].count("phase_id") == 1


def test_new_databases_have_phase_id_and_defaults_do_not_break(tmp_path):
    conn = connect(tmp_path / "new.db")
    assert [r[1] for r in conn.execute("PRAGMA table_info(timeline_entries)")].count("phase_id") == 1
    assert conn.execute("SELECT count(*) FROM timeline_entries").fetchone()[0] == 0
