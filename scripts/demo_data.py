"""Seed a fresh database with fictional demo data, for the README screenshots.

Usage:
    .venv/bin/python scripts/demo_data.py --config <config.toml> [--today YYYY-MM-DD]

Everything is invented: companies (Acme, Globex, Initech...), people and notes. The database named
by the config's [storage] section must NOT exist yet: the script refuses to touch an existing file,
so it can never write into a real database. Dates are relative to `--today` (default: the real
today), counted in weekdays, so a given day always produces the same data. The app itself uses
the real clock: serve the demo on the day you seed it. `scripts/screenshots.sh` does all of this
inside a throwaway folder.

It prints `featured_job=<id>` and `featured_application=<id>`: the pages worth a screenshot.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobfinder import applications as apps  # noqa: E402
from jobfinder.config import load_config  # noqa: E402
from jobfinder.db import connect, set_job_stack  # noqa: E402
from jobfinder.dedup import is_possible_duplicate  # noqa: E402
from jobfinder.enrich import detect_salary_basis, detect_work_mode, extract_stack, parse_salary  # noqa: E402

SITES = ("linkedin", "indeed", "glassdoor")
TERMS = ("software engineer", "backend developer", "frontend developer")
MTY, CDMX = "Monterrey, Nuevo León, Mexico", "Mexico City, CDMX, Mexico"
REMOTE = "Remote, Mexico"
# runs, as weekdays back from today; every job was first seen in one of them
RUN_DAYS = (0, 1, 3, 5, 8, 11, 14, 18, 22)

# --- job descriptions ---------------------------------------------------------------------------------

DUTIES = {
    "frontend": ("Build accessible, fast interfaces with our design system and ship them every week",
                 "Own the quality of the web client: tests, performance budgets and error monitoring",
                 "Work with designers and product managers to turn rough ideas into features"),
    "backend": ("Design and run the APIs and services behind our product",
                "Model data carefully, write migrations that are safe to deploy and keep queries fast",
                "Improve reliability: observability, alerts and a calm on-call rotation"),
    "fullstack": ("Take features from a conversation to production, across the API and the web client",
                  "Write clear, tested code and review the code of your teammates",
                  "Talk to customers and support to understand what is worth building next"),
    "platform": ("Build the paved road other teams use to ship: pipelines, environments and templates",
                 "Automate infrastructure, keep it reproducible and document how it works",
                 "Reduce cost and incident time with better metrics and clear runbooks"),
    "data": ("Build and maintain the pipelines that feed analytics and product features",
             "Keep datasets trustworthy: tests, lineage and clear ownership",
             "Partner with analysts to turn questions into reliable tables"),
    "qa": ("Design the end to end test strategy for web and API",
           "Build a stable automated suite and make it part of every pull request",
           "Help the team find the risky corners before customers do"),
    "devops": ("Run our deployments and keep environments consistent",
               "Own monitoring, backups and the incident process",
               "Make the developer experience boring in the best way"),
}


def describe(company: str, kind: str, stack: str, perks: str, pay: str, basis: str) -> str:
    """Markdown the way the scrapers return it: headings, lists and a bit of bold."""
    duties = "\n".join(f"- {d}" for d in DUTIES[kind])
    return f"""**{company}** is a product company with a small, senior team that values clear writing and steady delivery.

## About the role

You will join a team of about ten engineers working in two-week cycles. We hire for judgment more than for a checklist, and we pay for it.

## What you will do

{duties}

## What we are looking for

- 3 or more years building production software
- Solid experience with {stack}
- Comfortable reading unfamiliar code and explaining trade-offs in writing
- Good written and spoken English; Spanish is the team language

## Nice to have

- Experience with automated testing, CI/CD and cloud deployments
- Open source contributions or a public project we can read

## What we offer

