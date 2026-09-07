"""
database.py
SQLite persistence layer for Network Monitor.

All monitoring data (devices, ping history, offline/online events,
and settings) is stored in a local SQLite database so that closing
and reopening the application never loses data.

The database file lives in the user's APPDATA folder (on Windows) so
it works no matter where the .exe is launched from, and survives
application updates.
"""

import os
import sys
import sqlite3
import threading
import time
from datetime import datetime


def get_data_dir():
    """Return a writable per-user folder for storing app data."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        # Fallback for non-Windows dev/testing environments
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    data_dir = os.path.join(base, "NetworkMonitor")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


DB_PATH = os.path.join(get_data_dir(), "networkmonitor.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ip TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'Other',
    group_name TEXT NOT NULL DEFAULT 'Other',
    description TEXT DEFAULT '',
    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_latency REAL,
    avg_latency REAL,
    packet_loss REAL NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    went_offline_at TEXT,
    map_x INTEGER,
    map_y INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ping_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    success INTEGER NOT NULL,
    latency_ms REAL,
    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER,
    device_name TEXT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_ping_history_device ON ping_history(device_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp);
"""

DEFAULT_SETTINGS = {
    "ping_interval_seconds": "5",
    "ping_timeout_ms": "1000",
    "failed_checks_before_offline": "3",
    "theme": "dark",
    "notifications_enabled": "1",
    "history_retention_days": "30",
}


class Database:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            for k, v in DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
                )
            self._conn.commit()

    # ---------- generic helpers ----------

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()

    # ---------- settings ----------

    def get_setting(self, key, default=None):
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def all_settings(self):
        rows = self.query("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}

    # ---------- devices ----------

    def add_device(self, name, ip, dtype, group_name, description="", x=None, y=None):
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.execute(
            "INSERT INTO devices (name, ip, type, group_name, description, "
            "status, created_at, map_x, map_y) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, ip, dtype, group_name, description, "UNKNOWN", now, x, y),
        )
        return cur.lastrowid

    def update_device(self, device_id, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields.keys())
        params = list(fields.values()) + [device_id]
        self.execute(f"UPDATE devices SET {cols} WHERE id=?", params)

    def delete_device(self, device_id):
        self.execute("DELETE FROM devices WHERE id=?", (device_id,))
        self.execute("DELETE FROM ping_history WHERE device_id=?", (device_id,))

    def get_device(self, device_id):
        return self.query_one("SELECT * FROM devices WHERE id=?", (device_id,))

    def get_devices(self):
        return self.query("SELECT * FROM devices ORDER BY name COLLATE NOCASE")

    # ---------- ping history ----------

    def add_ping_result(self, device_id, success, latency_ms):
        now = datetime.now().isoformat(timespec="seconds")
        self.execute(
            "INSERT INTO ping_history (device_id, timestamp, success, latency_ms) "
            "VALUES (?,?,?,?)",
            (device_id, now, 1 if success else 0, latency_ms),
        )

    def get_ping_history(self, device_id, limit=200):
        return self.query(
            "SELECT * FROM ping_history WHERE device_id=? "
            "ORDER BY id DESC LIMIT ?",
            (device_id, limit),
        )

    def compute_avg_latency(self, device_id, sample=20):
        rows = self.query(
            "SELECT latency_ms FROM ping_history WHERE device_id=? AND success=1 "
            "ORDER BY id DESC LIMIT ?",
            (device_id, sample),
        )
        vals = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
        return sum(vals) / len(vals) if vals else None

    def compute_packet_loss(self, device_id, sample=20):
        rows = self.query(
            "SELECT success FROM ping_history WHERE device_id=? "
            "ORDER BY id DESC LIMIT ?",
            (device_id, sample),
        )
        if not rows:
            return 0.0
        fails = sum(1 for r in rows if r["success"] == 0)
        return round((fails / len(rows)) * 100, 1)

    # ---------- events ----------

    def add_event(self, device_id, device_name, event_type, message):
        now = datetime.now().isoformat(timespec="seconds")
        self.execute(
            "INSERT INTO events (device_id, device_name, event_type, message, timestamp) "
            "VALUES (?,?,?,?,?)",
            (device_id, device_name, event_type, message, now),
        )

    def get_events(self, limit=300):
        return self.query(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        )

    def get_events_for_device(self, device_id, limit=100):
        return self.query(
            "SELECT * FROM events WHERE device_id=? ORDER BY id DESC LIMIT ?",
            (device_id, limit),
        )

    def close(self):
        with self._lock:
            self._conn.close()
