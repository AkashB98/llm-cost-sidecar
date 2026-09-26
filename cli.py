#!/usr/bin/env python3
"""CLI: sim | report | check | serve. All offline, stdlib only.

    python cli.py sim --db traces.db --days 7 --seed 7
    python cli.py report --db traces.db
    python cli.py check --db traces.db --config fixtures/budgets.sample.json
    python cli.py serve --db traces.db --port 8000
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sidecar import (Pricing, TraceStore, BudgetConfig, BudgetWatcher,
                     AlertLog, generate_traffic, aggregator, serve)


def cmd_sim(args):
    store = TraceStore(args.db)
    pricing = Pricing.load()
    n = generate_traffic(store, pricing, seed=args.seed, days=args.days,
                         tenants=tuple(args.tenants), models=tuple(args.models),
                         requests_per_day=args.requests_per_day)
    t = store.totals()
    print(f"simulated {n} requests -> {args.db} "
          f"(${t['cost']:.4f} total, seed={args.seed})")


def cmd_report(args):
    store = TraceStore(args.db)
    s = aggregator.summary(store, day=args.day)
    t = s["totals"]
    scope = f"day={args.day}" if args.day else "all time"
    print(f"--- cost report ({scope}) ---")
    print(f"requests={t['requests']:,} tokens={t['tokens']:,} "
          f"cost=${t['cost_usd']:.4f} avg_latency={t['avg_latency_ms']:.0f}ms")
    print(f"\n{'tenant':<16}{'reqs':>6}{'cost':>12}")
    for r in s["by_tenant"]:
        print(f"{(r['key'] or '(unlabeled)'):<16}{r['requests']:>6} ${r['cost_usd']:>11.4f}")
    print(f"\n{'model':<20}{'reqs':>6}{'cost':>12}")
    for r in s["by_model"]:
        print(f"{r['key']:<20}{r['requests']:>6} ${r['cost_usd']:>11.4f}")
    if args.csv:
        events = store.query(day=args.day, limit=100000)
        with open(args.csv, "w", encoding="utf-8") as fh:
            fh.write(aggregator.events_to_csv(events))
        print(f"\nCSV exported -> {args.csv}")


def cmd_check(args):
    """Exit codes for CI: 0 = within soft, 1 = soft crossed, 2 = hard crossed."""
    store = TraceStore(args.db)
    config = BudgetConfig.from_json(args.config) if args.config else BudgetConfig()
    watcher = BudgetWatcher(store, config)
    st = watcher.status(day=args.day)
    worst = 0
    print(f"--- budget check ({args.db}) ---")
    for scope, info in [("global", st["global"])] + sorted(st["tenants"].items()):
        print(f"{scope:<16} spend=${info['spend_usd']:.4f} "
              f"soft={info['soft']} hard={info['hard']} state={info['state']}")
        worst = max(worst, {"ok": 0, "soft": 1, "hard": 2}[info["state"]])
    sys.exit(worst)


def cmd_serve(args):
    store = TraceStore(args.db)
    pricing = Pricing.load()
    watcher = None
    alert_log = AlertLog(store)
    if args.config:
        watcher = BudgetWatcher(store, BudgetConfig.from_json(args.config),
                                alert_log=alert_log)
    serve(store, host=args.host, port=args.port, pricing=pricing,
          watcher=watcher, alert_log=alert_log)


def main():
    ap = argparse.ArgumentParser(prog="cli.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sim", help="generate deterministic synthetic traffic")
    p.add_argument("--db", default="traces.db")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--tenants", nargs="+", default=["acme", "globex", "initech"])
    p.add_argument("--models", nargs="+",
                   default=["gpt-4o-mini", "gpt-4o", "claude-sonnet-4"])
    p.add_argument("--requests-per-day", type=int, default=40)

    p = sub.add_parser("report", help="print a cost summary table")
    p.add_argument("--db", default="traces.db")
    p.add_argument("--day", default=None)
    p.add_argument("--csv", default=None, help="also export events to this CSV path")

    p = sub.add_parser("check", help="budget status with CI-friendly exit code")
    p.add_argument("--db", default="traces.db")
    p.add_argument("--config", default=None)
    p.add_argument("--day", default=None)

    p = sub.add_parser("serve", help="run the dashboard server")
    p.add_argument("--db", default="traces.db")
    p.add_argument("--config", default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    args = ap.parse_args()
    {"sim": cmd_sim, "report": cmd_report, "check": cmd_check,
     "serve": cmd_serve}[args.cmd](args)


if __name__ == "__main__":
    main()
