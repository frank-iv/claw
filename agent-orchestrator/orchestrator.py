from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Union

from agents import AgentDefinition, EC2Config, ExecutionMode, build_registry
from cli_runner import CLIProcess, CLIRunResult, run_cli_agent, start_cli_process
from ec2_runner import EC2RunResult, run_ec2_review
from sdk_runner import SDKRunResult, run_sdk_agent


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskHandle:
    task_id: str
    agent_name: str
    execution_mode: ExecutionMode
    status: TaskStatus = TaskStatus.PENDING
    async_task: asyncio.Task[Union[SDKRunResult, CLIRunResult, EC2RunResult]] | None = None
    cli_process: CLIProcess | None = None
    result: SDKRunResult | CLIRunResult | EC2RunResult | None = None

    @property
    def is_active(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.RUNNING)


ANALYSIS_AGENTS = frozenset({"research"})
WRITE_AGENTS = frozenset({"code-review", "security", "linting", "testing", "infrastructure", "pm"})


def resolve_execution_mode(agent: AgentDefinition) -> ExecutionMode:
    if agent.mode_preference != ExecutionMode.AUTO:
        return agent.mode_preference
    if agent.name in ANALYSIS_AGENTS:
        return ExecutionMode.SDK
    return ExecutionMode.CLI


