"""S1 — job detail page."""
import re
from datetime import timedelta
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest

from test_web import NOW, add_job, add_run, env  # noqa: F401  (env is a fixture)

SEEN = NOW - timedelta(days=1)


def make_job(conn, ext_id="42", **kw):
    run = add_run(conn, SEEN)
    job_id = add_job(conn, run, SEEN, ext_id, **kw)
    conn.commit()
    return job_id


def status_of(conn, job_id):
    return conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]


def hrefs(html):
    return [unescape(h) for h in re.findall(r'href="([^"]*)"', html)]


# --- content ------------------------------------------------------------------

def test_detail_shows_all_job_fields(env):
    client, conn, _, _ = env
    job_id = make_job(
        conn, title="Senior Spring Engineer", company="Acme Corp", location="CDMX",
        work_mode="hybrid", salary_min=50000, salary_max=60000, salary_currency="MXN",
        salary_interval="monthly", salary_basis="net", stack="Kotlin, Spring, PostgreSQL",
        date_posted="2026-11-02", description="Build things.",
    )
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    html = r.text
    for text in ("Senior Spring Engineer", "Acme Corp", "CDMX", "HYBRID",
                 "MXN 50,000–60,000 / month", "net", "Kotlin", "PostgreSQL",
                 "2026-11-02", SEEN.isoformat()[:10], "Build things."):
        assert text in html
    assert "posted" in html.lower() and "first seen" in html.lower()


