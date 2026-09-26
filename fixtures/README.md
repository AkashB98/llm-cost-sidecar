# fixtures — SIMULATED DATA only

Everything in this directory is **synthetic demo material**: a sample budgets config
and a small seeded trace export. Nothing here is real spend, real traffic, or real
customer data. Safe to commit; safe to delete.

- `budgets.sample.json` — example soft/hard thresholds (global + per-tenant) for
  `python cli.py check --db traces.db --config fixtures/budgets.sample.json`.
- `sample_events.jsonl` — 60 seeded trace events (seed=7), exported from the
  simulator. Load one line per event into any TraceStore for a quick look.
