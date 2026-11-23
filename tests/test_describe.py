"""D1 — the description normalizer: scraped text in, tidy Markdown out (render only; storage untouched)."""
import re

import pytest

from jobfinder.describe import normalize_description, parse_description


def norm(text):
    return normalize_description(text)


def words(text):
    """Every word in order, lower-cased: what must survive normalization."""
    return re.findall(r"[^\W_]+", text.lower())


# --- real-looking samples, one per scraper quirk ---------------------------------------------

LINKEDIN_INLINE_BOLD = (
    "**The Role**\n\nWe are seeking a\n**Full Stack Software Engineer**\nto join our IT team and work closely with "
    "our Data Scientist. You will build a\n**Python backend**\nand\n**Next. js frontend**\nthat power internal tools.\n\n"
    "**Key Responsibilities**\n\n* Collaborate directly with the Data Science team to\n  **produce ML models**\n"
    "  , pricing engines, and forecasting systems.\n* Design, develop, and maintain\n  **microservices**\n"
    "  that are scalable.\n\n**Required Qualifications**\n\n* Bachelor's degree in computer science\n"
)

LINKEDIN_WHITESPACE = (
    "We are seeking a Full Stack Software Engineer with strong backend expertise in C#.\n  \n  \n"
    "The ideal candidate is capable of designing APIs.\n  \n  \n**We offer:**\n\n* Career Development\n"
    "* Pay Transparency\n\n**Learn More Here**\n: https://example.com/careers\n  \n  \n"
    "**Tasks and Qualifications:**\n**Key Responsibilities**\n\n* Design RESTful APIs using C#.\n"
)

LINKEDIN_BULLET_GLYPH = (
    "Must have:\n\n• At least 4 years of experience as a Data Engineer.\n\n• Deep knowledge of Google Cloud.\n\n"
    "• Programming skills (SQL, Python).\n\nNice to have:\n\n• Terraform\n"
)

INDEED_DOT_AND_O = (
    "Programar PLCs de maquinaria OEM\n\n· Programar sistemas de visión\n\n· Programar robots\n\n"
    "Conocimientos en:\n\n· Programar PLCs nivel experto en alguna de las siguientes marcas\n\no Siemens\n\no Allen Bradley\n\n"
    "· Haber integrado una celda robótica\n\nTipo de puesto: Tiempo completo\n"
)

INDEED_BOLD_HEADINGS = (
    "PROGRAMADOR DE MANTENIMIENTO – MINA SUBTERRÁNEA\n\n**Descripción del empleo:**\n\n"
    "Empresa contratista minera busca a **PROGRAMADOR DE MANTENIMIENTO** para un proyecto.\n\n"
    "REQUISITOS INDISPENSABLES\n\n* Escolaridad: Ingeniería Mecánica.\n* Experiencia mínima de **3 años**.\n"
)

INDEED_ATX = "### **About Us**\n\nFounded in 2011.\n\n# Big title\n\n#### Requisitos:\n\n- Python\n"

INDEED_SETEXT = "**Principal Full Stack Engineer**\n----------------------------------------------------------------\n\nWe build things.\n"

GLASSDOOR_PLAIN = (
    "Experiencia en:\n\n4 a 9 años de Experiencia Fullstack\n\nExperimente el ajuste fino de modelos de lenguaje\n\n"
    "Conocimiento de microservicios y Docker\n\nInglés intermedio\n\n"
    "Ubicación:\n\nCalle Falsa 123, Ciudad de México\n\nCategoría: Administración\n"
)

GLASSDOOR_TOP_META = (
    "Ubicación: Monterrey, Nuevo León\nCategoría: Administración\nID de Requisición: R-12345\n\n"
    "**ANALISTA DE ARQUITECTURA**\n\nResponsable de diseñar soluciones.\n"
)

ESCAPED = (
    "\\- Python\n\\- SQL \\(avanzado\\)\n\\* Docker\n1\\. Primero\nSalario: \\$50,000 \\+ bonos\\.\n"
    "\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\n\nC\\# y snake\\_case\n"
)

NUMBERED = "Pasos:\n\n1) Enviar CV\n2) Entrevista técnica\n3) Oferta\n"

SAMPLES = {
    "linkedin_inline_bold": LINKEDIN_INLINE_BOLD,
    "linkedin_whitespace": LINKEDIN_WHITESPACE,
    "linkedin_bullet_glyph": LINKEDIN_BULLET_GLYPH,
    "indeed_dot_and_o": INDEED_DOT_AND_O,
    "indeed_bold_headings": INDEED_BOLD_HEADINGS,
    "indeed_atx": INDEED_ATX,
    "indeed_setext": INDEED_SETEXT,
    "glassdoor_plain": GLASSDOOR_PLAIN,
    "glassdoor_top_meta": GLASSDOOR_TOP_META,
    "escaped": ESCAPED,
    "numbered": NUMBERED,
}


