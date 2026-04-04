# Memory Always-On Curator Technical Specification

## Purpose

This document specifies the Phase 3 always-on curator for Memory. The curator is responsible for:

- maintaining memory hygiene in the background
- building a current-state model of the user across platforms
- deciding when proactive outreach is appropriate
- preserving emotional continuity across sessions
- degrading safely when offline, rate-limited, or low on resources
- enforcing clear privacy boundaries on local machine signals

The design is implementation-oriented and is intended to be used directly for building:

- `src/memory/curator_runtime.py`
- `src/memory/state_tracker.py`
- `src/memory/proactive.py`
- `src/memory/consolidation.py`
- `tests/test_curator_runtime.py`
- `tests/test_state_tracker.py`

It assumes the existing `MemoryClient`, `MemoryTransport`, `SupabaseTransport`, `EmotionAnalyzer`, and fact extraction utilities remain the canonical memory APIs.

## Goals

- Reuse the existing `memory` client and transport stack instead of introducing a parallel storage path.
- Support both a long-running local curator and Hermes cron-driven execution.
- Maintain a rolling, privacy-bounded state model of what the user is doing.
- Generate proactive prompts only when useful, low-risk, and rate-limited.
- Keep power, CPU, memory, and network usage acceptable on a MacBook Air M2 with 8 GB RAM.
- Operate safely when Memory storage, OpenAI, network, or optional platform connectors are unavailable.

## Non-Goals

- Replacing Hermes as the message delivery layer.
- Capturing screenshots, keystrokes, clipboard contents, or browser content.
- Running arbitrary high-frequency OS surveillance.
- Requiring always-available cloud connectivity for core operation.
- Making health, finance, or mental-health inferences beyond lightweight conversational continuity.

## Existing Integration Surface

The curator should build on these existing modules:

- `src/memory/client.py`
  - session lifecycle
  - episode storage
  - fact CRUD
  - context enrichment
- `src/memory/transport.py`
  - `MemoryTransport`
  - `SupabaseTransport`
- `src/memory/fact_extraction.py`
  - deterministic fact extraction from conversations
- `src/memory/enrichment.py`
  - recent episodes and fact relevance

The curator must use `MemoryClient` as its primary write path. It may read some local system signals directly, but it should not bypass the client for Memory data mutations unless the operation is explicitly a transport-level maintenance function.

## High-Level Recommendation

Use a hybrid model:

1. Primary mode: persistent local curator managed by `launchd`.
2. Secondary mode: Hermes cron jobs as safety net and recovery path.
3. Development mode: foreground CLI process via `python -m memory.curator_runtime`.

This is the recommended split because:

- a persistent process is better for low-latency state awareness, event debouncing, offline spooling, and proactive scheduling
- cron is better for coarse maintenance, restart recovery, and environments where the curator is not running
- relying on cron alone is too coarse for "what is the user doing right now" and emotional carry-over logic

## Runtime Modes

### Mode A: Persistent Local Process

Recommended for primary local use on macOS.

Characteristics:

- runs under `launchd`
- keeps a small in-memory state cache
- subscribes to local signals and runs low-frequency polling
- maintains offline spool and local state snapshot
- schedules periodic tasks with jitter

Use for:

- active app tracking
- idle / presence state
- message recency awareness
- hourly proactive checks
- debounced session-end summarization

### Mode B: Hermes Cron Fallback

Recommended as backup even when the persistent curator is enabled.

Characteristics:

- stateless or near-stateless single-shot execution
- runs coarse maintenance jobs
- can backfill missed work after sleep, crash, or reboot

Use for:

- memory consolidation every 6 hours
- fact decay checks daily
- stalled job recovery
- opportunistic outreach checks if the curator was down

### Mode C: Foreground Development CLI

Example:

```bash
python -m memory.curator_runtime process-memory
python -m memory.curator_runtime stats
```

Use for:

- implementation
- local debugging
- task-level replay
- tests

## Process Architecture

```text
launchd / Hermes cron
        |
        v
  MemoryDaemon
    |- Config
    |- StructuredLogger
    |- HealthServer
    |- JobScheduler
    |- StateTracker
    |- ConsolidationEngine
    |- ProactiveEngine
    |- DeliveryAdapter
    |- OfflineSpool
    |- LocalStateStore
    `- MemoryClient
            |- MemoryTransport
            `- EmbeddingProvider / EmotionAnalyzer
```

## Core Components

### 1. `MemoryDaemon`

Owns lifecycle, startup checks, task registration, graceful shutdown, health reporting, and recovery behavior.

