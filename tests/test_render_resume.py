"""scripts/render_resume.py: an approved resume.md becomes the one-page ATS PDF (profile.example/10-cv-structure.md)."""
import importlib.util
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

pytest.importorskip("reportlab")

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "render_resume.py"
spec = importlib.util.spec_from_file_location("render_resume", SCRIPT)
rr = importlib.util.module_from_spec(spec)
sys.modules["render_resume"] = rr  # @dataclass resolves its module by name
spec.loader.exec_module(rr)

RESUME = """\
# Alex Martínez Example
Machine Learning Engineer · Retail Analytics · Python & SQL
alex@example.com | linkedin.com/in/alex-example

## Professional Summary
Machine Learning Engineer with 4+ years of experience turning operational data into forecasts and decision tools for logistics and healthcare teams. Currently at Acme Logistics, a fictional carrier, where I built the demand forecasting pipeline used by regional planners — feature engineering, model evaluation, and scheduled retraining on a managed warehouse — reducing manual planning spreadsheets across twelve distribution centers. Earlier work at Globex Health covered reporting and statistical analysis in R and Python, including a patient-wait-time dashboard for clinic managers with weekly refreshes and clear data-quality checks. Comfortable explaining model behavior to non-technical stakeholders and documenting assumptions, limits, and monitoring plans. Enjoys clear written communication, pairing with domain experts, and keeping analyses reproducible so that colleagues can rerun them months later without guesswork.

## Technical Skills
- **Programming:** Python (4+ years), SQL, R, Scala
- **Machine Learning:** scikit-learn, XGBoost, PyTorch, time-series forecasting, model evaluation, A/B analysis
- **Data Tools:** pandas, Spark, dbt, Airflow, Jupyter, Tableau
- **Databases:** BigQuery, PostgreSQL, SQLite
- **Platforms:** AWS (S3, Lambda, SageMaker), Docker, GitHub Actions
- **Visualization:** Tableau, matplotlib, Plotly, Streamlit
- **Quality:** pytest, data validation checks, experiment tracking with MLflow

## Professional Experience
### Acme Logistics — Machine Learning Engineer
*February 2021 – Present*
- Built a demand forecasting pipeline for twelve distribution centers, from data preparation through evaluation and scheduled retraining, replacing manual planning spreadsheets with weekly forecasts reviewed by regional planners.
- Created data-quality checks and drift reports for model inputs, and documented assumptions and limits so planners knew when to trust or override a forecast.
- Moved feature pipelines from ad-hoc notebooks into scheduled Airflow jobs with tests, versioned datasets, and a short runbook that another engineer could follow when a load failed.
- Mentored two interns on exploratory analysis and code review practices during a summer program, sharing a reusable project template and a checklist for reproducible experiments.
- Ran offline comparisons of three candidate models and presented trade-offs between accuracy, cost, and explainability to the operations team.
### Globex Health — Data Analyst
*August 2018 – January 2021*
- Built a patient-wait-time dashboard in Tableau for clinic managers, with weekly refreshes from a PostgreSQL reporting database and agreed metric definitions.
- Wrote R and Python scripts for statistical summaries and survey analysis, automating monthly reports that had been assembled by hand.
- Worked with nurses and administrators to clarify questions, validate results, and turn findings into short written recommendations.
- Migrated a collection of spreadsheet macros into versioned SQL views, so clinic managers and analysts read the same numbers and changes to definitions were reviewed before release.
- Presented quarterly findings to the leadership team, translating statistical results into plain language, caveats, and suggested next steps for scheduling and staffing.

## Education
**Example University** | B.Sc. in Statistics — *September 2014 – June 2018*

## Languages
**Portuguese:** Native · **English:** Advanced (C1) — clear technical communication, reading, and writing across engineering contexts
"""

HEADINGS = ["Professional Summary", "Technical Skills", "Professional Experience", "Education", "Languages"]


def run(md_path, out, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), str(md_path), "--out", str(out), *extra],
                          capture_output=True, text=True)


@pytest.fixture()
def rendered(tmp_path):
    md = tmp_path / "resume.md"
    md.write_text(RESUME, encoding="utf-8")
    out = tmp_path / "Resume_Test.pdf"
    proc = run(md, out)
    assert proc.returncode == 0, proc.stderr
    return out


def page_count(pdf: Path) -> int:
    return max(int(n) for n in re.findall(rb"/Count (\d+)", pdf.read_bytes()))


def content_stream(pdf: Path) -> str:
    """All page content, decompressed when the PDF compresses its streams."""
    data = pdf.read_bytes()
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        out.append(raw.decode("latin-1"))
    return "\n".join(out)


# --- the PDF itself ---------------------------------------------------------------------------

def test_renders_one_letter_page(rendered):
    assert rendered.read_bytes().startswith(b"%PDF")
    assert page_count(rendered) == 1
    assert re.search(rb"/MediaBox \[ 0 0 612 792 \]", rendered.read_bytes())


