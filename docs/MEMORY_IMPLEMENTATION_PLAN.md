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

- Date: `2026-04-06`
- Repository status: `green` (`225 passed, 10 skipped`; hermes setup-targeted `8 passed`)


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

## Execution Mode: Solo But General

Atlas is developed for a single primary user, but quality targets remain general-purpose.

- avoid user-specific hacks or hardcoded personal assumptions
- prefer generic memory primitives and retrieval policies that transfer beyond one persona
- use diverse/adversarial/synthetic evaluation to prevent overfitting to local usage patterns
- keep operations lightweight: strong local CI/eval loops over enterprise rollout ceremony
- no dashboards by design: memory upgrades must come from conversation evidence + automated replay evaluation, not manual dashboard curation


## Current Runtime State (Solo Build)

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

Status: `Completed` (compatibility views retired; rollback path documented)

### Completion Notes

- compatibility views retired via `2026-04-05_compatibility_view_retirement.sql`
- canonical RPC/migration hygiene reasserted via `2026-04-05_migration_hygiene_cleanup.sql`
- runtime/source guard added to prevent reintroduction of retired view dependencies
- operational rollback SQL + apply-order verification documented in `docs/FINAL_PRODUCT_RUNBOOK.md`


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

1. Post-closeout production rollout execution
- apply migration sequence in target Supabase environment(s)
- run post-migration verification SQL checks from `docs/FINAL_PRODUCT_RUNBOOK.md`
- run one real-environment smoke cycle (`setup` + memory-enriched chat + trust-ops action + replay-eval)

2. Optional quality expansion (not blocker for final-product closeout)
- broader judge calibration tuning and sampled scenario mix
- additional adversarial temporal/proactive replay coverage beyond current strict gates

Status update (2026-04-05): trust-ops polish completed in retrieval output + eval scoring.
- enrichment now emits explicit `Trust operations` guidance derived from trust-ledger certainty/freshness and quote coverage posture
- replay eval now measures `trust_calibration_rate` in the universal outcome scorecard
- adversarial trust/calibration fixture added and included in synthetic months benchmark gate

### Deferred Workstreams

- full graph indexing complexity before retrieval core maturity
- broad schema expansion that does not move retrieval quality metrics


### 2026-04-05 Milestone Update

- finalized Atlas-owned integration assets under `atlas/integrations/hermes/plugins/memory/atlas`
- hardened Atlas provider runtime root discovery for integration/user-plugin locations (including `ATLAS_ROOT` override and `.venv` python fallback)
- upstream-protection decision: reverted direct `hermes-agent` branch edits to avoid conflicts with upstream-linked branch workflow
- schema decision: no DB schema migration required for this milestone; setup/integration improvements stay at UX/runtime layer to remain retrieval-first and avoid niche schema churn
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
- added identity continuity replay regression pack and CI gate:
  - new fixture: `tests/fixtures/replay_eval_identity_scenarios.json` with `10` continuity scenarios
  - strict threshold gate in CI (`min_pass_rate=1.0`) alongside existing replay eval gate
  - eval harness now surfaces `always_on_identity_lines` count for quantitative continuity checks
- expanded identity continuity hardening with edge-case replay and slot scoring:
  - new fixture: `tests/fixtures/replay_eval_identity_edge_scenarios.json` with `14` edge-case scenarios (slot conflicts, revocation/reaffirmation ordering, sparse identity fallback, long-horizon drift)
  - eval harness now supports seeded fact timestamps (`created_at`/`updated_at`) for deterministic long-horizon ordering tests
  - eval report now includes per-slot continuity quality via `identity_slot_scores`
  - CI now enforces slot-level pass thresholds (`1.0`) for both baseline identity fixture and edge-case identity fixture
  - communication-preference promotion in always-on identity was tightened to avoid leaking non-communication preferences (for example: beverage preferences)
