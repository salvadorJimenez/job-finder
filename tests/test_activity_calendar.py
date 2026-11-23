"""Activity calendar on /applications: a GitHub-style grid (columns = weeks, rows = Mon..Sun) of what
happened each day: applications, timeline notes and phase moves, with markers for process notes,
rejections and upcoming interviews. Replaces the slim 30-cell strip; the daily-goal totals stay."""
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import pytest

from jobfinder import goal
from test_board import add_note, make_app
from test_goal import apply_on, env, text  # noqa: F401  (env is a fixture)
from test_web import NOW

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "jobfinder/static/style.css").read_text()
JS = (ROOT / "jobfinder/static/app.js").read_text()
# NOW is Wed 2026-11-04 10:00 (-06:00): its week runs Mon 2026-11-02 .. Sun 2026-11-08


def by_date(activity):
    return {c["date"]: c for c in activity["cells"]}


# --- grid shape ------------------------------------------------------------------------------

def test_grid_is_whole_weeks_from_monday_of_the_first_activity_to_the_sunday_of_this_week(env):
    _, conn = env
    apply_on(conn, "2026-08-12", 1)                                    # a Wednesday
    a = goal.activity(conn, NOW)
    cells = a["cells"]
    assert cells[0]["date"] == "2026-08-10" and date.fromisoformat(cells[0]["date"]).weekday() == 0
    assert cells[-1]["date"] == "2026-11-08" and date.fromisoformat(cells[-1]["date"]).weekday() == 6
    assert len(cells) % 7 == 0 and a["weeks"] == len(cells) // 7 == 13
    # column-major: week by week, Monday first, rows 1..7
    assert [c["row"] for c in cells[:14]] == [1, 2, 3, 4, 5, 6, 7] * 2
    assert [c["col"] for c in cells[:14]] == [1] * 7 + [2] * 7
    assert [c["date"] for c in cells] == [(date(2026, 8, 10) + timedelta(days=i)).isoformat() for i in range(len(cells))]


def test_grid_has_a_minimum_of_weeks_when_the_hunt_is_young(env):
    _, conn = env
    assert goal.activity(conn, NOW)["weeks"] == goal.CAL_MIN_WEEKS
    apply_on(conn, "2026-11-03", 1)
    a = goal.activity(conn, NOW)
    assert a["weeks"] == goal.CAL_MIN_WEEKS and a["cells"][-1]["date"] == "2026-11-08"


def test_grid_is_capped_and_ends_with_this_week(env):
    _, conn = env
    apply_on(conn, "2025-01-01", 1)
    a = goal.activity(conn, NOW)
    assert a["weeks"] == goal.CAL_MAX_WEEKS == 26
    assert a["cells"][0]["date"] == (date(2026, 11, 2) - timedelta(weeks=25)).isoformat()
    assert a["cells"][-1]["date"] == "2026-11-08"


def test_days_after_today_are_future_placeholders_and_today_is_marked(env):
    _, conn = env
    cells = by_date(goal.activity(conn, NOW))
    assert cells["2026-11-04"]["today"] and not cells["2026-11-04"]["future"]
    assert all(cells[d]["future"] for d in ("2026-11-05", "2026-11-08"))
    assert not cells["2026-11-03"]["future"] and not cells["2026-11-03"]["today"]
    assert cells["2026-11-07"]["weekend"] and cells["2026-11-08"]["weekend"] and not cells["2026-11-06"]["weekend"]


def test_month_labels_sit_on_the_week_where_a_month_starts(env):
    _, conn = env
    apply_on(conn, "2026-08-12", 1)
    months = goal.activity(conn, NOW)["months"]
    assert [m["label"] for m in months] == ["Aug", "Sep", "Oct", "Nov"]
    assert months[0]["col"] == 1
    assert [m["col"] for m in months] == [1, 4, 8, 12]                  # Sep 1 is in the week of Aug 31, and so on


# --- counts and levels -------------------------------------------------------------------------

def test_a_day_counts_applications_notes_and_phase_moves_but_not_the_creation_move(env):
    _, conn = env
    apply_on(conn, "2026-10-14", 2)                                    # each also records its "Applied" move
    cells = by_date(goal.activity(conn, NOW))
    c = cells["2026-10-14"]
    assert (c["applications"], c["notes"], c["moves"], c["count"]) == (2, 0, 0, 2)
    a = make_app(conn, "p", company="Globex", applied="2026-10-20T09:00:00-06:00")
    add_note(conn, a, "2026-10-14T11:30", "Pairing session", phase="Technical")
    add_note(conn, a, "2026-10-14T13:15:00-06:00", "Moved to Technical", phase="Technical", kind="move")
    c = by_date(goal.activity(conn, NOW))["2026-10-14"]
    assert (c["applications"], c["notes"], c["moves"], c["count"]) == (2, 1, 1, 4)


