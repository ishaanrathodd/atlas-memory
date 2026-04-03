from __future__ import annotations

import logging
from typing import Any

from memory.client import MemoryClient
from memory.fact_extraction import extract_and_store_facts
from memory.models import Episode, Fact, Session


logger = logging.getLogger(__name__)


class MemoryBridge:
    """Hermes-facing integration layer for memory operations."""

    def __init__(self, client: MemoryClient) -> None:
        self.client = client
        self._current_session_id: str | None = None
        self._current_platform: str = "local"

    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id

    @property
    def current_platform(self) -> str:
        return self._current_platform

    async def start_conversation(self, platform: str) -> Session | None:
        """Start a new Memory session for the active Hermes conversation."""
        try:
            session = await self.client.start_session(platform=platform)
            self._current_session_id = str(session.id) if session.id is not None else None
            self._current_platform = platform
            return session
        except Exception:
            logger.warning("Memory start_conversation failed.", exc_info=True)
            return None

    async def log_turn(self, role: str, content: str) -> Episode | None:
        """Persist a conversation turn without letting memory failures bubble up."""
        try:
            if self._current_session_id is None:
                return None
            return await self.client.store_message(
                self._current_session_id,
                role,
                content,
                platform=self._current_platform,
            )
        except Exception:
            logger.warning("Memory log_turn failed.", exc_info=True)
            return None

    async def enrich_system_prompt(self, user_message: str) -> str:
        """Fetch memory context and format it for system prompt injection."""
        try:
            context = await self.build_context_enrichment(user_message)
            if not context:
                return ""
            return f"<memory>\n{context}\n</memory>"
        except Exception:
            logger.warning("Memory enrich_system_prompt failed.", exc_info=True)
            return ""

    async def build_context_enrichment(self, user_message: str) -> str:
        """Build the memory context block that Hermes can inject into its system prompt."""
        try:
            return await self.client.enrich_context(
                user_message,
                platform=self._current_platform,
                active_session_id=self._current_session_id,
            )
        except Exception:
            logger.warning("Memory build_context_enrichment failed.", exc_info=True)
            return ""

    async def extract_facts(self, conversation_text: str | list[dict[str, Any]] | list[Episode]) -> list[Fact]:
        """Extract and persist durable facts from conversation turns."""
        try:
            return await extract_and_store_facts(self.client.transport, conversation_text)
        except Exception:
            logger.warning("Memory extract_facts failed.", exc_info=True)
            return []

    async def end_conversation(self, summary: str | None) -> Session | None:
        """Close the current Memory session."""
        try:
            if self._current_session_id is None:
                return None
            session = await self.client.end_session(self._current_session_id, summary=summary)
            self._current_session_id = None
            return session
        except Exception:
            logger.warning("Memory end_conversation failed.", exc_info=True)
            return None