Responsibilities:

- initialize config, client, stores, and adapters
- acquire singleton lock so only one persistent curator runs
- start scheduler and signal collectors
- expose health status
- trap SIGINT and SIGTERM
- restart failed background loops without exiting the whole process

### 2. `JobScheduler`

Responsible for periodic work and jittered scheduling.

Requirements:

- task registry with intervals and max concurrency
- per-task lock to ensure idempotent single execution
- persisted run metadata so cron and curator cannot duplicate work
- exponential backoff after transient failures
- random jitter to avoid bursty wakeups and synchronized API calls

### 3. `StateTracker`

Builds a current-state snapshot from Memory data and local signals.

Responsibilities:

- collect local machine signals
- collect Memory memory recency signals
- compute active projects, current conversation heat, and emotional baseline
- detect changes from prior snapshots
- persist a compact state snapshot locally and optionally to Memory

### 4. `ConsolidationEngine`

Performs memory hygiene and summarization.

Responsibilities:

- find ended or idle sessions lacking summaries
- generate summaries for completed sessions
- extract candidate facts from recent episodes and summaries
- deduplicate and merge facts
- flag or deactivate contradicted facts
- update current-state cache

### 5. `ProactiveEngine`

Evaluates whether Memory should surface a message, reminder, or contextual nudge.

Responsibilities:

- compare latest snapshot to previous snapshot
- score triggers
- apply privacy, risk, and rate-limit gates
- emit notification intents rather than sending directly

### 6. `DeliveryAdapter`

Converts a notification intent into a real outbound message through Hermes or local delivery.

Responsibilities:

- route by target platform
- avoid duplicate sends
- persist delivery result
- retry transient failures

### 7. `OfflineSpool`

Persistent local queue for writes and deliveries when Memory or network is unavailable.

Responsibilities:

- queue pending fact writes, summaries, and delivery intents
- guarantee ordering where required
- de-duplicate by idempotency key
- replay safely when connectivity returns

### 8. `LocalStateStore`

Low-overhead local persistence for curator-only metadata.

Recommended implementation:

- SQLite database under `~/.hermes/memory/state/curator.db`

Suggested tables:

- `daemon_runs`
- `task_runs`
- `state_snapshots`
- `signal_samples`
- `notification_intents`
- `delivery_log`
- `offline_queue`
- `feature_flags`

## Persistent Process vs Cron

### Decision

The implementation should support both, but default to persistent process plus cron backup.

### Why persistent process is preferred

- active app awareness needs intervals smaller than an hour
- session end is best detected with idle timers and debouncing, not fixed cron
- proactive logic benefits from local recency knowledge and in-memory cooldowns
- offline spooling and replay are simpler with a resident process

### Why cron still matters

- reboot and crash recovery
- low-complexity fallback on hosts where launchd is disabled
- guaranteed maintenance windows even if the persistent curator was stopped

### Operational model

- `launchd` starts the curator at login and keeps it alive
- Hermes cron runs these jobs even if the curator exists:
  - `memory-heartbeat` every 6 hours
  - `memory-decay-check` daily
  - `memory-recovery-check` hourly
- cron tasks first inspect the local lock and recent run metadata
  - if the persistent curator is healthy and the task is fresh, cron exits without doing work
  - if the curator is unhealthy, stale, or absent, cron executes the task directly

### Singleton locking

Use one local lock file for persistent mode:

- path: `~/.hermes/memory/state/curator.lock`
- include PID, hostname, start time, and heartbeat timestamp
- treat the lock as stale if PID is gone or heartbeat is older than threshold

Pseudocode:

```python
class SingleInstanceLock:
    def acquire(self) -> bool:
        if lock_file_exists():
            data = read_lock()
            if pid_alive(data.pid) and not stale(data.heartbeat_at):
                return False
            remove_stale_lock()
        write_lock(pid=os.getpid(), heartbeat_at=utcnow())
        return True

    def refresh(self) -> None:
        update_lock_heartbeat(utcnow())

    def release(self) -> None:
        remove_lock_if_owned_by_current_pid()
```

## Scheduling Model

### Task Registry

Recommended periodic jobs:

