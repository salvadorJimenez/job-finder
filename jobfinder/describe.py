"""Tidy a scraped job description into readable Markdown.

JobSpy hands over "Markdown-ish" text: escaped punctuation, bullets drawn with glyphs, lines of
spaces, bold used as headings, one item per line with blank lines between. `normalize_description`
rewrites that into plain Markdown *before* the safe renderer runs. It is a pure text-to-text step:
it never drops a word, never touches fenced code, and never interprets HTML (the renderer still
escapes it). It only runs when rendering or exporting; the stored description is not changed.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

BULLET_GLYPHS = "•·▪◦■●‣⁃∙"
SHORT_LINE = 120            # a "plain item" line is shorter than this ...
SHORT_WORDS = 16            # ... and has at most this many words
HEADING_MAX = 60            # ALL CAPS and "ends with a colon" headings
BOLD_HEADING_MAX = 100
META_VALUE_MAX = 100

# Top-of-description "Key: value" lines worth showing as a compact meta list.
META_KEYS = {
    "ubicacion", "ubicacion del puesto", "localizacion", "location", "lugar de trabajo",
    "categoria", "category", "id de requisicion", "requisition id", "req id", "job id", "id de vacante",
    "salary", "salario", "sueldo", "sueldo mensual", "pay", "compensation",
    "tipo de puesto", "tipo de empleo", "job type", "employment type", "modalidad", "work model",
}

_STOPWORDS = {
    "de", "del", "la", "las", "el", "los", "y", "e", "o", "u", "en", "con", "para", "por", "a", "al", "un", "una",
    "of", "and", "the", "for", "to", "in", "with", "or", "on", "at", "by", "an",
}

_SAFE_ESCAPE = re.compile(r"\\([-+.#()$!,:;%@/=?~|'\"])")
_LINE_START_ESCAPE = re.compile(r"^(\s*)\\([*_])")
_INTRAWORD_UNDERSCORE = re.compile(r"(?<=\w)\\_(?=\w)")
_ESCAPED_RULE = re.compile(r"^\s*(?:\\[-*_]\s*){3,}$")
_FENCE = re.compile(r"^\s*(```|~~~)")

_LIST = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(\S.*)$")
_BULLET_ITEM = re.compile(r"^\s*[-*+]\s+\S")
_ORDERED_ITEM = re.compile(r"^\s*\d{1,3}\.\s+\S")
_ATX = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^(?:\*{3,}|-{3,}|_{3,}|(?:\*\s+){2,}\*|(?:-\s+){2,}-)$")
_SETEXT = re.compile(r"^(?:-{3,}|={3,})$")
_BOLD_ONLY = re.compile(r"^(?:\*\*|__)\s*([^*_][^*]*?)\s*(?:\*\*|__)\s*:?$")
_GLYPH_ONLY = re.compile(rf"^\s*[{BULLET_GLYPHS}]\s*$")
_GLYPH_ITEM = re.compile(rf"^(\s*)[{BULLET_GLYPHS}]\s*(\S.*)$")
_DASH_ITEM = re.compile(r"^(\s*)[–—]\s+(\S.*)$")
_O_ITEM = re.compile(r"^(\s*)o\s+([A-ZÁÉÍÓÚÑ0-9¿¡].*)$")
_PAREN_NUMBER = re.compile(r"^(\s*)(\d{1,3})\)\s+(\S.*)$")
_PLUS_ITEM = re.compile(r"^(\s*)\+\s+(\S.*)$")
_COLON_HEADING = re.compile(r"^[^.!?;:]{2,%d}:$" % (HEADING_MAX - 1))
_META_LINE = re.compile(
    r"^(?:[-*+]\s+)?(?:\*\*)?(?P<key>[^:*\n]{2,40}?)\s*(?::\s*\*\*|\*\*\s*:|:)\s*(?P<value>\S.*)$")


@dataclass
class Description:
    """A description split into the compact meta lines found at its top and the Markdown body."""
    meta: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""

    def markdown(self) -> str:
        parts = []
        if self.meta:
            parts.append("\n".join(f"- **{key}:** {value}" for key, value in self.meta))
        if self.body:
            parts.append(self.body)
        return "\n\n".join(parts)


def normalize_description(text: str | None) -> str:
    """Readable Markdown for a scraped description (meta lines included, as a list)."""
    return parse_description(text).markdown()


def parse_description(text: str | None) -> Description:
    pieces: list[list[str]] = []
    meta: list[tuple[str, str]] = []
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, (is_code, chunk) in enumerate(_split_fences(raw)):
        if is_code:
            pieces.append(chunk)
            continue
        chunk = _bullets(_clean(chunk))
        chunk = _join_continuations(chunk)
        chunk = _setext(chunk)
        if index == 0:
            meta, chunk = _take_meta(chunk)
        chunk = _headings(chunk)
        chunk = _group_short_runs(chunk)
        pieces.append(_tidy(chunk))
    return Description(meta, "\n\n".join("\n".join(p) for p in pieces if p))


# --- 1. cleaning --------------------------------------------------------------------------

def _clean(raw_lines: list[str]) -> list[str]:
    """Lines with trailing whitespace gone, whitespace-only lines blank and scraper escapes undone."""
    lines = []
    for raw in raw_lines:
        line = raw.rstrip()
        if not line.strip():
            lines.append("")
            continue
        if _ESCAPED_RULE.match(line):
            lines.append("***")      # a rule; "---" would turn the line above it into a heading
            continue
        line = _SAFE_ESCAPE.sub(r"\1", line)
        line = _LINE_START_ESCAPE.sub(r"\1\2", line)
        line = _INTRAWORD_UNDERSCORE.sub("_", line)
        lines.append(line)
    return lines


def _split_fences(lines: list[str]) -> list[tuple[bool, list[str]]]:
    """[(is_code, lines)]: fenced code is passed through untouched."""
    segments: list[tuple[bool, list[str]]] = []
    current: list[str] = []
    fence = None
    for line in lines:
        match = _FENCE.match(line)
        if fence is None and match:
            segments.append((False, current))
            current, fence = [line], match.group(1)
        elif fence is not None:
            current.append(line)
            if match and match.group(1) == fence:
                segments.append((True, current))
                current, fence = [], None
        else:
            current.append(line)
    segments.append((fence is not None, current))     # an unclosed fence runs to the end
    return [(code, chunk) for code, chunk in segments if chunk or not code] or [(False, [])]


# --- 2. bullets and numbers -----------------------------------------------------------------

def _is_item(line: str) -> bool:
    return bool(_LIST.match(line))


def _bullets(lines: list[str]) -> list[str]:
    """Glyph, dash, "o" and "1)" markers become Markdown list markers."""
    out: list[str] = []
    last = ""                         # last non-blank line emitted
    gap = True                        # the previous line was blank (or this is the first)
    for line in lines:
        if not line:
            out.append(line)
            gap = True
            continue
        if _GLYPH_ONLY.match(line):
            out.append("")            # a stray bullet with its text on the next line
            gap = True
            continue
        new = line
        if m := _GLYPH_ITEM.match(line):
            new = f"{'  ' if len(m.group(1)) >= 2 else ''}- {m.group(2)}"
        elif m := _PAREN_NUMBER.match(line):
            new = f"{m.group(1)}{m.group(2)}. {m.group(3)}"
        elif m := _PLUS_ITEM.match(line):
            new = f"{m.group(1)}- {m.group(2)}"
        elif m := _DASH_ITEM.match(line):
            # "– text" starts a list only after a gap, a heading or another item; inside a paragraph it is prose.
            if gap or _is_item(last) or last.startswith("#"):
                new = f"{'  ' if len(m.group(1)) >= 2 else ''}- {m.group(2)}"
        elif m := _O_ITEM.match(line):
            if _is_item(last):
                new = f"  - {m.group(2)}"                                  # "o" sub-items of a "·" bullet
            elif gap or last.startswith("#"):
                new = f"- {m.group(2)}"
        out.append(new)
        last = new
        gap = False
    return out


def _join_continuations(lines: list[str]) -> list[str]:
    """Indented lines that continue a list item (scrapers wrap inline bold onto its own line) join it."""
    out: list[str] = []
    for line in lines:
        if line and out and out[-1] and _is_item(out[-1]) and not _is_item(line) and line[0] in " \t":
            out[-1] = _glue(out[-1], line.strip())
        else:
            out.append(line)
    return out


def _glue(left: str, right: str) -> str:
    return left + ("" if right[0] in ",.;:)!?" else " ") + right


# --- 3. headings --------------------------------------------------------------------------

def _plain(text: str) -> str:
    """Heading text without emphasis markers or a trailing colon."""
    text = text.strip()
    for _ in range(2):
        m = re.fullmatch(r"(\*\*|__|\*|_)(.+?)\1\s*:?", text)
        if not m:
            break
        text = m.group(2).strip()
    return text.rstrip(":").strip()


def _letters(text: str) -> str:
    return "".join(c for c in text if c.isalpha())


def _is_caps(text: str) -> bool:
    letters = _letters(text)
    return len(letters) >= 3 and letters == letters.upper() and letters != letters.lower()


def _title_case(text: str) -> str:
    """ALL CAPS to Title Case, keeping short acronyms (PLC, SQL, IT) and small words in place."""
    words = []
    for n, word in enumerate(text.split(" ")):
        letters = _letters(word)
        if not letters:
            words.append(word)
        elif letters.lower() in _STOPWORDS and n > 0:
            words.append(word.lower())
        elif len(letters) <= 3:
            words.append(word)
        else:
            words.append(word[:1] + word[1:].lower() if word[0].isalpha() else _capitalize_first_letter(word))
    return " ".join(words)


def _capitalize_first_letter(word: str) -> str:
    for i, c in enumerate(word):
        if c.isalpha():
            return word[:i] + c.upper() + word[i + 1:].lower()
    return word


def _heading(text: str) -> str:
    text = _plain(text)
    return f"### {_title_case(text) if _is_caps(text) else text}"


def _setext(lines: list[str]) -> list[str]:
    """"Title" over "-----" (or "=====") becomes a heading; the underline is not text."""
    out: list[str] = []
    for i, line in enumerate(lines):
        if _SETEXT.match(line) and out and out[-1] and not _is_item(out[-1]) and not out[-1].startswith("#") \
                and len(out[-1]) <= SHORT_LINE and not _HR.match(out[-1]):
            out[-1] = _heading(out[-1])
            continue
        out.append(line)
    return out


def _prose(line: str) -> bool:
    return bool(line) and not _is_item(line) and not line.startswith("#") and not _HR.match(line)


def _ends_sentence(line: str) -> bool:
    return line.rstrip()[-1:] in ".!?:;"


def _continues(line: str | None) -> bool:
    """The next line reads as the rest of a sentence: it starts lower-case or with punctuation."""
    return bool(line) and (line[0] in ",.;:)!?" or line[0].islower())


def _has_content_after(lines: list[str], i: int) -> bool:
    return any(lines[i + 1:])


def _attach_colons(lines: list[str]) -> list[str]:
    """Repair a bold title that a scraper split over lines: "**Title**" + ":" is "**Title**:", and
    "**Title**" + "**of the**" (starting lower-case) is one title."""
    out: list[str] = []
    for line in lines:
        previous = _BOLD_ONLY.match(out[-1]) if out else None
        if line.strip() == ":" and previous and not out[-1].rstrip().endswith(":"):
            out[-1] = out[-1].rstrip() + ":"
        elif previous and (bold := _BOLD_ONLY.match(line)) and bold.group(1)[0].islower() \
                and not out[-1].rstrip().endswith(":"):
            out[-1] = f"**{previous.group(1).strip()} {bold.group(1).strip()}**" + (":" if line.rstrip().endswith(":") else "")
        else:
            out.append(line)
    return out


def _headings(lines: list[str]) -> list[str]:
    """ATX levels are unified, and short title-like lines become `###` headings. Bold in the
    middle of a sentence stays inline: scrapers put it on its own line."""
    out: list[str] = []
    glue = False                               # the previous line was inline bold: the next text line continues it
    lines = _attach_colons(lines)
    for i, line in enumerate(lines):
        prev = out[-1] if out else ""
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if glue and _prose(line) and not _BOLD_ONLY.match(line):
            out[-1] = _glue(out[-1], line.strip())
            glue = False
            continue
        glue = False
        if m := _ATX.match(line):
            out.append(_heading(m.group(1)) if _plain(m.group(1)) else "")
            continue
        if not line or _is_item(line) or _HR.match(line):
            out.append(line)
            continue
        at_block_start = not prev or prev.startswith("#") or _HR.match(prev) or _is_item(prev)
        if bold := _BOLD_ONLY.match(line):
            if len(bold.group(1)) <= BOLD_HEADING_MAX and not _SETEXT.match(nxt):
                inline = _continues(nxt) or (_prose(prev) and not _ends_sentence(prev))
                if inline:
                    inner = f"**{bold.group(1).strip()}**" + (":" if line.rstrip().endswith(":") else "")
                    if _prose(prev):
                        out[-1] = _glue(out[-1], inner)
                    else:
                        out.append(inner)
                    glue = True
                    continue
                if _has_content_after(lines, i):
                    out.append(_heading(line))
                    continue
        elif at_block_start or not prev:
            text = line.strip()
            if (_is_caps(text) and len(text) <= HEADING_MAX and text[-1] not in ".!?:;,"
                    and _has_content_after(lines, i)):
                out.append(_heading(text))
                continue
            if _COLON_HEADING.match(text) and _has_content_after(lines, i):
                out.append(_heading(text))
                continue
        out.append(line)
    return out


# --- 4. meta lines at the very top ---------------------------------------------------------------

def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").strip()


def _take_meta(lines: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Leading "Key: value" lines with a known key; the rest of the lines is returned as is."""
    meta: list[tuple[str, str]] = []
    end = i = 0
    while i < len(lines):
        if lines[i]:
            found = _meta_of(lines[i])
            if not found:
                break
            meta.append(found)
            end = i + 1
        i += 1
    return (meta, lines[end:]) if meta else ([], lines)


