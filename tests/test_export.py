from datetime import date, datetime, timedelta, timezone

from jobfinder.db import connect
from jobfinder.export import export_day

TZ = timezone(timedelta(hours=-6))


def test_export_includes_every_field_and_full_description():
    conn = connect(":memory:")
    seen = datetime(2026, 11, 3, 9, 0, tzinfo=TZ)
    conn.execute("INSERT INTO runs (id, started_at, status) VALUES (1, ?, 'success')", (seen.isoformat(),))
    for ext, when, title in [("1", seen, "Senior Spring Engineer"), ("2", seen - timedelta(days=1), "Older Job")]:
        conn.execute(
            """INSERT INTO jobs (site, external_id, url, title, company, location, salary_min, salary_max,
                   salary_currency, salary_interval, salary_basis, stack, description, date_posted,
                   first_seen_run, first_seen_at, last_seen_at)
               VALUES ('linkedin', ?, ?, ?, 'Acme', 'CDMX', 50000, 60000, 'MXN', 'monthly', 'net',
                   'Kotlin, Spring', ?, '2026-11-02', 1, ?, ?)""",
            (ext, f"https://x/{ext}", title, "Line one.\n\n50 hours per week required.", when.isoformat(),
             when.isoformat()),
        )
    md = export_day(conn, date(2026, 11, 3))
    assert md.startswith("# Job offers first seen on 2026-11-03 (1)")
    assert "## 1. Senior Spring Engineer — Acme" in md
    assert "- Salary: MXN 50,000–60,000 / month (net)" in md
    assert "- Stack: Kotlin, Spring" in md
    assert "- Link: https://x/1" in md
    assert "50 hours per week required." in md
    assert "Older Job" not in md


def test_export_empty_day():
    md = export_day(connect(":memory:"), date(2026, 11, 3))
    assert md.startswith("# Job offers first seen on 2026-11-03 (0)")
    assert "No offers were first seen on this day." in md


def test_export_has_a_work_mode_line_per_job():
    conn = connect(":memory:")
    seen = datetime(2026, 11, 3, 9, 0, tzinfo=TZ)
    conn.execute("INSERT INTO runs (id, started_at, status) VALUES (1, ?, 'success')", (seen.isoformat(),))
    for ext, mode in [("1", "remote"), ("2", "hybrid"), ("3", "onsite"), ("4", None)]:
        conn.execute(
            """INSERT INTO jobs (site, external_id, url, title, work_mode, first_seen_run, first_seen_at, last_seen_at)
               VALUES ('linkedin', ?, ?, ?, ?, 1, ?, ?)""",
            (ext, f"https://x/{ext}", f"Job {ext}", mode, seen.isoformat(), seen.isoformat()),
        )
    md = export_day(conn, date(2026, 11, 3))
    lines = [line for line in md.splitlines() if line.startswith("- Work mode:")]
    assert lines == [
        "- Work mode: remote",
        "- Work mode: hybrid",
        "- Work mode: onsite",
        "- Work mode: not specified",
    ]


def test_export_uses_the_tidied_description_with_headings_below_description():
    conn = connect(":memory:")
    seen = datetime(2026, 11, 3, 9, 0, tzinfo=TZ)
    conn.execute("INSERT INTO runs (id, started_at, status) VALUES (1, ?, 'success')", (seen.isoformat(),))
    conn.execute(
        """INSERT INTO jobs (site, external_id, url, title, company, description, first_seen_run, first_seen_at, last_seen_at)
           VALUES ('linkedin', '1', 'https://x/1', 'Dev', 'Acme', ?, 1, ?, ?)""",
        ("**Requisitos:**\n\n• Python\n\n• SQL\n  \n  \n\n\n\n**Ofrecemos**\n\n\\- Seguro", seen.isoformat(), seen.isoformat()),
    )
    md = export_day(conn, date(2026, 11, 3))
    assert "### Description\n\n#### Requisitos\n\n- Python\n- SQL\n\n#### Ofrecemos\n\n- Seguro" in md
    assert "•" not in md and "\\-" not in md and "\n\n\n" not in md
