"""Client for NSE India's public JSON endpoints and static archive files.

NSE fronts its site with bot protection. A Chrome-impersonated TLS session (curl_cffi) warmed on the
home page gets through for the endpoints used here. Some endpoints (per-stock quote, historical
candles) are blocked regardless; those come from Yahoo Finance instead (see yahoo.py).
"""
from __future__ import annotations

import threading
import time

from curl_cffi import requests

from .cache import disk_text, ttl_cache

BASE = "https://www.nseindia.com"
ARCHIVE = "https://nsearchives.nseindia.com"
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}


class NSEError(RuntimeError):
    pass


_lock = threading.Lock()
_session: requests.Session | None = None
_warmed_at = 0.0


def _get_session(force: bool = False) -> requests.Session:
    global _session, _warmed_at
    with _lock:
        if _session is None or force or time.time() - _warmed_at > 900:
            s = requests.Session(impersonate="chrome")
            s.headers.update(HEADERS)
            try:
                s.get(BASE + "/", timeout=20)
            except Exception:
                pass
            _session = s
            _warmed_at = time.time()
        return _session


def get_json(path: str, params: dict | None = None):
    last: Exception | None = None
    for attempt in range(2):
        s = _get_session(force=attempt > 0)
        try:
            r = s.get(BASE + path, params=params, timeout=25)
        except Exception as e:  # network error
            last = e
            continue
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and "json" in ctype:
            try:
                return r.json()
            except Exception as e:
                last = e
                continue
        last = NSEError(f"HTTP {r.status_code}")
        if r.status_code not in (401, 403, 503):
            break
    raise NSEError(
        f"NSE request failed for {path} ({last}). NSE throttles automated traffic; wait a minute and retry."
    )


def archive_text(path: str, cache_name: str, max_age: float = 86400) -> str:
    def fetch():
        r = _get_session().get(f"{ARCHIVE}/{path}", timeout=30)
        if r.status_code != 200:
            raise NSEError(f"{ARCHIVE}/{path} -> HTTP {r.status_code}")
        return r.text

    return disk_text(cache_name, fetch, max_age)


# ---- live market data ----------------------------------------------------------------------

@ttl_cache(60)
def all_indices() -> list[dict]:
    return get_json("/api/allIndices").get("data", [])


@ttl_cache(60)
def market_status() -> list[dict]:
    return get_json("/api/marketStatus").get("marketState", [])


@ttl_cache(300)
def fii_dii() -> list[dict]:
    return get_json("/api/fiidiiTradeReact")


@ttl_cache(60)
def movers(direction: str) -> dict:
    key = "gainers" if direction == "gainers" else "loosers"  # NSE's spelling
    return get_json("/api/live-analysis-variations", {"index": key})


@ttl_cache(60)
def most_active(by: str) -> dict:
    return get_json("/api/live-analysis-most-active-securities", {"index": by})


@ttl_cache(300)
def week52(which: str) -> dict:
    path = "/api/live-analysis-data-52weekhighstock" if which == "high" else "/api/live-analysis-data-52weeklowstock"
    return get_json(path)


@ttl_cache(600)
def large_deals() -> dict:
    return get_json("/api/snapshot-capital-market-largedeal")


@ttl_cache(86400)
def holidays() -> dict:
    return get_json("/api/holiday-master", {"type": "trading"})


# ---- per-company corporate data ------------------------------------------------------------

@ttl_cache(3600)
def shareholding(symbol: str) -> list[dict]:
    return get_json("/api/corporate-share-holdings-master", {"index": "equities", "symbol": symbol})


@ttl_cache(3600)
def corporate_actions(symbol: str) -> list[dict]:
    return get_json("/api/corporates-corporateActions", {"index": "equities", "symbol": symbol})


@ttl_cache(3600)
def board_meetings(symbol: str) -> list[dict]:
    return get_json("/api/event-calendar", {"index": "equities", "symbol": symbol})


@ttl_cache(900)
def announcements(symbol: str) -> list[dict]:
    return get_json("/api/corporate-announcements", {"index": "equities", "symbol": symbol})


# ---- derivatives ---------------------------------------------------------------------------

INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


@ttl_cache(300)
def option_contract_info(symbol: str) -> dict:
    return get_json("/api/option-chain-contract-info", {"symbol": symbol})


@ttl_cache(60)
def option_chain(symbol: str, expiry: str) -> dict:
    kind = "Indices" if symbol in INDEX_UNDERLYINGS else "Equity"
    return get_json("/api/option-chain-v3", {"type": kind, "symbol": symbol, "expiry": expiry})
