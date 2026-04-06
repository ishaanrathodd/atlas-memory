# Atlas Research Layers

## Purpose

This folder is the layered research tree for Atlas.

The rule for this tree is simple:

- each document should target one specific intelligence upgrade
- each document should be narrow enough to implement against
- each document should make Atlas more like a persistent virtual human, not just a memory database

The top-level report remains:

- `../ATLAS_IDENTITY_RESEARCH_REPORT.md`

That report defines the overall mission.

This folder breaks that mission into individual research programs.

## How To Use This Tree

When a new capability idea emerges, it should usually become:

1. a narrow research doc here
2. a later implementation plan if the idea survives scrutiny
3. a real schema / runtime change only after the research shape is clear

Each deep-dive should answer:

- what intelligence boost this layer gives Atlas
- what failure mode it fixes today
- what new state must exist
- what behavior should change if the layer works
- how to tell if the layer is actually helping

## Research Tracks

### Identity

- `identity/RELATIONAL_IDENTITY_LAYER.md`
- `identity/DAILY_CONTINUITY_AND_ACTIVE_CONTEXT.md`

Focus:

- who the agent becomes with a specific human
- how Atlas preserves same-day lived continuity

### Heartbeat

- `heartbeat/SILENCE_ARCS_AND_PURSUIT_INTELLIGENCE.md`

Focus:

- how the agent should behave across silence
- how pursuit, distance, and eventual disappearance should be relationship-shaped

### Governance

- `governance/STYLE_DRIFT_AND_IDENTITY_GOVERNANCE.md`

Focus:

- how Atlas overpowers model-native drift
- how the same identity survives model swaps

## Authoring Standard

Good research docs in this tree should be:

- ambitious
- concrete
- falsifiable
- tightly scoped
- implementation-aware

Bad docs in this tree would be:

- vague philosophy with no architectural consequences
- giant catch-all documents
- shallow feature wishlists
- prompt-engineering notes without identity implications

## Current Direction

The current priority order is:

1. relational identity
2. daily continuity and active context
3. silence arcs and pursuit intelligence
4. style drift and identity governance

That order matters because heartbeat quality depends on current context and relationship state before it can become truly human-like.
