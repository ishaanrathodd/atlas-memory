# Memory Implementation Plan

## Purpose

This document turns `MEMORY_REDESIGN.md` into a concrete execution plan.

It answers:

- what to build first
- what schema changes to make
- which code paths to touch
- how to migrate safely
- how to validate that memory quality is actually improving


## Status Snapshot

As of `2026-04-04`, the following layers are implemented in code, migrated in Supabase, and rolled into the live `main` namespace:

- `agent_namespace` / instance-safe foundation
- `active_state`
- `directives`
- `timeline_events`
- `decision_outcomes`
- `patterns`
- `commitments`
- `corrections`
- `session_handoffs` (short-range baton continuity)
- always-on curator runtime (`hot`/`warm` paths active, cron demoted to backstop)

What is already true in live Memory:

- memory is isolated by runtime instance/profile namespace, not by platform
- session-start enrichment now includes stronger structured memory layers
- directives persist and are injected as standing rules
- active commitments are cleaned aggressively and no longer pollute prompts
- corrections suppress known-bad resurfacing
- low-signal greetings no longer drag in trivial prior conversation snippets
- unsupported preference hallucinations from summaries are blocked from materializing into `decision_outcomes`
- `Active life snapshot` is stable at the prompt level and no longer oscillates through repo sludge, system-status blobs, or testing chatter
- oversized identity/profile facts are rendered as compact human-readable context instead of giant CRM-like blobs
- generic advice queries no longer surface noisy Memory-build timeline summaries or raw session export text
- `timeline_events` now include day/week rollups and rank correctly for period-style questions
- `decision_outcomes` now require stronger grounding and cleaner lessons before they survive
- `patterns` now use stricter evidence rules and only promote live patterns that still have repeated support
- automatic session rollover now preserves last-thread continuity via handoff state
- live append/end hooks now run continuity curation without requiring cron as the primary driver
- end-of-session deep consolidation runs automatically and retries unsummarized backlog in bounded batches
- warm curation is hardened with idempotency keys, lock windows, and retry/backoff around summary pipelines

What is still actively left:

- restoring Memory as an additive Hermes memory provider instead of a deep runtime fork
- a polished `atlas` provider setup flow under `hermes memory setup`
- genericizing extraction and enrichment so Memory adapts to any user over time, not just current talking style
- `reflections`
- user-facing forget / revoke / override flows
- proactive presence / heartbeat behavior
- optional future multi-profile registry and visibility model
- deeper long-horizon evaluation coverage (LLM-in-the-loop + adversarial temporal suites), beyond deterministic replay fixtures
- retirement plan for old unused tables/views after confidence windows


## High-Uplift Tech Decision (`2026-04-05`)

Final recommendation:

- implement a `Temporal Memory Graph` in Supabase (`Postgres + pgvector`) and add graph-aware retrieval as a first-class route

This is the single highest-impact addition because it upgrades Memory from “retrieve similar snippets” to “reason over evolving relationships across months/years.”

### Tech stack to add now (high impact only)

1. `Temporal Memory Graph` layer in Supabase
- new graph tables:
  - `memory_entities`
  - `memory_relations`
  - `relation_evidence`
  - `relation_validity` (`valid_from`, `valid_to`, `superseded_by`, `confidence`)
- benefit: explicit lifecycle handling (what became true, false, or outdated over time)

2. Graph-aware retrieval route (`long_horizon_reasoning`)
- add an intent route that retrieves:
  - multi-hop graph neighborhoods
  - contradiction/supersession edges
  - timeline rollups
- fuse with existing semantic+keyword retrieval (RRF/hybrid ranking)
- benefit: materially better “connect the dots” reasoning

3. Optional orchestrator for graph extraction/querying (`LlamaIndex PropertyGraphIndex`)
- use for extractor/retriever plumbing and custom graph retrievers
- keep Supabase as source-of-truth; orchestrator is optional infra, not ownership boundary
- benefit: faster implementation speed without locking product logic to a framework

