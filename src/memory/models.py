from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

VECTOR_DIMENSIONS = 512


def _normalize_vector(value: list[float] | None, *, field_name: str) -> list[float] | None:
    if value is None:
        return None
    normalized = [float(item) for item in value]
    if len(normalized) != VECTOR_DIMENSIONS:
        raise ValueError(f"{field_name} must contain exactly {VECTOR_DIMENSIONS} dimensions.")
    return normalized


class FactCategory(str, Enum):
    PREFERENCE = "preference"
    FACT = "fact"
    GOAL = "goal"
    RELATIONSHIP = "relationship"
    PROJECT = "project"
    HEALTH = "health"
    FINANCE = "finance"
    HABIT = "habit"
    ENVIRONMENT = "environment"
    IDENTITY = "identity"
    OTHER = "other"


class EpisodeRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Platform(str, Enum):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    LOCAL = "local"
    DISCORD = "discord"
    OTHER = "other"


class FactOperation(str, Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"


class ActiveStateKind(str, Enum):
    PROJECT = "project"
    BLOCKER = "blocker"
    PRIORITY = "priority"
    EMOTION_STATE = "emotion_state"
    OPEN_LOOP = "open_loop"
    RELATIONSHIP_STATE = "relationship_state"


class ActiveStateStatus(str, Enum):
    ACTIVE = "active"
    COOLING = "cooling"
    RESOLVED = "resolved"
    STALE = "stale"


class DirectiveKind(str, Enum):
    BEHAVIOR = "behavior"
    COMMUNICATION = "communication"
    TOOLING = "tooling"
    MEMORY = "memory"


class DirectiveScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"
    SESSION = "session"


class DirectiveStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class TimelineEventKind(str, Enum):
    SESSION_SUMMARY = "session_summary"
    DAY_SUMMARY = "day_summary"
    WEEK_SUMMARY = "week_summary"
    MILESTONE = "milestone"
    DECISION = "decision"


class DecisionOutcomeKind(str, Enum):
    MEMORY = "memory"
    TOOLING = "tooling"
    WORKFLOW = "workflow"
    PRODUCT = "product"
    COMMUNICATION = "communication"
    OTHER = "other"


class DecisionOutcomeStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    MIXED = "mixed"
    OPEN = "open"


class PatternType(str, Enum):
    STRENGTH = "strength"
    TRAP = "trap"
    DECISION_STYLE = "decision_style"
    EMOTIONAL_PATTERN = "emotional_pattern"
    WORK_PATTERN = "work_pattern"
    TRUST_PATTERN = "trust_pattern"
    QUALITY_BAR = "quality_bar"


class CommitmentKind(str, Enum):
    FOLLOW_UP = "follow_up"
    REMINDER = "reminder"
    TRACKING = "tracking"
    FIX = "fix"
    MESSAGE = "message"
    OTHER = "other"


class CommitmentStatus(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CorrectionKind(str, Enum):
    FACT_CORRECTION = "fact_correction"
    DIRECTIVE_CLARIFICATION = "directive_clarification"
    MEMORY_DISPUTE = "memory_dispute"
    INTERPRETATION_REJECTION = "interpretation_rejection"
    SCOPE_CLARIFICATION = "scope_clarification"


class MemoryBaseModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="ignore",
    )


class EmotionProfile(MemoryBaseModel):
    scores: dict[str, float] = Field(default_factory=dict)
    dominant_emotion: str | None = None
    intensity: float = Field(default=0.0, ge=0.0)

    @field_validator("scores")
    @classmethod
    def normalize_scores(cls, value: dict[str, float]) -> dict[str, float]:
        return {str(key): float(score) for key, score in value.items()}


class Fact(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    content: str
    category: FactCategory
    content_fingerprint: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    event_time: AwareDatetime
    transaction_time: AwareDatetime
    is_active: bool = True
    replaced_by: UUID | None = None
    source_episode_ids: list[UUID] = Field(default_factory=list)

    @field_validator("source_episode_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_to_list(cls, v: Any) -> Any:
        return v if v is not None else []
    access_count: int = Field(default=0, ge=0)
    last_accessed_at: AwareDatetime | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None


class Episode(MemoryBaseModel):
    id: UUID | None = None
    session_id: UUID
    agent_namespace: str | None = None
    role: EpisodeRole
    content: str
    content_hash: str
    embedding: list[float] | None = None
    platform: Platform = Platform.LOCAL
    message_metadata: dict[str, Any] = Field(default_factory=dict)
    emotions: dict[str, float] = Field(default_factory=dict)
    dominant_emotion: str | None = None
    emotional_intensity: float = Field(default=0.0, ge=0.0)
    message_timestamp: AwareDatetime
    created_at: AwareDatetime | None = None

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, value: list[float] | None) -> list[float] | None:
        return _normalize_vector(value, field_name="embedding")


class Session(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    platform: Platform = Platform.LOCAL
    legacy_session_id: str | None = None
    title: str | None = None
    parent_session_id: UUID | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    end_reason: str | None = None
    model: str | None = None
    session_model_config: dict[str, Any] = Field(
        default_factory=dict,
        alias="model_config",
        serialization_alias="model_config",
    )
    system_prompt_snapshot: str | None = None
    message_count: int = Field(default=0, ge=0)
    user_message_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    actual_cost_usd: float | None = Field(default=None, ge=0.0)
    cost_status: str | None = None
    cost_source: str | None = None
    billing_provider: str | None = None
    billing_base_url: str | None = None
    billing_mode: str | None = None
    summary: str | None = None
    summary_embedding: list[float] | None = None
    topics: list[str] = Field(default_factory=list)
    avg_emotional_intensity: float | None = Field(default=None, ge=0.0)
    dominant_emotions: list[str] = Field(default_factory=list)
    dominant_emotion_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("summary_embedding")
    @classmethod
    def validate_summary_embedding(cls, value: list[float] | None) -> list[float] | None:
        return _normalize_vector(value, field_name="summary_embedding")

    @field_validator("session_model_config", mode="before")
    @classmethod
    def validate_model_config(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        return {str(key): item for key, item in dict(value).items()}

    @field_validator("dominant_emotion_counts", mode="before")
    @classmethod
    def validate_dominant_emotion_counts(cls, value: dict[str, int] | None) -> dict[str, int]:
        if value is None:
            return {}
        return {str(key): int(count) for key, count in value.items()}


class FactHistory(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    fact_id: UUID
    operation: FactOperation
    old_content: str | None = None
    new_content: str | None = None
    old_category: FactCategory | None = None
    new_category: FactCategory | None = None
    event_time: AwareDatetime
    transaction_time: AwareDatetime
    reason: str | None = None


class ActiveState(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    kind: ActiveStateKind
    title: str | None = None
    content: str
    content_hash: str | None = None
    state_key: str
    status: ActiveStateStatus = ActiveStateStatus.ACTIVE
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    priority_score: float = Field(default=0.5, ge=0.0)
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    last_observed_at: AwareDatetime
    source_episode_ids: list[UUID] = Field(default_factory=list)
    source_session_ids: list[UUID] = Field(default_factory=list)
    supporting_fact_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None

    @field_validator("source_episode_ids", "source_session_ids", "supporting_fact_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_active_state_lists(cls, v: Any) -> Any:
        return v if v is not None else []


class Directive(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    kind: DirectiveKind
    scope: DirectiveScope = DirectiveScope.GLOBAL
    title: str | None = None
    content: str
    content_hash: str | None = None
    directive_key: str
    status: DirectiveStatus = DirectiveStatus.ACTIVE
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    priority_score: float = Field(default=1.0, ge=0.0)
    source_episode_ids: list[UUID] = Field(default_factory=list)
    source_session_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    last_observed_at: AwareDatetime | None = None

    @field_validator("source_episode_ids", "source_session_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_directive_lists(cls, v: Any) -> Any:
        return v if v is not None else []


class TimelineEvent(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    kind: TimelineEventKind = TimelineEventKind.SESSION_SUMMARY
    title: str | None = None
    summary: str
    event_key: str
    event_time: AwareDatetime
    session_id: UUID | None = None
    source_episode_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    importance_score: float = Field(default=0.6, ge=0.0)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None

    @field_validator("source_episode_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_timeline_lists(cls, v: Any) -> Any:
        return v if v is not None else []


class DecisionOutcome(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    kind: DecisionOutcomeKind = DecisionOutcomeKind.OTHER
    title: str | None = None
    decision: str
    outcome: str
    lesson: str | None = None
    outcome_key: str
    status: DecisionOutcomeStatus = DecisionOutcomeStatus.OPEN
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    importance_score: float = Field(default=0.6, ge=0.0)
    event_time: AwareDatetime
    session_id: UUID | None = None
    source_episode_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None

    @field_validator("source_episode_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_decision_outcome_lists(cls, v: Any) -> Any:
        return v if v is not None else []


class Pattern(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    pattern_type: PatternType
    statement: str
    description: str | None = None
    pattern_key: str
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    frequency_score: float = Field(default=0.5, ge=0.0)
    impact_score: float = Field(default=0.5, ge=0.0)
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    supporting_episode_ids: list[UUID] = Field(default_factory=list)
    supporting_session_ids: list[UUID] = Field(default_factory=list)
    counterexample_episode_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None

    @field_validator("supporting_episode_ids", "supporting_session_ids", "counterexample_episode_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_pattern_lists(cls, v: Any) -> Any:
        return v if v is not None else []


class Commitment(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    kind: CommitmentKind = CommitmentKind.OTHER
    statement: str
    commitment_key: str
    status: CommitmentStatus = CommitmentStatus.OPEN
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    priority_score: float = Field(default=0.7, ge=0.0)
    first_committed_at: AwareDatetime
    last_observed_at: AwareDatetime
    source_episode_ids: list[UUID] = Field(default_factory=list)
    source_session_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None

    @field_validator("source_episode_ids", "source_session_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_commitment_lists(cls, v: Any) -> Any:
        return v if v is not None else []


class Correction(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    kind: CorrectionKind = CorrectionKind.MEMORY_DISPUTE
    statement: str
    target_text: str | None = None
    correction_key: str
    active: bool = True
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    source_episode_ids: list[UUID] = Field(default_factory=list)
    source_session_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None

    @field_validator("source_episode_ids", "source_session_ids", "tags", mode="before")
    @classmethod
    def _coerce_null_correction_lists(cls, v: Any) -> Any:
        return v if v is not None else []


class SessionHandoff(MemoryBaseModel):
    id: UUID | None = None
    agent_namespace: str | None = None
    session_id: UUID
    handoff_key: str
    last_thread: str
    carry_forward: str | None = None
    assistant_context: str | None = None
    emotional_tone: str | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source_episode_ids: list[UUID] = Field(default_factory=list)
    source_session_ids: list[UUID] = Field(default_factory=list)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    last_observed_at: AwareDatetime

    @field_validator("source_episode_ids", "source_session_ids", mode="before")
    @classmethod
    def _coerce_null_session_handoff_lists(cls, v: Any) -> Any:
        return v if v is not None else []