def test_detail_without_salary_or_mode_shows_fallbacks(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    html = client.get(f"/jobs/{job_id}").text
    assert "not published" in html
    assert "gross/net not specified" in html
    assert "tag mode" not in html


def test_detail_unknown_job_is_404(env):
    client, _, _, _ = env
    assert client.get("/jobs/999").status_code == 404


def test_open_posting_link_is_safe_and_points_to_original_url(env):
    client, conn, _, _ = env
    job_id = make_job(conn, "42")
    html = client.get(f"/jobs/{job_id}").text
    assert re.search(
        r'<a [^>]*href="https://www\.linkedin\.com/jobs/view/42"[^>]*>Open posting ↗</a>', html)
    link = re.search(r'<a [^>]*>Open posting ↗</a>', html).group(0)
    assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link


def test_open_posting_link_is_omitted_for_non_http_urls(env):
    client, conn, _, _ = env
    job_id = make_job(conn, url="javascript:alert(1)")
    html = client.get(f"/jobs/{job_id}").text
    assert "javascript:" not in html
    assert "Open posting ↗" not in html


# --- back to list ---------------------------------------------------------------

def back_href(html):
    return re.search(r'<a [^>]*class="back"[^>]*href="([^"]*)"', html) or \
        re.search(r'<a [^>]*href="([^"]*)"[^>]*class="back"', html)


def test_back_to_list_returns_to_same_filters_and_page(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    back = "/?site=linkedin&stack=spring&page=3"
    html = client.get(f"/jobs/{job_id}", params={"back": back}).text
    m = back_href(html)
    assert m and unescape(m.group(1)) == back
    assert "Back to list" in html


@pytest.mark.parametrize("bad", ["https://evil.example", "//evil.example", "javascript:alert(1)", "evil", ""])
def test_unsafe_back_falls_back_to_root(env, bad):
    client, conn, _, _ = env
    job_id = make_job(conn)
    html = client.get(f"/jobs/{job_id}", params={"back": bad}).text
    assert unescape(back_href(html).group(1)) == "/"
    assert "evil.example" not in html


def test_detail_forms_redirect_back_to_detail_keeping_back(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    back = "/?site=linkedin&page=2"
    html = client.get(f"/jobs/{job_id}", params={"back": back}).text
    nexts = [unescape(v) for v in re.findall(r'name="next" value="([^"]*)"', html)]
    assert len(nexts) == 2                      # status form + pin form
    for n in nexts:
        url = urlparse(n)
        assert url.path == f"/jobs/{job_id}"
        assert parse_qs(url.query) == {"back": [back]}
    # and posting to such a target really redirects to the detail page
    r = client.post(f"/jobs/{job_id}/status", data={"status": "seen", "next": nexts[0]},
                    follow_redirects=False)
    assert r.status_code == 303 and unescape(r.headers["location"]) == nexts[0]
    r = client.post(f"/jobs/{job_id}/pin", data={"pinned": "1", "next": nexts[1]},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == nexts[1]


def test_detail_forms_with_unsafe_back_keep_only_the_detail_url(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    html = client.get(f"/jobs/{job_id}", params={"back": "//evil.example"}).text
    for n in re.findall(r'name="next" value="([^"]*)"', html):
        assert "evil" not in n


def test_detail_has_status_selector_and_pin_toggle(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    html = client.get(f"/jobs/{job_id}").text
    assert f'action="/jobs/{job_id}/status"' in html
    assert re.search(r'<option value="seen"\s+selected', html)     # opening marked it seen
    assert f'action="/jobs/{job_id}/pin"' in html and ">PIN<" in html
    conn.execute("UPDATE jobs SET pinned_at = ?", (NOW.isoformat(),))
    conn.commit()
    html = client.get(f"/jobs/{job_id}").text
    assert ">UNPIN<" in html and 'name="pinned" value="0"' in html


def test_list_to_detail_back_round_trip(env):
    client, conn, _, _ = env
    make_job(conn, site="indeed")
    list_html = client.get("/", params={"site": "indeed", "page": "1"}).text
    href = unescape(re.search(r'class="title" href="([^"]+)"', list_html).group(1))
    detail = client.get(href).text
    assert unescape(back_href(detail).group(1)) == "/?site=indeed&page=1"
    assert client.get(unescape(back_href(detail).group(1))).status_code == 200


# --- seen on open ---------------------------------------------------------------

def test_opening_detail_marks_new_job_as_seen(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    assert status_of(conn, job_id) == "new"
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert status_of(conn, job_id) == "seen"


@pytest.mark.parametrize("status", ["applied", "discarded", "seen"])
def test_opening_detail_never_changes_other_statuses(env, status):
    client, conn, _, _ = env
    job_id = make_job(conn, status=status)
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert status_of(conn, job_id) == status


def test_opening_unknown_detail_changes_nothing(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    assert client.get("/jobs/999").status_code == 404
    assert status_of(conn, job_id) == "new"


# --- description: safe Markdown ---------------------------------------------------

def description_html(client, conn, text):
    job_id = make_job(conn, description=text)
    html = client.get(f"/jobs/{job_id}").text
    start = html.index('class="description"')
    return html[start:html.index("</article>", start)]


def test_markdown_list_and_bold_render(env):
    client, conn, _, _ = env
    body = description_html(client, conn, "**Requirements** and more\n\n- Python\n- SQL\n")
    assert "<strong>Requirements</strong> and more" in body
    assert "<ul>" in body and "<li>Python</li>" in body and "<li>SQL</li>" in body


def test_script_tag_in_description_is_escaped(env):
    client, conn, _, _ = env
    body = description_html(client, conn, "Hello <script>alert(1)</script> world")
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_raw_img_with_onerror_in_description_is_escaped(env):
    client, conn, _, _ = env
    body = description_html(client, conn, "<img src=x onerror=alert(1)>")
    assert "<img" not in body
    assert "&lt;img" in body


def test_raw_html_block_is_escaped_too(env):
    client, conn, _, _ = env
    body = description_html(client, conn, "<div onclick=alert(1)>x</div>\n\n<iframe src=//evil></iframe>")
    assert "<div" not in body and "<iframe" not in body


@pytest.mark.parametrize("url", ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<b>x</b>",
                                 "vbscript:msgbox(1)"])
def test_dangerous_markdown_links_get_no_href(env, url):
    client, conn, _, _ = env
    body = description_html(client, conn, f"[click me]({url})")
    # markdown-it leaves the unsafe link as inert text: no anchor, no href at all
    assert "<a " not in body and "href=" not in body


def test_safe_links_open_in_new_tab_with_noopener(env):
    client, conn, _, _ = env
    body = description_html(client, conn, "[apply](https://example.com/apply?a=1&b=2)")
    link = re.search(r"<a [^>]*>apply</a>", body).group(0)
    assert 'href="https://example.com/apply?a=1&amp;b=2"' in link
    assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link


def test_markdown_images_are_not_loaded(env):
    client, conn, _, _ = env
    body = description_html(client, conn, "![pixel](https://tracker.example/p.gif)")
    assert "<img" not in body


def test_missing_description_shows_placeholder(env):
    client, conn, _, _ = env
    job_id = make_job(conn, description=None)
    html = client.get(f"/jobs/{job_id}").text
    assert "No description" in html


def test_title_and_company_are_escaped(env):
    client, conn, _, _ = env
    job_id = make_job(conn, title="<script>alert(1)</script>", company="<b>x</b>")
    html = client.get(f"/jobs/{job_id}").text
    assert "<script>alert" not in html and "<b>x</b>" not in html


def test_detail_uses_same_stylesheet_and_no_external_assets(env):
    client, conn, _, _ = env
    job_id = make_job(conn)
    html = client.get(f"/jobs/{job_id}").text
    assert 'href="/static/style.css?v=' in html
    css = client.get("/static/style.css").text
    assert "75ch" in css and "line-height: 1.6" in css
    assert "fonts.googleapis" not in html
