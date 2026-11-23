"""scripts/import_applications.py: past applications become manual applications with history."""
import importlib.util
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from jobfinder.db import connect

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "import_applications.py"
spec = importlib.util.spec_from_file_location("import_applications", SCRIPT)
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)
TZ = timezone(timedelta(hours=-6))

RECORDS = [
    {"ref": "initech-7", "company": "Initech", "title": "Platform Engineer", "applied": "2026-03-09",
     "outcome": "rejected", "phase": "Technical", "summary": "Applied via Lever", "description": "Imported",
     "steps": [
         {"date": "2026-03-10", "phase": "Recruiter screen", "note": "Intro call"},
         {"date": "2026-03-19", "phase": "Technical", "note": "Pairing session"},
         {"date": "2026-04-02", "note": "Rejected: position filled"},
     ]},
    {"ref": "hooli-3", "company": "Hooli", "title": "Staff Data Engineer", "applied": "2026-02-02",
     "outcome": "ghosted", "phase": "Applied", "summary": "Applied via Greenhouse", "steps": []},
]


def rows(conn):
    return conn.execute(
        """SELECT j.company, j.site, j.external_id, j.status, substr(j.first_seen_at, 1, 10) AS seen,
                  substr(a.applied_at, 1, 10) AS applied, p.name AS phase, a.outcome, a.id
           FROM applications a JOIN jobs j ON j.id = a.job_id JOIN phases p ON p.id = a.phase_id
           ORDER BY a.applied_at""").fetchall()


def test_dry_run_writes_nothing(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    log = imp.import_records(conn, RECORDS, TZ, apply=False)
    assert len(log) == 2 and all(line.startswith("import") for line in log)
    assert rows(conn) == []


def test_apply_creates_manual_applications_with_phase_outcome_and_dated_timeline(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    imp.import_records(conn, RECORDS, TZ, apply=True)
    got = [tuple(r)[:8] for r in rows(conn)]
    assert got == [
        ("Hooli", "manual", "import-hooli-3", "applied", "2026-02-02", "2026-02-02", "Applied", "ghosted"),
        ("Initech", "manual", "import-initech-7", "applied", "2026-03-09", "2026-03-09", "Technical", "rejected"),
    ]
    initech = rows(conn)[1]["id"]
    timeline = [(e["kind"], e["phase_name"], e["happened_at"][:10], e["notes"]) for e in conn.execute(
        "SELECT * FROM timeline_entries WHERE application_id = ? ORDER BY id", (initech,))]
    assert timeline == [
        ("move", "Applied", "2026-03-09", "Applied"),
        ("note", "Applied", "2026-03-09", "Applied via Lever"),
        ("move", "Recruiter screen", "2026-03-10", "Moved to Recruiter screen"),
        ("note", "Recruiter screen", "2026-03-10", "Intro call"),
        ("move", "Technical", "2026-03-19", "Moved to Technical"),
        ("note", "Technical", "2026-03-19", "Pairing session"),
        ("note", "Technical", "2026-04-02", "Rejected: position filled"),
    ]


def test_running_twice_never_duplicates(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    imp.import_records(conn, RECORDS, TZ, apply=True)
    log = imp.import_records(conn, RECORDS, TZ, apply=True)
    assert all(line.startswith("skip") for line in log)
    assert len(rows(conn)) == 2


def test_unknown_phase_is_refused_before_writing_that_record(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    bad = [{**RECORDS[1], "ref": "x", "phase": "Nope"}]
    with pytest.raises(ValueError, match="unknown phase 'Nope'"):
        imp.import_records(conn, bad, TZ, apply=True)
    assert rows(conn) == []