def test_metadata(rendered):
    if shutil.which("pdfinfo"):
        info = subprocess.run(["pdfinfo", str(rendered)], capture_output=True, text=True).stdout
        assert re.search(r"Title:\s+Alex Martínez Example — Resume", info)
        assert re.search(r"Author:\s+Alex Martínez Example", info)
        assert re.search(r"Pages:\s+1\b", info) and "letter" in info
    data = rendered.read_bytes()
    assert b"/Title (Alex Mart\\355nez Example \\204 Resume)" in data
    assert b"/Author (Alex Mart\\355nez Example)" in data


def test_title_and_author_come_from_the_resume_h1(tmp_path):
    md = tmp_path / "r.md"
    md.write_text(RESUME.replace("# Alex Martínez Example", "# Jordan Q. Sample"), encoding="utf-8")
    out = tmp_path / "o.pdf"
    assert run(md, out).returncode == 0
    data = out.read_bytes()
    assert b"/Title (Jordan Q. Sample \\204 Resume)" in data
    assert b"/Author (Jordan Q. Sample)" in data
    assert b"Alex" not in data


def test_fonts_sizes_and_colors_follow_the_template(rendered):
    cs = content_stream(rendered)
    pdf = rendered.read_bytes().decode("latin-1")
    # font resource name -> base font
    resources = {}
    for m in re.finditer(r"<<[^<>]*?/BaseFont /([\w-]+)[^<>]*?/Name /(F\d+)[^<>]*?>>", pdf, re.S):
        resources[m.group(2)] = m.group(1)
    for m in re.finditer(r"<<[^<>]*?/Name /(F\d+)[^<>]*?/BaseFont /([\w-]+)[^<>]*?>>", pdf, re.S):
        resources[m.group(1)] = m.group(2)
    assert set(resources.values()) >= {"Helvetica", "Helvetica-Bold"}
    assert "WinAnsiEncoding" in pdf

    def uses(font, size):
        return any(f"/{res} {size} Tf" in cs for res, base_name in resources.items() if base_name == font)

    assert uses("Helvetica-Bold", 20)        # name
    assert uses("Helvetica", 9.5)            # headline
    assert uses("Helvetica", 8.5)            # contact
    assert uses("Helvetica-Bold", 9.5)       # section headings
    assert uses("Helvetica-Bold", 8)         # role lines, skill labels
    assert uses("Helvetica", 8)              # body
    assert ".101961 .478431 .478431 rg" in cs   # teal #1A7A7A headings
    assert ".101961 .478431 .478431 RG" in cs   # teal rule
    assert ".75 w" in cs                        # 0.75 pt rule
    assert ".333333 .333333 .333333 rg" in cs   # gray #555555 dates
    assert ".101961 .101961 .101961 rg" in cs   # near-black #1A1A1A text
    # the rule spans the 480 pt text width starting at the 66 pt margin
    assert re.search(r"n 66 [\d.]+ m 546 [\d.]+ l S", cs)


def test_italic_uses_oblique(tmp_path):
    md = tmp_path / "r.md"
    md.write_text(RESUME.replace("## Languages", "## Languages\nSome *slanted* word.\n"), encoding="utf-8")
    out = tmp_path / "o.pdf"
    assert run(md, out).returncode == 0
    assert b"Helvetica-Oblique" in out.read_bytes()


def test_margins_and_bottom_clearance(rendered):
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not available")
    xml = subprocess.run(["pdftotext", "-bbox", str(rendered), "-"], capture_output=True, text=True).stdout
    boxes = [(float(a), float(b), float(c), float(d)) for a, b, c, d in
             re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)"', xml)]
    assert min(b[0] for b in boxes) >= 66 - 0.01
    assert max(b[2] for b in boxes) <= 546 + 0.5
    assert min(b[1] for b in boxes) >= 35          # top ≈ 40 pt
    assert max(b[3] for b in boxes) <= 792 - 36    # bottom ≥ 36 pt


def test_text_extracts_in_reading_order(rendered):
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not available")
    text = subprocess.run(["pdftotext", str(rendered), "-"], capture_output=True, text=True).stdout
    assert "Alex Martínez Example" in text
    assert "Python & SQL" in text and "–" in text and "—" in text and "·" in text and "•" in text
    pos = [text.index(h) for h in HEADINGS]
    assert pos == sorted(pos)
    assert text.index("Acme Logistics — Machine Learning Engineer") < text.index("Globex Health — Data Analyst")
    assert "February 2021 – Present" in text


# --- overflow ---------------------------------------------------------------------------------

