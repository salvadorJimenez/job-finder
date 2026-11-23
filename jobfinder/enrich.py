"""Extract salary, gross/net basis and tech stack from a job posting."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Salary:
    min_amount: float | None
    max_amount: float | None
    currency: str | None      # "MXN" | "USD" | other ISO code | None when not stated
    interval: str | None      # hourly | daily | weekly | monthly | yearly | None
    source: str               # structured | description


# --- salary -----------------------------------------------------------------

_AMOUNT = r"(?P<{n}>\d{{1,3}}(?:[,.]\d{{3}})+(?:\.\d{{1,2}})?|\d+(?:\.\d{{1,2}})?)(?P<{n}k>\s?[kK]\b)?"
_CUR_BEFORE = r"(?:US\$|USD|MXN|\$)"
_CUR_AFTER = r"(?:USD|MXN|pesos|d[oó]lares|dollars)\b"
_SALARY_RE = re.compile(
    rf"(?<![\w.,])(?P<pre>{_CUR_BEFORE})?\s?{_AMOUNT.format(n='a')}"
    rf"(?:\s?(?:-|–|—|\ba\b|\bto\b|\bhasta\b)\s?(?:{_CUR_BEFORE})?\s?{_AMOUNT.format(n='b')})?"
    rf"(?P<post>\s?{_CUR_AFTER})?",
    re.IGNORECASE,
)
_MAGNITUDE_AFTER = re.compile(r"\s?(?:billion|million|bn\b|mm\b|[MB]\b|millones|mil millones)", re.IGNORECASE)
_SALARY_WORDS = re.compile(r"sueldo|salario|salary|compensation|pay\b|ingreso|rango", re.IGNORECASE)

_CURRENCY_HINTS = [
    ("USD", re.compile(r"USD|US\$|d[oó]lar|dollar", re.IGNORECASE)),
    ("MXN", re.compile(r"MXN|\bpesos?\b|M\.N\.", re.IGNORECASE)),
]
_INTERVAL_HINTS = [
    ("hourly", re.compile(r"/\s?(?:hr|h|hour|hora)\b|per hour|por hora|\bhourly\b", re.IGNORECASE)),
    ("monthly", re.compile(r"mensual(?:es)?|al mes|por mes|/\s?mes|month(?:ly)?|/\s?mo\b", re.IGNORECASE)),
    ("yearly", re.compile(r"anual(?:es)?|al a[ñn]o|por a[ñn]o|annual(?:ly)?|year(?:ly)?|/\s?yr\b", re.IGNORECASE)),
    ("weekly", re.compile(r"semanal(?:es)?|weekly|per week", re.IGNORECASE)),
    ("daily", re.compile(r"diari[oa]s?|daily|per day|por d[ií]a", re.IGNORECASE)),
]


def _to_number(raw: str, k: str | None) -> float:
    if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+(?:\.\d{1,2})?", raw):
        decimals = ""
        if re.search(r"\.\d{1,2}$", raw):
            raw, decimals = raw.rsplit(".", 1)
        raw = raw.replace(",", "").replace(".", "")
        if decimals:
            raw = f"{raw}.{decimals}"
    value = float(raw)
    return value * 1000 if k else value


def _first_hint(hints: list[tuple[str, re.Pattern[str]]], text: str) -> str | None:
    found = [(m.start(), name) for name, rx in hints if (m := rx.search(text))]
    return min(found)[1] if found else None


def _salary_from_text(description: str) -> Salary | None:
    for line in re.split(r"[\n\r]+", description):
        has_salary_word = bool(_SALARY_WORDS.search(line))
        for m in _SALARY_RE.finditer(line):
            low = _to_number(m["a"], m["ak"])
            high = _to_number(m["b"], m["bk"]) if m["b"] else None
            has_currency = bool(m["pre"] or m["post"])
            looks_like_money = bool(re.search(r"[,.]\d{3}", m["a"]) or m["ak"])
            if not (has_currency or (has_salary_word and looks_like_money)):
                continue
            if _MAGNITUDE_AFTER.match(line, m.end("b") if m["b"] else m.end("a") + len(m["ak"] or "")):
                continue  # market size or funding, not pay
            window = line[max(0, m.start() - 30): m.end() + 40]
            return Salary(
                min_amount=low,
                max_amount=high,
                currency=_first_hint(_CURRENCY_HINTS, window),
                interval=_first_hint(_INTERVAL_HINTS, window),
                source="description",
            )
    return None


def _clean(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
        return None
    return value


def parse_salary(raw: dict[str, Any], description: str | None) -> Salary | None:
    """Salary from JobSpy structured fields, falling back to the description text."""
    low, high = _clean(raw.get("min_amount")), _clean(raw.get("max_amount"))
    if low is not None or high is not None:
        interval = _clean(raw.get("interval"))
        return Salary(
            min_amount=float(low) if low is not None else None,
            max_amount=float(high) if high is not None else None,
            currency=_clean(raw.get("currency")),
            interval=getattr(interval, "value", interval),
            source="description" if raw.get("salary_source") == "description" else "structured",
        )
    return _salary_from_text(description or "")


# --- gross / net ------------------------------------------------------------

_GROSS_RE = re.compile(r"\bbrut[oa]s?\b|\bgross\b|antes de impuestos|before tax(?:es)?", re.IGNORECASE)
_NET_RE = re.compile(
    r"\bnet[oa]s?\b"
    r"|(?<![.\w])net\s+(?:salary|pay|income|monthly|per\b)"
    r"|\b(?:salary|pay)\s*\(net\)"
    r"|take[- ]home"
    r"|\b(?:sueldo|salario|pago)(?:\s+mensual)?\s+libres?\b"
    r"|\blibres? de impuestos"
    r"|after tax(?:es)?",
    re.IGNORECASE,
)


def detect_salary_basis(description: str | None) -> str | None:
    """"gross", "net", "both", or None when the posting does not say."""
    text = description or ""
    gross, net = bool(_GROSS_RE.search(text)), bool(_NET_RE.search(text))
    if gross and net:
        return "both"
    if gross:
        return "gross"
    if net:
        return "net"
    return None


# --- stack ------------------------------------------------------------------

_I = re.IGNORECASE
# (display name, pattern, flags). Case-sensitive entries avoid common English words.
_TECH: list[tuple[str, str, int]] = [
    ("Ruby", r"\bruby\b", _I),
    ("Rails", r"\brails\b|\bRoR\b", _I),
    ("Python", r"\bpython\b", _I),
    ("Django", r"\bdjango\b", _I),
    ("Flask", r"\bflask\b", _I),
    ("FastAPI", r"\bfastapi\b", _I),
    ("JavaScript", r"\bjavascript\b", _I),
    ("TypeScript", r"\btypescript\b", _I),
    ("Node.js", r"\bnode\.?js\b|\bNode\b", 0),
    ("Express", r"\bexpress\.?\s?js\b", _I),
    ("React", r"\bReact(?:\.?js|JS)?\b", 0),
    ("Next.js", r"\bnext\.?js\b", _I),
    ("Vue", r"\bvue(?:\.?js)?\b", _I),
    ("Angular", r"\bangular\b", _I),
    ("Java", r"\bjava\b", _I),
    ("Spring", r"\bSpring\s?(?:Boot|Framework|MVC|Cloud|WebFlux)\b", _I),
    ("Kotlin", r"\bkotlin\b", _I),
    ("Go", r"\bgolang\b|\bGo\b(?![-'’])", 0),
    ("C#", r"(?<!\w)C#", _I),
    (".NET", r"\.NET\b|\bdotnet\b", _I),
    ("PHP", r"\bphp\b", _I),
    ("Laravel", r"\blaravel\b", _I),
    ("Elixir", r"\belixir\b", _I),
    ("Rust", r"\bRust\b", 0),
    ("Swift", r"\bSwift\b", 0),
    ("Scala", r"\bscala\b", _I),
    ("AWS", r"\baws\b|amazon web services", _I),
    ("GCP", r"\bgcp\b|google cloud", _I),
    ("Azure", r"\bazure\b", _I),
    ("Docker", r"\bdocker\b", _I),
    ("Kubernetes", r"\bkubernetes\b|\bk8s\b", _I),
    ("Terraform", r"\bterraform\b", _I),
    ("PostgreSQL", r"\bpostgres(?:ql)?\b", _I),
    ("MySQL", r"\bmysql\b", _I),
    ("MongoDB", r"\bmongo(?:db)?\b", _I),
    ("Redis", r"\bredis\b", _I),
    ("Elasticsearch", r"\belastic\s?search\b", _I),
    ("SQL", r"\bsql\b", _I),
    ("GraphQL", r"\bgraphql\b", _I),
    ("Kafka", r"\bkafka\b", _I),
    ("RabbitMQ", r"\brabbitmq\b", _I),
    ("Sidekiq", r"\bsidekiq\b", _I),
    ("RSpec", r"\brspec\b", _I),
    ("Tailwind", r"\btailwind\b", _I),
    ("HTML", r"\bhtml5?\b", _I),
    ("CSS", r"\bcss3?\b", _I),
]
_TECH_RE = [(name, re.compile(pattern, flags)) for name, pattern, flags in _TECH]
_CANONICAL = {name.lower(): name for name, _, _ in _TECH}
# "Express" alone is an ordinary word, so it only counts as the framework when the same
# text mentions Node. Capitalized only; "express your ideas" and "American Express" are not it.
_EXPRESS_BARE = re.compile(
    r"(?<!American )\bExpress\b(?!\s+(?:your|yourself|interest|ideas|opinions)\b)"
)


def stack_key(name: str) -> str:
    """Normalized form used to store and compare stack names ("Node.js" -> "node.js")."""
    return name.strip().lower()


def extract_stack(description: str | None, skills: str | None = None) -> list[str]:
    """Technologies mentioned in the description, in order of first appearance,
    followed by any extra skills reported by the source."""
    text = description or ""
    matches = {name: m for name, rx in _TECH_RE if (m := rx.search(text))}
    if "Express" not in matches and "Node.js" in matches and (m := _EXPRESS_BARE.search(text)):
        matches["Express"] = m
    found = sorted((m.start(), name) for name, m in matches.items())
    stack = [name for _, name in found]
    for skill in (skills or "").split(","):
        skill = skill.strip()
        if not skill:
            continue
        name = _CANONICAL.get(skill.lower(), skill)
        if name not in stack:
            stack.append(name)
    return stack


# --- work mode --------------------------------------------------------------

# Patterns run on lowercased, accent-stripped text (see _fold).
# "hybrid cloud" or "nube hibrida" describe technology, not where you work.
_HYBRID_RE = re.compile(
    r"(?<!nube )(?<!aplicaciones )(?<!apps )\bhibrid[oa]s?\b"
    r"|\bhybrid\b(?!\s+(?:cloud|apps?|mobile|infrastructure|environments?|architecture|solutions?)\b)"
)
_REMOTE_RE = re.compile(
    r"\bremote\b|\bremot[oa]s?\b|\bdesde casa\b|\btrabajo en casa\b|\bhome office\b"
    r"|\bwfh\b|\bwork from home\b|\bteletrabajo\b"
)
_ONSITE_RE = re.compile(
    r"\bpresencial(?:es|mente)?\b|\bon-?site\b|\bon site\b|\bin-?office\b|\bin office\b"
    r"|\ben oficina\b|\ben sitio\b"
)


def _fold(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def detect_work_mode(
    title: str | None, location: str | None, description: str | None, is_remote: bool | None
) -> str | None:
    """"remote", "hybrid", "onsite", or None when the posting does not say.

    Title and location are the strong signal, then the source's own remote flag,
    then the description (remote wins when it mentions both remote and onsite work)."""
    strong = _fold(f"{title or ''} {location or ''}")
    if _HYBRID_RE.search(strong):
        return "hybrid"
    if _REMOTE_RE.search(strong):
        return "remote"
    if _ONSITE_RE.search(strong):
        return "onsite"
    if is_remote is True or is_remote == 1:
        return "remote"
    text = _fold(description)
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _REMOTE_RE.search(text):
        return "remote"
    if _ONSITE_RE.search(text):
        return "onsite"
    return None
