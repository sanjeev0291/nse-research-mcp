"""Price and portfolio alerts.

Rules live in ~/.config/nse-research/alerts.json and are checked on a schedule (every minute during
NSE hours by default). A triggered rule becomes an event that is shown in the desk UI, posted as a
desktop notification (macOS / Linux) and, if configured, sent to Telegram.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .symbols import normalize, yahoo_ticker

log = logging.getLogger("nse-research.alerts")
HOME = Path(os.environ.get("NSE_RESEARCH_HOME", Path.home() / ".config" / "nse-research"))
STORE = HOME / "alerts.json"
IST = ZoneInfo("Asia/Kolkata")

KINDS = {
    "move": "moves more than X% today",
    "above": "price goes above X",
    "below": "price goes below X",
    "portfolio_move": "any holding moves more than X% today",
    "portfolio_pnl": "any holding's P&L vs buy price crosses X%",
}
PORTFOLIO_KINDS = {"portfolio_move", "portfolio_pnl"}
DEFAULT_SETTINGS = {
    "interval_sec": 60,
    "cooldown_min": 30,
    "only_market_hours": True,
    "desktop": True,
    "telegram_token": "",
    "telegram_chat_id": "",
}


def market_open(now: datetime | None = None) -> bool:
    """NSE cash market, with a few minutes of slack either side."""
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 10 <= minutes <= 15 * 60 + 40


# ------------------------------------------------------------------ quotes -----------------------

def _fast_quote(symbol: str) -> dict:
    import yfinance as yf

    fi = yf.Ticker(yahoo_ticker(symbol)).fast_info
    price, prev = fi.last_price, fi.previous_close
    if price is None:
        raise LookupError("no price")
    chg = (price / prev - 1) * 100 if prev else None
    return {"price": float(price), "prev_close": float(prev) if prev else None, "change_pct": round(chg, 2) if chg is not None else None,
            "day_high": fi.day_high, "day_low": fi.day_low}


def fetch_quotes(symbols: list[str]) -> tuple[dict, dict]:
    quotes, errors = {}, {}

    def one(s):
        try:
            quotes[s] = _fast_quote(s)
        except Exception as e:
            errors[s] = str(e)[:120]

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, symbols))
    return quotes, errors


# ------------------------------------------------------------------ evaluation -------------------

def _dir_ok(direction: str, delta: float) -> bool:
    return (direction == "either") or (direction == "up" and delta > 0) or (direction == "down" and delta < 0)


def evaluate(rule: dict, quotes: dict, holdings: list[dict] | None) -> list[dict]:
    """Return the hits (one per symbol) a rule produces against the current data."""
    kind, value, direction = rule["kind"], float(rule["value"]), rule.get("direction", "either")
    hits = []
    if kind in ("move", "above", "below"):
        q = quotes.get(rule["symbol"])
        if not q:
            return []
        price, chg = q["price"], q.get("change_pct")
        if kind == "move" and chg is not None and abs(chg) >= value and _dir_ok(direction, chg):
            hits.append({"symbol": rule["symbol"], "price": price, "change_pct": chg, "message": f"{rule['symbol']} moved {chg:+.2f}% today to ₹{price:,.2f} (rule: ±{value:g}%)"})
        elif kind == "above" and price >= value:
            hits.append({"symbol": rule["symbol"], "price": price, "change_pct": chg, "message": f"{rule['symbol']} is above ₹{value:,.2f}: now ₹{price:,.2f}"})
        elif kind == "below" and price <= value:
            hits.append({"symbol": rule["symbol"], "price": price, "change_pct": chg, "message": f"{rule['symbol']} is below ₹{value:,.2f}: now ₹{price:,.2f}"})
    elif kind in PORTFOLIO_KINDS and holdings:
        for h in holdings:
            sym, price = h.get("symbol"), h.get("last_price")
            if kind == "portfolio_move":
                chg = h.get("day_change_pct")
                if chg is not None and abs(chg) >= value and _dir_ok(direction, chg):
                    hits.append({"symbol": sym, "price": price, "change_pct": chg, "message": f"Holding {sym} moved {chg:+.2f}% today to ₹{price:,.2f}"})
            else:
                pnl = h.get("pnl_pct")
                if pnl is not None and abs(pnl) >= value and _dir_ok(direction, pnl):
                    hits.append({"symbol": sym, "price": price, "change_pct": h.get("day_change_pct"), "message": f"Holding {sym} is {pnl:+.2f}% vs your buy price ₹{h.get('average_price'):,.2f} (now ₹{price:,.2f})"})
    return hits


# ------------------------------------------------------------------ delivery ---------------------

def desktop_notify(title: str, body: str) -> bool:
    system = platform.system()
    try:
        if system == "Darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}" sound name "Glass"'], check=True, capture_output=True, timeout=10)
            return True
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, body], check=True, timeout=10)
            return True
    except Exception as e:
        log.warning("desktop notification failed: %s", e)
    return False


def telegram_send(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok")
        if not ok:
            log.warning("telegram send failed: %s", r.text[:200])
        return bool(ok)
    except Exception as e:
        log.warning("telegram send failed: %s", e)
        return False


# ------------------------------------------------------------------ engine -----------------------

class AlertEngine:
    def __init__(self, holdings_fn=None, store: Path = STORE):
        self.holdings_fn = holdings_fn
        self.store = store
        self._lock = threading.RLock()
        self.data = {"rules": [], "settings": dict(DEFAULT_SETTINGS), "events": []}
        self.status = {"last_check": None, "last_summary": None, "running": False, "errors": []}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load()

    # ---- persistence ----
    def _load(self):
        try:
            d = json.loads(self.store.read_text())
            self.data["rules"] = d.get("rules", [])
            self.data["settings"] = {**DEFAULT_SETTINGS, **d.get("settings", {})}
            self.data["events"] = d.get("events", [])[-200:]
        except Exception:
            pass

    def _save(self):
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.store)

    # ---- rules ----
    def add_rule(self, symbol: str, kind: str, value, direction: str = "either", repeat: bool = True, note: str = "") -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        try:
            value = float(value)
        except Exception:
            raise ValueError("value must be a number")
        if value <= 0:
            raise ValueError("value must be positive")
        if direction not in ("up", "down", "either"):
            raise ValueError("direction must be up, down or either")
        sym = "PORTFOLIO" if kind in PORTFOLIO_KINDS else normalize(symbol)
        if not sym:
            raise ValueError("symbol is required")
        rule = {"id": uuid.uuid4().hex[:8], "symbol": sym, "kind": kind, "value": value, "direction": direction, "repeat": bool(repeat),
                "note": (note or "")[:120], "enabled": True, "created": time.time(), "state": {"last_fired": {}, "last_price": None}}
        with self._lock:
            self.data["rules"].append(rule)
            self._save()
        return rule

    def rule_action(self, rule_id: str, action: str) -> dict | None:
        with self._lock:
            for i, r in enumerate(self.data["rules"]):
                if r["id"] == rule_id:
                    if action == "delete":
                        self.data["rules"].pop(i)
                        self._save()
                        return r
                    if action == "toggle":
                        r["enabled"] = not r["enabled"]
                        r["state"]["last_fired"] = {}
                        self._save()
                        return r
                    raise ValueError("action must be delete or toggle")
        return None

    def update_settings(self, patch: dict) -> dict:
        with self._lock:
            s = self.data["settings"]
            for k in ("interval_sec", "cooldown_min"):
                if k in patch and patch[k] not in (None, ""):
                    s[k] = max(15, int(patch[k])) if k == "interval_sec" else max(0, int(patch[k]))
            for k in ("only_market_hours", "desktop"):
                if k in patch:
                    s[k] = bool(patch[k])
            for k in ("telegram_token", "telegram_chat_id"):
                if k in patch and patch[k] is not None and (patch[k] != "" or patch.get("clear_telegram")):
                    s[k] = str(patch[k]).strip()
            self._save()
            return self.public_settings()

    def public_settings(self) -> dict:
        s = dict(self.data["settings"])
        tok = s.pop("telegram_token", "")
        s["telegram_token_set"] = bool(tok)
        s["telegram_token_hint"] = ("…" + tok[-4:]) if tok else ""
        return s

    def snapshot(self) -> dict:
        with self._lock:
            return {"rules": self.data["rules"], "settings": self.public_settings(), "events": list(reversed(self.data["events"][-50:])),
                    "status": {**self.status, "market_open": market_open(), "now_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M")}, "kinds": KINDS}

    def events_since(self, ts: float) -> list[dict]:
        with self._lock:
            return [e for e in self.data["events"] if e["ts"] > ts]

    # ---- checking ----
    def check(self, force: bool = False) -> dict:
        with self._lock:
            rules = [r for r in self.data["rules"] if r["enabled"]]
            settings = dict(self.data["settings"])
        symbols = sorted({r["symbol"] for r in rules if r["kind"] not in PORTFOLIO_KINDS})
        quotes, errors = fetch_quotes(symbols) if symbols else ({}, {})
        holdings, note = None, None
        if any(r["kind"] in PORTFOLIO_KINDS for r in rules):
            if self.holdings_fn is None:
                note = "portfolio rules need the desk (Kite) running"
            else:
                try:
                    holdings = self.holdings_fn()
                except Exception as e:
                    note = f"portfolio rules skipped: {str(e)[:100]}"
        now = time.time()
        cooldown = float(settings.get("cooldown_min", 30)) * 60
        new_events = []
        with self._lock:
            for r in rules:
                if r["symbol"] in quotes:
                    r["state"]["last_price"] = quotes[r["symbol"]]["price"]
                for hit in evaluate(r, quotes, holdings):
                    last = r["state"]["last_fired"].get(hit["symbol"], 0)
                    if now - last < cooldown:
                        continue
                    r["state"]["last_fired"][hit["symbol"]] = now
                    ev = {"id": uuid.uuid4().hex[:8], "ts": now, "time_ist": datetime.now(IST).strftime("%d %b %H:%M"), "rule_id": r["id"], "kind": r["kind"],
                          "symbol": hit["symbol"], "price": hit["price"], "change_pct": hit["change_pct"], "message": hit["message"], "note": r.get("note", "")}
                    new_events.append(ev)
                    if not r["repeat"] and r["kind"] in ("above", "below"):
                        r["enabled"] = False
            self.data["events"] = (self.data["events"] + new_events)[-200:]
            summary = {"rules": len(rules), "quotes": len(quotes), "fired": len(new_events), "quote_errors": errors, "note": note, "forced": force}
            self.status.update({"last_check": now, "last_check_ist": datetime.now(IST).strftime("%H:%M:%S"), "last_summary": summary})
            self._save()
        for ev in new_events:
            self.deliver(ev, settings)
        return {"events": new_events, **summary}

    def deliver(self, ev: dict, settings: dict | None = None) -> dict:
        settings = settings or self.data["settings"]
        title = f"Stock Desk: {ev['symbol']}"
        body = ev["message"] + (f" · {ev['note']}" if ev.get("note") else "")
        out = {"desktop": False, "telegram": False}
        if settings.get("desktop", True):
            out["desktop"] = desktop_notify(title, body)
        if settings.get("telegram_token") and settings.get("telegram_chat_id"):
            out["telegram"] = telegram_send(settings["telegram_token"], settings["telegram_chat_id"], f"{title}\n{body}")
        ev["delivered"] = out
        return out

    def test_notification(self) -> dict:
        ev = {"symbol": "TEST", "message": "Alerts are working. This is a test from Stock Desk.", "note": ""}
        return self.deliver(ev)

    # ---- background loop ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="alerts", daemon=True)
        self._thread.start()
        self.status["running"] = True

    def stop(self):
        self._stop.set()
        self.status["running"] = False

    def _loop(self):
        while not self._stop.is_set():
            settings = self.data["settings"]
            if not self.data["rules"] or (settings.get("only_market_hours", True) and not market_open()):
                self.status["waiting"] = "no rules" if not self.data["rules"] else "outside market hours (09:15–15:30 IST, Mon–Fri)"
                self._stop.wait(30)
                continue
            self.status["waiting"] = None
            try:
                self.check()
            except Exception as e:  # never let the loop die
                log.exception("alert check failed")
                self.status["errors"] = (self.status.get("errors", []) + [str(e)[:200]])[-5:]
            self._stop.wait(max(15, int(settings.get("interval_sec", 60))))


# ------------------------------------------------------------------ CLI --------------------------

def main() -> None:
    ap = argparse.ArgumentParser(prog="nse-alerts", description="Price/portfolio alerts without the dashboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show rules and recent events")
    a = sub.add_parser("add", help="add a rule, e.g. add TCS move 3 --direction down")
    a.add_argument("symbol"); a.add_argument("kind", choices=list(KINDS)); a.add_argument("value", type=float)
    a.add_argument("--direction", default="either", choices=["up", "down", "either"]); a.add_argument("--once", action="store_true"); a.add_argument("--note", default="")
    d = sub.add_parser("delete", help="delete a rule by id"); d.add_argument("id")
    sub.add_parser("check", help="check all rules once now and print hits")
    sub.add_parser("test", help="send a test notification")
    r = sub.add_parser("run", help="keep checking in the foreground (portfolio rules use your Kite session if logged in)")
    r.add_argument("--always", action="store_true", help="ignore market hours")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stderr)

    holdings_fn = None
    if args.cmd in ("check", "run"):
        try:
            from .kite_client import KiteClient
            from .portfolio import holdings_summary
            kite = KiteClient()
            holdings_fn = lambda: holdings_summary(kite)["holdings"]
        except Exception:
            pass
    eng = AlertEngine(holdings_fn=holdings_fn)
    if args.cmd == "list":
        snap = eng.snapshot()
        for x in snap["rules"]:
            print(f"{x['id']}  {'on ' if x['enabled'] else 'off'}  {x['symbol']:<12} {x['kind']:<15} {x['value']:g} {x['direction']:<6} {'repeat' if x['repeat'] else 'once'}  {x.get('note','')}")
        print(f"-- {len(snap['events'])} recent events; last check {snap['status'].get('last_check_ist')}")
        for e in snap["events"][:10]:
            print(f"{e['time_ist']}  {e['message']}")
    elif args.cmd == "add":
        rule = eng.add_rule(args.symbol, args.kind, args.value, args.direction, not args.once, args.note)
        print("added", rule["id"], rule["symbol"], rule["kind"], rule["value"])
    elif args.cmd == "delete":
        print("deleted" if eng.rule_action(args.id, "delete") else "no such rule")
    elif args.cmd == "check":
        res = eng.check(force=True)
        print(json.dumps({k: v for k, v in res.items() if k != "events"}, indent=1))
        for e in res["events"]:
            print("ALERT:", e["message"])
    elif args.cmd == "test":
        print(eng.test_notification())
    elif args.cmd == "run":
        if args.always:
            eng.update_settings({"only_market_hours": False})
        eng.start()
        print("alerts running; Ctrl+C to stop", file=sys.stderr)
        try:
            while True:
                time.sleep(5)
                for e in eng.events_since(time.time() - 6):
                    print(e["time_ist"], e["message"])
        except KeyboardInterrupt:
            eng.stop()


if __name__ == "__main__":
    main()
