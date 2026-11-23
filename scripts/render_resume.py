"""Render an approved resume.md into the one-page PDF described in profile/10-cv-structure.md
(copy it from profile.example/10-cv-structure.md if you have no profile/ yet).

Usage:
    .venv/bin/python scripts/render_resume.py <resume.md> --out <file.pdf>

Exit codes: 0 rendered; 2 the content does not fit on one page (nothing is written);
1 anything else (unreadable input, Markdown outside the supported shape, ReportLab missing).

Needs ReportLab (`pip install -e '.[resume]'`); it is not a runtime dependency of the app.

Input shape (see .pi/agents/resume-writer.md): `# Name`, then headline and contact lines,
`## Section`s holding paragraphs, `- bullets`, and `### Company — Title` roles followed by a
`*dates*` line. Inline `**bold**` and `*italic*` only. Anything else is an error naming the line.

Layout values (colors, fonts, sizes, margins) are the constants right below the imports; edit
them to restyle the PDF, and keep profile/10-cv-structure.md in sync. They are design defaults.

The PDF title and author come from the resume's `# Name` heading; nothing personal lives in this script.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- template constants (profile.example/10-cv-structure.md); change them to restyle the PDF ----------
PAGE_W, PAGE_H = 612.0, 792.0
MARGIN_X = 66.0
TEXT_W = PAGE_W - 2 * MARGIN_X          # 480
BOTTOM_MARGIN = 36.0
NAME_BASELINE = 730.0                    # ≈ 62 pt from the top edge

INK = "#1A1A1A"
TEAL = "#1A7A7A"
GRAY = "#555555"

NAME_SIZE, HEADLINE_SIZE, CONTACT_SIZE = 20, 9.5, 8.5
HEADING_SIZE, BODY_SIZE, BODY_LEADING = 9.5, 8, 10.5
BULLET_INDENT, BULLET_SIZE = 10.0, 10
RULE_WIDTH = 0.75

HEADLINE_GAP, CONTACT_GAP = 12.5, 11.0   # baseline steps below name / headline
SECTION_GAP = 16.0                       # last baseline -> next heading baseline
RULE_BELOW_HEADING, BODY_BELOW_RULE = 4.25, 10.0
ROLE_GAP = 1.0                           # extra space between two roles
LINE_DESCENT_PAD = 2.5                   # space under the last baseline counted against the margin

TITLE_SUFFIX = " — Resume"      # PDF title = "<Name> — Resume"; the author is the name itself


class ResumeError(Exception):
    """The Markdown is outside the supported shape. The message starts with `line N:`."""


# --- parser -----------------------------------------------------------------------------------
@dataclass
class Item:
    kind: str            # "paragraph" | "bullet" | "dates"
    text: str
    line: int


@dataclass
class Role:
    title: str
    line: int
    items: list[Item] = field(default_factory=list)


@dataclass
class Section:
    title: str
    line: int
    items: list[Item] = field(default_factory=list)      # paragraphs and bullets outside roles
    roles: list[Role] = field(default_factory=list)


@dataclass
class Resume:
    name: str
    headline: str
    contact: str
    sections: list[Section] = field(default_factory=list)


_UNSUPPORTED = [
    (re.compile(r"^#{4,}\s"), "headings deeper than ### are not supported"),
    (re.compile(r"^#\s"), "only one H1 (the name) is allowed"),
    (re.compile(r"^\d+[.)]\s"), "numbered lists are not supported (use '- ' bullets)"),
    (re.compile(r"^[*+]\s"), "use '- ' for bullets"),
    (re.compile(r"^>"), "blockquotes are not supported"),
    (re.compile(r"^\|"), "tables are not supported"),
    (re.compile(r"^(```|~~~)"), "code blocks are not supported"),
    (re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$"), "horizontal rules are not supported"),
    (re.compile(r"^\s*!\["), "images are not supported"),
    (re.compile(r"^<"), "HTML is not supported"),
    (re.compile(r"^\s+\S"), "indented lines are not supported"),
]
_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")


def _check_text(text: str, lineno: int) -> None:
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as e:
        ch = text[e.start]
        raise ResumeError(f"line {lineno}: character {ch!r} (U+{ord(ch):04X}) is not in the WinAnsi "
                          "set of the Helvetica template; replace it") from None
    if _LINK.search(text):
        raise ResumeError(f"line {lineno}: Markdown links are not supported; write the URL as plain text")
    if "`" in text:
        raise ResumeError(f"line {lineno}: inline code is not supported")
    inline_markup(text, lineno)       # validates ** and * balance


def inline_markup(text: str, lineno: int) -> str:
    """Escape for ReportLab Paragraph markup, mapping **bold** and *italic* to <b>/<i>."""
    out: list[str] = []
    bold = italic = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("**", i):
            bold = not bold
            out.append("<b>" if bold else "</b>")
            i += 2
        elif ch == "*":
            prev = text[i - 1] if i else " "
            nxt = text[i + 1] if i + 1 < n else " "
            if prev.isspace() and nxt.isspace():     # a lone "*" between spaces is literal
                out.append("*")
            else:
                italic = not italic
                out.append("<i>" if italic else "</i>")
            i += 1
        else:
            out.append({"&": "&amp;", "<": "&lt;", ">": "&gt;"}.get(ch, ch))
            i += 1
    if bold or italic:
        raise ResumeError(f"line {lineno}: unbalanced {'**' if bold else '*'} emphasis marker")
    return "".join(out)


def parse_resume(text: str) -> Resume:
    lines = text.splitlines()
    content = [(n, ln.rstrip()) for n, ln in enumerate(lines, 1) if ln.strip()]
    if not content or not content[0][1].startswith("# "):
        raise ResumeError(f"line {content[0][0] if content else 1}: the file must start with an H1 "
                          "(# Full Name)")
    name_no, name_line = content[0]
    name = name_line[2:].strip()
    _check_text(name, name_no)
    if len(content) < 3:
        raise ResumeError(f"line {content[-1][0]}: expected a headline line and a contact line "
                          "after the name (contact line missing)")
    head_no, headline = content[1]
    contact_no, contact = content[2]
    for no, ln in ((head_no, headline), (contact_no, contact)):
        if ln.startswith("#") or ln.startswith("- "):
            raise ResumeError(f"line {no}: expected the headline then the contact line right after the name")
        _check_text(ln, no)
    resume = Resume(name, headline.strip(), contact.strip())

    section: Section | None = None
    role: Role | None = None
    pending: list[tuple[int, str]] = []     # paragraph lines awaiting a blank/structure break

    def flush() -> None:
        if pending:
            target = role.items if role else section.items  # type: ignore[union-attr]
            target.append(Item("paragraph", " ".join(t.strip() for _, t in pending), pending[0][0]))
            pending.clear()

    for no, ln in content[3:]:
        # blank lines separate paragraphs; they were dropped above, so detect gaps via line numbers
        if pending and no != pending[-1][0] + 1:
            flush()
        if ln.startswith("## "):
            flush()
            title = ln[3:].strip()
            if not title:
                raise ResumeError(f"line {no}: empty section heading")
            _check_text(title, no)
            section, role = Section(title, no), None
            resume.sections.append(section)
            continue
        if ln.startswith("### "):
            flush()
            if section is None:
                raise ResumeError(f"line {no}: role heading before any ## section")
            title = ln[4:].strip()
            _check_text(title, no)
            role = Role(title, no)
            section.roles.append(role)
            continue
        for pat, why in _UNSUPPORTED:
            if pat.match(ln):
                raise ResumeError(f"line {no}: {why}: {ln.strip()!r}")
        if section is None:
            raise ResumeError(f"line {no}: text outside any section (expected a ## heading): {ln.strip()!r}")
        if ln.startswith("# "):
            raise ResumeError(f"line {no}: only one H1 (the name) is allowed")
        if ln.startswith("- "):
            flush()
            body = ln[2:].strip()
            _check_text(body, no)
            (role.items if role else section.items).append(Item("bullet", body, no))
            continue
        if ln.startswith("#"):
            raise ResumeError(f"line {no}: unsupported heading: {ln.strip()!r}")
        _check_text(ln, no)
        if role and not role.items and not pending and re.fullmatch(r"\*[^*]+\*", ln.strip()):
            role.items.append(Item("dates", ln.strip()[1:-1], no))
            continue
        pending.append((no, ln))
    flush()
    if not resume.sections:
        raise ResumeError(f"line {content[-1][0]}: no ## sections found")
    return resume


# --- layout -----------------------------------------------------------------------------------
@dataclass
class Placed:
    """One thing to draw. `baseline` is the first text baseline (y up)."""
    kind: str                 # "text" | "heading" | "bullet" | "rule"
    baseline: float
    para: object = None       # reportlab Paragraph (already wrapped)
    x: float = MARGIN_X
    label: str = ""           # heading text / header string
    style: str = ""


@dataclass
class Block:
    name: str
    lines: int = 0


@dataclass
class Layout:
    placed: list[Placed]
    last_baseline: float
    blocks: list[Block]

    @property
    def overflow(self) -> float:
        return max(0.0, BOTTOM_MARGIN - (self.last_baseline - LINE_DESCENT_PAD))


def _styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    def style(name, font, size, leading, color=INK, align=TA_LEFT):
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading,
                              textColor=HexColor(color), alignment=align, spaceBefore=0, spaceAfter=0)

    return {
        "headline": style("headline", "Helvetica", HEADLINE_SIZE, HEADLINE_GAP, align=TA_CENTER),
        "contact": style("contact", "Helvetica", CONTACT_SIZE, CONTACT_GAP, align=TA_CENTER),
        "body": style("body", "Helvetica", BODY_SIZE, BODY_LEADING),
        "role": style("role", "Helvetica-Bold", BODY_SIZE, BODY_LEADING),
        "dates": style("dates", "Helvetica", BODY_SIZE, BODY_LEADING, color=GRAY),
    }


def _para(text: str, st, width: float):
    from reportlab.platypus import Paragraph
    p = Paragraph(text, st)
    p.wrap(width, 10_000)
    return p


def _lines(p) -> int:
    return len(p.blPara.lines)


def _first_baseline_offset(p) -> float:
    """Distance from the paragraph's bottom edge up to its first baseline (ReportLab: height - font size)."""
    return p.height - p.style.fontSize