| Task | Interval | Trigger Mode | Notes |
|---|---:|---|---|
| `sample_presence` | 60s | persistent only | idle state, local presence, last input timestamp |
| `sample_frontmost_app` | 15s active / 60s idle | persistent only | coarsely track what the user is focused on |
| `refresh_state_snapshot` | 5 min | persistent + cron fallback | rebuild unified current state |
| `refresh_context_cache` | 1 hour | both | precompute hot context |
| `check_proactive_outreach` | 1 hour | both | apply rate limits before delivering |
| `generate_session_summaries` | 15 min | persistent + cron fallback | summarize ended / idle sessions |
| `consolidate_memories` | 6 hours | both | merge facts and maintain hygiene |
| `check_fact_decay` | 24 hours | both | stale fact review |
| `replay_offline_queue` | 5 min | persistent only | retry queued writes and sends |
| `health_heartbeat` | 30s | persistent only | refresh singleton lock and health file |

### Jitter and budgeting

- Add `0-10%` positive jitter to non-critical intervals.
- Run at most one heavy job at a time:
  - heavy jobs: summarization, consolidation, backlog replay with embeddings
- Cap concurrent network-heavy tasks at `1`.
- If the machine is on battery and CPU temperature / pressure is elevated, defer heavy work.

## State Awareness

State awareness should combine Memory-native memory with local signals. It must not depend on any single source.

### State model

The curator should produce a normalized state snapshot like:

```json
{
  "captured_at": "2026-04-01T08:30:00Z",
  "presence": {
    "device_state": "active",
    "idle_seconds": 42,
    "last_local_activity_at": "2026-04-01T08:29:18Z"
  },
  "focus": {
    "frontmost_app": "com.apple.dt.Xcode",
    "window_title_hash": "optional_hash_only",
    "focus_duration_seconds": 960
  },
  "conversation": {
    "last_message_at": "2026-04-01T08:27:01Z",
    "active_platform": "telegram",
    "active_session_id": "uuid",
    "minutes_since_last_message": 3
  },
  "calendar": {
    "current_event": "1:1 with design",
    "next_event_start_at": "2026-04-01T09:00:00Z",
    "busy_until": "2026-04-01T09:30:00Z"
  },
  "projects": [
    {
      "name": "memory",
      "status": "active",
      "confidence": 0.91,
      "evidence": ["recent episodes", "frontmost app", "recent facts"]
    }
  ],
  "topics": ["curator", "memory", "cron"],
  "emotion": {
    "rolling_baseline": {"anticipation": 0.21, "trust": 0.18},
    "short_term_state": {"stress": 0.32, "joy": 0.10},
    "carryover_label": "focused_but_tense"
  }
}
```

### Signal sources

#### A. Memory memory recency

Derived from stored sessions, episodes, and facts.

Signals:

- last episode timestamp by platform
- active session with no `ended_at`
- recent topics from summaries and episode tokens
- project facts and project-related conversations
- recent emotion scores from stored episodes

Advantages:

- already privacy-filtered to what Memory knows
- cross-platform
- survives reboot

#### B. Local activity and presence

macOS-specific, opt-in.

Signals:

- frontmost application bundle identifier
- application name
- coarse idle time in seconds
- wake / sleep transitions
- network reachability

Recommended APIs:

- `NSWorkspace.sharedWorkspace.notificationCenter`
  - app activation changes
  - sleep / wake notifications
- Quartz or IOKit idle-time query for user inactivity
- `scutil` / `SystemConfiguration` reachability or a lightweight network ping

Rules:

- store bundle ID and app name by default
- do not store raw window titles unless explicitly enabled
- if enabled, sanitize window title before storage
  - hash by default
  - store plaintext only if user opts in

#### C. Last message time

This is required and should come from Memory first.

Primary source:

- newest `episodes.message_timestamp` grouped by platform / session

Secondary source:

- Hermes session JSONL watcher if live updates are needed before Memory write completes

Derived fields:

- `minutes_since_last_message`
- `conversation_heat_score`
- `current_active_platform`
- `session_idle_seconds`

#### D. Calendar

Optional connector, opt-in.

Preferred source order:

1. Google Calendar connector or local calendar adapter
2. Hermes-managed schedule cache if available
3. none

The curator should store only coarse event metadata:

- current event title or category
- start and end timestamps
- busy/free status

It should avoid storing:

- attendee emails unless required and approved
- full descriptions
- conference URLs
- attachments

Use cases:

- suppress outreach during busy meetings
- relate project activity to upcoming deadlines
- infer transitions like "workday winding down" only from time blocks, not content

### State synthesis

The state tracker should assign confidences to inferences rather than treating them as facts.

Example inference rules:

- project is active if:
  - project-tagged facts were updated in last 7 days, or
  - project keywords appear in last 10 user episodes, or
  - frontmost app matches project repo / IDE context for more than N minutes
- user is engaged in conversation if:
  - last message < 10 minutes ago, or
  - current Hermes session is open and receiving messages
