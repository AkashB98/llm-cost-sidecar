"""Simulated client: determinism, ranges, shapes."""

import unittest

from sidecar import Pricing, TraceStore, generate_traffic
from sidecar.simulate import SimulatedLLM


def stream_snapshot(store):
    """Deterministic event fields (excludes DB id and random request_id)."""
    rows = sorted(store.query(limit=100000), key=lambda r: r["id"])
    return [{k: v for k, v in r.items() if k not in ("id", "request_id")} for r in rows]


class TestSimulatedLLM(unittest.TestCase):
    def test_usage_shape(self):
        llm = SimulatedLLM(seed=1)
        r = llm.chat(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
        self.assertIn("prompt_tokens", r.usage)
        self.assertIn("completion_tokens", r.usage)
        self.assertEqual(r.model, "gpt-4o-mini")

    def test_same_seed_same_stream(self):
        a, b = SimulatedLLM(seed=42), SimulatedLLM(seed=42)
        ra = [a.chat("m", []).usage for _ in range(10)]
        rb = [b.chat("m", []).usage for _ in range(10)]
        self.assertEqual(ra, rb)

    def test_ranges_respected(self):
        llm = SimulatedLLM(seed=3, latency_ms=(10, 20),
                           prompt_tokens=(5, 9), completion_tokens=(1, 2))
        for _ in range(20):
            r = llm.chat(model="m", messages=[])
            self.assertTrue(5 <= r.usage["prompt_tokens"] <= 9)
            self.assertTrue(1 <= r.usage["completion_tokens"] <= 2)
            self.assertTrue(10 <= r.sampled_latency_ms <= 20)

    def test_latency_override(self):
        llm = SimulatedLLM(seed=3)
        r = llm.chat(model="m", messages=[], latency_ms=77.7)
        self.assertEqual(r.sampled_latency_ms, 77.7)


class TestGenerateTraffic(unittest.TestCase):
    def test_deterministic_across_runs(self):
        pricing = Pricing.load()
        s1, s2 = TraceStore(), TraceStore()
        n1 = generate_traffic(s1, pricing, seed=7, days=2, requests_per_day=10)
        n2 = generate_traffic(s2, pricing, seed=7, days=2, requests_per_day=10)
        self.assertEqual(n1, n2)
        self.assertEqual(stream_snapshot(s1), stream_snapshot(s2))

    def test_request_ids_unique(self):
        pricing = Pricing.load()
        store = TraceStore()
        generate_traffic(store, pricing, seed=7, days=1, requests_per_day=20)
        ids = [r["request_id"] for r in store.query(limit=100)]
        self.assertEqual(len(set(ids)), 20)

    def test_different_seeds_differ(self):
        pricing = Pricing.load()
        s1, s2 = TraceStore(), TraceStore()
        generate_traffic(s1, pricing, seed=7, days=1, requests_per_day=10)
        generate_traffic(s2, pricing, seed=8, days=1, requests_per_day=10)
        self.assertNotEqual(stream_snapshot(s1), stream_snapshot(s2))

    def test_counts_and_marks_simulated(self):
        pricing = Pricing.load()
        store = TraceStore()
        n = generate_traffic(store, pricing, seed=7, days=3, requests_per_day=10,
                             tenants=("a", "b"), models=("gpt-4o-mini",))
        self.assertEqual(n, 30)
        self.assertEqual(store.count(), 30)
        self.assertTrue(store.simulated)
        tenants = {r["tenant"] for r in store.query(limit=100)}
        self.assertEqual(tenants, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