- {perks}
- Annual bonus, health insurance and a learning budget
- {pay}
- {basis}
"""


# (key, site, company, title, location, kind, stack, salary, perks, status)
# salary: ("structured", min, max, currency, interval) | ("text", "Salary: ...") | None
# `basis` is a sentence the gross/net detector reads; "" when the posting does not say.
JOBS = [
    # --- jobs that became applications ---
    dict(key="acme", site="linkedin", company="Acme", title="Full Stack Engineer", location=f"{CDMX} (Hybrid)",
         kind="fullstack", stack="TypeScript, React, Node.js, PostgreSQL and AWS",
         salary=("structured", 75000, 95000, "MXN", "monthly"), basis="Salary is quoted gross, before taxes.",
         perks="Hybrid: two days a week in our Roma Norte office"),
    dict(key="hooli", site="indeed", company="Hooli", title="Senior Frontend Engineer", location=REMOTE,
         kind="frontend", stack="React, TypeScript, GraphQL and Tailwind",
         salary=("structured", 4500, 6000, "USD", "monthly"), basis="Paid in USD, net of the contractor fee.",
         perks="Fully remote, work from anywhere in Mexico"),
    dict(key="stark", site="glassdoor", company="Stark Industries", title="Backend Engineer (Python)",
         location=f"{MTY} (On-site)", kind="backend", stack="Python, FastAPI, PostgreSQL, Redis and Docker",
         salary=("text", "Salary: MXN 80,000 - 100,000 per month, gross (bruto)"), basis="",
         perks="On-site at our Monterrey campus, with free lunch"),
    dict(key="wayne", site="linkedin", company="Wayne Enterprises", title="Software Engineer, Platform",
         location=f"{MTY} (Hybrid)", kind="platform", stack="Python, Kubernetes, Terraform and AWS",
         salary=None, basis="", perks="Hybrid schedule, three days at home"),
    dict(key="globex", site="linkedin", company="Globex", title="Senior Software Engineer", location=REMOTE,
         kind="backend", stack="TypeScript, Node.js, GraphQL and PostgreSQL",
         salary=("structured", 60000, 80000, "USD", "yearly"), basis="Paid in USD, gross yearly.",
         perks="Remote-first, with two team meetups a year"),
    dict(key="umbrella", site="indeed", company="Umbrella", title="Full Stack Developer", location=f"{CDMX} (Hybrid)",
         kind="fullstack", stack="Python, Django, React and PostgreSQL",
         salary=("text", "Salary: MXN 70,000 - 90,000 per month, net (libres de impuestos)"), basis="",
         perks="Hybrid with flexible hours"),
    dict(key="piper", site="linkedin", company="Pied Piper", title="Frontend Engineer", location=REMOTE,
         kind="frontend", stack="React, TypeScript and Next.js",
         salary=("structured", 3800, 5200, "USD", "monthly"), basis="Paid in USD.",
         perks="Remote, async by default"),
    dict(key="aperture", site="glassdoor", company="Aperture Labs", title="Backend Engineer",
         location=f"{CDMX} (Hybrid)", kind="backend", stack="Python, Django, PostgreSQL, Redis and Docker",
         salary=("text", "Salary: MXN 85,000 - 115,000 per month, gross"), basis="",
         perks="Hybrid, with a quarterly team week"),
    dict(key="initech", site="indeed", company="Initech", title="Senior Software Engineer",
         location=f"{MTY} (On-site)", kind="fullstack", stack="TypeScript, Node.js, MySQL and Docker",
         salary=("structured", 65000, 85000, "MXN", "monthly"), basis="Salary is quoted gross.",
         perks="On-site in Monterrey, parking included"),
    dict(key="cyberdyne", site="linkedin", company="Cyberdyne Systems", title="Platform Engineer", location=REMOTE,
         kind="platform", stack="Python, Kubernetes, Terraform and AWS",
         salary=("structured", 5000, 7000, "USD", "monthly"), basis="Paid in USD.",
         perks="Remote, with a home office stipend"),
    dict(key="wonka", site="linkedin", company="Wonka Industries", title="Senior Full Stack Engineer",
         location=f"{MTY} (Hybrid)", kind="fullstack", stack="TypeScript, React, Node.js, PostgreSQL, AWS and Docker",
         salary=("structured", 110000, 140000, "MXN", "monthly"), basis="Salary is quoted gross, before taxes.",
         perks="Hybrid: Tuesday to Thursday in our Monterrey office"),
    dict(key="massive", site="glassdoor", company="Massive Dynamic", title="Senior Backend Engineer",
         location=f"{CDMX} (Hybrid)", kind="backend", stack="Python, FastAPI, PostgreSQL, AWS and Kubernetes",
         salary=("structured", 5500, 7500, "USD", "monthly"), basis="Paid in USD, gross.",
         perks="Hybrid with generous remote weeks"),
    dict(key="soylent", site="indeed", company="Soylent Corp", title="Full Stack Engineer", location=REMOTE,
         kind="fullstack", stack="JavaScript, React, Node.js and MongoDB",
         salary=("text", "Salary: MXN 60,000 - 80,000 per month"), basis="",
         perks="Remote, flexible hours"),
    dict(key="vandelay", site="indeed", company="Vandelay Industries", title="Software Engineer",
         location=f"{MTY} (On-site)", kind="fullstack", stack="TypeScript, Node.js, MongoDB and Docker",
         salary=("text", "Salary: MXN 55,000 - 75,000 per month, net"), basis="",
         perks="On-site in San Pedro Garza García"),
    # --- offers found by the searches, not applied to ---
    dict(key="stark-fe", site="linkedin", company="Stark Industries", title="Senior Frontend Engineer",
         location=REMOTE, kind="frontend", stack="React, TypeScript, GraphQL and Tailwind",
         salary=("structured", 4800, 6200, "USD", "monthly"), basis="Paid in USD, gross.",
         perks="Remote, with a yearly trip to the Monterrey office", status="new", pinned=True),
    dict(key="stark-fe", site="indeed", company="Stark Industries", title="Senior Frontend Engineer",
         location="Mexico", kind="frontend", stack="React, TypeScript, GraphQL and Tailwind",
         salary=("structured", 4800, 6200, "USD", "monthly"), basis="Paid in USD, gross.",
         perks="Remote, with a yearly trip to the Monterrey office", status="new"),
    dict(key="stark-fe", site="glassdoor", company="Stark Industries", title="Senior Frontend Engineer",
         location="Remote", kind="frontend", stack="React, TypeScript, GraphQL and Tailwind",
         salary=None, basis="", perks="Remote, with a yearly trip to the Monterrey office", status="new"),
    dict(key="acme-plat", site="linkedin", company="Acme", title="Platform Engineer", location=f"{CDMX} (Hybrid)",
         kind="platform", stack="Python, Docker, Kubernetes, Terraform and AWS",
         salary=("text", "Salary: MXN 90,000 - 120,000 per month, gross"), basis="",
         perks="Hybrid, two days a week in the office", status="new"),
    dict(key="acme-plat", site="indeed", company="Acme", title="Platform Engineer", location=f"{CDMX} (Hybrid)",
         kind="platform", stack="Python, Docker, Kubernetes, Terraform and AWS",
         salary=("text", "Salary: MXN 90,000 - 120,000 per month, gross"), basis="",
         perks="Hybrid, two days a week in the office", status="new"),
    dict(key="wayne-data", site="glassdoor", company="Wayne Enterprises", title="Data Engineer (Python)",
         location=f"{MTY} (Hybrid)", kind="data", stack="Python, SQL, PostgreSQL, Docker and AWS",
         salary=("text", "Salary: MXN 70,000 - 95,000 per month, gross"), basis="",
         perks="Hybrid, with a learning budget for data conferences", status="seen"),
    dict(key="umbrella-be", site="linkedin", company="Umbrella", title="Senior Backend Engineer", location=REMOTE,
         kind="backend", stack="Python, Django, PostgreSQL, Redis and Docker",
         salary=("structured", 5200, 7000, "USD", "monthly"), basis="Paid in USD, gross.",
         perks="Remote, async-friendly, with a four-day week in summer", status="new", pinned=True),
    dict(key="hooli-fs", site="indeed", company="Hooli", title="Full Stack Engineer", location=f"{CDMX} (Hybrid)",
         kind="fullstack", stack="TypeScript, React, Node.js and PostgreSQL",
         salary=("structured", 80000, 105000, "MXN", "monthly"), basis="Salary is quoted gross, before taxes.",
         perks="Hybrid, with a flexible calendar", status="new"),
    dict(key="globex-fe", site="linkedin", company="Globex", title="Frontend Developer", location=f"{MTY} (On-site)",
         kind="frontend", stack="React, JavaScript, HTML and CSS",
         salary=("text", "Salary: MXN 45,000 - 60,000 per month, net"), basis="",
         perks="On-site in Monterrey, with free parking", status="seen"),
    dict(key="initech-ops", site="glassdoor", company="Initech", title="DevOps Engineer", location=REMOTE,
         kind="devops", stack="Docker, Kubernetes, Terraform and AWS",
         salary=None, basis="", perks="Remote", status="discarded"),
    dict(key="piper-qa", site="indeed", company="Pied Piper", title="QA Automation Engineer",
         location=f"{CDMX} (On-site)", kind="qa", stack="TypeScript, JavaScript and SQL",
         salary=("text", "Salary: MXN 40,000 - 55,000 per month"), basis="",
         perks="On-site, central CDMX", status="discarded"),
]

# where each unapplied job was first seen, as an index into RUN_DAYS
SEEN_RUN = {"stark-fe": 0, "umbrella-be": 0, "hooli-fs": 0, "acme-plat": 1, "wayne-data": 1,
            "globex-fe": 3, "initech-ops": 3, "piper-qa": 3}

# --- applications -------------------------------------------------------------------------------------
# (job key, weekdays back it was applied, hour:minute, final phase, outcome, steps)
# a step is (weekdays after applying, "HH:MM", phase moved to or None, note or None)
APPLICATIONS = [
    ("acme", 0, "09:40", "Applied", "active", [
        (0, "09:45", None, "Applied through the company site. The cover note mentions the TypeScript and React work.")]),
    ("hooli", 0, "11:15", "Applied", "active", [
        (0, "11:20", None, "Referred by a former colleague, Priya Nair. She will mention my name to the team.")]),
    ("stark", 1, "09:30", "Applied", "active", [
        (1, "08:10", None, "Opened the posting again. Still no confirmation email, check the spam folder.")]),
    ("wayne", 1, "10:05", "Recruiter screen", "active", [
        (1, "08:20", "Recruiter screen", "Recruiter Dana Lee wrote back and wants a 30 minute call to confirm salary expectations.")]),
    ("globex", 2, "09:50", "Recruiter screen", "active", [
        (1, "12:30", "Recruiter screen", "Intro call with Dana Lee: remote-first, small platform team, salary range matches mine."),
        (2, "08:05", None, "Sent the availability for the hiring manager call.")]),
    ("umbrella", 2, "10:40", "Hiring manager", "active", [
        (1, "11:00", "Recruiter screen", "Screening call went well. They use Django on a modular monolith."),
        (2, "08:30", "Hiring manager", "Call booked with hiring manager Sam Rivera for later this week.")]),
    ("piper", 3, "09:20", "Hiring manager", "active", [
        (1, "15:00", "Recruiter screen", "Short call with the talent partner. Async culture, weekly demos."),
        (3, "09:00", "Hiring manager", "Hiring manager call: 45 minutes about frontend architecture and testing.")]),
    ("aperture", 3, "10:15", "Technical", "active", [
        (1, "13:00", "Recruiter screen", "Recruiter call: three interview rounds, process takes about three weeks."),
        (2, "10:30", "Hiring manager", "Talked with the engineering manager about the data import project."),
        (3, "08:45", "Technical", "Technical round scheduled: live coding with a pairing partner."),
        ("next", 1, "11:00", None, "Technical interview (live coding), 60 minutes, with John Roe. Review the Django ORM notes.")]),
    ("initech", 4, "09:10", "Hiring manager", "rejected", [
        (1, "12:00", "Recruiter screen", "Recruiter call: the role is on-site in Monterrey."),
        (2, "16:00", "Hiring manager", "Call with the hiring manager, mostly about Node.js services."),
        (4, "09:30", None, "Rejected: they are moving forward with a candidate who has more Java experience.")]),
    ("cyberdyne", 6, "09:00", "Technical", "active", [
        (1, "10:20", "Recruiter screen", "Intro call. The team runs everything on Kubernetes and Terraform."),
        (3, "14:00", "Hiring manager", "Hiring manager call with Lena Park. Good conversation about incident response."),
        (5, "11:30", "Technical", "Technical interview: system design exercise on a deployment pipeline. Waiting for feedback.")]),
    ("wonka", 8, "09:25", "Final", "active", [
        (1, "10:00", "Recruiter screen", "Recruiter Dana Lee called: hybrid in Monterrey, salary band shared."),
        (2, "09:30", None, "Sent my salary expectation and the dates I can start."),
        (3, "16:30", "Hiring manager", "Call with the hiring manager, Sam Rivera. They are rebuilding the order dashboard."),
        (5, "09:00", "Technical", "Take-home assigned: a small TypeScript, React and Node.js feature, about four hours."),
        (6, "18:15", None, "Submitted the take-home with a short write-up of the trade-offs."),
        (7, "12:00", "Final", "Passed the technical review. Final round is a conversation with the CTO and two engineers."),
        ("next", 2, "10:00", None, "Final interview at the Monterrey office with the CTO, Maria Chen. Bring questions about the roadmap.")]),
    ("massive", 11, "10:30", "Offer", "active", [
        (1, "09:15", "Recruiter screen", "Recruiter call with Chen Wei. The team is five backend engineers."),
        (3, "11:00", "Hiring manager", "Hiring manager call about ownership of the billing service."),
        (6, "10:00", "Technical", "Technical interview: API design and SQL. Felt good."),
        (8, "15:00", "Final", "Final conversation with the VP of Engineering."),
        (9, "12:15", "Offer", "Verbal offer received: USD 6,500 per month, fully remote after onboarding. Written offer to follow.")]),
    ("soylent", 14, "09:35", "Recruiter screen", "ghosted", [
        (2, "11:00", "Recruiter screen", "Intro call went fine. They said next steps within a week."),
        (9, "09:00", None, "Followed up by email, no reply."),
        (12, "09:00", None, "Second follow-up. Still no answer, closing this one as ghosted.")]),
    ("vandelay", 17, "10:00", "Technical", "rejected", [
        (2, "10:30", "Recruiter screen", "Recruiter screen, in person in San Pedro."),
        (5, "13:00", "Hiring manager", "Hiring manager call. They need someone who can start in two weeks."),
        (9, "11:00", "Technical", "Technical interview with two engineers, mostly Node.js and MongoDB."),
        (13, "09:00", None, "Rejected: they decided not to continue because of the start date.")]),
]
FEATURED_JOB, FEATURED_APPLICATION = "umbrella-be", "wonka"


# --- helpers ------------------------------------------------------------------------------------------

class Calendar:
    """Weekday arithmetic around the demo's "today", in the local time zone."""

    def __init__(self, today: date):
        self.today = today
        self.tz = datetime.now().astimezone().tzinfo

    def weekday(self, back: int) -> date:
        day = self.today
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        for _ in range(back):
            day -= timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
        return day

    def next_weekday(self, ahead: int) -> date:
        day = self.today
        for _ in range(ahead):
            day += timedelta(days=1)
            while day.weekday() >= 5:
                day += timedelta(days=1)
        return day

    def stamp(self, day: date, hhmm: str) -> datetime:
        return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=self.tz)


