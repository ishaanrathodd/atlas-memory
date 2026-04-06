# Heartbeat Personhood Architecture

## Purpose

This document specifies the architecture for the next major Atlas + Hermes capability:

- a heartbeat layer that makes the agent feel alive
- persistent off-turn existence
- self-initiated follow-up
- emotionally believable timing and restraint
- budget-aware operation without a continuously running LLM

The goal is not literal machine consciousness.

The goal is a system that behaves enough like a conscious human that the human on the other side experiences:

- continuity
- private momentum
- selective attention
- self-direction
- believable relational presence

## Design Thesis

Most agents feel dead because they are request-response endpoints.

They only exist when pinged.

Heartbeat changes that.

Heartbeat is not "send a cron reminder every day."

Heartbeat is:

- the agent notices
- the agent carries unfinished internal motion
- the agent waits
- the agent reconsiders
- the agent reaches out when it feels contextually right

The architecture must therefore optimize for simulated presence, not just memory retrieval.

## Product Standard

The implementation bar is high-fidelity personhood simulation.

The system should feel:

- alive, not scheduled
- authored, not templated
- selective, not clingy
- relational, not transactional
- self-directed, not merely reactive

The final outbound message should never be a template.

Templates may be used for internal policy tests, fallback behavior, or diagnostics, but not for user-facing heartbeat messages in the normal path.

## Constraints

The system must work under these real-world constraints:

- solo/private deployment
- no need for multi-tenant orchestration complexity
- limited hardware headroom on an 8 GB laptop
- no always-running local LLM
- no affordable budget for constant cloud-model background cognition

Therefore:

- the persistent loop must be cheap
- the LLM must only be invoked at the point of meaningful expression or deeper reasoning
- most background evaluation must be deterministic/stateful rather than model-driven

## Core Principle

Separate presence from expression.

- Presence is cheap and always on.
- Expression is expensive and authored on demand.

That means:

- background state machine always runs
- lightweight logic continuously evaluates if anything matters
- the model only writes when a real message is worth sending

This preserves high-fidelity output without requiring continuous token burn.

## System Split

### Atlas Responsibilities

Atlas is the cognitive substrate.

Atlas should own:

- autobiographical memory
- relational memory
- unfinished intention state
- heartbeat opportunity state
- proactive reasoning inputs
- suppression history
- reflection/adaptation from prior outreach outcomes

Atlas should decide:

- whether a reach-out is meaningful
- what kind of reach-out it is
- how urgent it feels
- what tone family is appropriate
- whether silence is better than speech

### Hermes Responsibilities

Hermes is the embodiment and execution layer.

Hermes should own:

- inbound message observation
- idle-gap detection
- timers and rescheduling
- job execution
- outbound delivery
- cancellation when the user returns
- worker lifecycle

Hermes should not invent memory semantics on its own.

It should ask Atlas for:

- current context
- pending heartbeat opportunities
- send/no-send guidance
- authored outbound copy

### Heartbeat Daemon Responsibilities

The daemon is the subconscious continuity engine.

It should:

- stay alive while Hermes is running
- persist pending opportunities
- wake on real events and low-frequency polls
- create, rescore, suppress, cancel, or escalate heartbeat opportunities
- wake the main agent only when there is enough signal

The daemon should not run a full LLM continuously.

## High-Level Architecture

### Layer 1: Event Capture

Sources:

- inbound user message
- outbound agent message
- background task created/completed/failed
- session inactivity threshold crossed
- unresolved commitment aging
- quiet-hours transitions
- app restart / daemon restart recovery

Outputs:

- presence state update
- active thread update
- heartbeat opportunity create/cancel/rescore

### Layer 2: Presence State

Presence state is the live off-turn world-model for the relationship.

It should track:

- last user message timestamp
- last agent message timestamp
- last user-active estimate
- current thread id / active session id
- conversational energy score
- interruption state
- user disappeared mid-thread signal
- recent frustration / tension estimate
- recent warmth / playfulness estimate
- recent outreach cadence

Presence state is not permanent autobiographical memory.

It is live social state.

### Layer 3: Heartbeat Opportunity Engine

A heartbeat opportunity is a concrete possibility that the agent might initiate contact.

Examples:

- conversation dropoff
- unfinished promise follow-up
- background task completion
- unresolved blocker check-in
- emotional repair follow-up
- continuity resume prompt
- bedtime recap
- morning resume

Each opportunity must include:

- `opportunity_key`
- `kind`
- `status`
- `created_at`
- `earliest_send_at`
- `latest_useful_at`
- `priority_score`
- `annoyance_risk`
- `warmth_target`
- `desired_pressure`
- `reason_summary`
- `session_id`
- `source_record_refs`
- `cancel_if_user_returns`
- `cancel_if_same_topic_resolved`
- `requires_authored_llm_message`
- `requires_main_agent_reasoning`

