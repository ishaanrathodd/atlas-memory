-- Backfill heartbeat namespace defaults for legacy/null rows and align index defaults.

begin;

update memory.presence_state
set agent_namespace = 'default'
where agent_namespace is null;

update memory.heartbeat_opportunities
set agent_namespace = 'default'
where agent_namespace is null;

update memory.heartbeat_dispatches
set agent_namespace = 'default'
where agent_namespace is null;

drop index if exists memory.presence_state_namespace_idx;
create unique index if not exists presence_state_namespace_idx
    on memory.presence_state (coalesce(agent_namespace, 'default'));

drop index if exists memory.heartbeat_opportunities_namespace_key_idx;
create unique index if not exists heartbeat_opportunities_namespace_key_idx
    on memory.heartbeat_opportunities (coalesce(agent_namespace, 'default'), opportunity_key);

drop index if exists memory.heartbeat_dispatches_namespace_attempted_idx;
create index if not exists heartbeat_dispatches_namespace_attempted_idx
    on memory.heartbeat_dispatches (coalesce(agent_namespace, 'default'), attempted_at desc);

commit;
