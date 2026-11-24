from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Where the config is looked for when --config is not given: the first one that exists wins.
DEFAULT_CONFIG_PATHS = (Path("config.toml"), Path("config/config.toml"))
CONFIG_FOLDER = "config"

NO_CONFIG_HINT = (
    "config file not found: looked for config.toml and config/config.toml. "
    "Create it with: mkdir -p config && cp config.example.toml config/config.toml"
)


def resolve_config_path(explicit: Path | None = None) -> Path | None:
    """The config file to use: an explicit path as is, else ./config.toml, else ./config/config.toml.

    None means the default places hold no file. The folder layout exists so Docker can mount a
    folder, which (unlike a single file) still shows the file after an editor replaces it."""
    if explicit is not None:
        return explicit
    return next((p for p in DEFAULT_CONFIG_PATHS if p.is_file()), None)


@dataclass(frozen=True)
class SearchConfig:
    terms: list[str]
    sites: list[str]
    location: str | list[str]   # one place, or several searched in order
    country_indeed: str
    results_wanted: int = 30
    first_search_days: int = 7
    overlap_hours: int = 6
    pause_seconds: float = 3

    def __post_init__(self) -> None:
        places = [self.location] if isinstance(self.location, str) else list(self.location)
        if not places or any(not isinstance(p, str) or not p.strip() for p in places):
            raise ValueError("[search] location must be a place or a non-empty list of places")

    @property
    def locations(self) -> list[str]:
        return [self.location] if isinstance(self.location, str) else list(self.location)


@dataclass(frozen=True)
class Config:
    search: SearchConfig
    database: Path
    exports_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    stack: list[str] = field(default_factory=list)
    daily_goal: int = 5        # applications per weekday; 0 turns the goal off


def load_config(path: Path | None = None) -> Config:
    """Read the config at `path`, or at the default place (see `resolve_config_path`) when None."""
    if path is None:
        path = resolve_config_path()
        if path is None:
            raise FileNotFoundError(NO_CONFIG_HINT)
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        raise FileNotFoundError(f"config file not found: {path}. Copy config.example.toml there first.") from None
    with fh:
        raw = tomllib.load(fh)
    # Storage paths are relative to the config's folder, except for config/config.toml: there the
    # base is the project folder, so data/ and exports/ stay where they are with either layout.
    base = path.parent.parent if path.parent.name == CONFIG_FOLDER else path.parent
    storage = raw.get("storage", {})
    web = raw.get("web", {})
    profile = raw.get("profile", {})
    goal = raw.get("goal", {})
    return Config(
        search=SearchConfig(**raw["search"]),
        database=base / storage.get("database", "data/jobs.db"),
        exports_dir=base / storage.get("exports_dir", "exports"),
        host=web.get("host", "127.0.0.1"),
        port=web.get("port", 8000),
        stack=list(profile.get("stack", [])),
        daily_goal=max(int(goal.get("daily", 5)), 0),
    )
