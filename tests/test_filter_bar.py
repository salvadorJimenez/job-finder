"""The applications filter bar: search first, dropdown menus (native <details>) with count badges,
active-filter pills with remove links, Clear all, and the `day` filter used by the calendar."""
import re
from html import unescape
from pathlib import Path

import pytest

from test_applications_view import ALL, board_titles, chips, list_titles, query, text, world  # noqa: F401
from test_board import env, pid  # noqa: F401  (env is a fixture)

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "jobfinder/static/style.css").read_text()
JS = (ROOT / "jobfinder/static/app.js").read_text()

# NOW is 2026-11-04. world: spring (applied 11-03, upcoming note 11-10), rust (applied 10-20, rejected, note 10-23),
# front (applied 08-01, note 11-02), back (applied 10-01), ops (applied 10-30, accepted)


def bar(html):
    return re.search(r'<form class="app-filters".*?</form>', html, re.S).group(0)


def menu(html, name):
    return re.search(rf'<details class="fmenu[^"]*" data-menu="{name}"[^>]*>(.*?)</details>', html, re.S).group(1)


def badge(html, name):
    m = re.search(r'<summary[^>]*>.*?<span class="fbadge"[^>]*>(\d+)</span>', menu(html, name), re.S)
    return int(m.group(1)) if m else None


def pills(html):
    block = re.search(r'<ul class="pills"[^>]*>(.*?)</ul>', html, re.S)
    if not block:
        return []
    return [(text(a.group(2)).rstrip("×").strip(), unescape(a.group(1)))
            for a in re.finditer(r'<a class="pill" href="([^"]*)"[^>]*>(.*?)</a>', block.group(1), re.S)]


# --- structure ------------------------------------------------------------------------------------

def test_one_form_row_search_first_then_menus_then_sort_and_the_view_switch(world):
    client, _, _ = world
    form = bar(client.get("/applications").text)
    order = [form.index(x) for x in ('name="q"', 'data-menu="outcome"', 'data-menu="phase"', 'data-menu="site"',
                                     'data-menu="date"', 'data-menu="more"', 'name="sort"', 'class="view-switch"')]
    assert order == sorted(order)
    assert "FILTERS" not in form                                                    # no big block with a title any more


def test_search_is_a_prominent_labelled_field_with_icon_hint_and_shortcut(world):
    client, _, _ = world
    form = bar(client.get("/applications").text)
    m = re.search(r'<input type="search" name="q"[^>]*>', form)
    assert m and 'placeholder="Search company, role or notes"' in m.group(0)
    assert 'aria-label="Search company, role or notes' in m.group(0) and "autocomplete=\"off\"" in m.group(0)
    assert '<svg class="fsearch-icon"' in form and 'aria-hidden="true"' in form.split('class="fsearch-icon"')[1][:80]
    assert '<kbd class="fkey" aria-hidden="true">/</kbd>' in form


def test_search_keeps_its_value_and_offers_a_clear_link_only_when_there_is_one(world):
    client, _, _ = world
    assert "fsearch-clear" not in client.get("/applications").text
    html = client.get("/applications", params={"view": "list", "q": "spring", "outcome": "active", "sort": "company"}).text
    assert re.search(r'name="q"[^>]*value="spring"', bar(html))
    link = re.search(r'<a class="fsearch-clear" href="([^"]*)" aria-label="Clear search">', html)
    assert link and query(link.group(1)) == {"view": ["list"], "outcome": ["active"], "sort": ["company"]}


@pytest.mark.parametrize("name, label", [("outcome", "Outcome"), ("phase", "Phase"), ("site", "Source"),
                                         ("date", "Date"), ("more", "More")])
def test_every_menu_is_a_native_details_with_a_labelled_summary(world, name, label):
    client, _, _ = world
    html = client.get("/applications").text
    assert re.search(rf'<details class="fmenu" data-menu="{name}">\s*<summary[^>]*>\s*{label}\s*(<span|</summary>)', html)
    assert re.search(r'<div class="fpop"[^>]*>', menu(html, name))
    assert badge(html, name) is None                                                # nothing active: no badge


def test_outcome_and_phase_menus_hold_the_existing_chips_with_counts(world):
    client, conn, _ = world
    html = client.get("/applications").text
    assert chips(menu(html, "outcome"), "outcome") == {"active": (3, False), "accepted": (1, False), "rejected": (1, False),
                                                       "withdrawn": (0, False), "ghosted": (0, False)}
    assert len(chips(menu(html, "phase"), "phase")) == 7 and "name=\"outcome\"" not in menu(html, "phase")
    assert chips(client.get("/applications?outcome=rejected").text, "outcome")["rejected"] == (1, True)


