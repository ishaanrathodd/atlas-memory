-- Atlas platform storage should be freeform source metadata, not a memory boundary.
-- Convert platform columns from enum to text so new transports work without schema changes.

drop view if exists memory.recent_context;
drop function if exists memory.search_episodes(vector, integer, memory.platform, integer, real);

alter table if exists memory.sessions
    alter column platform type text using platform::text,
    alter column platform set default 'local';

alter table if exists memory.episodes
    alter column platform type text using platform::text,
    alter column platform set default 'local';

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

drop type if exists memory.platform;

notify pgrst, 'reload schema';