- validation: replay harness tests `4 passed`; atlas full suite `196 passed, 10 skipped`
- added adversarial continuity replay hardening focused on contradiction chains and temporal inversion:
  - new fixture: `tests/fixtures/replay_eval_identity_adversarial_scenarios.json` with `12` adversarial scenarios (temporal inversion, cross-session contradiction chains, mixed lifecycle slots, sparse guardrails)
  - replay eval regression tests now include baseline + edge-case + adversarial identity packs
  - CI now runs a dedicated adversarial replay gate with slot-level pass threshold enforcement (`1.0`)
- validation: replay harness tests `5 passed`; atlas full suite `197 passed, 10 skipped`
- added long-horizon continuity replay suite and CI gate:
  - new fixture: `tests/fixtures/replay_eval_long_horizon_scenarios.json` with `6` long-horizon scenarios (old outcome grounding, exact recall across sessions, week-targeted timeline recall, proactive coaching from older failures, long-horizon profile persistence)
  - replay eval regression tests now include long-horizon suite alongside baseline + identity continuity packs
  - CI now runs a dedicated long-horizon replay eval gate with slot-level threshold enforcement when identity slots are exercised
  - schema decision: no schema changes required; this milestone is retrieval-eval and gating only
- validation: replay harness tests `6 passed`; atlas full suite `198 passed, 10 skipped`
- implemented universal cross-department replay scorecard in eval harness:
  - replay reports now include `universal_outcome_scorecard` with continuity carry-forward, restatement burden, outcome-grounded guidance, adaptation latency, and regression resilience metrics
  - each replay CI gate now fails on universal scorecard regressions (`overall_score.all_metrics_green=false`) in addition to existing pass-rate/slot gates
  - schema decision: no schema changes required; this is report-layer scoring and CI enforcement only
- validation: replay harness tests `6 passed`; atlas full suite `198 passed, 10 skipped`
- added fabricated-data synthetic long-horizon benchmark for real-world continuity confidence:
  - new test `tests/test_eval_harness_synthetic.py` fabricates 6 months of usage by cloning and time-shifting all replay suites (baseline + identity + edge + adversarial + long-horizon)
  - synthetic benchmark enforces strict thresholds over 200+ generated scenarios and requires universal scorecard health (`all_metrics_green=true`)
  - CI now runs this benchmark explicitly via `pytest tests/test_eval_harness_synthetic.py -q`
  - schema decision: no schema changes required; benchmark is test/runtime-eval only
- validation: replay harness tests `7 passed`; atlas full suite `199 passed, 10 skipped`
- implemented conversation-only adaptive directive upgrades (no dashboards):
  - refresh-directives now learns communication behavior from explicit instructions and implicit user feedback phrases (for example: "too verbose", "too robotic")
  - explicit revoke/forget phrases now retire matching standing directives directly from chat turns
  - anti-thrash safeguards prevent rapid oscillation on conflicting directives unless reinforced or explicitly overridden
  - enrichment now reranks standing directives per turn by relevance + confidence + recency to improve ambiguous retrieval behavior
  - schema decision: no schema changes required; this is conversation-driven curation and retrieval-layer ranking only
- validation: directive/enrichment targeted tests green; atlas full suite `204 passed, 10 skipped`
- implemented optional LLM-judge layer for replay evaluation (no dashboard dependency):
  - replay eval now supports an opt-in judge scorecard (`--enable-judge`) with configurable model and sample limit
  - deterministic gates remain the default source of truth; judge can be non-blocking or enforced (`--judge-enforce`) depending on run mode
  - runtime/CLI wiring added for conversation-driven operation only (no manual dashboard workflow)
  - schema decision: no schema changes required; this is eval/runtime orchestration only
