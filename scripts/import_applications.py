"""Import past job applications (found outside the app) as manual applications.

Usage:
    .venv/bin/python scripts/import_applications.py DATA.json [--config config.toml] [--apply]

Without --apply it only prints what it would do. Each record becomes a `manual` job with its
application, the phase it reached, its outcome and a dated timeline. Records are keyed by
`ref`, so running it twice never creates duplicates.

DATA.json is a list of:
    {"ref": "acme-1", "company": "...", "title": "...", "applied": "2026-01-05",
     "outcome": "withdrawn", "phase": "Hiring manager", "summary": "first note",
     "description": "stored on the job", "link": "",
     "steps": [{"date": "2026-01-12", "phase": "Hiring manager", "note": "..."}]}
A step with a phase moves the application there on that date; every step adds a note.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobfinder import applications as apps  # noqa: E402
from jobfinder.config import load_config  # noqa: E402
from jobfinder.db import connect  # noqa: E402

STEP_TIME = time(10, 0)
EXTERNAL_PREFIX = "import-"


def _at(day: str, tz, at: time = STEP_TIME) -> str:
    return datetime.combine(date.fromisoformat(day), at, tzinfo=tz).isoformat()


def import_records(conn, records: list[dict], tz, apply: bool) -> list[str]:
    """Import every record not imported before. Returns one log line per record."""
    phases = {p["name"]: p["id"] for p in conn.execute("SELECT id, name FROM phases")}
    log = []
    for r in records:
        external_id = f"{EXTERNAL_PREFIX}{r['ref']}"
        if conn.execute("SELECT 1 FROM jobs WHERE external_id = ?", (external_id,)).fetchone():
            log.append(f"skip   #{r['ref']} {r['company']} (already imported)")
            continue
        for name in [r["phase"], *(s["phase"] for s in r.get("steps", []) if s.get("phase"))]:
            if name not in phases:
                raise ValueError(f"#{r['ref']}: unknown phase {name!r}; existing: {', '.join(phases)}")
        if r["outcome"] not in apps.OUTCOMES:
            raise ValueError(f"#{r['ref']}: unknown outcome {r['outcome']!r}")
        log.append(f"import #{r['ref']} {r['applied']} {r['company']} — {r['title']} → {r['phase']} / {r['outcome']}"
                   f" ({len(r.get('steps', []))} steps)")
        if not apply:
            continue
        now = datetime.now(tz)
        app_id = apps.create_manual_application(conn, {
            "title": r["title"], "company": r["company"], "link": r.get("link", ""),
            "location": "", "work_mode": "", "salary": "", "applied_at": r["applied"],
        }, now)
        job_id = conn.execute("SELECT job_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
        applied = _at(r["applied"], tz, time())
        conn.execute(
            "UPDATE jobs SET external_id = ?, description = ?, first_seen_at = ?, last_seen_at = ? WHERE id = ?",
            (external_id, r.get("description", ""), applied, applied, job_id),
        )
        if r.get("summary"):
            first = conn.execute("SELECT phase_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
            apps.add_entry(conn, app_id, str(first), _at(r["applied"], tz, time(9, 0))[:16], r["summary"])
        for s in r.get("steps", []):
            when = _at(s["date"], tz)
            if s.get("phase"):
                apps.move_application(conn, app_id, phases[s["phase"]], when)
            current = conn.execute("SELECT phase_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
            apps.add_entry(conn, app_id, str(current), when[:16], s["note"])
        if conn.execute("SELECT phase_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0] != phases[r["phase"]]:
            last = r["steps"][-1]["date"] if r.get("steps") else r["applied"]
            apps.move_application(conn, app_id, phases[r["phase"]], _at(last, tz))
        apps.set_outcome(conn, app_id, r["outcome"])
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("data", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--apply", action="store_true", help="write to the database (default: dry run)")
    args = parser.parse_args(argv)
    records = json.loads(args.data.read_text())
    cfg = load_config(args.config)
    tz = datetime.now().astimezone().tzinfo
    conn = connect(cfg.database)
    with conn:  # one transaction: all records or none
        for line in import_records(conn, records, tz, args.apply):
            print(line)
    print("applied" if args.apply else "dry run: nothing written (use --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
