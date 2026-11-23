from __future__ import annotations

import sqlite3
from pathlib import Path

from . import applications
from .enrich import detect_work_mode, extract_stack, stack_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,          -- running | success | partial | failed
    new_count INTEGER NOT NULL DEFAULT 0,
    seen_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS run_sites (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    site TEXT NOT NULL,
    hours_old INTEGER NOT NULL,
    status TEXT NOT NULL,          -- success | failed
    error TEXT,
    PRIMARY KEY (run_id, site)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    url_direct TEXT,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    is_remote INTEGER,
    date_posted TEXT,
    job_type TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    salary_interval TEXT,
    salary_source TEXT,            -- structured | description
    salary_basis TEXT,             -- gross | net | both | NULL (not specified)
    stack TEXT NOT NULL DEFAULT '',
    description TEXT,
    search_terms TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',  -- new | seen | applied | discarded
    dup_group INTEGER,
    first_seen_run INTEGER NOT NULL REFERENCES runs(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    pinned_at TEXT,                -- NULL = not pinned
    work_mode TEXT,                -- remote | hybrid | onsite | NULL (not specified)
    UNIQUE (site, external_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phases (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cv_files (
    id INTEGER PRIMARY KEY,
    original_name TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    stored_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    phase_id INTEGER REFERENCES phases(id),   -- NULL = unassigned (no phases defined)
    outcome TEXT NOT NULL DEFAULT 'active'
        CHECK (outcome IN ('active', 'accepted', 'rejected', 'withdrawn', 'ghosted')),
    applied_at TEXT NOT NULL,
    cv_id INTEGER REFERENCES cv_files(id)
);

CREATE TABLE IF NOT EXISTS timeline_entries (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    phase_name TEXT NOT NULL,      -- snapshot, survives phase removal (see phase_id, added by _migrate)
    happened_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'note'   -- note (user) | move (automatic)
);

-- One row per technology a job mentions; `tech` is the normalized key (see enrich.stack_key).
-- jobs.stack keeps the display names. Filtering and ranking by stack go through this table.
CREATE TABLE IF NOT EXISTS job_stack (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    tech TEXT NOT NULL,
    PRIMARY KEY (job_id, tech)
);

CREATE INDEX IF NOT EXISTS job_stack_tech ON job_stack(tech, job_id);
CREATE INDEX IF NOT EXISTS timeline_application ON timeline_entries(application_id);
CREATE INDEX IF NOT EXISTS timeline_application_when ON timeline_entries(application_id, happened_at);
CREATE INDEX IF NOT EXISTS applications_applied_at ON applications(applied_at);
CREATE INDEX IF NOT EXISTS jobs_first_seen ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS jobs_dup_group ON jobs(dup_group);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # case-insensitive beyond ASCII ("Sénior" = "SÉNIOR"); SQLite's own LIKE only folds ASCII letters
    conn.create_function("casefold", 1, lambda text: text.casefold() if isinstance(text, str) else text,
                         deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive upgrades for databases created by older versions."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "pinned_at" not in columns:
        with conn:
            conn.execute("ALTER TABLE jobs ADD COLUMN pinned_at TEXT")
    if "work_mode" not in columns:
        with conn:
            conn.execute("ALTER TABLE jobs ADD COLUMN work_mode TEXT")
            rows = conn.execute("SELECT id, title, location, description, is_remote FROM jobs").fetchall()
            for r in rows:
                mode = detect_work_mode(r["title"], r["location"], r["description"], r["is_remote"])
                if mode:
                    conn.execute("UPDATE jobs SET work_mode = ? WHERE id = ? AND work_mode IS NULL", (mode, r["id"]))
    _migrate_job_stack(conn)
    _add_timeline_phase_id(conn)
    _migrate_applications(conn)
    _backfill_timeline_phase_id(conn)


def _migrate_job_stack(conn: sqlite3.Connection) -> None:
    """One-time fill of `job_stack` from the offers already stored.

    The stack is extracted again from each stored description (plus the skills already in
    `jobs.stack`) so technologies added to the detector since, like Express, are picked up.
    Only the derived `jobs.stack` column is rewritten. Guarded by a `meta` flag.
    """
    if conn.execute("SELECT 1 FROM meta WHERE key = 'job_stack_built'").fetchone():
        return
    with conn:
        for row in conn.execute("SELECT id, description, stack FROM jobs").fetchall():
            names = extract_stack(row["description"], row["stack"])
            joined = ", ".join(names)
            if joined != row["stack"]:
                conn.execute("UPDATE jobs SET stack = ? WHERE id = ?", (joined, row["id"]))
            set_job_stack(conn, row["id"], names)
        conn.execute("INSERT INTO meta (key, value) VALUES ('job_stack_built', '1')")


def _add_timeline_phase_id(conn: sqlite3.Connection) -> None:
    """`timeline_entries.phase_id`: the phase an entry belongs to, so a rename can rewrite exactly
    its entries. Nullable and without a foreign key: removing a phase sets it to NULL and never
    deletes history (the `phase_name` snapshot stays)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(timeline_entries)")}
    if "phase_id" not in columns:
        with conn:
            conn.execute("ALTER TABLE timeline_entries ADD COLUMN phase_id INTEGER")
    with conn:
        conn.execute("CREATE INDEX IF NOT EXISTS timeline_phase ON timeline_entries(phase_id)")


def _backfill_timeline_phase_id(conn: sqlite3.Connection) -> None:
    """One-time link of the entries that already exist to their phase, by name.

    An entry is linked only when its name (trimmed, ignoring upper/lower case) matches exactly one
    current phase; entries matching none (a removed phase) or several stay NULL and are never
    rewritten by a rename. `phase_name` is never changed. Guarded by a `meta` flag, and runs in
    one transaction together with setting it."""
    if conn.execute("SELECT 1 FROM meta WHERE key = 'timeline_phase_id_linked'").fetchone():
        return
    by_name: dict[str, list[int]] = {}
    for row in conn.execute("SELECT id, name FROM phases"):
        by_name.setdefault(row["name"].strip().casefold(), []).append(row["id"])
    with conn:
        for row in conn.execute("SELECT DISTINCT phase_name FROM timeline_entries WHERE phase_id IS NULL").fetchall():
            ids = by_name.get(row["phase_name"].strip().casefold(), [])
            if len(ids) == 1:
                conn.execute("UPDATE timeline_entries SET phase_id = ? WHERE phase_id IS NULL AND phase_name = ?",
                             (ids[0], row["phase_name"]))
        conn.execute("INSERT INTO meta (key, value) VALUES ('timeline_phase_id_linked', '1')")


def _migrate_applications(conn: sqlite3.Connection) -> None:
    """One-time setup of the default phases, and applications for jobs already `applied`.

    Guarded by a `meta` flag so phases the user deletes later are never re-created.
    """
    if conn.execute("SELECT 1 FROM meta WHERE key = 'phases_seeded'").fetchone():
        return
    now = applications.now_iso()
    with conn:
        if not conn.execute("SELECT 1 FROM phases LIMIT 1").fetchone():
            applications.seed_default_phases(conn)
        for row in conn.execute("SELECT id FROM jobs WHERE status = 'applied'").fetchall():
            applications.create_application_for_job(conn, row["id"], now)
        conn.execute("INSERT INTO meta (key, value) VALUES ('phases_seeded', '1')")


def set_job_stack(conn: sqlite3.Connection, job_id: int, names: list[str]) -> None:
    """Replace a job's rows in `job_stack` with the normalized keys of `names`."""
    conn.execute("DELETE FROM job_stack WHERE job_id = ?", (job_id,))
    keys = {stack_key(n) for n in names if n.strip()}
    conn.executemany("INSERT INTO job_stack (job_id, tech) VALUES (?, ?)", [(job_id, k) for k in sorted(keys)])
