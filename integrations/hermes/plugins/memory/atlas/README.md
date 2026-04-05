# Atlas Memory Provider

This plugin exposes Atlas as an external Hermes memory provider.

Setup path:

1. Run `hermes memory setup`.
2. Choose `atlas`.
3. Enter:
   - Supabase project URL
   - Supabase service key
   - embedding model API key
   - LLM model choice

Atlas setup is fail-fast by design:

- Hermes delegates to Atlas guided setup (`python -m memory.curator_runtime setup`).
- If Atlas setup fails, Hermes does not fall back to legacy provider prompts.
- Re-run `hermes memory setup` after fixing the reported issue.

Runtime/plugin path behavior:

- Atlas root discovery order: `ATLAS_ROOT` override, integration-relative path, `~/.hermes/atlas`, then `cwd/atlas`.
- Python runtime selection order: `atlas/.venv/bin/python`, then `atlas/venv/bin/python`, then Hermes Python.
- When Hermes Python is used, Atlas `src` is prepended to `PYTHONPATH` automatically.

Non-interactive diagnostics:

- `python -m memory.curator_runtime setup-diagnostics`
- `python -m memory.curator_runtime setup --no-auto-fix`
- `python -m memory.curator_runtime replay-eval --scenarios-file tests/fixtures/replay_eval_scenarios.json`

Atlas is now also available as a built-in Hermes memory plugin. This integration copy remains the source implementation and can still be linked as a user plugin override when needed.

Non-secret Atlas config is saved to `~/.hermes/atlas.json`.
Secrets are saved to `~/.hermes/.env`.

The provider keeps Hermes built-in memory active and adds Atlas as an external memory layer for:

- pre-turn context enrichment
- post-turn turn sync
- session-end closeout
