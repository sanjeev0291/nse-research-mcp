"""MCP server exposing Indian stock research tools. Runs over stdio; nothing is written to stdout
except the MCP protocol."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import nse, symbols, yahoo
from .yahoo import num

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

INSTRUCTIONS = """Self-hosted Indian stock market research (NSE-listed equities and index derivatives).
Data comes straight from NSE India (indices, FII/DII, shareholding, corporate actions, announcements,
bulk deals, option chain) and Yahoo Finance (quotes, price history, fundamentals, statements, analyst
data, screener). No third-party API sits in between. Symbols are NSE symbols (RELIANCE, TCS, M&M);
use search_stocks when unsure. Money values are in rupees; large amounts are in crore (1 crore = 10
million). Market hours: NSE 09:15 to 15:30 IST, Monday to Friday. This is data, not investment advice."""

mcp = FastMCP("nse-research", instructions=INSTRUCTIONS)

HOME = Path(os.environ.get("NSE_RESEARCH_HOME", Path.home() / ".config" / "nse-research"))
WATCHLIST = HOME / "watchlist.json"


def guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (LookupError, ValueError) as e:
        return {"error": str(e)}
    except nse.NSEError as e:
        return {"error": str(e), "hint": "NSE endpoints are rate limited; retry shortly or use a Yahoo-backed tool."}
    except Exception as e:  # keep the server alive on any upstream surprise
        logging.exception("tool failure")
        return {"error": f"{type(e).__name__}: {e}"}


def _parse_date(s: str | None):
    if not s or s == "-":
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


# ---------------------------------------------------------------- discovery ---------------------

@mcp.tool()
def search_stocks(query: str, limit: int = 10) -> list[dict]:
    """Find NSE symbols by company name or partial symbol (e.g. "tata motors", "hdfc", "INFY").
    Returns symbol, name, ISIN, listing date, NSE industry and index membership (NIFTY 50/500 etc.)."""
    return guard(symbols.search, query, max(1, min(limit, 50)))


@mcp.tool()
def screen_stocks(
    min_market_cap_cr: float | None = None,
    max_market_cap_cr: float | None = None,
    min_pe: float | None = None,
    max_pe: float | None = None,
    max_pb: float | None = None,
    min_roe_pct: float | None = None,
    min_dividend_yield_pct: float | None = None,
    min_revenue_growth_pct: float | None = None,
    min_eps_growth_pct: float | None = None,
    min_52w_change_pct: float | None = None,
    max_52w_change_pct: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_avg_volume: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str = "market_cap",
    ascending: bool = False,
    limit: int = 25,
) -> dict:
    """Screen all NSE-listed stocks by fundamentals and price action (server-side, fast).
    Filters: market cap in ₹ crore, PE (ttm), PB, ROE %, dividend yield %, quarterly revenue growth %,
    EPS growth %, 52-week change %, price, 3-month average volume, Yahoo sector (one of: Basic Materials,
    Communication Services, Consumer Cyclical, Consumer Defensive, Energy, Financial Services, Healthcare,
    Industrials, Real Estate, Technology, Utilities) or industry.
    sort_by: market_cap | pe | dividend_yield | change_1d | change_52w | volume | price. limit ≤ 250."""
    return guard(
        yahoo.screen,
        min_market_cap_cr=min_market_cap_cr, max_market_cap_cr=max_market_cap_cr, min_pe=min_pe, max_pe=max_pe,
        max_pb=max_pb, min_roe_pct=min_roe_pct, min_dividend_yield_pct=min_dividend_yield_pct,
        min_revenue_growth_pct=min_revenue_growth_pct, min_eps_growth_pct=min_eps_growth_pct,
        min_52w_change_pct=min_52w_change_pct, max_52w_change_pct=max_52w_change_pct, min_price=min_price,
        max_price=max_price, min_avg_volume=min_avg_volume, sector=sector, industry=industry, sort_by=sort_by,
        ascending=ascending, limit=limit,
    )


# ---------------------------------------------------------------- company research --------------

@mcp.tool()
def get_company_profile(symbol: str) -> dict:
    """Company overview for an NSE symbol: sector, industry (Yahoo and NSE classifications), market-cap
    bucket, index membership, business summary, employees, website, headline valuation."""
    return guard(yahoo.profile, symbol)


@mcp.tool()
def get_stock_quote(symbol: str) -> dict:
    """Latest quote for an NSE symbol: price, change, day range, volume vs 3-month average, 52-week range
    and distance from it, 50/200-day averages, market cap, PE, PB, dividend yield."""
    return guard(yahoo.quote, symbol)


@mcp.tool()
def get_price_history(symbol: str, period: str = "1y", interval: str = "1d", candles: int = 30) -> dict:
    """Historical OHLCV plus computed stats. period: 1mo 3mo 6mo 1y 2y 5y 10y max (or 5d/1d for intraday
    intervals). interval: 1d 1wk 1mo (intraday: 5m 15m 1h, max 60 days). Daily data also returns returns
    over 1w/1m/3m/6m/1y, SMA20/50/200, RSI14, max drawdown and annualised volatility. `candles` = how
    many most-recent bars to include (≤ 400)."""
    return guard(yahoo.price_history, symbol, period, interval, candles)


@mcp.tool()
def get_key_ratios(symbol: str) -> dict:
    """Valuation, profitability, growth, leverage, cash flow, per-share and ownership ratios for a stock."""
    return guard(yahoo.key_ratios, symbol)


@mcp.tool()
def get_financials(symbol: str, statement: str = "all", period: str = "annual") -> dict:
    """Financial statements in ₹ crore. statement: income | balance | cashflow | all.
    period: annual (last 4-5 fiscal years, FY ends 31 Mar) | quarterly (last 5 quarters)."""
    if statement not in ("income", "balance", "cashflow", "all"):
        return {"error": "statement must be income, balance, cashflow or all"}
    if period not in ("annual", "quarterly"):
        return {"error": "period must be annual or quarterly"}
    return guard(yahoo.financials, symbol, statement, period)


@mcp.tool()
def get_shareholding(symbol: str, quarters: int = 8) -> dict:
    """Promoter vs public shareholding trend from NSE filings (quarterly), plus Yahoo's
    insider/institution split. Rising promoter stake and falling pledge are usually read as positive."""

    def run():
        sym = symbols.normalize(symbol)
        rows = nse.shareholding(sym)
        if not rows:
            raise LookupError(f"No shareholding filings on NSE for {sym}.")
        rows = sorted(rows, key=lambda r: _parse_date(r.get("date")) or date.min, reverse=True)
        trend = []
        for r in rows[: max(1, min(quarters, 40))]:
            trend.append(
                {
                    "quarter_end": r.get("date"),
                    "promoter_pct": _f(r.get("pr_and_prgrp")),
                    "public_pct": _f(r.get("public_val")),
                    "employee_trusts_pct": _f(r.get("employeeTrusts")),
                    "filed_on": r.get("submissionDate"),
                    "filing_xbrl": r.get("xbrl"),
                }
            )
        out = {"symbol": sym, "name": rows[0].get("name"), "trend": trend}
        if len(trend) >= 2 and trend[0]["promoter_pct"] is not None and trend[-1]["promoter_pct"] is not None:
            out["promoter_change_over_period_pct_points"] = round(trend[0]["promoter_pct"] - trend[-1]["promoter_pct"], 2)
        try:
            i = yahoo.info(sym)
            out["yahoo_ownership_pct"] = {
                "insiders_promoters": yahoo.pct(i.get("heldPercentInsiders")),
                "institutions": yahoo.pct(i.get("heldPercentInstitutions")),
            }
        except Exception:
            pass
        return out

    return guard(run)


@mcp.tool()
def get_corporate_actions(symbol: str) -> dict:
    """Dividends, bonus, splits, rights and buybacks with ex-dates and record dates (NSE), plus upcoming
    and recent board meetings (results dates etc.)."""

    def run():
        sym = symbols.normalize(symbol)
        today = date.today()
        acts = [a for a in nse.corporate_actions(sym) if a.get("series") in (None, "EQ", "-")]
        acts.sort(key=lambda a: _parse_date(a.get("exDate")) or date.min, reverse=True)
        actions = [
            {"ex_date": a.get("exDate"), "record_date": a.get("recDate"), "subject": a.get("subject"), "face_value": a.get("faceVal"),
             "upcoming": bool((_parse_date(a.get("exDate")) or date.min) >= today)}
            for a in acts[:25]
        ]
        meets = nse.board_meetings(sym)
        meets.sort(key=lambda m: _parse_date(m.get("date")) or date.min, reverse=True)
        upcoming = [m for m in meets if (_parse_date(m.get("date")) or date.min) >= today]
        past = [m for m in meets if (_parse_date(m.get("date")) or date.min) < today][:3]
        fmt = lambda m: {"date": m.get("date"), "purpose": m.get("purpose"), "details": (m.get("bm_desc") or "")[:300]}
        return {"symbol": sym, "corporate_actions": actions, "upcoming_board_meetings": [fmt(m) for m in upcoming], "recent_board_meetings": [fmt(m) for m in past]}

    return guard(run)


@mcp.tool()
def get_announcements(symbol: str, limit: int = 15) -> dict:
    """Latest corporate announcements filed with NSE (results, orders, resignations, investor
    presentations…) with subject, short text and PDF link."""

    def run():
        sym = symbols.normalize(symbol)
        rows = nse.announcements(sym)
        if not rows:
            raise LookupError(f"No announcements on NSE for {sym}.")
        rows = sorted(rows, key=lambda r: r.get("sort_date") or "", reverse=True)
        out = []
        for r in rows[: max(1, min(limit, 100))]:
            out.append(
                {
                    "date": r.get("an_dt"),
                    "subject": r.get("desc"),
                    "text": (r.get("attchmntText") or "")[:400],
                    "attachment": r.get("attchmntFile"),
                }
            )
        return {"symbol": sym, "company": rows[0].get("sm_name"), "announcements": out}

    return guard(run)


@mcp.tool()
def get_analyst_view(symbol: str) -> dict:
    """Analyst consensus (buy/hold/sell), price targets with upside, recommendation trend over recent
    months, and past/upcoming earnings dates with EPS estimates and surprises."""
    return guard(yahoo.analyst_view, symbol)


@mcp.tool()
def compare_stocks(symbols_list: list[str]) -> dict:
    """Side-by-side key metrics for up to 15 NSE symbols: valuation (PE, PB, EV/EBITDA), ROE, margins,
    growth, leverage, dividend yield, 52-week performance and analyst consensus."""
    syms = [symbols.normalize(s) for s in symbols_list if s and s.strip()][:15]
    if not syms:
        return {"error": "Give at least one symbol."}
    return guard(lambda: {"count": len(syms), "stocks": yahoo.compare(syms)})


# ---------------------------------------------------------------- market wide -------------------

_KEY_INDICES = [
    "NIFTY 50", "NIFTY NEXT 50", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 100", "NIFTY BANK", "NIFTY IT",
    "NIFTY AUTO", "NIFTY PHARMA", "NIFTY FMCG", "NIFTY METAL", "NIFTY REALTY", "NIFTY ENERGY",
    "NIFTY FINANCIAL SERVICES", "INDIA VIX",
]


def _index_row(i: dict) -> dict:
    return {
        "index": i.get("index"),
        "group": i.get("key"),
        "last": num(i.get("last")),
        "change_pct": num(i.get("percentChange")),
        "open": num(i.get("open")),
        "high": num(i.get("high")),
        "low": num(i.get("low")),
        "previous_close": num(i.get("previousClose")),
        "year_high": num(i.get("yearHigh")),
        "year_low": num(i.get("yearLow")),
        "change_30d_pct": num(i.get("perChange30d")),
        "change_365d_pct": num(i.get("perChange365d")),
        "pe": num(i.get("pe")),
        "pb": num(i.get("pb")),
        "dividend_yield_pct": num(i.get("dy")),
        "advances": i.get("advances"),
        "declines": i.get("declines"),
    }


@mcp.tool()
def get_market_pulse() -> dict:
    """One-call market snapshot: market status, key broad and sectoral indices with % change and index
    PE/PB, India VIX, sector leaders and laggards today, and the latest FII/DII cash flows."""

    def run():
        status = [{"market": s.get("market"), "status": s.get("marketStatus"), "trade_date": s.get("tradeDate"), "message": s.get("marketStatusMessage")} for s in nse.market_status()]
        by_name = {i.get("index"): i for i in nse.all_indices()}
        key = [_index_row(by_name[n]) for n in _KEY_INDICES if n in by_name]
        sectoral = [_index_row(i) for i in by_name.values() if (i.get("key") or "").upper().startswith("SECTORAL")]
        sectoral = [s for s in sectoral if s["change_pct"] is not None]
        sectoral.sort(key=lambda s: s["change_pct"], reverse=True)
        out = {"market_status": status, "key_indices": key}
        if sectoral:
            out["sector_leaders"] = [{"index": s["index"], "change_pct": s["change_pct"]} for s in sectoral[:4]]
            out["sector_laggards"] = [{"index": s["index"], "change_pct": s["change_pct"]} for s in sectoral[-4:][::-1]]
        try:
            out["fii_dii_latest"] = [
                {"category": r.get("category"), "date": r.get("date"), "buy_cr": _f(r.get("buyValue")), "sell_cr": _f(r.get("sellValue")), "net_cr": _f(r.get("netValue"))}
                for r in nse.fii_dii()
            ]
        except Exception as e:
            out["fii_dii_latest"] = {"error": str(e)}
        return out

    return guard(run)


@mcp.tool()
def get_index_performance(group: str = "all") -> dict:
    """All NSE indices with last value, 1-day/30-day/1-year % change, PE, PB, dividend yield.
    group: all | broad | sectoral | thematic | strategy."""

    def run():
        rows = [_index_row(i) for i in nse.all_indices()]
        g = group.strip().lower()
        if g != "all":
            rows = [r for r in rows if (r.get("group") or "").lower().startswith(g)]
            if not rows:
                raise ValueError("group must be all, broad, sectoral, thematic or strategy")
        return {"count": len(rows), "indices": rows}

    return guard(run)


@mcp.tool()
def get_index_constituents(index: str = "NIFTY 50") -> dict:
    """Constituent stocks of an NSE index with industry labels (from NSE's official lists). Supports
    NIFTY 50/NEXT 50/100/200/500, MIDCAP 50/100/150, SMALLCAP 100/250, MICROCAP 250 and sectoral indices
    (BANK, IT, AUTO, PHARMA, FMCG, METAL, REALTY, ENERGY, FINANCIAL SERVICES, HEALTHCARE, OIL & GAS…)."""

    def run():
        rows = symbols.index_constituents(index)
        by_ind: dict[str, int] = {}
        for r in rows:
            by_ind[r["industry"]] = by_ind.get(r["industry"], 0) + 1
        return {"index": index.upper(), "count": len(rows), "industry_counts": dict(sorted(by_ind.items(), key=lambda x: -x[1])), "constituents": rows}

    return guard(run)


_MOVER_INDEX = {
    "NIFTY": "NIFTY", "NIFTY 50": "NIFTY", "NIFTY50": "NIFTY",
    "BANKNIFTY": "BANKNIFTY", "NIFTY BANK": "BANKNIFTY",
    "NIFTYNEXT50": "NIFTYNEXT50", "NIFTY NEXT 50": "NIFTYNEXT50",
    "FNO": "FOSec", "F&O": "FOSec", "FOSEC": "FOSec",
    "ALL": "allSec", "ALLSEC": "allSec",
    "ABOVE20": "SecGtr20", "SECGTR20": "SecGtr20", "BELOW20": "SecLwr20", "SECLWR20": "SecLwr20",
}


@mcp.tool()
def get_top_movers(index: str = "NIFTY", direction: str = "gainers", limit: int = 15) -> dict:
    """Today's top gainers or losers. index: NIFTY | BANKNIFTY | NIFTYNEXT50 | FNO (all F&O stocks) |
    ALL (all securities). direction: gainers | losers."""

    def run():
        code = _MOVER_INDEX.get(index.strip().upper())
        if not code:
            raise ValueError(f"index must be one of {sorted(set(_MOVER_INDEX))}")
        d = nse.movers("gainers" if direction.lower().startswith("g") else "losers")
        block = d.get(code) or {}
        rows = [
            {"symbol": r.get("symbol"), "ltp": num(r.get("ltp")), "change_pct": num(r.get("perChange")), "prev_close": num(r.get("prev_price")),
             "high": num(r.get("high_price")), "low": num(r.get("low_price")), "volume": r.get("trade_quantity"), "turnover_lakh": num(r.get("turnover"))}
            for r in (block.get("data") or [])[: max(1, min(limit, 100))]
        ]
        return {"index": code, "direction": direction, "as_of": block.get("timestamp"), "stocks": rows}

    return guard(run)


@mcp.tool()
def get_most_active(by: str = "value", limit: int = 15) -> dict:
    """Most actively traded stocks today by traded value or volume (NSE)."""

    def run():
        key = "value" if by.lower().startswith("val") else "volume"
        d = nse.most_active(key)
        rows = [
            {"symbol": r.get("symbol"), "price": num(r.get("lastPrice")), "change_pct": num(r.get("pChange")), "volume": r.get("totalTradedVolume"),
             "traded_value_cr": yahoo.cr(r.get("totalTradedValue")), "year_high": num(r.get("yearHigh")), "year_low": num(r.get("yearLow"))}
            for r in (d.get("data") or [])[: max(1, min(limit, 100))]
        ]
        return {"by": key, "as_of": d.get("timestamp"), "stocks": rows}

    return guard(run)


@mcp.tool()
def get_52_week_breakouts(which: str = "high", limit: int = 25) -> dict:
    """Stocks that hit a new 52-week high or low today (NSE). which: high | low."""

    def run():
        w = "high" if which.lower().startswith("h") else "low"
        d = nse.week52(w)
        rows = [
            {"symbol": r.get("symbol"), "name": r.get("comapnyName") or r.get("companyName"), "ltp": num(r.get("ltp")), "change_pct": num(r.get("pChange")),
             "new_52w_level": num(r.get("new52WHL")), "previous_52w_level": num(r.get("prev52WHL")), "previous_level_date": r.get("prevHLDate")}
            for r in (d.get("data") or [])[: max(1, min(limit, 200))]
        ]
        return {"which": w, "total_today": d.get(w), "stocks": rows}

    return guard(run)


@mcp.tool()
def get_fii_dii() -> dict:
    """Latest day's FII/FPI and DII cash-market buy, sell and net flows in ₹ crore (NSE)."""
    return guard(lambda: {"flows": [
        {"category": r.get("category"), "date": r.get("date"), "buy_cr": _f(r.get("buyValue")), "sell_cr": _f(r.get("sellValue")), "net_cr": _f(r.get("netValue"))}
        for r in nse.fii_dii()
    ]})


@mcp.tool()
def get_bulk_block_deals(limit: int = 20) -> dict:
    """Today's bulk deals, block deals and short-selling disclosures on NSE (who bought/sold what, at
    what price)."""

    def run():
        d = nse.large_deals()
        fmt = lambda r: {"symbol": r.get("symbol"), "name": r.get("name"), "client": r.get("clientName"), "side": r.get("buySell"), "quantity": _f(r.get("qty")), "avg_price": num(_f(r.get("watp"))), "date": r.get("date")}
        n = max(1, min(limit, 200))
        return {
            "as_on": d.get("as_on_date"),
            "bulk_deals": [fmt(r) for r in (d.get("BULK_DEALS_DATA") or [])[:n]],
            "block_deals": [fmt(r) for r in (d.get("BLOCK_DEALS_DATA") or [])[:n]],
            "short_deals": [fmt(r) for r in (d.get("SHORT_DEALS_DATA") or [])[:n]],
        }

    return guard(run)


@mcp.tool()
def get_option_chain(symbol: str = "NIFTY", expiry: str | None = None, strikes_around: int = 8) -> dict:
    """Option chain from NSE for an index (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY) or an F&O stock.
    Returns spot, expiry list, put-call ratio (OI), max-pain strike, top OI strikes for calls (resistance)
    and puts (support), and a compact table of strikes around the money with OI, OI change, IV, LTP,
    volume. expiry format DD-Mon-YYYY (e.g. 29-Sep-2026); defaults to the nearest expiry."""

    def run():
        sym = symbols.normalize(symbol)
        info = nse.option_contract_info(sym)
        expiries = info.get("expiryDates") or []
        if not expiries:
            raise LookupError(f"No option contracts on NSE for {sym}. Is it in the F&O segment?")
        exp = expiry or expiries[0]
        if exp not in expiries:
            raise ValueError(f"expiry must be one of: {', '.join(expiries)}")
        chain = nse.option_chain(sym, exp)
        rec = chain.get("filtered") or chain.get("records") or {}
        data = rec.get("data") or (chain.get("records") or {}).get("data") or []
        spot = (chain.get("records") or {}).get("underlyingValue") or rec.get("underlyingValue")
        rows = []
        for d in data:
            ce, pe = d.get("CE") or {}, d.get("PE") or {}
            rows.append(
                {"strike": d.get("strikePrice"), "ce_oi": ce.get("openInterest", 0), "ce_oi_change": ce.get("changeinOpenInterest", 0), "ce_iv": ce.get("impliedVolatility"),
                 "ce_ltp": ce.get("lastPrice"), "ce_volume": ce.get("totalTradedVolume", 0), "pe_oi": pe.get("openInterest", 0), "pe_oi_change": pe.get("changeinOpenInterest", 0),
                 "pe_iv": pe.get("impliedVolatility"), "pe_ltp": pe.get("lastPrice"), "pe_volume": pe.get("totalTradedVolume", 0)}
            )
        rows = [r for r in rows if r["strike"] is not None]
        rows.sort(key=lambda r: r["strike"])
        if not rows:
            raise LookupError("NSE returned an empty chain; try again in a moment.")
        tot_ce = sum(r["ce_oi"] or 0 for r in rows)
        tot_pe = sum(r["pe_oi"] or 0 for r in rows)
        pcr = round(tot_pe / tot_ce, 2) if tot_ce else None
        strikes = [r["strike"] for r in rows]
        pain = None
        best = None
        for s in strikes:
            loss = sum((r["ce_oi"] or 0) * max(0, s - r["strike"]) + (r["pe_oi"] or 0) * max(0, r["strike"] - s) for r in rows)
            if best is None or loss < best:
                best, pain = loss, s
        atm_idx = min(range(len(rows)), key=lambda k: abs(rows[k]["strike"] - (spot or 0))) if spot else len(rows) // 2
        w = max(1, min(strikes_around, 30))
        window = rows[max(0, atm_idx - w): atm_idx + w + 1]
        top_ce = sorted(rows, key=lambda r: -(r["ce_oi"] or 0))[:5]
        top_pe = sorted(rows, key=lambda r: -(r["pe_oi"] or 0))[:5]
        atm = rows[atm_idx]
        return {
            "symbol": sym, "expiry": exp, "available_expiries": expiries[:8], "spot": spot, "as_of": (chain.get("records") or {}).get("timestamp"),
            "pcr_oi": pcr, "total_call_oi": tot_ce, "total_put_oi": tot_pe, "max_pain_strike": pain, "atm_strike": atm["strike"],
            "atm_iv": {"call": atm["ce_iv"], "put": atm["pe_iv"]},
            "call_oi_walls_resistance": [{"strike": r["strike"], "oi": r["ce_oi"]} for r in top_ce],
            "put_oi_walls_support": [{"strike": r["strike"], "oi": r["pe_oi"]} for r in top_pe],
            "chain": window,
        }

    return guard(run)


@mcp.tool()
def get_market_holidays() -> dict:
    """NSE equity (capital market) trading holidays for the current year, upcoming ones first."""

    def run():
        d = nse.holidays()
        rows = d.get("CM") or []
        today = date.today()
        fmt = lambda r: {"date": r.get("tradingDate"), "day": r.get("weekDay"), "description": r.get("description")}
        upcoming = [fmt(r) for r in rows if (_parse_date(r.get("tradingDate")) or date.min) >= today]
        past = [fmt(r) for r in rows if (_parse_date(r.get("tradingDate")) or date.min) < today]
        return {"upcoming": upcoming, "past_this_year": past}

    return guard(run)


# ---------------------------------------------------------------- watchlist ---------------------

def _load_watchlist() -> list[str]:
    try:
        return json.loads(WATCHLIST.read_text())
    except Exception:
        return []


@mcp.tool()
def manage_watchlist(action: str = "get", symbols_list: list[str] | None = None) -> dict:
    """Personal watchlist stored locally (~/.config/nse-research/watchlist.json).
    action: get (returns symbols with live quotes) | add | remove | clear."""

    def run():
        wl = _load_watchlist()
        act = action.lower()
        syms = [symbols.normalize(s) for s in (symbols_list or []) if s and s.strip()]
        if act == "add":
            for s in syms:
                if s not in wl:
                    wl.append(s)
        elif act == "remove":
            wl = [s for s in wl if s not in syms]
        elif act == "clear":
            wl = []
        elif act != "get":
            raise ValueError("action must be get, add, remove or clear")
        if act != "get":
            HOME.mkdir(parents=True, exist_ok=True)
            WATCHLIST.write_text(json.dumps(wl, indent=2))
        out = {"action": act, "symbols": wl, "path": str(WATCHLIST)}
        if act == "get" and wl:
            out["quotes"] = yahoo.compare(wl[:30])
        return out

    return guard(run)


def _self_check() -> int:
    """`nse-research-mcp --check`: warm the environment and verify both data sources. Exit 0 on success."""
    from . import __version__

    print(f"nse-research-mcp {__version__}: {len(mcp._tool_manager.list_tools())} tools registered")
    ok = True
    try:
        q = yahoo.quote("TCS")
        print(f"Yahoo Finance: OK  (TCS {q['price']} as of {q['as_of']})")
    except Exception as e:
        ok = False
        print(f"Yahoo Finance: FAILED ({e})")
    try:
        st = nse.market_status()
        cm = next((s for s in st if s.get("market") == "Capital Market"), st[0] if st else {})
        print(f"NSE India: OK  (Capital Market is {cm.get('marketStatus')})")
    except Exception as e:
        ok = False
        print(f"NSE India: FAILED ({e})")
    print("READY" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def main() -> None:
    if "--version" in sys.argv:
        from . import __version__

        print(__version__)
        return
    if "--check" in sys.argv:
        sys.exit(_self_check())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
