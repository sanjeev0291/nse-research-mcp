"""Tiny in-memory TTL cache and a disk cache for slow-changing text files (NSE CSVs)."""
from __future__ import annotations

import functools
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_store: dict = {}

CACHE_DIR = Path(os.environ.get("NSE_RESEARCH_CACHE", Path.home() / ".cache" / "nse-research"))


def ttl_cache(seconds: float):
    """Memoize a function's return value for `seconds`. Arguments must be hashable."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (fn.__module__, fn.__qualname__, args, tuple(sorted(kwargs.items())))
            now = time.time()
            with _lock:
                hit = _store.get(key)
                if hit and hit[0] > now:
                    return hit[1]
            value = fn(*args, **kwargs)
            with _lock:
                _store[key] = (now + seconds, value)
            return value

        wrapper.cache_clear = lambda: _store.clear()  # type: ignore[attr-defined]
        return wrapper

    return deco


def disk_text(name: str, fetch, max_age: float) -> str:
    """Return cached text from CACHE_DIR/name if younger than max_age, else call fetch() and store it.
    If fetch fails and a stale copy exists, the stale copy is returned."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return path.read_text()
    try:
        text = fetch()
    except Exception:
        if path.exists():
            return path.read_text()
        raise
    path.write_text(text)
    return text
