create schema if not exists memory;

create table if not exists memory.background_jobs (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text,
    job_key text not null,
    kind text not null,
    status text not null default 'queued',
    session_id uuid references memory.sessions(id) on delete set null,
    title text not null,
    description text,
    priority_score double precision not null default 0.5,
    progress_note text,
    completion_summary text,
    source_refs text[] not null default '{}',
    result_refs text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    last_progress_at timestamptz
);

create unique index if not exists background_jobs_namespace_key_idx
    on memory.background_jobs (coalesce(agent_namespace, 'main'), job_key);

create index if not exists background_jobs_namespace_status_idx
    on memory.background_jobs (coalesce(agent_namespace, 'main'), status, updated_at desc);

create index if not exists background_jobs_session_updated_idx
    on memory.background_jobs (session_id, updated_at desc);
