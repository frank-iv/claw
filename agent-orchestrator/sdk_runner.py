from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from agents import AgentDefinition


@dataclass
class SDKRunResult:
    agent_name: str
    output_chunks: list[str] = field(default_factory=list)
    tool_uses: list[str] = field(default_factory=list)
    session_id: str | None = None
    total_cost_usd: float | None = None
    duration_ms: int = 0
    num_turns: int = 0
    is_error: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def full_output(self) -> str:
        return "\n".join(self.output_chunks)


async def run_sdk_agent(
    agent: AgentDefinition,
    task: str,
    working_dir: str,
    *,
    max_turns: int = 50,
    max_budget_usd: float | None = None,
    on_text: asyncio.Queue[str] | None = None,
) -> SDKRunResult:
    options = ClaudeAgentOptions(
        system_prompt=agent.system_prompt,
        cwd=working_dir,
        permission_mode="bypassPermissions",
        allowed_tools=agent.sdk_tools,
        max_turns=max_turns,
    )

    if max_budget_usd is not None:
        options.max_budget_usd = max_budget_usd

    result = SDKRunResult(agent_name=agent.name)
    start = time.monotonic()

    async for message in query(prompt=task, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    result.output_chunks.append(block.text)
                    if on_text is not None:
                        await on_text.put(block.text)
                elif isinstance(block, ToolUseBlock):
                    result.tool_uses.append(f"{block.name}({list(block.input.keys())})")

        elif isinstance(message, ResultMessage):
            result.session_id = message.session_id
            result.total_cost_usd = message.total_cost_usd
            result.duration_ms = message.duration_ms
            result.num_turns = message.num_turns
            result.is_error = message.is_error
            if message.errors:
                result.errors = list(message.errors)

    if result.duration_ms == 0:
        result.duration_ms = int((time.monotonic() - start) * 1000)

    return result


async def stream_sdk_agent(
    agent: AgentDefinition,
    task: str,
    working_dir: str,
    *,
    max_turns: int = 50,
) -> AsyncIterator[str]:
    text_queue: asyncio.Queue[str] = asyncio.Queue()
    sentinel = object()

    async def _run() -> SDKRunResult:
        result = await run_sdk_agent(
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
