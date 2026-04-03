# Memory Redesign

## Goal

Build a memory system that makes Hermes feel like a continuously learning companion, not just a transcript retriever.

The target behavior is:

- Hermes should know who Ishaan is without being reminded every session.
- Hermes should know what is actively going on in Ishaan's life and work right now.
- Hermes should remember important recent events across session boundaries.
- Hermes should recall older events accurately by date, period, and theme.
- Hermes should learn patterns from repeated behavior and use them proactively in advice.
- Hermes should infer emotional continuity over days and weeks, not only per-message sentiment.

This redesign intentionally separates raw evidence from derived memory layers.

Near-term product target:

- one real active profile: `Memory`
- one human user
- one shared Supabase backend

The design should be optimized for Memory-first daily use today, while remaining compatible with future named profiles later.

## Diagnosis

The current Memory design is a good base, but it is still too flat.

Today, the live retrieval path mainly uses:

- `sessions`
- `episodes`
- `facts`
- `fact_history`

This gives us:

- durable transcript storage
- extracted durable facts
- session summaries
- semantic recall over past episodes

What it does not yet give us well enough:

- active life state
- open loops
- recurring behavioral patterns
- emotionally coherent recent continuity
- robust timeline summaries
- reflective self-modeling with evidence


## Design Principles

1. Raw evidence and derived memory must be different things.
Raw messages should not be injected directly unless they are actually needed.

2. Not all memories are equal.
Stable identity, active state, episodic events, and inferred patterns should live in different layers.

3. Session start and per-turn retrieval should be different.
Session start should load a compact "state of Ishaan" snapshot. Per-turn retrieval should add only what is relevant to the current message.

4. Memory should be evidence-backed.
Every durable or inferred memory should point back to episodes, sessions, or prior derived records.

5. The system should optimize for helpful judgment, not just recall.
The goal is not only "remember a thing" but "use accumulated history to give better advice."

6. Standing instructions must not depend on fuzzy retrieval.
If the user gives a hard operating rule, Hermes should obey it automatically until it is revoked, superseded, or expires.

7. This deployment is single-user.
The memory system is being designed for one human user only.

8. This redesign is single-profile optimized, multi-profile ready.
We should not build a full multi-agent orchestration system yet. We should, however, include the schema boundaries needed so future profiles can coexist safely in the same Supabase backend.

9. Live temporal context must never be inferred from memory when authoritative current time is available.
Memory is for continuity. Current time, date, and time-of-day must come from runtime context on every turn.


## Memory Layers

### 1. Evidence Layer

This is the ground truth.

- `sessions`
- `episodes`
- `fact_history`

Purpose:

- store every visible conversation turn
- preserve timestamps, platform, session grouping, and source metadata
- preserve auditability for later derived memory generation

This layer should remain append-heavy and minimally opinionated.


### 1B. Live Runtime Context

This is not durable memory. It is authoritative context for the current turn.

Purpose:

- current UTC time
- current local time
- current timezone
- current date
- time-of-day label

Examples:

- `2026-04-02T09:48:58+00:00`
- `Asia/Kolkata`
- `2026-04-02 15:18 IST`
- `afternoon`

This layer should override stale session assumptions or time-sensitive memory.

If the agent thinks it is 3 AM when it is actually 3 PM, this is a runtime-context bug, not a memory bug.


### 2. Identity Layer

This is the durable "who is Ishaan?" layer.

Table:

- `facts`

Purpose:

- stable facts
- preferences
- identity claims
- long-running goals
- recurring relationships
- durable project facts

Examples:

- preferred collaboration style
- important projects and what they are
- long-lived motivations
- stable dislikes and preferences

This layer should be cautious and relatively slow-changing.

For the current deployment, this is primarily memory about Ishaan as seen by Memory.


### 3. Active State Layer

This is the "what is going on right now?" layer.

New table:

- `active_state`

Purpose:

- current projects
- current blockers
- current emotional load
- unresolved threads
- near-term goals
- current priorities
- recently changed life state

Examples:

- currently focused on Memory memory redesign
- currently frustrated by persistence and retrieval bugs
- open loop: wants Hermes to know him deeply without explicit prompting
- elevated stress around shipping vs over-optimizing

Unlike `facts`, this layer is intentionally short-horizon and should expire, decay, or refresh often.

