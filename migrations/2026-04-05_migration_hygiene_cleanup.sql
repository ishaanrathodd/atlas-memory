-- Compatibility schema retirement + migration hygiene cleanup
-- Date: 2026-04-05
--
-- This migration reasserts canonical memory RPCs and removes residual compatibility
-- surfaces that were recreated during earlier transition/platform migrations.

-- Retire compatibility views (idempotent cleanup; safe if already dropped).
drop view if exists memory.active_facts;
drop view if exists memory.fact_timeline;
drop view if exists memory.recent_context;

-- Remove stale legacy RPC signatures/schemas that can linger in partially migrated environments.
drop function if exists atlas.touch_fact(uuid);
drop function if exists atlas.search_facts(memory.fact_category, text, integer);
drop function if exists atlas.search_episodes(vector, integer, text, integer, real);
drop function if exists atlas.search_episodes(vector, integer, memory.platform, integer, real);
drop function if exists memory.search_episodes(vector, integer, memory.platform, integer, real);

drop type if exists memory.platform;

-- Reassert canonical search_episodes RPC used by transport layer.
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

comment on function memory.search_episodes(vector, integer, text, integer, real)
    is 'Canonical memory search_episodes RPC (platform is text; compatibility variants retired).';

notify pgrst, 'reload schema';
