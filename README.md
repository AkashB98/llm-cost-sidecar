# llm-cost-sidecar

**Per-request LLM cost observability: a drop-in tracing sidecar that makes AI spend legible — tokens, dollars, latency, per tenant — and fires budget alerts before the invoice arrives.**

An AI feature with no per-request cost visibility is a billing incident waiting to happen. One runaway retry loop, one customer pasting novels into the prompt box, and finance finds out at month-end. This sidecar sits next to your LLM calls, records what each request actually cost, rolls it up by model / tenant / day, and enforces soft and hard budgets on the live event stream.

## What it is

- **middleware** — code that sits between your app and the LLM call, watching passively. Wrap any OpenAI-compatible `chat(model=, messages=)` function with one decorator; every call gets traced (timestamp, model, **tokens** — the chunks of text the model reads and writes, the unit you're billed on, **latency** — how long the call took, **tenant** — which customer or team the request belongs to, and estimated cost). The provider's response is returned untouched (**passthrough** — the wrapper observes but never modifies).
- **Pricing table** — a bundled `pricing.json` turns token counts into dollars. Sample rates, clearly labeled (see honesty note below); unknown models use a flagged fallback rate.
- **Budget alerts** — soft/hard thresholds, global and per-tenant, evaluated against the live trace stream (never stale aggregates). **edge-triggered** — each threshold fires exactly once per scope per day, on the crossing event. Alerts go to an offline log plus a pluggable **webhook** — an HTTP POST you can point at Slack/PagerDuty (registered explicitly, never called by default).
- **Aggregation engine** — per-model / per-tenant / per-day **rollups** — plain-English: many per-request rows added up into totals, plus top-cost queries and CSV export.
- **Dashboard + API** — zero-dependency HTTP server: inline-SVG cost-over-time chart, per-tenant table, alert list, `/api/events`, `/api/summary`, `/api/alerts`, `/api/budgets`, `/export.csv`.
- **CLI** — `sim` (deterministic synthetic traffic), `report` (cost tables), `check` (budget status with CI-friendly exit codes: 0 ok / 1 soft crossed / 2 hard crossed), `serve`.
- **Fully offline** — no network, no API keys. The simulator is **seeded deterministic** — same seed always produces the same traffic, so demos and evals are reproducible.

Stdlib only. No pip installs. Python 3.8+.

## Quickstart

```bash
# 1. Generate a week of deterministic demo traffic (offline, no keys)
python cli.py sim --db traces.db --days 7 --seed 7

# 2. Print the cost report
python cli.py report --db traces.db

# 3. Check budgets (exit 2 = hard budget crossed; wire into CI)
python cli.py check --db traces.db --config fixtures/budgets.sample.json

# 4. Or run the whole thing end to end
python demo.py
python demo.py --serve   # dashboard at http://127.0.0.1:8000
```

Drop it into your own code:

```python
from sidecar import Pricing, TraceStore, trace_calls, tenant

store = TraceStore("traces.db")          # sqlite file, created at runtime
pricing = Pricing.load()                 # bundled sample rates

@trace_calls(store, pricing)
def chat(model, messages, **kw):         # your OpenAI-compatible call
    return openai_client.chat.completions.create(model=model, messages=messages, **kw)

with tenant("acme-corp"):                # labels every call in the block
    resp = chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
# resp is the provider's response object, untouched. The trace is in traces.db.
```

With budget enforcement:

```python
from sidecar import BudgetWatcher, BudgetConfig, AlertLog, PrintSink

alert_log = AlertLog(store)
watcher = BudgetWatcher(store, BudgetConfig({
    "global": {"soft": 25.0, "hard": 100.0},
    "tenants": {"acme-corp": {"soft": 5.0, "hard": 20.0}},
}), alert_log=alert_log, sinks=[PrintSink()])   # swap in WebhookSink(url) for real paging

traced = trace_calls(store, pricing, watcher=watcher)(chat)
```

## Architecture

```
your app ──► @trace_calls ──► LLM provider (OpenAI-compatible)
                   │
                   ├─► TraceStore (sqlite, append-only events)
                   │         ├─► aggregator (rollups, top-cost, CSV)
                   │         └─► server (dashboard + JSON API)
                   └─► BudgetWatcher ──► AlertLog + sinks (Print/Webhook)
```

- `sidecar/middleware.py` — `TracedCaller` / `trace_calls` decorator / `tenant()` context manager. Times the call, reads `response.usage` (falls back to a rough `len/4` token estimate, flagged `estimated=1`), prices it, records one event, returns the response object unchanged.
- `sidecar/trace.py` — thread-safe SQLite store (`events`, `alerts`, `meta`). The runtime DB is created on first use and git-ignored; only the synthetic `fixtures/` ship.
- `sidecar/pricing.py` — cost math over `pricing.json`.
- `sidecar/budgets.py` — `BudgetConfig` + `BudgetWatcher`: per-event threshold evaluation via fresh `SUM()` queries on the trace stream.
- `sidecar/alerts.py` — `AlertLog`, `PrintSink`, `WebhookSink`, `FanoutSink` (one dead sink never blocks the others).
- `sidecar/aggregator.py` — rollups, `top_cost`, CSV export.
- `sidecar/server.py` — `ThreadingHTTPServer`, JSON API + inline-SVG dashboard (no JS, no deps).
- `sidecar/simulate.py` — `SimulatedLLM` (OpenAI-SDK-shaped `ChatResult`) + `generate_traffic` (fixed dates, seeded RNG).
- `cli.py`, `demo.py`, `evals/run_evals.py` — CLI, end-to-end demo, golden verification.

## Pricing-table honesty note

`sidecar/pricing.json` contains **SAMPLE** per-1K-token rates bundled so the project runs fully offline. They are not live provider prices. Every event that uses the fallback rate (unknown model) or estimated tokens is flagged `estimated=1` in the store, and the dashboard + fixtures carry SIMULATED DATA banners. **Verify current prices on the provider's own pricing page before using this for real billing decisions.**

## Budgets & alerts

Thresholds live in a JSON config (`fixtures/budgets.sample.json` is an example):

```json
{"global": {"soft": 25.0, "hard": 100.0},
 "tenants": {"acme": {"soft": 5.0, "hard": 20.0}}}
```

- Evaluated on **every recorded event** against the trace store itself — an alert always reflects what's actually been recorded, not a cached number.
- **Edge-triggered**: one alert per (scope, day, level). Crossing soft then hard in one expensive call fires both, in order; later requests don't re-fire.
- `cli.py check` exits 0/1/2 (ok/soft/hard) — drop it in CI to gate deploys on spend.
- Webhooks are opt-in: `WebhookSink("https://hooks.example.com/...")`. Nothing phones home by default.

## API + dashboard

`python cli.py serve --db traces.db --port 8000` (binds localhost by default):

| Route | What |
|---|---|
| `GET /` | Dashboard: cost-over-time SVG chart, per-tenant table, alerts, budget status |
| `GET /api/events?tenant=&model=&day=&limit=` | Raw traced events (newest first) |
| `GET /api/summary?day=` | Totals + by-model / by-tenant / by-day rollups |
| `GET /api/alerts` | Fired alerts |
| `GET /api/budgets?day=` | Spend vs thresholds per scope |
| `GET /export.csv` | Events as CSV download |

## Tests & evals

**hermetic** — plain-English: the tests never touch the network or the real clock (fixed timestamps, injected clocks, seeded RNG), so they pass identically on any machine.

```bash
python3 -m unittest discover -s tests   # 71 tests
python3 evals/run_evals.py              # 4 golden checks -> evals/verify_report.json
```

Coverage: cost math across models (incl. fallback flagging), token-estimation, middleware passthrough (response object identity, messages unmutated), tenant labels + overrides, injected-clock latency, alert threshold firing (soft vs hard, ordering, no re-fire, per-tenant independence, day rollover), sinks (dead webhook can't break tracing), aggregation reconciliation, CSV export, simulator determinism, 8-thread concurrent writes, server routes, CLI exit codes.

## Dev loop: 3 real bugs the tests caught

1. **Random `request_id` broke determinism.** The simulator's "same seed → identical stream" test failed because every event carries a `uuid4` request ID. Rightly so — IDs are random by design. Fixed the *test* to compare deterministic fields and added a separate uniqueness assertion. Lesson: determinism checks must name exactly which fields are deterministic.
2. **Eval asserted alert order against a newest-first log.** `alert_scenario` expected `[soft, hard]` from `AlertLog.list()`, which returns newest-first. The watcher fired them in the right order; the assertion was wrong. Fixed with `reversed()` — and it documented the log's ordering contract.
3. **Tenant scope nearly measured global spend.** While wiring `BudgetWatcher.evaluate`, the tenant scope passed `tenant or None` into the spend query — for an unlabeled (empty-string) tenant that collapses to the *global* sum, which would fire tenant alerts on everyone's money. Caught while writing, fixed to pass the raw tenant string.

## Plugging in real LLM calls later

The traced function just needs the OpenAI call shape — `fn(model=, messages=)` returning an object with `.usage` (or a dict with `usage`/`text`):

```python
import os
from openai import OpenAI
client = OpenAI(base_url=os.environ["LLM_BASE_URL"])  # any OpenAI-compatible endpoint

@trace_calls(store, pricing)
def chat(model, messages, **kw):
    return client.chat.completions.create(model=model, messages=messages, **kw)
```

No API key is needed for anything in this repo — the simulator covers the full loop offline. When you go live, **replace `sidecar/pricing.json` with current provider rates** (the honesty note above applies) and register a `WebhookSink` for your alerting channel.

## Customize

- **Add a model**: one entry in `sidecar/pricing.json` (`input_per_1k` / `output_per_1k`).
- **Change budget windows**: `BudgetWatcher.evaluate` uses per-day scopes; adapt `store.spend(day=...)` for weekly/monthly.
- **Richer labels**: `with tenant("acme", plan="pro", region="us")` or per-call `labels={...}` — stored as JSON on the event, filterable in `/api/events`.
- **Real-time gating**: `watcher.evaluate` returns fired alerts — return HTTP 429 from your own wrapper when a `hard` alert fires for that tenant.

## Layout

```
sidecar/          the package (pricing, trace, middleware, budgets, alerts,
                  aggregator, server, simulate)
cli.py            sim | report | check | serve
demo.py           offline end-to-end demo (+ --serve)
evals/            run_evals.py + verify_report.json (4 golden checks)
fixtures/         budgets.sample.json + 60 seeded sample events (SIMULATED DATA)
tests/            71 hermetic unit tests
```

## License

MIT — see LICENSE.
