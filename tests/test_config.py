import re
from pathlib import Path

import pytest

from jobfinder.config import load_config
from jobfinder.db import connect

ROOT = Path(__file__).resolve().parent.parent


def test_example_config_is_valid_and_generic_for_mexico():
    cfg = load_config(ROOT / "config.example.toml")
    assert cfg.search.terms and all(isinstance(t, str) and t.strip() for t in cfg.search.terms)
    assert cfg.search.locations == ["Mexico"]
    assert cfg.search.sites == ["linkedin", "indeed", "glassdoor"]
    assert cfg.search.country_indeed == "Mexico"
    assert (cfg.search.results_wanted, cfg.search.pause_seconds) == (30, 30)
    assert cfg.stack and cfg.daily_goal == 5
    assert cfg.host == "127.0.0.1" and cfg.port == 8000
    assert cfg.database == ROOT / "data/jobs.db" and cfg.exports_dir == ROOT / "exports"


def test_example_config_documents_every_key_and_the_list_form():
    text = (ROOT / "config.example.toml").read_text()
    assert "cp config.example.toml config.toml" in text
    assert '["Monterrey, Nuevo León, Mexico", "Mexico"]' in text
    for key in ("terms", "sites", "location", "country_indeed", "results_wanted", "first_search_days",
                "overlap_hours", "pause_seconds", "stack", "daily", "database", "exports_dir", "host", "port"):
        assert re.search(rf"^{key} = ", text, re.M), key


def test_missing_config_raises_a_helpful_error(tmp_path):
    path = tmp_path / "config.toml"
    with pytest.raises(FileNotFoundError) as err:
        load_config(path)
    assert str(err.value) == (
        f"config file not found: {path}. Copy config.example.toml to config.toml first "
        "(cp config.example.toml config.toml)."
    )


def test_connect_creates_schema(tmp_path):
    conn = connect(tmp_path / "sub" / "jobs.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "runs", "run_sites"} <= tables


def test_profile_section_is_optional(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[search]\nterms = ["a"]\nsites = ["linkedin"]\nlocation = "Mexico"\ncountry_indeed = "Mexico"\n')
    assert load_config(path).stack == []
