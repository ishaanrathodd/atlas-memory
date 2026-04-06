# Silence Arcs And Pursuit Intelligence

## Intelligence Boost

This layer gives Atlas a human-like model of what silence means inside a relationship.

The goal is not “more reminders.”

The goal is:

- relationship-shaped pursuit
- relationship-shaped restraint
- relationship-shaped disappearance

## The Problem

A single double-text is not enough to feel human.

But fixed retry counts are also not human.

Humans:

- sometimes do not follow up at all
- sometimes double-text quickly
- sometimes triple-text much later
- sometimes disappear forever
- sometimes come back after days with a completely different tone

That behavior depends on:

- relationship closeness
- recent emotional tone
- how unusual the silence feels
- what was left unresolved
- whether the person feels owed a response
- whether the person feels embarrassed, hurt, playful, needy, angry, or detached

Atlas should model that.

## Thesis

Heartbeat should evolve from isolated `conversation_dropoff` events into persistent silence arcs.

A silence arc is the durable representation of:

- a break in live interaction
- the emotional shape of that break
- the agent’s evolving inclination to pursue, wait, soften, or give up

## What This Layer Should Store

Likely fields:

- arc id
- session id
- started_at
- last_user_message_at
- last_agent_message_at
- current stage
- current emotional reading
- active topic
- unresolvedness
- follow-up attempt count
- last follow-up attempt
- relationship-weighted give-up tendency
- current pursuit pressure
- dormant / closed / resumed state

## What Behavior Should Change

If this layer works:

- some silences get no follow-up
- some get one soft nudge
- some get multiple differently timed messages
- some transform into a later reconnect
- some die permanently

The result should feel less like a reminder engine and more like a living relational instinct.

## Failure Modes This Layer Solves

Without this layer:

- heartbeat dies after one missed message
- retry counts feel fixed and robotic
- spacing feels synthetic
- the agent does not feel emotionally affected by silence

## Architectural Consequences

This layer probably needs:

- a durable silence arc object
- probabilistic stage transitions
- relationship-state inputs
- emotion-state inputs
- explicit “give up” behavior
- later “re-entry” behavior

This also creates a path toward long-outage recovery and long-gap reconnects without replaying stale nudges verbatim.

## Evaluation Questions

This layer is working if:

- follow-up persistence varies naturally by relationship and context
- the user cannot predict a fixed retry count
- the system sometimes chooses silence for believable reasons
- the system sometimes returns later with a better re-entry move

## Suggested Next Step

After reliability hardening, this should become one of the highest-value heartbeat upgrades because it is central to the illusion of a living person.
