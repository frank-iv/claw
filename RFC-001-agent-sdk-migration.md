# RFC-001: Migrate OpenClaw Agent to Claude Agent SDK

**Status:** Draft

## Decisions (Frank, Apr 6)
- **Active channels:** Signal (primary), Telegram (fully operational)
- **Browser automation:** Full browser automation required (not just HTTP fetches)
- **Orchestrator dispatch:** Keep exec-based dispatch (not Python imports)
**Date:** 2026-04-06
**Author:** Shawty (PM Agent)

---

## Problem Statement

The current OpenClaw agent process (`openclaw-agent` / `pi-embedded` runtime) consumes ~270MB+ of memory to run the main agent loop. It bundles a 96K-line session manager, 40K-line agent runtime, 29K-line gateway, and 80+ tool plugins -- most of which we don't use. We already built an `agent-orchestrator/` that dispatches sub-agents via the Claude Agent SDK (23MB per call). The question is whether the SDK can replace the main agent process itself, not just sub-agents.

## Current Architecture

```
Signal/Telegram/WhatsApp
        |
        v
┌──────────────────────────┐
│  openclaw-gateway         │  (Node.js, ws://127.0.0.1:18789)
│  - Channel plugins        │  - 23 channel types loaded
│  - Session management     │  - 96K lines of session code
│  - Authentication         │  - Rate limiting, pairing
│  - Tool plugin registry   │  - 80+ tools
└──────────┬───────────────┘
           |
           v
┌──────────────────────────┐
│  openclaw-agent           │  (~270MB resident)
│  - Pi embedded runtime    │  - 40K-line agent loop
│  - Anthropic API calls    │  - Streaming + block replies
│  - Tool execution         │  - exec, fs, browser, search
│  - Context compaction     │  - Token tracking, pruning
│  - Memory flushing        │  - Disk-backed sessions
└──────────────────────────┘
           |
           v
┌──────────────────────────┐
│  agent-orchestrator       │  (Python, ~1K lines)
│  - SDK mode (read-only)   │  - claude_agent_sdk.query()
│  - CLI mode (write tasks) │  - claude --print subprocess
│  - 7 agent definitions    │  - Auto-select by keywords
│  - Async task tracking    │  - ~23MB per dispatch
└──────────────────────────┘
```

**What the gateway actually does that we need:**
1. Receives webhooks/socket messages from Signal, Telegram, WhatsApp
2. Normalizes channel-specific formats into a common message structure
3. Routes messages to the correct agent/session
4. Delivers agent replies back to the originating channel
5. Manages pairing/approval for new contacts
6. Heartbeat polling, cron scheduling

**What the agent process does that we need:**
1. Receives normalized messages from gateway
2. Calls Claude API with conversation history + tools
3. Executes tool calls (exec, fs, browser, web search)
4. Streams responses back to gateway for delivery
5. Manages session context (compaction, memory flushing)

## Key Analysis

### Q1: Can the Agent SDK fully replace openclaw-agent?

**Yes, for the agent loop. No, for channel routing.**

The Claude Agent SDK (`claude_agent_sdk.query()`) can:
- Send prompts with system instructions and conversation history
- Stream responses with tool use
- Execute tools via Claude Code's built-in tool set (Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch, Agent subagents)
- Manage sessions (`get_session_messages`, `fork_session`, `list_sessions`)
- Respect budgets (`TaskBudget`) and permissions (`PermissionMode`)
- Run hooks (pre/post tool use, permission requests, notifications)

The SDK **cannot**:
- Listen on webhooks for incoming channel messages
- Parse Signal/Telegram/WhatsApp protocol formats
- Deliver replies to specific channels
- Handle DM pairing, rate limiting, allowlists

**Conclusion:** The gateway (or a thin replacement) must stay for channel I/O. The agent runtime can be replaced.

### Q2: What OpenClaw components are still needed vs replaceable?