For the current deployment, this is Memory's live picture of what is going on in Ishaan's life and work right now.


### 4. Timeline Layer

This is the "what happened when?" layer.

New table:

- `timeline_events`

Purpose:

- session summaries
- day summaries
- week summaries
- major events
- transitions in projects or emotional state

Examples:

- "Spent most of the day debugging Telegram/Memory persistence and timestamp bugs."
- "Last week focused heavily on redesigning Hermes memory and prompt injection."
- "Around early April, shifted from local session files to Supabase-backed Memory storage."

This layer should make questions like these easy:

- what happened last week?
- what was I doing 473 days ago?
- what did that day feel like?


### 5. Pattern Layer

This is the "what keeps repeating?" layer.

New table:

- `patterns`

Purpose:

- repeated decision styles
- recurring strengths
- recurring traps
- emotional tendencies
- project behavior patterns
- response-to-stress patterns

Examples:

- tends to go deep on foundational architecture when trust in the system drops
- often over-invests in memory/infrastructure quality because long-term leverage matters deeply
- performs best when reducing scope to a testable minimal change
- gets irritated by continuity failures and then starts rebuilding the memory substrate itself

This is the layer that powers personalized advice:

- "Last time you approached this by widening the redesign too early, which cost time."
- "You usually do better when you isolate one failure mode and verify it end-to-end first."


### 6. Directive Layer

This is the "what ongoing rules must Hermes obey?" layer.

New table:

- `directives`

Purpose:

- standing behavioral rules
- communication rules
- tool-use rules
- delegation rules
- formatting constraints
- project- or user-specific operating instructions

Examples:

- always delegate implementation tasks to subagents unless explicitly told otherwise
- do not use em dashes in replies
- never surface tool payloads to the user
- always persist visible assistant voice replies as spoken text

This layer is different from facts.
Facts describe the user or world.
Directives constrain how Hermes should behave.

These rules should not rely on semantic recall alone.
If active, they should be injected automatically at session start and checked again before action planning.


### 7. Reflection Layer

This is the "what do I infer about Ishaan overall?" layer.

New table:

- `reflections`

Purpose:

- higher-order interpretations
- self-model hypotheses
- meaningful long-range inferences
- psychologically relevant patterns

Examples:

- likely biggest fear
- strongest motivation
- what he avoids under pressure
- what he values most across many sessions

This layer must be cautious.
It should store:

- confidence
- evidence references
- last reviewed time
- whether the reflection is tentative or well-supported

This layer should never be treated as hard fact by default.


### 8. Commitment Layer

This is the "what has Hermes promised, agreed to track, or committed to help with?" layer.

New table:

- `commitments`

Purpose:

- reminders the agent agreed to give
- obligations the agent accepted
- ongoing responsibilities the agent claimed it would handle
- decisions the user and agent explicitly agreed on

Examples:

- Memory promised to remind Ishaan about a follow-up
- Memory agreed to keep replies free of em dashes
- Memory agreed to track an open business thread

Without this layer, the agent can remember facts about the user while forgetting promises made to the user.


### 9. Correction Layer

This is the "what was remembered incorrectly and then corrected?" layer.

New table:

- `corrections`

Purpose:

- explicit user corrections
- memory disputes
- invalidated inferences
- instruction clarifications

Examples:

- "No, that is not what I meant"
- "That memory is wrong"
- "Do not infer that from what I said"
- "This is a hard rule, not just a preference"

This layer prevents the system from repeatedly resurfacing incorrect memories.


### 10. Decision/Outcome Layer

This is the "what was decided, and what happened after?" layer.

New table:

- `decision_outcomes`

Purpose:

- decisions taken
- alternatives considered
- rationale
- eventual results
- retrospective lessons

Examples:

- widened scope early and lost time
- isolated one failure mode first and made faster progress
- shipped a smaller version and learned sooner

This is the layer that makes advice genuinely outcome-aware.


## Recommended Schema Direction

### Keep

- `sessions`
- `episodes`
- `facts`
- `fact_history`

These are core and should remain.

### Repurpose or Replace

- `active_facts`
- `recent_context`
- `fact_timeline`

If these are only unused views or incomplete experiments, they should not remain ambiguous.

Recommended direction:

