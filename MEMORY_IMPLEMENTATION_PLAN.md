# Memory Implementation Plan

## Purpose

This document is the source of truth for how Atlas memory is built, what is already shipped, and what the final architecture should become.

It answers:

- what is done right now
- what remains and why
- which schema objects are core vs compatibility baggage
- how we evolve toward retrieval intelligence and long-horizon memory
- how we validate that memory quality is truly improving


## Last Updated

- Date: `2026-04-05`
- Repository status: `green` (`178 passed, 10 skipped`)


## Executive Summary

Atlas has completed the first major memory substrate:

- namespace-safe durable memory
- active state, directives, timeline events, commitments/corrections, outcomes, patterns
- session handoff continuity
- event-driven hot/warm curator runtime
- observability + replay eval harness + CI regression gates
- retrieval inspectability/trust signals (evidence/quote coverage paths)

The next moat is not adding niche fields. The moat is retrieval intelligence:

- case-based long-horizon recall
- multi-route retrieval planning
- outcome-aware reranking
- proactive intervention from historical analogues
- optional graph layer for multi-hop reasoning after core retrieval quality is strong


## Current Production State

### Implemented and Live

- `agent_namespace` isolation across memory records
- `active_state` compilation and bootstrap injection
- `directives` extraction + standing-rule injection
- `timeline_events` with session/day/week summaries
- `commitments` and `corrections` with suppression behavior
- `decision_outcomes` with conservative grounding
- `patterns` with evidence gating
- `session_handoffs` for rollover continuity
- always-on curation model (`hot`, `warm`; cron as backstop)
- replay eval harness and CI regression gating
- observability instrumentation for memory pipeline paths

### Hardening Completed Recently

- exact/inspectable recall behavior improved via evidence-oriented enrichment paths
- directive extraction genericized to avoid user-style overfitting
- preference-style directive wording support improved
- one-off temporary instructions filtered out of persistent directives
- false directive superseding reduced (scan-window guard)
- single-message persistence now enforces `user`/`assistant` roles only
- batch persistence already enforces `user`/`assistant` roles only

### Important Behavior Guarantees

- tool/system payload blobs are not persisted as durable episodes through normal batch ingestion
- single-message API now also rejects tool/system roles to prevent accidental DB bloat
- directives persist as standing memory, not just chat-context leftovers


## Target End-State (Final Atlas)

Atlas should behave like a companion memory engine with practical "perpetual memory" feel.

### Core Principles

- immutable evidence first
- derived memory second
- retrieval planning over single-vector lookup
- explainable evidence surfaced with memory responses
- corrections and supersession are first-class
- proactive guidance from prior outcomes, not only reactive QA
- avoid niche one-off schema features when retrieval intelligence can solve the problem generally

### Context Separation Strategy

- separate work/personal context through retrieval-time evidence selection, case similarity, and intent-aware ranking
- avoid hardcoding user-specific or temporary style assumptions into schema logic
- only introduce new schema objects when they improve broad retrieval quality metrics across users

### Final Memory Stack

1. Evidence layer
- sessions
- episodes
- facts
- fact_history

2. Derived memory layer
- active_state
- directives
- commitments
- corrections
- timeline_events
- decision_outcomes
- patterns
- reflections
- session_handoffs

3. Retrieval intelligence layer
- intent router
- multi-route retrieval planner (semantic, lexical, temporal, analogous-case)
- learned/heuristic reranking with outcome impact weighting
- trust/evidence rendering and quote coverage

4. Strategic memory layer
- case memory (attempts, outcomes, lessons)
- optional temporal graph reasoning for multi-hop queries


## Schema Source of Truth

### Core Schema Objects (Keep)

- `memory.sessions`
- `memory.episodes`
- `memory.facts`
- `memory.fact_history`
- `memory.active_state`
- `memory.directives`
- `memory.timeline_events`
- `memory.commitments`
- `memory.corrections`
- `memory.decision_outcomes`
- `memory.patterns`
- `memory.reflections`
- `memory.session_handoffs`
- RPCs: `memory.search_episodes`, `memory.search_facts`, `memory.touch_fact`

### Compatibility Objects (Candidates for Retirement)

- `memory.active_facts` (view)
- `memory.fact_timeline` (view)
- `memory.recent_context` (view)

Rationale:

- current Atlas runtime paths do not rely on these view names as primary query surfaces
- they are compatibility surfaces from schema transition and can be retired after a safety window

### Schema Risks To Resolve

- migration history duplicates definitions for `memory.search_episodes` and `memory.recent_context` across transition/platform migrations
- foundational DDL for `sessions/episodes/facts/fact_history` is not represented in this migration set and must be treated as external prerequisite
- `directives.scope` exists but is not a major retrieval discriminator yet


## Redundant Schema Retirement Plan

Status: `Planned`

### Step 1: Usage Audit Window

