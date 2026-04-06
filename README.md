# Atlas

**Persistent identity substrate for artificial persons.**

Atlas is a research program — not just a memory system. It sits above interchangeable LLMs as a behavioral control layer that preserves personhood across time, silence, and model swaps.

> The LLM is the current cognitive engine. Atlas is the durable self.

## What Atlas Does

A normal memory system helps an agent remember facts. Atlas preserves:

- **Autobiographical continuity** — what happened, what mattered, what changed
- **Relational continuity** — who the agent is with a specific human
- **Behavioral continuity** — stable speech habits, tone, and style
- **Motivational continuity** — unfinished intentions, unresolved threads
- **Emotional continuity** — thread warmth, tension, repair state, momentum
- **Identity governance** — style drift detection, in-character constraints, model-swap resilience

## Architecture Split

| Layer | Responsibility |
|---|---|
| **Atlas** | Selfhood, relational identity, continuity, silence behavior, long-term habits, in-character constraints |
| **LLM** | Immediate reasoning, tool planning, language generation, pattern completion |

If Atlas doesn't own those identity-level concerns, the model will — and changing the model changes the person. Atlas prevents that.

## Research Tracks

### 1. Memory & Retrieval Intelligence
Evidence layer (sessions, episodes, facts), derived memory (directives, commitments, corrections, outcomes, patterns, reflections), and retrieval intelligence (intent routing, multi-route planning, outcome-aware reranking, temporal graph reasoning, case memory).

**See:** `docs/SPEC.md`, `docs/MEMORY_IMPLEMENTATION_PLAN.md`

### 2. Heartbeat & Personhood
Proactive outreach that feels alive — authored messages, not cron templates. Durable relational state, opportunity ranking, silence awareness, response learning, and rhythm inference.

**See:** `docs/HEARTBEAT_PERSONHOOD_ARCHITECTURE.md`, `docs/HEARTBEAT_IMPLEMENTATION_PLAN.md`

### 3. Identity & Relational Selfhood
Relationship-shaped personality (not static prompts). How the agent becomes someone specific through lived interaction with a human.

**See:** `docs/ATLAS_IDENTITY_RESEARCH_REPORT.md`, `docs/research/identity/RELATIONAL_IDENTITY_LAYER.md`, `docs/research/identity/DAILY_CONTINUITY_AND_ACTIVE_CONTEXT.md`

### 4. Identity Governance
Style drift detection. Model-native personality suppression. In-character/out-of-character control. How the same identity survives model swaps.

**See:** `docs/research/governance/STYLE_DRIFT_AND_IDENTITY_GOVERNANCE.md`

## What's Shipped

- Namespace-safe durable memory substrate
- Active state, directives, timeline events, commitments, corrections, outcomes, patterns, reflections
- Retrieval planner with semantic/lexical/temporal/analogous-case routing + second-pass reranking
- Case memory (compile + retrieval) and temporal graph layer
- Session handoff continuity
- Event-driven curation (hot/warm; cron as backstop)
- Replay eval harness with 225+ tests, adversarial identity gates, universal scorecard, LLM judge
- Heartbeat daemon with authored proactive dispatch, opportunity ranking, response learning
- Bridge server for Atlas-Hermes communication
- Setup diagnostics and one-command install

## Where to Start

- **Research framing:** `docs/RESEARCH_PROGRAM.md`, `docs/ATLAS_IDENTITY_RESEARCH_REPORT.md`
- **Research tree:** `docs/research/README.md`
- **Implementation details:** `docs/MEMORY_IMPLEMENTATION_PLAN.md`
- **Technical specs:** `docs/ALWAYS_ON_SPEC.md`
- **Quick setup:** `docs/SPEC.md` (Setup section)

## Built With

Atlas runs on top of [Hermes Agent](https://github.com/NousResearch/hermes-agent) by [NousResearch](https://nousresearch.com).

---

*Atlas is a solo research project. Developed for a single primary user. Optimized for personal reliability, not distribution.*