- replace `active_facts` with a real `active_state`
- replace `recent_context` with a real `session_bootstrap_context` view or generator
- replace `fact_timeline` with `timeline_events`

### Add

- `active_state`
- `timeline_events`
- `patterns`
- `directives`
- `reflections`
- `commitments`
- `corrections`
- `decision_outcomes`

### Future-proof now, but do not overbuild yet

We should include profile-aware identifiers in the schema now, but keep runtime behavior Memory-first.

That means:

- add `profile_id` or `agent_namespace` fields where appropriate
- do not build complex inter-profile communication yet
- do not require multi-profile reasoning in the prompt pipeline yet
- do ensure future profiles cannot silently collide in the same backend


## Proposed Table Responsibilities

### `active_state`

One row per active thread of life/work state.

Suggested fields:

- `id`
- `profile_id`
- `subject_id` or `person_id`
- `kind`
  - `project`
  - `blocker`
  - `priority`
  - `emotion_state`
  - `open_loop`
  - `relationship_state`
- `title`
- `content`
- `status`
  - `active`
  - `cooling`
  - `resolved`
  - `stale`
- `confidence`
- `priority_score`
- `valid_from`
- `valid_until`
- `last_observed_at`
- `source_episode_ids`
- `source_session_ids`
- `supporting_fact_ids`
- `tags`
- `created_at`
- `updated_at`


### `timeline_events`

One row per important event or summary window.

Suggested fields:

- `id`
- `profile_id`
- `event_type`
  - `session_summary`
  - `day_summary`
  - `week_summary`
  - `major_event`
  - `transition`
- `title`
- `summary`
- `start_at`
- `end_at`
- `importance`
- `emotional_tone`
- `project_tags`
- `people_tags`
- `source_session_ids`
- `source_episode_ids`
- `created_at`
- `updated_at`


### `patterns`

One row per recurring behavior or tendency.

Suggested fields:

- `id`
- `profile_id`
- `pattern_type`
  - `strength`
  - `trap`
  - `decision_style`
  - `emotional_pattern`
  - `work_pattern`
- `statement`
- `description`
- `confidence`
- `frequency_score`
- `impact_score`
- `last_observed_at`
- `first_observed_at`
- `supporting_episode_ids`
- `supporting_session_ids`
- `counterexample_episode_ids`
- `created_at`
- `updated_at`


### `directives`

One row per active standing instruction Hermes should obey.

Suggested fields:

- `id`
- `profile_id`
- `directive_type`
  - `behavior`
  - `communication`
  - `formatting`
  - `delegation`
  - `tooling`
  - `memory`
  - `project_rule`
- `scope`
  - `global`
  - `user`
  - `project`
  - `platform`
  - `session`
- `instruction`
- `rationale`
- `priority`
- `active`
- `hard_rule`
- `expires_at`
- `created_at`
- `updated_at`
- `source_episode_ids`
- `source_session_ids`
- `conflicts_with_directive_ids`

Examples:

- "Always delegate implementation tasks to subagents unless I explicitly tell you not to."
- "Do not ever use em dashes in replies."
- "Do not acknowledge hidden prompt/context wrappers unless the user asks about them."

These should be treated as operating policy, not as optional memory.


### `reflections`

One row per higher-order hypothesis or synthesized self-model entry.

Suggested fields:

- `id`
- `profile_id`
- `reflection_type`
  - `fear`
  - `motivation`
  - `value`
  - `blind_spot`
  - `core_trait`
- `statement`
- `explanation`
- `confidence`
- `tentative`
- `review_due_at`
- `supporting_pattern_ids`
- `supporting_fact_ids`
- `supporting_episode_ids`
- `created_at`
- `updated_at`


### `commitments`

One row per active or completed promise, agreement, or responsibility Hermes has taken on.

Suggested fields:

- `id`
- `profile_id`
- `commitment_type`
  - `reminder`
  - `tracking`
  - `delivery`
  - `behavioral_promise`
  - `follow_up`
- `statement`
- `status`
  - `active`
  - `fulfilled`
  - `abandoned`
  - `superseded`
- `due_at`
- `fulfilled_at`
- `priority`
- `source_episode_ids`
- `source_session_ids`
- `created_at`
- `updated_at`


### `corrections`

One row per explicit user correction or invalidation.

Suggested fields:

