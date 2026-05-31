"""Pluggable storage backend for Daedalus-Agent.

Replaces Replit DB (used in ADN) with portable alternatives.

Backends:
  JsonStorage(path)    — single JSON file, good for local dev
  SqliteStorage(path)  — SQLite, good for production standalone use
  MemoryStorage()      — in-process only, good for testing

All backends implement the Storage protocol:
  get(key) -> Any | None
  set(key, value) -> None
  delete(key) -> None
  keys(prefix) -> list[str]
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol


class Storage(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self, prefix: str = "") -> list[str]: ...


class MemoryStorage:
    """Thread-safe in-memory storage. Data lost on restart."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            return [k for k in self._data if k.startswith(prefix)]


class JsonStorage:
    """Persistent JSON file storage. Good for single-process local use."""

    def __init__(self, path: str | Path = ".daedalus_storage.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        if not self._path.exists():
            self._path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text("utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._load().get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._load()
            data[key] = value
            self._save(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._load()
            data.pop(key, None)
            self._save(data)

    def keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            return [k for k in self._load() if k.startswith(prefix)]


class SqliteStorage:
    """SQLite-backed storage. Good for concurrent / production use."""

    def __init__(self, path: str | Path = ".daedalus.db") -> None:
        self._path = str(path)
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self._path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

    def get(self, key: str) -> Any | None:
        row = self._conn().execute(
            "SELECT value FROM kv WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set(self, key: str, value: Any) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self._conn().commit()

    def delete(self, key: str) -> None:
        self._conn().execute("DELETE FROM kv WHERE key=?", (key,))
        self._conn().commit()

    def keys(self, prefix: str = "") -> list[str]:
        rows = self._conn().execute(
            "SELECT key FROM kv WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        return [r["key"] for r in rows]


def default_storage(path: str = ".daedalus.db") -> Storage:
    """Return the recommended storage backend (SQLite)."""
    return SqliteStorage(path)