def _insert_job(conn, spec: dict, run_id: int, seen: datetime, calendar: Calendar, serial: int) -> int:
    description = describe(spec["company"], spec["kind"], spec["stack"], spec["perks"],
                           spec["salary"][1] if spec["salary"] and spec["salary"][0] == "text" else
                           "Competitive salary, paid monthly", spec["basis"] or "Compensation depends on experience.")
    raw: dict = {}
    if spec["salary"] and spec["salary"][0] == "structured":
        _, low, high, currency, interval = spec["salary"]
        raw = {"min_amount": low, "max_amount": high, "currency": currency, "interval": interval}
    salary = parse_salary(raw, description)
    site = spec["site"]
    external_id = f"demo-{site}-{serial:04d}"
    url = {"linkedin": f"https://www.linkedin.com/jobs/view/{external_id}",
           "indeed": f"https://mx.indeed.com/viewjob?jk={external_id}",
           "glassdoor": f"https://www.glassdoor.com.mx/job-listing/{external_id}"}[site]
    stack = extract_stack(description)
    job = {
        "site": site, "external_id": external_id, "url": url, "url_direct": None,
        "title": spec["title"], "company": spec["company"], "location": spec["location"],
        "is_remote": 1 if "Remote" in spec["location"] else 0,
        "work_mode": detect_work_mode(spec["title"], spec["location"], description, "Remote" in spec["location"]),
        "date_posted": seen.date().isoformat(), "job_type": "fulltime",
        "salary_min": salary.min_amount if salary else None, "salary_max": salary.max_amount if salary else None,
        "salary_currency": salary.currency if salary else None, "salary_interval": salary.interval if salary else None,
        "salary_source": salary.source if salary else None,
        "salary_basis": detect_salary_basis(description) if salary else None,
        "stack": ", ".join(stack), "description": description, "search_terms": TERMS[serial % len(TERMS)],
        "status": spec.get("status", "applied"), "first_seen_run": run_id,
        "first_seen_at": seen.isoformat(), "last_seen_at": seen.isoformat(),
        "pinned_at": (seen + timedelta(minutes=20)).isoformat() if spec.get("pinned") else None,
    }
    cols = ", ".join(job)
    marks = ", ".join(f":{c}" for c in job)
    job_id = conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", job).lastrowid
    set_job_stack(conn, job_id, stack)
    for other in conn.execute("SELECT id, title, company, location, dup_group FROM jobs WHERE id != ?", (job_id,)):
        if is_possible_duplicate(job, dict(other)):
            group = other["dup_group"] or other["id"]
            conn.execute("UPDATE jobs SET dup_group = ? WHERE id IN (?, ?)", (group, job_id, other["id"]))
            break
    return job_id