- user is unavailable if:
  - calendar says busy and idle time is low, or
  - machine asleep, or
  - no local activity plus no message activity for a threshold

### State change detection

Not every change should trigger outreach. The state tracker should emit normalized change events:

- `project_started`
- `project_resurfaced`
- `project_completed`
- `topic_spike`
- `emotion_shift`
- `conversation_return`
- `relationship_change`
- `schedule_proximity`
- `long_gap_since_followup`

Pseudocode:

```python
class MemoryStateTracker:
    async def refresh_snapshot(self) -> StateSnapshot:
        memory = await self._collect_memory_signals()
        local = await self._collect_local_signals()
        calendar = await self._collect_calendar_signals()
        snapshot = self._synthesize(memory, local, calendar)
        await self.store.save_snapshot(snapshot)
        return snapshot

    async def detect_changes(self, previous: StateSnapshot, current: StateSnapshot) -> list[StateChange]:
        changes = []
        if self._new_active_project(previous, current):
            changes.append(StateChange(type="project_started", severity="info"))
        if self._emotion_shift(previous, current):
            changes.append(StateChange(type="emotion_shift", severity="soft"))
        if self._conversation_return(previous, current):
            changes.append(StateChange(type="conversation_return", severity="soft"))
        if self._calendar_deadline_near(current):
            changes.append(StateChange(type="schedule_proximity", severity="info"))
        return changes
```

## Detecting What the User Is Doing

This section is intentionally concrete because the user requested implementation detail around active app, last message time, and calendar.

### Active app detection

#### Approach

- subscribe to frontmost-app changes via `NSWorkspace`
- keep a current app session in memory
- periodically flush a compact sample to `signal_samples`

Recommended stored fields:

- `bundle_id`
- `app_name`
- `started_at`
- `ended_at`
- `duration_seconds`
- optional sanitized `window_title_hash`

Do not store:

- raw document content
- editor buffer text
- browser page content

Pseudocode:

```python
class FrontmostAppMonitor:
    def on_app_activated(self, bundle_id: str, app_name: str, window_title: str | None) -> None:
        now = utcnow()
        if self.current is not None:
            self.current.ended_at = now
            self.store_signal(self.current)
        self.current = FocusSample(
            bundle_id=bundle_id,
            app_name=app_name,
            window_title_hash=hash_title(window_title) if self.config.capture_window_titles else None,
            started_at=now,
        )
```

### Last message time detection

#### Approach

Use Memory episodes as the durable truth.

Query logic:

- fetch newest episode overall
- fetch newest episode per platform
- fetch newest episode in the active session

Optional live path:

- if a Hermes session watcher exists, update a local "message arrived" timestamp immediately and reconcile with Memory after write succeeds

Derived behavior:

- session considered active if last user or assistant message < 15 minutes
- session considered ended when:
  - no new messages for 20 minutes and
  - there is no active local input, or
  - Hermes explicitly marks the session closed

### Calendar awareness

#### Approach

Add a pluggable `CalendarSignalProvider` interface.

```python
class CalendarSignalProvider(Protocol):
    async def get_current_window(self, now: datetime) -> CalendarWindow: ...
```

Output should be coarse:

```json
{
  "is_busy": true,
  "current_event_title": "Project sync",
  "current_event_category": "meeting",
  "busy_until": "2026-04-01T09:30:00Z",
  "next_event_start_at": "2026-04-01T10:00:00Z"
}
```

If permissions are missing or connector is unavailable:

- return `available=false`
- do not fail the state refresh

## Proactive Outreach Triggers and Rules

Proactive outreach should be intentional, infrequent, and reversible. The curator should generate notification intents, not immediately deliver them, until all gates pass.

### Trigger classes

#### 1. State-change triggers

- new project became active
- previously active project resurfaced after a gap
- likely project completion detected
- relationship fact changed
- user resumed a long-paused conversation thread

#### 2. Schedule-aware triggers

- a follow-up topic is near a known calendar event
- deadline window approaches for a project already discussed
- end-of-day or start-of-day check-ins aligned with prior patterns

#### 3. Emotional continuity triggers

- significant shift from rolling emotional baseline
- sustained elevated stress language across several sessions
- return to a previously difficult topic with different tone

These must be framed softly and must never present emotional inference as diagnosis.

#### 4. Memory hygiene triggers

- stale facts need review
- duplicate facts merged
- contradictory facts require confirmation
- sessions missing summaries

These can stay internal or be surfaced only when helpful.

### Trigger scoring

Each potential outreach item should be scored on:

