from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from jobfinder.config import SearchConfig
from jobfinder.db import connect
from jobfinder.search import SearchAlreadyRunning, last_search, run_search

TZ = timezone(timedelta(hours=-6))  # Mexico City
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=TZ)


def cfg(**kw):
    base = dict(
        terms=["backend developer", "fullstack"],
        sites=["linkedin", "indeed"],
        location="Mexico",
        country_indeed="Mexico",
        results_wanted=10,
        first_search_days=7,
        overlap_hours=6,
        pause_seconds=0,
    )
    base.update(kw)
    return SearchConfig(**base)


def row(site, ext_id, title="Backend Developer", company="Acme", **kw):
    data = dict(
        id=ext_id, site=site, job_url=f"https://{site}.example/{ext_id}", job_url_direct=None,
        title=title, company=company, location="Ciudad de México", date_posted=None,
        job_type=None, interval=None, min_amount=None, max_amount=None, currency=None,
        salary_source=None, is_remote=False, description="", skills=None,
    )
    data.update(kw)
    return data


class FakeScraper:
    """Stands in for jobspy.scrape_jobs; records every call."""

    def __init__(self, results=None, fail_sites=()):
        self.results = results or {}
        self.fail_sites = set(fail_sites)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        site = kw["site_name"][0]
        if site in self.fail_sites:
            raise RuntimeError(f"{site} blocked (429)")
        rows = self.results.get((site, kw["search_term"]), [])
        return pd.DataFrame(rows)


@pytest.fixture
def conn():
    return connect(":memory:")


def hours_by_site(scraper):
    return {c["site_name"][0]: c["hours_old"] for c in scraper.calls}


def test_first_search_uses_default_window(conn):
    scraper = FakeScraper()
    run_search(conn, cfg(), scrape=scraper, now=NOW)
    assert hours_by_site(scraper) == {"linkedin": 7 * 24, "indeed": 7 * 24}
    assert {c["search_term"] for c in scraper.calls} == {"backend developer", "fullstack"}
    assert all(c["country_indeed"] == "Mexico" and c["location"] == "Mexico" for c in scraper.calls)


def test_next_search_covers_since_last_search_plus_overlap(conn):
    run_search(conn, cfg(), scrape=FakeScraper(), now=NOW)
    scraper = FakeScraper()
    run_search(conn, cfg(), scrape=scraper, now=NOW + timedelta(days=3, hours=2))
    assert hours_by_site(scraper) == {"linkedin": 74 + 6, "indeed": 74 + 6}


def test_failed_site_keeps_its_previous_range(conn):
    run_search(conn, cfg(), scrape=FakeScraper(), now=NOW)
    result = run_search(conn, cfg(), scrape=FakeScraper(fail_sites=["linkedin"]), now=NOW + timedelta(days=1))
    assert result.status == "partial"
    assert "linkedin" in result.errors

    scraper = FakeScraper()
    run_search(conn, cfg(), scrape=scraper, now=NOW + timedelta(days=2))
    assert hours_by_site(scraper) == {"linkedin": 48 + 6, "indeed": 24 + 6}


def test_fully_failed_search_does_not_move_last_searched(conn):
    run_search(conn, cfg(), scrape=FakeScraper(), now=NOW)
    result = run_search(conn, cfg(), scrape=FakeScraper(fail_sites=["linkedin", "indeed"]), now=NOW + timedelta(days=1))
    assert result.status == "failed"
    assert last_search(conn).finished_at == NOW


def test_seen_again_job_keeps_applied_status_and_is_not_new(conn):
    results = {("linkedin", "backend developer"): [row("linkedin", "1")]}
    first = run_search(conn, cfg(), scrape=FakeScraper(results), now=NOW)
    assert first.new_count == 1
    conn.execute("UPDATE jobs SET status = 'applied'")

    second = run_search(conn, cfg(), scrape=FakeScraper(results), now=NOW + timedelta(days=1))
    assert second.new_count == 0
    job = conn.execute("SELECT status, first_seen_run, first_seen_at FROM jobs").fetchone()
    assert job["status"] == "applied"
    assert job["first_seen_run"] == first.run_id
    assert job["first_seen_at"] == NOW.isoformat()


