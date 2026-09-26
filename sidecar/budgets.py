"""Budget enforcement: soft/hard spend thresholds evaluated on the live trace stream.

**soft budget** — a warning line: spend crossed it, someone should look.
**hard budget** — a stop line: spend crossed it, page someone / gate traffic.
**edge-triggered** — each threshold fires exactly once per scope per day, on the
crossing event; it does not re-fire for every later request.

Thresholds are evaluated against the trace store itself (fresh SUMs), never against
cached aggregates — so an alert always reflects what has actually been recorded.
"""

import time


class BudgetConfig:
    """Parsed from a dict/JSON like::

        {"global": {"soft": 50.0, "hard": 100.0},
         "tenants": {"acme": {"soft": 10.0, "hard": 20.0}}}
    """

    def __init__(self, config=None):
        config = config or {}
        self.global_soft = (config.get("global") or {}).get("soft")
        self.global_hard = (config.get("global") or {}).get("hard")
        self.tenants = {}
        for name, lim in (config.get("tenants") or {}).items():
            self.tenants[name] = {"soft": lim.get("soft"), "hard": lim.get("hard")}

    @classmethod
    def from_json(cls, path):
        import json
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def limits_for(self, tenant):
        """Return (global_soft, global_hard, tenant_soft, tenant_hard)."""
        t = self.tenants.get(tenant or "", {})
        return self.global_soft, self.global_hard, t.get("soft"), t.get("hard")


class BudgetWatcher:
    """Evaluates every recorded event against thresholds; emits alerts on crossing."""

    def __init__(self, store, config, alert_log=None, now=None, sinks=()):
        self.store = store
        self.config = config if isinstance(config, BudgetConfig) else BudgetConfig(config)
        self.alert_log = alert_log
        self.now = now or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()))
        self.sinks = list(sinks)  # pluggable: PrintSink / WebhookSink / FanoutSink
        self._fired = set()  # (scope, day, level) already alerted

    def reset(self):
        """Clear edge-trigger state (e.g. new billing period in tests/demos)."""
        self._fired.clear()

    def evaluate(self, event):
        """Check one freshly-recorded event. Returns the list of alerts fired."""
        day = (event.get("ts") or "")[:10]
        tenant = event.get("tenant") or ""
        g_soft, g_hard, t_soft, t_hard = self.config.limits_for(tenant)
        fired = []
        # (scope label, spend query args, soft, hard)
        scopes = [
            ("global", None, g_soft, g_hard),
            (f"tenant:{tenant}", tenant, t_soft, t_hard),
        ]
        for scope, tenant_q, soft, hard in scopes:
            if soft is None and hard is None:
                continue
            spend = self.store.spend(tenant=tenant_q, day=day or None)
            for level, threshold in (("soft", soft), ("hard", hard)):
                if threshold is None:
                    continue
                key = (scope, day, level)
                if spend >= threshold and key not in self._fired:
                    self._fired.add(key)
                    alert = {
                        "ts": self.now(),
                        "level": level,
                        "scope": scope,
                        "spend_usd": round(spend, 6),
                        "threshold_usd": threshold,
                        "message": (
                            f"{level.upper()} budget crossed: {scope} spent "
                            f"${spend:,.4f} vs ${threshold:,.4f} limit ({day or 'all time'})"
                        ),
                    }
                    if self.alert_log is not None:
                        self.alert_log.record(alert)
                    for sink in self.sinks:
                        try:
                            sink.send(alert)
                        except Exception:
                            pass  # a dead webhook must never break tracing
                    fired.append(alert)
        return fired

    def status(self, day=None):
        """Current spend vs thresholds for dashboard/CLI. No alerts fired."""
        out = {"global": self._scope_status(None, day),
               "tenants": {t: self._scope_status(t, day) for t in self.config.tenants}}
        return out

    def _scope_status(self, tenant, day):
        g_soft, g_hard, t_soft, t_hard = self.config.limits_for(tenant or "")
        soft, hard = (g_soft, g_hard) if tenant is None else (t_soft, t_hard)
        spend = self.store.spend(tenant=tenant, day=day)
        state = "ok"
        if hard is not None and spend >= hard:
            state = "hard"
        elif soft is not None and spend >= soft:
            state = "soft"
        return {"spend_usd": round(spend, 6), "soft": soft, "hard": hard, "state": state}