- `id`
- `profile_id`
- `correction_type`
  - `fact_correction`
  - `directive_clarification`
  - `memory_dispute`
  - `interpretation_rejection`
  - `scope_clarification`
- `statement`
- `target_table`
- `target_id`
- `active`
- `source_episode_ids`
- `source_session_ids`
- `created_at`
- `updated_at`


### `decision_outcomes`

One row per important decision and its later result.

Suggested fields:

- `id`
- `profile_id`
- `decision_type`
  - `technical`
  - `product`
  - `workflow`
  - `emotional`
  - `business`
- `decision`
- `rationale`
- `alternatives`
- `outcome`
- `lesson`
- `confidence`
- `source_episode_ids`
- `source_session_ids`
- `created_at`
- `updated_at`


## Retrieval Architecture

The current enrichment format is too generic. We should move to layered retrieval.

For now, assume the active profile is Memory.
All bootstrap and retrieval behavior should be optimized for one continuously used companion profile.
Profile-aware fields exist to prevent future collisions, not to force multi-profile complexity into today's runtime.

Live runtime context should always sit above memory in prompt priority.


## Memory Compiler Pipeline

Raw episodes should not become prompt context directly by default.

Every new episode should pass through a memory compiler pipeline that decides what kind of memory it contributes to.

Suggested stages:

1. Intake
- persist raw episode in `episodes`

2. Classification
- determine whether the episode contains:
  - fact evidence
  - directive evidence
  - active-state evidence
  - timeline-event evidence
  - pattern evidence
  - commitment
  - correction
  - decision/outcome evidence

3. Promotion
- write or update the corresponding derived memory tables

4. Consolidation
- merge duplicates
- reduce stale noise
- connect evidence references

5. Review
- decay, resolve, supersede, or reaffirm derived memory over time

This is how transcript storage becomes actual memory.

### A. Session Start Bootstrap

When a new session starts, inject a compact bootstrap context with:

1. Identity snapshot
- top durable facts about Ishaan

2. Active state snapshot
- active projects
- open loops
- current blockers
- current emotional state across the last several days

3. Standing directives
- active hard rules
- communication/style rules
- delegation/tooling rules

4. Very recent continuity
- last few important exchanges across sessions

5. Important recent events
- major day/week/session summaries

This should be compact and reliable.
It should not depend heavily on the exact user message.

For the current deployment, this bootstrap should answer:

- who is Ishaan?
- what is going on in his life right now?
- what happened recently?
- what standing rules has he given Memory?

This bootstrap should also include current authoritative time context, but that time context must be refreshed every turn, not frozen for the whole session if the session spans long enough to matter.


### B. Per-Turn Retrieval

After session start, each user message should choose retrieval tools by intent.

#### If the message is generic

Examples:

- hey
- yo
- hmm
- okay

Inject only:

- live runtime context
- identity snapshot
- active state snapshot
- standing directives
- maybe one or two recent continuity items

Do not inject broad semantic recall.


#### If the message asks for advice

Examples:

- how should I approach this?
- what do you think I should do?

Inject:

- live runtime context
- relevant past episodes
- relevant patterns
- recent active state
- standing directives
- possibly related timeline events


#### If the message asks for recollection

Examples:

- what happened last week?
- what was I doing 473 days ago?

Inject:

- live runtime context
- timeline events
- day/week/session summaries
- standing directives when relevant
- supporting episodes only if needed


#### If the message asks for interpretation

Examples:

- what do you think is my biggest fear?
- what do you think keeps repeating in my life?

Inject:

- live runtime context
- patterns
- reflections
- standing directives when relevant
- strong supporting facts
- supporting timeline or episode evidence


### Outcome-aware retrieval

For advice-oriented questions, retrieval should prefer:

- prior similar decisions
- the observed outcomes of those decisions
- recurring patterns connected to those outcomes

This is the mechanism that enables answers like:

- "The last time you approached this by widening the redesign too early, it cost us time."


## Emotional Continuity

Per-message emotion alone is not enough.

We should derive rolling emotional state over time windows.

Recommended approach:

- keep per-episode emotion signals in `episodes`
- compute rolling aggregates over:
  - last 24 hours
  - last 7 days
  - last 30 days
- convert meaningful repeated signals into:
  - `active_state` entries for current emotional state
  - `patterns` entries for recurring emotional tendencies
  - `timeline_events` summaries for periods of pressure or relief

