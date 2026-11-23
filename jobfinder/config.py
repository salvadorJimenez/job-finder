from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")


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


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"config file not found: {path}. Copy config.example.toml to config.toml first "
            "(cp config.example.toml config.toml)."
        ) from None
    with fh:
        raw = tomllib.load(fh)
    base = path.parent
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