- validation: eval/runtime targeted tests green; atlas full suite `207 passed, 10 skipped`
- setup reliability + retrieval/trust/eval hardening pass (this session):
  - added `setup-diagnostics` task in `memory.curator_runtime` with environment, import-path, and Supabase health checks
  - added installable CLI entrypoint `memory-curator` in `pyproject.toml`
  - implemented retrieval second-pass reranker (`rerank_items_second_pass`) and wired analogous case reranking to outcome/evidence/confidence signals
  - proactive coaching now emits trigger-confidence context and keeps explicit better-path guidance when prior risk evidence exists
  - trust ledger now surfaces evidence certainty labels (`high/medium/low`) in addition to freshness/currentness markers
  - universal replay scorecard now includes intervention precision/recall observability metrics
  - schema decision: no schema changes required; all updates are runtime/retrieval/eval-layer only
- validation: targeted changed-suite `118 passed`; atlas full suite `210 passed, 10 skipped`
- setup UX hardening pass (this session):
  - added one-command setup task (`python -m memory.curator_runtime setup`) that safely creates missing non-secret defaults (`~/.hermes/.env`, `~/.hermes/atlas.json`)
  - setup now immediately runs diagnostics and returns unresolved next steps for missing secrets/config
  - added parser support for `setup` and `--no-auto-fix`
  - updated runtime tests for setup workflow and parser behavior
- validation: targeted changed-suite `120 passed`; atlas full suite `212 passed, 10 skipped`
- setup simplification pass (this session):
  - setup now asks only for core values: Supabase URL, service-role key, embedding API key, and LLM model choice
  - LLM choice defaults to Hermes `~/.hermes/config.yaml` (`model.default`) when available
  - selected LLM is written to `MEMORY_LLM_MODEL` and used by summary generation when enabled
- validation: targeted changed-suite `122 passed`; atlas full suite `214 passed, 10 skipped`
- trust ops + migration hygiene completion pass (this session):
  - trust output now includes explicit `Trust operations` section for confidence/freshness/grounding posture
  - replay eval scorecard now includes `trust_calibration_rate` and tracks trust-op diagnostics per scenario
  - added `tests/fixtures/replay_eval_trust_adversarial_scenarios.json` and wired dedicated + synthetic benchmark coverage
  - added `2026-04-05_migration_hygiene_cleanup.sql` to reassert canonical `memory.search_episodes` and retire residual compatibility surfaces/signatures idempotently
- final-product closeout completion pass (this session):
  - added explicit user-facing trust operations runtime path in Atlas CLI:
    - `python -m memory.curator_runtime trust-ops --trust-op forget|revoke|override`
    - supports targeted directive correction via `--directive-key`/`--match-text` and manual overrides via `--directive-content`
  - enrichment trust output now surfaces operator guidance for explicit forget/revoke/override correction workflow
  - replay eval hardening extended:
    - new adversarial proactive-temporal trust scenario with trigger-confidence expectations
    - new judge-enforcement regression test for skipped judge state
    - new universal scorecard regression test asserting trust-calibration threshold accounting
    - added runtime assertion test to ensure no source-code dependence on retired compatibility views (`memory.active_facts`, `memory.fact_timeline`, `memory.recent_context`)
  - Hermes setup reliability/product UX hardening completed:
    - added fallback-path test proving Atlas setup runs with Hermes Python + `PYTHONPATH` injection when Atlas venv is absent
    - aligned Atlas Python runtime selection order across setup/runtime plugin paths to prefer `.venv` before `venv`
    - documented fail-fast Atlas setup (no legacy fallback) and runtime path behavior in plugin READMEs
  - added clean-machine + migration operations runbook:
    - `docs/FINAL_PRODUCT_RUNBOOK.md` includes migration apply order, rollback SQL, post-migration verification SQL, setup/diagnostics commands, replay gates, and smoke checks
  - validation:
    - Atlas targeted: `128 passed`
    - Atlas full: `225 passed, 10 skipped`
    - Hermes targeted setup suites: `8 passed`

### 2026-04-06 Revalidation Update

- re-ran Atlas full suite: `225 passed, 10 skipped`
- re-ran Hermes setup-targeted suites: `8 passed`
- verified `hermes memory setup` command exits successfully in local workspace flow
- schema decision: no additional schema changes since 2026-04-05 closeout; operationalization remains migration/runbook execution in target environments


