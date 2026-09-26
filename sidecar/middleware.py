"""The sidecar itself: wraps any OpenAI-compatible chat call and traces it.

**middleware** — code that sits between your app and the LLM call, watching passively.
**passthrough** — the wrapper returns the provider's response untouched; it only observes.

Usage::

    store = TraceStore("traces.db")
    pricing = Pricing.load()
    traced_chat = trace_calls(store, pricing)(my_chat_fn)

    with tenant("acme-corp"):          # labels every call in the block
        resp = traced_chat(model="gpt-4o-mini", messages=[...])  # unchanged response

The wrapped function must accept ``model=`` and ``messages=`` keyword args and return
either an object with a ``.usage`` attribute (OpenAI SDK style) or a dict with
``usage`` / ``text`` keys. Token counts fall back to :func:`estimate_tokens` when no
usage block is present (flagged ``estimated=1`` on the event).
"""

import contextvars
import functools
import time
import uuid

from .pricing import estimate_tokens

_tenant_var = contextvars.ContextVar("sidecar_tenant", default="")
_labels_var = contextvars.ContextVar("sidecar_labels", default=None)


class tenant:
    """Context manager: label every traced call inside the block with a tenant.

    **tenant** — plain-English: which customer / team / user the request belongs to,
    so spend can be split per customer later.
    """

    def __init__(self, name, **labels):
        self.name = name
        self.labels = labels
        self._tok_t = None
        self._tok_l = None

    def __enter__(self):
        self._tok_t = _tenant_var.set(self.name or "")
        self._tok_l = _labels_var.set(dict(self.labels) if self.labels else None)
        return self

    def __exit__(self, *exc):
        _tenant_var.reset(self._tok_t)
        _labels_var.reset(self._tok_l)
        return False


def current_tenant():
    return _tenant_var.get()


def _extract_usage(response, messages):
    """Return (prompt_tokens, completion_tokens, tokens_were_estimated)."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is not None:
        if isinstance(usage, dict):
            pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        else:
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
        return int(pt), int(ct), False
    # No usage block: estimate from the raw text (flagged).
    prompt = sum(estimate_tokens(m.get("content", "")) for m in (messages or []))
    if isinstance(response, dict):
        text = response.get("text") or ""
    else:
        text = getattr(response, "text", "") or ""
    completion = estimate_tokens(text) if text else 0
    return prompt, completion, True


class TracedCaller:
    """Wraps chat callables: time them, cost them, record the event, return untouched."""

    def __init__(self, store, pricing, watcher=None, clock=time.monotonic,
                 endpoint="chat.completions", now=None):
        self.store = store
        self.pricing = pricing
        self.watcher = watcher
        self.clock = clock
        self.endpoint = endpoint
        # now: () -> ISO-8601 ts. Injectable so tests never depend on the wall clock.
        self.now = now or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()))

    def call(self, fn, model, messages, tenant=None, labels=None,
             request_id=None, latency_ms=None):
        tenant_name = tenant if tenant is not None else current_tenant()
        extra = dict(_labels_var.get() or {})
        if labels:
            extra.update(labels)
        request_id = request_id or uuid.uuid4().hex

        if latency_ms is None:
            start = self.clock()
            try:
                response = fn(model=model, messages=messages)
            finally:
                latency_ms = (self.clock() - start) * 1000.0
        else:
            # Recorded latency supplied by the caller (e.g. provider-reported
            # server timing, or a deterministic sample in offline sims).
            response = fn(model=model, messages=messages)
        pt, ct, tokens_estimated = _extract_usage(response, messages)
        cost, price_estimated = self.pricing.estimate_cost(model, pt, ct)

        event = self.store.record({
            "ts": self.now(),
            "request_id": request_id,
            "tenant": tenant_name,
            "endpoint": self.endpoint,
            "model": model,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "latency_ms": latency_ms,
            "cost_usd": cost,
            "estimated": tokens_estimated or price_estimated,
            "labels": extra,
        })
        if self.watcher is not None:
            self.watcher.evaluate(event)
        return response  # passthrough: the exact object fn returned


def trace_calls(store, pricing, watcher=None, clock=time.monotonic,
                endpoint="chat.completions", now=None):
    """Decorator factory: ``@trace_calls(store, pricing)`` on a chat function."""
    caller = TracedCaller(store, pricing, watcher=watcher, clock=clock,
                          endpoint=endpoint, now=now)

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(model, messages, tenant=None, labels=None, **kwargs):
            # kwargs (temperature, etc.) pass through to fn untouched.
            def _fn(model, messages):
                return fn(model=model, messages=messages, **kwargs)
            return caller.call(_fn, model, messages, tenant=tenant, labels=labels)
        return wrapper
    return deco