def seed(conn, calendar: Calendar) -> dict[str, int]:
    """Fill an empty database. Returns the ids worth a screenshot."""
    run_at = {k: calendar.stamp(calendar.weekday(k), "08:30") for k in sorted(RUN_DAYS, reverse=True)}  # oldest first
    run_ids = {}
    for k, started in run_at.items():
        run_ids[k] = conn.execute(
            "INSERT INTO runs (started_at, finished_at, status) VALUES (?, ?, 'success')",
            (started.isoformat(), (started + timedelta(minutes=4)).isoformat())).lastrowid
        for site in SITES:
            conn.execute("INSERT INTO run_sites (run_id, site, hours_old, status) VALUES (?, ?, ?, 'success')",
                         (run_ids[k], site, 24 if k == 0 else 72))
    applied_k = {key: back for key, back, *_ in APPLICATIONS}

    def run_index(back: int) -> int:           # the newest run on or before that weekday
        return min(k for k in RUN_DAYS if k >= back)

    job_ids: dict[str, int] = {}
    serial = 0
    for spec in JOBS:
        serial += 1
        key = spec["key"]
        if key in applied_k:
            k = run_index(applied_k[key])
        else:
            k = RUN_DAYS[SEEN_RUN[key]]
        job_id = _insert_job(conn, spec, run_ids[k], run_at[k] + timedelta(minutes=serial), calendar, serial)
        job_ids.setdefault(key, job_id)
    for k, run_id in run_ids.items():
        new = conn.execute("SELECT count(*) FROM jobs WHERE first_seen_run = ?", (run_id,)).fetchone()[0]
        conn.execute("UPDATE runs SET new_count = ?, seen_count = ? WHERE id = ?", (new, new * 3 + 7, run_id))

    phases = {p["name"]: p["id"] for p in conn.execute("SELECT id, name FROM phases")}
    app_ids: dict[str, int] = {}
    for key, back, hhmm, final_phase, outcome, steps in APPLICATIONS:
        applied = calendar.stamp(calendar.weekday(back), hhmm).isoformat()
        app_id = app_ids[key] = apps.create_application_for_job(conn, job_ids[key], applied)
        current = phases["Applied"]
        for step in steps:
            if step[0] == "next":                 # an upcoming interview: weekdays ahead of today
                _, ahead, at, phase, note = step
                day = calendar.next_weekday(ahead)
            else:
                after, at, phase, note = step
                day = calendar.weekday(back - after)
            when = calendar.stamp(day, at).isoformat()
            if phase:
                if phases[phase] != current:
                    apps.move_application(conn, app_id, phases[phase], when)
                    current = phases[phase]
            if note:
                apps.add_entry(conn, app_id, str(current), when[:16], note)
        assert current == phases[final_phase], f"{key}: steps end on the wrong phase"
        if outcome != "active":
            apps.set_outcome(conn, app_id, outcome)
    return {"featured_job": job_ids[FEATURED_JOB], "featured_application": app_ids[FEATURED_APPLICATION]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, required=True, help="config.toml whose [storage] database is new")
    parser.add_argument("--today", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                        help="the demo's today (default: the real today)")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if cfg.database.exists():
        print(f"Refusing to seed demo data: {cfg.database} already exists (it needs a new, empty location).",
              file=sys.stderr)
        return 1
    calendar = Calendar(args.today or date.today())
    conn = connect(cfg.database)
    with conn:
        ids = seed(conn, calendar)
    print(f"Seeded demo data for {calendar.today} into {cfg.database}")
    for name, value in ids.items():
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
