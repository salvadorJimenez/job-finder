import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from jobfinder import cvs
from jobfinder.db import connect
from jobfinder.web import create_app
from test_board import make_app
from test_web import NOW, make_cfg

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
DOCX = b"PK\x03\x04" + b"\x00" * 64
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    return client, conn, tmp_path / "cvs"


def upload(client, app_id, name, data, ctype="application/octet-stream"):
    return client.post(f"/applications/{app_id}/cv", files={"file": (name, data, ctype)})


def cv_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM cv_files ORDER BY id")]


def stored(cv_dir):
    return sorted(p.name for p in cv_dir.iterdir()) if cv_dir.exists() else []


def attached(conn, app_id):
    return conn.execute("SELECT cv_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]


# --- upload ok ----------------------------------------------------------------------------

def test_pdf_upload_is_stored_by_hash_and_attached(env):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    r = upload(client, app_id, "My CV.pdf", PDF)
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{app_id}")
    sha = hashlib.sha256(PDF).hexdigest()
    (row,) = cv_rows(conn)
    assert (row["original_name"], row["sha256"]) == ("My CV.pdf", sha)
    assert row["uploaded_at"]
    assert (cv_dir / f"{sha}.pdf").read_bytes() == PDF
    assert stored(cv_dir) == [f"{sha}.pdf"]
    assert str(cv_dir / f"{sha}.pdf") == row["stored_path"]
    assert attached(conn, app_id) == row["id"]


def test_docx_upload_ok(env):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    assert upload(client, app_id, "cv.DOCX", DOCX).status_code == 303
    sha = hashlib.sha256(DOCX).hexdigest()
    assert stored(cv_dir) == [f"{sha}.docx"]
    assert cv_rows(conn)[0]["original_name"] == "cv.DOCX"


def test_exactly_10_mb_is_accepted(env):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    data = PDF + b"0" * (cvs.MAX_BYTES - len(PDF))
    assert len(data) == 10 * 1024 * 1024
    assert upload(client, app_id, "big.pdf", data).status_code == 303
    assert len(cv_rows(conn)) == 1


# --- rejected uploads: nothing stored ------------------------------------------------------

def assert_nothing_stored(conn, cv_dir, app_id):
    assert cv_rows(conn) == []
    assert stored(cv_dir) == []
    assert attached(conn, app_id) is None


@pytest.mark.parametrize("name, data", [
    ("cv.txt", PDF),                       # wrong extension
    ("cv.doc", DOCX),
    ("cv", PDF),
    ("cv.pdf.exe", PDF),
    ("cv.exe", b"MZ\x90\x00"),
    ("cv.pdf", b"MZ\x90\x00\x03\x00\x00\x00"),   # executable renamed to .pdf
    ("cv.pdf", DOCX),                      # content does not match the extension
    ("cv.docx", PDF),
    ("cv.pdf", b""),
    ("cv.docx", b"PK"),
    ("cv.pdf", b"  %PDF-1.7"),             # magic must be at the very start
])
def test_bad_extension_or_content_is_rejected_and_nothing_stored(env, name, data):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    r = upload(client, app_id, name, data)
    assert r.status_code == 400
    assert 'role="alert"' in r.text
    assert_nothing_stored(conn, cv_dir, app_id)


def test_oversize_is_rejected_with_413_and_nothing_stored(env):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    r = upload(client, app_id, "big.pdf", PDF + b"0" * cvs.MAX_BYTES)
    assert r.status_code == 413
    assert_nothing_stored(conn, cv_dir, app_id)


def test_upload_without_file_or_choice_is_400(env):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    assert client.post(f"/applications/{app_id}/cv", data={}).status_code == 400
    assert client.post(f"/applications/{app_id}/cv", data={"cv_id": ""}).status_code == 400
    assert_nothing_stored(conn, cv_dir, app_id)


def test_upload_for_unknown_application_is_404_and_stores_nothing(env):
    client, conn, cv_dir = env
    assert upload(client, 999, "cv.pdf", PDF).status_code == 404
    assert cv_rows(conn) == [] and stored(cv_dir) == []


def test_rejected_upload_keeps_the_previously_attached_cv(env):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "good.pdf", PDF)
    before = cv_rows(conn)
    assert upload(client, app_id, "bad.pdf", b"nope").status_code == 400
    assert cv_rows(conn) == before and attached(conn, app_id) == before[0]["id"]


# --- file names never reach the filesystem path ------------------------------------------------

