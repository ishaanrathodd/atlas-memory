-- Memory schema transition migration
-- Date: 2026-04-03
--
-- Moves the durable memory schema from "atlas" to the canonical "memory"
-- namespace, including tables, views, enum types, RPC functions, grants, and
-- PostgREST schema exposure.

create schema if not exists memory;

alter type if exists atlas.fact_category set schema memory;
alter type if exists atlas.fact_operation set schema memory;
alter type if exists atlas.message_role set schema memory;
alter table if exists atlas.sessions set schema memory;
alter table if exists atlas.episodes set schema memory;
alter table if exists atlas.facts set schema memory;
alter table if exists atlas.fact_history set schema memory;
alter table if exists atlas.active_state set schema memory;
alter table if exists atlas.directives set schema memory;
alter table if exists atlas.timeline_events set schema memory;
alter table if exists atlas.decision_outcomes set schema memory;
alter table if exists atlas.patterns set schema memory;
alter table if exists atlas.commitments set schema memory;
alter table if exists atlas.corrections set schema memory;

drop view if exists atlas.active_facts;
drop view if exists atlas.fact_timeline;
drop view if exists atlas.recent_context;

create or replace view memory.active_facts as
select
    id,
    content,
    category,
    confidence,
    event_time,
    transaction_time,
    is_active,
    replaced_by,
    source_episode_ids,
    access_count,
    last_accessed_at,
    tags,
    created_at,
    updated_at
from memory.facts
where is_active = true;

create or replace view memory.fact_timeline as
select
    id,
    fact_id,
    operation,
    coalesce(old_content, new_content) as content,
    coalesce(old_category, new_category) as category,
    event_time,
    transaction_time,
    reason
from memory.fact_history fh
order by transaction_time desc;

create or replace view memory.recent_context as
select
    e.id,
    e.session_id,
    e.role,
    e.content,
    e.content_hash,
    e.embedding,
    e.message_metadata,
    e.emotions,
    e.dominant_emotion,
    e.emotional_intensity,
    e.message_timestamp,
    e.created_at,
    s.platform,
    s.topics
from memory.episodes e
join memory.sessions s on e.session_id = s.id
where e.message_timestamp > (now() - interval '7 days')
order by e.message_timestamp desc;

drop function if exists atlas.touch_fact(uuid);
drop function if exists atlas.search_facts(memory.fact_category, text, integer);
drop function if exists atlas.search_episodes(vector, integer, text, integer, real);
drop function if exists atlas.search_episodes(vector, integer, memory.platform, integer, real);

alter table if exists memory.sessions
    alter column platform type text using platform::text,
    alter column platform set default 'local';
alter table if exists memory.episodes
    alter column platform type text using platform::text,
    alter column platform set default 'local';

drop type if exists memory.platform;

create or replace function memory.touch_fact(fact_id uuid)
returns void
language plpgsql
as $function$
begin
  update memory.facts
  set
    access_count = access_count + 1,
    last_accessed_at = now()
  where id = fact_id;
end;
$function$;

create or replace function memory.search_facts(
    category_filter memory.fact_category default null::memory.fact_category,
    tag_filter text default null::text,
    limit_count integer default 50
)
returns setof jsonb
language sql
stable
as $function$
    select row_to_json(f.*)::jsonb
    from memory.facts f
    where f.is_active = true
      and (category_filter is null or f.category = category_filter)
      and (tag_filter is null or tag_filter = any(f.tags))
    order by f.access_count desc, f.event_time desc
    limit limit_count;
$function$;

create or replace function memory.search_episodes(
    query_embedding vector,
    match_count integer default 20,
    platform_filter text default null,
    days_back integer default 30,
    min_emotional_intensity real default 0.0
)
returns table(
    id uuid,
    content text,
    role memory.message_role,
    platform text,
    similarity real,
    emotions jsonb,
    dominant_emotion text,
    message_timestamp timestamptz
)
language plpgsql
stable
as $function$
begin
  return query
  select
    e.id,
    e.content,
    e.role,
    e.platform,
    1 - (e.embedding <=> query_embedding) as similarity,
    e.emotions,
    e.dominant_emotion,
    e.message_timestamp
  from memory.episodes e
  where
    (platform_filter is null or e.platform = platform_filter)
    and e.message_timestamp > now() - (days_back || ' days')::interval
    and e.emotional_intensity >= min_emotional_intensity
  order by e.embedding <=> query_embedding
  limit match_count;
end;
$function$;

grant usage on schema memory to anon, authenticated, service_role;
grant select on all tables in schema memory to anon, authenticated;
grant all privileges on all tables in schema memory to service_role;
grant execute on all functions in schema memory to anon, authenticated, service_role;

alter default privileges in schema memory
    grant select on tables to anon, authenticated;
alter default privileges in schema memory
    grant all privileges on tables to service_role;
alter default privileges in schema memory
    grant execute on functions to anon, authenticated, service_role;

alter role authenticator set pgrst.db_schemas = 'public,graphql_public,memory';
notify pgrst, 'reload config';
notify pgrst, 'reload schema';

drop schema if exists atlas;