def test_exact_duplicate_across_terms_is_stored_once(conn):
    results = {
        ("indeed", "backend developer"): [row("indeed", "9")],
        ("indeed", "fullstack"): [row("indeed", "9")],
    }
    result = run_search(conn, cfg(), scrape=FakeScraper(results), now=NOW)
    assert result.new_count == 1
    jobs = conn.execute("SELECT search_terms FROM jobs").fetchall()
    assert [j["search_terms"] for j in jobs] == ["backend developer, fullstack"]


def test_cross_site_duplicate_is_grouped_not_deleted(conn):
    results = {
        ("linkedin", "backend developer"): [row("linkedin", "1", "Sr. Backend Developer", "Acme S.A. de C.V.")],
        ("indeed", "backend developer"): [row("indeed", "2", "Senior Backend Developer (Remoto)", "Acme")],
        ("indeed", "fullstack"): [row("indeed", "3", "Fullstack Developer", "Acme")],
    }
    run_search(conn, cfg(), scrape=FakeScraper(results), now=NOW)
    jobs = {j["external_id"]: j["dup_group"] for j in conn.execute("SELECT external_id, dup_group FROM jobs")}
    assert len(jobs) == 3
    assert jobs["1"] is not None and jobs["1"] == jobs["2"]
    assert jobs["3"] is None


def test_enrichment_is_stored(conn):
    desc = "Sueldo bruto: $50,000 - $60,000 MXN mensuales. Stack: Ruby on Rails, PostgreSQL."
    results = {("linkedin", "backend developer"): [row("linkedin", "1", description=desc)]}
    run_search(conn, cfg(), scrape=FakeScraper(results), now=NOW)
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert (job["salary_min"], job["salary_max"], job["salary_currency"], job["salary_interval"]) == (
        50000, 60000, "MXN", "monthly")
    assert job["salary_basis"] == "gross"
    assert job["stack"] == "Ruby, Rails, PostgreSQL"


def test_work_mode_is_stored_for_new_jobs(conn):
    results = {("linkedin", "backend developer"): [
        row("linkedin", "1", title="Dev (Híbrido)"),
        row("linkedin", "2", is_remote=True),
        row("linkedin", "3"),
    ]}
    run_search(conn, cfg(sites=["linkedin"], terms=["backend developer"]), scrape=FakeScraper(results), now=NOW)
    modes = dict(conn.execute("SELECT external_id, work_mode FROM jobs").fetchall())
    assert modes == {"1": "hybrid", "2": "remote", "3": None}


def test_search_refused_while_another_is_running(conn):
    conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (NOW.isoformat(),))
    with pytest.raises(SearchAlreadyRunning):
        run_search(conn, cfg(), scrape=FakeScraper(), now=NOW + timedelta(minutes=5))


def test_several_locations_search_each_term_in_each_location_in_order(conn):
    scraper = FakeScraper()
    locations = ["Monterrey, Nuevo León, Mexico", "Mexico"]
    run_search(conn, cfg(sites=["linkedin"], location=locations), scrape=scraper, now=NOW)
    assert [(c["location"], c["search_term"]) for c in scraper.calls] == [
        ("Monterrey, Nuevo León, Mexico", "backend developer"),
        ("Monterrey, Nuevo León, Mexico", "fullstack"),
        ("Mexico", "backend developer"),
        ("Mexico", "fullstack"),
    ]
    assert all(c["country_indeed"] == "Mexico" for c in scraper.calls)


def test_pause_between_every_request_across_locations(conn):
    pauses = []
    run_search(conn, cfg(sites=["linkedin"], location=["Monterrey", "Mexico"], pause_seconds=8),
               scrape=FakeScraper(), now=NOW, sleep=pauses.append)
    assert pauses == [8, 8, 8]  # 4 requests, a pause before each but the first


