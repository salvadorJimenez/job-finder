import pytest

from jobfinder.enrich import Salary, detect_salary_basis, detect_work_mode, extract_stack, parse_salary


# --- S9: currency and period ------------------------------------------------

def test_structured_salary_keeps_currency_and_period():
    s = parse_salary(
        {"min_amount": 40000.0, "max_amount": 55000.0, "currency": "MXN", "interval": "monthly"},
        description="",
    )
    assert s == Salary(40000.0, 55000.0, "MXN", "monthly", "structured")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Sueldo: $40,000 - $50,000 MXN mensuales", Salary(40000, 50000, "MXN", "monthly", "description")),
        ("Salary range: USD 4,000 - 5,500 per month", Salary(4000, 5500, "USD", "monthly", "description")),
        ("Compensation: $90k - $120k USD annual", Salary(90000, 120000, "USD", "yearly", "description")),
        ("Ofrecemos sueldo de 35,000 pesos al mes", Salary(35000, None, "MXN", "monthly", "description")),
        ("Pay: 30 USD/hour", Salary(30, None, "USD", "hourly", "description")),
    ],
)
def test_salary_from_description(text, expected):
    assert parse_salary({}, description=text) == expected


def test_salary_unknown_currency_is_not_guessed():
    s = parse_salary({}, description="Sueldo: $45,000 mensuales")
    assert s.currency is None
    assert s.min_amount == 45000


def test_no_salary_published_returns_none():
    assert parse_salary({}, description="We use Ruby on Rails and have 5 years of history.") is None


def test_market_sizes_are_not_salaries():
    text = "Wealth management is a $545 billion industry. We raised $20M and US$ 3 millones."
    assert parse_salary({}, description=text) is None


def test_years_and_counts_are_not_salaries():
    text = "Requisitos: 5+ años de experiencia. Equipo de 200 personas desde 2015."
    assert parse_salary({}, description=text) is None


# --- S9: gross vs net -------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Sueldo bruto mensual de $50,000", "gross"),
        ("Salario neto: 40,000 MXN", "net"),
        ("Sueldo mensual libre de $38,000", "net"),
        ("USD 5,000 gross per month", "gross"),
        ("Net salary 4,000 USD", "net"),
        ("$60,000 brutos ($48,000 netos)", "both"),
        ("Sueldo: $45,000 mensuales", None),
        ("Experience with .NET and C# required", None),
        ("Tiempo libre y software libre", None),
    ],
)
def test_salary_basis(text, expected):
    assert detect_salary_basis(text) == expected


# --- S12: stack -------------------------------------------------------------

def test_extract_stack_from_description():
    text = (
        "Buscamos Ruby on Rails developer con PostgreSQL, Redis y Sidekiq. "
        "Frontend en React con TypeScript. Deploy en AWS con Docker."
    )
    # Order of first appearance in the text.
    assert extract_stack(text) == [
        "Ruby", "Rails", "PostgreSQL", "Redis", "Sidekiq", "React", "TypeScript", "AWS", "Docker",
    ]


def test_stack_avoids_false_positives():
    text = "We go fast. Java experience required. Learn to react quickly. Go-getter attitude."
    assert extract_stack(text) == ["Java"]


def test_spring_needs_a_framework_context():
    assert extract_stack("Access to Spring Health mental care") == []
    assert extract_stack("Java with Spring Boot") == ["Java", "Spring"]


def test_stack_merges_jobspy_skills_without_duplicates():
    assert extract_stack("Python and Django", skills="python, kubernetes") == ["Python", "Django", "Kubernetes"]


@pytest.mark.parametrize(
    "text",
    [
        "Backend with Express.js and PostgreSQL",
        "Stack: ExpressJS",
        "We use express js for APIs",
        "We use EXPRESS.JS",
        "Node.js with Express",          # bare capitalized Express next to Node
        "Express and Node services",     # Node mentioned after
        "REST APIs built on Node using Express",
    ],
)
def test_express_is_detected(text):
    assert "Express" in extract_stack(text)


@pytest.mark.parametrize(
    "text",
    [
        "Express your ideas and grow with us",
        "express yourself, we use Node.js",        # lowercase bare word never counts
        "Expresar ideas con claridad, Node.js",
        "Regular expression skills, Node.js",
        "Express your interest today. Backend in Node.js",
        "American Express is our client; stack Node.js",
        "Express delivery logistics, we hire in Ruby",  # no Node anywhere
        "Express",
    ],
)
def test_express_is_not_a_false_positive(text):
    assert "Express" not in extract_stack(text)


