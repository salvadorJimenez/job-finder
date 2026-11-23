"""Static assets: versioned URLs so a rebuild is picked up at once, and cache headers to match."""
import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

from jobfinder.db import connect
from jobfinder.web import create_app
from test_web import NOW, add_job, add_run, make_cfg

STATIC = Path(__file__).resolve().parent.parent / "jobfinder" / "static"


def version(name):
    return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12]


def client(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    add_job(conn, add_run(conn, NOW), NOW, "1"); conn.commit()
    return TestClient(create_app(cfg, clock=lambda: NOW))


def test_pages_link_assets_with_a_content_version(tmp_path):
    c = client(tmp_path)
    css, js = version("style.css"), version("app.js")
    for url in ("/", "/jobs/1", "/applications", "/applications/phases", "/applications/new"):
        html = c.get(url).text
        assert f'href="/static/style.css?v={css}"' in html, url
        assert f'<script src="/static/app.js?v={js}"></script>' in html, url
        assert not re.search(r'(href|src)="/static/(style\.css|app\.js)"', html), url


def test_versioned_assets_are_cached_long_and_plain_urls_are_revalidated(tmp_path):
    c = client(tmp_path)
    r = c.get(f"/static/style.css?v={version('style.css')}")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
    r = c.get("/static/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_offline_page_keeps_the_plain_url_the_service_worker_precaches(tmp_path):
    html = client(tmp_path).get("/offline").text
    assert 'href="/static/style.css"' in html
