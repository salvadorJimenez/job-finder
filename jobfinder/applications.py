"""Applications and their phases. Functions take an open connection and never commit:
the caller owns the transaction (`with conn:`)."""
from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, tzinfo

DEFAULT_PHASES = ("Applied", "Recruiter screen", "Hiring manager", "Technical", "Final", "Offer")
OUTCOMES = ("active", "accepted", "rejected", "withdrawn", "ghosted")
PHASE_NAME_MAX = 40
UNASSIGNED = "Unassigned"
NOTES_MAX = 5000
MANUAL_FIELD_MAX = 200
MANUAL_LINK_MAX = 2000
WORK_MODES = ("remote", "hybrid", "onsite")


class PhaseInUse(Exception):
    def __init__(self, name: str, count: int):
        self.name = name
        self.count = count
        noun = "application" if count == 1 else "applications"
        super().__init__(f"Move the {count} {noun} in {name} to another phase first")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


# --- phases -------------------------------------------------------------------

def seed_default_phases(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO phases (name, position) VALUES (?, ?)",
        [(name, i) for i, name in enumerate(DEFAULT_PHASES, start=1)],
    )


def list_phases(conn: sqlite3.Connection) -> list[dict]:
    """Phases in the user's order, each with the number of applications on it."""
    rows = conn.execute(
        """SELECT p.id, p.name, p.position,
                  (SELECT count(*) FROM applications a WHERE a.phase_id = p.id) AS application_count
           FROM phases p ORDER BY p.position, p.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def _valid_phase_name(conn: sqlite3.Connection, name: str, own_id: int | None = None) -> str:
    """The trimmed name; ValueError when empty, too long or used by another phase (ignoring case)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Phase name is required")
    if len(name) > PHASE_NAME_MAX:
        raise ValueError(f"Phase name must be at most {PHASE_NAME_MAX} characters")
    if any(p["name"].casefold() == name.casefold() for p in list_phases(conn) if p["id"] != own_id):
        raise ValueError(f"A phase named {name} already exists")
    return name


def add_phase(conn: sqlite3.Connection, name: str) -> int:
    """Append a phase; ValueError when the name is empty, too long or already used."""
    name = _valid_phase_name(conn, name)
    position = conn.execute("SELECT coalesce(max(position), 0) + 1 FROM phases").fetchone()[0]
    return conn.execute("INSERT INTO phases (name, position) VALUES (?, ?)", (name, position)).lastrowid


def rename_phase(conn: sqlite3.Connection, phase_id: int, name: str) -> None:
    """Rename a phase and the timeline entries linked to it (`timeline_entries.phase_id`), in the
    caller's transaction. LookupError for an unknown phase; ValueError for a name Add would refuse,
    except that the phase may keep its own name or change only its capitalization. Entries with no
    phase_id (from a removed phase, or that could not be linked) are never touched."""
    row = conn.execute("SELECT name FROM phases WHERE id = ?", (phase_id,)).fetchone()
    if row is None:
        raise LookupError("Unknown phase")
    old = row["name"]
    name = _valid_phase_name(conn, name, own_id=phase_id)
    conn.execute("UPDATE phases SET name = ? WHERE id = ?", (name, phase_id))
    # the automatic text of a move ("Moved to <name>") is kept in step; anything a person typed is not
    conn.execute(
        "UPDATE timeline_entries SET notes = ? WHERE phase_id = ? AND kind = 'move' AND notes = ?",
        (f"Moved to {name}", phase_id, f"Moved to {old}"),
    )
    conn.execute("UPDATE timeline_entries SET phase_name = ? WHERE phase_id = ?", (name, phase_id))


def _compact(conn: sqlite3.Connection) -> None:
    for position, phase in enumerate(list_phases(conn), start=1):
        conn.execute("UPDATE phases SET position = ? WHERE id = ?", (position, phase["id"]))


def delete_phase(conn: sqlite3.Connection, phase_id: int) -> None:
    """LookupError for an unknown phase, PhaseInUse while applications are on it."""
    row = conn.execute("SELECT name FROM phases WHERE id = ?", (phase_id,)).fetchone()
    if row is None:
        raise LookupError("Unknown phase")
    count = conn.execute("SELECT count(*) FROM applications WHERE phase_id = ?", (phase_id,)).fetchone()[0]
    if count:
        raise PhaseInUse(row["name"], count)
    # History stays: entries keep their phase_name snapshot but lose the link, so a later phase
    # that reuses this id (or this name) never inherits them.
    conn.execute("UPDATE timeline_entries SET phase_id = NULL WHERE phase_id = ?", (phase_id,))
    conn.execute("DELETE FROM phases WHERE id = ?", (phase_id,))
    _compact(conn)


def move_phase(conn: sqlite3.Connection, phase_id: int, direction: str) -> None:
    """Swap with the neighbour; a no-op at either end. ValueError / LookupError on bad input."""
    if direction not in ("up", "down"):
        raise ValueError("Direction must be up or down")
    ids = [p["id"] for p in list_phases(conn)]
    if phase_id not in ids:
        raise LookupError("Unknown phase")
    i = ids.index(phase_id)
    j = i - 1 if direction == "up" else i + 1
    if not 0 <= j < len(ids):
        return
    ids[i], ids[j] = ids[j], ids[i]
    _apply_order(conn, ids)


def _apply_order(conn: sqlite3.Connection, ids: list[int]) -> None:
    for position, phase_id in enumerate(ids, start=1):
        conn.execute("UPDATE phases SET position = ? WHERE id = ?", (position, phase_id))


def set_phase_order(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Reorder to exactly `ids`; ValueError unless it is a permutation of the current ids."""
    current = [p["id"] for p in list_phases(conn)]
    if len(ids) != len(set(ids)) or set(ids) != set(current):
        raise ValueError("Order must list every phase exactly once")
    _apply_order(conn, ids)


# --- applications ---------------------------------------------------------------

def _add_move_entry(conn: sqlite3.Connection, app_id: int, phase_id: int | None, phase_name: str,
                    now: str, notes: str) -> None:
    conn.execute(
        """INSERT INTO timeline_entries (application_id, phase_id, phase_name, happened_at, notes, kind)
           VALUES (?, ?, ?, ?, ?, 'move')""",
        (app_id, phase_id, phase_name, now, notes),
    )


def create_application_for_job(conn: sqlite3.Connection, job_id: int, now: str) -> int | None:
    """Create the job's application in the first phase; None if it exists or the job is unknown."""
    if conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
        return None
    if conn.execute("SELECT 1 FROM applications WHERE job_id = ?", (job_id,)).fetchone():
        return None
    first = conn.execute("SELECT id, name FROM phases ORDER BY position, id LIMIT 1").fetchone()
    app_id = conn.execute(
        "INSERT INTO applications (job_id, phase_id, applied_at) VALUES (?, ?, ?)",
        (job_id, first["id"] if first else None, now),
    ).lastrowid
    _add_move_entry(conn, app_id, first["id"] if first else None, first["name"] if first else UNASSIGNED,
                    now, "Applied")
    return app_id


def move_application(conn: sqlite3.Connection, app_id: int, phase_id: int, now: str) -> None:
    """Put the application on `phase_id` and record the move; LookupError for unknown ids.
    Moving to the phase it is already on changes nothing and records nothing."""
    phase = conn.execute("SELECT name FROM phases WHERE id = ?", (phase_id,)).fetchone()
    if phase is None:
        raise LookupError("Unknown phase")
    current = conn.execute("SELECT phase_id FROM applications WHERE id = ?", (app_id,)).fetchone()
    if current is None:
        raise LookupError("Unknown application")
    if current["phase_id"] == phase_id:
        return
    conn.execute("UPDATE applications SET phase_id = ? WHERE id = ?", (phase_id, app_id))
    _add_move_entry(conn, app_id, phase_id, phase["name"], now, f"Moved to {phase['name']}")


def set_outcome(conn: sqlite3.Connection, app_id: int, outcome: str) -> None:
    """ValueError for an unknown outcome, LookupError for an unknown application."""
    if outcome not in OUTCOMES:
        raise ValueError(f"Outcome must be one of: {', '.join(OUTCOMES)}")
    if conn.execute("UPDATE applications SET outcome = ? WHERE id = ?", (outcome, app_id)).rowcount == 0:
        raise LookupError("Unknown application")


# --- times ---------------------------------------------------------------------------

def _aware(value: str, tz: tzinfo) -> datetime:
    """Parse a stored timestamp; naive ones (from datetime-local inputs) are local to `tz`."""
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def _applied_on(value: str, tz: tzinfo) -> date:
    return _aware(value, tz).astimezone(tz).date()


def _day_label(day: date, today: date) -> str:
    """`Tue Nov 3 · yesterday`; the year only when it is not this one."""
    label = f"{day:%a %b} {day.day}" + (f" {day.year}" if day.year != today.year else "")
    near = {0: "today", -1: "yesterday", 1: "tomorrow"}.get((day - today).days)
    return f"{label} · {near}" if near else label


def _entry_times(entry: sqlite3.Row, now: datetime) -> dict:
    """Display fields of a timeline entry. Only notes can be upcoming (moves are recorded as they happen);
    "now or later" counts, like the next interview on the board."""
    tz = now.tzinfo
    when = _aware(entry["happened_at"], tz).astimezone(tz)
    return {
        "when": when.strftime("%Y-%m-%d %H:%M"),
        "input_value": when.strftime("%Y-%m-%dT%H:%M:%S"),
        "time": when.strftime("%H:%M"),
        "day": when.date().isoformat(),
        "day_label": _day_label(when.date(), now.date()),
        "short": f"{when:%b} {when.day} · {when:%H:%M}",
        "upcoming": entry["kind"] == "note" and when >= now,
    }


# --- filtering, sorting and the two views ---------------------------------------------------

SORTS = ("newest", "oldest", "activity", "interview", "company", "phase")
DEFAULT_SORT = "newest"
SORT_LABELS = {
    "newest": "Applied: newest", "oldest": "Applied: oldest", "activity": "Last activity",
    "interview": "Next interview", "company": "Company A–Z", "phase": "Furthest phase",
}
WITHIN_DAYS = (7, 30, 90)
CV_FILTERS = ("attached", "missing")
UNASSIGNED_KEY = "unassigned"
SEARCH_MAX = 200


def _first(raw: Mapping[str, Sequence[str]], key: str) -> str:
    values = raw.get(key) or []
    return values[0].strip() if values else ""


def _valid_date(text: str) -> str:
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) and _is_date(text) else ""


def parse_filters(raw: Mapping[str, Sequence[str]], *, phase_ids: set[int], sites: set[str]) -> dict:
    """Normalized filters from query parameters (name -> list of values). Anything that is not a
    known value (unknown outcome, phase or source, a bad date or day, an odd window, an unknown sort)
    is ignored, so a stale link never hides everything. `closed=0` is the old "hide closed" link."""
    outcome = list(dict.fromkeys(v for v in raw.get("outcome") or [] if v in OUTCOMES))
    if not outcome and _first(raw, "closed") == "0":
        outcome = ["active"]
    phase = []
    for value in raw.get("phase") or []:
        if value == UNASSIGNED_KEY:
            phase.append(UNASSIGNED_KEY)
        elif value.isascii() and value.isdigit() and int(value) in phase_ids:
            phase.append(str(int(value)))
    within = _first(raw, "within")
    site = _first(raw, "site")
    cv = _first(raw, "cv")
    sort = _first(raw, "sort")
    return {
        "q": _first(raw, "q")[:SEARCH_MAX],
        "outcome": outcome,
        "phase": list(dict.fromkeys(phase)),
        "site": site if site in sites else "",
        "cv": cv if cv in CV_FILTERS else "",
        "interview": _first(raw, "interview") == "1",
        "within": int(within) if within.isdigit() and int(within) in WITHIN_DAYS else None,
        "from_date": _valid_date(_first(raw, "from")),
        "to_date": _valid_date(_first(raw, "to")),
        "day": _valid_date(_first(raw, "day")),
        "sort": sort if sort in SORTS else DEFAULT_SORT,
    }


def _local(column: str) -> str:
    """SQL for a stored timestamp as local wall-clock 'YYYY-MM-DD HH:MM:SS'. Values with an offset
    (moves, applied dates) are converted with the offset of `now` (:off); naive ones (notes, typed
    in local time) are already local. These strings compare and sort correctly as text."""
    return (f"CASE WHEN {column} GLOB '*[+-][0-9][0-9]:[0-9][0-9]' OR {column} LIKE '%Z' "
            f"THEN datetime({column}, :off) ELSE datetime({column}) END")


def local_offset(now: datetime) -> str:
    """The UTC offset of `now` as '+HH:MM', for `_local` (the offset of the app clock)."""
    off = now.strftime("%z")
    return f"{off[:3]}:{off[3:]}" if off else "+00:00"


def _base(now: datetime) -> tuple[str, dict]:
    """One row per application with everything the views show: job, phase and its position, CV, the
    latest entry that already happened and the earliest upcoming note. The timeline is aggregated
    in this one statement, never per application."""
    params = {"off": local_offset(now), "now": now.strftime("%Y-%m-%d %H:%M:%S")}
    sql = f"""
    WITH t AS (SELECT id, application_id, kind, notes, phase_name, {_local('happened_at')} AS ts FROM timeline_entries),
    last AS (SELECT application_id, kind, notes, phase_name, ts
             FROM (SELECT t.*, row_number() OVER (PARTITION BY application_id ORDER BY ts DESC, id DESC) AS rn
                   FROM t WHERE ts <= :now) WHERE rn = 1),
    nxt AS (SELECT application_id, min(ts) AS ts FROM t WHERE kind = 'note' AND ts >= :now GROUP BY application_id),
    ph AS (SELECT id, name, row_number() OVER (ORDER BY position, id) AS idx, count(*) OVER () AS n FROM phases),
    base AS (
      SELECT a.id, a.phase_id, a.outcome, a.cv_id, j.title, j.company, j.site, j.work_mode,
             ph.name AS phase_name, ph.idx AS phase_idx, ph.n AS phase_count, cv.original_name AS cv_name,
             {_local('a.applied_at')} AS applied_at,
             last.ts AS last_at, last.kind AS last_kind, last.notes AS last_notes, last.phase_name AS last_phase,
             nxt.ts AS next_at
      FROM applications a
      JOIN jobs j ON j.id = a.job_id
      LEFT JOIN ph ON ph.id = a.phase_id
      LEFT JOIN cv_files cv ON cv.id = a.cv_id
      LEFT JOIN last ON last.application_id = a.id
      LEFT JOIN nxt ON nxt.application_id = a.id)
    """
    return sql, params


def _like(text: str) -> str:
    """LIKE pattern for a case-insensitive "contains" with no wildcards from the user."""
    escaped = text.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(f: dict, now: datetime, skip: str = "") -> tuple[list[str], dict]:
    """WHERE clauses for the filters, AND across groups. `skip` leaves one group out (chip counts)."""
    where: list[str] = []
    params: dict = {}
    if f["q"]:
        where.append(
            "(casefold(company) LIKE :q ESCAPE '\\' OR casefold(title) LIKE :q ESCAPE '\\' OR EXISTS ("
            "SELECT 1 FROM timeline_entries te WHERE te.application_id = base.id AND te.kind = 'note'"
            " AND casefold(te.notes) LIKE :q ESCAPE '\\'))")
        params["q"] = _like(f["q"])
    if f["outcome"] and skip != "outcome":
        names = {f"o{i}": v for i, v in enumerate(f["outcome"])}
        where.append(f"outcome IN ({', '.join(':' + k for k in names)})")
        params.update(names)
    if f["phase"] and skip != "phase":
        ids = {f"p{i}": int(v) for i, v in enumerate(v for v in f["phase"] if v != UNASSIGNED_KEY)}
        parts = [f"phase_id IN ({', '.join(':' + k for k in ids)})"] if ids else []
        if UNASSIGNED_KEY in f["phase"]:
            parts.append("phase_id IS NULL")
        where.append(f"({' OR '.join(parts)})")
        params.update(ids)
    if f["site"]:
        where.append("site = :site")
        params["site"] = f["site"]
    if f["cv"]:
        where.append("cv_id IS NOT NULL" if f["cv"] == "attached" else "cv_id IS NULL")
    if f["interview"]:
        where.append("next_at IS NOT NULL")
    if f["within"]:
        where.append("substr(applied_at, 1, 10) >= :since")
        params["since"] = (now.date() - timedelta(days=f["within"])).isoformat()
    if f["from_date"]:
        where.append("substr(applied_at, 1, 10) >= :from_date")
        params["from_date"] = f["from_date"]
    if f["to_date"]:
        where.append("substr(applied_at, 1, 10) <= :to_date")
        params["to_date"] = f["to_date"]
    if f["day"]:
        # Anything dated that local day: the application itself, or any entry of its timeline
        # (notes, upcoming interviews, phase moves). The activity calendar links here.
        where.append(f"(substr(applied_at, 1, 10) = :day OR EXISTS (SELECT 1 FROM timeline_entries te "
                     f"WHERE te.application_id = base.id AND substr({_local('te.happened_at')}, 1, 10) = :day))")
        params["day"] = f["day"]
    return where, params


_ORDER = {
    "newest": "applied_at DESC, id DESC",
    "oldest": "applied_at ASC, id DESC",
    "activity": "last_at IS NULL, last_at DESC, applied_at DESC, id DESC",
    "interview": "next_at IS NULL, next_at ASC, applied_at DESC, id DESC",
    "company": "(company IS NULL OR trim(company) = ''), casefold(company), applied_at DESC, id DESC",
    "phase": "phase_idx IS NULL, phase_idx DESC, applied_at DESC, id DESC",
}


def _short(text: str) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def ago_label(days: int) -> str:
    """`today`, `12d ago`, and months (`2mo ago`) from 60 days on, so a long hunt stays readable."""
    if days <= 0:
        return "today"
    return f"{days}d ago" if days < 60 else f"{days // 30}mo ago"


def _short_day(day: date, today: date) -> str:
    """`Oct 25`, with the year only when it is not this one."""
    return f"{day:%b} {day.day}" + (f" {day.year}" if day.year != today.year else "")


def _row(r: sqlite3.Row, now: datetime) -> dict:
    last = None
    today = now.date()
    if r["last_at"]:
        text = f"→ {r['last_phase']}" if r["last_kind"] == "move" else (_short(r["last_notes"]) or r["last_phase"])
        last = {"day": r["last_at"][:10], "text": text, "short": _short_day(date.fromisoformat(r["last_at"][:10]), today)}
    days_since = max((today - date.fromisoformat(r["applied_at"][:10])).days, 0)
    next_at = datetime.fromisoformat(r["next_at"]) if r["next_at"] else None
    return {
        "id": r["id"], "phase_id": r["phase_id"], "outcome": r["outcome"], "title": r["title"],
        "company": r["company"], "site": r["site"], "work_mode": r["work_mode"],
        "phase_name": r["phase_name"], "phase_idx": r["phase_idx"], "phase_count": r["phase_count"],
        "cv_name": r["cv_name"],
        "applied_on": r["applied_at"][:10],
        "days_since": days_since,
        "ago": ago_label(days_since),
        "last_activity": last,
        "next_interview": r["next_at"][:16] if r["next_at"] else None,
        "next_label": f"{next_at:%a %b} {next_at.day} · {next_at:%H:%M}" if next_at else None,
    }


def find_applications(conn: sqlite3.Connection, now: datetime, f: dict) -> list[dict]:
    """The applications matching `f`, in its sort order: filtering, aggregation and ordering all
    happen in a single SQL statement."""
    base, params = _base(now)
    where, extra = _where(f, now)
    sql = f"{base} SELECT * FROM base{' WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {_ORDER[f['sort']]}"
    return [_row(r, now) for r in conn.execute(sql, {**params, **extra})]


def facet_counts(conn: sqlite3.Connection, now: datetime, f: dict, group: str) -> dict[str, int]:
    """Counts per value of the `outcome` or `phase` chips under every other active filter (the
    group's own selection is ignored, as its chips combine with OR)."""
    base, params = _base(now)
    where, extra = _where(f, now, skip=group)
    key = "outcome" if group == "outcome" else f"CASE WHEN phase_id IS NULL THEN '{UNASSIGNED_KEY}' ELSE CAST(phase_id AS TEXT) END"
    sql = f"{base} SELECT {key}, count(*) FROM base{' WHERE ' + ' AND '.join(where) if where else ''} GROUP BY 1"
    return dict(conn.execute(sql, {**params, **extra}).fetchall())


def application_sites(conn: sqlite3.Connection) -> list[str]:
    """Sources that have at least one application, alphabetical."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT j.site FROM applications a JOIN jobs j ON j.id = a.job_id ORDER BY j.site")]


def totals(conn: sqlite3.Connection) -> dict:
    """Active and closed applications overall, whatever the filters say."""
    active = conn.execute("SELECT count(*) FROM applications WHERE outcome = 'active'").fetchone()[0]
    count = conn.execute("SELECT count(*) FROM applications").fetchone()[0]
    return {"total": count, "active": active, "closed": count - active}


def board(rows: list[dict], phases: list[dict], show_closed: bool = True) -> list[dict]:
    """Kanban columns from already filtered and sorted `rows`: one per phase, Unassigned when needed,
    then Closed. Closed applications (outcome != active) are only ever in the Closed column."""
    open_rows = [r for r in rows if r["outcome"] == "active"]
    columns = [
        {"key": f"phase-{p['id']}", "name": p["name"], "phase_id": p["id"],
         "cards": [r for r in open_rows if r["phase_id"] == p["id"]]}
        for p in phases
    ]
    unassigned = [r for r in open_rows if r["phase_id"] is None]
    if unassigned:
        columns.append({"key": "unassigned", "name": UNASSIGNED, "phase_id": None, "cards": unassigned})
    if show_closed:
        columns.append({"key": "closed", "name": "Closed", "phase_id": None,
                        "cards": [r for r in rows if r["outcome"] != "active"]})
    return columns


# --- application page --------------------------------------------------------------------

def get_application(conn: sqlite3.Connection, app_id: int, now: datetime) -> dict | None:
    """The application with its job, timeline (newest first) and attached CV, or None."""
    tz = now.tzinfo
    row = conn.execute(
        """SELECT a.id, a.job_id, a.phase_id, a.outcome, a.applied_at, a.cv_id,
                  j.title, j.company, j.url
           FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.id = ?""",
        (app_id,),
    ).fetchone()
    if row is None:
        return None
    app = dict(row)
    app["applied_on"] = _applied_on(row["applied_at"], tz).isoformat()
    app["posting_url"] = row["url"] if (row["url"] or "").lower().startswith(("http://", "https://")) else None
    raw = conn.execute("SELECT * FROM timeline_entries WHERE application_id = ?", (app_id,)).fetchall()
    app["entries"] = [
        {**dict(e), **_entry_times(e, now)}
        for e in sorted(raw, key=lambda e: (_aware(e["happened_at"], tz), e["id"]), reverse=True)
    ]
    cv = conn.execute("SELECT id, original_name FROM cv_files WHERE id = ?", (row["cv_id"],)).fetchone()
    app["cv"] = dict(cv) if cv else None
    return app


# --- timeline entries ------------------------------------------------------------------------

# Seconds are optional (a bare HH:MM means :00); fractions of a second are accepted and dropped.
_WHEN = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?")


def _entry_input(conn: sqlite3.Connection, phase_id: str, happened_at: str, notes: str,
                 kept: tuple[str, int | None] | None = None) -> tuple[str, int | None, str, str]:
    """Validated (phase name, phase id, ISO time, notes); ValueError with a message otherwise.
    phase_id "keep" keeps `kept` (name, id): the entry's snapshot, e.g. of a phase that was removed."""
    if phase_id == "keep" and kept is not None:
        name, pid = kept
    else:
        try:
            pid = int(phase_id)
        except (TypeError, ValueError):
            raise ValueError("Pick a phase") from None
        phase = conn.execute("SELECT name FROM phases WHERE id = ?", (pid,)).fetchone()
        if phase is None:
            raise ValueError("Unknown phase")
        name = phase["name"]
    when = (happened_at or "").strip()
    try:
        if not _WHEN.fullmatch(when):
            raise ValueError
        when = datetime.fromisoformat(when).replace(microsecond=0).isoformat()
    except ValueError:
        raise ValueError("Date and time are required, like 2026-11-10T14:00 (seconds optional)") from None
    notes = (notes or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(notes) > NOTES_MAX:
        raise ValueError(f"Notes must be at most {NOTES_MAX} characters")
    return name, pid, when, notes


def _require_entry(conn: sqlite3.Connection, app_id: int, entry_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, kind, phase_name, phase_id FROM timeline_entries WHERE id = ? AND application_id = ?", (entry_id, app_id)
    ).fetchone()
    if row is None:
        raise LookupError("Unknown entry")
    return row


def add_entry(conn: sqlite3.Connection, app_id: int, phase_id: str, happened_at: str, notes: str) -> int:
    """LookupError for an unknown application, ValueError for invalid input."""
    if conn.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone() is None:
        raise LookupError("Unknown application")
    name, pid, when, notes = _entry_input(conn, phase_id, happened_at, notes)
    return conn.execute(
        """INSERT INTO timeline_entries (application_id, phase_id, phase_name, happened_at, notes, kind)
           VALUES (?, ?, ?, ?, ?, 'note')""",
        (app_id, pid, name, when, notes),
    ).lastrowid


def update_entry(conn: sqlite3.Connection, app_id: int, entry_id: int,
                 phase_id: str, happened_at: str, notes: str) -> None:
    """LookupError unless the entry belongs to the application; ValueError for move entries or bad input."""
    entry = _require_entry(conn, app_id, entry_id)
    if entry["kind"] != "note":
        raise ValueError("Only notes can be edited")
    name, pid, when, notes = _entry_input(conn, phase_id, happened_at, notes,
                                          kept=(entry["phase_name"], entry["phase_id"]))
    conn.execute(
        "UPDATE timeline_entries SET phase_id = ?, phase_name = ?, happened_at = ?, notes = ? WHERE id = ?",
        (pid, name, when, notes, entry_id),
    )


def delete_entry(conn: sqlite3.Connection, app_id: int, entry_id: int) -> None:
    """LookupError unless the entry belongs to the application."""
    _require_entry(conn, app_id, entry_id)
    conn.execute("DELETE FROM timeline_entries WHERE id = ?", (entry_id,))


# --- manual applications ------------------------------------------------------------------------

MANUAL_SITE = "manual"
MANUAL_RUN_STATUS = "manual"


def validate_manual(form: dict[str, str]) -> tuple[dict, list[str]]:
    """(cleaned values, error messages) for the manual application form."""
    v = {k: (form.get(k) or "").strip() for k in
         ("title", "company", "link", "location", "work_mode", "salary", "applied_at")}
    errors = []
    for key, label, required in (("title", "Title", True), ("company", "Company", True),
                                 ("location", "Location", False), ("salary", "Salary", False)):
        if required and not v[key]:
            errors.append(f"{label} is required")
        elif len(v[key]) > MANUAL_FIELD_MAX:
            errors.append(f"{label} must be at most {MANUAL_FIELD_MAX} characters")
    if v["link"]:
        if not v["link"].lower().startswith(("http://", "https://")):
            errors.append("Link must start with http:// or https://")
        elif len(v["link"]) > MANUAL_LINK_MAX:
            errors.append(f"Link must be at most {MANUAL_LINK_MAX} characters")
    if v["work_mode"] and v["work_mode"] not in WORK_MODES:
        errors.append("Work mode must be remote, hybrid or onsite")
    if not v["applied_at"]:
        errors.append("Applied date is required")
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v["applied_at"]) or not _is_date(v["applied_at"]):
        errors.append("Applied date must be a valid date, like 2026-11-04")
    return v, errors


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _manual_run_id(conn: sqlite3.Connection, stamp: str) -> int:
    """`jobs.first_seen_run` is NOT NULL, so manual jobs point at one shared run with status
    'manual'. Searches only consider 'success'/'partial' runs, so it never counts as a search
    and manual jobs are never "new"."""
    row = conn.execute("SELECT id FROM runs WHERE status = ? ORDER BY id LIMIT 1", (MANUAL_RUN_STATUS,)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO runs (started_at, finished_at, status) VALUES (?, ?, ?)", (stamp, stamp, MANUAL_RUN_STATUS)
    ).lastrowid


def create_manual_application(conn: sqlite3.Connection, values: dict, now: datetime) -> int:
    """Create the `manual` job and its application in the first phase, dated `values['applied_at']`."""
    applied = datetime.combine(date.fromisoformat(values["applied_at"]), time(), tzinfo=now.tzinfo).isoformat()
    stamp = now.isoformat()
    job_id = conn.execute(
        """INSERT INTO jobs (site, external_id, url, title, company, location, work_mode, description,
                             status, first_seen_run, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?, ?)""",
        (
            MANUAL_SITE, f"manual-{uuid.uuid4().hex}", values["link"], values["title"], values["company"],
            values["location"] or None, values["work_mode"] or None,
            f"Salary: {values['salary']}" if values["salary"] else "",
            _manual_run_id(conn, stamp), stamp, stamp,
        ),
    ).lastrowid
    return create_application_for_job(conn, job_id, applied)
