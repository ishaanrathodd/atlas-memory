# Heartbeat Implementation Plan

## Purpose

This document tracks heartbeat as an implementation program, not just a design idea.

Related research:

- `docs/ATLAS_IDENTITY_RESEARCH_REPORT.md`

It answers:

- what is already shipped
- what remains fragile
- what the next best upgrades are
- what order we should build them in

## Current Build State

Heartbeat is partially shipped and live.

### Completed

- Atlas schema for:
  - `presence_state`
  - `heartbeat_opportunities`
  - `heartbeat_dispatches`
  - `background_jobs`
- Atlas client/transport/bridge support for:
  - presence sync
  - opportunity creation and listing
  - dispatch recording
  - background job lifecycle
  - dispatch context packets
- Hermes built-in heartbeat hook
- Hermes heartbeat daemon
- authored proactive dispatch path
- current opportunity kinds:
  - `conversation_dropoff`
  - `promise_followup`
  - `background_task_completion`
- ranking improvements:
  - dispatch cooldown
  - same-kind recency penalties
  - rhythm profile
  - response profile
  - response quality
  - thread emotion profile
- Signal live-session route persistence and session-id normalization fixes

### Partially Completed

- off-turn agency via `background_jobs`
- adaptation from outreach outcomes
- thread-level emotional continuity

These exist, but they are early and should not yet be considered fully hardened.

### Not Yet Complete

- generalized background workers
- robust restart repair / stale-state recovery
- first-class observability for heartbeat internals
- richer opportunity taxonomy
- strong suppression analytics
- reflection-driven behavior tuning

## Build Standard

The quality bar is still:

- no user-facing templates on the normal path
- proactive messages must feel chosen
- silence must be a first-class outcome
- the daemon must stay cheap
- Hermes changes should remain conflict-light
- all secrets remain in `~/.hermes/.env`

## Current Architecture Split

### Atlas Owns

- memory-side state
- selection logic
- dispatch context
- response/rhythm/emotion inference
- live route resolution support

### Hermes Owns

- daemon lifecycle
- hooks
- polling
- delivery
- background job plumbing

## Phase Status

## Phase 0: Architecture Lock

Status: `Completed`

Artifacts:

- `docs/HEARTBEAT_PERSONHOOD_ARCHITECTURE.md`
- `docs/HEARTBEAT_IMPLEMENTATION_PLAN.md`

Frozen decisions:

- no always-running LLM loop
- no normal-path user-facing templates
- Atlas decides, Hermes executes
- final proactive messages are authored at send time

## Phase 1: Cheap Persistent Presence

Status: `Completed`

Shipped:

- persistent `presence_state`
- user/assistant presence updates from gateway hooks
- daemon startup path
- live off-turn relational state

Important note:

- this phase initially had a real session-binding bug
- the bug is now fixed via deterministic session normalization and route metadata sync

## Phase 2: Heartbeat Opportunity Engine

Status: `Completed`

Shipped:

- `conversation_dropoff`
- `promise_followup`
- `background_task_completion`
- opportunity persistence
- cancellation / expiration behavior

Known limitation:

- stale opportunity recovery after downtime is still weak
- heartbeat does not yet preserve and intelligently reignite missed intent after long model/provider/gateway outages

## Phase 3: Send Decision Engine

Status: `Completed (v1)`

Shipped:

- top-candidate ranking
- same-session proactive cooldown
- same-opportunity retry suppression
- same-kind recency penalties
- preference toward stronger moves like concrete completion reports

Status caveat:

- functionally present, but still needs hardening and observability

## Phase 4: Authored Outbound Messaging

Status: `Completed (v1)`

Shipped:

- authored dispatch prompt
- authoring brief
- recent messages
- communication constraints
- active state / commitments / corrections
- response profile
- thread emotion profile
- background job context

Known limitation:

- authoring quality is only as good as the dispatch context and route health

## Phase 5: Background Work With Reporting

Status: `Partially Completed`

Shipped:

- `background_jobs`
- job lifecycle transitions
- automatic completion opportunity creation
- dispatch prompt includes linked completed work

Not yet shipped:

- generalized autonomous background workers
- progress-driven reporting loops
- broad off-turn task execution ecosystem

## Phase 6: Adaptation and Personhood Tuning

Status: `Partially Completed`

Shipped:

- rhythm profile
- response profile
- response-quality inference
- thread-emotion profile

Not yet shipped:

- higher-order reflection from proactive outcomes
- long-term personalization of outreach style
- stronger suppression learning

## Immediate Upgrade Priorities

