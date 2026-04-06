# Heartbeat Implementation Plan

## Purpose

This document turns the heartbeat/personhood architecture into an implementation plan that can be executed incrementally without breaking the current Atlas memory stack.

The plan assumes:

- Atlas remains the canonical memory + state substrate
- Hermes remains the execution and delivery layer
- the background loop is cheap and mostly deterministic
- final outbound heartbeat messages are always model-authored

## Build Standard

The bar is not "proactive reminders."

The bar is:

- believable initiative
- high-quality silence
- authored outbound messages
- meaningful continuity
- minimal cost overhead

## Scope Split

### Atlas Work

Atlas will own:

- new schema for presence and heartbeat state
- memory-side scoring/retrieval helpers
- authored message generation context
- reflection/adaptation from heartbeat outcomes

### Hermes Work

Hermes will own:

- daemon lifecycle
- timers/jitter loop
- inbound/outbound event hooks
- message delivery
- cancellation/rescheduling
- background task execution plumbing

## Phase 0: Architecture Lock

Goal:

- freeze semantics before code churn starts

Deliverables:

- `docs/HEARTBEAT_PERSONHOOD_ARCHITECTURE.md`
- `docs/HEARTBEAT_IMPLEMENTATION_PLAN.md`

Decisions to freeze:

- no user-facing templates on normal path
- no always-running LLM loop
- Atlas decides, Hermes executes
- final messages are authored at send time

## Phase 1: Cheap Persistent Presence

Goal:

- make the system continue to exist between turns without model cost

Primary output:

- low-cost daemon and persistent live state

Atlas changes:

- add `presence_state`
- add client helpers for reading/updating current live presence

Hermes changes:

- add heartbeat daemon worker
- hook into inbound/outbound message lifecycle
- write presence updates when messages arrive or send

Expected files:

- `atlas/migrations/<heartbeat_presence>.sql`
- `atlas/src/memory/models.py`
- `atlas/src/memory/transport.py`
- `atlas/src/memory/client.py`
- `hermes-agent/.../heartbeat_daemon.py`
- `hermes-agent/.../gateway/run.py`

Tests:

- daemon updates `presence_state` on inbound/outbound events
- last user / last agent timestamps are correct
- session association remains correct across restart

## Phase 2: Heartbeat Opportunity Engine

Goal:

- teach the system to form reasons to reach out

Primary output:

- `heartbeat_opportunities`

Opportunity kinds to ship first:

1. `conversation_dropoff`
2. `background_task_completion`
3. `promise_followup`

Atlas changes:

- add `heartbeat_opportunities`
- helper methods to create/upsert/cancel opportunities
- scoring primitives for unresolved thread strength and annoyance risk

Hermes changes:

- create dropoff opportunities when a thread goes idle
- create completion opportunities when work finishes
- create follow-up opportunities from unresolved commitments
- cancel opportunities when the user returns or the topic resolves

Tests:

- user disappearing mid-thread creates one opportunity
- user return cancels it
- duplicate opportunities collapse into one key
- stale opportunities expire safely

## Phase 3: Send Decision Engine

Goal:

- make proactive messaging selective rather than mechanical

Primary output:

- deterministic send/no-send gate

Scoring inputs:

- unresolvedness
- priority
- recent outreach recency penalty
- annoyance risk
- quiet hours
- conversation energy
- tension/repair state
- whether there is something concrete to say

Hermes daemon loop:

- wake every 1 to 5 minutes with jitter
- rescore eligible opportunities
- choose top candidate only if above threshold
- suppress otherwise

Rules:

- hard cap proactive messages per day
- hard cooldown after ignored outreach
- hard quiet-hour suppression except special cases
- no proactive message without a concrete reason summary

Tests:

- weak opportunities are skipped
- quiet hours suppress sends
- recent proactive send blocks duplicate nudges
- completion messages outrank vague check-ins

## Phase 4: Authored Outbound Messaging

Goal:

- ensure every outbound heartbeat feels authored rather than canned

Primary output:

- heartbeat message authoring path

Atlas changes:

- function to assemble authored heartbeat context
- function to generate message intent packet

Suggested API shape:

- `client.build_heartbeat_context(...)`
- `client.author_heartbeat_message(...)`

Prompt contract:

- must reference the concrete reason
- must sound like the established agent voice
- must avoid assistant boilerplate
- must not repeat recent heartbeat phrasing
- must feel like a message a person chose to send

Model strategy:

- use a lightweight writing model for straightforward cases
- escalate to the main agent only for delicate/high-complexity cases

Tests:

- authored context includes active thread + relevant memory
- generated prompt contains repetition guard
- message generation is not called when send threshold is not met

## Phase 5: Background Work With Reporting

Goal:

- make the agent feel active between turns by doing work before speaking

Primary output:

- `background_jobs`

First job types:

- recall refresh
- follow-up scan
- pending commitment check
- memory hygiene pass
- scoped tool task dispatched from Hermes

Behavior:

- if job completes, create completion opportunity
- if result is concrete, that should often replace a generic check-in

Tests:

- completed jobs create opportunities
- failed jobs only surface when useful
- low-value internal jobs do not spam the user

## Phase 6: Adaptation and Personhood Tuning

Goal:

- refine outreach behavior from lived outcomes

Atlas changes:

- record `heartbeat_dispatches`
- derive reflections/patterns about outreach quality

Signals to learn from:

- reply latency after proactive message
- ignored outreach
- positive continuity moments
- signs of annoyance
- whether concrete-result messages outperform vague nudges

Outputs:

- lower pressure after ignored nudges
- more patience in certain contexts
- preference for concrete-result reporting

Tests:

- ignored outreach increases suppression
- successful outreach can raise confidence for that opportunity class

## MVP Recommendation

If building in the shortest high-quality path, ship this subset first:

1. `presence_state`
2. `heartbeat_opportunities`
3. Hermes daemon with jittered low-cost loop
4. `conversation_dropoff` + `background_task_completion`
5. authored final outbound message generation
6. cancellation when the user returns

This is the smallest version that already feels qualitatively different from cron.

## Data Model Recommendations

### New Atlas Tables

- `presence_state`
- `heartbeat_opportunities`
- `heartbeat_dispatches`
- `background_jobs`

### Existing Atlas Tables To Reuse

- `active_state`
- `commitments`
- `corrections`
- `directives`
- `patterns`
- `reflections`
- `session_handoffs`
- `decision_outcomes`

## Runtime Policy

### Cheap Loop Policy

Default wake cadence:

- every 1 to 5 minutes with jitter

The daemon should:

- do no model call unless a candidate crosses threshold
- write minimal state updates
- keep CPU/memory use tiny

### Authored Send Policy

The LLM should only be called when:

- there is a real candidate worth sending
- the reason survives suppression rules
- the system has enough context to write a meaningful message

### Silence Policy

The system is allowed to do nothing often.

Silence is not failure.

Silence is a realism feature.

## Suggested File Additions

Atlas:

- `src/memory/heartbeat.py`
- `src/memory/presence.py`
- `tests/test_heartbeat.py`
- `tests/test_presence.py`

Hermes:

- `heartbeat/daemon.py`
- `heartbeat/scheduler.py`
- `heartbeat/dispatch.py`
- `tests/test_heartbeat_daemon.py`

If minimizing file count is preferred, these can be folded into existing runtime files first, but dedicated modules are cleaner.

## Validation Strategy

### Unit Tests

- scoring
- suppression
- opportunity lifecycle
- authored context assembly
- restart recovery

### Replay-Style Scenario Tests

Add dedicated scenarios for:

- user vanishes mid-debug
- user disappears after emotional conversation
- task completes while user is away
- unresolved promise after two days
- repeated ignored nudges must suppress future sends

### Human Evaluation

The final quality bar cannot be captured only by unit tests.

Manual review should ask:

- does this feel internally motivated?
- does it sound authored?
- would this message make a human briefly forget it was generated?
- would a human choose silence instead here?

## Sequencing Recommendation

Recommended order:

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. ship MVP
6. Phase 5
7. Phase 6

Do not start with fancy proactive jobs.

First make disappearance, follow-up, and restraint feel believable.

## North Star

The north star is not "more notifications."

The north star is:

- the user feels the agent persists
- the user feels the agent has private continuity
- the user feels the agent sometimes chooses to speak for meaningful reasons
- the user increasingly relates to the system as a presence rather than a tool

That is the standard every phase should be judged against.