Important rule:

Do not turn one-off emotional spikes into durable identity facts.


## Freshness and Decay

Memory needs different half-lives depending on what kind of thing it is.

Examples:

- identity facts decay very slowly
- active blockers decay quickly unless refreshed
- emotional state should cool unless sustained by repeated evidence
- directives remain active until revoked, superseded, or expired
- reflections should become stale if not supported by new evidence
- time-sensitive assumptions should decay immediately and never outrank live time context

This prevents old memory from overwhelming present reality.


## Open Loops

Hermes should know what is unresolved without being explicitly reminded.

Open loops should be first-class.

Examples:

- pending product decision
- unresolved emotional worry
- unfinished bug investigation
- unfinished redesign

These belong in `active_state`, not only inside raw transcript.


## Salience and Importance

Not all memories should compete equally.

We should explicitly model salience so that the system keeps the most important parts of life and personality near the top of context.

Examples of high-salience memory:

- core values
- major fears
- defining projects
- recurring destructive traps
- unresolved high-priority open loops

This matters for both retrieval and session-start bootstrap quality.


## Advice Memory

To support proactive, personalized advice, we should explicitly preserve outcome-aware memory.

Examples:

- when Ishaan approached similar problems by widening the redesign too early, it cost time
- when he isolated one failure mode and verified it end-to-end, it worked better

This means retrieval should consider:

- past similar situations
- how he responded
- what happened afterward

This can be built from:

- `episodes`
- `sessions.summary`
- `timeline_events`
- `patterns`
- `decision_outcomes`


## What the Agent Should Automatically Know

At session start, Hermes should know:

- who Ishaan is
- what he is actively working on
- what is unresolved right now
- what emotional tone has been present recently
- what major recent events happened

During a turn, Hermes should be able to answer naturally with:

- relevant past decisions
- relevant patterns
- recent continuity
- long-range memory when needed
- important corrections
- active commitments


## Migration Recommendation

Do this in phases.

### Phase 1

Stabilize the current core:

- keep `sessions`
- keep `episodes`
- keep `facts`
- keep `fact_history`
- keep retrieval reliable and clean
- add profile-aware identifiers now, even if Memory is the only active profile

### Phase 2

Add active-state memory:

- create `active_state`
- generate it from recent sessions, recent facts, and recent episodes
- inject it at session start

### Phase 3

Add timeline summaries:

- create `timeline_events`
- generate session/day/week summaries
- use them for date-based and period-based recall

### Phase 4

Add behavioral patterns:

- create `patterns`
- infer repeating strengths, traps, and styles
- use them in advice-oriented retrieval

### Phase 5

Add reflective memory:

- create `reflections`
- store tentative high-level inferences with evidence and confidence
- use them only for interpretation-style questions

### Phase 6

Add accountability memory:

- create `commitments`
- create `corrections`
- create `decision_outcomes`
- make advice retrieval outcome-aware
- let the agent remember obligations and fixes, not just facts

### Phase 7

Build evaluation and review:

- add replay/eval harness
- measure whether bootstrap and retrieval select the right memory
- periodically review stale, contradicted, or low-value memories

### Not in scope yet

Do not build these as part of the first redesign pass:

- inter-profile messaging between Memory, Dhruv, Aryan, etc.
- profile-to-profile delegation or work routing
- company/org-chart simulation
- profile collaboration workflows

Those can be added later once Memory-first memory is solid.


## Edge Cases That Must Be Designed Explicitly

### Directive precedence

If two directives conflict, use:

1. explicit current-turn instruction
2. active session-scoped directive
3. active project-scoped directive
4. active global directive

More recent directives of equal scope should supersede older ones.


### Directive revocation

The user must be able to say things like:

- stop doing that
- ignore the old rule
- from now on, do X instead

This should deactivate or supersede previous directives instead of leaving contradictory rules active.


### Directive scope

Not every rule is global.

Examples:

- "Do not use em dashes" is global.
- "Always delegate Memory implementation work" may be project-scoped.
- "During this session, keep replies extremely short" is session-scoped.


### Temporal validity

Some memories are durable, some are temporary.

Examples:

- identity facts may remain for years
- active blockers may expire in days
- emotional state should decay unless reinforced
- directives may expire or remain permanent depending on the rule


