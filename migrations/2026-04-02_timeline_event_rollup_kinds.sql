create schema if not exists memory;

alter table memory.timeline_events
    drop constraint if exists timeline_events_kind_check;

alter table memory.timeline_events
    add constraint timeline_events_kind_check
    check (kind = any (array[
        'session_summary'::text,
        'day_summary'::text,
        'week_summary'::text,
        'milestone'::text,
        'decision'::text
    ]));
