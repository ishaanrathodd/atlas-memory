# Atlas Memory Specification

This package provides the Atlas memory runtime, retrieval, consolidation, and evaluation utilities.

Primary modules:
- `memory.curator_runtime`
- `memory.enrichment`
- `memory.retrieval_planner`
- `memory.eval_harness`

For implementation details and roadmap, see:
- `MEMORY_IMPLEMENTATION_PLAN.md`
- `MEMORY_REDESIGN.md`

## Quick Setup

Run Atlas setup in one command:

```bash
./.venv/bin/python -m memory.curator_runtime setup
```

What it does:
- creates `~/.hermes/.env` and `~/.hermes/atlas.json` defaults when missing (non-secret values only)
- asks only for:
	- Supabase project URL
	- Supabase service role key
	- embedding model API key
	- LLM model choice (defaults to Hermes `~/.hermes/config.yaml` -> `model.default`)
- runs setup diagnostics
- returns unresolved next steps (for example, missing Supabase key)

Diagnostics only mode:

```bash
./.venv/bin/python -m memory.curator_runtime setup-diagnostics
```
