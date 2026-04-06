# Agent Orchestrator

Manages Claude Agent SDK and Claude Code CLI agents with parallel execution, auto-routing, and structured output.

## Setup

```bash
pip install -e .
```

Requires `claude` CLI on PATH and a valid Anthropic API key for SDK mode.

## Usage

### List available agents

```bash
python dispatcher.py agents
```

### Run a single task

```bash
# Auto-select agent and mode
python dispatcher.py run "find all API endpoints in the codebase"

# Specify agent
python dispatcher.py run --agent security "audit the authentication module"

# Force SDK mode
python dispatcher.py run --agent research --mode sdk "explain the deployment pipeline"

# Force CLI mode with custom working directory
python dispatcher.py -d /path/to/project run --agent linting "clean up unused imports"
```

### Run parallel tasks

```bash
python dispatcher.py parallel '[
  {"task": "audit for SQL injection", "agent": "security"},
  {"task": "review the latest diff", "agent": "code-review"},
  {"task": "find dead code", "agent": "linting"}
]'
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--agents-dir` | `.claude/agents` | Path to agent prompt .md files |
| `--working-dir, -d` | `.` | Working directory for agents |
| `--max-turns` | `50` | Max conversation turns per agent |

## Agents

| Name | Mode | Description |
|------|------|-------------|
| research | SDK | Documentation lookup, codebase exploration |
| linting | CLI | Dead code removal, convention enforcement |
| testing | CLI | Write and run pytest tests |
| security | SDK | Vulnerability scanning, secrets detection |
| code-review | SDK | Diff review against guidelines |
| infrastructure | CLI | AWS infrastructure management |

## Execution Modes

- **SDK mode**: Uses `claude_agent_sdk.query()` with scoped tools. Best for read-only analysis.
- **CLI mode**: Spawns `claude --print` subprocess. Best for write-heavy coding tasks.
- **Auto mode**: SDK for analysis agents (research, security, code-review), CLI for write agents (linting, testing, infrastructure).

## Programmatic Usage

```python
import asyncio
from pathlib import Path
from orchestrator import Orchestrator

async def main():
    orch = Orchestrator(agents_dir=Path(".claude/agents"), default_working_dir=".")

    # Single dispatch
    handle = await orch.dispatch("find all TODO comments", agent_name="research")
    result = await orch.wait_for(handle.task_id)
    print(result.full_output)

    # Parallel dispatch
    handles = await orch.dispatch_parallel([
        {"task": "audit auth module", "agent": "security"},
        {"task": "review recent changes", "agent": "code-review"},
    ])
    results = await orch.wait_all([h.task_id for h in handles])

    # Cancel a running task
    handle = await orch.dispatch("run all tests", agent_name="testing")
    await orch.cancel(handle.task_id)

    # Check status
    print(orch.list_tasks())

asyncio.run(main())
```

## Output

All CLI output is JSON. Single tasks produce an object, parallel tasks produce an array.

```json
{
  "agent": "security",
  "mode": "sdk",
  "output": "### Critical\n...",
  "tool_uses": ["Grep(['pattern'])", "Read(['file_path'])"],
  "session_id": "abc123",
  "cost_usd": 0.0042,
  "duration_ms": 15200,
  "turns": 6,
  "is_error": false,
  "errors": []
}
```