4. Long-horizon eval expansion (LLM-in-the-loop)
- keep deterministic replay gates
- add hard temporal/multi-hop eval suites judged for factual support and chain consistency
- benefit: catches subtle regressions deterministic string checks miss

### What this improves (questions to ask the agent)

Use these prompts as acceptance checks for the new graph layer:

1. Recurring trap detection
- `what pattern keeps repeating every month that hurts execution?`
- expected improvement: identifies repeated causal chain, not just isolated reminders

2. Cross-month causal reasoning
- `connect the January redesign decision to the March delay; what were the intermediate steps?`
- expected improvement: explains multi-hop path (`decision -> dependency shift -> bottleneck -> delay`) with evidence

3. Contradiction and change tracking
- `what did we believe 6 weeks ago that is no longer true now?`
- expected improvement: returns superseded claims and why they changed

4. Long-range planning continuity
- `based on the last 3 similar project attempts, what plan has the highest chance of working now?`
- expected improvement: synthesizes outcomes across attempts instead of citing one anecdote

5. Relationship-aware coaching
- `which open commitments are blockers for other goals right now?`
- expected improvement: surfaces dependency graph of commitments/outcomes

### What not to over-invest in first

- do not do full enterprise GraphRAG indexing complexity on day one
- do not add more generic vector retrieval tweaks as the primary roadmap

Start with `Temporal Memory Graph + graph-aware route` first, then decide whether full GraphRAG indexing modes are needed.


## Strategic Direction

Memory should now be treated as a standalone Atlas product with a thin Hermes integration seam.

That means:

- the real memory engine lives in the Atlas repo
- Hermes built-in memory stays intact
- Atlas integrates as an external Hermes memory provider
- user-owned provider code should live outside the Hermes checkout so `hermes update` keeps working cleanly

Current reality check:

- Atlas is **not** already more mature than Honcho, Supermemory, or RetainDB
- Atlas is only worth continuing if it differentiates on trustworthy personal continuity, not generic memory plumbing
- genericization is now a first-class requirement; extraction rules cannot be tied to one user's current phrasing, slang, or mood

Practical implication:

- stop pushing more Atlas semantics deep into `hermes-agent`
- use the upstream memory-provider seam as the stable integration boundary
- keep plugin UX beautiful enough that a new user can choose `atlas` from `hermes memory setup` and paste credentials without reading source code


## Guiding Constraints

1. Do not break current Memory transcript persistence while redesigning memory.
2. Keep `episodes` as the canonical evidence layer.
3. Prefer additive migrations before destructive cleanup.
4. Improve Memory-first daily behavior early, before deeper reflective memory.
5. Build evaluation alongside memory features.
6. Session boundaries should become an internal implementation detail, not a user-visible reset.
7. The runtime layer and memory layer should be described separately; avoid overloaded “Hermes vs Memory” wording.
8. The primary memory brain should be event-driven and always-on, not a scheduled cron loop.
9. Continuous memory must not imply continuous expensive LLM usage; use heuristic-first curation and selective promotion.


## Terminology

Use these terms consistently in this plan:

- `runtime layer`
  - message ingress
  - session lifecycle / routing
  - model call orchestration
  - session rollover detection

- `memory layer`
  - evidence storage
  - continuity state
  - derived memory compilation
  - retrieval / enrichment

Avoid using `Hermes vs Memory` as architecture shorthand inside this document. There is one agent. The useful boundary is runtime layer vs memory layer.


## Architecture Correction

The current scheduled curator/cron setup was acceptable as a bootstrap path, but it should not remain the main memory operating model.

Target architecture:

- one always-on background memory curator process
- event-driven work submission whenever new evidence arrives
- a cheap hot path for recent continuity and correction handling
- a selective warm path for active state and handoff summaries
- a slower cold path for rollups, outcomes, patterns, reflections, and pruning

Design rule:

- always-on curator does **not** mean always-on LLM usage
- message writes and lightweight heuristics should be cheap and immediate
- LLM calls should happen only at promotion boundaries or on meaningful state change

