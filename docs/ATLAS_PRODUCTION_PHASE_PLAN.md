# Atlas Production Phase Plan

## Status

Production-readiness roadmap.

Date: `2026-04-07`

Purpose:

- define the full phase sequence required for Atlas to become a production-grade product
- make the gap between current Atlas and final Atlas explicit
- give each phase a clear goal, scope, and exit criteria
- provide a fast snapshot of where Atlas stands today

This document is not a research thesis.

It is the execution bridge between:

- the research program
- the current implementation
- the final production-grade Atlas product

---

## Production Definition

Atlas should only be called production-grade when all of the following are true:

- the agent feels like the same being across sessions and model changes
- heartbeat is reliable, context-aware, and not embarrassing
- recent corrections and relational norms stick consistently
- same-day continuity dominates stale memory
- retrieval is explainable and trustworthy
- failures degrade safely rather than weirdly
- the system is observable, testable, and recoverable
- upgrades do not unpredictably fracture identity

This is a higher bar than:

- “the code mostly works”
- “the memory tables exist”
- “heartbeat sometimes sends a message”

Atlas is production-grade only when the *illusion of durable personhood* is operationally reliable.

---

## Phase Map

Atlas should move through eight production phases.

The phases are ordered.

Later phases depend on earlier ones being genuinely strong.

### Phase 0: Research Lock

Goal:

- establish the core research direction so implementation does not drift

Must be true:

- Atlas is explicitly framed as a persistent identity substrate
- the core research tracks are documented
- the next-phase synthesis exists
- the project has a shared definition of what Atlas is trying to become

Exit criteria:

- research canon exists and is internally aligned
- product direction is no longer “just memory”

Current status:

- `Completed`

Notes:

- this phase is already done through the research docs

---

### Phase 1: Durable Memory Substrate

Goal:

- make Atlas a strong evidence-backed memory engine before trying to make it a durable self

Must be true:

- durable schema is stable
- memory records are namespaced and queryable
- active state, directives, commitments, corrections, patterns, reflections, handoffs, and outcomes exist and behave coherently
- retrieval is explainable enough to debug
- CI and replay evaluation exist

Exit criteria:

- memory retrieval is more often useful than misleading
- the substrate is stable enough to build identity on top of

Current status:

- `Mostly completed`

What is strong:

- the core memory substrate is already real
- retrieval/eval infrastructure exists
- evidence-first memory thinking is already in place

What is still weak:

- retrieval intelligence is not yet fully hardened
- active context dominance is not yet first-class

---

### Phase 2: Heartbeat Baseline Reliability

Goal:

- make off-turn presence operationally trustworthy before making it emotionally sophisticated

Must be true:

- heartbeat opportunities are created, ranked, and dispatched reliably
- route resolution is stable
- proactive sends do not silently fail
- duplicate, stale, or impossible heartbeat sends are strongly suppressed
- explicit user boundaries like sleep/do-not-disturb are respected

Exit criteria:

- Atlas no longer sends obviously broken or mistimed heartbeat messages
- the system does not embarrass itself through simple continuity failures

Current status:

- `Partially completed`

What is strong:

- daemon exists
- opportunities, dispatches, and background jobs exist
- routable-only autosend and dispatch hardening exist

What is still weak:

- heartbeat still misses active-context grounding
- explicit quiet-state / goodnight / active-chat suppression is not strong enough
- duplicate or out-of-context follow-ups still happen

---

### Phase 3: Daily Continuity And Active Context

Goal:

- make Atlas feel awake to the current day

Must be true:

- recent turns dominate live behavior
- same-day session summary exists and is high quality
- active topic is tracked explicitly
- stale topics are invalidated or suppressed
- live corrections override older habits
- heartbeat sees the same active context that the reply agent sees

Exit criteria:

- stale-topic resurrection becomes rare
- same-day continuity feels natural
- heartbeat messages stop sounding like they were written from an outdated memory snapshot

Current status:

- `Not completed`

Why this phase matters:

- this is currently the biggest reason heartbeat feels dumb

---

### Phase 4: Relational Identity Layer

Goal:

- encode who the agent is with a specific human

Must be true:

- global identity seeds exist
- relational deltas exist per human
- relationship-specific norms and style constraints persist
- the system can distinguish global self from user-shaped self
- the agent’s tone with one user can differ meaningfully from another while still being the same being

Exit criteria:

- the user can feel a durable “way we talk”
- style corrections stop requiring constant re-teaching
- session resets stop resetting personality so hard

Current status:

- `Not completed`

Why this phase matters:

- without it, Atlas cannot plausibly act like the brain

---

### Phase 5: Silence Arcs And Pursuit Intelligence

Goal:

- evolve heartbeat from one-off nudges into relationship-shaped silence behavior