- relevance
- timeliness
- confidence
- novelty
- interruption cost
- privacy risk

Only deliver if:

- score exceeds threshold
- no cooldown is active
- user is not currently busy
- content passes tone and privacy filters

### Cooldown rules

Global cooldown:

- maximum 2 proactive user-facing messages per 24 hours

Per-trigger cooldown:

- same project/topic: 72 hours
- emotional continuity nudge: 7 days
- stale fact reminder: 14 days

Suppression rules:

- do not send during calendar busy blocks unless severity is high
- do not send within 20 minutes of an active conversation
- do not send if the user has been idle > 8 hours
- do not send if the last two proactive messages were ignored

### Content rules

Allowed message shapes:

- gentle recall: "You mentioned X last week. Want to pick that back up?"
- context bridge: "You were working on X earlier today."
- check-in: "You usually revisit X on Mondays."
- clarification: "Memory has two conflicting memories about X. Want to confirm?"

Disallowed message shapes:

- certainty beyond evidence
- emotionally loaded assumptions
- medical, financial, or legal advice
- statements implying surveillance
  - bad: "I saw you were in Xcode for 47 minutes"
  - better: "You seemed focused on the Memory work earlier"

### Intent generation pseudocode

```python
class ProactiveEngine:
    async def generate_intents(self, snapshot: StateSnapshot, changes: list[StateChange]) -> list[NotificationIntent]:
        candidates = []
        candidates.extend(await self._state_change_candidates(snapshot, changes))
        candidates.extend(await self._schedule_candidates(snapshot))
        candidates.extend(await self._emotion_candidates(snapshot))
        candidates.extend(await self._memory_hygiene_candidates(snapshot))

        approved = []
        for candidate in candidates:
            score = self._score(candidate, snapshot)
            if score < candidate.threshold:
                continue
            if not await self.rate_limiter.allow(candidate):
                continue
            if self._should_suppress(candidate, snapshot):
                continue
            approved.append(candidate.to_intent())
        return approved
```

## Emotional Continuity Across Sessions

Emotional continuity should preserve conversational context without overcommitting to shaky inference.

### Principles

- use deterministic signals already produced by `EmotionAnalyzer`
- separate short-term state from long-term baseline
- decay old emotion influence over time
- never store diagnostic or pathologizing labels

### Emotional model

Maintain three layers:

#### Layer 1: Episode-level emotion

Already present on each episode:

- `emotions`
- `dominant_emotion`
- `emotional_intensity`

#### Layer 2: Session-level emotional summary

Already partly supported on `Session`:

- `avg_emotional_intensity`
- `dominant_emotions`
- `dominant_emotion_counts`

Add in curator-maintained local summary:

- `session_emotion_vector`
- `session_resolved_tone`

#### Layer 3: Rolling emotional continuity

Store a local `emotional_state` record:

- `baseline_vector_30d`
- `recent_vector_72h`
- `carryover_label`
- `trend_direction`
- `last_shift_at`

### Update rules

- baseline: exponential moving average over last 30 days of user episodes only
- recent state: weighted average over last 72 hours
- shift: triggered if cosine distance or normalized delta exceeds threshold for 2 or more sessions
- carryover label examples:
  - `steady`
  - `focused`
  - `focused_but_tense`
  - `low_energy`
  - `celebratory`

These labels are internal UX simplifications, not stored as immutable facts.

### Emotional continuity behavior

Use cases:

- surface previous emotional context during next session enrichment
- avoid abrupt tone mismatch when the user resumes a difficult thread
- optionally nudge follow-up if a stress-heavy topic returns

Do not:

- create permanent facts like "user is anxious"
- infer mental health state
- use emotion alone to trigger outreach without topic evidence

Pseudocode:

```python
class EmotionalContinuity:
    async def refresh(self) -> EmotionalState:
        episodes = await self._recent_user_episodes(days=30)
        baseline = ema([e.emotions for e in episodes], half_life_days=10)
        recent = weighted_average([e.emotions for e in episodes if e.message_timestamp >= utcnow() - timedelta(hours=72)])
        shift = vector_distance(baseline, recent)
        label = self._label_from_vectors(baseline, recent, shift)
        state = EmotionalState(
            baseline_vector_30d=baseline,
            recent_vector_72h=recent,
            carryover_label=label,
            last_shift_at=utcnow() if shift > self.config.emotion_shift_threshold else None,
        )
        await self.store.save_emotional_state(state)
        return state
```

## Multi-Platform Unified Memory

The curator must treat Memory as one memory plane across all platforms while preserving source attribution.

