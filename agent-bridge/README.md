# agent-bridge

Phase 1 of RFC-001: replaces the openclaw-agent runtime (~270MB) with a thin Python bridge (~40-60MB) that translates gateway WebSocket RPC into `claude_agent_sdk.query()` calls.

## Architecture

```
Signal/Telegram → openclaw-gateway (ws://127.0.0.1:18789) → agent-bridge → claude_agent_sdk.query()
```

The gateway handles channel I/O. The bridge handles agent logic.

## Components

- `bridge.py` - WebSocket client that connects to gateway, authenticates, routes messages to SDK
- `conversation.py` - Per-session message history with context window compaction
- `channel_tools.py` - Tools the agent can use: send_message (channel delivery), dispatch_agent (orchestrator)
- `config.py` - Loads gateway URL, auth token, workspace paths from openclaw.json

## Configuration

Reads `~/.openclaw/openclaw.json` for gateway host/port and workspace path. Gateway token from `OPENCLAW_GATEWAY_TOKEN` env var or config file.

## Running

```
OPENCLAW_GATEWAY_TOKEN=<token> python3 bridge.py
```

## Protocol

Implements gateway protocol v3:
1. Receives `connect.challenge` event with nonce
2. Sends `connect` request with auth token
3. Receives `hello-ok` response
4. Listens for `chat.message` and `heartbeat` events
5. Sends `chat.send` requests to deliver responses
