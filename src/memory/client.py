from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from memory.embedding import EmbeddingProvider
from memory.enrichment import enrich_context as build_enriched_context
from memory.emotions import EmotionAnalyzer
from memory.fact_extraction import _content_fingerprint
from memory.models import (
    ActiveState,
    ActiveStateKind,
    ActiveStateStatus,
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    Correction,
    CorrectionKind,
    DecisionOutcome,
    DecisionOutcomeKind,
    DecisionOutcomeStatus,
    Directive,
    DirectiveKind,
    DirectiveScope,
    DirectiveStatus,
    Episode,
    EpisodeRole,
    Fact,
    FactCategory,
    FactHistory,
    FactOperation,
    Pattern,
    PatternType,
    Platform,
    Session,
    TimelineEvent,
    TimelineEventKind,
)
from memory.transport import MemoryTransport


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_batch_message(message: dict[str, Any], default_platform: str) -> dict[str, Any] | None:
    role = str(message.get("role") or "").strip().lower()
    if role not in {EpisodeRole.USER.value, EpisodeRole.ASSISTANT.value}:
        return None
    content = str(message.get("content") or "").strip()
    if not content:
        return None
    normalized = dict(message)
    normalized["role"] = role
    normalized["content"] = content
    normalized["platform"] = str(message.get("platform", default_platform) or default_platform)
    return normalized


