create schema if not exists memory;

create table if not exists memory.temporal_graph_nodes (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text not null default 'default',
    node_key text not null,
    node_type text not null,
    title text not null,
    summary text,
    confidence double precision not null default 0.72,
    importance_score double precision not null default 0.6,
    first_observed_at timestamptz not null,
    last_observed_at timestamptz not null,
    source_episode_ids uuid[] not null default '{}',
    source_fact_ids uuid[] not null default '{}',
    source_outcome_ids uuid[] not null default '{}',
    source_pattern_ids uuid[] not null default '{}',
    source_case_ids uuid[] not null default '{}',
    source_reflection_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists temporal_graph_nodes_namespace_key_idx
    on memory.temporal_graph_nodes (agent_namespace, node_key);

create index if not exists temporal_graph_nodes_namespace_type_importance_idx
    on memory.temporal_graph_nodes (agent_namespace, node_type, importance_score desc, last_observed_at desc);

create index if not exists temporal_graph_nodes_namespace_last_observed_idx
    on memory.temporal_graph_nodes (agent_namespace, last_observed_at desc);

create table if not exists memory.temporal_graph_edges (
    id uuid primary key default gen_random_uuid(),
    agent_namespace text not null default 'default',
    edge_key text not null,
    from_node_id uuid not null references memory.temporal_graph_nodes(id) on delete cascade,
    to_node_id uuid not null references memory.temporal_graph_nodes(id) on delete cascade,
    relation text not null,
    confidence double precision not null default 0.7,
    weight double precision not null default 0.6,
    evidence_count integer not null default 1,
    first_observed_at timestamptz not null,
    last_observed_at timestamptz not null,
    source_case_ids uuid[] not null default '{}',
    source_outcome_ids uuid[] not null default '{}',
    source_pattern_ids uuid[] not null default '{}',
    source_reflection_ids uuid[] not null default '{}',
    tags text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists temporal_graph_edges_namespace_key_idx
    on memory.temporal_graph_edges (agent_namespace, edge_key);

create index if not exists temporal_graph_edges_namespace_triplet_idx
    on memory.temporal_graph_edges (agent_namespace, from_node_id, to_node_id, relation);

create index if not exists temporal_graph_edges_namespace_relation_weight_idx
    on memory.temporal_graph_edges (agent_namespace, relation, weight desc, last_observed_at desc);

create index if not exists temporal_graph_edges_namespace_last_observed_idx
    on memory.temporal_graph_edges (agent_namespace, last_observed_at desc);