def build_layout(doc: Resume) -> Layout:
    st = _styles()
    placed: list[Placed] = []
    blocks: list[Block] = []

    # header: name / headline / contact, centered
    placed.append(Placed("name", NAME_BASELINE, label=doc.name))
    base = NAME_BASELINE - HEADLINE_GAP
    for key, text in (("headline", doc.headline), ("contact", doc.contact)):
        p = _para(inline_markup(text, 0), st[key], TEXT_W)
        placed.append(Placed("text", base, p))
        base -= (_lines(p) - 1) * st[key].leading
        if key == "headline":
            base -= CONTACT_GAP
    last = base

    def add_para(text: str, style, baseline: float, x: float = MARGIN_X, width: float = TEXT_W):
        p = _para(text, style, width)
        placed.append(Placed("text", baseline, p, x))
        return p, baseline - (_lines(p) - 1) * style.leading

    for sec in doc.sections:
        block = Block(sec.title)
        if sec.items or not sec.roles:
            blocks.append(block)
        heading = last - SECTION_GAP
        placed.append(Placed("heading", heading, label=sec.title))
        placed.append(Placed("rule", heading - RULE_BELOW_HEADING))
        # `last` is the previous baseline; the first body step (BODY_LEADING) must land
        # BODY_BELOW_RULE under the rule, so start BODY_LEADING above that.
        last = heading - RULE_BELOW_HEADING - BODY_BELOW_RULE + BODY_LEADING

        in_skills = sec.title.strip().lower() == "technical skills"

        def add_items(items, blk):
            nonlocal last
            for it in items:
                step = BODY_LEADING
                if it.kind == "dates":
                    p, last = add_para(inline_markup(it.text, it.line), st["dates"], last - step)
                elif it.kind == "bullet" and not in_skills:
                    p, new_last = add_para(inline_markup(it.text, it.line), st["body"], last - step,
                                           MARGIN_X + BULLET_INDENT, TEXT_W - BULLET_INDENT)
                    placed.append(Placed("bullet", last - step))
                    last = new_last
                else:
                    p, last = add_para(inline_markup(it.text, it.line), st["body"], last - step)
                blk.lines += _lines(p)

        add_items(sec.items, block)
        for idx, role in enumerate(sec.roles):
            rblock = Block(role.title)
            blocks.append(rblock)
            step = BODY_LEADING + (ROLE_GAP if (idx or sec.items) else 0)
            p, last = add_para(inline_markup(role.title, role.line), st["role"], last - step)
            rblock.lines += _lines(p)
            add_items(role.items, rblock)
    return Layout(placed, last, blocks)


