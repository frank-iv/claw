from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import websockets.asyncio.client as ws_client


@dataclass(frozen=True)
class SendResult:
    success: bool
    message_id: str
    error: str = ""


@dataclass(frozen=True)
class DispatchResult:
    task_id: str
    agent_name: str
    success: bool
    output: str = ""
    error: str = ""


async def send_message(
    ws: ws_client.ClientConnection,
    channel: str,
    target: str,
    message: str,
    session_key: str = "agent:main:chat",
) -> SendResult:
    request_id = str(uuid.uuid4())[:8]
    payload = {
        "type": "req",
        "id": request_id,
        "method": "chat.send",
        "params": {
            "sessionKey": session_key,
            "message": {"content": message},
            "deliveryTargets": [{"channel": channel, "to": target}],
        },
    }
    await ws.send(json.dumps(payload))

    async for raw in ws:
        data = json.loads(raw)
        if data.get("type") == "res" and data.get("id") == request_id:
            if data.get("ok"):
                return SendResult(
                    success=True,
                    message_id=data["payload"].get("messageId", ""),
                )
            return SendResult(
                success=False,
                message_id="",
                error=data.get("error", {}).get("message", "Unknown error"),
            )
    return SendResult(success=False, message_id="", error="Connection closed")


async def dispatch_agent(
    agent_name: str,
    task: str,
    mode: str = "sdk",
    working_dir: str = ".",
) -> DispatchResult:
    cmd = [
        "python3",
        "/root/.openclaw/workspace/agent-orchestrator/dispatcher.py",
        "--agent", agent_name,
        "--mode", mode,
        "--task", task,
        "--working-dir", working_dir,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        return DispatchResult(
            task_id=str(uuid.uuid4())[:8],
            agent_name=agent_name,
            success=True,
            output=stdout.decode(),
        )
    return DispatchResult(
        task_id=str(uuid.uuid4())[:8],
        agent_name=agent_name,
        success=False,
        error=stderr.decode() or f"Exit code {proc.returncode}",
    )
