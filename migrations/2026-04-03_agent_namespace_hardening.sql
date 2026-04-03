begin;

do $$
declare
    v_table text;
    tables text[] := array[
        'sessions',
        'episodes',
        'facts',
        'fact_history',
        'active_state',
        'directives',
        'timeline_events',
        'decision_outcomes',
        'patterns',
        'commitments',
        'corrections',
        'session_handoffs'
    ];
begin
    foreach v_table in array tables loop
        if exists (
            select 1
            from information_schema.columns
            where table_schema = 'memory'
              and table_name = v_table
              and column_name = 'agent_namespace'
        ) then
            execute format(
                'update memory.%I set agent_namespace = %L where agent_namespace is null',
                v_table,
                'default'
            );
            execute format(
                'alter table memory.%I alter column agent_namespace set default %L',
                v_table,
                'default'
            );
            execute format(
                'alter table memory.%I alter column agent_namespace set not null',
                v_table
            );
        end if;
    end loop;
end $$;

notify pgrst, 'reload schema';

commit;