def test_source_menu_has_the_select_of_sources_present(world):
    client, _, _ = world
    html = client.get("/applications?site=indeed").text
    select = re.search(r'<select name="site"[^>]*>(.*?)</select>', menu(html, "site"), re.S).group(1)
    assert re.findall(r'<option value="([^"]*)"', select) == ["", "glassdoor", "indeed", "linkedin", "manual"]
    assert '<option value="indeed" selected>' in select


def test_date_menu_has_quick_ranges_and_from_to_inputs(world):
    client, _, _ = world
    html = client.get("/applications?within=30&from=2026-10-01&to=2026-10-30").text
    date_menu = menu(html, "date")
    radios = re.findall(r'<input type="radio" name="within" value="([^"]*)"( checked)?>', date_menu)
    assert [(v, bool(c)) for v, c in radios] == [("", False), ("7", False), ("30", True), ("90", False)]
    assert re.search(r'<input type="date" name="from" value="2026-10-01"', date_menu)
    assert re.search(r'<input type="date" name="to" value="2026-10-30"', date_menu)
    assert "Last 7 days" in text(date_menu) and "Any time" in text(date_menu)


def test_more_menu_has_cv_and_upcoming_interview_only(world):
    client, _, _ = world
    html = client.get("/applications?cv=missing&interview=1").text
    more = menu(html, "more")
    assert re.findall(r'<option value="([^"]*)"', re.search(r'<select name="cv"[^>]*>(.*?)</select>', more, re.S).group(1)) == ["", "attached", "missing"]
    assert '<option value="missing" selected>' in more
    assert re.search(r'<input type="checkbox" name="interview" value="1" checked>', more) and "upcoming interview only" in more


def test_sort_select_and_a_noscript_submit_stay_in_the_form(world):
    client, _, _ = world
    form = bar(client.get("/applications").text)
    assert re.search(r'<label[^>]*>\s*<span class="field-label">Sort</span>\s*<select name="sort">', form)
    assert re.search(r"<noscript><button class=\"btn\" type=\"submit\">FILTER</button></noscript>", form)


def test_form_still_gets_to_the_page_with_view_and_day_carried_in_hidden_fields(world):
    client, _, _ = world
    assert re.search(r'<form class="app-filters" method="get" action="/applications" data-autosubmit', client.get("/applications").text)
    html = client.get("/applications?view=list&day=2026-11-03").text
    assert '<input type="hidden" name="view" value="list">' in html
    assert '<input type="hidden" name="day" value="2026-11-03">' in html
    assert 'name="day"' not in client.get("/applications").text.split("</form>")[0]


def test_view_switch_lives_in_the_bar_and_still_keeps_every_filter(world):
    client, conn, _ = world
    html = client.get("/applications", params=[("outcome", "rejected"), ("day", "2026-10-23"), ("q", "rust")]).text
    switch = re.search(r'<nav class="view-switch" aria-label="View">(.*?)</nav>', bar(html), re.S).group(1)
    href = re.search(r'href="([^"]*)"[^>]*>LIST</a>', switch).group(1)
    assert query(href) == {"view": ["list"], "outcome": ["rejected"], "day": ["2026-10-23"], "q": ["rust"]}


# --- badges ---------------------------------------------------------------------------------------------

def test_badges_count_what_is_active_in_each_menu(world):
    client, conn, _ = world
    tech, final = pid(conn, "Technical"), pid(conn, "Final")
    html = client.get("/applications", params=[
        ("outcome", "rejected"), ("outcome", "ghosted"), ("phase", str(tech)), ("phase", str(final)), ("phase", "unassigned"),
        ("site", "indeed"), ("within", "30"), ("from", "2026-10-01"), ("to", "2026-10-30"), ("day", "2026-10-23"),
        ("cv", "missing"), ("interview", "1")]).text
    assert (badge(html, "outcome"), badge(html, "phase"), badge(html, "site")) == (2, 3, 1)
    assert badge(html, "date") == 4 and badge(html, "more") == 2


def test_the_badge_says_what_it_counts_to_screen_readers(world):
    client, _, _ = world
    html = client.get("/applications?outcome=rejected&outcome=ghosted").text
    assert re.search(r'<span class="fbadge"[^>]*>2</span>\s*<span class="sr-only">2 selected</span>', menu(html, "outcome"))


