"""
Daedalus Storage — portable key-value persistence layer.

Replaces Replit DB with a JSON-file backend so the standalone framework
works in any environment. Drop-in compatible with the Replit DB pattern.

Usage:
    from daedalus.storage import kv_get, kv_set, kv_del, kv_keys

The storage file defaults to  ~/.daedalus/store.json
Override with  DAEDALUS_STORE_PATH  env var.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_STORE_PATH = Path(
    os.environ.get("DAEDALUS_STORE_PATH", str(Path.home() / ".daedalus" / "store.json"))
)
_LOCK = threading.Lock()


def _load() -> dict:
    try:
        if _STORE_PATH.exists():
            return json.loads(_STORE_PATH.read_text("utf-8"))
    except Exception as exc:
        log.warning("storage: load failed: %s", exc)
    return {}


def _save(data: dict) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except Exception as exc:
        log.warning("storage: save failed: %s", exc)


def kv_get(key: str, default: Any = None) -> Any:
    with _LOCK:
        return _load().get(key, default)


def kv_set(key: str, value: Any) -> None:
    with _LOCK:
        data = _load()
        data[key] = value
        _save(data)


def kv_del(key: str) -> None:
    with _LOCK:
        data = _load()
        data.pop(key, None)
        _save(data)


def kv_keys(prefix: str = "") -> list[str]:
    with _LOCK:
        return [k for k in _load() if k.startswith(prefix)]


# ── Compat shim mimicking replit.db dict-like interface ──────────────────────

class _DB:
    def get(self, key: str, default: Any = None) -> Any:
        return kv_get(key, default)

    def __getitem__(self, key: str) -> Any:
        v = kv_get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __setitem__(self, key: str, value: Any) -> None:
        kv_set(key, value)

    def __delitem__(self, key: str) -> None:
        kv_del(key)

    def __contains__(self, key: object) -> bool:
        return kv_get(str(key)) is not None

    def prefix(self, prefix: str) -> list[str]:
        return kv_keys(prefix)


db = _DB()