# --- whitespace -------------------------------------------------------------------------------

def test_whitespace_only_lines_are_dropped_and_blank_runs_collapse():
    out = norm("one\n  \n  \n\n\n\ntwo\n \t \nthree")
    assert "  " not in out
    assert "\n\n\n" not in out
    assert out.split() == ["one", "two", "three"]


def test_empty_and_none_give_empty():
    assert norm(None) == "" and norm("") == "" and norm("  \n \n") == ""


# --- escapes ----------------------------------------------------------------------------------

def test_markdown_escapes_from_scrapers_are_undone():
    out = norm(ESCAPED)
    assert "\\" not in out
    assert "- Python" in out and "- SQL (avanzado)" in out
    assert "* Docker" in out                       # "\* Docker" was a bullet (existing "* " lists are kept)
    assert "1. Primero" in out
    assert "Salario: $50,000 + bonos." in out
    assert "C# y snake_case" in out


def test_all_escaped_separator_becomes_a_rule_not_underscores():
    out = norm("Intro text.\n\\_\\_\\_\\_\\_\\_\n\nMore text.")
    assert "\\" not in out and "___" not in out
    assert re.search(r"^\*\*\*$", out, re.M)      # a thematic break, never a setext underline for "Intro text."
    assert "### Intro text" not in out


# --- bullets and numbers ------------------------------------------------------------------------

@pytest.mark.parametrize("glyph", ["•", "·", "▪", "◦", "–", "—"])
def test_bullet_glyphs_become_dash_lists(glyph):
    out = norm(f"Skills\n\n{glyph} Python\n\n{glyph} SQL\n\n{glyph} Docker\n")
    assert [l for l in out.splitlines() if l.startswith("- ")] == ["- Python", "- SQL", "- Docker"]


def test_blank_lines_between_bullets_are_removed_so_the_list_is_tight():
    out = norm(LINKEDIN_BULLET_GLYPH)
    assert "- At least 4 years of experience as a Data Engineer.\n- Deep knowledge of Google Cloud.\n- Programming" in out


def test_a_dash_inside_a_paragraph_is_not_a_bullet():
    text = "Customer Files Orchestration Service\n– the system that makes files usable.\n"
    out = norm(text)
    assert "- the system" not in out and "– the system" in out


def test_o_bullets_nest_under_a_bullet_and_need_a_capital():
    out = norm(INDEED_DOT_AND_O)
    assert "- Programar PLCs nivel experto en alguna de las siguientes marcas\n  - Siemens\n  - Allen Bradley\n- Haber integrado" in out
    prose = norm("Buscamos gente\no que sepa programar\n")
    assert "- que sepa" not in prose and "o que sepa programar" in prose


def test_existing_lists_are_kept_and_numbered_parens_become_dots():
    assert "- one\n- two" in norm("- one\n- two\n")
    assert "* one\n* two" in norm("* one\n* two\n")
    out = norm(NUMBERED)
    assert "1. Enviar CV\n2. Entrevista técnica\n3. Oferta" in out


def test_list_item_continuation_lines_are_joined_with_punctuation_fixed():
    out = norm(LINKEDIN_INLINE_BOLD)
    assert "* Collaborate directly with the Data Science team to **produce ML models**, pricing engines, and forecasting systems." in out
    assert "* Design, develop, and maintain **microservices** that are scalable." in out


# --- headings ---------------------------------------------------------------------------------

def test_bold_only_lines_become_headings_without_bold_or_colon():
    out = norm("**Requisitos indispensables:**\n* Experiencia en Spring\n* Inglés\n")
    assert out.startswith("### Requisitos indispensables\n\n* Experiencia en Spring")
    assert "**" not in out.splitlines()[0]


def test_all_caps_lines_become_title_cased_headings():
    out = norm(INDEED_BOLD_HEADINGS)
    assert "### Programador de Mantenimiento – Mina Subterránea" in out
    assert "### Requisitos Indispensables" in out
    out = norm("**ANALISTA DE ARQUITECTURA**\n\nDiseña soluciones con SQL y PLC.\n")
    assert out.startswith("### Analista de Arquitectura")


def test_short_lines_ending_with_colon_become_headings():
    out = norm(GLASSDOOR_PLAIN)
    assert "### Experiencia en\n" in out
    assert "### Ubicación\n" in out


