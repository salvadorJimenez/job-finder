"""scripts/import_applications.py: past applications become manual applications with history."""
import importlib.util
import json
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


def write_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[search]\nterms = ["x"]\nsites = ["indeed"]\nlocation = "Mexico"\ncountry_indeed = "Mexico"\n')


def run_import(tmp_path, *extra):
    data = tmp_path / "data.json"
    data.write_text(json.dumps(RECORDS[1:]))
    return imp.main([str(data), *extra])


def test_default_config_is_found_in_the_config_folder_and_storage_stays_in_the_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(imp, "ROOT", tmp_path)
    write_config(tmp_path / "config" / "config.toml")
    assert run_import(tmp_path, "--apply") == 0
    assert "applied" in capsys.readouterr().out
    # config/config.toml keeps data/ in the project folder, not inside config/
    assert (tmp_path / "data" / "jobs.db").is_file() and not (tmp_path / "config" / "data").exists()
    assert [r["company"] for r in rows(connect(tmp_path / "data" / "jobs.db"))] == ["Hooli"]


def test_default_config_prefers_a_root_config_toml_when_both_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(imp, "ROOT", tmp_path)
    write_config(tmp_path / "config" / "config.toml")
    write_config(tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text(
        (tmp_path / "config.toml").read_text() + '\n[storage]\ndatabase = "root.db"\n')
    assert run_import(tmp_path, "--apply") == 0
    assert (tmp_path / "root.db").is_file() and not (tmp_path / "data").exists()


def test_without_any_config_it_prints_the_hint_and_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(imp, "ROOT", tmp_path)
    assert run_import(tmp_path) == 1
    assert "looked for config.toml and config/config.toml" in capsys.readouterr().err


def test_default_config_comes_from_the_project_folder_even_when_run_elsewhere(tmp_path, monkeypatch, capsys):
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    write_config(project / "config" / "config.toml")
    write_config(elsewhere / "config.toml")              # an unrelated config in the current folder
    (elsewhere / "config.toml").write_text(
        (elsewhere / "config.toml").read_text() + '\n[storage]\ndatabase = "wrong.db"\n')
    monkeypatch.setattr(imp, "ROOT", project)
    monkeypatch.chdir(elsewhere)
    assert run_import(tmp_path, "--apply") == 0
    assert (project / "data" / "jobs.db").is_file() and not (elsewhere / "wrong.db").exists()


def test_an_explicit_missing_config_prints_one_line_and_exits_1(tmp_path, capsys):
    missing = tmp_path / "nope.toml"
    assert run_import(tmp_path, "--config", str(missing)) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.strip().splitlines() == [err.strip()] and str(missing) in err
