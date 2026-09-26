"""Trace store: recording, filtering, spend math, thread safety."""

import threading
import unittest

from sidecar import TraceStore


def ev(ts="2026-09-20T10:00:00", tenant="acme", model="gpt-4o-mini",
       cost=0.01, pt=100, ct=50):
    return {"ts": ts, "tenant": tenant, "model": model, "prompt_tokens": pt,
            "completion_tokens": ct, "latency_ms": 12.5, "cost_usd": cost}


class TestTraceStore(unittest.TestCase):
    def test_record_assigns_id_and_roundtrips(self):
        store = TraceStore()
        row = store.record(ev())
        self.assertIn("id", row)
        self.assertEqual(store.count(), 1)
        got = store.query()[0]
        self.assertEqual(got["id"], row["id"])
        self.assertEqual(got["model"], "gpt-4o-mini")
        self.assertEqual(got["total_tokens"], 150)

    def test_query_filters(self):
        store = TraceStore()
        store.record(ev(tenant="acme", model="gpt-4o-mini"))
        store.record(ev(tenant="globex", model="gpt-4o"))
        store.record(ev(tenant="acme", model="gpt-4o", ts="2026-09-21T10:00:00"))
        self.assertEqual(len(store.query(tenant="acme")), 2)
        self.assertEqual(len(store.query(model="gpt-4o")), 2)
        self.assertEqual(len(store.query(day="2026-09-21")), 1)
        self.assertEqual(len(store.query(tenant="acme", day="2026-09-20")), 1)

    def test_spend_sums_by_scope(self):
        store = TraceStore()
        store.record(ev(tenant="acme", cost=0.10))
        store.record(ev(tenant="acme", cost=0.20))
        store.record(ev(tenant="globex", cost=1.00))
        self.assertAlmostEqual(store.spend(tenant="acme"), 0.30)
        self.assertAlmostEqual(store.spend(tenant="globex"), 1.00)
        self.assertAlmostEqual(store.spend(), 1.30)  # global = all tenants
        self.assertAlmostEqual(store.spend(day="2026-09-21"), 0.0)

    def test_totals(self):
        store = TraceStore()
        store.record(ev(cost=0.10))
        store.record(ev(cost=0.20))
        t = store.totals()
        self.assertEqual(t["requests"], 2)
        self.assertAlmostEqual(t["cost"], 0.30)
        self.assertEqual(t["tokens"], 300)

    def test_simulated_flag(self):
        store = TraceStore()
        self.assertFalse(store.simulated)
        store.mark_simulated()
        self.assertTrue(store.simulated)

    def test_thread_safe_concurrent_writes(self):
        store = TraceStore()
        def worker():
            for _ in range(25):
                store.record(ev())
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(store.count(), 200)


if __name__ == "__main__":
    unittest.main()
