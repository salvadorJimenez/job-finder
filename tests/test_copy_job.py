"""COPY FOR RESUME: the job view carries a plain-text block (header lines + the tidied description)
that app.js puts on the clipboard, so it can be pasted into the resume-writer prompt."""
import re
from datetime import timedelta
from html import unescape
from pathlib import Path

from jobfinder.describe import normalize_description
from test_web import NOW, add_job, add_run, env  # noqa: F401  (env is a fixture)

ROOT = Path(__file__).resolve().parent.parent / "jobfinder"
APP_JS = (ROOT / "static" / "app.js").read_text()
CSS = (ROOT / "static" / "style.css").read_text()
SEEN = NOW - timedelta(days=1)

DESCRIPTION = "**The Role**\n\nWe build payment APIs.\n\n* Kotlin and Spring\n* PostgreSQL\n"


def make_job(conn, ext_id="42", **kw):
    run = add_run(conn, SEEN)
    job_id = add_job(conn, run, SEEN, ext_id, **kw)
    conn.commit()
    return job_id


def copy_block(html):
    """The text the button copies: the content of the [data-copy-text] element, HTML-unescaped."""
    m = re.search(r"<textarea[^>]*\bdata-copy-text\b[^>]*>(.*?)</textarea>", html, re.S)
    assert m, "no copy block in the page"
    return unescape(m.group(1))


FULL = dict(title="Senior Spring Engineer", company="Acme Corp", location="CDMX", work_mode="hybrid",
            date_posted="2026-11-02", salary_min=50000, salary_max=60000, salary_currency="MXN",
            salary_interval="monthly", salary_basis="gross", description=DESCRIPTION)


def test_copy_block_has_the_header_lines_and_the_normalized_description(env):
    client, conn, _, _ = env
    job_id = make_job(conn, **FULL)
    expected = (
        "Company: Acme Corp\n"
        "Role: Senior Spring Engineer\n"
        "Location: CDMX · hybrid\n"
        "Source: linkedin · https://www.linkedin.com/jobs/view/42\n"
        "Posted: 2026-11-02\n"
        "Salary: MXN 50,000–60,000 / month · gross\n"
        "\n"
        "Job description:\n"
        + normalize_description(DESCRIPTION)
    )
    for url in (f"/jobs/{job_id}", f"/jobs/{job_id}?pane=1"):
        r = client.get(url)
        assert r.status_code == 200
        assert copy_block(r.text) == expected


def test_missing_fields_render_dashes_and_not_published(env):
    client, conn, _, _ = env
    job_id = make_job(conn, company="", location="", description="")
    for url in (f"/jobs/{job_id}", f"/jobs/{job_id}?pane=1"):
        text = copy_block(client.get(url).text)
        lines = text.splitlines()
        assert lines[0] == "Company: —"
        assert lines[2] == "Location: —"
        assert lines[3] == "Source: linkedin · https://www.linkedin.com/jobs/view/42"
        assert lines[4] == "Posted: —"
        assert lines[5] == "Salary: not published"
        assert lines[6] == "" and lines[7] == "Job description:"
        assert lines[8:] == ["—"]


def test_source_without_a_usable_url_is_just_the_site(env):
    client, conn, _, _ = env
    job_id = make_job(conn, url="javascript:alert(1)", **{k: v for k, v in FULL.items() if k != "work_mode"})
    text = copy_block(client.get(f"/jobs/{job_id}").text)
    assert "Source: linkedin\n" in text
    assert "javascript" not in text
    assert "Location: CDMX\n" in text


def test_html_in_the_description_stays_escaped(env):
    client, conn, _, _ = env
    job_id = make_job(conn, title="<b>Boss</b>", company="A & B </textarea><script>alert(1)</script>",
                      description="Hello <img src=x onerror=alert(1)> world </textarea><script>boom()</script>")
    for url in (f"/jobs/{job_id}", f"/jobs/{job_id}?pane=1"):
        html = client.get(url).text
        assert "<script>alert(1)</script>" not in html and "<script>boom()</script>" not in html
        assert "<img src=x" not in html
        block = re.search(r"<textarea[^>]*\bdata-copy-text\b[^>]*>(.*?)</textarea>", html, re.S).group(1)
        assert "&lt;img src=x onerror=alert(1)&gt;" in block
        assert "&lt;/textarea&gt;" in block
        assert "Company: A & B </textarea><script>alert(1)</script>" in copy_block(html)   # text survives, escaped in markup


def test_button_and_hooks_in_the_job_view(env):
    client, conn, _, _ = env
    job_id = make_job(conn, **FULL)
    for url in (f"/jobs/{job_id}", f"/jobs/{job_id}?pane=1"):
        html = client.get(url).text
        start = html.index('class="job-head"')
        head = html[start:html.index("</header>", start)]
        assert re.search(r'<button[^>]*class="btn"[^>]*data-copy-job[^>]*>COPY FOR RESUME</button>', head) \
            or re.search(r'<button[^>]*data-copy-job[^>]*class="btn"[^>]*>COPY FOR RESUME</button>', head)
        assert 'type="button"' in head
        assert 'aria-live="polite"' in head and "data-copy-status" in head
        assert html.count("data-copy-text") == 1


def test_js_copies_with_clipboard_api_and_falls_back(env):
    assert "data-copy-job" in APP_JS
    assert "navigator.clipboard.writeText" in APP_JS
    assert 'document.execCommand("copy")' in APP_JS
    assert "COPIED ✓" in APP_JS and "COPY FAILED" in APP_JS
    assert "2000" in APP_JS
    # delegated on document, so it also works for a pane loaded with fetch
    assert re.search(r'document\.addEventListener\("click", function \(event\) \{[\s\S]{0,300}data-copy-job', APP_JS)


def test_copy_source_is_hidden_visually_but_selectable():
    rule = re.search(r"\.copy-source\s*\{([^}]*)\}", CSS)
    assert rule and "display: none" not in rule.group(1) and "visibility: hidden" not in rule.group(1)
