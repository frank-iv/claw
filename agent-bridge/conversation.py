from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Message:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    channel: str = ""
    sender_id: str = ""
    sender_name: str = ""


@dataclass
class Session:
    session_key: str
    messages: list[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def add_user_message(
        self,
        content: str,
        channel: str = "",
        sender_id: str = "",
        sender_name: str = "",
    ) -> None:
        self.messages.append(Message(
            role="user",
            content=content,
            channel=channel,
            sender_id=sender_id,
            sender_name=sender_name,
        ))
        self.last_active = time.time()

    def add_assistant_message(self, content: str) -> None:
        self.messages.append(Message(role="assistant", content=content))
        self.last_active = time.time()

    def to_prompt_messages(self) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


MAX_CONTEXT_MESSAGES = 100
COMPACTION_TARGET = 60


class ConversationManager:
    def __init__(self, system_prompt_paths: list[Path]) -> None:
        self._system_prompt_paths = system_prompt_paths
        self._sessions: dict[str, Session] = {}
        self._system_prompt_cache: str | None = None
        self._system_prompt_loaded_at: float = 0.0

    @property
    def system_prompt(self) -> str:
        now = time.time()
        if self._system_prompt_cache is None or (now - self._system_prompt_loaded_at) > 300:
            self._system_prompt_cache = self._load_system_prompt()
            self._system_prompt_loaded_at = now
        return self._system_prompt_cache

    def get_or_create_session(self, session_key: str) -> Session:
        if session_key not in self._sessions:
            self._sessions[session_key] = Session(session_key=session_key)
        return self._sessions[session_key]

    def get_prompt_context(self, session_key: str) -> tuple[str, list[dict[str, str]]]:
        session = self.get_or_create_session(session_key)
        if session.message_count > MAX_CONTEXT_MESSAGES:
            self._compact(session)
        return self.system_prompt, session.to_prompt_messages()

    def _compact(self, session: Session) -> None:
        kept_messages = session.messages[-COMPACTION_TARGET:]
        summary_text = f"[Prior conversation of {session.message_count - COMPACTION_TARGET} messages compacted]"
        summary = Message(role="user", content=summary_text, timestamp=kept_messages[0].timestamp)
        session.messages = [summary] + kept_messages

    def _load_system_prompt(self) -> str:
        parts: list[str] = []
        for path in self._system_prompt_paths:
            if path.exists():
                content = path.read_text().strip()
                if content:
                    parts.append(content)
        return "\n\n---\n\n".join(parts) if parts else "You are a helpful assistant."

    def remove_session(self, session_key: str) -> None:
        self._sessions.pop(session_key, None)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())