## Evaluation and Quality Gates

### Existing Gates

- deterministic replay scenarios in CI
- regression pass/fail thresholds
- universal cross-department scorecard gate (`universal_outcome_scorecard.overall_score`)
- unit and integration test coverage

### Next Evaluation Layer

- expand LLM-judge calibration coverage (prompt tuning, sampled-scenario mix, cost/latency guardrails)
- adversarial temporal recall cases
- expand proactive-intervention precision/recall scenario coverage (metric is now emitted in universal scorecard)

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

### Universal Outcome Metrics (Cross-Department, V1)

These metrics intentionally apply across communication, planning, debugging, execution, and risk coaching.
No department-specific schema is required; all measurements should be derived from existing memory evidence and outcomes.

1. Continuity carry-forward rate
- definition: in turns where prior durable context should matter, how often did the response behavior reflect that context without user restatement?
- signal sources: replay scenarios, quote coverage lines, continuity handoff lines, identity/policy evidence surfaced in enrichment.

2. Restatement burden
- definition: average number of clarification/restatement turns required before alignment after context changes.
- target direction: lower is better.
- signal sources: user corrections, repeated preference restatements, directive churn windows.

3. Outcome-grounded guidance rate
- definition: percentage of recommendation/execution answers that cite relevant prior outcomes or patterns when available.
- target direction: higher is better, with evidence linkage.
- signal sources: decision outcome usage, pattern usage, proactive coach lines, evidence coverage.

4. Adaptation latency
- definition: number of turns between new user feedback and stable behavioral adoption in downstream responses.
- target direction: lower is better, but non-zero guardrails prevent thrash.
- signal sources: directive/preference updates, supersession events, replay temporal phase scenarios.

5. Regression resilience
- definition: pass rate of deterministic + adversarial + long-horizon replay suites under strict thresholds.
- target direction: remain at or above gate thresholds; no silent degradation across departments.
- signal sources: CI replay gates and per-slot/per-route replay scorecards.

### Universal Metrics Guardrails

- optimize globally, not per-feature: any change that improves one department while hurting others should fail evaluation.
- keep evidence auditable: every metric movement must map back to concrete replay cases or linked outcomes.
- avoid overfitting: add scenario diversity (context switches, temporal drift, contradictory feedback) before accepting improvements.


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
- local setup is reproducible on a clean machine without brittle steps


## Immediate Next Actions

1. [x] finalize personal setup reliability (`hermes memory setup` UX and docs) — atlas-side one-command setup and diagnostics are live; hermes-side setup wizard integration/docs and runtime path behavior completed
2. [x] implement retrieval planner skeleton and first reranker pass
3. [x] define and migrate case-memory tables
4. [x] make episode-first feature extraction the default memory processor path
5. [x] add explicit always-on identity layer in enrichment context
6. [x] add long-horizon eval suite baseline (deterministic replay + CI gate, LLM-in-loop ready)
7. [x] complete canonical identity conflict-resolution lifecycle (confirm/supersede/revoke semantics)
8. [x] add optional replay LLM-judge layer with CLI toggles (non-dashboard, enforce optional)
9. [x] ship retrieval/trust hardening pass (second-pass rerank + certainty labels + proactive trigger confidence)

Remaining blockers (2026-04-06):

- no code blockers in this workspace; test and closeout gates are green
- remaining operational work is environment rollout execution:
  - run migration apply order on target DB
  - run verification SQL checks
  - run one production-like smoke cycle using `docs/FINAL_PRODUCT_RUNBOOK.md`


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
- `atlas/tests/fixtures/replay_eval_long_horizon_scenarios.json`
- `atlas/tests/fixtures/replay_eval_identity_edge_scenarios.json`
- `atlas/tests/fixtures/replay_eval_identity_adversarial_scenarios.json`
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
- atlas/tests/fixtures/replay_eval_long_horizon_scenarios.json
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

