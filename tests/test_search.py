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
                        scrape=FakeScraper(fail_sites=["linkedin"]), now=NOW, on_progress=messages.append)
    assert messages == ["linkedin: fullstack · Monterrey", "linkedin: fullstack · Mexico"]
    assert result.errors["linkedin"] == "fullstack · Mexico: linkedin blocked (429)"


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
