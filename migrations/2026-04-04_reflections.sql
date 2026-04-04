create schema if not exists memory;

create table if not exists memory.reflections (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    kind text not null default 'workflow_hypothesis',
    statement text not null,
    evidence_summary text,
    reflection_key text not null,
    status text not null default 'tentative',
    confidence double precision not null default 0.62,
    first_observed_at timestamptz not null,
    last_observed_at timestamptz not null,
    supporting_episode_ids uuid[] not null default '{}',
    supporting_session_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists reflections_namespace_key_idx
    on memory.reflections (coalesce(agent_namespace, 'main'), reflection_key);

create index if not exists reflections_namespace_status_observed_idx
    on memory.reflections (agent_namespace, status, last_observed_at desc);

create index if not exists reflections_confidence_observed_idx
    on memory.reflections (confidence desc, last_observed_at desc);