def test_search_and_sort_are_not_counted_in_any_badge(world):
    client, _, _ = world
    html = client.get("/applications?q=spring&sort=company").text
    assert all(badge(html, n) is None for n in ("outcome", "phase", "site", "date", "more"))


# --- active pills ----------------------------------------------------------------------------------------

def test_no_pills_and_no_clear_all_without_filters_but_the_count_is_always_there(world):
    client, _, _ = world
    html = client.get("/applications?view=list&sort=company").text
    assert pills(html) == [] and "Clear all</a>" not in html and 'class="pills"' not in html
    assert '<p class="count muted" id="app-count">5 of 5 applications</p>' in html


def test_every_active_filter_is_a_pill_and_its_link_drops_exactly_that_value(world):
    client, conn, _ = world
    tech = pid(conn, "Technical")
    params = [("view", "list"), ("q", "spring"), ("outcome", "rejected"), ("outcome", "ghosted"), ("phase", str(tech)),
              ("phase", "unassigned"), ("site", "indeed"), ("cv", "missing"), ("interview", "1"), ("within", "30"),
              ("from", "2026-10-01"), ("to", "2026-10-30"), ("day", "2026-10-14"), ("sort", "company")]
    full = {"view": ["list"], "q": ["spring"], "outcome": ["rejected", "ghosted"], "phase": [str(tech), "unassigned"],
            "site": ["indeed"], "cv": ["missing"], "interview": ["1"], "within": ["30"], "from": ["2026-10-01"],
            "to": ["2026-10-30"], "day": ["2026-10-14"], "sort": ["company"]}
    got = pills(client.get("/applications", params=params).text)
    assert [label for label, _ in got] == [
        "“spring”", "rejected", "ghosted", "Technical", "Unassigned", "Source: indeed", "CV missing", "Upcoming interview",
        "Last 30 days", "Since 2026-10-01", "Until 2026-10-30", "Day Wed Oct 14"]
    drops = [("q", None), ("outcome", "rejected"), ("outcome", "ghosted"), ("phase", str(tech)), ("phase", "unassigned"),
             ("site", None), ("cv", None), ("interview", None), ("within", None), ("from", None), ("to", None), ("day", None)]
    for (label, href), (key, value) in zip(got, drops):
        expected = {k: list(v) for k, v in full.items()}
        if value is None:
            del expected[key]
        else:
            expected[key].remove(value)
            if not expected[key]:
                del expected[key]
        assert query(href) == expected, label


def test_pills_have_accessible_remove_names_and_the_count_sits_with_them(world):
    client, _, _ = world
    html = client.get("/applications?outcome=rejected&q=rust").text
    assert re.search(r'<a class="pill" href="[^"]*" aria-label="Remove filter: rejected">rejected\s*<span aria-hidden="true">×</span></a>', html)
    assert re.search(r'aria-label="Remove filter: “rust”"', html)
    active = re.search(r'<div class="factive"[^>]*>.*?</div>\s*</div>|<div class="factive"[^>]*>.*?</div>', html, re.S).group(0)
    assert 'id="app-count"' in active and "1 of 5 applications" in text(active)


def test_clear_all_forgets_every_filter_but_keeps_the_view_and_the_sort(world):
    client, _, _ = world
    html = client.get("/applications?view=list&q=rust&outcome=rejected&day=2026-10-23&sort=company").text
    m = re.search(r'<a class="reset" href="([^"]*)" data-reset-apps>Clear all</a>', html)
    assert m and query(m.group(1)) == {"view": ["list"], "sort": ["company"]}
    board = client.get("/applications?q=rust").text
    assert re.search(r'<a class="reset" href="/applications" data-reset-apps>Clear all</a>', board)


def test_pills_escape_what_the_user_typed(world):
    client, _, _ = world
    html = client.get("/applications", params={"q": "<img src=x onerror=1>"}).text
    assert "<img src=x" not in html and "&lt;img src=x onerror=1&gt;" in html


def test_a_filter_with_no_results_keeps_the_pills_so_it_can_be_undone(world):
    client, _, _ = world
    html = client.get("/applications?q=zzz-nothing&outcome=active").text
    assert "No applications match these filters" in html and len(pills(html)) == 2 and "0 of 5 applications" in html


