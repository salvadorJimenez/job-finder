"""The resume agents: one canonical prompt per agent, thin wrappers per tool.

Every wrapper must exist, parse, describe itself and point to an existing
canonical file under ``agents/``. No file may carry local tooling blocks or
personal data.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ["resume-writer", "resume-pdf"]
MD_TOOLS = {
    "claude": ".claude/agents/{name}.md",
    "opencode": ".opencode/agents/{name}.md",
    "pi": ".pi/agents/{name}.md",
}
CODEX = ".codex/agents/{name}.toml"
FORBIDDEN = ["gentle-ai", "codegraph", "/users/", "/home/"]   # local tooling and absolute paths


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal YAML frontmatter reader: top-level `key: value` lines only."""
    assert text.startswith("---\n"), "missing frontmatter"
    head, sep, body = text[4:].partition("\n---\n")
    assert sep, "unterminated frontmatter"
    data: dict[str, str] = {}
    for line in head.splitlines():
        if line and not line.startswith((" ", "-", "#")) and ":" in line:
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data, body


def all_agent_files() -> list[Path]:
    paths = [ROOT / "agents" / f"{n}.md" for n in AGENTS]
    for name in AGENTS:
        paths += [ROOT / t.format(name=name) for t in MD_TOOLS.values()]
        paths.append(ROOT / CODEX.format(name=name))
    return paths


@pytest.mark.parametrize("name", AGENTS)
def test_canonical_prompt_exists_and_is_tool_neutral(name):
    path = ROOT / "agents" / f"{name}.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert not text.startswith("---"), "canonical prompt has no harness frontmatter"
    assert "profile/06-cv-rules.md" in text
    assert "override" in text.lower()
    assert "repository root" in text


@pytest.mark.parametrize("name", AGENTS)
@pytest.mark.parametrize("tool", MD_TOOLS)
def test_markdown_wrapper_points_to_canonical(name, tool):
    path = ROOT / MD_TOOLS[tool].format(name=name)
    assert path.is_file()
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    assert meta["description"]
    if tool in ("claude", "pi"):
        assert meta["name"] == name
        assert "tools" in meta
    if tool == "opencode":
        assert meta["mode"] == "subagent"
    assert f"agents/{name}.md" in body
    assert (ROOT / "agents" / f"{name}.md").is_file()


@pytest.mark.parametrize("name", AGENTS)
def test_codex_wrapper_points_to_canonical(name):
    path = ROOT / CODEX.format(name=name)
    assert path.is_file()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == name
    assert data["description"]
    assert f"agents/{name}.md" in data["developer_instructions"]
    assert (ROOT / "agents" / f"{name}.md").is_file()


@pytest.mark.parametrize("path", all_agent_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_local_tooling_or_personal_data(path):
    assert path.is_file()
    text = path.read_text(encoding="utf-8").lower()
    for word in FORBIDDEN:
        assert word not in text, f"{word!r} found in {path.relative_to(ROOT)}"


# OpenCode V2 agent files: `permissions` is an ordered list of {action, resource, effect};
# the V1 `permission:` map is deprecated (https://opencode.ai/docs/agents/).
OPENCODE_RULES = {
    "resume-writer": [("edit", "allow"), ("shell", "deny")],
    "resume-pdf": [("edit", "deny"), ("shell", "allow")],
}


@pytest.mark.parametrize("name", AGENTS)
def test_opencode_wrapper_uses_v2_permissions_list(name):
    text = (ROOT / MD_TOOLS["opencode"].format(name=name)).read_text(encoding="utf-8")
    header = text.split("---")[1]
    assert "\npermission:" not in header and "permissions:" in header
    rules = re.findall(r"- action: (\w+)\s+resource: \"\*\"\s+effect: (\w+)", header)
    assert rules == OPENCODE_RULES[name]