class MemoryClient:
    """Main client that orchestrates memory operations."""

    def __init__(self, transport: MemoryTransport, embedding: EmbeddingProvider, emotions: EmotionAnalyzer) -> None:
        self.transport = transport
        self.embedding = embedding
        self.emotions = emotions

    async def start_session(self, platform: str = "local", agent_namespace: str | None = None) -> Session:
        session = Session(
            agent_namespace=agent_namespace,
            platform=Platform(platform),
            started_at=_utcnow(),
            message_count=0,
            user_message_count=0,
            topics=[],
            dominant_emotions=[],
            dominant_emotion_counts={},
        )
        return await self.transport.insert_session(session)

    async def end_session(self, session_id: str, summary: str | None = None) -> Session:
        updates: dict[str, Any] = {"ended_at": _utcnow()}
        if summary is not None:
            updates["summary"] = summary
            updates["summary_embedding"] = await self.embedding.embed_text(summary)
        return await self.transport.update_session(session_id, updates)

    async def delete_session(self, session_id: str) -> bool:
        return await self.transport.delete_session(session_id)

    async def store_message(
        self,
        session_id: str,
        role: str,
        content: str,
        platform: str = "local",
        agent_namespace: str | None = None,
    ) -> Episode:
        embedding = await self.embedding.embed_text(content)
        emotion_profile = self.emotions.analyze(content)
        episode = Episode(
            session_id=session_id,
            agent_namespace=agent_namespace,
            role=EpisodeRole(role),
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            embedding=embedding,
            platform=Platform(platform),
            message_metadata={},
            emotions=emotion_profile.scores,
            dominant_emotion=emotion_profile.dominant_emotion,
            emotional_intensity=emotion_profile.intensity,
            message_timestamp=_utcnow(),
        )
        stored = await self.transport.insert_episode(episode)
        await self._update_session_stats(session_id, [stored])
        return stored

    async def store_messages_batch(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        platform: str = "local",
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        if not messages:
            return []
        normalized_messages = [
            normalized
            for message in messages
            if isinstance(message, dict)
            for normalized in [_normalize_batch_message(message, platform)]
            if normalized is not None
        ]
        if not normalized_messages:
            return []
        contents = [message["content"] for message in normalized_messages]
        embeddings = await self.embedding.embed_texts(contents)
        episodes: list[Episode] = []
        for message, embedding in zip(normalized_messages, embeddings, strict=True):
            emotion_profile = self.emotions.analyze(message["content"])
            message_metadata = message.get("message_metadata") or {}
            episodes.append(
                Episode(
                    session_id=session_id,
                    agent_namespace=agent_namespace,
                    role=EpisodeRole(str(message["role"])),
                    content=message["content"],
                    content_hash=hashlib.sha256(message["content"].encode("utf-8")).hexdigest(),
                    embedding=embedding,
                    platform=Platform(str(message.get("platform", platform))),
                    message_metadata=dict(message_metadata),
                    emotions=emotion_profile.scores,
                    dominant_emotion=emotion_profile.dominant_emotion,
                    emotional_intensity=emotion_profile.intensity,
                    message_timestamp=message.get("message_timestamp", _utcnow()),
                )
            )

        stored = [await self.transport.insert_episode(episode) for episode in episodes]
        await self._update_session_stats(session_id, stored)
        return stored

    async def add_fact(
        self,
        content: str,
        category: str,
        confidence: float = 1.0,
        tags: list[str] | None = None,
        agent_namespace: str | None = None,
    ) -> Fact:
        now = _utcnow()
        fact = Fact(
            agent_namespace=agent_namespace,
            content=content,
            category=FactCategory(category),
            confidence=confidence,
            event_time=now,
            transaction_time=now,
            is_active=True,
            source_episode_ids=[],
            access_count=0,
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        stored = await self.transport.insert_fact(fact)
        if stored.id is None:
            raise ValueError("Transport returned a fact without an id.")
        history = FactHistory(
            agent_namespace=agent_namespace,
            fact_id=stored.id,
            operation=FactOperation.ADD,
            new_content=stored.content,
            new_category=stored.category,
            event_time=now,
            transaction_time=now,
            reason="initial insert",
        )
        await self.transport.insert_fact_history(history)
        return stored

    async def update_fact(self, fact_id: str, new_content: str, reason: str | None = None) -> Fact:
        existing = await self.transport.get_fact(fact_id)
        if existing is None:
            raise ValueError(f"Fact {fact_id} was not found.")
        now = _utcnow()
        updated = await self.transport.update_fact(
            fact_id,
            {
                "content": new_content,
                "content_fingerprint": _content_fingerprint(existing.category, new_content),
                "transaction_time": now,
                "updated_at": now,
            },
        )
        await self.transport.insert_fact_history(
            FactHistory(
                agent_namespace=existing.agent_namespace,
                fact_id=updated.id or existing.id,
                operation=FactOperation.UPDATE,
                old_content=existing.content,
                new_content=updated.content,
                old_category=existing.category,
                new_category=updated.category,
                event_time=now,
                transaction_time=now,
                reason=reason,
            )
        )
        return updated

    async def delete_fact(self, fact_id: str, reason: str | None = None) -> None:
        existing = await self.transport.get_fact(fact_id)
        if existing is None:
            raise ValueError(f"Fact {fact_id} was not found.")
        now = _utcnow()
        await self.transport.deactivate_fact(fact_id)
        await self.transport.insert_fact_history(
            FactHistory(
                agent_namespace=existing.agent_namespace,
                fact_id=existing.id,
                operation=FactOperation.DELETE,
                old_content=existing.content,
                old_category=existing.category,
                event_time=now,
                transaction_time=now,
                reason=reason,
            )
        )

    async def search_facts(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[Fact]:
        return await self.transport.search_facts(
            category=category,
            tags=tags,
            limit=limit,
            agent_namespace=agent_namespace,
        )

    async def upsert_active_state(self, state: ActiveState) -> ActiveState:
        return await self.transport.upsert_active_state(state)

    async def add_active_state(
        self,
        *,
        kind: str,
        content: str,
        state_key: str,
        title: str | None = None,
        status: str = "active",
        confidence: float = 0.7,
        priority_score: float = 0.5,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        last_observed_at: datetime | None = None,
        source_episode_ids: list[Any] | None = None,
        source_session_ids: list[Any] | None = None,
        supporting_fact_ids: list[Any] | None = None,
        tags: list[str] | None = None,
        agent_namespace: str | None = None,
    ) -> ActiveState:
        now = _utcnow()
        normalized_content = str(content or "").strip()
        if not normalized_content:
            raise ValueError("Active state content cannot be empty.")
        state = ActiveState(
            agent_namespace=agent_namespace,
            kind=ActiveStateKind(kind),
            title=title,
            content=normalized_content,
            content_hash=hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
            state_key=str(state_key),
            status=ActiveStateStatus(status),
            confidence=confidence,
            priority_score=priority_score,
            valid_from=valid_from or now,
            valid_until=valid_until,
            last_observed_at=last_observed_at or now,
            source_episode_ids=list(source_episode_ids or []),
            source_session_ids=list(source_session_ids or []),
            supporting_fact_ids=list(supporting_fact_ids or []),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
        )
        return await self.transport.upsert_active_state(state)

    async def list_active_state(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[ActiveState]:
        return await self.transport.list_active_state(
            limit=limit,
            agent_namespace=agent_namespace,
            statuses=statuses,
        )

    async def upsert_directive(self, directive: Directive) -> Directive:
        return await self.transport.upsert_directive(directive)

    async def add_directive(
        self,
        *,
        kind: str,
        content: str,
        directive_key: str,
        title: str | None = None,
        scope: str = "global",
        status: str = "active",
        confidence: float = 0.85,
        priority_score: float = 1.0,
        source_episode_ids: list[Any] | None = None,
        source_session_ids: list[Any] | None = None,
        tags: list[str] | None = None,
        last_observed_at: datetime | None = None,
        agent_namespace: str | None = None,
    ) -> Directive:
        now = _utcnow()
        normalized_content = str(content or "").strip()
        if not normalized_content:
            raise ValueError("Directive content cannot be empty.")
        directive = Directive(
            agent_namespace=agent_namespace,
            kind=DirectiveKind(kind),
            scope=DirectiveScope(scope),
            title=title,
            content=normalized_content,
            content_hash=hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
            directive_key=str(directive_key),
            status=DirectiveStatus(status),
            confidence=confidence,
            priority_score=priority_score,
            source_episode_ids=list(source_episode_ids or []),
            source_session_ids=list(source_session_ids or []),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
            last_observed_at=last_observed_at or now,
        )
        return await self.transport.upsert_directive(directive)

    async def list_directives(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Directive]:
        return await self.transport.list_directives(
            limit=limit,
            agent_namespace=agent_namespace,
            statuses=statuses,
        )

    async def upsert_timeline_event(self, event: TimelineEvent) -> TimelineEvent:
        return await self.transport.upsert_timeline_event(event)

    async def add_timeline_event(
        self,
        *,
        summary: str,
        event_key: str,
        event_time: datetime,
        kind: str = "session_summary",
        title: str | None = None,
        session_id: Any | None = None,
        source_episode_ids: list[Any] | None = None,
        tags: list[str] | None = None,
        importance_score: float = 0.6,
        agent_namespace: str | None = None,
    ) -> TimelineEvent:
        now = _utcnow()
        normalized_summary = str(summary or "").strip()
        if not normalized_summary:
            raise ValueError("Timeline event summary cannot be empty.")
        event = TimelineEvent(
            agent_namespace=agent_namespace,
            kind=TimelineEventKind(kind),
            title=title,
            summary=normalized_summary,
            event_key=str(event_key),
            event_time=event_time,
            session_id=session_id,
            source_episode_ids=list(source_episode_ids or []),
            tags=list(tags or []),
            importance_score=importance_score,
            created_at=now,
            updated_at=now,
        )
        return await self.transport.upsert_timeline_event(event)

    async def list_timeline_events(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
    ) -> list[TimelineEvent]:
        return await self.transport.list_timeline_events(
            limit=limit,
            agent_namespace=agent_namespace,
        )

    async def upsert_decision_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome:
        return await self.transport.upsert_decision_outcome(outcome)

    async def add_decision_outcome(
        self,
        *,
        decision: str,
        outcome: str,
        outcome_key: str,
        event_time: datetime,
        kind: str = "other",
        title: str | None = None,
        lesson: str | None = None,
        status: str = "open",
        confidence: float = 0.75,
        importance_score: float = 0.6,
        session_id: Any | None = None,
        source_episode_ids: list[Any] | None = None,
        tags: list[str] | None = None,
        agent_namespace: str | None = None,
    ) -> DecisionOutcome:
        now = _utcnow()
        normalized_decision = str(decision or "").strip()
        normalized_outcome = str(outcome or "").strip()
        if not normalized_decision:
            raise ValueError("Decision outcome decision cannot be empty.")
        if not normalized_outcome:
            raise ValueError("Decision outcome outcome cannot be empty.")
        record = DecisionOutcome(
            agent_namespace=agent_namespace,
            kind=DecisionOutcomeKind(kind),
            title=title,
            decision=normalized_decision,
            outcome=normalized_outcome,
            lesson=(str(lesson).strip() if lesson else None),
            outcome_key=str(outcome_key),
            status=DecisionOutcomeStatus(status),
            confidence=confidence,
            importance_score=importance_score,
            event_time=event_time,
            session_id=session_id,
            source_episode_ids=list(source_episode_ids or []),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
        )
        return await self.transport.upsert_decision_outcome(record)

    async def list_decision_outcomes(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[DecisionOutcome]:
        return await self.transport.list_decision_outcomes(
            limit=limit,
            agent_namespace=agent_namespace,
            statuses=statuses,
        )

    async def upsert_pattern(self, pattern: Pattern) -> Pattern:
        return await self.transport.upsert_pattern(pattern)

    async def add_pattern(
        self,
        *,
        pattern_type: str,
        statement: str,
        pattern_key: str,
        first_observed_at: datetime,
        last_observed_at: datetime,
        description: str | None = None,
        confidence: float = 0.75,
        frequency_score: float = 0.5,
        impact_score: float = 0.5,
        supporting_episode_ids: list[Any] | None = None,
        supporting_session_ids: list[Any] | None = None,
        counterexample_episode_ids: list[Any] | None = None,
        tags: list[str] | None = None,
        agent_namespace: str | None = None,
    ) -> Pattern:
        now = _utcnow()
        normalized_statement = str(statement or "").strip()
        if not normalized_statement:
            raise ValueError("Pattern statement cannot be empty.")
        record = Pattern(
            agent_namespace=agent_namespace,
            pattern_type=PatternType(pattern_type),
            statement=normalized_statement,
            description=(str(description).strip() if description else None),
            pattern_key=str(pattern_key),
            confidence=confidence,
            frequency_score=frequency_score,
            impact_score=impact_score,
            first_observed_at=first_observed_at,
            last_observed_at=last_observed_at,
            supporting_episode_ids=list(supporting_episode_ids or []),
            supporting_session_ids=list(supporting_session_ids or []),
            counterexample_episode_ids=list(counterexample_episode_ids or []),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
        )
        return await self.transport.upsert_pattern(record)

    async def list_patterns(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        pattern_types: list[str] | None = None,
    ) -> list[Pattern]:
        return await self.transport.list_patterns(
            limit=limit,
            agent_namespace=agent_namespace,
            pattern_types=pattern_types,
        )

    async def upsert_commitment(self, commitment: Commitment) -> Commitment:
        return await self.transport.upsert_commitment(commitment)

    async def add_commitment(
        self,
        *,
        kind: str,
        statement: str,
        commitment_key: str,
        first_committed_at: datetime,
        last_observed_at: datetime,
        status: str = "open",
        confidence: float = 0.8,
        priority_score: float = 0.7,
        source_episode_ids: list[Any] | None = None,
        source_session_ids: list[Any] | None = None,
        tags: list[str] | None = None,
        agent_namespace: str | None = None,
    ) -> Commitment:
        now = _utcnow()
        normalized_statement = str(statement or "").strip()
        if not normalized_statement:
            raise ValueError("Commitment statement cannot be empty.")
        record = Commitment(
            agent_namespace=agent_namespace,
            kind=CommitmentKind(kind),
            statement=normalized_statement,
            commitment_key=str(commitment_key),
            status=CommitmentStatus(status),
            confidence=confidence,
            priority_score=priority_score,
            first_committed_at=first_committed_at,
            last_observed_at=last_observed_at,
            source_episode_ids=list(source_episode_ids or []),
            source_session_ids=list(source_session_ids or []),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
        )
        return await self.transport.upsert_commitment(record)

    async def list_commitments(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Commitment]:
        return await self.transport.list_commitments(
            limit=limit,
            agent_namespace=agent_namespace,
            statuses=statuses,
        )

    async def upsert_correction(self, correction: Correction) -> Correction:
        return await self.transport.upsert_correction(correction)

    async def add_correction(
        self,
        *,
        kind: str,
        statement: str,
        correction_key: str,
        first_observed_at: datetime,
        last_observed_at: datetime,
        target_text: str | None = None,
        active: bool = True,
        confidence: float = 0.9,
        source_episode_ids: list[Any] | None = None,
        source_session_ids: list[Any] | None = None,
        tags: list[str] | None = None,
        agent_namespace: str | None = None,
    ) -> Correction:
        now = _utcnow()
        normalized_statement = str(statement or "").strip()
        if not normalized_statement:
            raise ValueError("Correction statement cannot be empty.")
        record = Correction(
            agent_namespace=agent_namespace,
            kind=CorrectionKind(kind),
            statement=normalized_statement,
            target_text=(str(target_text).strip() if target_text else None),
            correction_key=str(correction_key),
            active=active,
            confidence=confidence,
            first_observed_at=first_observed_at,
            last_observed_at=last_observed_at,
            source_episode_ids=list(source_episode_ids or []),
            source_session_ids=list(source_session_ids or []),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
        )
        return await self.transport.upsert_correction(record)

    async def list_corrections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        active_only: bool = True,
    ) -> list[Correction]:
        return await self.transport.list_corrections(
            limit=limit,
            agent_namespace=agent_namespace,
            active_only=active_only,
        )

    async def search_memory(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        return await self.transport.search_episodes(
            query=query,
            limit=limit,
            platform=platform,
            days_back=days_back,
            agent_namespace=agent_namespace,
        )

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        return await self.transport.list_recent_episodes(
            limit=limit,
            platform=platform,
            exclude_session_id=exclude_session_id,
            agent_namespace=agent_namespace,
        )

    async def enrich_context(
        self,
        user_message: str,
        platform: str = "local",
        active_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> str:
        return await build_enriched_context(
            self.transport,
            user_message,
            platform=platform,
            active_session_id=active_session_id,
            agent_namespace=agent_namespace,
        )

    async def health_check(self) -> bool:
        return await self.transport.health_check()

    async def _update_session_stats(self, session_id: str, episodes: list[Episode]) -> None:
        session = await self.transport.get_session(session_id)
        if session is None:
            return

        new_message_count = session.message_count + len(episodes)
        user_increments = sum(1 for episode in episodes if episode.role is EpisodeRole.USER)
        new_user_count = session.user_message_count + user_increments

        previous_avg = session.avg_emotional_intensity or 0.0
        previous_total = previous_avg * session.message_count
        new_total = previous_total + sum(episode.emotional_intensity for episode in episodes)
        avg_intensity = new_total / new_message_count if new_message_count else 0.0

        dominant_counter = Counter(session.dominant_emotion_counts)
        if not dominant_counter and session.dominant_emotions:
            dominant_counter.update(session.dominant_emotions)
        dominant_counter.update(
            episode.dominant_emotion for episode in episodes if episode.dominant_emotion
        )
        top_dominant = [emotion for emotion, _ in dominant_counter.most_common(3)]
        dominant_emotion_counts = dict(dominant_counter)

        await self.transport.update_session(
            session_id,
            {
                "message_count": new_message_count,
                "user_message_count": new_user_count,
                "avg_emotional_intensity": avg_intensity,
                "dominant_emotions": top_dominant,
                "dominant_emotion_counts": dominant_emotion_counts,
            },
        )
