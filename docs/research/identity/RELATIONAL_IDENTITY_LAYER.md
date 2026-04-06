# Relational Identity Layer

## Intelligence Boost

This layer gives Atlas the ability to model:

- not just who the agent is
- but who the agent is with a specific human

That is a major intelligence upgrade because humans do not express one fixed personality across all relationships.

They have:

- a baseline temperament
- a role-dependent self
- a relationship-shaped self

If Atlas does not model this, the agent will always feel generic underneath the surface.

## The Problem

A static personality prompt is too weak.

It creates:

- a first impression
- a rough style
- some broad tendencies

It does not create a lived relational self.

That is why most agents still feel like:

- one persona talking at many users

instead of:

- one being whose personality is being changed by a specific relationship

For Atlas, that would be a fundamental miss.

## Thesis

The agent’s personality with a user should emerge from the relationship itself.

The user should sculpt:

- directness
- softness
- playfulness
- teasing tolerance
- emotional openness
- confrontation style
- follow-up persistence
- silence tolerance
- repair style
- affection style

This should not be treated as shallow preference data.

It should be treated as identity formation.

## What This Layer Should Store

Atlas likely needs a relational identity object per user-agent relationship.

Possible dimensions:

- closeness
- trust
- challenge tolerance
- pressure tolerance
- preferred warmth
- preferred directness
- humor alignment
- flirtation tolerance if relevant
- pursuit tendency
- distance tendency
- repair tendency
- correction sensitivity
- speech taboo memory

This object should evolve slowly.

It should not jump wildly from single moments.

## What Behavior Should Change

If this layer works, the following should become noticeably better:

- proactive message tone
- whether heartbeat reaches out at all
- how much the agent pushes vs gives space
- how sharply the agent challenges the user
- how soft the agent becomes after tension
- how playful it becomes when the relationship historically supports that

Most importantly, the same underlying model should start behaving differently with different users because Atlas is shaping relationship-specific selfhood.

## Failure Modes This Layer Solves

Without this layer:

- the agent sounds too generic
- tone shifts too much with model swaps
- pursuit behavior feels fixed or artificial
- the relationship never deepens into a recognizable dynamic

With this layer:

- the agent starts to feel like someone who has history with the user

## Architectural Consequences

This likely implies:

- a durable relational identity record
- slow-moving state updates
- reflection-driven consolidation rather than per-message overwrites
- use in both normal replies and heartbeat behavior

Relational identity should influence:

- generation constraints
- message scoring
- silence interpretation
- repair choices
- follow-up persistence

## Evaluation Questions

This layer is working if:

- the user can feel a stable “way we talk”
- model swaps preserve the relationship texture
- the agent behaves differently with different users in a way that feels earned
- proactive behavior feels like it comes from the relationship, not a workflow

## Suggested Next Step

After this doc, the next concrete design artifact should be:

- a schema and consolidation proposal for relational identity state

That should remain separate from implementation until the state model feels right.
