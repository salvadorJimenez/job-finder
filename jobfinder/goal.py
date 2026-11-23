"""Daily application goal: per-day counts, the weekday streak, the header counter, the
30-day totals and the activity calendar. The goal applies Monday to Friday; applications made on a weekend still count
in the totals but never add to, or break, the streak.

Everything takes the app clock's `now`, so the "day" is always the local calendar date."""
from __future__ import annotations

import unicodedata

import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, tzinfo

from . import applications

HISTORY_DAYS = 30
FIRST_WINDOW_DAYS = 60       # covers the history and the week; the streak query widens it on demand
SEGMENTS_MAX = 10


def is_weekday(day: date) -> bool:
    return day.weekday() < 5


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def daily_counts(conn: sqlite3.Connection, since: date, tz: tzinfo) -> dict[date, int]:
    """Applications per local calendar date, from `since` on, in one query.

    The SQL bound is a day wider than `since`: stamps carry different UTC offsets, so only the
    date prefix is safe to compare as text. The exact local date is worked out in Python."""
    bound = (since - timedelta(days=1)).isoformat()
    rows = conn.execute("SELECT applied_at FROM applications WHERE applied_at >= ?", (bound,))
    days = (applications._applied_on(r[0], tz) for r in rows)
    return dict(Counter(d for d in days if d >= since))


def _streak(conn: sqlite3.Connection, today: date, goal: int, tz: tzinfo,
            counts: dict[date, int], since: date) -> int:
    """Consecutive weekdays at or above the goal, counting back from today. Today counts when
    met, and does not break the streak while still short. Weekend days are skipped."""
    n, day, window = 0, today, FIRST_WINDOW_DAYS
    while True:
        if day < since:                      # the streak outran the data: widen the window
            window *= 2
            since = today - timedelta(days=window)
            counts = daily_counts(conn, since, tz)
        if is_weekday(day):
            if counts.get(day, 0) >= goal:
                n += 1
            elif day != today:
                return n
        day -= timedelta(days=1)


def header(conn: sqlite3.Connection, now: datetime, goal: int) -> dict | None:
    """What the header counter shows, or None when the goal is off (0)."""
    if goal <= 0:
        return None
    today = now.date()
    since = today - timedelta(days=FIRST_WINDOW_DAYS)
    counts = daily_counts(conn, since, now.tzinfo)
    count = counts.get(today, 0)
    streak = _streak(conn, today, goal, now.tzinfo, counts, since)
    if not is_weekday(today):
        kind, segments = "weekend", []
        sr = f"{_plural(count, 'application')} today, weekend, no goal"
    else:
        kind = "met" if count >= goal else "progress"
        total = min(goal, SEGMENTS_MAX)
        filled = min(count if goal <= SEGMENTS_MAX else count * SEGMENTS_MAX // goal, total)
        segments = [i < filled for i in range(total)]
        sr = f"{count} of {_plural(goal, 'application')} today"
    return {"kind": kind, "count": count, "goal": goal, "streak": streak, "segments": segments, "sr": sr,
            "streak_title": f"{_plural(streak, 'weekday')} in a row meeting the goal"}


def history(conn: sqlite3.Connection, now: datetime, goal: int, days: int = HISTORY_DAYS) -> dict | None:
    """The totals shown next to the calendar: this week (since Monday), the last `days` days and the
    weekdays that met the goal out of the weekdays so far. None when the goal is off."""
    if goal <= 0:
        return None
    today = now.date()
    first = today - timedelta(days=days - 1)
    counts = daily_counts(conn, first, now.tzinfo)
    window = [first + timedelta(days=i) for i in range(days)]
    weekdays = [d for d in window if is_weekday(d) and not (d == today and counts.get(d, 0) < goal)]
    week_start = today - timedelta(days=today.weekday())
    return {
        "week": sum(n for d, n in counts.items() if week_start <= d <= today),
        "total": sum(counts.get(d, 0) for d in window),
        "met": sum(counts.get(d, 0) >= goal for d in window if is_weekday(d)),
        "weekdays": len(weekdays),
    }


# --- activity calendar -------------------------------------------------------------------------------

CAL_MIN_WEEKS = 8            # a young hunt still gets a calendar that looks like one
CAL_MAX_WEEKS = 26           # about half a year; older activity is left out
UPCOMING_WEEKS_MAX = 8       # an interview further away than this does not stretch the grid
LEVEL_STEPS = (1, 2, 3, 5)   # events per day for levels 1..4: 1, 2, 3-4, 5 or more
# Matched against the note in lowercase without accents, so "No pasé" matches "no pase".
REJECTION_PHRASES = (
    "reject", "not moving forward", "not continuing", "not advancing", "not progressing",
    "rechaz", "no pase", "no continu", "no avanz", "no segui", "descartad", "no fui seleccionad",
)


def _plain(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))
SNIPPET_MAX = 40
SNIPPETS_SHOWN = 3


def _level(count: int) -> int:
    return sum(count >= step for step in LEVEL_STEPS)


def _snippet(text: str) -> str:
    return text if len(text) <= SNIPPET_MAX else text[:SNIPPET_MAX].rstrip() + "…"


def _listing(items: list[str]) -> str:
    shown = items[:SNIPPETS_SHOWN]
    if len(items) > SNIPPETS_SHOWN:
        shown.append(f"+{len(items) - SNIPPETS_SHOWN} more")
    return "; ".join(shown)