def _meta_of(line: str) -> tuple[str, str] | None:
    m = _META_LINE.match(line.strip())
    if not m:
        return None
    key = m.group("key").strip().strip("*_ ")
    value = m.group("value").strip().strip("*").strip()
    if _fold(key) not in META_KEYS or not value or len(value) > META_VALUE_MAX:
        return None
    return key, value


# --- 5. short plain lines -> bullets ------------------------------------------------------------

def _plain_item(line: str) -> bool:
    return (bool(line) and _prose(line) and len(line) < SHORT_LINE and len(line.split()) <= SHORT_WORDS
            and not _BOLD_ONLY.match(line))


def _group_short_runs(lines: list[str]) -> list[str]:
    """After a heading, three or more short plain lines in a row (the Glassdoor layout, one item
    per paragraph) are a list."""
    out = list(lines)
    i = 0
    while i < len(out):
        if not out[i].startswith("### "):
            i += 1
            continue
        j, run = i + 1, []
        while j < len(out):
            if not out[j]:
                j += 1
                continue
            if not _plain_item(out[j]):
                break
            if run and not _ends_sentence(out[run[-1]]) and _continues(out[j]):
                run = []                    # a sentence cut in pieces, not a list
                break
            run.append(j)
            j += 1
        if len(run) >= 3:
            for k in run:
                out[k] = f"- {out[k].strip()}"
        i = max(j, i + 1)
    return out


# --- 6. spacing ---------------------------------------------------------------------------------

def _same_list(a: str, b: str) -> bool:
    return (bool(_BULLET_ITEM.match(a)) and bool(_BULLET_ITEM.match(b))) or \
        (bool(_ORDERED_ITEM.match(a)) and bool(_ORDERED_ITEM.match(b)))


def _tidy(lines: list[str]) -> list[str]:
    """One blank line between blocks, none inside a list, blank lines around headings and rules."""
    kept: list[str] = []
    for i, line in enumerate(lines):
        if line == "":
            if not kept or kept[-1] == "":
                continue
            following = next((l for l in lines[i + 1:] if l), None)
            if following is not None and _same_list(kept[-1], following):
                continue                  # inside a list the items sit together
        kept.append(line)
    out: list[str] = []
    for line in kept:
        prev = out[-1] if out else ""
        if line and prev:
            fences = line.startswith("### ") or bool(_HR.match(line)) or prev.startswith("### ") or bool(_HR.match(prev))
            list_start = _is_item(line) and not _is_item(prev)
            if fences or list_start:
                out.append("")
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return out