class Orchestrator:
    def __init__(self, agents_dir: Path | str, default_working_dir: str = ".") -> None:
        self._agents_dir = Path(agents_dir)
        self._default_working_dir = default_working_dir
        self._registry = build_registry(self._agents_dir)
        self._tasks: dict[str, TaskHandle] = {}
        self._lock = asyncio.Lock()

    @property
    def available_agents(self) -> list[str]:
        return list(self._registry.keys())

    def get_agent(self, name: str) -> AgentDefinition:
        if name not in self._registry:
            raise KeyError(f"Unknown agent: {name}. Available: {self.available_agents}")
        return self._registry[name]

    async def dispatch(
        self,
        task_description: str,
        *,
        agent_name: str | None = None,
        working_dir: str | None = None,
        force_mode: ExecutionMode | None = None,
        max_turns: int = 50,
        max_budget_usd: float | None = None,
        ec2_config: EC2Config | None = None,
    ) -> TaskHandle:
        agent = self._select_agent(task_description, agent_name)
        mode = force_mode if force_mode is not None else resolve_execution_mode(agent)
        target_dir = working_dir or self._default_working_dir

        task_id = str(uuid.uuid4())[:8]
        handle = TaskHandle(
            task_id=task_id,
            agent_name=agent.name,
            execution_mode=mode,
        )

        coro: asyncio.Coroutine[None, None, SDKRunResult | CLIRunResult | EC2RunResult]

        if mode == ExecutionMode.EC2:
            config = ec2_config or agent.ec2_config
            if config is None:
                raise ValueError(f"EC2 mode requires ec2_config for agent {agent.name}")
            coro = run_ec2_review(
                agent,
                repo_url=config.repo_url,
                branch=config.branch,
                stages=config.stages,
                instance_type=config.instance_type,
                region=config.region,
            )
        elif mode == ExecutionMode.SDK:
            coro = run_sdk_agent(
                agent,
                task_description,
                target_dir,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
            )
        else:
            coro = run_cli_agent(
                agent,
                task_description,
                target_dir,
                max_turns=max_turns,
            )

        handle.status = TaskStatus.RUNNING
        handle.async_task = asyncio.create_task(
            self._run_and_track(handle, coro),
            name=f"agent-{agent.name}-{task_id}",
        )

        async with self._lock:
            self._tasks[task_id] = handle

        return handle

    async def dispatch_parallel(
        self,
        tasks: list[dict[str, str | None]],
        *,
        working_dir: str | None = None,
        max_turns: int = 50,
    ) -> list[TaskHandle]:
        handles = []
        for task_spec in tasks:
            handle = await self.dispatch(
                task_description=task_spec["task"],  # type: ignore[arg-type]
                agent_name=task_spec.get("agent"),
                working_dir=working_dir,
                force_mode=ExecutionMode.CLI,
                max_turns=max_turns,
            )
            handles.append(handle)
        return handles

    async def wait_for(self, task_id: str) -> SDKRunResult | CLIRunResult | EC2RunResult:
        handle = self._get_handle(task_id)
        if handle.async_task is None:
            raise RuntimeError(f"Task {task_id} has no running coroutine")
        await handle.async_task
        assert handle.result is not None
        return handle.result

    async def wait_all(self, task_ids: list[str] | None = None) -> list[SDKRunResult | CLIRunResult | EC2RunResult]:
        ids = task_ids or list(self._tasks.keys())
        results: list[SDKRunResult | CLIRunResult | EC2RunResult] = []
        for tid in ids:
            try:
                results.append(await self.wait_for(tid))
            except Exception as exc:
                handle = self._get_handle(tid)
                if isinstance(handle.result, (SDKRunResult, CLIRunResult)):
                    results.append(handle.result)
                else:
                    results.append(SDKRunResult(
                        agent_name=handle.agent_name,
                        is_error=True,
                        errors=[str(exc)],
                    ))
        return results

    async def cancel(self, task_id: str) -> None:
        handle = self._get_handle(task_id)
        if not handle.is_active:
            return

        if handle.cli_process is not None:
            await handle.cli_process.kill()

        if handle.async_task is not None and not handle.async_task.done():
            handle.async_task.cancel()
            try:
                await handle.async_task
            except asyncio.CancelledError:
                pass

        handle.status = TaskStatus.CANCELLED

    def status(self, task_id: str) -> dict[str, str | int | None]:
        handle = self._get_handle(task_id)
        info: dict[str, str | int | None] = {
            "task_id": handle.task_id,
            "agent": handle.agent_name,
            "mode": handle.execution_mode.value,
            "status": handle.status.value,
        }
        if handle.cli_process is not None:
            info["pid"] = handle.cli_process.pid
            info["elapsed_ms"] = handle.cli_process.elapsed_ms
        return info

    def list_tasks(self) -> list[dict[str, str | int | None]]:
        return [self.status(tid) for tid in self._tasks]

    def _get_handle(self, task_id: str) -> TaskHandle:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task_id}")
        return self._tasks[task_id]

    def _select_agent(self, task_description: str, agent_name: str | None) -> AgentDefinition:
        if agent_name is not None:
            return self.get_agent(agent_name)
        return self._auto_select_agent(task_description)

    def _auto_select_agent(self, task_description: str) -> AgentDefinition:
        lower = task_description.lower()
        keyword_map: dict[str, list[str]] = {
            "research": ["research", "find", "look up", "documentation", "explain", "what is", "how does"],
            "security": ["security", "vulnerability", "audit", "cve", "injection", "secrets"],
            "code-review": ["review", "diff", "check code", "guidelines", "conventions"],
            "testing": ["test", "pytest", "coverage", "spec", "unit test", "integration test"],
            "linting": ["lint", "format", "clean", "unused", "dead code", "style"],
            "infrastructure": ["deploy", "infra", "lambda", "ecr", "iam", "aws", "docker"],
            "pm": ["design", "architect", "plan", "breakdown", "github actions", "ci/cd", "workflow", "project", "spec", "rfc", "proposal"],
            "ec2-review": ["ec2 review", "spot review", "remote review"],
        }
        for agent_name, keywords in keyword_map.items():
            if any(kw in lower for kw in keywords):
                return self.get_agent(agent_name)
        return self.get_agent("research")

    async def _run_and_track(
        self,
        handle: TaskHandle,
        coro: asyncio.Coroutine[None, None, SDKRunResult | CLIRunResult],
    ) -> SDKRunResult | CLIRunResult:
        try:
            result = await coro
            handle.result = result
            is_error = result.is_error if isinstance(result, SDKRunResult) else result.is_error
            handle.status = TaskStatus.FAILED if is_error else TaskStatus.COMPLETED
            return result
        except asyncio.CancelledError:
            handle.status = TaskStatus.CANCELLED
            raise
        except Exception as exc:
            handle.status = TaskStatus.FAILED
            handle.result = SDKRunResult(
                agent_name=handle.agent_name,
                is_error=True,
                errors=[str(exc)],
            )
            raise
