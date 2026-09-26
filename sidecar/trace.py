"""Append-only trace store: every traced LLM request lands here as one row.

**trace** — a per-request record: model, tokens, latency, cost, tenant labels.
**append-only** — rows are written once and never updated; history is the product.

SQLite, thread-safe (one lock + check_same_thread=False). Pass ``path=None`` for an
in-memory store (tests, demos); pass a file path for the server/CLI. The runtime DB
is created on first use and is NOT committed to git (see .gitignore).
"""

import json
import sqlite3
import threading
import uuid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT    NOT NULL,   -- ISO-8601 UTC
    request_id       TEXT    NOT NULL,
    tenant           TEXT    NOT NULL DEFAULT '',
    endpoint         TEXT    NOT NULL DEFAULT 'chat.completions',
    model            TEXT    NOT NULL,
    prompt_tokens    INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens     INTEGER NOT NULL,
    latency_ms       REAL    NOT NULL,
    cost_usd         REAL    NOT NULL,
    estimated        INTEGER NOT NULL DEFAULT 0,  -- 1 = tokens or price were estimated
    labels           TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_tenant ON events(tenant);
CREATE INDEX IF NOT EXISTS idx_events_model  ON events(model);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    level         TEXT NOT NULL,   -- 'soft' | 'hard'
    scope         TEXT NOT NULL,   -- 'global' | 'tenant:<name>'
    spend_usd     REAL NOT NULL,
    threshold_usd REAL NOT NULL,
    message       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_EVENT_FIELDS = (
    "id", "ts", "request_id", "tenant", "endpoint", "model",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "latency_ms", "cost_usd", "estimated", "labels",
)


class TraceStore:
    def __init__(self, path=None):
        self.path = path
        self._lock = threading.Lock()
        # check_same_thread=False + our own lock: safe for threaded servers.
        self._conn = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- meta -------------------------------------------------------------
    def set_meta(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def mark_simulated(self):
        """Flag the store as holding synthetic demo traffic (drives UI banners)."""
        self.set_meta("simulated", "1")

    @property
    def simulated(self):
        return self.get_meta("simulated") == "1"

    # -- events -----------------------------------------------------------
    def record(self, event):
        """Append one event dict; returns the stored row as a dict (with id)."""
        labels = event.get("labels") or {}
        row = {
            "ts": event["ts"],
            "request_id": event.get("request_id") or uuid.uuid4().hex,
            "tenant": event.get("tenant") or "",
            "endpoint": event.get("endpoint") or "chat.completions",
            "model": event["model"],
            "prompt_tokens": int(event["prompt_tokens"]),
            "completion_tokens": int(event["completion_tokens"]),
            "total_tokens": int(event.get("total_tokens",
                                         event["prompt_tokens"] + event["completion_tokens"])),
            "latency_ms": float(event["latency_ms"]),
            "cost_usd": float(event["cost_usd"]),
            "estimated": int(bool(event.get("estimated"))),
            "labels": json.dumps(labels),
        }
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(ts, request_id, tenant, endpoint, model,"
                " prompt_tokens, completion_tokens, total_tokens, latency_ms,"
                " cost_usd, estimated, labels) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(row[k] for k in _EVENT_FIELDS[1:]),
            )
            self._conn.commit()
            row["id"] = cur.lastrowid
        return row

    def query(self, tenant=None, model=None, day=None, limit=500):
        """Newest-first event listing with optional filters. day = 'YYYY-MM-DD'."""
        where, params = [], []
        if tenant:
            where.append("tenant = ?"); params.append(tenant)
        if model:
            where.append("model = ?"); params.append(model)
        if day:
            where.append("date(ts) = ?"); params.append(day)
        sql = "SELECT * FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count(self):
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]

    def spend(self, tenant=None, day=None):
        """Total USD for a scope. tenant=None -> global. day='YYYY-MM-DD' or None=all."""
        where, params = [], []
        if tenant is not None:
            where.append("tenant = ?"); params.append(tenant)
        if day:
            where.append("date(ts) = ?"); params.append(day)
        sql = "SELECT COALESCE(SUM(cost_usd), 0.0) AS s FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            return self._conn.execute(sql, params).fetchone()["s"]

    def totals(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS requests, COALESCE(SUM(cost_usd),0) AS cost,"
                " COALESCE(SUM(total_tokens),0) AS tokens,"
                " COALESCE(AVG(latency_ms),0) AS avg_latency FROM events"
            ).fetchone()
        return dict(row)

    def close(self):
        with self._lock:
            self._conn.close()