@pytest.mark.parametrize("count, level", [(0, 0), (1, 1), (2, 2), (3, 3), (4, 3), (5, 4), (9, 4)])
def test_intensity_levels_scale_with_the_count(env, count, level):
    _, conn = env
    if count:
        apply_on(conn, "2026-10-14", count)
    assert by_date(goal.activity(conn, NOW))["2026-10-14"]["level"] == level


def test_a_day_with_only_notes_is_active_too(env):
    _, conn = env
    a = make_app(conn, "a", applied="2026-10-01T09:00:00-06:00")
    add_note(conn, a, "2026-10-15T11:00", "Call with recruiter")
    c = by_date(goal.activity(conn, NOW))["2026-10-15"]
    assert c["count"] == 1 and c["level"] == 1 and c["active"] is True
    assert by_date(goal.activity(conn, NOW))["2026-10-16"]["active"] is False


def test_days_use_the_local_calendar_date_whatever_offset_the_stamp_has(env):
    _, conn = env
    make_app(conn, "a", applied="2026-11-04T05:30:00+00:00")           # 2026-11-03 23:30 local
    make_app(conn, "b", applied="2026-11-03T23:30:00-06:00")
    cells = by_date(goal.activity(conn, NOW))
    assert cells["2026-11-03"]["applications"] == 2 and cells["2026-11-04"]["applications"] == 0


# --- markers --------------------------------------------------------------------------------------

def test_notes_after_the_applied_day_are_process_notes_and_notes_on_it_are_not(env):
    _, conn = env
    a = make_app(conn, "a", company="Acme", applied="2026-10-20T09:00:00-06:00")
    add_note(conn, a, "2026-10-20T09:30", "Applied via the Acme site", phase="Applied")
    add_note(conn, a, "2026-10-27T15:00", "Hiring manager call", phase="Hiring manager")
    cells = by_date(goal.activity(conn, NOW))
    assert cells["2026-10-20"]["notes"] == 1 and cells["2026-10-20"]["process_notes"] == 0
    assert cells["2026-10-27"]["notes"] == 1 and cells["2026-10-27"]["process_notes"] == 1


@pytest.mark.parametrize("note, rejected", [
    ("Rejected: the role was closed", True),
    ("They sent a REJECTION email", True),
    ("We are not moving forward with your application", True),
    ("Not continuing with the process", True),
    ("Sadly not advancing to the next round", True),
    ("Not progressing this time", True),
    ("Great call, moving forward with a technical round", False),
    ("Interview with Ana", False),
    # Spanish notes
    ("No pase esta vez", True),
    ("No pasé la entrevista técnica", True),
    ("Me rechazaron después del challenge", True),
    ("Rechazado: buscan más experiencia", True),
    ("No continúo en el proceso", True),
    ("Descartado en la etapa final", True),
    ("Pasé a la siguiente etapa", False),
    ("Entrevista técnica con el equipo", False),
])
def test_rejection_is_inferred_from_the_note_text(env, note, rejected):
    _, conn = env
    a = make_app(conn, "a", applied="2026-10-01T09:00:00-06:00")
    add_note(conn, a, "2026-10-25T09:00", note)
    c = by_date(goal.activity(conn, NOW))["2026-10-25"]
    assert c["rejections"] == (1 if rejected else 0)


def test_rejection_words_are_documented_in_one_place():
    assert set(goal.REJECTION_PHRASES) >= {"reject", "not moving forward", "not continuing",
                                           "not advancing", "not progressing"}


def test_future_notes_are_upcoming_interviews_not_activity_and_extend_the_grid(env):
    _, conn = env
    a = make_app(conn, "a", company="Acme", applied="2026-11-01T09:00:00-06:00")
    add_note(conn, a, "2026-11-10T14:00", "Final interview", phase="Final")
    add_note(conn, a, "2026-11-04T16:00", "Call later today", phase="Technical")    # NOW is 10:00
    act = goal.activity(conn, NOW)
    cells = by_date(act)
    assert cells["2026-11-10"]["upcoming"] == 1 and cells["2026-11-10"]["count"] == 0
    assert cells["2026-11-10"]["active"] and not cells["2026-11-10"]["future"]
    assert cells["2026-11-04"]["upcoming"] == 1 and cells["2026-11-04"]["notes"] == 0
    assert cells["2026-11-12"]["future"] and cells["2026-11-12"]["upcoming"] == 0
    assert act["cells"][-1]["date"] == "2026-11-15"                       # the week of the interview is shown
    assert "upcoming" in cells["2026-11-10"]["label"] and "Acme" in cells["2026-11-10"]["label"]


