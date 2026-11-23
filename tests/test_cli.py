import subprocess
import sys
from pathlib import Path

import pytest

import uvicorn
from fastapi import FastAPI

from jobfinder import cli
from jobfinder.web import app_from_env

ROOT = Path(__file__).resolve().parent.parent


def capture(monkeypatch, real_port_check=False):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    if not real_port_check:   # tests must not depend on which ports this machine has free
        monkeypatch.setattr(cli, "_port_is_free", lambda host, port: True)
    return calls


def test_serve_without_reload_runs_the_app_object(monkeypatch):
    calls = capture(monkeypatch)
    assert cli.main(["--config", str(ROOT / "config.example.toml"), "serve"]) == 0
    (app, kw), = calls
    assert isinstance(app, FastAPI)
    assert kw["host"] == "127.0.0.1" and "reload" not in kw


def test_serve_reload_restarts_on_code_changes(monkeypatch):
    monkeypatch.delenv("JOB_FINDER_CONFIG", raising=False)
    calls = capture(monkeypatch)
    assert cli.main(["--config", str(ROOT / "config.example.toml"), "serve", "--reload"]) == 0
    (app, kw), = calls
    assert app == "jobfinder.web:app_from_env"
    assert kw["factory"] is True and kw["reload"] is True
    assert kw["reload_dirs"] == [str(ROOT / "jobfinder")]
    assert kw["host"] == "127.0.0.1" and kw["port"] == 8000
    import os
    assert os.environ["JOB_FINDER_CONFIG"] == str((ROOT / "config.example.toml").resolve())