@pytest.mark.parametrize("name, shown", [
    ("../../etc/passwd.pdf", "passwd.pdf"),
    ("..\\..\\windows\\evil.pdf", "evil.pdf"),
    ("/abs/path/cv.pdf", "cv.pdf"),
    ("dir/sub/résumé final.pdf", "résumé final.pdf"),
])
def test_path_traversal_names_are_stored_safely_by_hash(env, tmp_path, name, shown):
    client, conn, cv_dir = env
    app_id = make_app(conn, "a")
    assert upload(client, app_id, name, PDF).status_code == 303
    sha = hashlib.sha256(PDF).hexdigest()
    assert stored(cv_dir) == [f"{sha}.pdf"]
    assert cv_rows(conn)[0]["original_name"] == shown
    assert "/" not in shown and "\\" not in shown
    # nothing escaped the data directory
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == sorted(["jobs.db", f"cvs/{sha}.pdf"])


def test_original_name_is_sanitised_and_bounded():
    assert cvs.clean_name("a/b\\c.pdf") == "c.pdf"
    assert cvs.clean_name("evil\r\nname\x00.pdf") == "evilname.pdf"
    assert len(cvs.clean_name("x" * 400 + ".pdf")) <= 255
    assert cvs.clean_name("x" * 400 + ".pdf").endswith(".pdf")
    assert cvs.clean_name("..") == ""


# --- dedupe ------------------------------------------------------------------------------------

def test_identical_content_is_stored_once_and_reuses_the_row(env):
    client, conn, cv_dir = env
    one, two = make_app(conn, "a"), make_app(conn, "b")
    upload(client, one, "first.pdf", PDF)
    upload(client, two, "second.pdf", PDF)
    (row,) = cv_rows(conn)
    assert row["original_name"] == "first.pdf"
    assert len(stored(cv_dir)) == 1
    assert attached(conn, one) == attached(conn, two) == row["id"]


def test_different_content_gets_its_own_row_and_file(env):
    client, conn, cv_dir = env
    one, two = make_app(conn, "a"), make_app(conn, "b")
    upload(client, one, "a.pdf", PDF)
    upload(client, two, "b.pdf", PDF + b"different")
    assert len(cv_rows(conn)) == 2 and len(stored(cv_dir)) == 2
    assert attached(conn, one) != attached(conn, two)


def test_reuploading_replaces_the_attached_cv(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "a.pdf", PDF)
    upload(client, app_id, "b.docx", DOCX)
    assert cv_rows(conn)[attached(conn, app_id) - 1]["original_name"] == "b.docx"


# --- pick existing -------------------------------------------------------------------------------

def test_pick_an_existing_cv(env):
    client, conn, cv_dir = env
    one, two = make_app(conn, "a"), make_app(conn, "b")
    upload(client, one, "shared.pdf", PDF)
    cv_id = cv_rows(conn)[0]["id"]
    r = client.post(f"/applications/{two}/cv", data={"cv_id": str(cv_id)})
    assert (r.status_code, r.headers["location"]) == (303, f"/applications/{two}")
    assert attached(conn, two) == cv_id
    assert len(cv_rows(conn)) == 1 and len(stored(cv_dir)) == 1


@pytest.mark.parametrize("cv_id", ["999", "abc", "-1"])
def test_pick_unknown_cv_is_400_and_unchanged(env, cv_id):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    assert client.post(f"/applications/{app_id}/cv", data={"cv_id": cv_id}).status_code == 400
    assert attached(conn, app_id) is None


def test_pick_for_unknown_application_is_404(env):
    client, conn, _ = env
    make_app(conn, "a")
    upload(client, 1, "a.pdf", PDF)
    assert client.post("/applications/999/cv", data={"cv_id": "1"}).status_code == 404


# --- application page ---------------------------------------------------------------------------------

def test_page_shows_attached_cv_with_download_and_picker(env):
    client, conn, _ = env
    one, two = make_app(conn, "a"), make_app(conn, "b")
    upload(client, one, "Alice CV.pdf", PDF)
    upload(client, two, "Other.docx", DOCX)
    cv_one = cv_rows(conn)[0]["id"]
    html = client.get(f"/applications/{one}").text
    assert f'href="/cvs/{cv_one}/view"' in html and "Alice CV.pdf" in html
    form = re.search(r'<form[^>]*action="/applications/%d/cv"[^>]*enctype="multipart/form-data".*?</form>' % one,
                     html, re.S).group(0)
    assert 'type="file"' in form and 'name="file"' in form and 'accept=".pdf,.docx"' in form
    assert 'name="cv_id"' in form and "Other.docx" in form


