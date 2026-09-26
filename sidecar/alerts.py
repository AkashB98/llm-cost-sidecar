"""Alert delivery: every alert is logged offline; webhooks are pluggable and OFF by default.

**alert log** — an append-only record of every budget alert that fired.
**webhook** — plain-English: an HTTP POST the sidecar can fire to PagerDuty/Slack/etc.
    Registered explicitly via ``register_sink`` — never called unless you opt in.
"""

import json
import urllib.request


class AlertLog:
    """Persist alerts to the trace store's alerts table (offline by default)."""

    def __init__(self, store):
        self.store = store

    def record(self, alert):
        with self.store._lock:
            self.store._conn.execute(
                "INSERT INTO alerts(ts, level, scope, spend_usd, threshold_usd, message)"
                " VALUES(?,?,?,?,?,?)",
                (alert["ts"], alert["level"], alert["scope"],
                 alert["spend_usd"], alert["threshold_usd"], alert["message"]),
            )
            self.store._conn.commit()
        return alert

    def list(self, limit=200):
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]


class PrintSink:
    """Demo sink: prints alerts to stdout. Handy for `demo.py`."""

    def send(self, alert):
        print(f"[ALERT] {alert['message']}")
        return True


class WebhookSink:
    """Pluggable HTTP sink: POSTs the alert JSON to a URL.

    NOT called by default — wire it up explicitly::

        watcher_sends_alerts_to = WebhookSink("https://hooks.example.com/llm-alerts")
    """

    def __init__(self, url, timeout=5):
        self.url = url
        self.timeout = timeout

    def send(self, alert):
        body = json.dumps(alert).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "llm-cost-sidecar/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return 200 <= resp.status < 300


class FanoutSink:
    """Send to several sinks; one failing sink never blocks the others."""

    def __init__(self, *sinks):
        self.sinks = list(sinks)

    def send(self, alert):
        results = []
        for sink in self.sinks:
            try:
                results.append(bool(sink.send(alert)))
            except Exception:
                results.append(False)
        return results