def test_upcoming_interviews_far_away_do_not_stretch_the_grid(env):
    _, conn = env
    a = make_app(conn, "a")
    add_note(conn, a, "2027-03-01T14:00", "Someday", phase="Final")
    assert goal.activity(conn, NOW)["cells"][-1]["date"] == "2026-11-08"


# --- tooltip text -------------------------------------------------------------------------------------

def test_label_spells_out_the_day(env):
    _, conn = env
    a = make_app(conn, "a", company="Globex", applied="2026-10-20T09:00:00-06:00")
    add_note(conn, a, "2026-10-14T11:30", "Pairing session with the whole platform team", phase="Technical")
    add_note(conn, a, "2026-10-14T16:30", "Follow-up call", phase="Technical")
    add_note(conn, a, "2026-10-14T13:15:00-06:00", "Moved to Technical", phase="Technical", kind="move")
    make_app(conn, "b", company="Acme", applied="2026-10-14T13:15:00-06:00")
    cells = by_date(goal.activity(conn, NOW))
    label = cells["2026-10-14"]["label"]
    assert label.startswith("Wed Oct 14 · 1 application (Acme) · 2 notes (Globex: Pairing session")
    assert "…" in label and "Globex: Follow-up call" in label and label.endswith("· 1 phase move")
    assert cells["2026-10-19"]["label"] == "Mon Oct 19 · no activity"
    assert cells["2026-11-04"]["label"].startswith("Wed Nov 4 · today")


def test_label_counts_rejections_and_keeps_the_snippet_list_short(env):
    _, conn = env
    for i in range(5):
        a = make_app(conn, f"n{i}", company=f"Co{i}", applied="2026-10-01T09:00:00-06:00")
        add_note(conn, a, "2026-10-25T09:00", "Rejected: no fit" if i == 0 else f"Call {i}")
    label = by_date(goal.activity(conn, NOW))["2026-10-25"]["label"]
    assert "5 notes" in label and "1 rejection" in label and "+2 more" in label
    assert label.count("Co") == 3


# --- efficiency ------------------------------------------------------------------------------------------

def test_the_calendar_takes_at_most_two_queries(env):
    _, conn = env
    for i in range(30):
        a = make_app(conn, f"q{i}", applied=f"2026-10-{1 + i % 20:02d}T09:00:00-06:00")
        add_note(conn, a, f"2026-10-{3 + i % 20:02d}T09:00", f"note {i}")
    statements = []
    conn.set_trace_callback(statements.append)
    goal.activity(conn, NOW)
    conn.set_trace_callback(None)
    assert 1 <= len(statements) <= 2, statements


# --- page ---------------------------------------------------------------------------------------------------

def section(html):
    return re.search(r'<details class="activity"[^>]*>.*?</details>', html, re.S).group(0)


def day_cells(html):
    return re.findall(r'<li class="cal-day[^"]*"[^>]*>.*?</li>', html, re.S)


def test_page_shows_the_calendar_above_the_filters_and_the_board_and_not_the_old_strip(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 2)
    html = client().get("/applications").text
    assert 'class="history"' not in html and 'class="days"' not in html
    assert html.index('class="activity"') < html.index('class="app-filters"') < html.index('id="board"')
    sec = section(html)
    assert len(day_cells(sec)) == goal.CAL_MIN_WEEKS * 7
    assert re.search(r"<summary[^>]*>.*?ACTIVITY.*?</summary>", sec, re.S)


def test_calendar_shows_with_no_applications_and_only_on_the_applications_page(env):
    client, conn = env
    assert 'class="activity"' in client().get("/applications").text
    for url in ["/", "/applications/phases", "/applications/new"]:
        assert day_cells(client().get(url).text) == []


def test_calendar_keeps_the_goal_totals_and_they_are_in_the_summary(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 2)
    sec = section(client().get("/applications").text)
    summary = re.search(r"<summary.*?</summary>", sec, re.S).group(0)
    assert 'class="activity-totals"' in summary
    assert "this week 2 · 30 days 2 · days met 0/21" in text(summary)


def test_with_the_goal_off_the_calendar_stays_but_the_totals_go(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 2)
    html = client(daily_goal=0).get("/applications").text
    assert len(day_cells(html)) == goal.CAL_MIN_WEEKS * 7 and "activity-totals" not in html


def test_each_active_day_is_a_link_that_filters_by_that_day_and_empty_days_are_not(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 3)
    html = client().get("/applications?view=list&outcome=active&sort=company").text
    (cell,) = [c for c in day_cells(html) if 'data-date="2026-11-03"' in c]
    href = unescape(re.search(r'<a [^>]*href="([^"]*)"', cell).group(1))
    assert href.startswith("/applications?") and "day=2026-11-03" in href
    assert "view=list" in href and "outcome=active" in href and "sort=company" in href
    assert 'aria-label="Tue Nov 3 · 3 applications' in cell and 'data-level="3"' in cell
    (empty,) = [c for c in day_cells(html) if 'data-date="2026-11-02"' in c]
    assert "<a " not in empty and 'aria-label="Mon Nov 2 · no activity"' in empty


