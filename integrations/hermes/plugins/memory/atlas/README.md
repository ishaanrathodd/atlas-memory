# Atlas Memory Provider

This plugin exposes Atlas as an external Hermes memory provider.

Setup path:

1. Ensure this plugin directory is linked into `~/.hermes/plugins/memory/atlas`.
2. Run `hermes memory setup`.
3. Choose `atlas`.
4. Enter:
   - Supabase project URL
   - Supabase service key
   - optional schema / embedding config

Non-secret Atlas config is saved to `~/.hermes/atlas.json`.
Secrets are saved to `~/.hermes/.env`.

The provider keeps Hermes built-in memory active and adds Atlas as an external memory layer for:

- pre-turn context enrichment
- post-turn turn sync
- session-end closeout
