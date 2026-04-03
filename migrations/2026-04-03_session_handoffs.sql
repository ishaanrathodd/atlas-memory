create schema if not exists memory;

create table if not exists memory.session_handoffs (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    session_id uuid not null references memory.sessions(id) on delete cascade,
    handoff_key text not null,
    last_thread text not null,
    carry_forward text,
    assistant_context text,
    emotional_tone text,
    confidence double precision not null default 0.8,
    source_episode_ids uuid[] not null default '{}',
    source_session_ids uuid[] not null default '{}',
    last_observed_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists session_handoffs_namespace_key_idx
    on memory.session_handoffs (coalesce(agent_namespace, 'main'), handoff_key);

create index if not exists session_handoffs_namespace_observed_idx
    on memory.session_handoffs (agent_namespace, last_observed_at desc);

create index if not exists session_handoffs_session_idx
    on memory.session_handoffs (session_id, last_observed_at desc);