def draw(layout: Layout, doc: Resume, stream) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(stream, pagesize=(PAGE_W, PAGE_H), pageCompression=0)
    c.setTitle(doc.name + TITLE_SUFFIX)
    c.setAuthor(doc.name)
    c.setCreator("scripts/render_resume.py")
    for it in layout.placed:
        if it.kind == "name":
            c.setFillColor(HexColor(INK))
            c.setFont("Helvetica-Bold", NAME_SIZE)
            c.drawCentredString(PAGE_W / 2, it.baseline, it.label)
        elif it.kind == "heading":
            c.setFillColor(HexColor(TEAL))
            c.setFont("Helvetica-Bold", HEADING_SIZE)
            c.drawString(MARGIN_X, it.baseline, it.label)
        elif it.kind == "rule":
            c.setStrokeColor(HexColor(TEAL))
            c.setLineWidth(RULE_WIDTH)
            c.setLineCap(1)
            c.line(MARGIN_X, it.baseline, MARGIN_X + TEXT_W, it.baseline)
        elif it.kind == "bullet":
            c.setFillColor(HexColor(INK))
            c.setFont("Helvetica", BULLET_SIZE)
            c.drawString(MARGIN_X, it.baseline, "•")
        else:
            p = it.para
            p.drawOn(c, it.x, it.baseline - _first_baseline_offset(p))
    c.showPage()
    c.save()


