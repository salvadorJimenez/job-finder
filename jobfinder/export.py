"""Export the offers first seen on one day as Markdown, ready to paste into a chat."""
from __future__ import annotations

import sqlite3
from datetime import date

from .describe import normalize_description
from .format import format_basis, format_salary


def _export_description(text: str | None) -> str:
    """The tidied description, its headings one level below the "### Description" they sit under."""
    tidy = normalize_description(text)
    return "\n".join(f"#{line}" if line.startswith("### ") else line for line in tidy.split("\n")) or "(no description)"


def jobs_first_seen_on(conn: sqlite3.Connection, day: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE substr(first_seen_at, 1, 10) = ? ORDER BY id",
        (day.isoformat(),),
    ).fetchall()


def export_day(conn: sqlite3.Connection, day: date) -> str:
    jobs = jobs_first_seen_on(conn, day)
    lines = [f"# Job offers first seen on {day.isoformat()} ({len(jobs)})", ""]
    if not jobs:
        lines.append("No offers were first seen on this day.")
    for n, job in enumerate(jobs, 1):
        salary = format_salary(job)
        if job["salary_min"] is not None or job["salary_max"] is not None:
            salary = f"{salary} ({format_basis(job)})"
        lines += [
            f"## {n}. {job['title']} — {job['company'] or 'Unknown company'}",
            "",
            f"- Location: {job['location'] or '—'}",
            f"- Work mode: {job['work_mode'] or 'not specified'}",
            f"- Source: {job['site']}",
            f"- Posted: {job['date_posted'] or '—'}",
            f"- Salary: {salary}",
            f"- Stack: {job['stack'] or '—'}",
            f"- Status: {job['status']}",
            f"- Link: {job['url']}",
            "",
            "### Description",
            "",
            _export_description(job["description"]),
            "",
            "---",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"
