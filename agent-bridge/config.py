from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BridgeConfig:
    gateway_url: str
    gateway_token: str
    workspace: Path
    soul_path: Path
    user_path: Path
    agents_path: Path
    memory_path: Path

    @property
    def system_prompt_files(self) -> list[Path]:
        return [self.soul_path, self.user_path, self.agents_path, self.memory_path]


def _resolve_path(base: Path, raw: str) -> Path:
    expanded = Path(os.path.expanduser(raw))
    if expanded.is_absolute():
        return expanded
    return base / expanded


def load_config(config_path: Path | None = None) -> BridgeConfig:
    if config_path is None:
        config_path = Path(os.environ.get(
            "OPENCLAW_CONFIG",
            os.path.expanduser("~/.openclaw/openclaw.json"),
        ))

    raw = json.loads(config_path.read_text())

    gateway = raw.get("gateway", {})
    host = gateway.get("host", "127.0.0.1")
    port = gateway.get("port", 18789)
    gateway_url = f"ws://{host}:{port}"

    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if not token:
        token = gateway.get("token", "")

    agents_defaults = raw.get("agents", {}).get("defaults", {})
    workspace_raw = agents_defaults.get("workspace", "~/.openclaw/workspace")
    workspace = Path(os.path.expanduser(workspace_raw))

    return BridgeConfig(
        gateway_url=gateway_url,
        gateway_token=token,
        workspace=workspace,
        soul_path=workspace / "SOUL.md",
        user_path=workspace / "USER.md",
        agents_path=workspace / "AGENTS.md",
        memory_path=workspace / "MEMORY.md",
    )
