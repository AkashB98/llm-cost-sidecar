"""Aggregation engine: rollups, top-cost queries, CSV export.

**rollup** — plain-English: adding up many per-request rows into per-model /
per-tenant / per-day totals, so you see where the money went.
"""

import csv
import io


def rollup(store, group_by="model", day=None):
    """Aggregate events. group_by: 'model' | 'tenant' | 'day' | 'endpoint'."""
    col = {"model": "model", "tenant": "tenant", "day": "date(ts)",
           "endpoint": "endpoint"}.get(group_by)
    if col is None:
        raise ValueError(f"unknown group_by: {group_by!r}")
    where, params = "", []
    if day:
        where, params = "WHERE date(ts) = ?", [day]
    sql = (f"SELECT {col} AS key, COUNT(*) AS requests,"
           " SUM(prompt_tokens) AS prompt_tokens,"
           " SUM(completion_tokens) AS completion_tokens,"
           " SUM(total_tokens) AS total_tokens,"
           " SUM(cost_usd) AS cost_usd,"
           " AVG(latency_ms) AS avg_latency_ms"
           f" FROM events {where} GROUP BY {col} ORDER BY cost_usd DESC")
    with store._lock:
        rows = store._conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["cost_usd"] = round(d["cost_usd"] or 0.0, 6)
        d["avg_latency_ms"] = round(d["avg_latency_ms"] or 0.0, 2)
        out.append(d)
    return out


def top_cost(store, by="tenant", n=5, day=None):
    """Top-N spenders by tenant or model. Returns list of {key, cost_usd, requests}."""
    rows = rollup(store, group_by=by, day=day)[: max(0, int(n))]
    return [{"key": r["key"], "cost_usd": r["cost_usd"], "requests": r["requests"]}
            for r in rows]


def summary(store, day=None):
    """One dict: totals + per-model + per-tenant + per-day rollups."""
    totals = store.totals()
    totals["cost_usd"] = round(totals.pop("cost") or 0.0, 6)
    totals["avg_latency_ms"] = round(totals.pop("avg_latency") or 0.0, 2)
    return {
        "totals": totals,
        "by_model": rollup(store, "model", day=day),
        "by_tenant": rollup(store, "tenant", day=day),
        "by_day": rollup(store, "day", day=day),
        "top_tenants": top_cost(store, "tenant", 5, day=day),
        "top_models": top_cost(store, "model", 5, day=day),
    }


def events_to_csv(events):
    """Export event dicts to CSV text (for /export.csv and the CLI)."""
    buf = io.StringIO()
    fields = ["id", "ts", "request_id", "tenant", "endpoint", "model",
              "prompt_tokens", "completion_tokens", "total_tokens",
              "latency_ms", "cost_usd", "estimated"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for e in events:
        w.writerow(e)
    return buf.getvalue()
