from __future__ import annotations

import asyncio
import json
import signal
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from agents import AgentDefinition


@dataclass
class CLIRunResult:
    agent_name: str
    output: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    is_error: bool = False
    pid: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.is_error


@dataclass
class CLIProcess:
    agent_name: str
    process: asyncio.subprocess.Process
    started_at: float = field(default_factory=time.monotonic)

    @property
    def pid(self) -> int | None:
        return self.process.pid

    @property
    def is_running(self) -> bool:
        return self.process.returncode is None

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    async def kill(self) -> None:
        if self.is_running:
            self.process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


def _build_cli_command(
    agent: AgentDefinition,
    task: str,
    working_dir: str,
    *,
    max_turns: int = 50,
) -> tuple[list[str], str]:
    prompt_dir = Path(tempfile.mkdtemp(prefix="agent-ctx-"))
    (prompt_dir / "CLAUDE.md").write_text(agent.system_prompt)
    cmd = [
        "claude",
        "-p", task,
        "--verbose",
        "--output-format", "stream-json",
        "--max-turns", str(max_turns),
        "--permission-mode", "bypassPermissions",
        "--add-dir", str(prompt_dir),
    ]
    return cmd, working_dir, prompt_dir


async def start_cli_process(
    agent: AgentDefinition,
    task: str,
    working_dir: str,
    *,
    max_turns: int = 50,
) -> tuple[CLIProcess, Path]:
    cmd, cwd, prompt_file = _build_cli_command(agent, task, working_dir, max_turns=max_turns)
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    return CLIProcess(agent_name=agent.name, process=process), prompt_file


async def run_cli_agent(
    agent: AgentDefinition,
    task: str,
    working_dir: str,
    *,
    max_turns: int = 50,
    on_text: asyncio.Queue[str] | None = None,
) -> CLIRunResult:
    cli_proc, prompt_file = await start_cli_process(agent, task, working_dir, max_turns=max_turns)
    result = CLIRunResult(agent_name=agent.name, pid=cli_proc.pid)
    output_parts: list[str] = []

    assert cli_proc.process.stdout is not None
    async for line in cli_proc.process.stdout:
        decoded = line.decode().strip()
        if not decoded:
            continue
        text = _extract_text_from_stream_json(decoded)
        if text:
            output_parts.append(text)
            if on_text is not None:
                await on_text.put(text)

    stderr_data = b""
    if cli_proc.process.stderr is not None:
        stderr_data = await cli_proc.process.stderr.read()
    await cli_proc.process.wait()
    result.output = "\n".join(output_parts)
    if not result.output and stderr_data:
        result.output = stderr_data.decode(errors="replace")
    result.exit_code = cli_proc.process.returncode
    result.duration_ms = cli_proc.elapsed_ms
    result.is_error = result.exit_code != 0
    import shutil
    shutil.rmtree(prompt_file, ignore_errors=True)
    return result


def _extract_text_from_stream_json(line: str) -> str | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return line

    msg_type = data.get("type", "")

    if msg_type == "assistant":
        content = data.get("message", {}).get("content", [])
        texts = [
            block.get("text", "")
            for block in content
            if block.get("type") == "text"
        ]
        return "\n".join(texts) if texts else None

    if msg_type == "result":
        return data.get("result")

    return None


async def stream_cli_agent(
    agent: AgentDefinition,
    task: str,
    working_dir: str,
    *,
    max_turns: int = 50,
) -> AsyncIterator[str]:
    text_queue: asyncio.Queue[str] = asyncio.Queue()
    sentinel = object()

    async def _run() -> CLIRunResult:
        result = await run_cli_agent(
            agent, task, working_dir, max_turns=max_turns, on_text=text_queue
        )
        await text_queue.put(sentinel)  # type: ignore[arg-type]
        return result

    task_handle = asyncio.create_task(_run())

    while True:
        chunk = await text_queue.get()
        if chunk is sentinel:
            break
        yield chunk

    await task_handle
