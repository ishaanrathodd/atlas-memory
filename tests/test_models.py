from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory.models import EmotionProfile, Episode, EpisodeRole, Fact, FactCategory, Platform, Session, VECTOR_DIMENSIONS


def test_fact_model_round_trips_enums_and_lists() -> None:
    now = datetime.now(timezone.utc)
    fact = Fact(
        id=uuid4(),
        content="I prefer oat milk.",
        category=FactCategory.PREFERENCE,
        confidence=0.8,
        event_time=now,
        transaction_time=now,
        tags=["food", "preference"],
    )

    payload = fact.model_dump(mode="json")

    assert payload["category"] == "preference"
    assert payload["tags"] == ["food", "preference"]


def test_session_requires_timezone_aware_datetimes() -> None:
    with pytest.raises(ValidationError):
        Session(platform=Platform.LOCAL, started_at=datetime.utcnow())


def test_episode_embedding_is_normalized_to_float() -> None:
    now = datetime.now(timezone.utc)
    episode = Episode(
        session_id=uuid4(),
        role=EpisodeRole.USER,
        content="Hello there",
        content_hash="hash",
        embedding=[1] * VECTOR_DIMENSIONS,
        message_timestamp=now,
    )

    assert episode.embedding == [1.0] * VECTOR_DIMENSIONS


def test_episode_embedding_requires_exact_vector_length() -> None:
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError):
        Episode(
            session_id=uuid4(),
            role=EpisodeRole.USER,
            content="Hello there",
            content_hash="hash",
            embedding=[1.0, 2.0, 3.0],
            message_timestamp=now,
        )


def test_session_summary_embedding_requires_exact_vector_length() -> None:
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError):
        Session(platform=Platform.LOCAL, started_at=now, summary_embedding=[0.0] * (VECTOR_DIMENSIONS - 1))


def test_emotion_profile_normalizes_scores() -> None:
    profile = EmotionProfile(scores={"joy": 1, "fear": 0.5}, dominant_emotion="joy", intensity=1)

    assert profile.scores == {"joy": 1.0, "fear": 0.5}
    assert profile.intensity == 1.0