def test_app_from_env_uses_the_config_path(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text((ROOT / "config.example.toml").read_text().replace('"data/jobs.db"', f'"{tmp_path}/jobs.db"'))
    monkeypatch.setenv("JOB_FINDER_CONFIG", str(cfg))
    assert isinstance(app_from_env(), FastAPI)


def test_reload_server_survives_a_broken_config_and_recovers_when_fixed(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    cfg = tmp_path / "config.toml"
    good = (ROOT / "config.example.toml").read_text().replace('"data/jobs.db"', f'"{tmp_path}/jobs.db"')
    cfg.write_text('[search]\nterms = [\n  "a"\n  "b",\n]\n')
    monkeypatch.setenv("JOB_FINDER_CONFIG", str(cfg))
    client = TestClient(app_from_env())          # must not raise: the worker would die
    r = client.get("/")
    assert r.status_code == 503 and "config.toml has an error" in r.text
    cfg.write_text(good)
    r = client.get("/")
    assert r.status_code == 200 and "JOB_FINDER" in r.text


def test_serve_host_and_port_flags_override_the_config(monkeypatch):
    calls = capture(monkeypatch)
    assert cli.main(["--config", str(ROOT / "config.example.toml"), "serve", "--host", "0.0.0.0", "--port", "9000"]) == 0
    (_, kw), = calls
    assert (kw["host"], kw["port"]) == ("0.0.0.0", 9000)


def test_docker_files_keep_the_app_private_and_the_data_on_the_host():
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "compose.yaml").read_text()
    ignore = (ROOT / ".dockerignore").read_text().split()
    assert "tzdata" in dockerfile                     # local dates for "today", the goal and the streak
    assert "USER " in dockerfile                      # not root
    assert '"serve", "--host", "0.0.0.0"' in dockerfile
    assert '"127.0.0.1:8000:8000"' in compose         # published on this machine only
    assert "restart: unless-stopped" in compose
    assert "TZ: ${TZ:-America/Mexico_City}" in compose   # Mexico by default, overridable
    assert "cp config.example.toml config.toml" in compose  # Docker makes a directory if the file is missing
    assert "./data:/app/data" in compose and "./config.toml:/app/config.toml:ro" in compose
    assert {".venv", "data", "exports", ".git"} <= set(ignore)
    assert {"config.toml", "profile", "resumes"} <= set(ignore)  # private files never enter the build context
    assert "COPY pyproject.toml README.md LICENSE" in dockerfile  # pyproject declares license-files


# --- missing config.toml ----------------------------------------------------------------------

def missing_message(path):
    return (f"config file not found: {path}. Copy config.example.toml to config.toml first "
            "(cp config.example.toml config.toml).")


@pytest.mark.parametrize("argv", [["serve"], ["search"], ["export", "--date", "2026-01-01"]])
def test_missing_config_exits_1_with_a_one_line_hint(argv, tmp_path, capsys):
    path = tmp_path / "config.toml"
    assert cli.main(["--config", str(path), *argv]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == missing_message(path) + "\n"
    assert "Traceback" not in out.err


def test_missing_config_through_the_console_script_has_no_traceback(tmp_path):
    path = tmp_path / "config.toml"
    run = subprocess.run([sys.executable, "-m", "jobfinder.cli", "--config", str(path), "search"],
                         capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 1
    assert run.stderr == missing_message(path) + "\n"


def test_reload_server_with_a_missing_config_shows_the_hint_and_recovers(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    path = tmp_path / "config.toml"
    monkeypatch.setenv("JOB_FINDER_CONFIG", str(path))
    client = TestClient(app_from_env())
    r = client.get("/")
    assert r.status_code == 503
    assert "config.toml has an error" in r.text
    assert "Copy config.example.toml to config.toml first" in r.text
    path.write_text((ROOT / "config.example.toml").read_text().replace('"data/jobs.db"', f'"{tmp_path}/jobs.db"'))
    assert client.get("/").status_code == 200


def test_serve_prints_where_it_listens_and_how_to_stop(monkeypatch, capsys):
    capture(monkeypatch)
    assert cli.main(["--config", str(ROOT / "config.example.toml"), "serve", "--port", "8765"]) == 0
    out = capsys.readouterr().out
    assert out == "job-finder running at http://127.0.0.1:8765 (press Ctrl+C to stop)\n"


def test_serve_line_is_flushed_when_output_is_not_a_terminal(tmp_path):
    """Redirected stdout is block-buffered: the line must appear before the server blocks."""
    import subprocess, sys
    script = (
        "import sys, uvicorn; from jobfinder import cli\n"
        "def blocked(*a, **k):\n"
        "    sys.stderr.write(open(sys.argv[1]).read()); sys.stderr.flush()\n"
        "uvicorn.run = blocked\n"
        "cli._port_is_free = lambda host, port: True\n"
        f"cli.main(['--config', {str(ROOT / 'config.example.toml')!r}, 'serve'])\n"
    )
    out_file = tmp_path / "out.txt"
    with open(out_file, "w") as out:
        proc = subprocess.run([sys.executable, "-c", script, str(out_file)], stdout=out,
                              stderr=subprocess.PIPE, text=True, cwd=ROOT, timeout=60)
    assert proc.stderr == "job-finder running at http://127.0.0.1:8000 (press Ctrl+C to stop)\n"


def test_serve_refuses_a_busy_port_without_claiming_it_is_running(monkeypatch, capsys):
    import socket
    calls = capture(monkeypatch, real_port_check=True)
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        code = cli.main(["--config", str(ROOT / "config.example.toml"), "serve", "--port", str(port)])
    finally:
        busy.close()
    out, err = capsys.readouterr()
    assert code == 1 and calls == [] and out == ""
    assert err == (f"Port {port} on 127.0.0.1 is already in use (is job-finder already running, "
                   f"maybe in Docker?). Stop it or pick another port with --port.\n")


def _time_wait_port():
    """A port whose server side is in TIME_WAIT: the server closes an accepted connection first."""
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = listener.accept()
    conn.close()          # server closes first -> server side ends in TIME_WAIT
    client.close()
    listener.close()
    return port


def test_serve_restarts_right_away_on_a_port_left_in_time_wait(monkeypatch, capsys):
    calls = capture(monkeypatch, real_port_check=True)
    port = _time_wait_port()
    assert cli.main(["--config", str(ROOT / "config.example.toml"), "serve", "--port", str(port)]) == 0
    assert len(calls) == 1 and "already in use" not in capsys.readouterr().err


def test_serve_leaves_other_bind_errors_to_the_server(monkeypatch):
    calls = capture(monkeypatch, real_port_check=True)
    assert cli.main(["--config", str(ROOT / "config.example.toml"), "serve",
                     "--host", "no.such.host.invalid", "--port", "8765"]) == 0
    assert calls and calls[0][1]["host"] == "no.such.host.invalid"


def test_serve_brackets_an_ipv6_host_in_the_running_line(monkeypatch, capsys):
    capture(monkeypatch)
    assert cli.main(["--config", str(ROOT / "config.example.toml"), "serve", "--host", "::1", "--port", "8765"]) == 0
    assert capsys.readouterr().out == "job-finder running at http://[::1]:8765 (press Ctrl+C to stop)\n"


def test_python_m_serve_reports_a_busy_port_instead_of_crashing():
    import socket, subprocess, sys
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        proc = subprocess.run([sys.executable, "-m", "jobfinder.cli", "--config", str(ROOT / "config.example.toml"),
                               "serve", "--port", str(port)], capture_output=True, text=True, cwd=ROOT, timeout=60)
    finally:
        busy.close()
    assert proc.returncode == 1 and proc.stdout == "" and "Traceback" not in proc.stderr
    assert proc.stderr.startswith(f"Port {port} on 127.0.0.1 is already in use")
