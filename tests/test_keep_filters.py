"""The list filters survive moving between Jobs and Applications (until the tab is closed)."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from jobfinder.web import create_app
from test_web import NOW, make_cfg

APP_JS = (Path(__file__).resolve().parent.parent / "jobfinder" / "static" / "app.js").read_text()


def client(tmp_path):
    return TestClient(create_app(make_cfg(tmp_path), clock=lambda: NOW))


def test_links_back_to_the_list_are_marked_to_restore_filters(tmp_path):
    c = client(tmp_path)
    for url in ("/", "/applications", "/applications/phases", "/applications/new"):
        html = c.get(url).text
        assert re.search(r'<h1><a href="/" data-keep-filters>JOB_FINDER</a></h1>', html), url
        assert re.search(r'<a href="/" data-keep-filters(?: aria-current="page")?>Jobs</a>', html), url
    assert '<a href="/" data-keep-filters>list</a>' in c.get("/applications").text


def test_every_page_loads_the_script_that_restores_filters(tmp_path):
    from test_web import add_job, add_run
    from jobfinder.db import connect
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    add_job(conn, add_run(conn, NOW), NOW, "1"); conn.commit()
    c = TestClient(create_app(cfg, clock=lambda: NOW))
    for url in ("/", "/jobs/1", "/applications", "/applications/phases", "/applications/new"):
        assert '<script src="/static/app.js?v=' in c.get(url).text, url


def test_reset_links_forget_the_saved_filters(tmp_path):
    c = client(tmp_path)
    html = c.get("/", params={"site": "indeed"}).text
    assert '<a class="reset" href="/" data-reset-filters>Reset filters</a>' in html
    assert '<a href="/" data-reset-filters>Clear all filters</a>' in html


def test_app_js_saves_the_list_query_per_tab_and_restores_it_on_marked_links():
    assert "sessionStorage" in APP_JS and "job-finder:list-query" in APP_JS
    assert "a[data-keep-filters]" in APP_JS and "a[data-reset-filters]" in APP_JS