This means the current cron job should eventually become:

- a safety net / maintenance backstop
- not the primary mechanism by which Memory learns what just happened


## Continuity Principle

The most important unsolved continuity gap is not long-range memory. It is short-range baton continuity across session boundaries.

Desired user experience:

- the user should never need to manually “start a new session”
- the user should never feel that a session reset caused Memory to forget the last active thread
- the last unresolved topic, next step, recent emotional tone, and active assistant promise should carry across automatic rollover

This should be treated as first-class product behavior, not as an optional later refinement.


## Delivery Roadmap

This is the recommended roadmap from `2026-04-03` onward.

### Stage 1: Provider foundation (`1-3 days`)

Goals:

- keep Hermes built-in memory intact
- expose Atlas as a real external Hermes provider
- make the plugin discoverable outside the Hermes checkout
- support a working `hermes memory setup` path for Atlas

Deliverables:

- external `atlas` provider plugin
- setup flow for Supabase URL/key and schema
- minimal provider lifecycle: initialize, prefetch, sync-turn, shutdown
- focused provider discovery and setup tests

### Stage 2: Genericization pass (`2-5 days`)

Goals:

- remove Ishaan-specific production heuristics
- remove talking-style-dependent phrase patches
- make extraction and retrieval adapt to new users and future personality drift

Deliverables:

- depersonalized enrichment rules
- depersonalized outcome/pattern extraction rules
- tests that use varied fixtures instead of one speaking style

### Stage 3: Live continuity (`3-7 days`)

Goals:

- make automatic session rollover invisible to the user
- keep short-range baton continuity sharp across sessions

Deliverables:

- dedicated live baton / handoff object
- recent open-loop carry-forward
- last-thread bootstrap tuned for new-session startup
- continuity tests around rollover and recent-thread recall

### Stage 4: Always-on curator (`1-2 weeks`)

Goals:

- replace cron as the primary memory brain
- keep cost low with heuristic-first event-driven updates

Deliverables:

- hot / warm / cold curation queues
- cheap per-message state tracking
- threshold-triggered promotion instead of periodic blind batch work
- cron retained only as a safety backstop

### Stage 5: Higher-order trust layers (`1-3 weeks`)

Goals:

- deepen Memory without making it hallucination-prone

Deliverables:

- `reflections`
- forget / revoke / override UX
- better confidence / uncertainty handling
- improved operational memory for tasks, meetings, and follow-ups

### Stage 6: Productization (`ongoing`)

Goals:

- make Atlas installable and usable by someone who is not the original builder

Deliverables:

- provider README and setup docs
- cleaner standalone package/install story
- migration guides
- plugin polish for eventual upstream PR or separate distribution


## Current Source-of-Truth Code Paths

These are the main files the redesign will touch.

### Storage and retrieval

- [models.py](/Users/ishaanrathod/.hermes/memory/src/memory/models.py)
- [transport.py](/Users/ishaanrathod/.hermes/memory/src/memory/transport.py)
- [enrichment.py](/Users/ishaanrathod/.hermes/memory/src/memory/enrichment.py)
- [fact_extraction.py](/Users/ishaanrathod/.hermes/memory/src/memory/fact_extraction.py)
- [consolidation.py](/Users/ishaanrathod/.hermes/memory/src/memory/consolidation.py)
- [bridge_cli.py](/Users/ishaanrathod/.hermes/memory/src/memory/bridge_cli.py)
- [recall.py](/Users/ishaanrathod/.hermes/memory/src/memory/recall.py)

### Runtime layer integration

- [memory_session_mirror.py](/Users/ishaanrathod/.hermes/hermes-agent/memory_session_mirror.py)
- [session.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/session.py)
- [run.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/run.py)
- [memory_bridge.py](/Users/ishaanrathod/.hermes/hermes-agent/memory_bridge.py)


## Recommended Build Order

Build in this order:

