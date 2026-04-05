from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from memory.fact_extraction import deduplicate_facts, extract_and_store_facts, extract_facts
from memory.models import Fact, FactCategory, FactHistory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryTransport:
    def __init__(self) -> None:
        self.facts: dict[str, Fact] = {}
        self.history: list[FactHistory] = []

    async def insert_fact(self, fact: Fact) -> Fact:
        if fact.id is None:
            fact = fact.model_copy(update={"id": uuid4()})
        self.facts[str(fact.id)] = fact
        return fact

    async def get_fact(self, fact_id: str) -> Fact | None:
        return self.facts.get(fact_id)

    async def update_fact(self, fact_id: str, updates: dict[str, object]) -> Fact:
        fact = self.facts[fact_id]
        updated = fact.model_copy(update=updates)
        self.facts[fact_id] = updated
        return updated

    async def search_facts(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[Fact]:
        _ = agent_namespace
        facts = [fact for fact in self.facts.values() if fact.is_active]
        if category is not None:
            facts = [fact for fact in facts if fact.category.value == category]
        if tags:
            facts = [fact for fact in facts if all(tag in fact.tags for tag in tags)]
        return facts[:limit]

    async def insert_fact_history(self, history: FactHistory) -> FactHistory:
        if history.id is None:
            history = history.model_copy(update={"id": uuid4()})
        self.history.append(history)
        return history


def _make_turn(content: str, *, minutes_ago: int = 0) -> dict[str, object]:
    return {
        "id": uuid4(),
        "role": "user",
        "content": content,
        "message_timestamp": _utcnow() - timedelta(minutes=minutes_ago),
    }


def test_extract_facts_returns_structured_candidates_with_temporal_metadata() -> None:
    turns = [
        _make_turn("I prefer oat milk in coffee.", minutes_ago=8),
        _make_turn("I drink green tea every morning.", minutes_ago=7),
        _make_turn("I want to finish the API migration this month.", minutes_ago=6),
        _make_turn("I'm a backend engineer.", minutes_ago=5),
    ]

    extracted = extract_facts(turns, now=_utcnow())

    categories = {fact.category for fact in extracted}
    assert categories == {
        FactCategory.PREFERENCE,
        FactCategory.HABIT,
        FactCategory.GOAL,
        FactCategory.IDENTITY,
    }
    assert all(fact.confidence > 0.7 for fact in extracted)
    assert all(fact.source_episode_ids for fact in extracted)
    assert all(fact.source_references for fact in extracted)
    assert extracted[0].event_time == turns[0]["message_timestamp"]
    assert extracted[0].transaction_time.tzinfo is not None


def test_extract_facts_ignores_agent_directives_and_meta_operational_requests() -> None:
    turns = [
        _make_turn("I want you to restart the gateway and check the Supabase schema.", minutes_ago=3),
        _make_turn("I should delegate everything and the project belongs in ~/.hermes.", minutes_ago=2),
        _make_turn("I prefer oat milk.", minutes_ago=1),
    ]

    extracted = extract_facts(turns, now=_utcnow())

    assert len(extracted) == 1
    assert extracted[0].category is FactCategory.PREFERENCE
    assert extracted[0].content == "User prefers oat milk."


def test_extract_facts_ignores_assistant_rephrased_self_talk() -> None:
    turns = [
        {
            "id": uuid4(),
            "role": "assistant",
            "content": "I should delegate everything and restart the gateway.",
            "message_timestamp": _utcnow(),
        },
        _make_turn("I want to finish the API migration this month.", minutes_ago=1),
    ]

    extracted = extract_facts(turns, now=_utcnow())

    assert len(extracted) == 1
    assert extracted[0].category is FactCategory.GOAL
    assert extracted[0].content == "User wants to finish the API migration this month."


def test_extract_facts_skips_low_value_project_chatter() -> None:
    turns = [
        _make_turn("Okay now what if left with memory project.", minutes_ago=2),
        _make_turn("Env file for the memory project.", minutes_ago=1),
        _make_turn("I am rebuilding the Memory memory retrieval layer this week.", minutes_ago=0),
    ]

    extracted = extract_facts(turns, now=_utcnow())

    assert len(extracted) == 1
    assert extracted[0].category is FactCategory.PROJECT
    assert "rebuilding the Memory memory retrieval layer" in extracted[0].content


def test_extract_facts_captures_informal_religion_identity_phrase() -> None:
    turns = [
        _make_turn("im from a marwari family", minutes_ago=1),
    ]

    extracted = extract_facts(turns, now=_utcnow())

    assert len(extracted) == 1
    assert extracted[0].category is FactCategory.IDENTITY
    assert "marwari family" in extracted[0].content.lower()


def test_deduplicate_facts_merges_duplicate_candidates() -> None:
    turns = [
        {
            "id": uuid4(),
            "role": "user",
            "content": "I prefer oat milk.",
            "message_timestamp": _utcnow() - timedelta(minutes=2),
        },
        {
            "id": uuid4(),
            "role": "assistant",
            "content": "You prefer oat milk.",
            "message_timestamp": _utcnow() - timedelta(minutes=1),
        },
    ]

    extracted = extract_facts(turns, now=_utcnow())
    deduplicated = deduplicate_facts(extracted)

    assert len(deduplicated) == 1
    assert deduplicated[0].category is FactCategory.PREFERENCE
    assert deduplicated[0].confidence >= max(fact.confidence for fact in extracted)
    assert len(deduplicated[0].source_references) == 1


@pytest.mark.asyncio
async def test_extract_and_store_facts_creates_new_records_with_history() -> None:
    transport = InMemoryTransport()
    now = _utcnow()
    turns = [
        _make_turn("I prefer oat milk.", minutes_ago=4),
        _make_turn("I usually review logs before lunch.", minutes_ago=3),
    ]

    stored = await extract_and_store_facts(transport, turns, now=now)

    assert len(stored) == 2
    assert len(transport.facts) == 2
    assert len(transport.history) == 2
    assert {history.operation.value for history in transport.history} == {"add"}
    stored_preference = next(fact for fact in stored if fact.category is FactCategory.PREFERENCE)
    assert stored_preference.source_episode_ids == [turns[0]["id"]]
    assert stored_preference.event_time == turns[0]["message_timestamp"]
    assert stored_preference.transaction_time == now


@pytest.mark.asyncio
async def test_extract_and_store_facts_merges_with_existing_fact() -> None:
    transport = InMemoryTransport()
    existing_time = _utcnow() - timedelta(days=2)
    existing = Fact(
        id=uuid4(),
        content="User prefers oat milk.",
        category=FactCategory.PREFERENCE,
        confidence=0.61,
        event_time=existing_time,
        transaction_time=existing_time,
        is_active=True,
        source_episode_ids=[],
        access_count=0,
        tags=["milk"],
        created_at=existing_time,
        updated_at=existing_time,
    )
    await transport.insert_fact(existing)

    new_turn = _make_turn("I prefer oat milk in coffee.", minutes_ago=1)
    stored = await extract_and_store_facts(transport, [new_turn], now=_utcnow())

    assert len(stored) == 1
    assert len(transport.facts) == 1
    merged = transport.facts[str(existing.id)]
    assert merged.id == existing.id
    assert merged.confidence > existing.confidence
    assert merged.source_episode_ids == [new_turn["id"]]
    assert "coffee" in merged.content.lower()
    assert "preference" in merged.tags
    assert [history.operation.value for history in transport.history] == ["update"]


def test_extract_facts_emits_identity_slot_and_lifecycle_tags() -> None:
    turns = [
        _make_turn("my religion is hindu", minutes_ago=3),
        _make_turn("my religion is not hindu", minutes_ago=2),
        _make_turn("i think i am a marwari", minutes_ago=1),
    ]

    extracted = extract_facts(turns, now=_utcnow())

    religion_affirmed = next(
        fact
        for fact in extracted
        if fact.category is FactCategory.IDENTITY
        and "identity_slot:religion" in fact.tags
        and "identity_state:affirmed" in fact.tags
    )
    religion_revoked = next(
        fact
        for fact in extracted
        if fact.category is FactCategory.IDENTITY
        and "identity_slot:religion" in fact.tags
        and "identity_state:revoked" in fact.tags
    )
    uncertain_identity = next(
        fact
        for fact in extracted
        if fact.category is FactCategory.IDENTITY
        and "identity_state:uncertain" in fact.tags
    )

    assert "religion is hindu" in religion_affirmed.content.lower()
    assert "religion is not hindu" in religion_revoked.content.lower()
    assert "may be" in uncertain_identity.content.lower()
    assert "marwari" in uncertain_identity.content.lower()


@pytest.mark.asyncio
async def test_extract_and_store_facts_keeps_conflicting_identity_lifecycle_rows_separate() -> None:
    transport = InMemoryTransport()
    existing_time = _utcnow() - timedelta(days=1)
    existing = Fact(
        id=uuid4(),
        content="User's religion is hindu.",
        category=FactCategory.IDENTITY,
        confidence=0.88,
        event_time=existing_time,
        transaction_time=existing_time,
        is_active=True,
        source_episode_ids=[],
        access_count=0,
        tags=["identity", "identity_slot:religion", "identity_state:affirmed"],
        created_at=existing_time,
        updated_at=existing_time,
    )
    await transport.insert_fact(existing)

    new_turn = _make_turn("my religion is not hindu", minutes_ago=1)
    stored = await extract_and_store_facts(transport, [new_turn], now=_utcnow())

    assert len(stored) == 1
    assert len(transport.facts) == 2
    assert any("identity_state:revoked" in fact.tags for fact in transport.facts.values())