def test_express_json_variants_follow_first_appearance_order():
    assert extract_stack("Node.js and ExpressJS, then PostgreSQL") == ["Node.js", "Express", "PostgreSQL"]


# --- work mode --------------------------------------------------------------

@pytest.mark.parametrize(
    "title, location, description, is_remote, expected",
    [
        # (1) title + location are the strong signal
        ("Senior Developer (Híbrido)", "CDMX", "", None, "hybrid"),
        ("Backend Developer", "Monterrey (Hybrid)", "", None, "hybrid"),
        ("Dev Híbrida", None, None, None, "hybrid"),
        ("Backend Developer", "Desde casa, Mexico", "", None, "remote"),
        ("Remote Ruby Engineer", "Mexico", "", None, "remote"),
        ("Desarrollador 100% remoto", "México", "", None, "remote"),
        ("Backend Developer", "Mexico City (On-site)", "", None, "onsite"),
        ("Desarrollador Presencial", "CDMX", "", None, "onsite"),
        ("Backend Developer", "Monterrey, en oficina", "", None, "onsite"),
        # hybrid wins over remote inside the strong signal
        ("Remote friendly - Híbrido", "CDMX", "", None, "hybrid"),
        ("Backend Developer", "Híbrido / Remoto", "", None, "hybrid"),
        # strong signal beats is_remote and the description
        ("Desarrollador Presencial", "CDMX", "", True, "onsite"),
        ("Remote Engineer", "CDMX", "Trabajo presencial en oficina", None, "remote"),
        # (2) is_remote
        ("Backend Developer", "CDMX", "", True, "remote"),
        ("Backend Developer", "CDMX", "Esquema híbrido", True, "remote"),
        # (3) description
        ("Backend Developer", "CDMX", "Esquema híbrido, 2 días en casa", None, "hybrid"),
        ("Backend Developer", "CDMX", "Hybrid schedule. Remote on Fridays.", False, "hybrid"),
        ("Backend Developer", "CDMX", "Puedes trabajar desde casa.", None, "remote"),
        ("Backend Developer", "CDMX", "We are remote-first.", False, "remote"),
        ("Backend Developer", "CDMX", "Modalidad presencial en nuestras oficinas.", None, "onsite"),
        ("Backend Developer", "CDMX", "Position is on site in Querétaro.", None, "onsite"),
        # ambiguous description: remote and onsite both mentioned
        ("Backend Developer", "CDMX", "Remote option, otherwise presencial in CDMX.", None, "remote"),
        ("Backend Developer", "CDMX", "Modalidad presencial o 100% remoto.", None, "remote"),
        # (4) nothing
        ("Backend Developer", "CDMX", "Great team, great salary.", None, None),
        ("Backend Developer", "CDMX", "Great team, great salary.", False, None),
        ("Backend Developer", None, None, None, None),
    ],
)
def test_detect_work_mode_precedence(title, location, description, is_remote, expected):
    assert detect_work_mode(title, location, description, is_remote) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("HÍBRIDO", "hybrid"),
        ("hibrido", "hybrid"),
        ("Home Office", "remote"),
        ("Trabajo en casa", "remote"),
        ("WFH", "remote"),
        ("work from home", "remote"),
        ("Teletrabajo", "remote"),
        ("REMOTA", "remote"),
        ("remote-first", "remote"),
        ("ONSITE", "onsite"),
        ("on site", "onsite"),
        ("In-Office", "onsite"),
        ("in office", "onsite"),
        ("En Sitio", "onsite"),
        ("PRESENCIAL", "onsite"),
    ],
)
def test_detect_work_mode_keywords_ignore_case_and_accents(text, expected):
    assert detect_work_mode(text, None, None, None) == expected


def test_detect_work_mode_matches_whole_words_only():
    assert detect_work_mode("Dev", None, "Ambiente de remoteness total", None) is None
    assert detect_work_mode("Dev", None, "Trabajo presencialmente en CDMX", None) == "onsite"


@pytest.mark.parametrize(
    "description",
    ["Experience with hybrid cloud on AWS and Azure.", "Desarrollo de apps hibridas con Ionic.", "Migracion a nube hibrida."],
)
def test_hybrid_technology_is_not_hybrid_work(description):
    assert detect_work_mode("Backend Developer", "Mexico", description, None) is None
