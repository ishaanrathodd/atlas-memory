create schema if not exists memory;

create table if not exists memory.corrections (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    kind text not null default 'memory_dispute',
    statement text not null,
    target_text text,
    correction_key text not null,
    active boolean not null default true,
    confidence double precision not null default 0.9,
    first_observed_at timestamptz not null,
    last_observed_at timestamptz not null,
    source_episode_ids uuid[] not null default '{}',
    source_session_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists corrections_namespace_key_idx
    on memory.corrections (coalesce(agent_namespace, 'main'), correction_key);

create index if not exists corrections_namespace_active_last_observed_idx
    on memory.corrections (agent_namespace, active, last_observed_at desc);