# --- day filter -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("day, expected", [
    ("2026-11-03", ["Spring Engineer"]),            # applied that day
    ("2026-10-20", ["Rust Developer"]),              # applied that day (a different application also has a move that day: its own)
    ("2026-10-23", ["Rust Developer"]),              # a timeline note that day
    ("2026-11-02", ["Frontend Dev"]),              # a note on an application applied months before
    ("2026-11-10", ["Spring Engineer"]),            # an upcoming interview day
    ("2026-11-01", []),
])
def test_day_matches_applied_that_day_or_a_timeline_entry_that_day(world, day, expected):
    client, _, _ = world
    assert list_titles(client, day=day) == expected
    assert board_titles(client, day=day) == expected


def test_day_counts_phase_moves_in_the_timeline_too(world):
    client, conn, ids = world
    conn.execute("INSERT INTO timeline_entries (application_id, phase_name, happened_at, notes, kind) "
                 "VALUES (?, 'Final', '2026-11-02T09:00:00-06:00', 'Moved to Final', 'move')", (ids["back"],))
    conn.commit()
    assert list_titles(client, day="2026-11-02") == ["Backend Dev", "Frontend Dev"]


def test_day_uses_local_dates_for_stamps_with_other_offsets(world):
    client, conn, ids = world
    conn.execute("INSERT INTO timeline_entries (application_id, phase_name, happened_at, notes, kind) "
                 "VALUES (?, 'Final', '2026-11-03T05:30:00+00:00', 'late move', 'move')", (ids["back"],))     # 11-02 23:30 local
    conn.commit()
    assert "Backend Dev" in list_titles(client, day="2026-11-02") and "Backend Dev" not in list_titles(client, day="2026-11-03")


@pytest.mark.parametrize("junk", ["nope", "2026-13-45", "2026-1-3", "11/03/2026", "", "   ", "2026-11-03T10:00"])
def test_an_invalid_day_is_ignored(world, junk):
    client, _, _ = world
    assert list_titles(client, day=junk) == ALL
    html = client.get("/applications", params={"day": junk}).text
    assert 'name="day"' not in html and pills(html) == []


def test_day_combines_with_the_other_filters_by_and(world):
    client, _, _ = world
    assert list_titles(client, day="2026-10-23", outcome="rejected") == ["Rust Developer"]
    assert list_titles(client, day="2026-10-23", outcome="active") == []
    assert list_titles(client, day="2026-11-03", q="spring", site="linkedin") == ["Spring Engineer"]


def test_day_narrows_the_chip_counts_like_any_other_filter(world):
    client, _, _ = world
    got = chips(client.get("/applications?day=2026-10-23").text, "outcome")
    assert got["rejected"] == (1, False) and got["active"] == (0, False)


def test_day_survives_sort_header_and_view_links(world):
    client, _, _ = world
    html = client.get("/applications?view=list&day=2026-10-23").text
    for label in ("Applied", "Phase", "Last activity"):
        href = re.search(rf'<th[^>]*>\s*<a href="([^"]*)"[^>]*>{label}', html).group(1)
        assert query(href)["day"] == ["2026-10-23"]
    assert query(re.search(r'<a class="muted tool-link" href="([^"]*)">Hide closed</a>', html).group(1))["day"] == ["2026-10-23"]


# --- script and styles ---------------------------------------------------------------------------------------

def test_script_closes_menus_on_escape_and_outside_clicks_and_keeps_one_open():
    assert "details.fmenu" in JS or "details[data-menu]" in JS
    assert '"Escape"' in JS and "closest" in JS
    assert re.search(r'event\.key === "/"', JS) and 'input[name="q"]' in JS             # "/" focuses search
    assert "data-menu" in JS                                                                  # reopens the menu you were using


def test_script_reopens_the_menu_that_held_the_focused_control_after_a_filter_reloads():
    assert 'closest("details' in JS and ".open = true" in JS


def test_menus_are_positioned_popovers_that_wrap_on_narrow_screens():
    assert re.search(r"\.fpop\s*\{[^}]*position:\s*absolute[^}]*z-index", CSS)
    assert re.search(r"\.fmenu > summary\s*\{[^}]*cursor:\s*pointer", CSS) and ".fbadge" in CSS and ".pill" in CSS
    narrow = re.search(r"@media \(max-width: (\d+)px\)\s*\{[^@]*\.fpop", CSS)
    assert narrow and int(narrow.group(1)) >= 520