### Requirements

- unify Telegram, WhatsApp, local, Discord, and future platforms
- preserve source platform on every episode and session
- compute cross-platform recency and project activity
- avoid duplicate fact extraction when the same conversation is mirrored across channels

### Identity strategy

The curator should operate on these levels:

- `platform`
- `session_id`
- `conversation_key`
- `user_identity_scope`

Suggested additions in local curator metadata:

- `platform_session_map`
- `episode_dedup_index` keyed by content hash + time bucket + platform

### Deduplication

When the same content appears on multiple platforms:

- prefer a single canonical fact write
- keep all episode records if they are true separate interactions
- associate a fact with multiple `source_episode_ids`

### Unified current-state logic

Cross-platform activity should be summarized as:

- current active platform
- recent message counts by platform
- topic overlap across platforms
- project continuity independent of platform

Example:

- user discussed Memory on Telegram yesterday, local CLI today, and WhatsApp tonight
- curator should model that as one project continuity thread, not three disconnected topics

## Resource Management for MacBook Air M2 8 GB

The curator must be tuned for a fanless laptop with limited RAM.

### Budgets

Idle targets:

- resident memory under 150 MB
- near-zero network while idle
- average CPU under 2% over 5-minute windows
- no more than one wakeup-heavy poll loop faster than 15 seconds

Heavy-work targets:

- resident memory under 300 MB during summarization / consolidation
- at most one embedding batch in flight
- at most one LLM summarization job at a time

### Tactics

#### Event-driven over polling

Prefer:

- NSWorkspace activation notifications
- sleep / wake notifications
- file watchers where already available

Avoid:

- sub-second polling
- repeated full-database scans

#### Incremental queries

Never rescan all Memory data every cycle. Track watermarks:

- last summarized session timestamp
- last consolidated episode timestamp
- last fact decay scan cursor
- last replayed queue item

#### Batch size limits

- summarize sessions in small batches, for example 3-5 at a time
- fact extraction in bounded chunk sizes
- queue replay with per-run caps

#### Battery-aware degradation

If on battery and:

- machine is idle, delay heavy tasks
- low power mode is enabled, move heavy jobs to cron windows or charging periods

#### Memory hygiene

- avoid retaining full episode bodies in long-lived caches
- cache IDs and summaries, not raw large payloads
- periodically clear transient in-memory maps

### Scheduler throttling pseudocode

```python
async def maybe_run_heavy_task(task_name: str, coro: Awaitable[None]) -> None:
    if system_state.on_battery and not system_state.on_ac_power:
        if system_state.low_power_mode or system_state.cpu_pressure_high:
            logger.info("deferring heavy task", task=task_name)
            return
    async with heavy_task_semaphore:
        await coro
```

## Fallback Behavior When Offline

Offline behavior must be first-class. The curator should continue to track coarse state locally even if Supabase or OpenAI are unavailable.

### Offline classes

#### Class 1: Memory storage unavailable

Symptoms:

- Supabase health check fails
- transport writes error

Behavior:

- keep collecting local signals
- queue summaries, facts, and intents in `offline_queue`
- mark state snapshot as `memory_sync_pending`

#### Class 2: OpenAI unavailable

Symptoms:

- embedding or summarization calls fail

Behavior:

- continue deterministic operations:
  - emotion analysis
  - rule-based fact extraction
  - state tracking
- create placeholder summaries with deterministic local heuristics if needed
- enqueue rich summarization for later retry

#### Class 3: No network

Behavior:

- disable delivery attempts
- continue local state sampling
- continue queueing outbound items
- perform only local privacy-safe work

### Offline queue design

Each queue item should include:

- `id`
- `kind`
- `payload`
- `idempotency_key`
- `created_at`
- `retry_count`
- `next_attempt_at`
- `last_error`

Kinds:

- `session_summary`
- `fact_upsert`
- `fact_deactivate`
- `notification_intent`
- `delivery_attempt`

Replay rules:

- sort by `next_attempt_at`
- exponential backoff with cap
- dead-letter after max retries, but do not delete automatically

Pseudocode:

```python
async def replay_offline_queue() -> None:
    if not network_available():
        return
    items = await queue.fetch_due(limit=20)
    for item in items:
        try:
            await process_queue_item(item)
            await queue.mark_done(item.id)
        except TransientError as exc:
            await queue.reschedule(item.id, error=str(exc))
        except PermanentError as exc:
            await queue.dead_letter(item.id, error=str(exc))
```

## Privacy and Data Boundaries

This section is normative. If implementation detail conflicts with this section, this section wins.