def test_progress_and_errors_name_the_location(conn):
    messages = []
    result = run_search(conn, cfg(sites=["linkedin"], terms=["fullstack"], location=["Monterrey", "Mexico"]),
                        scrape=BlockingScraper(raises={("linkedin", "fullstack"): "connection reset"}), now=NOW,
                        on_progress=messages.append)
    assert messages == ["linkedin: fullstack · Monterrey", "linkedin: fullstack · Mexico"]
    assert result.errors["linkedin"] == "fullstack · Mexico: connection reset"


def test_same_job_found_in_two_locations_is_stored_once(conn):
    class ByLocation(FakeScraper):
        def __call__(self, **kw):
            self.calls.append(kw)
            return pd.DataFrame([row("linkedin", "7")])
    result = run_search(conn, cfg(sites=["linkedin"], terms=["fullstack"], location=["Monterrey", "Mexico"]),
                        scrape=ByLocation(), now=NOW)
    assert (result.new_count, result.seen_count) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_config_reads_a_location_list_or_a_single_string(tmp_path):
    from jobfinder.config import load_config
    path = tmp_path / "config.toml"
    body = '[search]\nterms = ["a"]\nsites = ["linkedin"]\ncountry_indeed = "Mexico"\nlocation = {}\n'
    path.write_text(body.format('["Monterrey, Nuevo León, Mexico", "Mexico"]'))
    assert load_config(path).search.locations == ["Monterrey, Nuevo León, Mexico", "Mexico"]
    path.write_text(body.format('"Mexico"'))
    assert load_config(path).search.locations == ["Mexico"]


@pytest.mark.parametrize("bad", [[], [""], ["Mexico", "  "], ""])
def test_config_rejects_an_empty_location(bad):
    with pytest.raises(ValueError, match="location"):
        cfg(location=bad)


class BlockingScraper(FakeScraper):
    """Like JobSpy on a block: some sites answer (rows, errors) or raise, others work."""

    def __init__(self, results=None, errors=None, raises=None):
        super().__init__(results)
        self.errors = errors or {}            # (site, term) -> error text reported with the rows
        self.raises = raises or {}            # (site, term) -> exception message

    def __call__(self, **kw):
        self.calls.append(kw)
        key = (kw["site_name"][0], kw["search_term"])
        if key in self.raises:
            raise RuntimeError(self.raises[key])
        df = pd.DataFrame(self.results.get(key, []))
        if key in self.errors:
            df.attrs["errors"] = {key[0]: self.errors[key]}
        return df


def calls_of(scraper):
    return [(c["site_name"][0], c["search_term"], c["location"]) for c in scraper.calls]


def test_offers_returned_together_with_an_error_are_kept(conn):
    scraper = BlockingScraper(
        results={("linkedin", "backend developer"): [row("linkedin", "1"), row("linkedin", "2", title="Python Developer")],
                 ("indeed", "backend developer"): [row("indeed", "9", title="Java Engineer", company="Globex")]},
        errors={("linkedin", "backend developer"): "response status code 400 (bad page)"},
    )
    result = run_search(conn, cfg(), scrape=scraper, now=NOW)
    assert {j["external_id"] for j in conn.execute("SELECT external_id FROM jobs")} == {"1", "2", "9"}
    assert (result.new_count, result.seen_count) == (3, 0)
    assert result.errors == {"linkedin": "backend developer · Mexico: response status code 400 (bad page)"}
    assert result.status == "partial"
    # a non rate-limit error does not stop the site: the next term still runs
    assert calls_of(scraper)[:2] == [("linkedin", "backend developer", "Mexico"), ("linkedin", "fullstack", "Mexico")]
    saved = conn.execute("SELECT new_count, seen_count FROM runs").fetchone()
    assert (saved["new_count"], saved["seen_count"]) == (3, 0)


