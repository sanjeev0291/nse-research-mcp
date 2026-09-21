"""nse-desk: a local web dashboard (http://127.0.0.1:8765) for portfolio, orders, research and AI.

Backend only ever talks to Zerodha's hosted Kite MCP, NSE India and Yahoo Finance. The "Ask Claude"
box shells out to the `claude` CLI on this machine (Claude Code) so it uses your own subscription.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import nse, server as S, symbols, yahoo
from ..kite_client import READ_TOOLS, WRITE_TOOLS, KiteClient, KiteError, KiteUnreachable, NotLoggedIn

STATIC = Path(__file__).parent / "static"
kite = KiteClient()
pool = ThreadPoolExecutor(max_workers=8)


def ok(data, status=200):
    return JSONResponse(data, status_code=status)


def fail(msg, status=400, **extra):
    return JSONResponse({"error": str(msg), **extra}, status_code=status)


async def index(request: Request):
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------------ Zerodha (Kite MCP) ----------

def _status():
    try:
        p = kite.profile()
        return {"connected": True, "profile": p, "session_started": kite.session_started}
    except NotLoggedIn:
        return {"connected": False}
    except KiteUnreachable as e:
        return {"connected": False, "error": str(e)}
    except KiteError as e:  # a tool error on a session that is not logged in reads as "not logged in"
        return {"connected": False, "detail": str(e)}
    except Exception as e:
        return {"connected": False, "error": str(e)}


async def kite_status(request: Request):
    return ok(await run_in_threadpool(_status))


async def kite_login(request: Request):
    try:
        url = await run_in_threadpool(kite.login_url)
        return ok({"login_url": url})
    except Exception as e:
        return fail(e, 502)


async def kite_logout(request: Request):
    await run_in_threadpool(kite.logout)
    return ok({"connected": False})


def _logged_in() -> bool:
    try:
        kite.profile()
        return True
    except Exception:
        return False


def _kite_error(e: Exception):
    """Map a Kite failure to a response; a generic failure on a logged-out session is reported as such."""
    if isinstance(e, NotLoggedIn) or not _logged_in():
        return fail("Not logged in to Kite", 401, not_logged_in=True)
    return fail(e, 502)


async def kite_call(request: Request):
    body = await request.json()
    name, args = body.get("name"), body.get("arguments") or {}
    if name not in READ_TOOLS | WRITE_TOOLS:
        return fail(f"tool '{name}' is not allowed from the desk")
    try:
        result = await run_in_threadpool(kite.call, name, args)
        return ok({"result": result, "write": name in WRITE_TOOLS})
    except Exception as e:
        return await run_in_threadpool(_kite_error, e)


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _holdings():
    raw = kite.call("get_holdings")
    rows = KiteClient.rows(raw)
    members = symbols.membership()
    out, invested, current, day_pnl = [], 0.0, 0.0, 0.0
    for h in rows:
        sym = h.get("tradingsymbol") or h.get("symbol") or ""
        qty = _num(h.get("quantity")) + _num(h.get("t1_quantity"))
        avg, ltp = _num(h.get("average_price")), _num(h.get("last_price"))
        close = _num(h.get("close_price")) or ltp
        value, cost = qty * ltp, qty * avg
        m = members.get(sym, {})
        out.append(
            {
                "symbol": sym, "exchange": h.get("exchange"), "isin": h.get("isin"), "product": h.get("product"),
                "quantity": qty, "t1_quantity": _num(h.get("t1_quantity")), "average_price": avg, "last_price": ltp,
                "invested": round(cost, 2), "value": round(value, 2), "pnl": round(value - cost, 2),
                "pnl_pct": round((ltp / avg - 1) * 100, 2) if avg else None,
                "day_change_pct": _num(h.get("day_change_percentage")) or (round((ltp / close - 1) * 100, 2) if close else None),
                "day_pnl": round((ltp - close) * qty, 2) if close else None,
                "industry": m.get("industry"), "cap_bucket": m.get("cap_bucket"), "indices": m.get("indices", []),
            }
        )
        invested += cost
        current += value
        day_pnl += (ltp - close) * qty if close else 0.0
    for r in out:
        r["weight_pct"] = round(r["value"] / current * 100, 2) if current else None
    out.sort(key=lambda r: -r["value"])
    sectors: dict[str, float] = {}
    for r in out:
        k = r["industry"] or "Unclassified"
        sectors[k] = sectors.get(k, 0.0) + r["value"]
    sector_rows = sorted(({"industry": k, "value": round(v, 2), "weight_pct": round(v / current * 100, 2) if current else None} for k, v in sectors.items()), key=lambda x: -x["value"])
    totals = {
        "count": len(out), "invested": round(invested, 2), "current": round(current, 2), "pnl": round(current - invested, 2),
        "pnl_pct": round((current / invested - 1) * 100, 2) if invested else None, "day_pnl": round(day_pnl, 2),
        "top3_weight_pct": round(sum(r["weight_pct"] or 0 for r in out[:3]), 2) if out else None,
    }
    return {"holdings": out, "totals": totals, "sectors": sector_rows}


async def kite_holdings(request: Request):
    try:
        return ok(await run_in_threadpool(_holdings))
    except Exception as e:
        return await run_in_threadpool(_kite_error, e)


def _enrich(syms: list[str]):
    out = []
    for i in range(0, len(syms), 15):
        out.extend(yahoo.compare(syms[i:i + 15]))
    return {r["symbol"]: r for r in out}


async def kite_enrich(request: Request):
    body = await request.json()
    syms = [symbols.normalize(s) for s in body.get("symbols", [])][:60]
    return ok(await run_in_threadpool(_enrich, syms))


# ------------------------------------------------------------------ research --------------------

def _guarded(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # each panel fails independently
        return {"error": str(e)}


def _stock_bundle(symbol: str):
    sym = symbols.normalize(symbol)
    jobs = {
        "quote": (yahoo.quote, sym),
        "profile": (yahoo.profile, sym),
        "ratios": (yahoo.key_ratios, sym),
        "history": (yahoo.price_history, sym, "1y", "1d", 260),
        "shareholding": (S.get_shareholding, sym, 8),
        "actions": (S.get_corporate_actions, sym),
        "announcements": (S.get_announcements, sym, 10),
        "analyst": (yahoo.analyst_view, sym),
    }
    futures = {k: pool.submit(_guarded, *v) for k, v in jobs.items()}
    out = {k: f.result() for k, f in futures.items()}
    out["symbol"] = sym
    return out


async def research_search(request: Request):
    q = request.query_params.get("q", "")
    return ok(await run_in_threadpool(symbols.search, q, 12))


async def research_stock(request: Request):
    return ok(await run_in_threadpool(_stock_bundle, request.path_params["symbol"]))


async def research_financials(request: Request):
    sym = request.path_params["symbol"]
    period = request.query_params.get("period", "annual")
    return ok(await run_in_threadpool(_guarded, yahoo.financials, sym, "all", period))


async def research_screen(request: Request):
    body = await request.json()
    clean = {k: v for k, v in body.items() if v not in (None, "", [])}
    return ok(await run_in_threadpool(_guarded, lambda: S.screen_stocks(**clean)))


def _market():
    jobs = {
        "pulse": (S.get_market_pulse,),
        "gainers": (S.get_top_movers, "NIFTY", "gainers", 10),
        "losers": (S.get_top_movers, "NIFTY", "losers", 10),
        "active": (S.get_most_active, "value", 10),
        "highs": (S.get_52_week_breakouts, "high", 12),
        "lows": (S.get_52_week_breakouts, "low", 12),
        "deals": (S.get_bulk_block_deals, 10),
        "nifty_chain": (S.get_option_chain, "NIFTY", None, 5),
    }
    futures = {k: pool.submit(_guarded, *v) for k, v in jobs.items()}
    return {k: f.result() for k, f in futures.items()}


async def research_market(request: Request):
    return ok(await run_in_threadpool(_market))


async def research_option_chain(request: Request):
    sym = request.query_params.get("symbol", "NIFTY")
    exp = request.query_params.get("expiry") or None
    return ok(await run_in_threadpool(_guarded, S.get_option_chain, sym, exp, 10))


async def research_compare(request: Request):
    syms = [s for s in request.query_params.get("symbols", "").split(",") if s.strip()][:15]
    return ok(await run_in_threadpool(_guarded, yahoo.compare, syms))


async def watchlist_get(request: Request):
    return ok(await run_in_threadpool(S.manage_watchlist, "get", None))


async def watchlist_post(request: Request):
    body = await request.json()
    return ok(await run_in_threadpool(S.manage_watchlist, body.get("action", "get"), body.get("symbols") or []))


# ------------------------------------------------------------------ Ask Claude ------------------

def _claude_bin() -> str | None:
    for cand in (shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude")):
        if cand and Path(cand).exists():
            return cand
    return None


def _ask(question: str, include_portfolio: bool) -> dict:
    exe = _claude_bin()
    if not exe:
        return {"error": "Claude Code CLI not found. Install it (https://claude.com/claude-code) to use Ask Claude."}
    context = ""
    if include_portfolio:
        try:
            h = _holdings()
            slim = [{k: r[k] for k in ("symbol", "quantity", "average_price", "last_price", "value", "pnl_pct", "weight_pct", "industry")} for r in h["holdings"]]
            context = "\n\nMy Zerodha holdings (from Kite, just now):\n" + json.dumps({"totals": h["totals"], "holdings": slim}, indent=0) + "\n"
        except NotLoggedIn:
            context = "\n\n(Kite is not logged in, so no live holdings are available.)\n"
        except Exception as e:
            context = f"\n\n(Could not fetch holdings: {e})\n"
    prompt = (
        "You are a research assistant for Indian equities with the nse-research MCP tools available. "
        "Use them for any live data you need. Be concrete and brief; use tables for tabular data; "
        "this is analysis of the user's own data, not investment advice.\n\nQuestion: " + question + context
    )
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    try:
        proc = subprocess.run(
            [exe, "-p", prompt, "--output-format", "json", "--allowedTools", "mcp__nse-research__*", "--disallowedTools", "mcp__kite__*"],
            capture_output=True, text=True, timeout=300, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Claude took longer than 5 minutes; try a narrower question."}
    if proc.returncode != 0 and not proc.stdout.strip():
        return {"error": f"claude exited with {proc.returncode}: {proc.stderr[-800:]}"}
    try:
        data = json.loads(proc.stdout)
        text = data.get("result") if isinstance(data, dict) else None
        return {"answer": text or proc.stdout, "cost_usd": data.get("total_cost_usd") if isinstance(data, dict) else None, "turns": data.get("num_turns") if isinstance(data, dict) else None}
    except Exception:
        return {"answer": proc.stdout}


async def ai_ask(request: Request):
    body = await request.json()
    q = (body.get("question") or "").strip()
    if not q:
        return fail("Ask something first")
    return ok(await run_in_threadpool(_ask, q, bool(body.get("include_portfolio"))))


async def ai_status(request: Request):
    return ok({"available": _claude_bin() is not None})


routes = [
    Route("/", index),
    Route("/api/kite/status", kite_status),
    Route("/api/kite/login", kite_login, methods=["POST"]),
    Route("/api/kite/logout", kite_logout, methods=["POST"]),
    Route("/api/kite/call", kite_call, methods=["POST"]),
    Route("/api/kite/holdings", kite_holdings),
    Route("/api/kite/enrich", kite_enrich, methods=["POST"]),
    Route("/api/research/search", research_search),
    Route("/api/research/stock/{symbol}", research_stock),
    Route("/api/research/financials/{symbol}", research_financials),
    Route("/api/research/screen", research_screen, methods=["POST"]),
    Route("/api/research/market", research_market),
    Route("/api/research/option_chain", research_option_chain),
    Route("/api/research/compare", research_compare),
    Route("/api/watchlist", watchlist_get),
    Route("/api/watchlist", watchlist_post, methods=["POST"]),
    Route("/api/ai/ask", ai_ask, methods=["POST"]),
    Route("/api/ai/status", ai_status),
    Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
]

app = Starlette(routes=routes)


def main() -> None:
    ap = argparse.ArgumentParser(prog="nse-desk", description="Local stock desk: portfolio, orders, research, AI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NSE_DESK_PORT", "8765")))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"nse-desk running at {url}  (Ctrl+C to stop)", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
