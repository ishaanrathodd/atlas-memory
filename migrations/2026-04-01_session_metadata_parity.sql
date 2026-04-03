-- Memory session metadata parity migration
-- Date: 2026-04-01
--
-- Adds Hermes-oriented session metadata so memory can eventually replace
-- state.db for durable session/resume/title flows.

create schema if not exists memory;

alter table if exists memory.sessions
    add column if not exists legacy_session_id text,
    add column if not exists title text,
    add column if not exists parent_session_id uuid references memory.sessions(id),
    add column if not exists end_reason text,
    add column if not exists model text,
    add column if not exists model_config jsonb not null default '{}'::jsonb,
    add column if not exists system_prompt_snapshot text,
    add column if not exists tool_call_count integer not null default 0,
    add column if not exists prompt_tokens integer not null default 0,
    add column if not exists completion_tokens integer not null default 0,
    add column if not exists total_tokens integer not null default 0,
    add column if not exists input_tokens integer not null default 0,
    add column if not exists output_tokens integer not null default 0,
    add column if not exists cache_read_tokens integer not null default 0,
    add column if not exists cache_write_tokens integer not null default 0,
    add column if not exists reasoning_tokens integer not null default 0,
    add column if not exists estimated_cost_usd double precision,
    add column if not exists actual_cost_usd double precision,
    add column if not exists cost_status text,
    add column if not exists cost_source text,
    add column if not exists billing_provider text,
    add column if not exists billing_base_url text,
    add column if not exists billing_mode text;

create index if not exists sessions_legacy_session_id_idx
    on memory.sessions (legacy_session_id);

create index if not exists sessions_title_started_at_idx
    on memory.sessions (title, started_at desc);

create index if not exists sessions_parent_session_id_idx
    on memory.sessions (parent_session_id);