# --- CLI --------------------------------------------------------------------------------------
class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"render_resume.py: error: {message}", file=sys.stderr)
        raise SystemExit(1)       # 2 is reserved for "does not fit on one page"


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(description="Render resume.md into the one-page PDF template.")
    ap.add_argument("resume", type=Path, help="approved resume.md")
    ap.add_argument("--out", type=Path, required=True, help="PDF to write")
    args = ap.parse_args(argv)

    try:
        text = args.resume.read_text(encoding="utf-8")
    except OSError as e:
        print(f"cannot read {args.resume}: {e.strerror or e}", file=sys.stderr)
        return 1
    try:
        import reportlab  # noqa: F401
    except ImportError:
        print("ReportLab is not installed: .venv/bin/pip install -e '.[resume]'", file=sys.stderr)
        return 1
    try:
        doc = parse_resume(text)
        layout = build_layout(doc)
    except ResumeError as e:
        print(f"{args.resume}: {e}", file=sys.stderr)
        args.out.unlink(missing_ok=True)
        return 1

    if layout.overflow > 0:
        longest = max(layout.blocks, key=lambda b: b.lines)
        print(f"does not fit on one page: overflow ≈ {layout.overflow:.0f} pt; "
              f"longest block: {longest.name} ({longest.lines} lines)", file=sys.stderr)
        args.out.unlink(missing_ok=True)
        return 2

    buf = io.BytesIO()
    draw(layout, doc, buf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(buf.getvalue())
    print(f"wrote {args.out} (1 page)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