### Default posture

- opt-in for local machine signals
- minimal collection
- coarse storage
- clear provenance
- reversible disabling per signal source

### Allowed by default

- Memory-native sessions, episodes, and facts
- last message timestamps
- platform metadata
- coarse frontmost app bundle ID and app name
- coarse idle / presence state
- calendar busy/free window if explicitly enabled

### Disallowed by default

- screenshots
- clipboard capture
- keyboard logging
- browser content scraping
- raw editor buffer text
- file content indexing outside Memory conversations
- microphone, camera, or location access

### Optional but gated

- raw window titles
- calendar event titles
- relationship metadata from external connectors

These require:

- explicit config enablement
- documentation in config
- ability to disable without code changes

### Data boundary rules

#### Rule 1: Provenance tagging

Every signal-derived record must include source metadata:

- `source_type`: `memory`, `os_signal`, `calendar`, `connector`
- `source_detail`: `episodes`, `frontmost_app`, `google_calendar`, etc.
- `confidence`

#### Rule 2: Facts vs transient state

Do not convert transient signals directly into durable semantic facts unless they recur and are confirmed by conversational evidence.

Examples:

- acceptable transient state:
  - "frontmost app is Xcode"
  - "user appears busy until 09:30"
- not acceptable durable fact without confirmation:
  - "user works in Xcode every morning"

#### Rule 3: Explainability

Any proactive outreach should be explainable internally:

- which signals triggered it
- why it passed cooldown
- which privacy filters were applied

#### Rule 4: Retention

Suggested retention:

- raw signal samples: 7 days
- derived state snapshots: 30 days
- delivery logs: 30 days
- dead-letter queue: until manually reviewed or 30 days

#### Rule 5: Local file permissions

Curator local state files should be created with restrictive permissions where possible.

- directories: `0700`
- SQLite / logs / queue files: `0600`

## Interfaces and Proposed Modules

### `src/memory/curator_runtime.py`

Should expose:

- `MemoryDaemon`
- CLI entrypoint
- launchd / cron-safe `main()`

### `src/memory/state_tracker.py`

Should expose:

- `StateSnapshot`
- `StateChange`
- `MemoryStateTracker`
- signal provider protocols:
  - `LocalSignalProvider`
  - `CalendarSignalProvider`

### `src/memory/proactive.py`

Should expose:

- `NotificationIntent`
- `RateLimiter`
- `ProactiveEngine`
- `DeliveryAdapter`

### `src/memory/consolidation.py`

Should expose:

- `ConsolidationEngine`
- `SessionSummaryJob`
- `FactMergeJob`
- `FactDecayJob`

### Suggested config additions

Add curator-specific configuration fields, likely in `MemoryConfig` or a separate `DaemonConfig`:

```python
@dataclass(slots=True)
class DaemonConfig:
    enabled: bool = True
    mode: str = "persistent"
    state_db_path: str = "~/.hermes/memory/state/curator.db"
    lock_path: str = "~/.hermes/memory/state/curator.lock"
    health_path: str = "~/.hermes/memory/state/curator-health.json"
    enable_frontmost_app: bool = True
    capture_window_titles: bool = False
    enable_calendar: bool = False
    calendar_provider: str | None = None
    proactive_enabled: bool = True
    max_proactive_per_day: int = 2
    emotion_shift_threshold: float = 0.25
    fact_decay_days: int = 30
    session_idle_minutes: int = 20
    context_refresh_minutes: int = 60
    state_refresh_minutes: int = 5
```

## Health, Observability, and Failure Handling

### Health file

Write a JSON health file at least every 30 seconds:

```json
{
  "pid": 12345,
  "started_at": "2026-04-01T08:00:00Z",
  "heartbeat_at": "2026-04-01T08:30:00Z",
  "mode": "persistent",
  "status": "healthy",
  "last_successful_tasks": {
    "refresh_state_snapshot": "2026-04-01T08:25:00Z",
    "consolidate_memories": "2026-04-01T06:00:10Z"
  },
  "degraded": {
    "memory_unavailable": false,
    "network_unavailable": false,
    "calendar_unavailable": true
  }
}
```

### Local health endpoint

Expose an optional local-only HTTP endpoint for monitoring and cron diagnostics.

Requirements:

- bind to `127.0.0.1` only
- default port configurable, for example `8765`
- return current curator status, recent task results, and degraded-source flags
- never expose secrets, pending payload contents, or raw user data

Suggested routes:

- `GET /healthz`
  - lightweight liveness check