- add temporary telemetry around SQL access for:
  - `memory.active_facts`
  - `memory.fact_timeline`
  - `memory.recent_context`
- run for at least `14` days in staging and `14` days in production

### Step 2: Soft Deprecation

- keep objects but mark as deprecated in migration comments/docs
- route all internal code paths to base tables/RPCs only

### Step 3: Remove Compatibility Views

- drop deprecated views in a dedicated migration once external usage is confirmed zero
- keep rollback migration ready to recreate views quickly if needed

### Step 4: Migration Hygiene

- consolidate duplicate function/view definitions into one canonical migration path for new installs
- document base schema prerequisites explicitly for fresh environments


## Retrieval-First Roadmap (Moat Work)

This is the roadmap for "best personal assistant" quality memory.

### Phase A: Retrieval Core Quality (Next)

- build retrieval planner that executes multiple retrieval routes per turn
- add intent-aware weighting by task type
- add outcome-aware reranking
- add contradiction/supersession checks in final selection

Deliverables:

- retrieval planner module
- ranking policy module
- evidence bundle output for downstream prompting

### Phase B: Case Memory (High Impact)

- compile repeated real-world attempts into case objects:
  - intent
  - constraints
  - approach
  - outcome
  - failure cause
  - better alternative

Why:

- this is the strongest path to: "you tried this years ago and it failed; here is a better path"

Deliverables:

- `memory_cases` table
- `case_evidence_links` table
- case retrieval route

### Phase C: Proactive Guidance

- add trigger layer for analogous-failure warnings
- trigger only when confidence and similarity exceed threshold
- require concise causal explanation when intervening

Deliverables:

- proactive trigger scorer
- intervention safety thresholds
- intervention replay evals

### Phase D: Temporal Graph Layer (Staged)

- add graph memory only after phases A-C are strong
- support multi-hop relation traversal over long timescales
- use graph for hard connect-the-dots tasks

Graph can be implemented with:

- native temporal graph tables in Postgres
- optional orchestration via graph tooling (including GraphRAG-style pipelines) when scale justifies it

Important:

- Graph/GraphRAG is an accelerator, not phase zero
- first win is retrieval planner + case memory quality


## Workstream Plan and Status

### Completed Workstreams

- namespace-safe memory substrate
- derived memory tables and curation pipelines (except reflections runtime usage maturity)
- event-driven continuity curation
- replay harness + CI gates
- directive persistence hardening and de-overfitting
- ingestion bloat guardrails (`user`/`assistant` only for durable episodes)

### Active Workstreams

1. Provider productization
- polished Atlas provider setup flow under `hermes memory setup`
- external-provider seam hardening

2. Retrieval intelligence
- retrieval planner
- reranker improvements
- case memory design

3. Trust operations
- forget / revoke / override UX
- stronger uncertainty surfacing

### Deferred Workstreams

- full graph indexing complexity before retrieval core maturity
- broad schema expansion that does not move retrieval quality metrics


## Evaluation and Quality Gates

### Existing Gates

- deterministic replay scenarios in CI
- regression pass/fail thresholds
- unit and integration test coverage

### Next Evaluation Layer

- LLM-in-the-loop long-horizon eval suites
- adversarial temporal recall cases
- proactive-intervention precision/recall tests

### Primary Metrics (Source of Truth)

1. Long-horizon retrieval hit rate
- did top evidence include the best historical analogues?

2. Advice outcome grounding rate
- did advice reference real prior outcomes with traceable evidence?

3. Memory contradiction rate
- how often outdated/superseded memory was surfaced as active truth

4. Restatement burden
- how often user had to repeat known preferences/context

5. Proactive intervention precision
- how often proactive warnings were judged useful vs noisy


## Data Retention and Cost Controls

- persist only user/assistant readable turns as durable episodes
- avoid storing raw tool/system blobs in durable memory
- keep heavy raw artifacts outside primary memory retrieval tables
- prune stale derived rows by confidence/recency policy, never by arbitrary bulk deletion


## Operational Guardrails

- additive migrations first, destructive changes only after usage proof
- explicit rollback path for every schema retirement migration
- no retrieval behavior change without replay/eval deltas recorded
- no new derived memory class without evidence linkage and confidence semantics


## Definition of Done for Final Atlas

Final Atlas is done when all are true:

- user can interact for months/years without manual memory restating
- directives/commitments/corrections remain stable and auditable
- retrieval consistently surfaces the right historical evidence at the right time
- assistant proactively warns on repeated failure patterns with evidence
- long-horizon recall is measurable and regresses rarely due to CI gates
- compatibility schema baggage is retired safely
- provider setup is clean enough for non-authors to adopt


## Immediate Next Actions

1. finalize provider productization (`hermes memory setup` UX and docs)
2. implement retrieval planner skeleton and first reranker pass
3. define and migrate case-memory tables
4. add long-horizon LLM eval suite (alongside deterministic replay)
5. begin compatibility-view deprecation telemetry