def test_clicking_the_selected_day_again_clears_it(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 1)
    html = client().get("/applications?day=2026-11-03&q=dev").text
    (cell,) = [c for c in day_cells(html) if 'data-date="2026-11-03"' in c]
    assert 'aria-current="true"' in cell or "cal-selected" in cell
    href = unescape(re.search(r'<a [^>]*href="([^"]*)"', cell).group(1))
    assert "day=" not in href and "q=dev" in href


def test_markers_levels_and_weekend_rows_are_in_the_markup(env):
    client, conn = env
    a = make_app(conn, "a", company="Acme", applied="2026-10-20T09:00:00-06:00")
    add_note(conn, a, "2026-10-24T10:00", "Rejected: no fit")                    # a Saturday
    add_note(conn, a, "2026-10-27T15:00", "Hiring manager call", phase="Hiring manager")
    add_note(conn, a, "2026-11-10T14:00", "Final", phase="Final")
    html = client().get("/applications").text
    cell = lambda d: [c for c in day_cells(html) if f'data-date="{d}"' in c][0]
    assert 'class="mk mk-rej"' in cell("2026-10-24") and 'class="mk mk-note"' not in cell("2026-10-24")
    assert 'class="mk mk-note"' in cell("2026-10-27") and 'class="mk mk-rej"' not in cell("2026-10-27")
    assert 'class="mk mk-note"' not in cell("2026-10-20")                         # the day you applied
    assert "cal-upcoming" in cell("2026-11-10") and "cal-upcoming" not in cell("2026-10-27")
    assert "cal-weekend" in cell("2026-10-24") and "cal-weekend" not in cell("2026-10-27")
    assert "cal-today" in cell("2026-11-04") and 'aria-current="date"' in cell("2026-11-04")
    assert "cal-future" in cell("2026-11-12") and "<a " not in cell("2026-11-12")
    assert 'data-level="0"' in cell("2026-10-21")


def test_marker_shapes_differ_not_only_colours():
    assert ".mk-note" in CSS and ".mk-rej" in CSS
    assert "border-radius: 50%" in CSS.split(".mk-note")[1].split("}")[0]
    assert re.search(r"\.mk-rej::?(before|after)?[^{]*\{[^}]*content:\s*\"×\"", CSS) or "×" in CSS.split(".mk-rej")[1].split("}")[0]
    assert "var(--warn)" in CSS.split(".cal-upcoming")[1].split("}")[0]


def test_grid_has_month_and_weekday_labels_and_a_legend(env):
    client, conn = env
    apply_on(conn, "2026-08-12", 1)
    sec = section(client().get("/applications").text)
    months = re.search(r'<ol class="cal-months"[^>]*>(.*?)</ol>', sec, re.S).group(1)
    assert [text(m) for m in re.findall(r"<li[^>]*>.*?</li>", months, re.S)] == ["Aug", "Sep", "Oct", "Nov"]
    weekdays = re.search(r'<ol class="cal-weekdays"[^>]*>(.*?)</ol>', sec, re.S).group(1)
    assert [text(w) for w in re.findall(r"<li[^>]*>.*?</li>", weekdays, re.S) if text(w)] == ["Mon", "Wed", "Fri"]
    legend = text(sec[sec.index('class="cal-legend"'):])
    for words in ("Less", "More", "note", "rejection", "upcoming"):
        assert words in legend
    assert sec.count('class="cal-key"') >= 5


def test_calendar_region_scrolls_sideways_and_is_labelled(env):
    client, _ = env
    sec = section(client().get("/applications").text)
    assert re.search(r'<div class="cal-scroll"[^>]*role="region"[^>]*aria-label="[^"]*"[^>]*tabindex="0"', sec)
    assert re.search(r"\.cal-scroll\s*\{[^}]*overflow-x:\s*auto", CSS)


def test_section_is_open_without_script_and_script_remembers_the_toggle():
    assert "job-finder:activity-open" in JS and "localStorage" in JS
    assert "details.activity" in JS


def test_calendar_is_centred_and_its_toggle_is_a_visible_button():
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "jobfinder" / "static" / "style.css").read_text()
    body = css[css.index(".activity-body {"):].split("}", 1)[0]
    assert "flex-direction: column" in body and "align-items: center" in body
    toggle = css[css.index(".activity-toggle {"):].split("}", 1)[0]
    assert "border:" in toggle                       # looks like a button, not loose text
    assert '.activity-toggle::after { content: "HIDE ▴"; }' in css
    assert '.activity:not([open]) .activity-toggle::after { content: "SHOW ACTIVITY ▾"; }' in css