- `GET /readyz`
  - indicates whether Memory transport, local store, and scheduler are usable
- `GET /metrics`
  - optional JSON counters, not Prometheus-specific unless needed later

Example `GET /healthz` response:

```json
{
  "status": "healthy",
  "pid": 12345,
  "heartbeat_at": "2026-04-01T08:30:00Z",
  "queue_depth": 3,
  "degraded": {
    "memory_unavailable": false,
    "network_unavailable": false
  }
}
```

### Structured logging

Use structured logs with:

- task name
- duration
- records processed
- queue length
- suppression reason for skipped proactive messages
- exception type and retry count

### Never-crash policy

The curator should treat almost all task failures as isolated.

- a failed task should not terminate the process
- use supervised child tasks
- fatal process exit only for startup misconfiguration or unrecoverable state DB corruption

Pseudocode:

```python
async def supervised_task(name: str, fn: Callable[[], Awaitable[None]]) -> None:
    while not shutdown_requested:
        try:
            await fn()
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("task failed", task=name, error=str(exc))
            await asyncio.sleep(backoff_for(name))
```

## End-to-End Main Loop Pseudocode

```python
class MemoryDaemon:
    async def run(self) -> None:
        self.config = load_config()
        self.lock = SingleInstanceLock(self.config.lock_path)
        if self.config.mode == "persistent" and not self.lock.acquire():
            raise RuntimeError("Memory curator already running")

        await self._init_runtime()
        self.scheduler.register("health_heartbeat", every=30, fn=self._heartbeat)
        self.scheduler.register("refresh_state_snapshot", every=300, fn=self._refresh_state)
        self.scheduler.register("generate_session_summaries", every=900, fn=self.consolidation.generate_session_summaries)
        self.scheduler.register("refresh_context_cache", every=3600, fn=self.consolidation.refresh_context_cache)
        self.scheduler.register("check_proactive_outreach", every=3600, fn=self._proactive_cycle)
        self.scheduler.register("consolidate_memories", every=21600, fn=self.consolidation.consolidate_memories)
        self.scheduler.register("check_fact_decay", every=86400, fn=self.consolidation.check_fact_decay)
        self.scheduler.register("replay_offline_queue", every=300, fn=self.offline_queue.replay)

        await self.scheduler.run_forever()

    async def _refresh_state(self) -> None:
        previous = await self.state_tracker.get_latest_snapshot()
        current = await self.state_tracker.refresh_snapshot()
        changes = await self.state_tracker.detect_changes(previous, current) if previous else []
        await self.local_store.save_pending_changes(changes)

    async def _proactive_cycle(self) -> None:
        snapshot = await self.state_tracker.get_latest_snapshot()
        changes = await self.local_store.load_pending_changes()
        intents = await self.proactive.generate_intents(snapshot, changes)
        for intent in intents:
            await self.delivery.enqueue(intent)
        await self.local_store.clear_pending_changes()
```

## Implementation Phasing

### Phase 3A: Minimal Curator Skeleton

- curator lifecycle
- singleton lock
- scheduler
- health file
- local SQLite store
- cron coexistence

### Phase 3B: State Tracking

- Memory recency signals
- last message time
- frontmost app monitoring
- basic presence / idle state
- state snapshot persistence

### Phase 3C: Consolidation

- session summary generation
- fact extraction from unsummarized sessions
- duplicate merge
- fact decay pass

### Phase 3D: Proactive Engine

- trigger scoring
- cooldowns
- suppression rules
- notification intent persistence

### Phase 3E: Optional Connectors

- calendar provider
- Hermes delivery adapter
- richer cross-platform inference

## Testing Requirements

### Unit tests

- scheduler executes with jitter but without duplicate runs
- stale lock recovery
- state synthesis with missing sources
- rate limiter and cooldown logic
- offline queue replay and idempotency
- emotional continuity baseline / shift calculations

### Integration tests

- curator start / stop lifecycle
- cron fallback skips if curator is healthy
- failed Memory transport spools work offline
- replay drains queue once transport returns
- state tracker handles empty Memory database

### Resource tests

- repeated state refresh does not grow memory over time
- idle loops do not exceed configured frequency
- consolidation respects concurrency budget

## Success Criteria

The implementation is successful when:

- persistent curator and cron fallback both work
- state snapshots can answer what the user is doing using active app, last message time, and optional calendar
- emotional continuity is available for next-session enrichment
- proactive outreach is useful, rare, and rate-limited
- MacBook Air M2 idle overhead stays within budget
- offline queue preserves work without loss
- privacy boundaries are enforced by default
