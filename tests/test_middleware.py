"""Middleware: passthrough integrity, token capture, tenant labels, latency."""

import unittest

from sidecar import Pricing, TraceStore, TracedCaller, trace_calls, tenant, current_tenant
from sidecar.simulate import ChatResult


def make_caller(store, **kw):
    kw.setdefault("now", lambda: "2026-09-20T12:00:00")
    return TracedCaller(store, Pricing.load(), **kw)


class FakeClock:
    def __init__(self, ticks):
        self.ticks = list(ticks)

    def __call__(self):
        return self.ticks.pop(0)


class TestPassthrough(unittest.TestCase):
    def test_response_returned_untouched(self):
        store = TraceStore()
        resp = ChatResult("hello", "gpt-4o-mini", 10, 5)
        caller = make_caller(store)
        out = caller.call(lambda model, messages: resp, "gpt-4o-mini",
                          [{"role": "user", "content": "hi"}])
        self.assertIs(out, resp)  # identical object, not a copy

    def test_messages_not_mutated(self):
        store = TraceStore()
        caller = make_caller(store)
        msgs = [{"role": "user", "content": "hi"}]
        snapshot = [dict(m) for m in msgs]
        caller.call(lambda model, messages: ChatResult("r", "gpt-4o-mini", 3, 2),
                    "gpt-4o-mini", msgs)
        self.assertEqual(msgs, snapshot)

    def test_decorator_preserves_name_and_kwargs(self):
        store = TraceStore()

        @trace_calls(store, Pricing.load(), now=lambda: "2026-09-20T12:00:00")
        def chat(model, messages, temperature=0.0):
            self.assertEqual(temperature, 0.7)
            return ChatResult("ok", model, 4, 4)

        out = chat(model="gpt-4o-mini", messages=[], temperature=0.7)
        self.assertEqual(out.text, "ok")
        self.assertEqual(chat.__name__, "chat")


class TestTokenCapture(unittest.TestCase):
    def test_usage_block_recorded(self):
        store = TraceStore()
        caller = make_caller(store)
        caller.call(lambda model, messages: ChatResult("r", "gpt-4o-mini", 100, 50),
                    "gpt-4o-mini", [], tenant="acme")
        ev = store.query()[0]
        self.assertEqual((ev["prompt_tokens"], ev["completion_tokens"],
                          ev["total_tokens"]), (100, 50, 150))
        self.assertEqual(ev["estimated"], 0)
        self.assertAlmostEqual(ev["cost_usd"], (100 / 1000) * 0.15 + (50 / 1000) * 0.6)

    def test_dict_response_with_usage(self):
        store = TraceStore()
        caller = make_caller(store)
        caller.call(lambda model, messages: {"usage": {"prompt_tokens": 7,
                                                       "completion_tokens": 3}},
                    "gpt-4o", [])
        ev = store.query()[0]
        self.assertEqual(ev["total_tokens"], 10)
        self.assertEqual(ev["estimated"], 0)

    def test_missing_usage_falls_back_to_estimate_and_flags(self):
        store = TraceStore()
        caller = make_caller(store)
        caller.call(lambda model, messages: {"text": "abcdefghij"},  # 10 chars -> 3 tokens
                    "gpt-4o", [{"role": "user", "content": "abcd"}])  # 4 chars -> 1 token
        ev = store.query()[0]
        self.assertEqual(ev["prompt_tokens"], 1)
        self.assertEqual(ev["completion_tokens"], 3)
        self.assertEqual(ev["estimated"], 1)

    def test_unknown_model_flags_estimated_cost(self):
        store = TraceStore()
        caller = make_caller(store)
        caller.call(lambda model, messages: ChatResult("r", "future-model-x", 10, 10),
                    "future-model-x", [])
        ev = store.query()[0]
        self.assertEqual(ev["estimated"], 1)


class TestTenantLabels(unittest.TestCase):
    def test_context_manager_labels_calls(self):
        store = TraceStore()
        caller = make_caller(store)
        fn = lambda model, messages: ChatResult("r", "gpt-4o-mini", 1, 1)
        with tenant("acme"):
            caller.call(fn, "gpt-4o-mini", [])
            self.assertEqual(current_tenant(), "acme")
        caller.call(fn, "gpt-4o-mini", [])
        evs = store.query()
        self.assertEqual(evs[1]["tenant"], "acme")  # newest first: [1] is older
        self.assertEqual(evs[0]["tenant"], "")

    def test_explicit_tenant_overrides_context(self):
        store = TraceStore()
        caller = make_caller(store)
        fn = lambda model, messages: ChatResult("r", "gpt-4o-mini", 1, 1)
        with tenant("acme"):
            caller.call(fn, "gpt-4o-mini", [], tenant="globex")
        self.assertEqual(store.query()[0]["tenant"], "globex")

    def test_extra_labels_recorded(self):
        store = TraceStore()
        caller = make_caller(store)
        fn = lambda model, messages: ChatResult("r", "gpt-4o-mini", 1, 1)
        with tenant("acme", feature="summarizer"):
            caller.call(fn, "gpt-4o-mini", [], labels={"env": "prod"})
        import json
        labels = json.loads(store.query()[0]["labels"])
        self.assertEqual(labels, {"feature": "summarizer", "env": "prod"})


class TestLatencyAndWatcher(unittest.TestCase):
    def test_injected_clock_measures_latency(self):
        store = TraceStore()
        caller = make_caller(store, clock=FakeClock([10.0, 10.5]))
        caller.call(lambda model, messages: ChatResult("r", "gpt-4o-mini", 1, 1),
                    "gpt-4o-mini", [])
        self.assertAlmostEqual(store.query()[0]["latency_ms"], 500.0)

    def test_explicit_latency_override(self):
        store = TraceStore()
        caller = make_caller(store)
        caller.call(lambda model, messages: ChatResult("r", "gpt-4o-mini", 1, 1),
                    "gpt-4o-mini", [], latency_ms=123.4)
        self.assertAlmostEqual(store.query()[0]["latency_ms"], 123.4)

    def test_watcher_evaluated_per_event(self):
        store = TraceStore()

        class Spy:
            def __init__(self):
                self.seen = []
            def evaluate(self, event):
                self.seen.append(event["id"])
                return []

        spy = Spy()
        caller = make_caller(store, watcher=spy)
        fn = lambda model, messages: ChatResult("r", "gpt-4o-mini", 1, 1)
        caller.call(fn, "gpt-4o-mini", [])
        caller.call(fn, "gpt-4o-mini", [])
        self.assertEqual(len(spy.seen), 2)


if __name__ == "__main__":
    unittest.main()
