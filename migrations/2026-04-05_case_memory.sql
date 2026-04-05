create schema if not exists memory;

create table if not exists memory.memory_cases (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text not null default 'default',
    case_key text not null,
    title text,
    problem_statement text not null,
    resolution_summary text,
    outcome_status text not null default 'open',
    confidence double precision not null default 0.72,
    impact_score double precision not null default 0.6,
    first_observed_at timestamptz not null,
    last_observed_at timestamptz not null,
    source_outcome_ids uuid[] not null default '{}',
    source_pattern_ids uuid[] not null default '{}',
    source_episode_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists memory_cases_namespace_key_idx
    on memory.memory_cases (agent_namespace, case_key);

create index if not exists memory_cases_namespace_impact_last_observed_idx
    on memory.memory_cases (agent_namespace, impact_score desc, last_observed_at desc);

create index if not exists memory_cases_status_last_observed_idx
    on memory.memory_cases (outcome_status, last_observed_at desc);

create table if not exists memory.case_evidence_links (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text not null default 'default',
    case_id uuid not null references memory.memory_cases(id) on delete cascade,
    evidence_type text not null,
    evidence_id uuid not null,
    relevance_score double precision not null default 0.5,
    note text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists case_evidence_links_case_evidence_idx
    on memory.case_evidence_links (case_id, evidence_type, evidence_id);

create index if not exists case_evidence_links_namespace_case_relevance_idx
    on memory.case_evidence_links (agent_namespace, case_id, relevance_score desc);
