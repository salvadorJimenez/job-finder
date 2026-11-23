"""scripts/demo_data.py: a fictional database for the README screenshots, never a real one."""
import importlib.util
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobfinder.config import load_config
from jobfinder.db import connect
from jobfinder.web import create_app

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "demo_data.py"
spec = importlib.util.spec_from_file_location("demo_data", SCRIPT)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

TODAY = date(2026, 11, 18)          # a Wednesday
TZ = datetime.now().astimezone().tzinfo      # the demo stamps use the local zone
NOW = datetime(2026, 11, 18, 14, 0, tzinfo=TZ)
FICTIONAL = {"Acme", "Globex", "Initech", "Umbrella", "Hooli", "Stark Industries", "Wayne Enterprises"}


def write_config(folder: Path) -> Path:
    path = folder / "config.toml"
    path.write_text(
        '[search]\nterms = ["software engineer"]\nsites = ["linkedin", "indeed", "glassdoor"]\n'
        'location = "Mexico"\ncountry_indeed = "Mexico"\n\n'
        '[profile]\nstack = ["TypeScript", "React", "Python", "PostgreSQL"]\n\n'
        '[goal]\ndaily = 2\n\n'
        f'[storage]\ndatabase = "{folder / "demo" / "jobs.db"}"\nexports_dir = "{folder / "exports"}"\n'
    )
    return path


@pytest.fixture
def seeded(tmp_path, capsys):
    config = write_config(tmp_path)
    assert demo.main(["--config", str(config), "--today", TODAY.isoformat()]) == 0
    out = capsys.readouterr().out
    return config, load_config(config), out


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_seeds_the_expected_rows(seeded):
    _, cfg, out = seeded
    conn = connect(cfg.database)
    assert one(conn, "SELECT count(*) FROM jobs") == 25
    assert one(conn, "SELECT count(*) FROM applications") == 14
    assert one(conn, "SELECT count(*) FROM phases") == 6
    assert one(conn, "SELECT count(*) FROM timeline_entries") >= 40
    assert {r[0] for r in conn.execute("SELECT DISTINCT site FROM jobs")} == {"linkedin", "indeed", "glassdoor"}
    assert {r[0] for r in conn.execute("SELECT DISTINCT status FROM jobs")} == {"new", "seen", "applied", "discarded"}
    assert {r[0] for r in conn.execute("SELECT DISTINCT outcome FROM applications")} >= {"active", "rejected", "ghosted"}
    assert {r[0] for r in conn.execute("SELECT DISTINCT salary_currency FROM jobs")} >= {"MXN", "USD"}
    assert {r[0] for r in conn.execute("SELECT DISTINCT work_mode FROM jobs")} >= {"remote", "hybrid", "onsite"}
    assert one(conn, "SELECT count(*) FROM jobs WHERE pinned_at IS NOT NULL") >= 2
    groups = [r[0] for r in conn.execute("SELECT count(*) FROM jobs WHERE dup_group IS NOT NULL GROUP BY dup_group")]
    assert len(groups) >= 2 and all(n >= 2 for n in groups)
    assert one(conn, "SELECT count(*) FROM jobs WHERE stack != ''") >= 20
    assert one(conn, "SELECT count(*) FROM jobs WHERE length(description) > 400") >= 20
    assert one(conn, "SELECT status FROM runs ORDER BY id DESC LIMIT 1") == "success"
    assert one(conn, "SELECT substr(started_at, 1, 10) FROM runs ORDER BY id DESC LIMIT 1") == TODAY.isoformat()
    assert re.search(r"^featured_job=\d+$", out, re.M) and re.search(r"^featured_application=\d+$", out, re.M)


def test_only_fictional_companies_and_a_neutral_persona(seeded):
    _, cfg, _ = seeded
    conn = connect(cfg.database)
    companies = {r[0] for r in conn.execute("SELECT DISTINCT company FROM jobs")}
    assert len(companies) >= 10
    assert FICTIONAL <= companies
    places = [r[0] for r in conn.execute("SELECT DISTINCT location FROM jobs")]
    assert all(p.startswith(("Monterrey", "Mexico City", "Remote", "Mexico")) for p in places), places
    text = " ".join(r[0] for r in conn.execute("SELECT description FROM jobs"))
    assert "TypeScript" in text and "Python" in text
    assert not {"Ruby", "Rails", "Kafka"} & {t for t in text.replace(",", " ").split()}


def test_dates_are_relative_to_today_and_nothing_is_in_the_future_but_interviews(seeded):
    _, cfg, _ = seeded
    conn = connect(cfg.database)
    assert one(conn, "SELECT max(substr(applied_at, 1, 10)) FROM applications") == TODAY.isoformat()
    assert one(conn, "SELECT min(substr(applied_at, 1, 10)) FROM applications") >= (TODAY - timedelta(days=30)).isoformat()
    assert one(conn, "SELECT max(substr(first_seen_at, 1, 10)) FROM jobs") <= TODAY.isoformat()
    future = conn.execute("SELECT kind, notes FROM timeline_entries WHERE happened_at > ?", (NOW.isoformat(),)).fetchall()
    assert len(future) >= 2 and all(r["kind"] == "note" for r in future)
    assert all("interview" in r["notes"].lower() or "call" in r["notes"].lower() for r in future)


def test_the_same_day_gives_the_same_data(tmp_path):
    rows = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        config = write_config(folder)
        assert demo.main(["--config", str(config), "--today", TODAY.isoformat()]) == 0
        conn = connect(load_config(config).database)
        rows.append([tuple(r) for r in conn.execute(
            "SELECT title, company, site, status, first_seen_at, salary_min FROM jobs ORDER BY id")]
            + [tuple(r) for r in conn.execute("SELECT notes, happened_at, kind FROM timeline_entries ORDER BY id")])
    assert rows[0] == rows[1]


def test_refuses_an_existing_database_and_leaves_it_alone(tmp_path, capsys):
    config = write_config(tmp_path)
    db = load_config(config).database
    db.parent.mkdir(parents=True)
    db.write_bytes(b"precious")
    assert demo.main(["--config", str(config), "--today", TODAY.isoformat()]) == 1
    captured = capsys.readouterr()
    assert db.read_bytes() == b"precious"
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1 and "already exists" in captured.err


def test_rejects_a_bad_today(tmp_path, capsys):
    config = write_config(tmp_path)
    with pytest.raises(SystemExit):
        demo.main(["--config", str(config), "--today", "tomorrow"])
    assert not load_config(config).database.exists()


def test_the_app_renders_the_demo_data(seeded):
    _, cfg, out = seeded
    client = TestClient(create_app(cfg, clock=lambda: NOW, runner=lambda *a, **k: None))
    job_id = re.search(r"^featured_job=(\d+)$", out, re.M).group(1)
    app_id = re.search(r"^featured_application=(\d+)$", out, re.M).group(1)

    jobs = client.get(f"/?job={job_id}")
    assert jobs.status_code == 200
    assert "Searched today at" in jobs.text and "config error" not in jobs.text.lower()
    assert any(name in jobs.text for name in ("Hooli", "Acme", "Globex"))

    board = client.get("/applications")
    assert board.status_code == 200
    assert all(name in board.text for name in ("Acme", "Globex", "Initech", "Umbrella", "Hooli"))
    assert "Recruiter screen" in board.text and "Offer" in board.text

    page = client.get(f"/applications/{app_id}")
    assert page.status_code == 200
    assert page.text.count("timeline") >= 1 and "Wonka" in page.text