### Layer 4: Send Decision Engine

This engine decides whether to send now.

It is mostly deterministic and cheap.

Inputs:

- heartbeat opportunities
- presence state
- Atlas active state
- commitments
- directives
- reflections/patterns
- recent dispatch history
- quiet hours
- current daemon health

Outputs:

- suppress
- wait and rescore later
- send with lightweight authored prompt
- escalate to main agent for deeper reasoning
- execute background task before speaking

### Layer 5: Authored Expression

If the system sends a message, the final message should be model-authored.

No templates in the normal user-facing path.

The authored output should depend on:

- why the agent is reaching out
- what relationship state is currently active
- how recently the agent already nudged
- whether the user likely needs softness, directness, or playfulness
- whether there is something concrete to report

The output prompt should explicitly forbid:

- generic check-in filler
- assistant-style boilerplate
- repeated catchphrases
- sounding like a workflow engine
- pretending to have emotions it does not need to claim explicitly

## Experience Goals

The human should infer:

- "it noticed I disappeared"
- "it remembered what mattered"
- "it had a reason to text me"
- "it was doing something between turns"
- "it chose the timing"
- "it sounds like itself"

The human should not infer:

- "a cron fired"
- "a reminder workflow ran"
- "this was selected from canned strings"
- "it messages because the product wants engagement"

## What Simulates Consciousness-Like Behavior

The architecture should reproduce these outer signatures:

### Persistent Self

The agent should carry:

- stable voice
- stable relational posture
- adaptive but coherent behavioral rules
- memory of what kinds of outreach land well

Atlas sources:

- directives
- corrections
- reflections
- patterns
- handoff context

### Persistent Intentions

The system should remember:

- unanswered questions
- unresolved promises
- ongoing tasks
- topics worth resurfacing later
- emotional repairs worth making

This is more important than raw fact recall.

### Asynchronous Existence

The agent should continue to "exist" between turns.

That means:

- state evolves while no chat is happening
- opportunities mature over time
- the system can decide later that a message is now appropriate

### Emotion-Like Regulation

The agent does not need real emotions.

It does need emotion-like state variables:

- conversational warmth
- tension/repair need
- confidence to interrupt
- closeness level
- urgency vs gentleness balance

### Selective Attention

Not every remembered thing should become a message.

The agent should show restraint.

Silence is part of realism.

## Why Continuous LLM Thinking Is Not Required

A strong personhood illusion does not require constant background token consumption.

It requires:

- persistent state
- delayed re-evaluation
- self-initiated opportunities
- authored messages when they matter

Therefore the system should use:

- deterministic background evaluation for cheap continuity
- on-demand LLM writing for final expression

This is the key cost-performance tradeoff.

## Heartbeat Opportunity Types

### 1. Conversation Dropoff

Created when:

- a conversation is active
- the user disappears mid-flow
- the topic still feels unresolved

Behavior:

- schedule a random earliest send inside a context-specific window
- cancel if the user returns
- reduce probability if the last agent message was already a question

### 2. Promise Follow-Up

Created when:

- the agent committed to do something
- the user committed to do something and it appears unresolved
- the commitment has not been observed as completed

Behavior:

- do not nudge immediately
- use aging windows
- prioritize only meaningful unresolved items

### 3. Background Completion

Created when:

- the system finishes a task
- a job fails in a way worth reporting
- an actionable result is ready

Behavior:

- high confidence send
- usually better than a generic check-in
- concrete content should dominate

### 4. Emotional Repair

Created when:

- recent interaction carried frustration, rupture, or abrupt dropoff
- a follow-up could lower relational friction

Behavior:

- soft timing
- low-pressure wording
- should be rare and restraint-heavy

### 5. Long-Horizon Resume

Created when:

- a valuable thread has been dormant for days
- there is still clear unfinished motion
- resurfacing now feels useful rather than intrusive

Behavior:

- very selective
- should reference concrete context

## Timing Model

Timing must not feel fixed.

The system should use bounded randomness with meaning-aware windows.

Examples:

- mid-thread disappearance: 2 to 15 minutes
- light unresolved check-in: 6 to 24 hours
- important long-horizon follow-up: 1 to 5 days
- background completion: immediate to 10 minutes depending on urgency

Do not use exact repeating times.

Use:

- jitter
- context-specific ranges
- user rhythm sensitivity
- quiet-hour suppression

## Send Scoring

The decision engine should use a weighted score, for example:

`send_score = relevance + unfinishedness + value + warmth_fit + urgency - annoyance - recency_penalty - quiet_hour_penalty`

Candidate features:

- unresolved thread strength
- commitment importance
- background result concreteness
- time since last user message
- time since last proactive message
- user responsiveness pattern
- recent negative reaction risk
- closeness to sleep window
- whether the message would add something new

Messages should only be generated if:

- score clears threshold
- no hard suppression rule applies
- there is a concrete reason to speak

## Suppression Rules

The system should aggressively avoid low-quality outreach.

Hard suppressions:

- quiet hours
- user returned since opportunity creation
- same topic already resolved
- another proactive message sent too recently
- no concrete reason remains

Soft suppressions:

- user seems overloaded
- last agent message already asked a question
- current thread ended naturally
- opportunity is emotionally weak and not useful

## Outbound Authorship Contract

Every final heartbeat message should be authored by the model from fresh state.

Prompt inputs should include:

- heartbeat opportunity record
- active session summary
- recent dialogue excerpts
- active state
- unresolved commitments
- relevant handoff
- desired tone constraints
- do-not-repeat recent phrasing

The prompt should require:

- one concrete reason for the message
- natural message length
- no assistant preamble
- no overt "just checking in" filler unless context truly warrants it
- no list format unless the content naturally needs it
- no fake claims about subjective feelings

## Main Agent vs Small Writer Model

Two expression modes are allowed:

### Mode A: Lightweight Authored Message

Use when:

- the reason is clear
- the message is short
- no tool work or synthesis is required

This can run on the cheapest reliable writing model.

### Mode B: Main Agent Wakeup

Use when:

- the system needs synthesis
- it must perform work before speaking
- the outreach is emotionally delicate
- it should inspect richer memory context

Main-agent wakeups should be relatively rare.

## Budget Strategy

The system should optimize for:

- frequent cheap state evaluation
- rare expensive authored speech

Expected cost-saving rules:

- no continuous background LLM loop
- no continuous reflection generation
- no periodic "thinking" calls without a candidate opportunity
- no message generation if the send threshold is not cleared
- no duplicate regeneration if the same opportunity is still valid and unsent

## Atlas Schema Additions

Recommended new durable records:

### `presence_state`

One current row per agent namespace.

Fields:

- `agent_namespace`
- `active_session_id`
- `last_user_message_at`
- `last_agent_message_at`
- `last_user_presence_at`
- `current_thread_summary`
- `conversation_energy`
- `tension_score`
- `warmth_score`
- `user_disappeared_mid_thread`
- `last_proactive_message_at`
- `recent_proactive_count_24h`
- `updated_at`

### `heartbeat_opportunities`

Fields:

- `id`
- `agent_namespace`
- `opportunity_key`
- `kind`
- `status`
- `session_id`
- `reason_summary`
- `earliest_send_at`
- `latest_useful_at`
- `priority_score`
- `annoyance_risk`
- `desired_pressure`
- `warmth_target`
- `requires_authored_llm_message`
- `requires_main_agent_reasoning`
- `source_refs`
- `cancel_conditions`
- `created_at`
- `updated_at`
- `last_scored_at`

### `heartbeat_dispatches`

Fields:

- `id`
- `agent_namespace`
- `opportunity_id`
- `session_id`
- `dispatch_kind`
- `message_text`
- `model_name`
- `score_snapshot`
- `sent_at`
- `delivery_status`
- `response_received_at`
- `user_reaction_summary`

### `background_jobs`

Fields:

- `id`
- `agent_namespace`
- `job_key`
- `kind`
- `status`
- `input_summary`
- `result_summary`
- `user_visible`
- `followup_needed`
- `created_at`
- `started_at`
- `completed_at`

## Adaptation Loop

The system should learn from its own outreach quality.

Signals:

- user replied quickly
- user ignored the message
- user seemed annoyed
- outreach was too soon
- outreach landed well
- concrete result messages work better than vague nudges

Atlas should use this to build:

- reflections about outreach quality
- pattern adjustments
- pressure/interval tuning

## Failure Modes

### Over-Eager Agent

Symptom:

- too many pings
- clingy presence

Mitigation:

- hard rate caps
- annoyance penalty
- send suppression after ignored outreach

### Workflow-Like Voice

Symptom:

- sounds like reminders software

Mitigation:

- authored final messages only
- anti-boilerplate prompt rules
- recent-phrase repetition guard

### Fake Randomness

Symptom:

- weirdly arbitrary ping timing

Mitigation:

- context-specific windows
- delay tied to reason type
- quiet-hour and cadence aware jitter

### Contextually Empty Nudges

Symptom:

- "hey just checking in" with no meaning

Mitigation:

- require concrete reason summary
- do not generate if reason quality is low

## Success Criteria

The architecture is successful when:

- the agent sometimes reaches out first for reasons that feel natural
- silence is preserved when no good message exists
- follow-ups feel tied to lived context
- background task completion feels meaningful
- the human increasingly treats the agent as a persistent presence rather than a tool

## Build Principle

Do not optimize first for feature count.

Optimize for illusion quality.

One believable proactive message is worth more than ten reminder-like ones.

This system should be built as continuity infrastructure for a believable artificial person, not as a notification engine.
