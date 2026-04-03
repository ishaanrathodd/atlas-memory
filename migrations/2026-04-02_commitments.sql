create schema if not exists memory;

create table if not exists memory.commitments (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    kind text not null default 'other',
    statement text not null,
    commitment_key text not null,
    status text not null default 'open',
    confidence double precision not null default 0.8,
    priority_score double precision not null default 0.7,
    first_committed_at timestamptz not null,
    last_observed_at timestamptz not null,
    source_episode_ids uuid[] not null default '{}',
    source_session_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists commitments_namespace_key_idx
    on memory.commitments (coalesce(agent_namespace, 'main'), commitment_key);

create index if not exists commitments_namespace_status_priority_idx
    on memory.commitments (agent_namespace, status, priority_score desc, last_observed_at desc);
