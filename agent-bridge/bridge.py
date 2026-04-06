from __future__ import annotations

import asyncio
import json
import logging
import signal
import uuid

import websockets.asyncio.client as ws_client
import websockets.exceptions
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from channel_tools import send_message
from config import BridgeConfig, load_config
from conversation import ConversationManager

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("agent-bridge")
log.setLevel(logging.INFO)

RECONNECT_DELAY_SECONDS = 5
HEARTBEAT_RESPONSE = "HEARTBEAT_OK"


class AgentBridge:
    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._conversations = ConversationManager(config.system_prompt_files)
        self._ws: ws_client.ClientConnection | None = None
        self._shutting_down = False
        self._pending_requests: dict[str, asyncio.Future[dict[str, object]]] = {}

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_shutdown)

        while not self._shutting_down:
            try:
                await self._connect_and_serve()
            except websockets.exceptions.ConnectionClosed:
                log.info("Gateway connection lost, reconnecting...")
            except OSError as exc:
                log.info("Connection failed: %s", exc)

            if not self._shutting_down:
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _connect_and_serve(self) -> None:
        async with ws_client.connect(self._config.gateway_url) as ws:
            self._ws = ws
            await self._authenticate(ws)
            log.info("Connected to gateway at %s", self._config.gateway_url)
            await self._message_loop(ws)

    async def _authenticate(self, ws: ws_client.ClientConnection) -> None:
        challenge_raw = await ws.recv()
        challenge = json.loads(challenge_raw)

        if challenge.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {challenge.get('event')}")

        nonce = challenge["payload"]["nonce"]
        request_id = str(uuid.uuid4())[:8]

        connect_msg = {
            "type": "req",
            "id": request_id,
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": "agent-bridge",
                    "version": "0.1.0",
                    "platform": "linux",
                    "mode": "node",
                },
                "role": "node",
                "scopes": ["operator.read", "operator.write"],
                "caps": [],
                "commands": [],
                "permissions": {},
                "auth": {"token": self._config.gateway_token},
                "device": {"nonce": nonce},
            },
        }
        await ws.send(json.dumps(connect_msg))

        response_raw = await ws.recv()
        response = json.loads(response_raw)

        if not response.get("ok"):
            error = response.get("error", {})
            raise RuntimeError(f"Auth failed: {error.get('message', 'unknown')}")

        log.info("Authenticated with gateway (protocol %s)", response["payload"].get("protocol"))

    async def _message_loop(self, ws: ws_client.ClientConnection) -> None:
        async for raw in ws:
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "event":
                asyncio.create_task(self._handle_event(data, ws))
            elif msg_type == "res":
                self._resolve_pending(data)

    async def _handle_event(
        self,
        data: dict[str, object],
        ws: ws_client.ClientConnection,
    ) -> None:
        event = data.get("event")

        if event == "chat.message":
            await self._handle_chat_message(data, ws)
        elif event == "heartbeat":
            await self._handle_heartbeat(data, ws)

    async def _handle_chat_message(
        self,
        data: dict[str, object],
        ws: ws_client.ClientConnection,
    ) -> None:
        payload: dict[str, object] = data.get("payload", {})  # type: ignore[assignment]
        session_key = str(payload.get("sessionKey", "agent:main:chat"))
        channel = str(payload.get("channel", ""))
        sender: dict[str, str] = payload.get("sender", {})  # type: ignore[assignment]
        content: dict[str, str] = payload.get("content", {})  # type: ignore[assignment]
        text = content.get("text", "")

        if not text:
            return

        sender_prefix = ""
        if sender.get("name"):
            sender_prefix = f"[{sender['name']} via {channel}] "

        self._conversations.get_or_create_session(session_key).add_user_message(
            content=f"{sender_prefix}{text}",
            channel=channel,
            sender_id=sender.get("id", ""),
            sender_name=sender.get("name", ""),
        )

        response_text = await self._run_agent(session_key)

        self._conversations.get_or_create_session(session_key).add_assistant_message(response_text)

        await send_message(
            ws,
            channel=channel,
            target=sender.get("id", ""),
            message=response_text,
            session_key=session_key,
        )

    async def _handle_heartbeat(
        self,
        data: dict[str, object],
        ws: ws_client.ClientConnection,
    ) -> None:
        payload: dict[str, object] = data.get("payload", {})  # type: ignore[assignment]
        session_key = str(payload.get("sessionKey", "agent:main:heartbeat"))
        prompt = str(payload.get("prompt", ""))

        if not prompt:
            prompt = "Check HEARTBEAT.md if it exists. If nothing needs attention, reply HEARTBEAT_OK."

        self._conversations.get_or_create_session(session_key).add_user_message(content=prompt)
        response_text = await self._run_agent(session_key)
        self._conversations.get_or_create_session(session_key).add_assistant_message(response_text)

        target_channel = str(payload.get("target", ""))
        target_to = str(payload.get("to", ""))

        is_heartbeat_ok = HEARTBEAT_RESPONSE in response_text and len(response_text) <= 300

        if not is_heartbeat_ok and target_channel and target_to:
            await send_message(
                ws,
                channel=target_channel,
                target=target_to,
                message=response_text,
                session_key=session_key,
            )

    async def _run_agent(self, session_key: str) -> str:
        system_prompt, messages = self._conversations.get_prompt_context(session_key)

        last_user_msg = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=str(self._config.workspace),
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Glob", "Grep", "Bash", "Edit", "Write", "WebFetch", "WebSearch"],
            max_turns=30,
        )

        output_chunks: list[str] = []

        async for message in query(prompt=last_user_msg, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        output_chunks.append(block.text)
            elif isinstance(message, ResultMessage) and message.is_error and message.errors:
                    return f"Error: {'; '.join(message.errors)}"

        return "\n".join(output_chunks) if output_chunks else "No response generated."

    def _resolve_pending(self, data: dict[str, object]) -> None:
        request_id = str(data.get("id", ""))
        if request_id in self._pending_requests:
            self._pending_requests[request_id].set_result(data)  # type: ignore[arg-type]

    def _request_shutdown(self) -> None:
        log.info("Shutdown requested")
        self._shutting_down = True


async def main() -> None:
    config = load_config()
    bridge = AgentBridge(config)
    await bridge.run()


if __name__ == "__main__":
    asyncio.run(main())
