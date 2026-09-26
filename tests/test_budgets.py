"""Budgets: threshold firing, edge-triggering, scoping, stream evaluation."""

import unittest

from sidecar import Pricing, TraceStore, BudgetConfig, BudgetWatcher, AlertLog
from sidecar.middleware import TracedCaller
from sidecar.simulate import ChatResult

DAY = "2026-09-20T10:00:00"


def _store_with_costs(costs, tenant="acme", model="gpt-4o-mini"):
    """Write events directly to the store (bypasses middleware/aggregates)."""
    store = TraceStore()
    for c in costs:
        store.record({"ts": DAY, "tenant": tenant, "model": model,
                      "prompt_tokens": 0, "completion_tokens": 0,
                      "latency_ms": 1.0, "cost_usd": c})
    return store


def _watcher(store, config):
    log = AlertLog(store)
    return BudgetWatcher(store, config, alert_log=log,
                         now=lambda: "2026-09-20T10:00:01"), log


class TestThresholdFiring(unittest.TestCase):
    def test_soft_fires_once_on_crossing(self):
        store = _store_with_costs([0.40, 0.40])  # total 0.80 < 1.0
        watcher, log = _watcher(store, {"global": {"soft": 1.0, "hard": 10.0}})
        self.assertEqual(watcher.evaluate({"ts": DAY, "tenant": "acme"}), [])
        store.record({"ts": DAY, "tenant": "acme", "model": "m",
                      "prompt_tokens": 0, "completion_tokens": 0,
                      "latency_ms": 1.0, "cost_usd": 0.50})  # total 1.30
        fired = watcher.evaluate({"ts": DAY, "tenant": "acme"})
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["level"], "soft")
        self.assertEqual(fired[0]["scope"], "global")
        self.assertEqual(len(log.list()), 1)

    def test_no_refire_after_crossing(self):
        store = _store_with_costs([2.0])
        watcher, log = _watcher(store, {"global": {"soft": 1.0, "hard": 10.0}})
        watcher.evaluate({"ts": DAY, "tenant": "acme"})
        store.record({"ts": DAY, "tenant": "acme", "model": "m",
                      "prompt_tokens": 0, "completion_tokens": 0,
                      "latency_ms": 1.0, "cost_usd": 0.10})
        self.assertEqual(watcher.evaluate({"ts": DAY, "tenant": "acme"}), [])
        self.assertEqual(len(log.list()), 1)  # still exactly one alert

    def test_hard_fires_and_soft_fires_first(self):
        store = TraceStore()
        watcher, log = _watcher(store, {"global": {"soft": 1.0, "hard": 2.0}})
        store.record({"ts": DAY, "tenant": "a", "model": "m", "prompt_tokens": 0,
                      "completion_tokens": 0, "latency_ms": 1.0, "cost_usd": 5.0})
        fired = watcher.evaluate({"ts": DAY, "tenant": "a"})
        levels = [a["level"] for a in fired]
        self.assertEqual(levels, ["soft", "hard"])  # both crossed, soft first
        self.assertTrue(all(a["scope"] == "global" for a in fired))

    def test_reset_allows_refire(self):
        store = _store_with_costs([2.0])
        watcher, _ = _watcher(store, {"global": {"soft": 1.0}})
        watcher.evaluate({"ts": DAY, "tenant": "acme"})
        watcher.reset()
        fired = watcher.evaluate({"ts": DAY, "tenant": "acme"})
        self.assertEqual(len(fired), 1)