def test_rate_limit_keeps_rows_and_stops_that_site_for_the_search(conn):
    scraper = BlockingScraper(
        results={("linkedin", "backend developer"): [row("linkedin", "1")],
                 ("indeed", "backend developer"): [row("indeed", "9", title="Java Engineer", company="Globex")]},
        errors={("linkedin", "backend developer"): "response status code 429 (blocked: use a proxy)"},
    )
    pauses, messages = [], []
    result = run_search(conn, cfg(location=["Monterrey", "Mexico"], pause_seconds=8), scrape=scraper, now=NOW,
                        sleep=pauses.append, on_progress=messages.append)

    # linkedin: only its first request; indeed: all four (term × place)
    assert calls_of(scraper) == [
        ("linkedin", "backend developer", "Monterrey"),
        ("indeed", "backend developer", "Monterrey"),
        ("indeed", "fullstack", "Monterrey"),
        ("indeed", "backend developer", "Mexico"),
        ("indeed", "fullstack", "Mexico"),
    ]
    assert messages == [f"{s}: {t} · {l}" for s, t, l in calls_of(scraper)]
    assert pauses == [8, 8, 8]                        # none spent on the 3 skipped linkedin requests
    assert conn.execute("SELECT external_id FROM jobs WHERE site = 'linkedin'").fetchone()[0] == "1"
    assert (result.new_count, result.seen_count) == (2, 1)   # indeed's offer shows up again for the 2nd place
    assert result.status == "partial"
    assert result.errors == {
        "linkedin": "backend developer · Monterrey: response status code 429 (blocked: use a proxy)"
                    " (stopped after a rate limit; 3 requests skipped)"
    }
    sites = {r["site"]: (r["status"], r["error"]) for r in conn.execute("SELECT * FROM run_sites")}
    assert sites["linkedin"] == ("failed", result.errors["linkedin"])
    assert sites["indeed"] == ("success", None)
    assert conn.execute("SELECT error FROM runs").fetchone()[0] == f"linkedin: {result.errors['linkedin']}"


def test_rate_limit_raised_as_exception_also_stops_the_site(conn):
    scraper = BlockingScraper(raises={("linkedin", "backend developer"): "linkedin 429 Too Many Requests"})
    result = run_search(conn, cfg(), scrape=scraper, now=NOW)
    assert calls_of(scraper) == [
        ("linkedin", "backend developer", "Mexico"),
        ("indeed", "backend developer", "Mexico"),
        ("indeed", "fullstack", "Mexico"),
    ]
    assert result.errors["linkedin"] == (
        "backend developer · Mexico: linkedin 429 Too Many Requests (stopped after a rate limit; 1 request skipped)")


def test_rate_limit_on_the_last_request_has_nothing_to_report_as_skipped(conn):
    scraper = BlockingScraper(raises={("linkedin", "fullstack"): "linkedin 429"})
    result = run_search(conn, cfg(), scrape=scraper, now=NOW)
    assert result.errors == {"linkedin": "fullstack · Mexico: linkedin 429"}


def test_other_errors_keep_going_with_the_next_term(conn):
    scraper = BlockingScraper(
        results={("linkedin", "fullstack"): [row("linkedin", "5", title="Fullstack Developer")]},
        raises={("linkedin", "backend developer"): "connection reset"},
    )
    result = run_search(conn, cfg(), scrape=scraper, now=NOW)
    assert calls_of(scraper)[:2] == [("linkedin", "backend developer", "Mexico"), ("linkedin", "fullstack", "Mexico")]
    assert result.new_count == 1
    assert result.errors == {"linkedin": "backend developer · Mexico: connection reset"}
    assert "stopped" not in result.errors["linkedin"]


def test_next_search_retries_the_window_of_a_rate_limited_site(conn):
    run_search(conn, cfg(), scrape=FakeScraper(), now=NOW)
    blocked = BlockingScraper(raises={("linkedin", "backend developer"): "linkedin 429"})
    run_search(conn, cfg(), scrape=blocked, now=NOW + timedelta(days=1))
    retry = FakeScraper()
    result = run_search(conn, cfg(), scrape=retry, now=NOW + timedelta(days=2))
    hours = hours_by_site(retry)
    assert hours["linkedin"] == 48 + 6       # since the last successful linkedin run (2 days ago), not yesterday's block
    assert hours["indeed"] == 24 + 6
    assert result.status == "success"
