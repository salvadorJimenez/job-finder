from jobfinder.dedup import is_possible_duplicate, normalize_company, normalize_title


def job(title, company, location="Ciudad de México"):
    return {"title": title, "company": company, "location": location}


def test_normalize_title_strips_noise_and_accents():
    assert normalize_title("Sr. Backend Developer (Remoto) - México") == "senior backend developer"


def test_normalize_company_strips_legal_suffixes():
    assert normalize_company("Acme Software S.A. de C.V.") == "acme software"
    assert normalize_company("Acme Software, Inc.") == "acme software"


def test_same_job_on_linkedin_and_indeed_is_duplicate():
    a = job("Senior Kotlin and Spring Developer", "Acme Software S.A. de C.V.")
    b = job("Sr. Kotlin and Spring Developer (Remote)", "Acme Software")
    assert is_possible_duplicate(a, b)


def test_different_level_is_not_duplicate():
    a = job("Software Engineer II", "Acme")
    b = job("Software Engineer III", "Acme")
    assert not is_possible_duplicate(a, b)


def test_different_company_is_not_duplicate():
    assert not is_possible_duplicate(job("Backend Developer", "Acme"), job("Backend Developer", "Globex"))


def test_different_role_same_company_is_not_duplicate():
    assert not is_possible_duplicate(job("Backend Developer", "Acme"), job("Frontend Developer", "Acme"))


def test_missing_company_is_never_duplicate():
    assert not is_possible_duplicate(job("Backend Developer", None), job("Backend Developer", None))
