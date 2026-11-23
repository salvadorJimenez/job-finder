"""Daily application goal: config, counting, streak, header counter and 30-day history."""
import re
from pathlib import Path
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jobfinder import applications as apps
from jobfinder import goal
from jobfinder.config import load_config
from jobfinder.db import connect
from jobfinder.web import create_app
from test_applications import make_job
from test_web import NOW, make_cfg

ROOT = Path(__file__).resolve().parent.parent
TZ = timezone(timedelta(hours=-6))
# 2026-11-02 is a Monday: Mon 02 ... Fri 06, Sat 07, Sun 08, Mon 09, Tue 10
WED, FRI, SAT, SUN, MON = (datetime(2026, 11, d, 10, 0, tzinfo=TZ) for d in (4, 6, 7, 8, 9))
PAGES = ["/", "/jobs/1", "/applications", "/applications/1", "/applications/phases", "/applications/new"]


def at(day, hour=9, minute=0, tz=TZ):
    return datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=hour, minute=minute).isoformat()


_ids = iter(range(10_000))


def apply_on(conn, day, n=1, stamp=None):
    """n applications dated `day` ('YYYY-MM-DD' or date); `stamp` overrides the stored timestamp."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    for _ in range(n):
        job = make_job(conn, f"g{next(_ids)}")
        apps.create_application_for_job(conn, job, stamp or at(day))
    conn.commit()


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def goal_block(html):
    m = re.search(r'<div class="goal[ "].*?</div>', html, re.S)
    return m.group(0) if m else None


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)

    def client(now=NOW, daily_goal=5):
        return TestClient(create_app(replace(cfg, daily_goal=daily_goal), clock=lambda: now), follow_redirects=False)

    return client, conn


# --- S1 config ----------------------------------------------------------------------

def test_default_config_file_sets_goal_of_five():
    assert load_config(ROOT / "config.example.toml").daily_goal == 5


def test_goal_section_is_optional_and_defaults_to_five(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[search]\nterms = ["a"]\nsites = ["linkedin"]\nlocation = "Mexico"\ncountry_indeed = "Mexico"\n')
    assert load_config(path).daily_goal == 5


@pytest.mark.parametrize("value", [0, 3, 12])
def test_goal_is_read_from_the_goal_section(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text('[search]\nterms = ["a"]\nsites = ["linkedin"]\nlocation = "Mexico"\ncountry_indeed = "Mexico"\n'
                    f"[goal]\ndaily = {value}\n")
    assert load_config(path).daily_goal == value


# --- S2 counting ------------------------------------------------------------------------

def test_index_on_applied_at_exists(env):
    _, conn = env
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "applications_applied_at" in names


def test_counts_use_the_local_date_of_applied_at(env):
    _, conn = env
    apply_on(conn, "2026-11-04", stamp="2026-11-04T23:30:00-06:00")   # late evening, still the 4th
    apply_on(conn, "2026-11-04", stamp="2026-11-05T03:00:00+00:00")   # = 21:00 on the 4th, local
    apply_on(conn, "2026-11-05", stamp="2026-11-05T05:59:00+00:00")   # = 23:59 on the 4th, local
    apply_on(conn, "2026-11-05", stamp="2026-11-05T06:00:00+00:00")   # = 00:00 on the 5th, local
    apply_on(conn, "2026-11-03", stamp="2026-11-03T12:00:00")         # naive stamps are local
    counts = goal.daily_counts(conn, date(2026, 11, 1), TZ)
    assert counts == {date(2026, 11, 3): 1, date(2026, 11, 4): 3, date(2026, 11, 5): 1}


def test_daily_counts_is_a_single_select_for_the_whole_window(env):
    _, conn = env
    apply_on(conn, "2026-11-04", 2)
    statements = []
    conn.set_trace_callback(statements.append)
    goal.daily_counts(conn, date(2026, 10, 6), TZ)
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1


def test_header_runs_one_select_when_the_streak_ends_inside_the_window(env):
    _, conn = env
    apply_on(conn, "2026-11-04", 5)
    statements = []
    conn.set_trace_callback(statements.append)
    goal.header(conn, WED, 5)
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1


def test_manual_application_counts_on_its_chosen_date(env):
    client, conn = env
    c = client()
    c.post("/applications/new", data={"title": "T", "company": "C", "applied_at": "2026-11-03"})
    assert "TODAY 0/5" in text(c.get("/").text)
    c.post("/applications/new", data={"title": "T", "company": "C", "applied_at": "2026-11-04"})
    assert "TODAY 1/5" in text(c.get("/").text)


def test_unmarking_a_job_does_not_reduce_todays_count(env):
    client, conn = env
    c = client()
    job = make_job(conn, "solo")
    assert c.post(f"/jobs/{job}/status", data={"status": "applied"}).status_code == 303
    assert "TODAY 1/5" in text(c.get("/").text)
    c.post(f"/jobs/{job}/status", data={"status": "new"})
    assert "TODAY 1/5" in text(c.get("/").text)


def test_applications_in_the_future_do_not_count_today(env):
    client, conn = env
    apply_on(conn, "2026-11-09", 3)
    assert "TODAY 0/5" in text(client().get("/").text)


# --- S3 header counter -----------------------------------------------------------------------

@pytest.mark.parametrize("url", PAGES)
def test_every_page_shows_the_counter(env, url):
    client, conn = env
    make_job(conn)
    apps_job = make_job(conn, "x2")
    apps.create_application_for_job(conn, apps_job, at(NOW.date()))
    conn.commit()
    r = client().get(url)
    assert r.status_code == 200
    assert "TODAY 1/5" in text(goal_block(r.text) or "")


@pytest.mark.parametrize("url", PAGES)
def test_header_markup_is_one_partial_with_the_nav_everywhere(env, url):
    client, conn = env
    apply_on(conn, "2026-11-04")
    html = client().get(url).text
    assert html.count('<header class="top">') == 1
    assert html.count('<nav class="main-nav">') == 1
    assert '<h1><a href="/" data-keep-filters>JOB_FINDER</a></h1>' in html


def test_progress_state_shows_text_meter_and_accessible_label(env):
    client, conn = env
    apply_on(conn, "2026-11-04", 3)
    block = goal_block(client().get("/").text)
    assert "TODAY 3/5" in text(block)
    assert "✓" not in block
    assert block.count('class="seg on"') == 3 and block.count('class="seg"') == 2
    meter = re.search(r'<[^>]*class="goal-meter"[^>]*>', block).group(0)
    assert 'aria-hidden="true"' in meter
    assert re.search(r'<span class="sr-only">3 of 5 applications today</span>', block)


def test_zero_applications_shows_empty_meter(env):
    client, _ = env
    block = goal_block(client().get("/").text)
    assert "TODAY 0/5" in text(block)
    assert block.count('class="seg on"') == 0 and block.count('class="seg"') == 5


def test_met_state_has_check_mark_and_full_meter(env):
    client, conn = env
    apply_on(conn, "2026-11-04", 5)
    block = goal_block(client().get("/").text)
    assert "✓ TODAY 5/5" in text(block)
    assert block.count('class="seg on"') == 5
    assert "goal-met" in block


def test_counts_above_the_goal_are_shown_as_is(env):
    client, conn = env
    apply_on(conn, "2026-11-04", 7)
    block = goal_block(client().get("/").text)
    assert "✓ TODAY 7/5" in text(block)
    assert block.count('class="seg on"') == 5


def test_weekend_shows_count_without_goal_or_meter(env):
    client, conn = env
    apply_on(conn, "2026-11-07", 2)
    block = goal_block(client(SAT).get("/").text)
    assert "WEEKEND · 2 applied" in text(block)
    assert "/5" not in block and 'class="seg' not in block and "goal-meter" not in block
    assert "goal-weekend" in block


def test_weekend_with_no_applications(env):
    client, _ = env
    assert "WEEKEND · 0 applied" in text(goal_block(client(SUN).get("/").text))


def test_streak_is_shown_next_to_the_counter_when_at_least_one(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 5)
    apply_on(conn, "2026-11-02", 5)
    block = goal_block(client().get("/").text)       # Wed, in progress with 0
    assert "STREAK 2" in text(block)


def test_streak_is_hidden_at_zero(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 4)
    assert "STREAK" not in goal_block(client().get("/").text)


def test_goal_zero_turns_the_feature_off_everywhere(env):
    client, conn = env
    apply_on(conn, "2026-11-04", 9)
    c = client(daily_goal=0)
    for url in ["/", "/applications", "/applications/phases", "/applications/new"]:
        html = c.get(url).text
        assert goal_block(html) is None
        assert "TODAY" not in html and "STREAK" not in html and "history" not in html.lower()


def test_larger_goals_scale_the_meter_to_ten_segments(env):
    client, conn = env
    apply_on(conn, "2026-11-04", 5)
    block = goal_block(client(daily_goal=20).get("/").text)
    assert "TODAY 5/20" in text(block)
    assert block.count('class="seg on"') == 2 and block.count('class="seg"') == 8


def test_goal_one_has_a_single_segment(env):
    client, _ = env
    assert goal_block(client(daily_goal=1).get("/").text).count('class="seg"') == 1


def test_header_uses_the_injected_clock_not_the_wall_clock(env):
    client, conn = env
    apply_on(conn, "2026-11-04", 2)
    assert "TODAY 2/5" in text(goal_block(client(WED).get("/").text))
    assert "TODAY 0/5" in text(goal_block(client(WED + timedelta(days=1)).get("/").text))


# --- S4 streak ----------------------------------------------------------------------------

def streak(conn, now, goal_=5):
    return goal.header(conn, now, goal_)["streak"]


def test_streak_continues_over_the_weekend_into_a_full_monday(env):
    _, conn = env
    apply_on(conn, "2026-11-06", 5)           # Fri
    apply_on(conn, "2026-11-09", 5)           # Mon, today
    assert streak(conn, MON) == 2


def test_unmet_today_does_not_break_the_streak(env):
    _, conn = env
    apply_on(conn, "2026-11-06", 5)           # Fri met
    apply_on(conn, "2026-11-05", 5)           # Thu met
    apply_on(conn, "2026-11-09", 2)           # Mon, still in progress
    assert streak(conn, MON) == 2


def test_no_applications_today_and_friday_met(env):
    _, conn = env
    apply_on(conn, "2026-11-06", 5)
    assert streak(conn, MON) == 1


def test_a_missed_weekday_breaks_the_streak(env):
    _, conn = env
    apply_on(conn, "2026-11-06", 5)           # Fri
    apply_on(conn, "2026-11-05", 4)           # Thu missed by one
    apply_on(conn, "2026-11-04", 5)           # Wed (before the break)
    assert streak(conn, FRI) == 1


def test_missed_yesterday_while_today_unmet_gives_zero(env):
    _, conn = env
    apply_on(conn, "2026-11-03", 4)
    apply_on(conn, "2026-11-02", 5)
    assert streak(conn, WED) == 0


def test_weekend_applications_do_not_add_to_the_streak(env):
    _, conn = env
    apply_on(conn, "2026-11-06", 5)           # Fri met
    apply_on(conn, "2026-11-07", 9)           # Sat
    apply_on(conn, "2026-11-08", 9)           # Sun
    assert streak(conn, MON) == 1


def test_not_applying_on_the_weekend_does_not_break_the_streak(env):
    _, conn = env
    apply_on(conn, "2026-11-05", 5)
    apply_on(conn, "2026-11-06", 5)
    assert streak(conn, SUN) == 2             # on a weekend: counts back from the last weekday


def test_streak_on_a_weekday_counts_met_today(env):
    _, conn = env
    apply_on(conn, "2026-11-04", 5)
    apply_on(conn, "2026-11-03", 5)
    assert streak(conn, WED) == 2


def test_no_applications_means_no_streak(env):
    _, conn = env
    assert streak(conn, WED) == 0


def test_long_streaks_are_counted_past_the_first_query_window(env):
    _, conn = env
    day, now = date(2026, 11, 9), MON
    start, n = day, 0
    while n < 70:                              # 70 weekdays, ~14 weeks, more than the initial window
        if start.weekday() < 5:
            apply_on(conn, start)
            n += 1
        start -= timedelta(days=1)
    assert streak(conn, now, 1) == 70


def test_goal_zero_has_no_header_data(env):
    _, conn = env
    assert goal.header(conn, WED, 0) is None


# --- S5 history: the totals next to the calendar (the cells themselves moved to test_activity_calendar) ---------

def cells(html):
    return re.findall(r'<li class="cal-day[^"]*"[^>]*>.*?</li>', html, re.S)


def totals(c):
    return text(re.search(r'<span class="activity-totals">.*?</span>\s*</span>', c.get("/applications").text, re.S).group(0))


def test_history_totals_are_the_week_the_last_30_days_and_the_weekdays_met(env):
    client, conn = env
    apply_on(conn, "2026-11-03", 2)
    h = goal.history(conn, NOW, 5)
    assert h == {"week": 2, "total": 2, "met": 0, "weekdays": 21}
    assert goal.history(conn, NOW, 0) is None


def test_totals_this_week_last_30_days_and_days_met(env):
    client, conn = env
    apply_on(conn, "2026-11-02", 5)           # Mon (met)   — this week starts Monday
    apply_on(conn, "2026-11-03", 2)           # Tue
    apply_on(conn, "2026-11-04", 5)           # Wed today, met
    apply_on(conn, "2026-11-01", 3)           # Sun before the week: not "this week"
    apply_on(conn, "2026-10-20", 5)           # Tue, met
    apply_on(conn, "2026-10-05", 9)           # 30 days window starts 10-06: excluded
    t = totals(client())
    assert "this week 12" in t
    assert "30 days 20" in t
    assert "days met 3/" in t


def test_days_met_denominator_counts_weekdays_and_skips_an_unfinished_today(env):
    client, conn = env
    # 2026-10-06 .. 2026-11-04 holds 22 weekdays, today (Wed) included
    assert "days met 0/21" in totals(client())              # today not met yet: still in progress
    apply_on(conn, "2026-11-04", 5)
    assert "days met 1/22" in totals(client())


def test_weekend_applications_count_in_totals_but_not_in_days_met(env):
    client, conn = env
    apply_on(conn, "2026-10-31", 6)           # Sat
    t = totals(client())
    assert "30 days 6" in t and "days met 0/" in t


def test_custom_goal_changes_what_counts_as_met(env):
    client, conn = env
    apply_on(conn, "2026-11-04", 3)
    assert "days met 1/22" in totals(client(daily_goal=3))
    assert "days met 0/21" in totals(client(daily_goal=5))


def test_totals_only_on_the_board_not_on_other_pages(env):
    client, conn = env
    make_job(conn)
    for url in ["/", "/jobs/1", "/applications/phases", "/applications/new"]:
        assert cells(client().get(url).text) == []
        assert "activity-totals" not in client().get(url).text


def test_calendar_ends_on_the_clocks_week_for_a_weekend_clock(env):
    client, conn = env
    found = cells(client(SAT).get("/applications").text)
    assert 'data-date="2026-11-08"' in found[-1] and "cal-future" in found[-1]
    (today,) = [c for c in found if "cal-today" in c]
    assert 'data-date="2026-11-07"' in today and "cal-weekend" in today


# --- styles ---------------------------------------------------------------------------------------

def test_stylesheet_has_goal_and_activity_rules():
    css = (ROOT / "jobfinder/static/style.css").read_text()
    for selector in (".goal", ".goal-meter", ".seg.on", ".activity", ".cal-grid", ".cal-day", ".cal-cell"):
        assert selector in css
    for level in range(5):
        assert f'.cal-cell[data-level="{level}"]' in css
    assert "dim" in css.split(".cal-weekend")[1].split("}")[0] or "opacity" in css.split(".cal-weekend")[1].split("}")[0]