def test_long_or_sentence_colon_lines_are_not_headings():
    sentence = "Every file a customer brings to the company passes through this team and its services:"
    assert "###" not in norm(f"{sentence}\n\n- a\n- b\n")
    assert "###" not in norm("Habla inglés. Contacto:\n\ntexto\n")      # sentence punctuation before the colon


def test_a_heading_needs_content_after_it():
    assert "###" not in norm("Intro.\n\nUbicación:\n")
    assert "###" not in norm("Intro.\n\n**Fin**\n")


def test_bold_in_the_middle_of_a_sentence_stays_inline():
    out = norm(LINKEDIN_INLINE_BOLD)
    assert ("We are seeking a **Full Stack Software Engineer** to join our IT team and work closely with our Data Scientist."
            in out)
    assert "build a **Python backend** and **Next. js frontend** that power internal tools." in out
    assert "### Full Stack Software Engineer" not in out


def test_consecutive_bold_headings_stay_separate():
    out = norm(LINKEDIN_WHITESPACE)
    assert "### Tasks and Qualifications\n\n### Key Responsibilities" in out
    assert "### We offer\n\n* Career Development\n* Pay Transparency" in out


def test_atx_headings_are_normalized_to_one_level_without_bold_or_colon():
    out = norm(INDEED_ATX)
    assert "### About Us" in out and "### Big title" in out and "### Requisitos\n" in out
    assert not re.search(r"^#{1,2} |^#{4,} ", out, re.M)


def test_setext_underlines_become_headings():
    out = norm(INDEED_SETEXT)
    assert out.startswith("### Principal Full Stack Engineer\n\nWe build things.")
    assert "-----" not in out


# --- top meta ---------------------------------------------------------------------------------

def test_key_value_lines_at_the_very_top_become_a_meta_list():
    parsed = parse_description(GLASSDOOR_TOP_META)
    assert parsed.meta == [("Ubicación", "Monterrey, Nuevo León"), ("Categoría", "Administración"),
                           ("ID de Requisición", "R-12345")]
    assert parsed.body.startswith("### Analista de Arquitectura")
    out = norm(GLASSDOOR_TOP_META)
    assert out.startswith("- **Ubicación:** Monterrey, Nuevo León\n- **Categoría:** Administración\n- **ID de Requisición:** R-12345\n\n###")


def test_key_value_lines_further_down_are_left_alone():
    parsed = parse_description("Intro paragraph here.\n\nUbicación: Monterrey\n")
    assert parsed.meta == [] and "Ubicación: Monterrey" in parsed.body


def test_unknown_keys_at_the_top_are_not_meta():
    assert parse_description("Rol: Desarrollador .NET\n\nTexto.\n").meta == []


# --- short plain lines -> bullets ---------------------------------------------------------------

def test_three_short_paragraphs_after_a_heading_become_a_list():
    out = norm(GLASSDOOR_PLAIN)
    assert ("### Experiencia en\n\n- 4 a 9 años de Experiencia Fullstack\n- Experimente el ajuste fino de modelos de lenguaje\n"
            "- Conocimiento de microservicios y Docker\n- Inglés intermedio") in out


def test_fewer_than_three_short_lines_stay_paragraphs():
    out = norm("**Perfil**\n\nUno corto\n\nDos corto\n\nTexto largo. " + "palabra " * 30 + "\n")
    assert "- Uno corto" not in out


def test_long_lines_are_never_turned_into_bullets():
    long = "Una línea muy larga que describe el puesto con todo detalle y sin dejar nada fuera de la descripción real. " * 2
    out = norm(f"### Perfil\n\n{long}\n\n{long}\n\n{long}\n")
    assert "- Una" not in out


# --- invariants -------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_normalization_is_idempotent(name):
    once = norm(SAMPLES[name])
    assert norm(once) == once


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_no_words_are_lost_or_reordered(name):
    before = words(SAMPLES[name])
    after = words(norm(SAMPLES[name]))
    if name == "indeed_dot_and_o":
        before = [w for i, w in enumerate(before) if not (w == "o" and before[i + 1] in ("siemens", "allen"))]
    assert after == before


def test_already_good_markdown_is_left_alone():
    good = "### Role\n\nBuild things.\n\n- Python\n- SQL\n\n1. First\n2. Second\n"
    assert norm(good) == good.strip()


def test_code_fences_are_untouched():
    text = "Intro\n\n```\n• not a bullet\n\\- kept\n\n\n\nGLOBAL\n```\n"
    out = norm(text)
    assert "```\n• not a bullet\n\\- kept\n\n\n\nGLOBAL\n```" in out


def test_html_is_never_interpreted_by_the_normalizer():
    out = norm("**REQUISITOS**\n\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n")
    assert "<script>alert(1)</script>" in out and "<img src=x onerror=alert(1)>" in out