1. restore Hermes integration to the upstream memory-provider seam
2. ship Atlas as a thin external provider
3. genericize extraction and enrichment
4. live continuity + handoff
5. always-on memory curator
6. `reflections`
7. forget / revoke / override UX
8. operational memory polish for tasks / meetings / follow-ups
9. cleanup / pruning / old-table retirement

This order gives the fastest user-visible gain while keeping the hardest inference layers for later.

Current rollout state:

- phases `0` through `6` below are functionally done and live
- live continuity and event-driven curation are now implemented in the runtime path
- the next architecture milestone is Atlas-as-provider and productization hardening
- `reflections` have not started
- cleanup / quality work is no longer the only focus; operating-model changes now matter too


## Phase 0: Foundations

Status: `Done`

### Goals

- make the backend profile-safe
- preserve current behavior
- add the minimum metadata needed for future layers

### Schema changes

Add profile-aware fields where appropriate:

- `sessions.profile_id` or `sessions.agent_namespace`
- `episodes.profile_id` or `episodes.agent_namespace`
- `facts.profile_id` or `facts.agent_namespace`
- `fact_history.profile_id` or `fact_history.agent_namespace`

For Memory-first today, legacy/live rows default to the historical namespace:

- `main`

### Runtime changes

Update session/routing identity so it is profile-aware but still platform-independent.

Current risk:

- session keys are currently shaped like `agent:main:telegram:dm:...`
- they do not include profile identity

Required changes:

- extend `build_session_key(...)` in [session.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/session.py) to include profile namespace
- persist that profile namespace into routing metadata in [session.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/session.py)
- filter Memory route lookup and transcript lookup by profile namespace where appropriate

### Code touchpoints

- [session.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/session.py)
- [recall.py](/Users/ishaanrathod/.hermes/memory/src/memory/recall.py)
- [bridge_cli.py](/Users/ishaanrathod/.hermes/memory/src/memory/bridge_cli.py)
- [transport.py](/Users/ishaanrathod/.hermes/memory/src/memory/transport.py)
- [models.py](/Users/ishaanrathod/.hermes/memory/src/memory/models.py)

### Success criteria

- current Memory behavior still works for one profile
- future profiles cannot silently collide in the same backend
- platform remains only transport metadata

### Completed

- `agent_namespace` added across `sessions`, `episodes`, `facts`, and `fact_history`
- bridge/session/routing/retrieval paths are namespace-aware
- legacy rows remain backward-compatible under `main`


## Phase 1: Active State

Status: `Done, prompt-level behavior now stable`

### Goals

- give Memory a strong sense of what is happening in life right now
- improve continuity across session boundaries immediately

### New table

- `active_state`

### What should go into it

- current projects
- current blockers
- current emotional pressure
- open loops
- current priorities
- short-horizon life state

### How to populate it

Start simple:

- derive from recent sessions
- derive from recent user episodes
- derive from recent facts
- use conservative heuristics first

Do not start with fully abstract inference.

### Runtime changes

Session bootstrap should include:

- core identity facts
- active state
- a tiny recent continuity slice

### Code touchpoints

- [models.py](/Users/ishaanrathod/.hermes/memory/src/memory/models.py)
- [transport.py](/Users/ishaanrathod/.hermes/memory/src/memory/transport.py)
- [consolidation.py](/Users/ishaanrathod/.hermes/memory/src/memory/consolidation.py)
- [enrichment.py](/Users/ishaanrathod/.hermes/memory/src/memory/enrichment.py)
- [run.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/run.py)

### Success criteria

- Memory starts new sessions already aware of what is actively going on
- generic greetings do not cause broad irrelevant recall
- continuity feels stronger without huge prompts

### Completed

- `active_state` table and model are live
- current scheduled consolidation compiles active state from recent sessions/facts/episodes
- session-start enrichment injects `Active life snapshot`
- active-state prompt output is stable for generic advice queries
- repo-maintenance chatter, giant session-status blobs, and testing lines are no longer surfacing as live focus
- active-state fallback now uses the same fact humanization rules as the main fact renderer