| Component | Status | Rationale |
|-----------|--------|-----------|
| **Gateway channel plugins** | KEEP | Signal/Telegram/WhatsApp protocol handling is non-trivial. Rewriting is months of work for no gain. |
| **Gateway WebSocket server** | KEEP | Channels need a persistent listener. The gateway already does this. |
| **Gateway message routing** | KEEP | Routes messages to correct session/agent. |
| **Gateway reply delivery** | KEEP | Formats and sends replies back to channels. |
| **Agent runner (pi-embedded)** | REPLACE | This is what the SDK replaces. The 40K-line agent loop becomes `claude_agent_sdk.query()`. |
| **Session manager (96K lines)** | REPLACE | SDK has `get_session_messages`, `fork_session`, session persistence. |
| **Tool plugin system** | REPLACE | SDK's built-in tools (Bash, Read, Write, etc.) plus MCP servers for extensions. |
| **Heartbeat/cron system** | KEEP (gateway) | Gateway dispatches heartbeats. The handler changes from pi-embedded to SDK. |
| **Memory/compaction** | PARTIAL REPLACE | SDK handles context windows. We keep our MEMORY.md file-based system. |

### Q3: What is the migration path?

Incremental, three phases. The key insight: OpenClaw gateway communicates with agents via WebSocket RPC. We can build a **bridge process** that receives gateway RPC calls and translates them into `claude_agent_sdk.query()` calls, then returns results in the format the gateway expects.

### Q4: What are the risks?

| Risk | Severity | Mitigation |
|------|----------|------------|
| Gateway RPC protocol is undocumented | HIGH | Reverse-engineer from `call-h8FrADrE.js` and `agent-runner.runtime-CJ-tudLz.js`. Sniff traffic on ws://127.0.0.1:18789. |
| SDK tool set differs from OpenClaw tools | MEDIUM | Map OpenClaw tools to SDK equivalents. Missing tools (camera, canvas, device) are unused on this server anyway. |
| Session format incompatibility | MEDIUM | SDK sessions vs OpenClaw sessions have different schemas. Build a thin adapter or start fresh. |
| Streaming format differences | MEDIUM | Gateway expects block-reply chunks. SDK streams `StreamEvent`s. Bridge must translate. |
| Channel message delivery timing | LOW | Gateway handles delivery. Bridge just needs to return the response text. |
| Claude Max OAuth vs API key | LOW | SDK already uses Claude Max OAuth on this server. Confirmed working. |
| Concurrent message handling | MEDIUM | OpenClaw gateway queues messages per-lane. Need to ensure bridge handles this correctly. |

## Proposed Architecture

```
Signal/Telegram/WhatsApp
        |
        v
┌──────────────────────────┐
│  openclaw-gateway         │  KEPT AS-IS
│  - Channel plugins        │  (handles all channel I/O)
│  - Message routing        │
│  - Reply delivery         │
│  - Heartbeat/cron         │
└──────────┬───────────────┘
           |
           | WebSocket RPC (existing protocol)
           v
┌──────────────────────────────────────────┐
│  agent-bridge (NEW - Python, ~500 lines) │
│                                          │
│  - WS client to gateway                  │
│  - Translates RPC → SDK calls            │
│  - Manages conversation history          │
│  - Loads system prompt (SOUL.md, etc.)   │
│  - Dispatches to orchestrator for        │
│    sub-agent work                        │
│  - Returns responses to gateway          │
│                                          │
│  Estimated memory: ~40-60MB              │
│  (Python process + SDK client overhead)  │
└──────────┬───────────────────────────────┘
           |
           v
┌──────────────────────────┐
│  claude_agent_sdk.query() │  (~23MB per call)
│  - Built-in tools         │
│  - MCP server extensions  │
│  - Session management     │
│  - Hooks                  │
└──────────────────────────┘
           |
           v
┌──────────────────────────┐
│  agent-orchestrator       │  (existing, unchanged)
│  - Sub-agent dispatch     │
│  - SDK/CLI mode routing   │
└──────────────────────────┘
```

**Net memory savings:** ~270MB (openclaw-agent) replaced by ~40-60MB (bridge) = **~200MB saved**.

### Alternative: Gateway-less Architecture

Instead of keeping the gateway, build channel adapters directly:

```
Signal  ──→ signal-adapter.py  ──→ agent-bridge ──→ SDK
Telegram ─→ telegram-adapter.py ─→ agent-bridge ──→ SDK
WhatsApp ─→ whatsapp-adapter.py ─→ agent-bridge ──→ SDK
```

**Pros:** Eliminates gateway entirely (~380MB Node.js process gone). Full control.
**Cons:** Must reimplement Signal protocol (libsignal), Telegram Bot API, WhatsApp Business API. Months of work. The gateway already does this well.

