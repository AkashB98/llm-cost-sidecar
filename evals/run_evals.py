#!/usr/bin/env python3
"""Golden verification suite: determinism, alert correctness, pricing sanity,
aggregation reconciliation. Writes evals/verify_report.json. Fully offline."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sidecar import (Pricing, TraceStore, BudgetWatcher, AlertLog,
                     generate_traffic, aggregator)
from sidecar.middleware import TracedCaller
from sidecar.simulate import ChatResult

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append({"name": name, "pass": True, "detail": detail})
    except AssertionError as e:
        RESULTS.append({"name": name, "pass": False, "detail": str(e)})


def determinism():
    p = Pricing.load()

    def snap(store):
        rows = sorted(store.query(limit=100000), key=lambda r: r["id"])
        return [{k: v for k, v in r.items() if k not in ("id", "request_id")}
                for r in rows]

    s1, s2 = TraceStore(), TraceStore()
    generate_traffic(s1, p, seed=7, days=3, requests_per_day=25)
    generate_traffic(s2, p, seed=7, days=3, requests_per_day=25)
    assert snap(s1) == snap(s2), "same seed produced different streams"
    s3 = TraceStore()
    generate_traffic(s3, p, seed=8, days=3, requests_per_day=25)
    assert snap(s1) != snap(s3), "different seeds produced identical streams"
    return "seed=7 x2 identical (75 events), seed=8 differs"


def alert_scenario():
    store = TraceStore()
    log = AlertLog(store)
    watcher = BudgetWatcher(store, {"global": {"soft": 1.0, "hard": 2.0}},
                            alert_log=log, now=lambda: "2026-09-20T10:00:00")
    pricing = Pricing.load()
    caller = TracedCaller(store, pricing, watcher=watcher,
                          now=lambda: "2026-09-20T10:00:00")
    # One $2.50 call crosses soft AND hard in a single event.
    caller.call(lambda model, messages: ChatResult("r", "gpt-4o", 1000000, 0),
                "gpt-4o", [], tenant="acme")
    alerts = log.list()  # newest-first; reverse to get firing order
    assert [a["level"] for a in reversed(alerts)] == ["soft", "hard"], \
        f"expected [soft, hard], got {[a['level'] for a in reversed(alerts)]}"
    # A second crossing event must NOT re-fire (edge-triggered).
    fired = watcher.evaluate({"ts": "2026-09-20T11:00:00", "tenant": "acme"})
    assert fired == [], f"re-fired: {fired}"
    return "single $2.50 call fired soft then hard; no re-fire on later events"


def pricing_sanity():
    p = Pricing.load()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "sidecar", "pricing.json"), encoding="utf-8") as fh:
        raw = fh.read()
    assert "SAMPLE" in raw, "pricing table lost its SAMPLE disclaimer"
    for name in p.model_names():
        entry = p.models[name]
        assert entry["input_per_1k"] > 0 and entry["output_per_1k"] > 0, name
        assert entry["output_per_1k"] >= entry["input_per_1k"], name
    cost, est = p.estimate_cost("not-a-real-model", 1000, 1000)
    assert est and cost > 0, "fallback must flag estimated"
    return f"{len(p.model_names())} models sane; fallback flagged; SAMPLE disclaimer present"


def reconciliation():
    p = Pricing.load()
    store = TraceStore()
    generate_traffic(store, p, seed=7, days=4, requests_per_day=30)
    total = store.totals()["cost"]
    by_tenant = sum(r["cost_usd"] for r in aggregator.rollup(store, "tenant"))
    by_model = sum(r["cost_usd"] for r in aggregator.rollup(store, "model"))
    by_day = sum(r["cost_usd"] for r in aggregator.rollup(store, "day"))
    for name, val in (("tenant", by_tenant), ("model", by_model), ("day", by_day)):
        assert abs(val - total) < 1e-4, f"{name} rollup {val} != total {total}"
    return f"tenant/model/day rollups all reconcile to ${total:.4f}"


def main():
    check("determinism", determinism)
    check("alert_scenario", alert_scenario)
    check("pricing_sanity", pricing_sanity)
    check("reconciliation", reconciliation)
    passed = sum(1 for r in RESULTS if r["pass"])
    report = {"suite": "llm-cost-sidecar golden verification",
              "passed": passed, "total": len(RESULTS), "checks": RESULTS}
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    for r in RESULTS:
        print(f"[{'PASS' if r['pass'] else 'FAIL'}] {r['name']}: {r['detail']}")
    print(f"{passed}/{len(RESULTS)} checks passed -> {out}")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
