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
- Repository status: `green` (`194 passed, 10 skipped`)


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
- explicit `always-on identity layer` in enrichment context (identity + communication preference lines are always injected)
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
- process-memory now defaults to episode-level feature extraction (summaries are optional)
- active-state focus/priority and timeline session/day/week rollups now derive from episode evidence first, with session-summary fallback only

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
- retrieval planner skeleton + first reranker pass (route-aware episode reranking)
- case-memory schema + first compile/read path (tables, curation write path, retrieval integration)

### Active Workstreams

1. Provider productization + seam hardening
- upstream-safe Atlas provider productization path (avoid direct edits on linked `hermes-agent` upstream branch)
- cross-environment Atlas bridge path hardening and packaging checks
- setup diagnostics for missing Atlas prerequisites

2. Retrieval intelligence
- reranker improvements (second pass + evaluation tuning)
- case memory quality tuning (ranking quality + pruning heuristics)

3. Trust operations
- forget / revoke / override UX
- stronger uncertainty surfacing

### Deferred Workstreams

- full graph indexing complexity before retrieval core maturity
- broad schema expansion that does not move retrieval quality metrics


### 2026-04-05 Milestone Update

- finalized Atlas-owned integration assets under `atlas/integrations/hermes/plugins/memory/atlas`
- hardened Atlas provider runtime root discovery for integration/user-plugin locations (including `ATLAS_ROOT` override and `.venv` python fallback)
- upstream-protection decision: reverted direct `hermes-agent` branch edits to avoid conflicts with upstream-linked branch workflow
- schema decision: no DB schema migration required for this milestone; provider productization stays at integration/UX layer to remain retrieval-first and avoid niche schema churn
- implemented retrieval planner skeleton in `memory.retrieval_planner` with semantic/lexical/temporal/analogous-case routing signals and per-route weights
- integrated first reranker pass into enrichment episode selection using planner route weights (semantic + lexical overlap + temporal freshness blend)
- added planner regression tests and enrichment retrieval-limit assertions (`tests/test_retrieval_planner.py`, `tests/test_enrichment.py`)
- schema decision: retrieval planner/reranker milestone introduced no schema or migration changes (retrieval-layer only)
- validation: targeted planner+enrichment suite `39 passed`; atlas full suite `182 passed, 10 skipped`
- added `2026-04-05_case_memory.sql` with `memory_cases` and `case_evidence_links` tables + indexes (agent-namespace safe, additive migration)
- implemented `refresh_memory_cases` curation pass to compile durable cases from decision outcomes and attach evidence links to outcomes/patterns
- integrated case-memory retrieval read path in enrichment (analogous-case route) and surfaced matched cases in context + ranking signals
- schema decision: case-memory introduced additive tables only; no destructive schema or compatibility-view changes in this milestone
- validation: targeted case-memory/enrichment/runtime suite `94 passed`; atlas full suite `184 passed, 10 skipped`
- added compatibility cleanup migration `2026-04-05_compatibility_view_retirement.sql` to retire legacy views (`memory.active_facts`, `memory.fact_timeline`, `memory.recent_context`)
- retired session-topic metadata compatibility fallback path; session payload now reads canonical session columns only
- added cleanup telemetry logs for legacy session reference resolution and legacy session-id lookup path usage
- implemented explicit always-on identity assembly in enrichment:
  - deterministic identity slot handling (name/religion/origin/location/work)
  - always-on preference/directive injection for communication style continuity