**Recommendation:** Keep the gateway. Replace only the agent runtime.

## Implementation Phases

### Phase 1: Protocol Discovery & Bridge Skeleton (1 week)

Reverse-engineer the gateway-to-agent RPC protocol so the bridge knows exactly what messages to expect and what format to return.

**Tasks:**

1.1. **Sniff gateway-agent WebSocket traffic**
- Scope: Capture and document the RPC message format between gateway and agent-runner
- Method: Add a WebSocket proxy or tap the existing connection, send test messages from each channel
- Deliverable: Protocol spec document (message types, fields, sequence diagrams)
- Dependencies: None
- Complexity: Medium
- Agent: Research

1.2. **Document required RPC handlers**
- Scope: List every RPC method the gateway calls on the agent, with request/response schemas
- Key methods to find: message delivery, tool result return, heartbeat, session management
- Dependencies: 1.1
- Complexity: Small
- Agent: Research

1.3. **Build bridge skeleton**
- Scope: Python WebSocket client that connects to gateway, receives messages, returns hardcoded "echo" responses
- Validate that gateway accepts responses from the bridge
- Dependencies: 1.2
- Complexity: Medium
- Agent: Infrastructure

1.4. **Test bridge with each channel**
- Scope: Send a message from Signal, Telegram, WhatsApp. Confirm echo response arrives.
- Dependencies: 1.3
- Complexity: Small
- Agent: Testing

### Phase 2: SDK Integration (1 week)

Replace the echo handler with actual Claude Agent SDK calls.

**Tasks:**

2.1. **Implement conversation history adapter**
- Scope: Convert gateway's session transcript format to SDK-compatible message list
- Handle: system prompt injection, tool results, multi-turn context
- Dependencies: 1.3
- Complexity: Medium
- Agent: Infrastructure

2.2. **Wire SDK query into bridge message handler**
- Scope: On inbound message, build prompt from conversation history + new message, call `claude_agent_sdk.query()`, return result text to gateway
- Include: system prompt loading (SOUL.md, AGENTS.md, MEMORY.md), tool permissions, budget limits
- Dependencies: 2.1
- Complexity: Large
- Agent: Infrastructure

2.3. **Map OpenClaw tools to SDK tools**
- Scope: Audit which OpenClaw tools we actually use. Map each to SDK equivalent.
- Expected mappings:
  - `exec` / `system.run` → `Bash`
  - `fs.read` / `fs.write` → `Read` / `Write`
  - `browser` → `WebFetch` or MCP browser server
  - `send` → Custom tool that calls gateway delivery API
  - `chat.history` → `get_session_messages()`
- Dependencies: 1.2
- Complexity: Medium
- Agent: Research

2.4. **Implement `send` as custom MCP tool**
- Scope: The SDK needs a way to send messages to channels. Build an MCP server that calls the gateway's delivery endpoint.
- This is critical: without it, the agent can receive but not proactively send.
- Dependencies: 2.3
- Complexity: Medium
- Agent: Infrastructure

2.5. **Implement streaming response delivery**
- Scope: Instead of waiting for full SDK response, stream `StreamEvent`s back to gateway as block-reply chunks
- Dependencies: 2.2
- Complexity: Medium
- Agent: Infrastructure

### Phase 3: Cutover & Hardening (1 week)

Run bridge in parallel with openclaw-agent, validate, then switch.

**Tasks:**

3.1. **Shadow mode: run bridge alongside agent**
- Scope: Both bridge and openclaw-agent receive messages. Bridge logs what it would respond. Agent actually responds. Compare outputs.
- Dependencies: 2.2, 2.5
- Complexity: Medium
- Agent: Infrastructure

3.2. **Session migration**
- Scope: Decide whether to migrate existing OpenClaw sessions to SDK format or start fresh.
- Recommendation: Start fresh. OpenClaw sessions are in a proprietary JSON format with token counts, compaction metadata, etc. Not worth migrating.
- Dependencies: 2.1
- Complexity: Small (if starting fresh) / Large (if migrating)
- Agent: Infrastructure

3.3. **Heartbeat and cron integration**
- Scope: Ensure gateway heartbeats and cron triggers route to bridge correctly.
- Dependencies: 3.1
- Complexity: Small
- Agent: Infrastructure

