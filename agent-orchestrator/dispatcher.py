from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agents import ExecutionMode
from cli_runner import CLIRunResult
from orchestrator import Orchestrator
from sdk_runner import SDKRunResult


def _format_sdk_result(result: SDKRunResult) -> dict[str, object]:
    return {
        "agent": result.agent_name,
        "mode": "sdk",
        "output": result.full_output,
        "tool_uses": result.tool_uses,
        "session_id": result.session_id,
        "cost_usd": result.total_cost_usd,
        "duration_ms": result.duration_ms,
        "turns": result.num_turns,
        "is_error": result.is_error,
        "errors": result.errors,
    }


def _format_cli_result(result: CLIRunResult) -> dict[str, object]:
    return {
        "agent": result.agent_name,
        "mode": "cli",
        "output": result.output,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "is_error": result.is_error,
        "pid": result.pid,
    }


def _format_result(result: SDKRunResult | CLIRunResult) -> dict[str, object]:
    if isinstance(result, SDKRunResult):
        return _format_sdk_result(result)
    return _format_cli_result(result)


async def _run_single(
    orchestrator: Orchestrator,
    task: str,
    agent_name: str | None,
    working_dir: str,
    force_mode: ExecutionMode | None,
    max_turns: int,
) -> None:
    handle = await orchestrator.dispatch(
        task,
        agent_name=agent_name,
        working_dir=working_dir,
        force_mode=force_mode,
        max_turns=max_turns,
    )

    sys.stderr.write(
        f"[dispatch] task={handle.task_id} agent={handle.agent_name} "
        f"mode={handle.execution_mode.value}\n"
    )

    result = await orchestrator.wait_for(handle.task_id)
    print(json.dumps(_format_result(result), indent=2))


async def _run_parallel(
    orchestrator: Orchestrator,
    tasks_json: str,
    working_dir: str,
    max_turns: int,
) -> None:
    task_specs = json.loads(tasks_json)
    handles = await orchestrator.dispatch_parallel(
        task_specs,
        working_dir=working_dir,
        max_turns=max_turns,
    )

    for h in handles:
        sys.stderr.write(
            f"[dispatch] task={h.task_id} agent={h.agent_name} "
            f"mode={h.execution_mode.value}\n"
        )

    results = await orchestrator.wait_all([h.task_id for h in handles])
    print(json.dumps([_format_result(r) for r in results], indent=2))


async def _list_agents(orchestrator: Orchestrator) -> None:
    for name in orchestrator.available_agents:
        agent = orchestrator.get_agent(name)
        print(f"{name:20s} {agent.mode_preference.value:5s}  {agent.description}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dispatch")

    parser.add_argument(
        "--agents-dir",
        default=".claude/agents",
        help="Path to agent prompt directory",
    )
    parser.add_argument(
        "--working-dir", "-d",
        default=".",
        help="Working directory for agent execution",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=100,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("task", help="Task description")
    run_p.add_argument("--agent", "-a", default=None, help="Agent name (auto-selects if omitted)")
    run_p.add_argument(
        "--mode", "-m",
        choices=["sdk", "cli"],
        default=None,
        help="Force execution mode",
    )

    parallel_p = sub.add_parser("parallel")
    parallel_p.add_argument(
        "tasks_json",
        help='JSON array: [{"task": "...", "agent": "..."}]',
    )

    sub.add_parser("agents")

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    working_dir = Path(args.working_dir).resolve()
    agents_path = Path(args.agents_dir)
    if not agents_path.is_absolute():
        agents_path = working_dir / agents_path
    agents_dir = agents_path.resolve()
    orchestrator = Orchestrator(
        agents_dir=agents_dir,
        default_working_dir=str(working_dir),
    )

    if args.command == "agents":
        asyncio.run(_list_agents(orchestrator))
        return

    if args.command == "run":
        force_mode = ExecutionMode(args.mode) if args.mode else None
        asyncio.run(
            _run_single(
                orchestrator,
                task=args.task,
                agent_name=args.agent,
                working_dir=str(Path(args.working_dir).resolve()),
                force_mode=force_mode,
                max_turns=args.max_turns,
            )
        )
        return

    if args.command == "parallel":
        asyncio.run(
            _run_parallel(
                orchestrator,
                tasks_json=args.tasks_json,
                working_dir=str(Path(args.working_dir).resolve()),
                max_turns=args.max_turns,
            )
        )
        return


if __name__ == "__main__":
    main()