def _label(day: date, today: date, d: dict) -> str:
    parts = [f"{day:%a %b} {day.day}" + (f" {day.year}" if day.year != today.year else "") + (" · today" if day == today else "")]
    if d["applications"]:
        names = [f"{c} ×{n}" if n > 1 else c for c, n in Counter(d["applications"]).items()]
        parts.append(f"{_plural(len(d['applications']), 'application')} ({_listing(names)})")
    if d["notes"]:
        parts.append(f"{_plural(len(d['notes']), 'note')} ({_listing(d['notes'])})")
    if d["rejections"]:
        parts.append(_plural(d["rejections"], "rejection"))
    if d["moves"]:
        parts.append(_plural(d["moves"], "phase move"))
    if d["upcoming"]:
        parts.append(f"{_plural(len(d['upcoming']), 'upcoming interview')} ({_listing(d['upcoming'])})")
    if not (d["applications"] or d["notes"] or d["moves"] or d["upcoming"]):
        parts.append("no activity")
    return " · ".join(parts)


def _day_data() -> dict:
    return {"applications": [], "notes": [], "process": 0, "rejections": 0, "moves": 0, "upcoming": []}


def activity(conn: sqlite3.Connection, now: datetime) -> dict:
    """The activity calendar: one cell per day in whole weeks (Monday to Sunday), oldest week first.

    A day's intensity is its events: applications made, timeline notes that already happened and
    phase moves (an application's own "Applied" move is the application, not a second event).
    Markers on top: a *process note* is a note dated after the day its application was made (notes
    written on the day you applied are application notes); a *rejection* is a note whose text says
    so (see REJECTION_PHRASES, English and Spanish); an *upcoming* interview is a note that has not
    happened yet. The grid starts at the Monday of the first activity (at least CAL_MIN_WEEKS and
    at most CAL_MAX_WEEKS back) and ends on the Sunday of this week, or of the week of the furthest
    upcoming interview within UPCOMING_WEEKS_MAX. Two queries over the whole history (a private
    list is small), grouped by local date in Python; the local-date logic is the one the `day`
    filter uses, so a cell and its click agree."""
    today = now.date()
    off = {"off": applications.local_offset(now)}
    now_ts = now.strftime("%Y-%m-%d %H:%M:%S")
    app_rows = conn.execute(
        f"""SELECT ts, company FROM (
              SELECT {applications._local('a.applied_at')} AS ts, j.company
              FROM applications a JOIN jobs j ON j.id = a.job_id)""", off).fetchall()
    entry_rows = conn.execute(
        f"""SELECT kind, notes, phase_name, ts, applied_ts, company FROM (
              SELECT te.kind, te.notes, te.phase_name, {applications._local('te.happened_at')} AS ts,
                     {applications._local('a.applied_at')} AS applied_ts, j.company
              FROM timeline_entries te JOIN applications a ON a.id = te.application_id
              JOIN jobs j ON j.id = a.job_id)""", off).fetchall()

    days: dict[date, dict] = {}
    for r in app_rows:
        days.setdefault(date.fromisoformat(r["ts"][:10]), _day_data())["applications"].append(r["company"] or "Unknown company")
    for r in entry_rows:
        day = date.fromisoformat(r["ts"][:10])
        d = days.setdefault(day, _day_data())
        who = r["company"] or "Unknown company"
        if r["kind"] == "move":
            if r["notes"] != "Applied":                     # the creation move is the application itself
                d["moves"] += 1
        elif r["ts"] > now_ts:
            d["upcoming"].append(f"{who}: {_snippet(applications._short(r['notes']) or r['phase_name'])} · {r['ts'][11:16]}")
        else:
            d["notes"].append(f"{who}: {_snippet(applications._short(r['notes']) or r['phase_name'])}")
            d["process"] += r["ts"][:10] != r["applied_ts"][:10]
            d["rejections"] += any(p in _plain(r["notes"]) for p in REJECTION_PHRASES)

    week_today = today - timedelta(days=today.weekday())
    horizon = today + timedelta(weeks=UPCOMING_WEEKS_MAX)
    last = max([today, *(d for d, v in days.items() if v["upcoming"] and d <= horizon)])
    end_monday = last - timedelta(days=last.weekday())
    first = min((d for d, v in days.items() if d <= today and (v["applications"] or v["notes"] or v["moves"])), default=today)
    start = min(max(first - timedelta(days=first.weekday()), end_monday - timedelta(weeks=CAL_MAX_WEEKS - 1)),
                end_monday - timedelta(weeks=CAL_MIN_WEEKS - 1))
    weeks = (end_monday - start).days // 7 + 1

    cells, month_starts = [], []
    for i in range(weeks * 7):
        day = start + timedelta(days=i)
        d = days.get(day) or _day_data()
        count = len(d["applications"]) + len(d["notes"]) + d["moves"]
        upcoming = len(d["upcoming"])
        if day.day == 1:
            month_starts.append((i // 7 + 1, f"{day:%b}"))
        cells.append({
            "date": day.isoformat(), "col": i // 7 + 1, "row": i % 7 + 1,
            "applications": len(d["applications"]), "notes": len(d["notes"]), "process_notes": d["process"],
            "moves": d["moves"], "rejections": d["rejections"], "upcoming": upcoming,
            "count": count, "level": _level(count),
            "future": day > today and not upcoming, "today": day == today, "weekend": not is_weekday(day),
            "active": bool(count or upcoming), "label": _label(day, today, d),
        })
    months = list(dict.fromkeys(month_starts))
    if not months or months[0][0] > 1:
        first_day = start
        if not months or months[0][0] - 1 >= 3:
            months.insert(0, (1, f"{first_day:%b}"))
    return {"weeks": weeks, "cells": cells, "months": [{"col": c, "label": m} for c, m in months],
            "start": start.isoformat(), "end": (start + timedelta(days=weeks * 7 - 1)).isoformat()}