### Open-loop resolution

If a blocker, project, or worry is resolved, the system must stop surfacing it as active.

Open loops should be explicitly marked:

- active
- cooling
- resolved
- stale


### Outcome memory

The system should remember not just what happened, but how prior approaches turned out.

Examples:

- widening scope too early caused delay
- isolating one failure mode first worked better
- asking for a quick patch before understanding the system caused rework

This is critical for personalized advice.


### Correction memory

If the user says a memory, inference, or behavior is wrong, the system must preserve that correction explicitly.

Otherwise the agent can keep repeating the same wrong recollection.


### Evidence traceability

Hermes should be able to answer:

- why do you think that?
- what memory made you say this?

Patterns, reflections, active state, and directives should all preserve evidence references.


### Observed vs inferred vs instructed

Every derived memory should preserve its epistemic source:

- observed from explicit conversation
- inferred from repeated evidence
- instructed directly by the user

These should not be mixed together.


### Quoted text vs adopted instruction

If the user quotes or pastes prior instructions, docs, or prompts, the system must not automatically adopt them as active directives.

It should only activate a directive when the user is clearly stating or reaffirming it.


### Reflection caution

Reflections should never be treated as guaranteed truth.

They must carry:

- confidence
- evidence
- reviewability
- reversibility


### Commitment tracking

If Hermes promises to do something, that promise should become durable memory with a status lifecycle.

Otherwise the agent can appear caring in the moment but unreliable over time.


### Profile isolation

Even though this deployment is single-user, Hermes profiles are conceptually separate agents.

If multiple Hermes profiles share the same Supabase backend, their memory must still be isolated by profile identity.

This means routing/session identity should include a profile or agent namespace, not just `agent:main`.


### Platform is not a memory boundary

Platform must remain transport-only metadata.

Examples:

- Memory on Telegram and Memory on WhatsApp should share one memory
- platform should affect delivery and modality, not memory ownership
- retrieval should be profile-centered, not platform-centered


### Time awareness

The agent should never rely on stale memory to determine:

- what time it is now
- whether it is morning, afternoon, or night
- whether the user should be sleeping right now

This must come from live runtime context each turn.

Memory may inform habits like:

- user tends to sleep late
- user had a rough night yesterday
- mornings are often productive

But these should never override the actual current local time.


## Evaluation Harness

This redesign needs a permanent replay suite.

We should test prompts like:

- "how should I approach this?"
- "what happened last week?"
- "what was I doing 473 days ago?"
- "what do you think is my biggest fear?"
- "follow the directive I gave you 50 days ago"
- "do not use em dashes"
- "what did you promise me recently?"
- "what were you wrong about and I corrected?"

Success should be measured on:

- memory selection quality
- correctness
- grounding
- obedience to directives
- avoidance of stale or irrelevant recall
- compactness of injected context


## Prompt Injection Redesign

Replace the current single generic `<memory>` block with a layered structure:

- `Identity snapshot`
- `Active state`
- `Recent continuity`
- `Relevant recall`
- `Interpretive patterns` when appropriate

At session start, inject:

- identity
- active state
- recent continuity
- recent important events

Per-turn, inject only the extra relevant layers.

For now, this prompt assembly should be designed around one active profile, Memory.
The system should be able to add profile-aware filtering later without changing the fundamental memory layers.


## Success Criteria

The redesign is successful when Hermes can do all of the following without explicit prompting:

- remember who Ishaan is and what matters to him
- stay current on what he is actively dealing with
- reference the last few important interactions across sessions
- answer date-based historical questions accurately
- give advice based on repeated past outcomes
- infer recurring emotional and behavioral patterns
- make helpful observations that feel grounded rather than random


## Summary

The future Memory stack should be:

- `sessions` for session envelopes
- `episodes` for raw conversational evidence
- `facts` for durable truths
- `fact_history` for auditability
- `active_state` for what is live now
- `timeline_events` for what happened when
- `patterns` for recurring behavior
- `directives` for standing instructions Hermes must obey
- `reflections` for high-level interpretation

This should be built as:

- Memory-first
- single-profile optimized
- multi-profile ready
- platform-independent for memory continuity

This is the memory architecture that can make Hermes feel like it truly knows the user over time.
