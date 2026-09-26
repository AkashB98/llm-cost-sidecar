#!/usr/bin/env python3
"""Offline end-to-end demo: simulate traffic -> trace -> budgets -> report.

Fully offline: no network, no API keys. Optional --serve starts the dashboard.

    python demo.py            # simulate + print report + fire demo alerts
    python demo.py --serve    # ...then serve the dashboard on :8000
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sidecar import (Pricing, TraceStore, BudgetConfig, BudgetWatcher,
                     AlertLog, PrintSink, generate_traffic, aggregator, serve,
                     SimulatedLLM, trace_calls, tenant)


def build_demo_store():
    pricing = Pricing.load()
    store = TraceStore()
    alert_log = AlertLog(store)
    # Budgets set LOW on purpose so the deterministic sim traffic crosses them
    # and you can watch alerts fire.
    config = BudgetConfig({
        "global": {"soft": 2.00, "hard": 5.00},
        "tenants": {"acme": {"soft": 0.50, "hard": 1.50}},
    })
    watcher = BudgetWatcher(store, config, alert_log=alert_log, sinks=[PrintSink()])
    n = generate_traffic(store, pricing, seed=7, days=7,
                         tenants=("acme", "globex", "initech"),
                         models=("gpt-4o-mini", "gpt-4o", "claude-sonnet-4"),
                         requests_per_day=40, watcher=watcher)
    return store, pricing, watcher, alert_log, n


def print_report(store):
    s = aggregator.summary(store)
    t = s["totals"]
    print(f"\n=== llm-cost-sidecar demo report (SIMULATED DATA) ===")
    print(f"requests: {t['requests']:,}   tokens: {t['tokens']:,}   "
          f"cost: ${t['cost_usd']:.4f}   avg latency: {t['avg_latency_ms']:.0f} ms")
    print(f"\n{'model':<18}{'reqs':>7}{'tokens':>10}{'cost':>12}")
    for r in s["by_model"]:
        print(f"{r['key']:<18}{r['requests']:>7}{r['total_tokens']:>10,} "
              f"${r['cost_usd']:>11.4f}")
    print(f"\n{'tenant':<18}{'reqs':>7}{'cost':>12}")
    for r in s["top_tenants"]:
        print(f"{r['key']:<18}{r['requests']:>7} ${r['cost_usd']:>11.4f}")


def live_call_demo(store, pricing):
    """Show the middleware on a single live (simulated) call with tenant labels."""
    llm = SimulatedLLM(seed=99)
    traced = trace_calls(store, pricing)(llm.chat)
    with tenant("demo-tenant", feature="checkout-summarizer"):
        resp = traced(model="gpt-4o-mini",
                      messages=[{"role": "user", "content": "Summarize this checkout flow."}])
    ev = store.query(tenant="demo-tenant", limit=1)[0]
    print(f"\nLive traced call -> model={ev['model']} tokens={ev['total_tokens']} "
          f"cost=${ev['cost_usd']:.6f} latency={ev['latency_ms']:.0f}ms "
          f"labels=checkout-summarizer")
    print(f"Response passed through untouched: {resp.text[:40]!r}...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="serve the dashboard after the demo")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    store, pricing, watcher, alert_log, n = build_demo_store()
    print(f"Simulated {n} requests across 3 tenants x 3 models x 7 days (seed=7).")
    print(f"Alerts fired during sim: {len(alert_log.list())} "
          f"(global soft/hard + acme tenant soft/hard expected)")
    print_report(store)
    live_call_demo(store, pricing)

    st = watcher.status()
    print(f"\nBudget status now: global={st['global']}")
    if args.serve:
        serve(store, port=args.port, pricing=pricing, watcher=watcher,
              alert_log=alert_log)


if __name__ == "__main__":
    main()
