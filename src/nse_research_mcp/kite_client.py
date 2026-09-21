"""Minimal client for Zerodha's hosted Kite MCP server (https://mcp.kite.trade/mcp).

Speaks MCP JSON-RPC over plain HTTP with the Mcp-Session-Id header, so a login done once (via the
link the `login` tool returns) stays usable across restarts of the desk UI until Kite expires it
(every morning around 6 AM IST). The session id is stored in ~/.config/nse-research/kite_session.json.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

KITE_MCP_URL = os.environ.get("KITE_MCP_URL", "https://mcp.kite.trade/mcp")
HOME = Path(os.environ.get("NSE_RESEARCH_HOME", Path.home() / ".config" / "nse-research"))
SESSION_FILE = HOME / "kite_session.json"


class KiteError(RuntimeError):
    pass


class NotLoggedIn(KiteError):
    pass


class KiteUnreachable(KiteError):
    """Network or HTTP-level failure talking to the Kite MCP server (as opposed to a tool error)."""


READ_TOOLS = {
    "get_profile", "get_margins", "get_holdings", "get_positions", "get_mf_holdings", "get_orders",
    "get_trades", "get_order_history", "get_order_trades", "get_gtts", "get_quotes", "get_ltp", "get_ohlc",
    "get_historical_data", "search_instruments",
}
WRITE_TOOLS = {"place_order", "modify_order", "cancel_order", "place_gtt_order", "modify_gtt_order", "delete_gtt_order"}


class KiteClient:
    def __init__(self, url: str = KITE_MCP_URL):
        self.url = url
        self.session_id: str | None = None
        self.session_started: float | None = None
        self._id = 0
        self._lock = threading.Lock()
        self._http = httpx.Client(
            timeout=60,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        try:
            d = json.loads(SESSION_FILE.read_text())
            if d.get("url") == self.url and d.get("session_id"):
                self.session_id = d["session_id"]
                self.session_started = d.get("started")
        except Exception:
            pass

    def _save(self) -> None:
        HOME.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps({"url": self.url, "session_id": self.session_id, "started": self.session_started}))

    def logout(self) -> None:
        sid = self.session_id
        self.session_id = None
        self.session_started = None
        try:
            SESSION_FILE.unlink()
        except FileNotFoundError:
            pass
        if sid:
            try:
                self._http.delete(self.url, headers={"Mcp-Session-Id": sid})
            except Exception:
                pass

    # ---- transport ----
    def _post(self, payload: dict, with_session: bool = True) -> httpx.Response:
        headers = {}
        if with_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            return self._http.post(self.url, content=json.dumps(payload), headers=headers)
        except httpx.HTTPError as e:
            raise KiteUnreachable(f"Could not reach Kite MCP: {e}") from e

    @staticmethod
    def _parse(resp: httpx.Response) -> dict:
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            last = None
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    try:
                        last = json.loads(line[5:].strip())
                    except Exception:
                        continue
            if last is None:
                raise KiteError("Empty event stream from Kite MCP")
            return last
        try:
            return resp.json()
        except Exception:
            raise KiteError(f"Kite MCP returned HTTP {resp.status_code}: {resp.text[:200]}")

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        resp = self._post(payload)
        if resp.status_code in (400, 404) and self.session_id:
            # stale or expired session id: start a fresh session (the user will need to log in again)
            self.session_id = None
            self._connect()
            resp = self._post(payload)
        if resp.status_code >= 400:
            raise KiteUnreachable(f"Kite MCP returned HTTP {resp.status_code}: {resp.text[:200]}")
        data = self._parse(resp)
        if "error" in data:
            raise KiteError(data["error"].get("message", str(data["error"])))
        return data.get("result", {})

    def _connect(self) -> None:
        self._id += 1
        resp = self._post(
            {
                "jsonrpc": "2.0", "id": self._id, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "nse-research-desk", "version": "0.1"}},
            },
            with_session=False,
        )
        if resp.status_code >= 400:
            raise KiteUnreachable(f"Could not reach Kite MCP (HTTP {resp.status_code})")
        sid = resp.headers.get("mcp-session-id")
        if not sid:
            raise KiteError("Kite MCP did not return a session id")
        self.session_id = sid
        self.session_started = time.time()
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._save()

    def ensure_session(self) -> None:
        with self._lock:
            if not self.session_id:
                self._connect()

    # ---- tools ----
    def call(self, name: str, arguments: dict | None = None):
        self.ensure_session()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(texts).strip()
        if result.get("isError"):
            if re.search(r"log ?in", text, re.I):
                raise NotLoggedIn(text)
            raise KiteError(text or "Kite tool returned an error")
        try:
            return json.loads(text)
        except Exception:
            return text

    def login_url(self) -> str:
        text = self.call("login")
        if not isinstance(text, str):
            text = json.dumps(text)
        m = re.search(r"https://[^\s)\]]+authorize[^\s)\]]*", text)
        if not m:
            raise KiteError(f"Could not find a login link in Kite's response: {text[:200]}")
        return m.group(0)

    def profile(self):
        return self.call("get_profile")

    @staticmethod
    def rows(result) -> list:
        """Kite tools return either a list or {"<something>": [...], "pagination": {...}}."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for k in ("holdings", "positions", "orders", "trades", "gtts", "data", "items", "results", "net"):
                if isinstance(result.get(k), list):
                    return result[k]
            for v in result.values():
                if isinstance(v, list):
                    return v
        return []
