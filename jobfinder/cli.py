"""Command line entry point: serve the page, search, or export a day."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from .config import NO_CONFIG_HINT, load_config, resolve_config_path
from .db import connect


def _port_is_free(host: str, port: int) -> bool:
    """False only when another process listens on host:port, so uvicorn could not bind it.

    SO_REUSEADDR matches what uvicorn does: a port left in TIME_WAIT by a server that just
    stopped is free. Windows lets SO_REUSEADDR bind over a live listener, so it uses
    SO_EXCLUSIVEADDRUSE instead. Other bind errors (unknown host, not a local address) are left
    for uvicorn to report.
    """
    import errno
    import socket

    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            return exc.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE))
    return True


def _url(host: str, port: int) -> str:
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-finder", description="Private local job search for LinkedIn, Indeed and Glassdoor.")
    parser.add_argument("--config", type=Path, help="path to the config file (default: config.toml, else config/config.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the local web page")
    serve.add_argument("--reload", action="store_true", help="restart automatically when the code changes")
    serve.add_argument("--host", help="address to listen on (default: [web] host in config.toml)")
    serve.add_argument("--port", type=int, help="port to listen on (default: [web] port in config.toml)")
    sub.add_parser("search", help="search for offers posted since the last search")
    exp = sub.add_parser("export", help="export the offers first seen on a day as Markdown")
    exp.add_argument("--date", required=True, help="day in YYYY-MM-DD format")
    exp.add_argument("--stdout", action="store_true", help="print instead of writing a file")
    args = parser.parse_args(argv)
    config_path = resolve_config_path(args.config)
    try:
        if config_path is None:
            raise FileNotFoundError(NO_CONFIG_HINT)
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.command == "serve":
        import uvicorn

        from .web import create_app

        host, port = args.host or cfg.host, args.port or cfg.port
        if not _port_is_free(host, port):
            # Checked before printing "running at", which would otherwise be followed by a crash.
            print(f"Port {port} on {host} is already in use (is job-finder already running, maybe in "
                  f"Docker?). Stop it or pick another port with --port.", file=sys.stderr)
            return 1
        # flush: when stdout is redirected (a log file, a background job) it is block-buffered
        # and the line would only appear after the server stops.
        print(f"job-finder running at {_url(host, port)} (press Ctrl+C to stop)", flush=True)
        if args.reload:
            # The reloader starts a fresh process on every change, so it needs an
            # import string; the config path travels in an environment variable.
            os.environ["JOB_FINDER_CONFIG"] = str(config_path.resolve())
            uvicorn.run(
                "jobfinder.web:app_from_env", factory=True, reload=True,
                reload_dirs=[str(Path(__file__).resolve().parent)],
                host=host, port=port, log_level="warning",
            )
            return 0
        app = create_app(cfg, config_path=config_path)
        uvicorn.run(app, host=host, port=port, log_level="warning")
        return 0

    conn = connect(cfg.database)
    if args.command == "search":
        from .search import run_search

        result = run_search(conn, cfg.search, on_progress=lambda m: print(f"  searching {m}"))
        print(f"{result.status}: {result.new_count} new, {result.seen_count} already seen")
        for site, error in result.errors.items():
            print(f"  {site} failed: {error}", file=sys.stderr)
        return 0 if result.status != "failed" else 1

    try:
        day = date.fromisoformat(args.date)
    except ValueError:
        print("Invalid --date, expected YYYY-MM-DD", file=sys.stderr)
        return 2
    from .export import export_day

    body = export_day(conn, day)
    if args.stdout:
        print(body, end="")
        return 0
    cfg.exports_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.exports_dir / f"jobs-{day.isoformat()}.md"
    out.write_text(body, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