class TestScoping(unittest.TestCase):
    def test_per_tenant_thresholds_are_independent(self):
        store = _store_with_costs([6.0], tenant="acme")
        watcher, _ = _watcher(store, {"tenants": {"acme": {"soft": 5.0},
                                                 "globex": {"soft": 5.0}}})
        fired = watcher.evaluate({"ts": DAY, "tenant": "acme"})
        self.assertEqual([(a["scope"], a["level"]) for a in fired],
                         [("tenant:acme", "soft")])
        # globex has spent nothing: evaluating a globex event fires nothing new
        self.assertEqual(watcher.evaluate({"ts": DAY, "tenant": "globex"}), [])

    def test_global_and_tenant_can_fire_together(self):
        store = _store_with_costs([6.0], tenant="acme")
        watcher, _ = _watcher(store, {"global": {"soft": 5.0},
                                      "tenants": {"acme": {"soft": 5.0}}})
        fired = watcher.evaluate({"ts": DAY, "tenant": "acme"})
        scopes = sorted(a["scope"] for a in fired)
        self.assertEqual(scopes, ["global", "tenant:acme"])

    def test_new_day_resets_edge_trigger(self):
        store = _store_with_costs([6.0])
        watcher, log = _watcher(store, {"global": {"soft": 5.0}})
        watcher.evaluate({"ts": DAY, "tenant": "acme"})
        store.record({"ts": "2026-09-21T10:00:00", "tenant": "acme", "model": "m",
                      "prompt_tokens": 0, "completion_tokens": 0,
                      "latency_ms": 1.0, "cost_usd": 6.0})
        fired = watcher.evaluate({"ts": "2026-09-21T10:00:00", "tenant": "acme"})
        self.assertEqual(len(fired), 1)  # new day -> fires again
        self.assertEqual(len(log.list()), 2)


class TestStreamEvaluation(unittest.TestCase):
    def test_alerts_read_live_store_not_stale_aggregates(self):
        # Events written straight to the store (no middleware, no rollups built):
        # the watcher must still see them, because it SUMs the trace stream.
        store = TraceStore()
        watcher, log = _watcher(store, {"global": {"soft": 0.50}})
        pricing = Pricing.load()
        caller = TracedCaller(store, pricing, watcher=watcher,
                              now=lambda: DAY)
        caller.call(lambda model, messages: ChatResult("r", "gpt-4o", 100000, 0),
                    "gpt-4o", [], tenant="acme")  # $250 in one call
        self.assertEqual(len(log.list()), 1)
        self.assertEqual(log.list()[0]["level"], "soft")

    def test_status_reports_ok_soft_hard(self):
        store = _store_with_costs([0.10])
        watcher, _ = _watcher(store, {"global": {"soft": 1.0, "hard": 2.0}})
        self.assertEqual(watcher.status()["global"]["state"], "ok")
        store.record({"ts": DAY, "tenant": "x", "model": "m", "prompt_tokens": 0,
                      "completion_tokens": 0, "latency_ms": 1.0, "cost_usd": 1.0})
        self.assertEqual(watcher.status()["global"]["state"], "soft")
        store.record({"ts": DAY, "tenant": "x", "model": "m", "prompt_tokens": 0,
                      "completion_tokens": 0, "latency_ms": 1.0, "cost_usd": 1.0})
        self.assertEqual(watcher.status()["global"]["state"], "hard")


class TestSinks(unittest.TestCase):
    def test_sink_receives_alert(self):
        received = []

        class RecSink:
            def send(self, alert):
                received.append(alert)
                return True

        store = _store_with_costs([5.0])
        log = AlertLog(store)
        watcher = BudgetWatcher(store, {"global": {"soft": 1.0}},
                                alert_log=log, now=lambda: DAY, sinks=[RecSink()])
        watcher.evaluate({"ts": DAY, "tenant": "acme"})
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["level"], "soft")

    def test_dead_sink_never_breaks_tracing(self):
        class DeadSink:
            def send(self, alert):
                raise ConnectionError("webhook down")

        store = _store_with_costs([5.0])
        log = AlertLog(store)
        watcher = BudgetWatcher(store, {"global": {"soft": 1.0}},
                                alert_log=log, now=lambda: DAY, sinks=[DeadSink()])
        fired = watcher.evaluate({"ts": DAY, "tenant": "acme"})  # must not raise
        self.assertEqual(len(fired), 1)
        self.assertEqual(len(log.list()), 1)


if __name__ == "__main__":
    unittest.main()
