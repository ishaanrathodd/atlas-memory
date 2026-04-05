-- Compatibility view retirement
-- Date: 2026-04-05
--
-- Retires transitional read-only views that were carried during atlas->memory
-- namespace migration. Runtime reads now use canonical tables/RPCs directly.

-- Drop compatibility views that are no longer used by Atlas runtime paths.
drop view if exists memory.active_facts;
drop view if exists memory.fact_timeline;
drop view if exists memory.recent_context;

notify pgrst, 'reload schema';
