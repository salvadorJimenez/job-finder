"""A typo in config.toml must not take the page down."""
from pathlib import Path

from fastapi.testclient import TestClient

from jobfinder.config import load_config
from jobfinder.db import connect
from jobfinder.web import create_app

ROOT = Path(__file__).resolve().parent.parent
BROKEN = '[search]\nterms = [\n  "backend engineer"\n  "software engineer",\n]\n'


def setup(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text((ROOT / "config.example.toml").read_text().replace('"data/jobs.db"', f'"{tmp_path}/jobs.db"'))
    cfg = load_config(cfg_path)
    calls = []
    app = create_app(cfg, runner=lambda *a: calls.append(a), config_path=cfg_path)
    connect(cfg.database).close()
    return TestClient(app), cfg_path, calls


def test_broken_config_shows_a_notice_instead_of_crashing(tmp_path):
    client, cfg_path, _ = setup(tmp_path)
    cfg_path.write_text(BROKEN)
    r = client.get("/")
    assert r.status_code == 200
    assert 'class="notice config-error"' in r.text
    assert "config.toml has an error" in r.text and "line 4" in r.text


def test_search_refuses_to_start_with_a_broken_config(tmp_path):
    client, cfg_path, calls = setup(tmp_path)
    cfg_path.write_text(BROKEN)
    r = client.post("/search", data={"confirm": "1"})
    assert r.status_code == 422
    body = r.json()
    assert body["reason"] == "config_error" and "config.toml has an error" in body["message"]
    assert calls == []


def test_fixed_config_clears_the_notice(tmp_path):
    client, cfg_path, _ = setup(tmp_path)
    good = cfg_path.read_text()
    cfg_path.write_text(BROKEN)
    assert "config.toml has an error" in client.get("/").text
    cfg_path.write_text(good)
    assert "config.toml has an error" not in client.get("/").text
