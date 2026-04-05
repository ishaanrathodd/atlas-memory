# Atlas Final Product Runbook

## Purpose

Single operational runbook for:

- migration rollout + compatibility retirement
- clean-machine Atlas + Hermes integration setup
- deterministic validation gates

## Operator vs End-User Responsibilities

This project uses BYO Supabase per user.

1. BYO Supabase project (common distribution mode)
- Each user connects Atlas to their own Supabase project.
- Important: credentials alone are not enough on a fresh project.
- That project needs Atlas schema initialized once (migration apply order in Section 1).
- After schema is initialized, setup is simple for that user:
  - Supabase project URL
  - Supabase service role key
  - embedding API key
  - optional LLM model choice

## 1) Migration Rollout (Operational)

Run this section when provisioning or upgrading a Supabase backend.

- In BYO mode: each user (or your onboarding automation for that user) runs this once for their own project.

Apply in this order (idempotent-safe for mixed environments):

1. `migrations/2026-04-03_memory_schema_transition.sql`
2. `migrations/2026-04-03_platform_text.sql`
3. `migrations/2026-04-05_compatibility_view_retirement.sql`
4. `migrations/2026-04-05_migration_hygiene_cleanup.sql`

Recommended execution:

- Run each SQL file in Supabase SQL editor or your migration runner in the exact order above.
- Do not skip `2026-04-05_migration_hygiene_cleanup.sql`; it reasserts canonical RPC signatures and removes stale compatibility surfaces.

### Post-Migration Verification SQL

Run and verify these checks:

```sql
-- Canonical search_episodes RPC should be text-based platform filter.
select
  n.nspname as schema_name,
  p.proname as function_name,
  pg_get_function_identity_arguments(p.oid) as args
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'memory'
  and p.proname = 'search_episodes';
```

Expected: includes one signature with `platform_filter text`.

```sql
-- Retired compatibility views should be absent.
select schemaname, viewname
from pg_views
where schemaname = 'memory'
  and viewname in ('active_facts', 'fact_timeline', 'recent_context');
```

Expected: `0 rows`.

```sql
-- Legacy enum should be absent after platform-text migration hygiene.
select n.nspname as schema_name, t.typname as type_name
from pg_type t
join pg_namespace n on n.oid = t.typnamespace
where n.nspname = 'memory'
  and t.typname = 'platform';
```

Expected: `0 rows`.

### Rollback Strategy

If external dependencies still require compatibility views temporarily, re-create them as a short-lived rollback patch:

```sql
create or replace view memory.active_facts as
select * from memory.facts where is_active = true;

create or replace view memory.fact_timeline as
select
  id,
  fact_id,
  operation,
  coalesce(old_content, new_content) as content,
  coalesce(old_category, new_category) as category,
  event_time,
  transaction_time,
  reason
from memory.fact_history
order by transaction_time desc;

create or replace view memory.recent_context as
select
  e.id,
  e.session_id,
  e.role,
  e.content,
  e.content_hash,
  e.embedding,
  e.message_metadata,
  e.emotions,
  e.dominant_emotion,
  e.emotional_intensity,
  e.message_timestamp,
  e.created_at,
  s.platform,
  s.topics
from memory.episodes e
join memory.sessions s on e.session_id = s.id
where e.message_timestamp > (now() - interval '7 days')
order by e.message_timestamp desc;

notify pgrst, 'reload schema';
```

After rollback, re-audit all runtime callers and remove dependencies before retiring again.

## 2) Clean-Machine Setup (Atlas + Hermes)

From workspace root (`~/.hermes`):

```bash
cd /Users/ishaanrathod/.hermes/atlas
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -e .
```

```bash
cd /Users/ishaanrathod/.hermes/hermes-agent
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -e .
```

Atlas runtime diagnostics and guided setup:

```bash
cd /Users/ishaanrathod/.hermes/atlas
./venv/bin/python -m memory.curator_runtime setup-diagnostics
./venv/bin/python -m memory.curator_runtime setup
```

Hermes provider activation:

```bash
cd /Users/ishaanrathod/.hermes/hermes-agent
./venv/bin/python -m hermes_cli.main memory setup
./venv/bin/python -m hermes_cli.main memory status
```

## 3) Trust Ops CLI (Forget/Revoke/Override)

Atlas user-facing trust operations:

```bash
cd /Users/ishaanrathod/.hermes/atlas
./venv/bin/python -m memory.curator_runtime trust-ops --trust-op forget --match-text "concise direct"
./venv/bin/python -m memory.curator_runtime trust-ops --trust-op revoke --directive-key "manual:test:key"
./venv/bin/python -m memory.curator_runtime trust-ops --trust-op override --match-text "very long" --directive-content "Use concise bullet responses for rollout guidance." --directive-kind communication --directive-scope global
```

## 4) Replay Eval + Smoke Gates

```bash
cd /Users/ishaanrathod/.hermes/atlas
./venv/bin/python -m memory.curator_runtime replay-eval --scenarios-file tests/fixtures/replay_eval_scenarios.json --min-pass-rate 1.0
./venv/bin/python -m memory.curator_runtime replay-eval --scenarios-file tests/fixtures/replay_eval_trust_adversarial_scenarios.json --min-pass-rate 1.0
./venv/bin/python -m memory.curator_runtime replay-eval --scenarios-file tests/fixtures/replay_eval_long_horizon_scenarios.json --min-pass-rate 1.0
```

Optional judge layer:

```bash
./venv/bin/python -m memory.curator_runtime replay-eval --scenarios-file tests/fixtures/replay_eval_scenarios.json --enable-judge --judge-model gpt-4o-mini --judge-sample-limit 12
```

## 5) Required Test Gates

Atlas targeted:

```bash
cd /Users/ishaanrathod/.hermes/atlas
./venv/bin/python -m pytest tests/test_curator_runtime.py tests/test_enrichment.py tests/test_eval_harness.py tests/test_eval_harness_synthetic.py -q
```

Atlas full:

```bash
cd /Users/ishaanrathod/.hermes/atlas
./venv/bin/python -m pytest -q
```

Hermes targeted:

```bash
cd /Users/ishaanrathod/.hermes/hermes-agent
./venv/bin/python -m pytest tests/hermes_cli/test_memory_setup.py tests/hermes_cli/test_setup_noninteractive.py -q
```