### Still left

- make active-state wording more natural and less roadmap-shaped
- improve blockers/open-loops so they feel current, not quoted
- move active-state refresh for live continuity onto the always-on curator hot/warm path


## Phase 2: Directives

Status: `Done`

### Goals

- make hard rules deterministic
- stop relying on semantic recall for obedience

### New table

- `directives`

### What should go into it

- delegation rules
- formatting rules
- communication rules
- tool-usage rules
- standing operating rules

### Required runtime behavior

At session start:

- inject active directives

Before action planning:

- re-check directives in a lightweight rule application step

### Important semantics

- support scope
- support revocation
- support superseding
- support hard vs soft rules

### Code touchpoints

- [models.py](/Users/ishaanrathod/.hermes/memory/src/memory/models.py)
- [transport.py](/Users/ishaanrathod/.hermes/memory/src/memory/transport.py)
- [enrichment.py](/Users/ishaanrathod/.hermes/memory/src/memory/enrichment.py)
- [run.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/run.py)
- possibly [run_agent.py](/Users/ishaanrathod/.hermes/hermes-agent/run_agent.py) for a final action-planning guard

### Success criteria

- “don’t use em dashes” persists reliably
- “always delegate implementation tasks” persists reliably
- user does not need to restate rules every message

### Completed

- `directives` table and model are live
- current scheduled consolidation extracts standing rules from explicit user language
- enrichment injects `Standing directives`


## Phase 3: Timeline Events

Status: `Done, needs richer summarization later`

### Goals

- make date-based and period-based memory robust
- stop depending on raw episode search for long-range recall

### New table

- `timeline_events`

### What should go into it

- session summaries
- day summaries
- week summaries
- important events
- transitions

### Population strategy

Start with:

- one summary per completed session
- then daily rollups
- then weekly rollups

### Code touchpoints

- [consolidation.py](/Users/ishaanrathod/.hermes/memory/src/memory/consolidation.py)
- new summarization helpers under `memory/src/memory/`
- [enrichment.py](/Users/ishaanrathod/.hermes/memory/src/memory/enrichment.py)
- [recall.py](/Users/ishaanrathod/.hermes/memory/src/memory/recall.py)

### Success criteria

- “what happened last week?” works from summaries, not raw transcript luck
- “what was I doing 473 days ago?” can anchor into timeline summaries first

### Completed

- `timeline_events` table and compiler are live
- session summaries materialize into `Recent major events`
- noisy operational and reference-like timeline entries are suppressed
- generic advice queries no longer surface Memory-build maintenance summaries

### Still left

- day rollups
- week rollups
- stronger period-based recall quality
- better human summarization for long session summaries


## Phase 4: Commitments and Corrections

Status: `Done, live quality now acceptable`

### Goals

- make Memory accountable
- preserve corrections explicitly

### New tables

- `commitments`
- `corrections`

### Commitments

Store things Memory agreed to do:

- reminders
- follow-ups
- promises
- tracking obligations

### Corrections

Store things the user said were wrong:

- fact corrections
- directive clarifications
- rejected inferences
- memory disputes

### Runtime behavior

- active commitments should be visible in session bootstrap where relevant
- corrections should suppress bad resurfacing

### Code touchpoints

- [models.py](/Users/ishaanrathod/.hermes/memory/src/memory/models.py)
- [transport.py](/Users/ishaanrathod/.hermes/memory/src/memory/transport.py)
- [consolidation.py](/Users/ishaanrathod/.hermes/memory/src/memory/consolidation.py)
- [enrichment.py](/Users/ishaanrathod/.hermes/memory/src/memory/enrichment.py)

### Success criteria

- Memory remembers promises made to the user
- Memory stops repeating corrected errors

### Completed

- `commitments` and `corrections` tables are live
- stale/noisy commitments are cancelled automatically
- stale false-positive corrections are deactivated automatically
- live corrections now suppress known-bad resurfacing such as the `updated rules` misfire

