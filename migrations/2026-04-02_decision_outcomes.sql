create schema if not exists memory;

create table if not exists memory.decision_outcomes (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    kind text not null default 'other',
    title text,
    decision text not null,
    outcome text not null,
    lesson text,
    outcome_key text not null,
    status text not null default 'open',
    confidence double precision not null default 0.75,
    importance_score double precision not null default 0.6,
    event_time timestamptz not null,
    session_id uuid,
    source_episode_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists decision_outcomes_namespace_key_idx
    on memory.decision_outcomes (coalesce(agent_namespace, 'main'), outcome_key);

create index if not exists decision_outcomes_namespace_status_time_idx
    on memory.decision_outcomes (agent_namespace, status, event_time desc);

create index if not exists decision_outcomes_importance_time_idx
    on memory.decision_outcomes (importance_score desc, event_time desc);
