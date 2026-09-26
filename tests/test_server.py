"""HTTP server: routes, JSON shapes, CSV export, dashboard."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from sidecar import Pricing, TraceStore, generate_traffic
from sidecar.server import create_app


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")


class ServerTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = TraceStore()
        generate_traffic(cls.store, Pricing.load(), seed=7, days=2,
                         requests_per_day=10)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                        create_app(cls.store))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=5)


class TestRoutes(ServerTestBase):
    def test_dashboard_200_and_simulated_banner(self):
        status, ctype, body = get(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("SIMULATED DATA", body)
        self.assertIn("<svg", body)  # cost-over-time chart

    def test_api_summary_shape(self):
        status, ctype, body = get(self.port, "/api/summary")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        self.assertIn("totals", data)
        self.assertIn("by_tenant", data)
        self.assertEqual(data["totals"]["requests"], 20)

    def test_api_events_filter(self):
        _, _, body = get(self.port, "/api/events?tenant=acme&limit=100")
        data = json.loads(body)
        self.assertTrue(data)
        self.assertTrue(all(e["tenant"] == "acme" for e in data))

    def test_api_alerts_empty_list(self):
        _, _, body = get(self.port, "/api/alerts")
        self.assertEqual(json.loads(body), [])

    def test_api_budgets_no_watcher(self):
        _, _, body = get(self.port, "/api/budgets")
        self.assertIn("error", json.loads(body))

    def test_export_csv(self):
        status, ctype, body = get(self.port, "/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", ctype)
        lines = body.strip().splitlines()
        self.assertEqual(len(lines), 21)  # header + 20 events
        self.assertTrue(lines[0].startswith("id,ts,request_id"))

    def test_404(self):
        try:
            get(self.port, "/nope")
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