- switched `process-memory` default behavior to episode-first feature extraction (`extract-facts`) and made session summarization opt-in via `MEMORY_ENABLE_SESSION_SUMMARIES`
- aligned warm live-curator session/backlog consolidation with the same summary gate (`MEMORY_ENABLE_SESSION_SUMMARIES`) so LLM summaries are optional in continuity refresh loops
- updated active-state curation to prefer episode-derived focus/priority signals and include unsummarized sessions by default
- updated timeline event generation to build session/day/week summaries from episode content first (summary column as fallback)
- implemented canonical identity lifecycle resolution inside always-on identity injection:
  - deterministic slot model (`name`, `religion`, `origin`, `location`, `role`, `employer`, `identity`)
  - explicit lifecycle states (`active/confirmed`, `superseded`, `revoked`, `uncertain`)
  - contradiction-safe storage behavior (identity facts with conflicting lifecycle states are preserved as separate evidence)
- validation: targeted lifecycle/enrichment suite `52 passed`; atlas full suite `194 passed, 10 skipped`


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

1. [ ] finalize provider productization (`hermes memory setup` UX and docs) — pending upstream-safe landing path
2. [x] implement retrieval planner skeleton and first reranker pass
3. [x] define and migrate case-memory tables
4. [x] make episode-first feature extraction the default memory processor path
5. [x] add explicit always-on identity layer in enrichment context
6. [ ] add long-horizon LLM eval suite (alongside deterministic replay)
7. [x] complete canonical identity conflict-resolution lifecycle (confirm/supersede/revoke semantics)


## Next Chat Continuation Protocol

Use this section whenever work continues in a new chat.

### Continuation Workflow

1. Re-open this file first and treat it as the source of truth.
2. Re-validate repository status before edits:
- run full tests
- confirm current migration state
- confirm no drift in key memory pipelines
3. Continue from `Immediate Next Actions` in order unless a blocker is discovered.
4. Keep changes retrieval-first:
- avoid niche one-off schema hacks
- prefer improvements that move long-horizon retrieval metrics
5. After each milestone:
- update this document (status + what changed)
- run targeted tests + full suite
- record any schema/migration decisions here

### Context Files For Next Chat

Minimum files to load before implementation:

- `atlas/MEMORY_IMPLEMENTATION_PLAN.md`
- `atlas/MEMORY_REDESIGN.md`
- `atlas/src/memory/client.py`
- `atlas/src/memory/transport.py`
- `atlas/src/memory/consolidation.py`
- `atlas/src/memory/enrichment.py`
- `atlas/src/memory/eval_harness.py`
- `atlas/tests/test_curator_runtime.py`
- `atlas/tests/test_enrichment.py`
- `atlas/tests/test_eval_harness.py`
- `atlas/tests/fixtures/replay_eval_scenarios.json`
- `atlas/migrations/2026-04-03_memory_schema_transition.sql`
- `atlas/migrations/2026-04-03_platform_text.sql`
- `atlas/migrations/2026-04-03_agent_namespace_hardening.sql`

### Copy-Paste Prompt For Next Chat

```text
Continue Atlas memory implementation from source-of-truth plan.

Read these files first:
- atlas/MEMORY_IMPLEMENTATION_PLAN.md
- atlas/MEMORY_REDESIGN.md
- atlas/src/memory/client.py
- atlas/src/memory/transport.py
- atlas/src/memory/consolidation.py
- atlas/src/memory/enrichment.py
- atlas/src/memory/eval_harness.py
- atlas/tests/test_curator_runtime.py
- atlas/tests/test_enrichment.py
- atlas/tests/test_eval_harness.py
- atlas/tests/fixtures/replay_eval_scenarios.json
- atlas/migrations/2026-04-03_memory_schema_transition.sql
- atlas/migrations/2026-04-03_platform_text.sql
- atlas/migrations/2026-04-03_agent_namespace_hardening.sql

Then:
1) Validate current state (run tests, summarize pass/fail, note drift).
2) Execute the next unchecked item from “Immediate Next Actions” in MEMORY_IMPLEMENTATION_PLAN.md.
3) Keep implementation retrieval-first; avoid niche schema hacks.
4) Update MEMORY_IMPLEMENTATION_PLAN.md with progress and any schema decisions.
5) Run targeted + full tests and report results.
```