def test_page_without_cv_says_so_and_hides_picker_when_none_exist(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    html = client.get(f"/applications/{app_id}").text
    assert "No CV attached" in html
    assert 'name="cv_id"' not in html and 'name="file"' in html


def test_cv_name_is_escaped_on_the_page(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "<img src=x onerror=alert(1)>.pdf", PDF)
    html = client.get(f"/applications/{app_id}").text
    assert "<img src=x" not in html and "&lt;img src=x" in html


# --- download ----------------------------------------------------------------------------------------------

def test_pdf_opens_in_the_browser_instead_of_downloading(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "My CV.pdf", PDF)
    r = client.get(f"/cvs/{cv_rows(conn)[0]['id']}")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'inline; filename="My CV.pdf"'
    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_docx_media_type(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "cv.docx", DOCX)
    r = client.get(f"/cvs/{cv_rows(conn)[0]['id']}")
    assert r.headers["content-type"] == DOCX_MIME
    # browsers cannot display Word files, so DOCX still downloads
    assert r.headers["content-disposition"] == 'attachment; filename="cv.docx"'


def test_download_quotes_awkward_names_safely(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, 'a"b; filename=evil.pdf', PDF)
    r = client.get(f"/cvs/{cv_rows(conn)[0]['id']}")
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("inline;")
    assert '"b;' not in disposition and "\n" not in disposition and "\r" not in disposition
    assert "filename*=utf-8''" in disposition


def test_download_non_ascii_name(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "résumé.pdf", PDF)
    r = client.get(f"/cvs/{cv_rows(conn)[0]['id']}")
    assert "r%C3%A9sum%C3%A9.pdf" in r.headers["content-disposition"]


def test_download_unknown_cv_or_missing_file_is_404(env):
    client, conn, cv_dir = env
    assert client.get("/cvs/999").status_code == 404
    app_id = make_app(conn, "a")
    upload(client, app_id, "cv.pdf", PDF)
    (cv_dir / f"{hashlib.sha256(PDF).hexdigest()}.pdf").unlink()
    assert client.get(f"/cvs/{cv_rows(conn)[0]['id']}").status_code == 404


def test_download_ignores_a_tampered_stored_path(env):
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "cv.pdf", PDF)
    with conn:
        conn.execute("UPDATE cv_files SET stored_path = '/etc/hosts'")
    assert client.get(f"/cvs/{cv_rows(conn)[0]['id']}").status_code == 404


def test_application_page_opens_pdf_in_a_new_tab_and_offers_download_for_docx(env):
    client, conn, _ = env
    pdf_app = make_app(conn, "a")
    upload(client, pdf_app, "cv.pdf", PDF)
    html = client.get(f"/applications/{pdf_app}").text
    cv_id = cv_rows(conn)[0]["id"]
    assert f'<a class="btn small" href="/cvs/{cv_id}/view" target="_blank" rel="noopener" data-cv-open>OPEN</a>' in html
    assert "DOWNLOAD" not in html

    docx_app = make_app(conn, "b")
    upload(client, docx_app, "cv.docx", DOCX)
    html = client.get(f"/applications/{docx_app}").text
    docx_id = [r["id"] for r in cv_rows(conn) if r["original_name"] == "cv.docx"][0]
    assert f'<a class="btn small" href="/cvs/{docx_id}">DOWNLOAD</a>' in html


def test_pdf_view_url_is_never_cached(env):
    # A response cached before PDFs were served inline would keep downloading.
    client, conn, _ = env
    app_id = make_app(conn, "a")
    upload(client, app_id, "cv.pdf", PDF)
    cv_id = cv_rows(conn)[0]["id"]
    for url in (f"/cvs/{cv_id}", f"/cvs/{cv_id}/view"):
        r = client.get(url)
        assert r.status_code == 200 and r.content == PDF
        assert r.headers["content-disposition"] == 'inline; filename="cv.pdf"'
        assert r.headers["cache-control"] == "no-store"
    assert client.get("/cvs/999/view").status_code == 404


def test_cv_responses_can_only_be_framed_by_the_app_itself(env):
    # the CV card previews the PDF in same-origin frames; nobody else may embed it
    client, conn, _ = env
    pdf_app, docx_app = make_app(conn, "a"), make_app(conn, "b")
    upload(client, pdf_app, "cv.pdf", PDF)
    upload(client, docx_app, "cv.docx", DOCX)
    pdf_id, docx_id = (r["id"] for r in cv_rows(conn))
    for url in (f"/cvs/{pdf_id}", f"/cvs/{pdf_id}/view", f"/cvs/{docx_id}"):
        r = client.get(url)
        assert r.headers["content-security-policy"] == "frame-ancestors 'self'"
        assert r.headers["x-frame-options"] == "SAMEORIGIN"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "no-store"
    assert client.get(f"/cvs/{pdf_id}/view").headers["content-disposition"].startswith("inline;")
    assert client.get(f"/cvs/{docx_id}").headers["content-disposition"].startswith("attachment;")
