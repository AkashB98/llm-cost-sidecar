"""Aggregator: rollups, top-cost, CSV export."""

import csv
import io
import unittest

from sidecar import TraceStore, aggregator


def seed(store):
    rows = [
        ("2026-09-20T10:00:00", "acme", "gpt-4o-mini", 0.10, 100, 50),
        ("2026-09-20T11:00:00", "acme", "gpt-4o", 1.00, 200, 100),
        ("2026-09-20T12:00:00", "globex", "gpt-4o-mini", 0.20, 100, 50),
        ("2026-09-21T10:00:00", "globex", "claude-sonnet-4", 2.00, 300, 200),
    ]
    for ts, tenant, model, cost, pt, ct in rows:
        store.record({"ts": ts, "tenant": tenant, "model": model,
                      "prompt_tokens": pt, "completion_tokens": ct,
                      "latency_ms": 10.0, "cost_usd": cost})


class TestRollup(unittest.TestCase):
    def setUp(self):
        self.store = TraceStore()
        seed(self.store)

    def test_by_model(self):
        rows = {r["key"]: r for r in aggregator.rollup(self.store, "model")}
        self.assertAlmostEqual(rows["gpt-4o"]["cost_usd"], 1.00)
        self.assertEqual(rows["gpt-4o-mini"]["requests"], 2)
        self.assertAlmostEqual(rows["claude-sonnet-4"]["cost_usd"], 2.00)

    def test_by_tenant(self):
        rows = {r["key"]: r for r in aggregator.rollup(self.store, "tenant")}
        self.assertAlmostEqual(rows["acme"]["cost_usd"], 1.10)
        self.assertAlmostEqual(rows["globex"]["cost_usd"], 2.20)

    def test_by_day(self):
        rows = {r["key"]: r for r in aggregator.rollup(self.store, "day")}
        self.assertAlmostEqual(rows["2026-09-20"]["cost_usd"], 1.30)
        self.assertAlmostEqual(rows["2026-09-21"]["cost_usd"], 2.00)

    def test_day_filter(self):
        rows = aggregator.rollup(self.store, "tenant", day="2026-09-21")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "globex")

    def test_rollup_sums_reconcile_with_total(self):
        total = self.store.totals()["cost"]
        by_tenant = sum(r["cost_usd"] for r in aggregator.rollup(self.store, "tenant"))
        by_model = sum(r["cost_usd"] for r in aggregator.rollup(self.store, "model"))
        self.assertAlmostEqual(by_tenant, total, places=5)
        self.assertAlmostEqual(by_model, total, places=5)

    def test_unknown_group_by_raises(self):
        with self.assertRaises(ValueError):
            aggregator.rollup(self.store, "bogus")


class TestTopCost(unittest.TestCase):
    def setUp(self):
        self.store = TraceStore()
        seed(self.store)

    def test_top_tenant_ordering(self):
        top = aggregator.top_cost(self.store, by="tenant", n=1)
        self.assertEqual(top[0]["key"], "globex")  # 2.20 > 1.10

    def test_top_model_n_limits(self):
        top = aggregator.top_cost(self.store, by="model", n=2)
        self.assertEqual(len(top), 2)
        self.assertGreaterEqual(top[0]["cost_usd"], top[1]["cost_usd"])


class TestCsvExport(unittest.TestCase):
    def test_header_and_rows(self):
        store = TraceStore()
        seed(store)
        text = aggregator.events_to_csv(store.query(limit=100))
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 4)
        for col in ("ts", "tenant", "model", "prompt_tokens", "cost_usd", "estimated"):
            self.assertIn(col, rows[0])

    def test_empty_export_is_header_only(self):
        text = aggregator.events_to_csv([])
        self.assertEqual(len(text.strip().splitlines()), 1)


class TestSummary(unittest.TestCase):
    def test_summary_shape(self):
        store = TraceStore()
        seed(store)
        s = aggregator.summary(store)
        self.assertIn("totals", s)
        self.assertIn("by_model", s)
        self.assertIn("by_tenant", s)
        self.assertIn("by_day", s)
        self.assertIn("top_tenants", s)
        self.assertAlmostEqual(s["totals"]["cost_usd"], 3.30)


if __name__ == "__main__":
    unittest.main()
