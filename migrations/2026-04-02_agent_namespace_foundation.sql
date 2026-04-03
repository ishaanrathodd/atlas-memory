-- Memory agent namespace foundation migration
-- Date: 2026-04-02
--
-- Adds profile/instance-safe namespace columns so multiple Hermes profiles can
-- share one Supabase backend without cross-contaminating memory. Legacy rows
-- remain NULL and are treated as the historical default namespace ("main") by
-- the application layer for backward compatibility.

create schema if not exists memory;

alter table if exists memory.sessions
    add column if not exists agent_namespace text;

alter table if exists memory.episodes
    add column if not exists agent_namespace text;

alter table if exists memory.facts
    add column if not exists agent_namespace text;

alter table if exists memory.fact_history
    add column if not exists agent_namespace text;

create index if not exists sessions_agent_namespace_started_at_idx
    on memory.sessions (agent_namespace, started_at desc);

create index if not exists episodes_agent_namespace_timestamp_idx
    on memory.episodes (agent_namespace, message_timestamp desc);

create index if not exists facts_agent_namespace_updated_at_idx
    on memory.facts (agent_namespace, updated_at desc);

create index if not exists fact_history_agent_namespace_transaction_time_idx
    on memory.fact_history (agent_namespace, transaction_time desc);