def test_overflow_exits_2_and_leaves_no_file(tmp_path):
    long_summary = " ".join(["Built and evaluated forecasting models for planners."] * 120)
    md = tmp_path / "resume.md"
    md.write_text(RESUME.replace(RESUME.split("## Professional Summary\n")[1].split("\n")[0], long_summary), encoding="utf-8")
    out = tmp_path / "o.pdf"
    proc = run(md, out)
    assert proc.returncode == 2
    assert re.search(r"does not fit on one page: overflow ≈ \d+ pt; longest block: Professional Summary \(\d+ lines\)", proc.stderr)
    assert not out.exists()


def test_overflow_removes_a_stale_output(tmp_path):
    md = tmp_path / "resume.md"
    md.write_text(RESUME.replace("Machine Learning Engineer with", "word " * 2000 + "Machine Learning Engineer with", 1), encoding="utf-8")
    out = tmp_path / "o.pdf"
    out.write_bytes(b"old")
    assert run(md, out).returncode == 2
    assert not out.exists()


def test_longest_block_can_be_a_role(tmp_path):
    bullets = "\n".join(f"- {'Shipped things. ' * 30}" for _ in range(12))
    md = tmp_path / "resume.md"
    md.write_text(RESUME.replace("### Globex Health — Data Analyst\n", "### Globex Health — Data Analyst\n*Aug 2018 – Jan 2021*\n" + bullets + "\n### X — Y\n"), encoding="utf-8")
    proc = run(md, tmp_path / "o.pdf")
    assert proc.returncode == 2
    assert "longest block: Globex Health — Data Analyst (" in proc.stderr


# --- parser: strict, line-numbered errors -----------------------------------------------------

def parse(text):
    return rr.parse_resume(text)


def test_parser_reads_the_markdown_shape():
    doc = parse(RESUME)
    assert doc.name == "Alex Martínez Example"
    assert doc.headline.startswith("Machine Learning Engineer")
    assert doc.contact.startswith("alex@example.com")
    assert [s.title for s in doc.sections] == HEADINGS


@pytest.mark.parametrize("bad, lineno, needle", [
    (RESUME.replace("## Education", "#### Education"), None, "#### Education"),
    (RESUME.replace("- **Programming:**", "1. **Programming:**"), None, "1. **Programming:**"),
    (RESUME.replace("## Education", "| a | b |\n## Education"), None, "| a | b |"),
    (RESUME.replace("## Languages", "![img](x.png)\n## Languages"), None, "![img](x.png)"),
    (RESUME.replace("R, Scala\n", "R, Scala **unclosed\n", 1), None, "unbalanced"),
    (RESUME.replace("## Education", "```\ncode\n```\n## Education"), None, "```"),
    (RESUME.replace("Example University", "University → Example"), None, "→"),
    ("no heading here\n", 1, "H1"),
    ("# Name\nonly a headline\n", None, "contact"),
    ("# Name\nheadline\ncontact\nstray text before any section\n", 4, "stray"),
])
def test_parser_rejects_unknown_constructs_naming_the_line(bad, lineno, needle):
    with pytest.raises(rr.ResumeError) as e:
        parse(bad)
    msg = str(e.value)
    assert needle in msg or needle.lower() in msg.lower()
    m = re.match(r"line (\d+):", msg)
    assert m, msg
    if lineno is None:
        assert bad.splitlines()[int(m.group(1)) - 1].strip() != ""
    else:
        assert int(m.group(1)) == lineno


def test_cli_reports_malformed_markdown_with_line_number(tmp_path):
    md = tmp_path / "resume.md"
    md.write_text(RESUME.replace("## Education", "> quoted\n## Education"), encoding="utf-8")
    out = tmp_path / "o.pdf"
    proc = run(md, out)
    assert proc.returncode == 1
    line = RESUME.replace("## Education", "> quoted\n## Education").splitlines().index("> quoted") + 1
    assert f"line {line}:" in proc.stderr
    assert not out.exists()


def test_cli_missing_input_is_a_clear_error(tmp_path):
    proc = run(tmp_path / "nope.md", tmp_path / "o.pdf")
    assert proc.returncode == 1 and "nope.md" in proc.stderr


# --- inline markup ----------------------------------------------------------------------------

def test_inline_escapes_xml_and_maps_bold_italic():
    assert rr.inline_markup("A & B <c> **bold** and *it*", 1) == "A &amp; B &lt;c&gt; <b>bold</b> and <i>it</i>"
    assert rr.inline_markup("5 * 3", 1) == "5 * 3"
    with pytest.raises(rr.ResumeError):
        rr.inline_markup("*oops", 7)


def test_special_characters_render(tmp_path):
    md = tmp_path / "r.md"
    md.write_text(RESUME.replace("## Languages", "## Languages\nR&D <b>raw</b> & C++  ok\n"), encoding="utf-8")
    out = tmp_path / "o.pdf"
    assert run(md, out).returncode == 0
    if shutil.which("pdftotext"):
        text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True).stdout
        assert "R&D <b>raw</b> & C++" in text