These are the highest-value upgrades from here.

## Priority 1: Reliability and Silent-Failure Hardening

Why first:

- a silent heartbeat failure is worse than a weak message

Build:

- heartbeat health checks
- route validation before send
- clearer failure recording for no-route / no-session / stale-opportunity cases
- restart reconciliation for stale presence and live routes
- bridge lifecycle audit for subprocess and file-descriptor churn

Definition of done:

- we can explain why any given heartbeat did or did not send

## Priority 2: Restart and Downtime Recovery

Build:

- recover live session bindings after restart
- refresh stale presence state
- rescore still-viable opportunities after downtime
- mark truly missed opportunities as expired instead of leaving ambiguity
- preserve transiently failed heartbeat intent so it can be reconsidered when the system becomes healthy again
- age missed opportunities into better post-outage re-entry moves instead of replaying stale nudges
- add boot-time reconciliation for “the gateway/model/provider was down, but now I am back”

Definition of done:

- gateway restart does not silently sever heartbeat continuity
- the agent can come back after a long unhealthy period and either:
  - send a still-valid recovery message
  - transform the old missed moment into a more appropriate reconnect
  - suppress it cleanly if the moment is dead

## Priority 3: Real Off-Turn Agency

Build:

- first real background worker classes
- scoped “keep working while user is away” flows
- progress updates in `background_jobs`
- richer completion reporting

Examples:

- tracing a bug and reporting findings later
- scanning unresolved commitments and preparing a report
- refreshing relevant memory state before proactively resuming a thread

Definition of done:

- heartbeat sometimes returns with meaningful work, not just nudges

## Priority 4: Better Opportunity Taxonomy

Build:

- `emotional_repair`
- `gentle_resume`
- `night_recap`
- `morning_resume`
- `deadline_checkin`
- `resolved_thread_resume`

Only add kinds that materially improve behavior, not taxonomy for its own sake.

## Priority 5: User-Specific Outreach Adaptation

Build:

- per-kind landing quality trends
- time-of-day preference learning
- playful vs direct style preference learning
- user-specific cooldown tuning

Definition of done:

- heartbeat starts feeling tailored rather than just generally careful

## Priority 6: Observability and Debuggability

Build:

- inspect current `presence_state`
- inspect pending opportunities
- inspect top candidate and suppression reasons
- inspect recent dispatches and outcomes
- inspect live route resolution

Definition of done:

- debugging heartbeat no longer requires ad hoc log spelunking

## Potential Future Upgrades

These are strong, but not as urgent as reliability + agency.

### Reflection-Driven Heartbeat Policy

Use dispatch history to derive durable reflections such as:

- “soft followups after tense threads underperform”
- “completion reports get strong reopen rates”
- “late-night nudges are usually bad”

### Better Emotional Continuity

Go beyond thread emotion to relationship-level pacing:

- cooling-off periods
- repair debt
- recent overload estimation
- “not now” confidence

### Multi-Opportunity Blending

Instead of selecting one candidate in isolation, allow the authoring layer to synthesize:

- unfinished thread + completed work
- promise follow-up + concrete new finding

This could make proactive messages feel even more human.

### Personal Presence Modes

Optional long-term behavior modes:

- more active
- more restrained
- more playful
- more work-focused

This should come only after the core system is robust.

### Outage-Aware Re-entry Recovery

This is a future feature that should only be built after basic heartbeat reliability is proven in real use.

Target behavior:

- if Hermes was paused, gateway was stopped, or no LLM/provider was available, heartbeat should not simply lose the underlying intent
- once the system is healthy again, Atlas should re-open dormant intent and ask:
  - is this still directly sendable?
  - should this be transformed into a softer reconnect?
  - is this now socially stale and better suppressed?

Examples:

- short outage:
  - retry a still-valid follow-up once routing/model/provider health returns
- long outage:
  - transform a missed `conversation_dropoff` into a higher-level “resume/reconnect” message
- very stale context:
  - suppress instead of sending a broken late nudge

This feature matters because it makes the agent feel aware of its own downtime instead of simply forgetting that it ever meant to reach out.

## Practical Next Step

If choosing one next build slice, do this:

1. harden reliability and restart recovery
2. add heartbeat observability
3. ship one real background worker path

That sequence improves both trust and product quality the fastest.

## North Star

Heartbeat succeeds when:

- the agent does not feel scheduled
- the user can disappear and still feel remembered
- the agent can return with real progress
- the proactive message feels authored and justified
- the system can stay silent when silence is the right move

That is the bar for “alive enough to matter.”