3.4. **Kill switch: revert to openclaw-agent**
- Scope: systemd service that can swap between bridge and openclaw-agent with a single command.
- Dependencies: 3.1
- Complexity: Small
- Agent: Infrastructure

3.5. **Cutover**
- Scope: Stop openclaw-agent. Route all traffic through bridge. Monitor for 48 hours.
- Dependencies: 3.1, 3.2, 3.3, 3.4
- Complexity: Small
- Agent: Infrastructure

3.6. **Memory and performance validation**
- Scope: Measure bridge RSS, response latency, token usage. Compare to openclaw-agent baseline.
- Dependencies: 3.5
- Complexity: Small
- Agent: Testing

## Task Summary

| # | Task | Phase | Complexity | Dependencies | Agent |
|---|------|-------|------------|--------------|-------|
| 1.1 | Sniff gateway-agent WS protocol | 1 | Medium | None | Research |
| 1.2 | Document RPC handlers | 1 | Small | 1.1 | Research |
| 1.3 | Build bridge skeleton | 1 | Medium | 1.2 | Infrastructure |
| 1.4 | Test bridge with channels | 1 | Small | 1.3 | Testing |
| 2.1 | Conversation history adapter | 2 | Medium | 1.3 | Infrastructure |
| 2.2 | Wire SDK query into bridge | 2 | Large | 2.1 | Infrastructure |
| 2.3 | Map OpenClaw tools to SDK | 2 | Medium | 1.2 | Research |
| 2.4 | Build `send` MCP tool | 2 | Medium | 2.3 | Infrastructure |
| 2.5 | Streaming response delivery | 2 | Medium | 2.2 | Infrastructure |
| 3.1 | Shadow mode parallel run | 3 | Medium | 2.2, 2.5 | Infrastructure |
| 3.2 | Session migration (or reset) | 3 | Small | 2.1 | Infrastructure |
| 3.3 | Heartbeat/cron integration | 3 | Small | 3.1 | Infrastructure |
| 3.4 | Kill switch service | 3 | Small | 3.1 | Infrastructure |
| 3.5 | Cutover | 3 | Small | 3.1-3.4 | Infrastructure |
| 3.6 | Performance validation | 3 | Small | 3.5 | Testing |

## Open Questions (Need Human Input)

1. **Gateway version pinning.** The gateway at `/usr/lib/node_modules/openclaw/` is version 2026.3.31. Are you on auto-update? If the gateway updates and changes the RPC protocol, the bridge breaks. Should we pin the version?

2. **Session continuity.** Do you care about preserving existing conversation history from OpenClaw sessions, or is a clean start acceptable? Clean start is strongly recommended.

3. **Channel priority.** Which channels are actually active? If only Signal is used day-to-day, we can validate against Signal first and handle Telegram/WhatsApp later.

4. **Browser tool.** OpenClaw has a built-in Chromium browser tool. The SDK doesn't have a direct equivalent -- `WebFetch` is HTTP-only. Do you need full browser automation? If so, we'd need an MCP server wrapping Playwright or Puppeteer.

5. **Canvas/media tools.** OpenClaw bundles camera, canvas, TTS, image generation. Are any of these in use on this server? If not, we can skip mapping them entirely.

6. **Orchestrator integration.** Currently the main agent dispatches to agent-orchestrator via `exec` (running `su - coder -c 'python3 agent-orchestrator/dispatcher.py ...'`). In the new architecture, should the bridge call the orchestrator directly via Python imports, or keep the exec-based dispatch?

7. **Gateway process ownership.** After migration, the gateway is the only OpenClaw component. Are you comfortable depending on it long-term, or do you eventually want to replace it too? That's a separate RFC if so.

## Decision Record

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Keep gateway | Yes | Channel protocol handling is battle-tested and complex. Not worth rewriting. |
| Replace agent runtime | Yes | SDK does everything the agent loop does at 1/5 the memory. |
| Bridge language | Python | Matches orchestrator. SDK is Python-native. Team (Frank) knows Python. |
| Session migration | Skip | Start fresh. Proprietary format not worth adapting. |
| Tool strategy | SDK built-ins + MCP | SDK covers 90% of needs. MCP servers fill gaps (channel send, browser if needed). |