### Still left

- add a clean user-facing forget / revoke / override flow on top of these layers


## Phase 5: Decision Outcomes

Status: `Done for this pass, can be recalibrated later if live drift appears`

### Goals

- make future advice outcome-aware

### New table

- `decision_outcomes`

### What should go into it

- decision taken
- alternatives considered
- rationale
- later outcome
- lesson

### Population strategy

Start manually conservative:

- only capture clearly explicit decisions and later explicit outcomes
- do not infer too aggressively from one ambiguous turn

### Runtime behavior

Advice questions should prefer:

- similar old situations
- linked outcomes
- linked lessons

### Success criteria

- Memory can say “last time this approach cost us time”
- advice feels grounded in actual prior outcomes

### Completed

- `decision_outcomes` table and compiler are live
- enrichment can inject `Relevant prior outcomes`
- open outcomes are now suppressed from advice context
- unsupported hallucinated preference outcomes are blocked

### Still left

- monitor live drift and prune any newly surfaced low-value legacy rows
- revisit only if fresh real conversations reveal a new grounding failure mode


## Phase 6: Patterns

Status: `Done for this pass, with live evidence gating now in place`

### Goals

- make Memory recognize recurring behavioral tendencies

### New table

- `patterns`

### What should go into it

- recurring strengths
- recurring traps
- decision styles
- emotional response patterns
- project behavior patterns

### Population strategy

Only infer patterns after enough repeated evidence.

Minimum bar:

- multiple supporting sessions or episodes
- confidence
- explicit evidence links

### Success criteria

- Memory can identify repeated tendencies without overfitting to one bad day

### Completed

- `patterns` table and compiler are live
- enrichment can inject `Relevant patterns`
- current live patterns cover debugging style, redesign bias, high standards, and reliability-driven emotional intensity

### Still left

- monitor live evidence quality as new memory accumulates
- expand only when a genuinely new repeated pattern family shows up


## Immediate Next Priorities

If continuing from the current state, work in this order:

1. Atlas provider seam + setup UX (`hermes memory setup` quality)
2. genericization pass across extraction/enrichment and retrieval ranking
3. long-horizon graph reasoning (`Temporal Memory Graph` + graph-aware retrieval route)
4. `reflections`
5. user-facing forget / revoke / override flows
6. observability hardening (SLOs/alerts) + LLM-in-the-loop long-horizon eval expansion
7. proactive presence / heartbeat behavior
8. optional future multi-profile registry and visibility model

Rationale:

- the core memory substrate is now real and live
- the biggest remaining gap is productization and trust operations, not base continuity
- handoff continuity and event-driven curation are in place and should now be tuned by eval data
- cron-based consolidation is now a safety backstop, not the primary memory operating model
- deeper reflective layers should land only after live continuity and curator architecture are solid


## Live Continuity and Curator Architecture

Status: `Implemented (active), ongoing hardening`

### Goals

- make new-session starts feel like an ongoing conversation
- eliminate the “Memory remembers yesterday but not the last 15-30 messages” gap
- replace scheduled cron-style curation with an always-on event-driven operating model
- preserve low cost by making LLM synthesis selective, not continuous

### Required new behavior

- maintain a short-horizon baton / handoff state across session rollover
- carry forward:
  - last active topic
  - unresolved thread
  - next expected step
  - recent emotional tone when relevant
  - active assistant promise or pending action
- allow session rollover to happen automatically without user-visible reset behavior

### Operating model

Split background memory work into three paths:

1. hot path
- runs after or near every message
- no heavy LLM assumption
- updates corrections, commitments, recent baton state, and immediate continuity flags

2. warm path
- triggered by meaningful state changes or session rollover
- can use selective small LLM summarization
- updates handoff summaries, active state refreshes, and compact session summaries

3. cold path
- runs when enough evidence accumulates or the system is idle
- updates timeline rollups, decision outcomes, patterns, reflections, and pruning

### Design rule

