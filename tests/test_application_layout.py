"""Application page layout: summary sidebar + timeline as the focus + compact CV card with drag and drop."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobfinder import applications as apps
from jobfinder.db import connect
from jobfinder.web import create_app
from test_board import add_note, make_app, pid
from test_web import NOW, make_cfg

STATIC = Path(apps.__file__).parent / "static"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
DOCX = b"PK\x03\x04" + b"\x00" * 64


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path)
    conn = connect(cfg.database)
    client = TestClient(create_app(cfg, clock=lambda: NOW), follow_redirects=False)
    return client, conn


def page(client, app_id):
    return client.get(f"/applications/{app_id}").text


def block(html, css_class, tag):
    """The first <tag class="... css_class ..."> element, up to its closing tag (no nesting of the same tag)."""
    return re.search(r'<%s class="[^"]*\b%s\b[^"]*"[^>]*>.*?</%s>' % (tag, css_class, tag), html, re.S).group(0)


def entry_item(html, text):
    return re.search(r'<li class="entry[^"]*"[^>]*>(?:(?!</li>).)*%s.*?</li>' % re.escape(text), html, re.S).group(0)


# --- two columns ----------------------------------------------------------------------------

def test_layout_has_timeline_main_column_and_a_sidebar_with_summary_and_cv(env):
    client, conn = env
    app_id = make_app(conn, "a")
    html = page(client, app_id)
    assert 'class="app-layout"' in html
    assert html.index('class="app-timeline') < html.index('class="app-side') < html.index("</main>")   # timeline first in the DOM
    summary, cv = block(html, "app-summary", "section"), block(html, "app-cv", "section")
    side = block(html, "app-side", "aside")
    assert summary in side and cv in side
    assert f'action="/applications/{app_id}/move"' in summary and f'action="/applications/{app_id}/outcome"' in summary
    assert f'action="/applications/{app_id}/cv"' in cv
    timeline = block(html, "app-timeline", "section")
    assert 'class="timeline"' in timeline and f'action="/applications/{app_id}/entries"' in timeline
    assert 'action="/applications/%d/cv"' % app_id not in timeline


def test_summary_holds_applied_date_and_links_and_page_uses_the_medium_width(env):
    client, conn = env
    app_id = make_app(conn, "a", url="https://jobs.example/1")
    html = page(client, app_id)
    summary = block(html, "app-summary", "section")
    job_id = conn.execute("SELECT job_id FROM applications").fetchone()[0]
    assert "2026-11-01" in summary and f'href="/jobs/{job_id}"' in summary
    assert re.search(r'<a [^>]*href="https://jobs.example/1"[^>]*>Open posting ↗</a>', summary)
    assert re.search(r'<body[^>]*class="medium"', html)


# --- timeline: upcoming, grouping, compact moves ----------------------------------------------

def test_upcoming_notes_are_highlighted_and_labelled_once_at_the_top(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-06T15:00", "SOON", phase="Technical")
    add_note(conn, app_id, "2026-11-08T09:00", "LATER", phase="Final")
    add_note(conn, app_id, "2026-11-02T10:00", "PASTNOTE", phase="Recruiter screen")
    html = page(client, app_id)
    assert html.count('class="tl-label tl-label-upcoming"') == 1
    assert html.index("tl-label-upcoming") < html.index("LATER") < html.index("SOON") < html.index("PASTNOTE")
    for text in ("SOON", "LATER"):
        assert "data-upcoming" in re.match(r'<li [^>]*>', entry_item(html, text)).group(0)
    assert "data-upcoming" not in re.match(r'<li [^>]*>', entry_item(html, "PASTNOTE")).group(0)
    assert "UPCOMING" in html[html.index("tl-label-upcoming"):html.index("LATER")]    # not only colour: the group says it


def test_no_upcoming_label_without_future_notes_and_moves_are_never_upcoming(env):
    client, conn = env
    app_id = make_app(conn, "a")
    conn.execute("UPDATE timeline_entries SET happened_at = '2026-12-01T10:00:00' WHERE kind = 'move'")
    conn.commit()
    add_note(conn, app_id, "2026-11-02T10:00", "PASTNOTE")
    html = page(client, app_id)
    assert "tl-label-upcoming" not in html and "data-upcoming" not in html


def test_entry_at_exactly_now_counts_as_upcoming_like_the_board(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-04T10:00", "RIGHTNOW")
    assert "data-upcoming" in entry_item(page(client, app_id), "RIGHTNOW")


def test_entries_are_grouped_under_day_labels(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-02T10:00", "ONE", phase="Technical")
    add_note(conn, app_id, "2026-11-02T16:00", "TWO", phase="Technical")
    add_note(conn, app_id, "2026-11-03T16:00", "THREE", phase="Technical")
    html = page(client, app_id)
    labels = re.findall(r'<li class="tl-label"[^>]*>(.*?)</li>', html, re.S)
    texts = [re.sub(r"<[^>]+>|\s+", " ", l).strip() for l in labels]
    assert texts == ["Tue Nov 3 · yesterday", "Mon Nov 2", "Sun Nov 1"]
    assert html.index("THREE") < html.index("Mon Nov 2") < html.index("TWO") < html.index("ONE")
    assert 'datetime="2026-11-02T16:00:00"' in entry_item(html, "TWO") and ">16:00<" in entry_item(html, "TWO")


def test_labels_name_the_year_only_when_it_is_not_this_one(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2025-12-31T10:00", "OLD")
    assert "Wed Dec 31 2025" in page(client, app_id)


def test_move_entries_are_compact_one_liners_without_notes_paragraph(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/move", data={"phase_id": pid(conn, "Final")})
    html = page(client, app_id)
    moves = re.findall(r'<li class="entry entry-move"[^>]*>.*?</li>', html, re.S)
    assert len(moves) == 2
    final = [m for m in moves if "Final" in m][0]
    assert "→" in final and 'class="sr-only">Moved to </span>' in final
    assert "entry-notes" not in final and "<details" not in final
    assert f"/entries/{conn.execute('SELECT id FROM timeline_entries ORDER BY id DESC').fetchone()[0]}/delete" in final


def test_note_entries_show_phase_time_notes_and_named_quiet_actions(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-02T10:00", "line one\nline two", phase="Technical")
    item = entry_item(page(client, app_id), "line one")
    assert 'class="entry-phase">Technical<' in item
    assert 'aria-label="Delete entry from 2026-11-02 10:00"' in item
    assert 'aria-label="Edit entry from 2026-11-02 10:00"' in item
    assert '<p class="entry-notes">line one\nline two</p>' in item


def test_leading_blank_lines_of_notes_are_not_displayed_but_stay_editable(env):
    client, conn = env
    app_id = make_app(conn, "a")
    add_note(conn, app_id, "2026-11-02T10:00", "\n\nbody")
    item = entry_item(page(client, app_id), "body")
    assert '<p class="entry-notes">body</p>' in item
    assert ">\n\n\nbody</textarea>" in item      # browsers drop the first newline after <textarea>


def test_empty_timeline_invites_logging_the_first_interview(env):
    client, conn = env
    app_id = make_app(conn, "a")
    html = page(client, app_id)
    assert "No notes yet" in html and "first interview" in html
    add_note(conn, app_id, "2026-11-02T10:00", "x")
    assert "No notes yet" not in page(client, app_id)


# --- composer ---------------------------------------------------------------------------------

def test_composer_is_an_expandable_inline_form_that_posts_without_js(env):
    client, conn = env
    app_id = make_app(conn, "a", phase="Technical")
    html = page(client, app_id)
    composer = block(html, "composer", "details")
    assert re.search(r"<summary[^>]*>\s*\+ ADD ENTRY\s*</summary>", composer)
    form = re.search(r'<form[^>]*action="/applications/%d/entries"[^>]*method="post"|'
                     r'<form[^>]*method="post"[^>]*action="/applications/%d/entries"' % (app_id, app_id), composer)
    assert form
    assert 'name="phase_id"' in composer and 'name="happened_at"' in composer and 'name="notes"' in composer
    assert re.search(r'<button class="btn" type="submit">ADD</button>', composer)
    assert re.search(r'<button[^>]*type="button"[^>]*data-composer-cancel[^>]*hidden', composer)   # JS reveals it
    assert re.match(r"<details[^>]*\bopen\b", composer)               # nothing logged yet: ready to type
    add_note(conn, app_id, "2026-11-02T10:00", "x")
    assert not re.match(r"<details[^>]*\bopen\b", block(page(client, app_id), "composer", "details"))


def test_composer_defaults_to_current_phase_and_now(env):
    client, conn = env
    app_id = make_app(conn, "a", phase="Technical")
    composer = block(page(client, app_id), "composer", "details")
    assert f'<option value="{pid(conn, "Technical")}" selected>' in composer
    assert composer.count(" selected>") == 1
    assert 'type="datetime-local"' in composer and 'value="2026-11-04T10:00:00"' in composer
    assert 'maxlength="5000"' in composer and "data-autogrow" in composer


def test_composer_for_an_unassigned_application_still_renders_a_phase(env):
    client, conn = env
    app_id = make_app(conn, "a", phase=None)
    composer = block(page(client, app_id), "composer", "details")
    assert re.search(r'<select name="phase_id"[^>]*>\s*<option value="\d+"', composer)


# --- CV card ----------------------------------------------------------------------------------

def test_cv_card_without_cv_is_a_compact_drop_zone_with_a_plain_form(env):
    client, conn = env
    app_id = make_app(conn, "a")
    html = page(client, app_id)
    cv = block(html, "app-cv", "section")
    assert "No CV attached" in cv
    zone = block(cv, "dropzone", "div")
    assert "data-dropzone" in zone and "Drop your CV here" in zone and "PDF or DOCX" in zone and "10 MB" in zone
    form = re.search(r'<form[^>]*data-cv-form[^>]*>.*?</form>', cv, re.S).group(0)
    assert 'action="/applications/%d/cv"' % app_id in form and 'enctype="multipart/form-data"' in form
    assert 'method="post"' in form
    assert re.search(r'<input type="file" name="file" accept="\.pdf,\.docx"[^>]*>', form)
    assert re.search(r'<button class="btn" type="submit"[^>]*>ATTACH</button>', form)       # no-JS fallback
    assert 'role="status"' in cv and "data-cv-status" in cv


def test_page_has_a_full_page_drop_overlay_hidden_by_default(env):
    client, conn = env
    html = page(client, make_app(conn, "a"))
    assert re.search(r'<div[^>]*id="drop-overlay"[^>]*\bhidden\b[^>]*aria-hidden="true"|'
                     r'<div[^>]*id="drop-overlay"[^>]*aria-hidden="true"[^>]*\bhidden\b', html)
    assert "Drop to attach CV" in html


def test_previous_cvs_are_collapsed_under_a_toggle_and_only_when_some_exist(env):
    client, conn = env
    one, two = make_app(conn, "a"), make_app(conn, "b")
    assert "use one you uploaded" not in page(client, one)
    client.post(f"/applications/{two}/cv", files={"file": ("Alice.pdf", PDF, "application/pdf")})
    cv = block(page(client, one), "app-cv", "section")
    details = re.search(r'<details class="cv-pick"[^>]*>.*?</details>', cv, re.S).group(0)
    assert "use one you uploaded" in details and 'name="cv_id"' in details and "Alice.pdf" in details
    assert not re.match(r"<details[^>]*\bopen\b", details)


def test_attached_pdf_shows_name_open_button_and_a_quiet_replace_that_still_works_without_js(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/cv", files={"file": ("Alice CV.pdf", PDF, "application/pdf")})
    cv_id = conn.execute("SELECT cv_id FROM applications").fetchone()[0]
    cv = block(page(client, app_id), "app-cv", "section")
    assert f'href="/cvs/{cv_id}/view"' in cv and "Alice CV.pdf" in cv and ">OPEN</a>" in cv
    assert "No CV attached" not in cv
    replace = re.search(r'<details class="cv-replace"[^>]*>.*</details>', cv, re.S).group(0)
    assert ">Replace</summary>" in replace
    assert 'data-cv-form' in replace and 'name="file"' in replace and re.search(r'type="submit"[^>]*>ATTACH<', replace)
    assert "data-dropzone" in replace


def test_attached_docx_offers_download(env):
    client, conn = env
    app_id = make_app(conn, "a")
    client.post(f"/applications/{app_id}/cv", files={"file": ("cv.docx", DOCX, "application/octet-stream")})
    cv_id = conn.execute("SELECT cv_id FROM applications").fetchone()[0]
    cv = block(page(client, app_id), "app-cv", "section")
    assert f'<a class="btn small" href="/cvs/{cv_id}">DOWNLOAD</a>' in cv


def test_server_error_still_renders_the_page_with_an_alert_the_script_can_read(env):
    client, conn = env
    app_id = make_app(conn, "a")
    r = client.post(f"/applications/{app_id}/cv", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    assert re.search(r'<p class="error"[^>]*role="alert"[^>]*>[^<]+</p>', r.text)
    r = client.post(f"/applications/{app_id}/cv", files={"file": ("big.pdf", PDF + b"0" * (10 * 1024 * 1024), "application/pdf")})
    assert r.status_code == 413 and 'role="alert"' in r.text


# --- script hooks -----------------------------------------------------------------------------

def test_script_uploads_with_fetch_prechecks_type_and_size_and_reads_server_errors():
    js = (STATIC / "app.js").read_text()
    assert "[data-cv-form]" in js and "[data-dropzone]" in js and "drop-overlay" in js
    assert "fetch(form.action" in js and "new FormData()" in js and 'append("file"' in js
    assert "10 * 1024 * 1024" in js and '".pdf"' in js and '".docx"' in js
    assert "role=\"alert\"" in js or "role='alert'" in js or "[role=alert]" in js     # server's message is shown
    assert "window.location.reload()" in js


def test_script_autosubmits_the_browse_input_and_hides_the_attach_button():
    js = (STATIC / "app.js").read_text()
    assert 'input[type="file"]' in js and '"change"' in js
    assert "data-attach" in js and "hidden = true" in js


def test_attach_button_is_marked_so_the_script_can_hide_it(env):
    client, conn = env
    html = page(client, make_app(conn, "a"))
    assert re.search(r'<button class="btn" type="submit" data-attach>ATTACH</button>', html)


def test_style_has_reduced_motion_and_narrow_layout_rules():
    css = (STATIC / "style.css").read_text()
    assert ".app-layout" in css and "position: sticky" in css
    assert "prefers-reduced-motion" in css and ".drop-overlay" in css
    assert "(hover: none)" in css                                  # actions always visible on touch


# --- CV preview thumbnail and modal viewer ---------------------------------------------------------

def attach(client, conn, name, data):
    app_id = make_app(conn, name)
    client.post(f"/applications/{app_id}/cv", files={"file": (name, data, "application/octet-stream")})
    return app_id, conn.execute("SELECT cv_id FROM applications WHERE id = ?", (app_id,)).fetchone()[0]


def test_pdf_cv_gets_a_clickable_thumbnail_with_a_non_interactive_lazy_frame(env):
    client, conn = env
    app_id, cv_id = attach(client, conn, "Alice CV.pdf", PDF)
    cv = block(page(client, app_id), "app-cv", "section")
    button = block(cv, "cv-preview", "button")
    assert 'type="button"' in button and 'aria-label="Preview Alice CV.pdf"' in button and "data-cv-open" in button
    frame = re.search(r"<iframe[^>]*>", button).group(0)
    assert f'src="/cvs/{cv_id}/view#toolbar=0&amp;navpanes=0&amp;scrollbar=0&amp;view=FitH"' in frame \
        or f'src="/cvs/{cv_id}/view#toolbar=0&navpanes=0&scrollbar=0&view=FitH"' in frame
    assert 'loading="lazy"' in frame and 'tabindex="-1"' in frame and 'aria-hidden="true"' in frame
    assert re.search(r'title="[^"]*Alice CV\.pdf', frame)
    assert "Alice CV.pdf" in button.split("<iframe")[0] + button.split("</iframe>")[1]     # fallback text under the frame


def test_docx_cv_has_no_thumbnail_and_no_modal(env):
    client, conn = env
    app_id, cv_id = attach(client, conn, "cv.docx", DOCX)
    html = page(client, app_id)
    cv = block(html, "app-cv", "section")
    assert "cv-preview" not in cv and "<iframe" not in html and "<dialog" not in html
    assert f'<a class="btn small" href="/cvs/{cv_id}">DOWNLOAD</a>' in cv


def test_no_cv_has_no_thumbnail_and_no_modal(env):
    client, conn = env
    html = page(client, make_app(conn, "a"))
    assert "cv-preview" not in html and "<dialog" not in html


def test_pdf_modal_is_a_native_dialog_with_an_lazy_frame_name_new_tab_link_and_close(env):
    client, conn = env
    app_id, cv_id = attach(client, conn, "Alice CV.pdf", PDF)
    html = page(client, app_id)
    dialog = block(html, "cv-modal", "dialog")
    assert "data-cv-modal" in dialog and "aria-labelledby" in dialog
    assert "Alice CV.pdf" in dialog
    frame = re.search(r"<iframe[^>]*>", dialog).group(0)
    assert not re.search(r'\ssrc=', frame)                                       # the full viewer loads only when it opens
    assert f'data-src="/cvs/{cv_id}/view"' in frame
    new_tab = re.search(r"<a[^>]*>[^<]*Open in new tab[^<]*</a>", dialog).group(0)
    assert f'href="/cvs/{cv_id}/view"' in new_tab and 'target="_blank"' in new_tab and "noopener" in new_tab
    assert re.search(r"<button[^>]*data-cv-close[^>]*>CLOSE</button>", dialog)
    assert html.count("<dialog") == 1


def test_open_link_and_file_name_stay_plain_new_tab_links_the_script_can_intercept(env):
    client, conn = env
    app_id, cv_id = attach(client, conn, "Alice CV.pdf", PDF)
    cv = block(page(client, app_id), "app-cv", "section")
    link = f'href="/cvs/{cv_id}/view" target="_blank" rel="noopener" data-cv-open'
    assert f'<a class="btn small" {link}>OPEN</a>' in cv
    assert re.search(r'<a class="cv-name" %s>Alice CV.pdf</a>' % re.escape(link), cv)


def test_script_opens_the_cv_in_a_dialog_loading_the_viewer_only_then():
    js = (STATIC / "app.js").read_text()
    assert "[data-cv-open]" in js and "[data-cv-modal]" in js and "[data-cv-close]" in js
    assert "showModal()" in js and ".close()" in js
    assert "preventDefault()" in js and "metaKey" in js and "ctrlKey" in js          # modified clicks keep opening a new tab
    assert 'setAttribute("src"' in js and 'removeAttribute("src")' in js              # set on open, cleared on close
    assert "event.target === dialog" in js                                            # backdrop click closes
    assert '"close"' in js and ".focus()" in js                                       # focus returns to the trigger
    assert "cv-modal-open" in js                                                      # body scroll lock


def test_style_has_the_thumbnail_and_a_dark_responsive_modal():
    css = (STATIC / "style.css").read_text()
    assert ".cv-preview" in css and "aspect-ratio" in css and "pointer-events: none" in css
    assert ".cv-modal" in css and "::backdrop" in css and "min(900px, 92vw)" in css and "90vh" in css
    assert ".cv-modal-open" in css and "overflow: hidden" in css
    modal_motion = css.split("prefers-reduced-motion: no-preference", 1)[1:]
    assert any(".cv-modal" in part for part in modal_motion)                          # animations only without reduced motion
    assert re.search(r"@media \(max-width: \d+px\)[^{]*\{[^}]*\.cv-modal", css, re.S)  # full screen on narrow screens
