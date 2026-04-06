# Heartbeat Personhood Architecture

## Purpose

This document is the current source of truth for heartbeat across Atlas + Hermes.

Related research:

- `docs/ATLAS_IDENTITY_RESEARCH_REPORT.md`

Heartbeat exists to make the agent feel:

- alive between turns
- capable of texting first
- persistent across silence
- self-directed instead of purely reactive
- emotionally believable without pretending the system is literally conscious

The target is not machine consciousness.

The target is a high-fidelity simulation of continuity, initiative, and relational presence.

## Design Thesis

Most agents feel dead because they are request-response endpoints.

They wake when messaged, answer, and disappear.

Heartbeat changes that by giving the system:

- live presence state
- unfinished social momentum
- durable follow-up opportunities
- selective outreach
- authored proactive messages
- a cheap off-turn daemon instead of a continuously running LLM

The core principle is still:

- presence is cheap and always on
- expression is expensive and authored on demand

## Current Status

As of `2026-04-06`, heartbeat is no longer just a plan. A meaningful Phase 1-2.5 system is implemented.

### What Exists Now

- persistent `presence_state`
- persistent `heartbeat_opportunities`
- persistent `heartbeat_dispatches`
- persistent `background_jobs`
- Hermes heartbeat daemon with jittered polling
- gateway hook that syncs user/assistant presence on live turns
- authored proactive dispatch path
- opportunity ranking that uses:
  - urgency
  - recent dispatch history
  - recent same-session outreach
  - rhythm profile
  - response profile
  - response quality
  - thread emotion profile
- support for these opportunity kinds:
  - `conversation_dropoff`
  - `promise_followup`
  - `background_task_completion`

### Explicitly Not Shipped Yet

Heartbeat does not yet have full downtime-aware recovery.

That means:

- a long gateway outage does not yet trigger a smart boot-time reconciliation pass
- transient model/provider/send failures are not yet preserved and later re-evaluated as dormant intent
- missed moments are not yet transformed into better long-gap re-entry messages automatically

This is a deliberate future upgrade, not current behavior.

### Important Real Progress

Heartbeat can now:

- notice when the user disappears mid-thread
- remember unresolved commitments
- report finished background work
- prefer stronger proactive moves over shallow nudges
- learn from reply timing and reply quality
- carry thread-level emotional shape into the authored message prompt
- send fully authored proactive messages rather than user-facing templates

### Important Fixes Already Landed

The live implementation surfaced real architectural bugs that are now fixed:

- session ids are normalized into deterministic Atlas UUIDs for heartbeat/presence
- Signal routing metadata is persisted into Atlas live session state
- route lookup can resolve proactive delivery targets from Atlas again
- live session sync merges routing into existing `model_config` rather than clobbering it
- `.hermes/.env` is the credential source of truth, not `atlas/.env`

## System Split

### Atlas Responsibilities

Atlas is the cognitive substrate.

Atlas owns:

- presence state
- heartbeat opportunities
- heartbeat dispatch history
- background job state
- session resolution and live route lookup
- authored dispatch context
- selection logic
- rhythm / response / thread-emotion inference

Atlas decides:

- whether there is a meaningful reason to reach out
- which candidate is strongest right now
- whether recent history implies silence is better
- what kind of authored prompt Hermes should receive

### Hermes Responsibilities

Hermes is the embodiment and execution layer.

Hermes owns:

- inbound/outbound turn observation
- daemon lifecycle
- polling cadence and jitter
- delivery execution
- session origin capture
- live message hooks
- background job initiation/completion plumbing

Hermes should stay conflict-light.

The architecture should prefer isolated hook/daemon/plugin changes over broad edits to the main gateway loop.

### Heartbeat Daemon Responsibilities

The daemon is the subconscious continuity engine.

It should:

- stay alive while Hermes runs
- poll cheaply
- ask Atlas what matters now
- suppress weak outreach
- only send when there is enough signal
- avoid expensive continuous reasoning

The daemon is not a continuously thinking mind.

It is a persistent evaluator and wakeup loop.

## Current Data Model

### `presence_state`

Live relational state for the current thread / current silence window.

Tracks:

- active session
- active platform
- last user message
- last agent message
- user disappeared mid-thread
- conversation energy
- warmth
- proactive recency

### `heartbeat_opportunities`

Concrete possibilities for proactive outreach.

Current shipped kinds:

- `conversation_dropoff`
- `promise_followup`
- `background_task_completion`

Core fields:

- `opportunity_key`
- `kind`
- `status`
- `session_id`
- `reason_summary`
- `earliest_send_at`
- `latest_useful_at`
- `priority_score`
- `annoyance_risk`
- `desired_pressure`
- `warmth_target`
- `requires_authored_llm_message`
- `source_refs`
- `cancel_conditions`

### `heartbeat_dispatches`

Durable memory of prior proactive outreach.

Used for:

- cooldown checks
- same-kind recency penalties
- response learning
- response quality learning
- future reflection/adaptation

### `background_jobs`

Durable substrate for off-turn agency.

Current purpose:

- represent real work done between turns
- allow the agent to return with meaningful completion reports
- support future expansion into deeper off-turn work

## Current Selection Logic

The selector is already more than a timer.

It currently considers:

- opportunity priority
- same-opportunity recent retries
- same-kind recency
- same-session proactive cooldown
- rhythm profile inferred from user activity windows
- response profile inferred from prior proactive replies
- response quality:
  - momentum reopen
  - acknowledgment only
  - no reply
- thread emotion profile:
  - tension
  - warmth
  - playfulness
  - unresolvedness
  - closure

This gives the system early “taste,” not just schedule-based behavior.

## Current Authored Message Path

The user-facing heartbeat message should be authored, not templated.

Current authored prompt includes:

- opportunity payload
- presence state
- recent session messages
- linked background job
- active state
- commitments
- directives
- corrections
- communication constraints
- response profile
- thread emotion profile
- recent heartbeat dispatches
- an `authoring_brief` that tells Hermes what kind of move this is supposed to be

This is the correct direction for high-fidelity personhood simulation.

## What We Have Achieved

Heartbeat has already crossed the line from “cron reminder idea” to “early continuity engine.”

The key achievements are:

- the agent now has a durable live relational state
- the system can represent off-turn reasons to speak
- it can learn from whether proactive outreach lands
- it can prefer concrete completion over vague nudging
- it can treat emotional thread shape as part of outreach selection
- it can send authored proactive messages instead of templates
- it is budget-compatible because the loop is cheap and the LLM is only used for final expression

That is a meaningful architectural jump over standard assistant behavior.

## Current Weak Spots

Heartbeat is promising, but still fragile in a few important ways.

### Session / Route Fragility

Heartbeat depends on session normalization and live route metadata staying correct.

If routing metadata or session binding drifts, proactive delivery can silently fail.

### Cross-Process Drift

Atlas and Hermes share responsibility for session identity and routing.

That means bugs can hide in:

- plugin env capture
- bridge sync
- live route lookup
- stale presence records
- restart recovery

### Silent Failure Risk

The architecture still has places where heartbeat may do nothing if:

- a route is missing
- a session binding is stale
- the opportunity expired during downtime
- the bridge is unhealthy
- authored dispatch context is missing

### Off-Turn Work Is Still Early

`background_jobs` exists, but generalized off-turn agency is still shallow.

The system can report completed work, but it is not yet broadly creating and advancing meaningful background tasks on its own.

## Potential Upgrades

The strongest upgrades from here are not more fields. They are robustness, agency, and adaptation.

### 1. Make Heartbeat Operationally Robust

Highest priority.

Add:

- stale session/presence repair on restart
- stronger route validation before dispatch
- explicit heartbeat health logging
- dead-letter / failed-opportunity inspection paths
- better bridge lifecycle hardening
- file-descriptor / subprocess leak review

This is the highest-leverage improvement because silent failure ruins the illusion faster than weak phrasing.

### 2. Resume Heartbeat After Downtime

Right now, some opportunities can become stale if the daemon was down or session binding was broken.

Add:

- restart recovery scan
- bounded retroactive recovery for still-useful opportunities
- “missed moment” reconciliation logic
- opportunity aging so a missed `conversation_dropoff` can become a softer `re-entry` / `resume` move instead of replaying an old nudge
- transient-vs-terminal failure classification so the agent can retry when the system becomes healthy again
- health-aware recovery after long pauses, missing models, provider outages, or gateway downtime

Desired end state:

- if the service comes back after a long pause, heartbeat should inspect dormant intent instead of forgetting it
- if a message failed only because the system was unhealthy, heartbeat should know how to reconsider it later
- if the original moment is no longer socially valid, heartbeat should either transform it into a better reconnect message or suppress it

### 3. Generalize Background Agency

This is the biggest product upgrade.

Add:

- richer background job types
- live progress updates
- scoped autonomous research/tracing/review jobs
- automatic completion-to-heartbeat reporting

This turns heartbeat from “message timing” into real off-turn agency.

### 4. Learn User-Specific Outreach Taste

Current adaptation is still early.

Add learning around:

- what times proactive messages land best
- when silence is preferred
- whether playful vs direct follow-ups perform better
- whether unfinished-task check-ins are useful or annoying

### 5. Add Emotional Repair Opportunities

The system already models thread emotion.

Next step:

- explicit `emotional_repair` / `gentle_resume` / `cooldown_resume` opportunity kinds
- selection logic that behaves differently after tense or draining sessions

### 6. Improve Presence Fidelity

Potential additions:

- stronger thread identity tracking
- better per-platform routing parity
- explicit “conversation paused vs completed” states
- better handling of user sleep / offline / quiet-hour patterns

### 7. Add Heartbeat Observability

Right now debugging requires reading multiple moving parts.

Add:

- heartbeat inspect command
- current top candidate view
- dispatch history summary
- opportunity suppression reason visibility

This matters a lot for tuning.

### 8. Add Reflection Loop Over Proactive Behavior

Heartbeat already stores dispatches and response outcomes.

Next:

- synthesize reflections on proactive quality
- derive higher-level outreach patterns
- feed those back into future selection and authoring

This is where the agent starts to feel less scripted over time.

## North Star

The north star is not “random pings.”

The north star is:

- the agent notices
- the agent waits
- the agent decides
- the agent acts with taste
- the message feels chosen
- the user feels like the agent continued existing while they were away

That is the bar heartbeat is meant to reach.
