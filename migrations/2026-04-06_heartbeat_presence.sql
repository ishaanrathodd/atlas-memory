create schema if not exists memory;

create table if not exists memory.presence_state (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    active_session_id uuid references memory.sessions(id) on delete set null,
    active_platform text,
    last_user_message_at timestamptz,
    last_agent_message_at timestamptz,
    last_user_presence_at timestamptz,
    current_thread_summary text,
    conversation_energy double precision not null default 0.45,
    tension_score double precision not null default 0.0,
    warmth_score double precision not null default 0.6,
    user_disappeared_mid_thread boolean not null default false,
    last_proactive_message_at timestamptz,
    recent_proactive_count_24h integer not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists presence_state_namespace_idx
    on memory.presence_state (coalesce(agent_namespace, 'main'));

create index if not exists presence_state_session_idx
    on memory.presence_state (active_session_id);

create table if not exists memory.heartbeat_opportunities (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    opportunity_key text not null,
    kind text not null,
    status text not null default 'pending',
    session_id uuid references memory.sessions(id) on delete cascade,
    reason_summary text not null,
    earliest_send_at timestamptz not null default now(),
    latest_useful_at timestamptz,
    priority_score double precision not null default 0.5,
    annoyance_risk double precision not null default 0.2,
    desired_pressure double precision not null default 0.35,
    warmth_target double precision not null default 0.7,
    requires_authored_llm_message boolean not null default true,
    requires_main_agent_reasoning boolean not null default false,
    source_refs text[] not null default '{}',
    cancel_conditions text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_scored_at timestamptz
);

create unique index if not exists heartbeat_opportunities_namespace_key_idx
    on memory.heartbeat_opportunities (coalesce(agent_namespace, 'main'), opportunity_key);

create index if not exists heartbeat_opportunities_status_send_idx
    on memory.heartbeat_opportunities (agent_namespace, status, earliest_send_at asc, priority_score desc);

create index if not exists heartbeat_opportunities_session_idx
    on memory.heartbeat_opportunities (session_id, created_at desc);

create table if not exists memory.heartbeat_dispatches (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    opportunity_key text not null,
    opportunity_kind text,
    session_id uuid references memory.sessions(id) on delete cascade,
    dispatch_status text not null,
    target text,
    send_score double precision,
    response_preview text,
    failure_reason text,
    attempted_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists heartbeat_dispatches_namespace_attempted_idx
    on memory.heartbeat_dispatches (coalesce(agent_namespace, 'main'), attempted_at desc);

create index if not exists heartbeat_dispatches_namespace_opportunity_idx
    on memory.heartbeat_dispatches (coalesce(agent_namespace, 'main'), opportunity_key, attempted_at desc);

create index if not exists heartbeat_dispatches_session_attempted_idx
    on memory.heartbeat_dispatches (session_id, attempted_at desc);
