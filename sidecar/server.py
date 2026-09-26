"""Zero-dependency HTTP server: JSON API + inline-SVG dashboard.

Endpoints:
    GET /              dashboard (cost-over-time chart, per-tenant table, alerts)
    GET /api/events    ?tenant=&model=&day=YYYY-MM-DD&limit=
    GET /api/summary   ?day=
    GET /api/alerts    ?limit=
    GET /api/budgets   ?day=      (needs a watcher passed to create_app)
    GET /export.csv    ?tenant=&model=&day=
"""

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import aggregator


def _svg_bars(by_day, width=640, height=180):
    """Inline SVG bar chart of daily cost. No JS, no dependencies."""
    if not by_day:
        return '<p class="muted">No data yet.</p>'
    rows = sorted(by_day, key=lambda r: r["key"])
    costs = [r["cost_usd"] for r in rows]
    peak = max(costs) or 1.0
    n = len(rows)
    bw = width / n
    bars = []
    for i, r in enumerate(rows):
        h = max(2.0, (r["cost_usd"] / peak) * (height - 40))
        x = i * bw + bw * 0.15
        y = height - 20 - h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.7:.1f}" height="{h:.1f}"'
            f' rx="3"><title>{html.escape(str(r["key"]))}: ${r["cost_usd"]:.4f} '
            f'({r["requests"]} reqs)</title></rect>'
            f'<text x="{x + bw * 0.35:.1f}" y="{height - 6}" font-size="9"'
            f' text-anchor="middle" fill="#666">{html.escape(str(r["key"])[5:])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img"'
        f' aria-label="Daily LLM cost bar chart">'
        f'<text x="4" y="14" font-size="11" fill="#666">Daily cost (USD) — peak '
        f'${peak:.4f}</text>' + "".join(bars) + "</svg>"
    )


def _dashboard(ctx):
    store, watcher = ctx["store"], ctx.get("watcher")
    s = aggregator.summary(store)
    t = s["totals"]
    banner = ""
    if store.simulated:
        banner = ('<div class="banner">SIMULATED DATA — this dashboard is showing '
                  'synthetic demo traffic, not real spend.</div>')
    rows_tenant = "".join(
        f"<tr><td>{html.escape(str(r['key']) or '(unlabeled)')}</td>"
        f"<td>{r['requests']}</td><td>{r['total_tokens']:,}</td>"
        f"<td>${r['cost_usd']:.4f}</td><td>{r['avg_latency_ms']:.0f} ms</td></tr>"
        for r in s["by_tenant"]
    )
    rows_model = "".join(
        f"<tr><td>{html.escape(str(r['key']))}</td><td>{r['requests']}</td>"
        f"<td>${r['cost_usd']:.4f}</td></tr>"
        for r in s["by_model"]
    )
    alerts = ctx.get("alert_log").list(limit=50) if ctx.get("alert_log") else []
    rows_alerts = "".join(
        f"<tr class='{html.escape(a['level'])}'><td>{html.escape(a['ts'])}</td>"
        f"<td>{html.escape(a['level']).upper()}</td>"
        f"<td>{html.escape(a['scope'])}</td>"
        f"<td>{html.escape(a['message'])}</td></tr>"
        for a in alerts
    ) or '<tr><td colspan="4" class="muted">No alerts fired.</td></tr>'
    budget_line = ""
    if watcher is not None:
        st = watcher.status()
        g = st["global"]
        budget_line = (
            f"<p>Global spend: <b>${g['spend_usd']:.4f}</b>"
            f" (soft ${g['soft']}, hard ${g['hard']}) — state: <b>{g['state']}</b></p>"
        )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>LLM Cost Sidecar</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}}
.banner{{background:#fff3cd;border:1px solid #e0c36a;padding:10px;border-radius:6px;margin-bottom:16px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{border:1px solid #ddd;border-radius:8px;padding:12px 16px;min-width:150px}}
.card b{{font-size:1.4em}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
th,td{{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:14px}}
th{{background:#f5f5f5}}
tr.soft td{{background:#fff8e1}}tr.hard td{{background:#fdecea}}
.muted{{color:#888}}rect{{fill:#4a7dff}}
h2{{margin-top:28px}}
</style></head><body>
<h1>LLM Cost Sidecar</h1>
{banner}
<div class="cards">
<div class="card"><b>${t['cost_usd']:.4f}</b><br>total cost</div>
<div class="card"><b>{t['requests']:,}</b><br>requests</div>
<div class="card"><b>{t['tokens']:,}</b><br>tokens</div>
<div class="card"><b>{t['avg_latency_ms']:.0f} ms</b><br>avg latency</div>
</div>
{budget_line}
<h2>Cost over time</h2>
{_svg_bars(s["by_day"])}
<h2>By tenant</h2>
<table><tr><th>Tenant</th><th>Requests</th><th>Tokens</th><th>Cost</th><th>Avg latency</th></tr>
{rows_tenant}</table>
<h2>By model</h2>
<table><tr><th>Model</th><th>Requests</th><th>Cost</th></tr>{rows_model}</table>
<h2>Alerts</h2>
<table><tr><th>Time</th><th>Level</th><th>Scope</th><th>Message</th></tr>
{rows_alerts}</table>
<p><a href="/export.csv">Download events CSV</a> · API: <a href="/api/summary">/api/summary</a>
· <a href="/api/events">/api/events</a> · <a href="/api/alerts">/api/alerts</a></p>
<p class="muted">Pricing table: bundled SAMPLE rates — verify against provider pages
before using for real billing.</p>
</body></html>"""


def create_app(store, pricing=None, watcher=None, alert_log=None):
    ctx = {"store": store, "pricing": pricing, "watcher": watcher,
           "alert_log": alert_log}

    class Handler(BaseHTTPRequestHandler):
        server_version = "LLMCostSidecar/1.0"

        def _send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str), "application/json")

        def do_GET(self):
            parsed = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            path = parsed.path
            try:
                if path == "/":
                    self._send(200, _dashboard(ctx), "text/html; charset=utf-8")
                elif path == "/api/events":
                    self._json(store.query(tenant=q.get("tenant"), model=q.get("model"),
                                           day=q.get("day"), limit=int(q.get("limit", 500))))
                elif path == "/api/summary":
                    self._json(aggregator.summary(store, day=q.get("day")))
                elif path == "/api/alerts":
                    self._json(ctx["alert_log"].list(limit=int(q.get("limit", 200)))
                               if ctx["alert_log"] else [])
                elif path == "/api/budgets":
                    self._json(ctx["watcher"].status(day=q.get("day"))
                               if ctx["watcher"] else {"error": "no watcher configured"})
                elif path == "/export.csv":
                    events = store.query(tenant=q.get("tenant"), model=q.get("model"),
                                         day=q.get("day"), limit=int(q.get("limit", 100000)))
                    self._send(200, aggregator.events_to_csv(events), "text/csv")
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:  # never leak a traceback to the dashboard
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def log_message(self, *args):
            pass  # quiet by default; run with --verbose some day

    return Handler


def serve(store, host="127.0.0.1", port=8000, **kwargs):
    """Blocking serve. Binds localhost only by default."""
    handler = create_app(store, **kwargs)
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"LLM Cost Sidecar dashboard: http://{host}:{httpd.server_port}/")
        httpd.serve_forever()
