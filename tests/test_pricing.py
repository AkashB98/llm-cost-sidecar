"""Pricing math + honesty guarantees."""

import json
import os
import unittest

from sidecar.pricing import Pricing, estimate_tokens

TABLE = os.path.join(os.path.dirname(__file__), "..", "sidecar", "pricing.json")


class TestPricingMath(unittest.TestCase):
    def setUp(self):
        self.p = Pricing.load()

    def test_gpt4o_mini_cost(self):
        cost, est = self.p.estimate_cost("gpt-4o-mini", 1000, 500)
        self.assertAlmostEqual(cost, 0.15 + 0.30)  # 0.15/1K in, 0.60/1K out
        self.assertFalse(est)

    def test_gpt4o_cost(self):
        cost, est = self.p.estimate_cost("gpt-4o", 2000, 1000)
        self.assertAlmostEqual(cost, 5.0 + 10.0)
        self.assertFalse(est)

    def test_claude_sonnet_cost(self):
        cost, est = self.p.estimate_cost("claude-sonnet-4", 1000, 1000)
        self.assertAlmostEqual(cost, 3.0 + 15.0)
        self.assertFalse(est)

    def test_unknown_model_falls_back_and_flags_estimated(self):
        cost, est = self.p.estimate_cost("mystery-model-3000", 1000, 1000)
        self.assertAlmostEqual(cost, 3.0 + 12.0)  # fallback rate
        self.assertTrue(est)

    def test_zero_tokens_zero_cost(self):
        cost, est = self.p.estimate_cost("gpt-4o", 0, 0)
        self.assertEqual(cost, 0.0)
        self.assertFalse(est)

    def test_all_bundled_rates_positive(self):
        for name in self.p.model_names():
            cost, _ = self.p.estimate_cost(name, 1000, 1000)
            self.assertGreater(cost, 0, name)

    def test_pricing_json_carries_sample_disclaimer(self):
        with open(TABLE, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertIn("SAMPLE", raw)
        data = json.loads(raw)
        self.assertIn("verify", data["_note"].lower())


class TestEstimateTokens(unittest.TestCase):
    def test_empty_is_one(self):
        self.assertEqual(estimate_tokens(""), 1)
        self.assertEqual(estimate_tokens(None), 1)

    def test_ceil_div_4(self):
        self.assertEqual(estimate_tokens("abcd"), 1)     # 4 chars
        self.assertEqual(estimate_tokens("abcde"), 2)   # 5 chars
        self.assertEqual(estimate_tokens("x" * 100), 25)


if __name__ == "__main__":
    unittest.main()