- always-on curator does not mean always-on expensive LLM calls
- evidence writes should be immediate
- heuristics should be cheap and frequent
- deeper synthesis should happen only when promoted by thresholds or explicit need

### Success criteria

- the user never manually manages session resets
- Memory starts a fresh session while clearly knowing what was being discussed just before rollover
- recent continuity survives even before slower higher-order layers refresh
- memory curation cost stays bounded because deep synthesis remains selective

### Current implementation notes

- live append and live session-end events call continuity curation directly
- warm path performs per-session deep consolidation and bounded backlog retry automatically
- duplicate warm events are skipped via idempotency keys and lock-window checks
- backlog processing advances with a cursor to avoid unbounded catch-up runs
- cron remains available only as operational maintenance backstop


## Phase 7: Reflections

Status: `Not started`

### Goals

- allow higher-order interpretation without pretending certainty

### New table

- `reflections`

### What should go into it

- likely fears
- major motivations
- values
- blind spots
- broader personality hypotheses

### Guardrails

- must be tentative unless strongly supported
- must carry evidence
- must be reversible
- must not be injected for ordinary turns unless relevant

### Success criteria

- Memory can answer deep interpretive questions with caution and grounding


## Prompt and Retrieval Work

## Session-start bootstrap

Status: `Partially done`

Build a dedicated session bootstrap assembler that is different from generic recall.

It should include:

1. live runtime time context
2. identity snapshot
3. active state
4. standing directives
5. very recent continuity
6. important recent events
7. optionally active commitments

This should live near or inside:

- [enrichment.py](/Users/ishaanrathod/.hermes/memory/src/memory/enrichment.py)
- [run.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/run.py)

Current live reality:

- live runtime time context is still handled outside Memory memory
- identity facts, active state, directives, major events, patterns, outcomes, and continuity are already injected
- commitments are injected only when relevant

Still left:

- a first-class handoff / baton section for short-range continuity
- a cleaner dedicated bootstrap assembler
- tighter budgeting / section prioritization for ordinary turns

## Per-turn retrieval

Keep retrieval intent-aware:

- generic → tiny context only
- advice → patterns + outcomes + relevant history
- recall → timeline-first
- interpretation → patterns + reflections + evidence

Status: `Partially done`

Completed:

- low-signal greetings now suppress trivial prior conversations and recent continuity
- open outcomes no longer leak into advice retrieval
- corrected content is filtered from facts / episodes / outcomes / patterns

Still left:

- stronger fact ranking and filtering
- better timeline-first routing for date/period questions
- stronger previous-session handoff retrieval for the most recent active thread
- better separation between ordinary prior conversation and truly useful recall


## Live Time Context

This is not durable memory.

Every turn should include authoritative current time and timezone.

Implementation note:

- do not trust old session context for current time-of-day
- do not let memory override actual runtime time

Likely touchpoints:

- [run_agent.py](/Users/ishaanrathod/.hermes/hermes-agent/run_agent.py)
- [run.py](/Users/ishaanrathod/.hermes/hermes-agent/gateway/run.py)

Status: `Partially addressed`

Completed:

- UTC handling and session metadata handling were fixed earlier in Hermes

Still left:

- formalize live temporal context as a first-class bootstrap layer in the redesign work


## Backfill Strategy

Backfill in layers. Do not try to infer everything from all history in one shot.

### Completed

1. Backfilled profile namespace support onto existing access paths.
2. Backfilled `active_state` from recent history.
3. Backfilled session summaries into `timeline_events`.
4. Backfilled directives from explicit language.
5. Backfilled commitments, corrections, and decision outcomes conservatively.
6. Backfilled first-pass patterns.

### Remaining

1. selective cleanup / pruning of bad derived rows
2. future reflection backfill only after the layer exists


## Old Table Strategy

Current uncertain tables:

- `active_facts`
- `recent_context`
- `fact_timeline`

Recommendation:

- do not depend on them for the first implementation
- replace them with explicit real tables
- only delete or retire them after the new system is live and validated