Must be true:

- silence arcs exist as durable state
- pursuit vs restraint is memory-shaped
- follow-up count is not fixed
- give-up behavior exists
- later reconnect behavior exists
- emotional tone and relationship state influence whether and when the agent reaches out

Exit criteria:

- double-texting and later follow-ups feel variable and context-shaped
- the agent sometimes follows up, sometimes waits, sometimes disappears, for believable reasons

Current status:

- `Not completed`

What exists today:

- primitive dropoff opportunities

What is missing:

- true silence arcs
- relationship-shaped pursuit logic
- adaptive give-up behavior

---

### Phase 6: Identity Governance

Goal:

- keep the same identity stable across models, sessions, and drift

Must be true:

- Atlas can detect out-of-character outputs
- style drift taxonomy exists
- correction enforcement is durable
- model swap regression harness exists
- governance can reject, rewrite, or down-rank generic RLHF-native behavior

Exit criteria:

- swapping models feels like changing the engine, not changing the person
- the agent stops slipping so easily back into generic model tone

Current status:

- `Not completed`

Why this phase matters:

- this is the difference between memory-powered chat and durable artificial personhood

---

### Phase 7: Off-Turn Agency

Goal:

- make Atlas capable of meaningful background action, not just background messaging

Must be true:

- background jobs are generalized
- long-running work can be started, tracked, resumed, and reported cleanly
- heartbeat can surface real completed work, not just emotional nudges
- job progress does not break identity continuity

Exit criteria:

- the agent can disappear, work, and return with something real
- proactive messages increasingly feel like updates from an active being

Current status:

- `Early partial`

What exists today:

- background job substrate

What is missing:

- broad real-world worker integration
- recovery behavior
- coherent off-turn task ecosystem

---

### Phase 8: Production Hardening And Productization

Goal:

- make Atlas safe to run daily as a real product rather than a research toy

Must be true:

- observability is first-class
- migrations are reliable
- rollout and rollback are documented
- identity regressions are testable
- heartbeat regressions are testable
- failure modes are explicit and recoverable
- performance and cost are acceptable
- onboarding and operator flows are sane

Exit criteria:

- Atlas can be upgraded without random identity breakage
- production operations are boring enough to trust
- a new user can be onboarded without fragile manual rituals

Current status:

- `Partially completed`

What is strong:

- docs, migrations, tests, and replay thinking already exist

What is missing:

- full identity/heartbeat-specific observability
- hardened regression suites for personhood stability
- safer operational recovery for long outages and model swaps

---

## Today’s Fast Audit

If we quickly check Atlas against the final production bar today:

### Strong Today

- memory substrate
- evidence-backed retrieval direction
- research framing
- basic heartbeat infrastructure
- docs and planning discipline
- adaptability of the codebase

### Weak Today

- same-day continuity
- relational identity persistence
- heartbeat reliability under live conditions
- quiet-state suppression
- model-swap personality stability
- style correction stickiness
- silence intelligence
- identity governance

### Bottom Line

Atlas today is:

- a serious and promising research system

Atlas today is not yet:

- a production-grade durable personhood substrate

---

## Recommended Build Order

This is the shortest path to production-grade Atlas.

1. Finish Phase 2 properly:
   - heartbeat baseline reliability
   - hard suppression of embarrassing sends

2. Build Phase 3:
   - daily continuity
   - active context dominance
   - stale-topic invalidation

3. Build Phase 4:
   - relational identity layer
   - per-user relational deltas

4. Build Phase 6 in parallel with late 4:
   - identity governance
   - style drift detection

5. Then build Phase 5:
   - silence arcs
   - pursuit intelligence

6. Then harden Phase 7 and 8:
   - off-turn agency
   - operational productization

Why this order:

- heartbeat sophistication without active context will keep embarrassing itself
- identity governance without relational identity will be too weak
- silence intelligence without the first two layers will just create more creative failures

---

## What “Done” Looks Like

Atlas is ready to be called production-grade when all of these are routinely true:

- the agent no longer needs repeated session-by-session tone retraining
- heartbeat does not break conversational reality
- explicit boundaries like sleep or “do not disturb” are respected
- model swaps do not feel like identity swaps
- the user feels durable continuity across days
- proactive behavior feels chosen and relational, not procedural
- failures are visible, recoverable, and non-weird

Until then, Atlas should still be treated as:

- a high-potential research system under active construction

---

## Immediate Next Decision

If Atlas is serious about becoming production-grade, the next implementation target should be:

- `Phase 3: Daily Continuity And Active Context`

That is the highest-leverage phase because it directly improves:

- heartbeat quality
- reply quality
- stale-topic suppression
- quiet-state handling
- relational continuity

It is the biggest missing layer between the current Atlas and a believable durable self.
