create schema if not exists memory;

create table if not exists memory.patterns (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    pattern_type text not null,
    statement text not null,
    description text,
    pattern_key text not null,
    confidence double precision not null default 0.75,
    frequency_score double precision not null default 0.5,
    impact_score double precision not null default 0.5,
    first_observed_at timestamptz not null,
    last_observed_at timestamptz not null,
    supporting_episode_ids uuid[] not null default '{}',
    supporting_session_ids uuid[] not null default '{}',
    counterexample_episode_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists patterns_namespace_key_idx
    on memory.patterns (coalesce(agent_namespace, 'main'), pattern_key);

create index if not exists patterns_namespace_impact_last_observed_idx
    on memory.patterns (agent_namespace, impact_score desc, last_observed_at desc);

create index if not exists patterns_type_last_observed_idx
    on memory.patterns (pattern_type, last_observed_at desc);