Status: `Still pending`

We are no longer relying on these tables/views for the redesigned live path, but we have not formally retired or removed them yet.


## Evaluation Plan

Build replay/eval coverage from the start.

### Core eval categories

1. Identity
- does Memory remember who Ishaan is?

2. Active continuity
- does Memory know what is going on recently?

3. Directives
- does Memory obey standing rules without restatement?

4. Timeline recall
- can Memory answer date-based questions accurately?

5. Correction fidelity
- do corrected bad memories stop resurfacing?

6. Low-signal greeting behavior
- does `hey!` avoid dragging in junk context?

7. Grounding
- do derived memories only appear when supported by actual episodes?


## Immediate Next Work

This is the actual highest-value remaining sequence now:

1. Atlas provider productization
- expose Atlas cleanly through upstream provider seam
- finish polished setup docs and setup flow under `hermes memory setup`

2. genericization and retrieval quality
- remove remaining style-specific extraction assumptions
- improve ranking so timeline-first and handoff-first routing is consistent

3. `facts` cleanup and grounding
- remove awkward or weakly supported fact rows
- tighten preference/goal/project extraction quality

4. `active_state` synthesis cleanup
- turn raw lines into sharper current-life summaries
- tune active-state freshness and phrasing using replay evals

5. long-horizon graph reasoning
- add temporal entity/relation graph tables and evidence links
- add graph-aware retrieval for multi-hop/cross-month questions

6. `reflections`
- implement cautiously with evidence and reversibility

7. user controls and trust UX
- implement forget / revoke / override flows on top of commitments/corrections layers

8. observability hardening and eval expansion
- add latency/cost/quality metrics and alert thresholds
- keep replay/CI regression gates and add LLM-in-the-loop temporal suites

9. proactive presence / heartbeat
- inactivity awareness
- open-loop follow-up
- check-in logic

### Additional eval categories

8. Advice
- does Memory use past outcomes and patterns helpfully?

9. Corrections
- does Memory stop repeating corrected mistakes?

10. Commitments
- does Memory remember promises it made?

11. Time awareness
- does Memory know whether it is morning, afternoon, or late night right now?

### Suggested eval prompts

- `how should I approach this?`
- `what happened last week?`
- `what was I doing 473 days ago?`
- `what do you think is my biggest fear?`
- `remember the rule I gave you about delegation`
- `do not use em dashes`
- `what did you promise me recently?`
- `what were you wrong about and I corrected?`
- `should I be sleeping right now?`


## Rollout Strategy

### Stage 1

Additive schema migrations only.

### Stage 2

Write new layers in parallel, but keep old retrieval behavior.

### Stage 3

Switch session bootstrap to the new layered context.

### Stage 4

Switch per-turn retrieval to use the new derived layers.

### Stage 5

Retire old unused tables/views only after stability and eval confidence.


## First Recommended Build Slice

If we want the highest payoff first, implement this slice:

1. profile-safe namespace fields
2. `active_state`
3. `directives`
4. stronger session-start bootstrap
5. live time context hardening

Why this slice first:

- biggest immediate improvement in daily conversations
- fixes the “Memory should already know what is going on” problem
- fixes the “Memory forgets hard rules” problem
- avoids jumping too early into speculative reflective memory


## Definition of Done for V1

V1 is successful when:

- Memory remembers key identity facts without restatement
- Memory knows active life/work context at session start
- Memory preserves and obeys hard directives reliably
- Memory keeps continuity across Telegram/WhatsApp/web as one mind
- Memory does not confuse stale time context with current time
- advice can begin using recent patterns and recent outcomes
- the system is profile-safe for future expansion


## Final Recommendation

Do not implement the full redesign in one shot.

Implement:

1. foundations
2. active state
3. directives
4. bootstrap
5. timeline
6. accountability memory
7. outcome memory
8. patterns
9. reflections last

This is the safest path to make Memory dramatically better without destabilizing the current system.
