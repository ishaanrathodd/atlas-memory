create schema if not exists memory;

create table if not exists memory.directives (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    kind text not null,
    scope text not null default 'global',
    title text,
    content text not null,
    content_hash text,
    directive_key text not null,
    status text not null default 'active',
    confidence double precision not null default 0.85,
    priority_score double precision not null default 1.0,
    source_episode_ids uuid[] not null default '{}',
    source_session_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_observed_at timestamptz not null default now()
);

create unique index if not exists directives_namespace_key_idx
    on memory.directives (coalesce(agent_namespace, 'main'), directive_key);

create index if not exists directives_namespace_status_priority_idx
    on memory.directives (agent_namespace, status, priority_score desc, last_observed_at desc);
