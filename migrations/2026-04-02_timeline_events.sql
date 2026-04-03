create schema if not exists memory;

create table if not exists memory.timeline_events (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    kind text not null default 'session_summary',
    title text,
    summary text not null,
    event_key text not null,
    event_time timestamptz not null,
    session_id uuid,
    source_episode_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    importance_score double precision not null default 0.6,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists timeline_events_namespace_key_idx
    on memory.timeline_events (coalesce(agent_namespace, 'main'), event_key);

create index if not exists timeline_events_namespace_time_idx
    on memory.timeline_events (agent_namespace, event_time desc);
