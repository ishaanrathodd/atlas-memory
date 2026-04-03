create schema if not exists memory;

create table if not exists memory.active_state (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    kind text not null,
    title text,
    content text not null,
    content_hash text,
    state_key text not null,
    status text not null default 'active',
    confidence double precision not null default 0.7,
    priority_score double precision not null default 0.5,
    valid_from timestamptz not null default now(),
    valid_until timestamptz,
    last_observed_at timestamptz not null default now(),
    source_episode_ids uuid[] not null default '{}',
    source_session_ids uuid[] not null default '{}',
    supporting_fact_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists active_state_namespace_state_key_idx
    on memory.active_state (coalesce(agent_namespace, 'main'), state_key);

create index if not exists active_state_namespace_status_priority_idx
    on memory.active_state (agent_namespace, status, priority_score desc, last_observed_at desc);

create index if not exists active_state_last_observed_idx
    on memory.active_state (last_observed_at desc);
