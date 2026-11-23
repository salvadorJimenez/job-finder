"""PWA contract: manifest, head tags on every page, icons, offline page and service worker."""
import json
import re
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobfinder.db import connect
from jobfinder.web import create_app
from test_board import make_app
from test_web import NOW, add_job, add_run, make_cfg

ROOT = Path(__file__).parent.parent / "jobfinder"
ICON_SIZES = {
    "favicon-32.png": 32,
    "apple-touch-icon.png": 180,
    "icon-192.png": 192,
    "icon-512.png": 512,
    "icon-512-maskable.png": 512,
}


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    return TestClient(create_app(cfg, clock=lambda: NOW)), conn


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


# --- manifest -----------------------------------------------------------------------------

def test_manifest_is_served_with_its_media_type(env):
    client, _ = env
    res = client.get("/manifest.webmanifest")
    assert res.status_code == 200
    assert res.headers["content-type"].split(";")[0] == "application/manifest+json"


def test_manifest_fields(env):
    client, _ = env
    m = client.get("/manifest.webmanifest").json()
    assert m["name"] == "job_finder"
    assert m["short_name"] == "job_finder"
    assert m["description"]
    assert (m["start_url"], m["scope"], m["id"]) == ("/", "/", "/")
    assert m["display"] == "standalone"
    assert m["background_color"] == "#000000"
    assert m["theme_color"] == "#000000"


def test_manifest_icons_exist_and_declare_their_size(env):
    client, _ = env
    icons = client.get("/manifest.webmanifest").json()["icons"]
    seen = {(i["sizes"], i["purpose"]) for i in icons}
    assert seen == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for icon in icons:
        assert icon["type"] == "image/png"
        res = client.get(icon["src"])
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"
        side = int(icon["sizes"].split("x")[0])
        assert png_size(ROOT / "static" / icon["src"].removeprefix("/static/")) == (side, side)


@pytest.mark.parametrize("name,side", ICON_SIZES.items())
def test_icon_files_have_the_right_pixel_size(name, side):
    assert png_size(ROOT / "static/icons" / name) == (side, side)


def test_icons_are_packaged():
    assert "static/icons/*" in (ROOT.parent / "pyproject.toml").read_text()


# --- head tags on every page ----------------------------------------------------------------

def pages(client, conn):
    run = add_run(conn, NOW)
    job_id = add_job(conn, run, NOW, "j1")
    app_id = make_app(conn, "a1")
    return ["/", f"/jobs/{job_id}", "/applications", f"/applications/{app_id}",
            "/applications/phases", "/applications/new", "/offline"]


def test_every_page_links_manifest_theme_color_and_icons(env):
    client, conn = env
    for url in pages(client, conn):
        res = client.get(url)
        assert res.status_code == 200, url
        html = res.text
        head = html.split("</head>")[0]
        assert '<link rel="manifest" href="/manifest.webmanifest">' in head, url
        assert '<meta name="theme-color" content="#000000">' in head, url
        assert re.search(r'<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon\.png">', head), url
        assert re.search(r'<link rel="icon" type="image/png" sizes="32x32" href="/static/icons/favicon-32\.png">', head), url
        assert html.count('rel="manifest"') == 1, url


def test_head_tags_live_in_one_partial():
    partial = (ROOT / "templates/_head_meta.html").read_text()
    assert "manifest.webmanifest" in partial
    for name in ("index", "detail", "phases", "base", "offline"):
        text = (ROOT / f"templates/{name}.html").read_text()
        assert '{% include "_head_meta.html" %}' in text, name
        assert "manifest" not in text, name


# --- offline page -----------------------------------------------------------------------------

def test_offline_page_explains_how_to_start_the_server(env):
    client, _ = env
    html = client.get("/offline").text
    assert "job_finder is not running" in html
    assert ".venv/bin/job-finder serve --reload" in html


# --- service worker ---------------------------------------------------------------------------

def test_service_worker_is_served_from_root_and_never_cached(env):
    client, _ = env
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert res.headers["content-type"].split(";")[0] == "text/javascript"
    assert res.headers["cache-control"] == "no-cache"


def sw_source(client):
    return client.get("/sw.js").text


def test_service_worker_cache_name_is_versioned_and_old_caches_deleted(env):
    client, _ = env
    src = sw_source(client)
    assert re.search(r'const CACHE = "job-finder-v\d+"', src)
    activate = src.split('addEventListener("activate"')[1].split('addEventListener("fetch"')[0]
    assert "caches.keys()" in activate and "caches.delete" in activate and "CACHE" in activate


def test_service_worker_precaches_offline_page_and_assets(env):
    client, _ = env
    src = sw_source(client)
    install = src.split('addEventListener("install"')[1].split('addEventListener("activate"')[0]
    for asset in ("/offline", "/static/style.css", "/static/fonts/GeistPixel-Square.woff2",
                  "/static/fonts/GeistMono-Variable.woff2", "/static/icons/favicon-32.png"):
        assert asset in src, asset
    assert "cache.addAll" in install or "addAll" in install


def test_service_worker_only_handles_get_navigations_network_first(env):
    client, _ = env
    src = sw_source(client)
    fetch = src.split('addEventListener("fetch"')[1]
    assert 'request.method !== "GET"' in fetch
    assert 'request.mode !== "navigate"' in fetch
    assert "fetch(request)" in fetch                   # network first
    assert "caches.match(OFFLINE_URL)" in fetch        # offline fallback
    assert "cache.put" not in fetch                    # pages and API answers are never cached
    assert "cache.add(" not in fetch


def test_service_worker_precaches_files_that_exist(env):
    client, _ = env
    urls = re.findall(r'"(/(?:static|offline)[^"]*)"', sw_source(client))
    assert len(urls) >= 5
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_service_worker_source_matches_file_edits_without_restart(env):
    client, _ = env
    assert sw_source(client) == (ROOT / "static/sw.js").read_text()


def test_app_js_registers_the_service_worker():
    js = (ROOT / "static/app.js").read_text()
    assert '"serviceWorker" in navigator' in js
    assert 'navigator.serviceWorker.register("/sw.js"' in js


def test_cross_site_posts_still_refused_and_same_origin_allowed(env):
    client, conn = env
    run = add_run(conn, NOW)
    job_id = add_job(conn, run, NOW, "j1")
    res = client.post(f"/jobs/{job_id}/pin", data={"pinned": "1"}, headers={"origin": "https://evil.example"},
                      follow_redirects=False)
    assert res.status_code == 403
    res = client.post(f"/jobs/{job_id}/pin", data={"pinned": "1"}, headers={"origin": "http://testserver"},
                      follow_redirects=False)
    assert res.status_code == 303


# --- leaving the app --------------------------------------------------------------------------

def test_external_links_open_in_the_normal_browser(env):
    client, conn = env
    run = add_run(conn, NOW)
    job_id = add_job(conn, run, NOW, "j1")
    app_id = make_app(conn, "a1", url="https://jobs.example/1")
    for url in (f"/jobs/{job_id}", f"/applications/{app_id}"):
        html = client.get(url).text
        for tag in re.findall(r'<a [^>]*href="https?://[^"]*"[^>]*>', html):
            assert 'target="_blank"' in tag and "noopener" in tag, tag
