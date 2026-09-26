"""Deterministic simulated LLM client: zero network, zero API keys.

**seeded deterministic** — same seed always produces the same token counts and
latencies, so demos, evals, and tests are reproducible.

``SimulatedLLM.chat(model=..., messages=...)`` returns a ``ChatResult`` shaped like an
OpenAI SDK response (``.text``, ``.usage``), so it drops straight into the traced
middleware — and later into a real OpenAI-compatible client with the same call shape.
"""

import random


class ChatResult:
    """Minimal OpenAI-SDK-shaped response: .text, .usage, .model."""

    def __init__(self, text, model, prompt_tokens, completion_tokens):
        self.text = text
        self.model = model
        self.usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def __repr__(self):
        return f"ChatResult(model={self.model!r}, usage={self.usage!r})"


class SimulatedLLM:
    def __init__(self, seed=7, latency_ms=(40, 400),
                 prompt_tokens=(80, 1500), completion_tokens=(30, 600),
                 sleep=False):
        """sleep=True actually sleeps the sampled latency (demo realism); default
        False so sims/evals run instantly — latency is still *recorded* as sampled."""
        self.rng = random.Random(seed)
        self.seed = seed
        self.latency_ms = latency_ms
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.sleep = sleep
        self.calls = 0

    def chat(self, model, messages, latency_ms=None, **kwargs):
        prompt_n = self.rng.randint(*self.prompt_tokens)
        completion_n = self.rng.randint(*self.completion_tokens)
        latency = self.sample_latency() if latency_ms is None else latency_ms
        self.calls += 1
        if self.sleep:
            import time
            time.sleep(min(latency, 1500) / 1000.0)
        result = ChatResult(
            text=f"[simulated reply #{self.calls} for model {model}]",
            model=model,
            prompt_tokens=prompt_n,
            completion_tokens=completion_n,
        )
        result.sampled_latency_ms = latency
        return result

    def sample_latency(self):
        """Draw one deterministic latency sample (ms)."""
        return self.rng.uniform(*self.latency_ms)


def generate_traffic(store, pricing, seed=7, days=7, tenants=("acme", "globex", "initech"),
                     models=("gpt-4o-mini", "gpt-4o", "claude-sonnet-4"),
                     requests_per_day=40, base_date="2026-09-19", watcher=None):
    """Deterministic synthetic traffic: the offline demo/eval workload.

    Timestamps march forward from ``base_date`` 00:00 UTC across ``days`` days —
    fixed dates, never "now", so runs are reproducible. Returns event count.
    """
    import datetime
    from .middleware import TracedCaller

    rng = random.Random(seed)
    llm = SimulatedLLM(seed=seed)
    caller = TracedCaller(store, pricing, watcher=watcher)
    base = datetime.datetime.strptime(base_date, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc)
    n = 0
    for day in range(days):
        for _ in range(requests_per_day):
            tenant = rng.choice(tenants)
            model = rng.choice(models)
            jitter = rng.uniform(0, 86399)
            ts = (base + datetime.timedelta(days=day, seconds=jitter)).strftime("%Y-%m-%dT%H:%M:%S")
            caller.now = (lambda ts=ts: ts)
            lat = llm.sample_latency()

            def _fn(model, messages, _lat=lat):
                return llm.chat(model=model, messages=messages, latency_ms=_lat)

            caller.call(_fn, model, [{"role": "user", "content": "simulated prompt"}],
                        tenant=tenant, latency_ms=lat)
            n += 1
    store.mark_simulated()
    return n
