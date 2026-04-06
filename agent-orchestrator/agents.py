from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class ExecutionMode(enum.Enum):
    SDK = "sdk"
    CLI = "cli"
    AUTO = "auto"


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    system_prompt: str
    mode_preference: ExecutionMode
    sdk_tools: list[str] = field(default_factory=list)
    hooks: dict[str, list[str]] = field(default_factory=dict)


ANTI_SLOP_PREAMBLE = (
    "You follow strict anti-slop rules: no emojis, no comments in code, "
    "no docstrings unless required by tooling, self-documenting names only, "
    "strict typing, no Any types, minimal imports, no over-engineering, "
    "no backwards-compatibility shims, no defensive coding against impossible scenarios, "
    "no verbose logging. Keep output concise and direct."
)


def _load_prompt(agents_dir: Path, filename: str) -> str:
    prompt_path = agents_dir / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Agent prompt not found: {prompt_path}")
    raw = prompt_path.read_text()
    return f"{ANTI_SLOP_PREAMBLE}\n\n{raw}"


def _read_only_tools() -> list[str]:
    return [
        "Read",
        "Glob",
        "Grep",
        "Bash(read-only)",
        "WebFetch",
        "WebSearch",
    ]


def _write_tools() -> list[str]:
    return [
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Edit",
        "Write",
    ]


def build_registry(agents_dir: Path) -> dict[str, AgentDefinition]:
    return {
        "research": AgentDefinition(
            name="research",
            description="Documentation lookup, codebase exploration, API reference",
            system_prompt=_load_prompt(agents_dir, "research.md"),
            mode_preference=ExecutionMode.SDK,
            sdk_tools=_read_only_tools(),
        ),
        "linting": AgentDefinition(
            name="linting",
            description="Dead code removal, convention enforcement, cleanup",
            system_prompt=_load_prompt(agents_dir, "linting.md"),
            mode_preference=ExecutionMode.CLI,
            sdk_tools=_write_tools(),
        ),
        "testing": AgentDefinition(
            name="testing",
            description="Write and run pytest tests, coverage gap analysis",
            system_prompt=_load_prompt(agents_dir, "testing.md"),
            mode_preference=ExecutionMode.CLI,
            sdk_tools=_write_tools(),
        ),
        "security": AgentDefinition(
            name="security",
            description="Vulnerability scanning, secrets detection, input validation audit",
            system_prompt=_load_prompt(agents_dir, "security.md"),
            mode_preference=ExecutionMode.CLI,
            sdk_tools=_write_tools(),
        ),
        "code-review": AgentDefinition(
            name="code-review",
            description="PR review, diff review against CLAUDE.md guidelines",
            system_prompt=_load_prompt(agents_dir, "review.md"),
            mode_preference=ExecutionMode.CLI,
            sdk_tools=_write_tools(),
        ),
        "infrastructure": AgentDefinition(
            name="infrastructure",
            description="General infrastructure management, cloud resources, deployment",
            system_prompt=_load_prompt(agents_dir, "infrastructure.md"),
            mode_preference=ExecutionMode.CLI,
            sdk_tools=_write_tools(),
        ),
        "pm": AgentDefinition(
            name="pm",
            description="System design, architecture, project planning, GitHub Actions, task breakdown",
            system_prompt=_load_prompt(agents_dir, "pm.md"),
            mode_preference=ExecutionMode.CLI,
            sdk_tools=_write_tools(),
        ),
    }
