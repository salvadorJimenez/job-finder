"""Detect the same posting published on several sites or reposted under a new id."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from rapidfuzz import fuzz

TITLE_THRESHOLD = 90
COMPANY_THRESHOLD = 90

_TITLE_SYNONYMS = {"sr": "senior", "jr": "junior", "ssr": "semi senior", "dev": "developer", "eng": "engineer"}
_TITLE_NOISE = {
    "remote", "remoto", "remota", "hybrid", "hibrido", "presencial", "onsite",
    "mexico", "mx", "cdmx", "latam", "home", "office", "urgente",
}
# Words that change the role itself; two titles must agree on them.
_LEVEL_WORDS = {"i", "ii", "iii", "iv", "1", "2", "3", "senior", "junior", "semi", "mid", "lead", "staff", "principal"}
_COMPANY_SUFFIXES = re.compile(
    r"\b(s\.?\s?a\.?\s?p\.?\s?i\.?|s\.?\s?a\.?|de c\.?\s?v\.?|s\.?\s?de r\.?\s?l\.?|inc\.?|llc\.?|ltd\.?|corp\.?|gmbh)(?=\s|$|,)"
)


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def normalize_title(title: str | None) -> str:
    words = re.sub(r"[^a-z0-9#+]+", " ", _ascii(title or "")).split()
    words = [_TITLE_SYNONYMS.get(w, w) for w in words]
    return " ".join(w for w in " ".join(words).split() if w not in _TITLE_NOISE)


def normalize_company(company: str | None) -> str:
    text = _COMPANY_SUFFIXES.sub(" ", _ascii(company or ""))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def is_possible_duplicate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    company_a, company_b = normalize_company(a.get("company")), normalize_company(b.get("company"))
    if not company_a or not company_b:
        return False
    if fuzz.ratio(company_a, company_b) < COMPANY_THRESHOLD:
        return False
    title_a, title_b = normalize_title(a.get("title")), normalize_title(b.get("title"))
    if set(title_a.split()) & _LEVEL_WORDS != set(title_b.split()) & _LEVEL_WORDS:
        return False
    return fuzz.token_sort_ratio(title_a, title_b) >= TITLE_THRESHOLD
