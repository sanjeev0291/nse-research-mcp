"""Yahoo Finance (via yfinance) for NSE-listed tickers. Covers what NSE blocks for bots:
quotes, price history, fundamentals, statements, analyst data and server-side screening."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery

from . import indicators as ind
from .cache import ttl_cache
from .symbols import membership, normalize, yahoo_ticker

CRORE = 1e7


def num(v, nd: int = 2):
    """JSON-safe number: NaN/inf -> None, floats rounded."""
    try:
        if v is None:
            return None
        if isinstance(v, pd.Timestamp):
            return str(v.date())
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, nd)
    except Exception:
        return v


def cr(v):
    """Absolute rupees -> rupee crore."""
    n = num(v, 4)
    return None if n is None else round(n / CRORE, 2)


def pct(v):
    """Fraction -> percent."""
    n = num(v, 6)
    return None if n is None else round(n * 100, 2)


def ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(yahoo_ticker(symbol))


@ttl_cache(600)
def info(symbol: str) -> dict:
    try:
        i = ticker(symbol).info or {}
    except Exception as e:
        raise LookupError(f"Yahoo Finance has no data for {yahoo_ticker(symbol)} ({e}). Check the symbol with search_stocks.")
    if not (i.get("regularMarketPrice") or i.get("longName") or i.get("shortName")):
        raise LookupError(f"Yahoo Finance has no data for {yahoo_ticker(symbol)}. Check the symbol with search_stocks.")
    return i


def _ts(epoch):
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone().isoformat(timespec="minutes")
    except Exception:
        return None


def quote(symbol: str) -> dict:
    i = info(symbol)
    price = i.get("regularMarketPrice") or i.get("currentPrice")
    prev = i.get("regularMarketPreviousClose") or i.get("previousClose")
    change = (price - prev) if price is not None and prev else None
    hi, lo = i.get("fiftyTwoWeekHigh"), i.get("fiftyTwoWeekLow")
    avg_vol = i.get("averageVolume")
    vol = i.get("regularMarketVolume") or i.get("volume")
    return {
        "symbol": normalize(symbol),
        "name": i.get("longName") or i.get("shortName"),
        "price": num(price),
        "previous_close": num(prev),
        "change": num(change),
        "change_pct": num(change / prev * 100) if change is not None and prev else None,
        "open": num(i.get("regularMarketOpen") or i.get("open")),
        "day_low": num(i.get("regularMarketDayLow") or i.get("dayLow")),
        "day_high": num(i.get("regularMarketDayHigh") or i.get("dayHigh")),
        "volume": num(vol, 0),
        "avg_volume_3m": num(avg_vol, 0),
        "volume_vs_avg_pct": num(vol / avg_vol * 100) if vol and avg_vol else None,
        "week52_high": num(hi),
        "week52_low": num(lo),
        "pct_from_52w_high": num((price / hi - 1) * 100) if price and hi else None,
        "pct_above_52w_low": num((price / lo - 1) * 100) if price and lo else None,
        "sma50": num(i.get("fiftyDayAverage")),
        "sma200": num(i.get("twoHundredDayAverage")),
        "market_cap_cr": cr(i.get("marketCap")),
        "pe_ttm": num(i.get("trailingPE")),
        "pb": num(i.get("priceToBook")),
        "dividend_yield_pct": num(i.get("dividendYield")),
        "market_state": i.get("marketState"),
        "as_of": _ts(i.get("regularMarketTime")),
        "currency": i.get("currency", "INR"),
        "source": "Yahoo Finance (NSE feed)",
    }


def profile(symbol: str) -> dict:
    i = info(symbol)
    sym = normalize(symbol)
    m = membership().get(sym, {})
    summary = i.get("longBusinessSummary") or ""
    if len(summary) > 700:
        summary = summary[:700].rsplit(" ", 1)[0] + " …"
    return {
        "symbol": sym,
        "name": i.get("longName") or i.get("shortName"),
        "sector": i.get("sector"),
        "industry": i.get("industry"),
        "nse_industry": m.get("industry"),
        "cap_bucket": m.get("cap_bucket"),
        "index_membership": m.get("indices", []),
        "market_cap_cr": cr(i.get("marketCap")),
        "employees": i.get("fullTimeEmployees"),
        "website": i.get("website"),
        "headquarters": ", ".join(x for x in [i.get("city"), i.get("state"), i.get("country")] if x) or None,
        "summary": summary or None,
        "price": num(i.get("regularMarketPrice") or i.get("currentPrice")),
        "pe_ttm": num(i.get("trailingPE")),
        "pb": num(i.get("priceToBook")),
        "dividend_yield_pct": num(i.get("dividendYield")),
        "beta": num(i.get("beta")),
    }


def key_ratios(symbol: str) -> dict:
    i = info(symbol)
    return {
        "symbol": normalize(symbol),
        "name": i.get("longName") or i.get("shortName"),
        "price": num(i.get("regularMarketPrice") or i.get("currentPrice")),
        "valuation": {
            "market_cap_cr": cr(i.get("marketCap")),
            "enterprise_value_cr": cr(i.get("enterpriseValue")),
            "pe_ttm": num(i.get("trailingPE")),
            "pe_forward": num(i.get("forwardPE")),
            "peg": num(i.get("trailingPegRatio")),
            "pb": num(i.get("priceToBook")),
            "price_to_sales_ttm": num(i.get("priceToSalesTrailing12Months")),
            "ev_to_ebitda": num(i.get("enterpriseToEbitda")),
            "ev_to_revenue": num(i.get("enterpriseToRevenue")),
        },
        "profitability_pct": {
            "gross_margin": pct(i.get("grossMargins")),
            "operating_margin": pct(i.get("operatingMargins")),
            "ebitda_margin": pct(i.get("ebitdaMargins")),
            "net_margin": pct(i.get("profitMargins")),
            "roe": pct(i.get("returnOnEquity")),
            "roa": pct(i.get("returnOnAssets")),
        },
        "growth_pct": {
            "revenue_growth_yoy": pct(i.get("revenueGrowth")),
            "earnings_growth_yoy": pct(i.get("earningsGrowth")),
            "earnings_growth_quarterly": pct(i.get("earningsQuarterlyGrowth")),
        },
        "balance_sheet": {
            "total_debt_cr": cr(i.get("totalDebt")),
            "total_cash_cr": cr(i.get("totalCash")),
            "debt_to_equity_pct": num(i.get("debtToEquity")),
            "current_ratio": num(i.get("currentRatio")),
            "quick_ratio": num(i.get("quickRatio")),
            "book_value_per_share": num(i.get("bookValue")),
        },
        "cash_flow": {
            "operating_cash_flow_cr": cr(i.get("operatingCashflow")),
            "free_cash_flow_cr": cr(i.get("freeCashflow")),
        },
        "per_share": {
            "eps_ttm": num(i.get("trailingEps")),
            "eps_forward": num(i.get("forwardEps")),
            "dividend_per_share": num(i.get("dividendRate")),
            "dividend_yield_pct": num(i.get("dividendYield")),
            "payout_ratio_pct": pct(i.get("payoutRatio")),
        },
        "ownership_pct": {
            "insiders_promoters": pct(i.get("heldPercentInsiders")),
            "institutions": pct(i.get("heldPercentInstitutions")),
        },
        "risk": {
            "beta": num(i.get("beta")),
            "week52_change_pct": pct(i.get("52WeekChange")),
        },
        "note": "Yahoo Finance data. Margins/ROE are trailing twelve months; debt_to_equity is in percent (100 = 1x).",
    }


def price_history(symbol: str, period: str = "1y", interval: str = "1d", candles: int = 30) -> dict:
    h = ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    if h is None or h.empty:
        raise LookupError(f"No price history for {yahoo_ticker(symbol)} (period={period}, interval={interval}).")
    close = h["Close"].dropna()
    last = float(close.iloc[-1])
    daily = interval == "1d"
    stats = {
        "symbol": normalize(symbol),
        "period": period,
        "interval": interval,
        "bars": int(len(h)),
        "from": str(h.index[0].date()),
        "to": str(h.index[-1].date()),
        "last_close": num(last),
        "period_return_pct": num((last / float(close.iloc[0]) - 1) * 100),
        "high": num(float(h["High"].max())),
        "low": num(float(h["Low"].min())),
        "pct_from_period_high": num((last / float(h["High"].max()) - 1) * 100),
        "avg_volume": num(float(h["Volume"].tail(20).mean()), 0),
    }
    if daily:
        stats["returns_pct"] = {
            "1w": num(ind.pct_return(close, 5)),
            "1m": num(ind.pct_return(close, 21)),
            "3m": num(ind.pct_return(close, 63)),
            "6m": num(ind.pct_return(close, 126)),
            "1y": num(ind.pct_return(close, 252)),
        }
        stats["technicals"] = {
            "sma20": num(ind.sma(close, 20)),
            "sma50": num(ind.sma(close, 50)),
            "sma200": num(ind.sma(close, 200)),
            "rsi14": num(ind.rsi(close, 14)),
            "max_drawdown_pct": num(ind.max_drawdown(close)),
            "annualized_volatility_pct": num(ind.annualized_volatility(close)),
        }
    tail = h.tail(max(0, min(candles, 400)))
    stats["candles"] = [
        {
            "date": str(idx.date()) if daily or interval.endswith(("d", "wk", "mo")) else idx.isoformat(),
            "open": num(r["Open"]),
            "high": num(r["High"]),
            "low": num(r["Low"]),
            "close": num(r["Close"]),
            "volume": num(r["Volume"], 0),
        }
        for idx, r in tail.iterrows()
    ]
    stats["note"] = "Adjusted for splits/bonus/dividends (Yahoo auto_adjust)."
    return stats


_INCOME = [
    ("Total Revenue", "Total Revenue"),
    ("Operating Income", "Operating Income"),
    ("EBITDA", "EBITDA"),
    ("EBIT", "EBIT"),
    ("Interest Expense", "Interest Expense"),
    ("Pretax Income", "Pretax Income"),
    ("Tax Provision", "Tax Provision"),
    ("Net Income", "Net Income"),
    ("Diluted EPS", "Diluted EPS"),
]
_BALANCE = [
    ("Total Assets", "Total Assets"),
    ("Total Liabilities", "Total Liabilities Net Minority Interest"),
    ("Stockholders Equity", "Stockholders Equity"),
    ("Total Debt", "Total Debt"),
    ("Net Debt", "Net Debt"),
    ("Cash And Equivalents", "Cash And Cash Equivalents"),
    ("Working Capital", "Working Capital"),
    ("Shares Outstanding", "Ordinary Shares Number"),
]
_CASHFLOW = [
    ("Operating Cash Flow", "Operating Cash Flow"),
    ("Capital Expenditure", "Capital Expenditure"),
    ("Free Cash Flow", "Free Cash Flow"),
    ("Investing Cash Flow", "Investing Cash Flow"),
    ("Financing Cash Flow", "Financing Cash Flow"),
    ("Dividends Paid", "Cash Dividends Paid"),
    ("End Cash Position", "End Cash Position"),
]


def _statement(df: pd.DataFrame | None, rows, max_periods: int = 5):
    if df is None or df.empty:
        return None
    cols = list(df.columns)[:max_periods]
    out = {"periods": [str(c.date()) if hasattr(c, "date") else str(c) for c in cols], "rows": {}}
    for label, key in rows:
        if key in df.index:
            vals = df.loc[key, cols].tolist()
            keep_raw = "EPS" in key or "Shares" in key
            out["rows"][label] = [num(v) if keep_raw else cr(v) for v in vals]
    return out


def financials(symbol: str, statement: str = "all", period: str = "annual") -> dict:
    t = ticker(symbol)
    q = period == "quarterly"
    out = {"symbol": normalize(symbol), "period": period, "unit": "₹ crore, except EPS (₹) and share counts"}
    if statement in ("income", "all"):
        out["income_statement"] = _statement(t.quarterly_income_stmt if q else t.income_stmt, _INCOME)
    if statement in ("balance", "all"):
        out["balance_sheet"] = _statement(t.quarterly_balance_sheet if q else t.balance_sheet, _BALANCE)
    if statement in ("cashflow", "all"):
        out["cash_flow"] = _statement(t.quarterly_cashflow if q else t.cashflow, _CASHFLOW)
    if all(out.get(k) is None for k in ("income_statement", "balance_sheet", "cash_flow") if k in out):
        raise LookupError(f"No financial statements on Yahoo for {yahoo_ticker(symbol)}.")
    return out


def analyst_view(symbol: str) -> dict:
    t = ticker(symbol)
    i = info(symbol)
    price = i.get("regularMarketPrice") or i.get("currentPrice")
    target = i.get("targetMeanPrice")
    out = {
        "symbol": normalize(symbol),
        "consensus": i.get("recommendationKey"),
        "consensus_score_1_to_5": num(i.get("recommendationMean")),
        "analysts": i.get("numberOfAnalystOpinions"),
        "target_mean": num(target),
        "target_median": num(i.get("targetMedianPrice")),
        "target_low": num(i.get("targetLowPrice")),
        "target_high": num(i.get("targetHighPrice")),
        "upside_to_mean_target_pct": num((target / price - 1) * 100) if target and price else None,
    }
    try:
        rec = t.recommendations
        if rec is not None and not rec.empty:
            out["recommendation_trend"] = rec.head(4).to_dict("records")
    except Exception:
        pass
    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            rows = []
            for idx, r in ed.head(6).iterrows():
                rows.append({"date": str(idx.date()), "eps_estimate": num(r.get("EPS Estimate")), "reported_eps": num(r.get("Reported EPS")), "surprise_pct": num(r.get("Surprise(%)"))})
            out["earnings_dates"] = rows
    except Exception:
        pass
    return out


def _compare_one(symbol: str) -> dict:
    try:
        i = info(symbol)
    except Exception as e:
        return {"symbol": normalize(symbol), "error": str(e)}
    price = i.get("regularMarketPrice") or i.get("currentPrice")
    m = membership().get(normalize(symbol), {})
    return {
        "symbol": normalize(symbol),
        "name": i.get("shortName") or i.get("longName"),
        "sector": i.get("sector"),
        "nse_industry": m.get("industry"),
        "price": num(price),
        "market_cap_cr": cr(i.get("marketCap")),
        "pe_ttm": num(i.get("trailingPE")),
        "pe_forward": num(i.get("forwardPE")),
        "pb": num(i.get("priceToBook")),
        "ev_ebitda": num(i.get("enterpriseToEbitda")),
        "roe_pct": pct(i.get("returnOnEquity")),
        "net_margin_pct": pct(i.get("profitMargins")),
        "revenue_growth_pct": pct(i.get("revenueGrowth")),
        "earnings_growth_pct": pct(i.get("earningsGrowth")),
        "debt_to_equity_pct": num(i.get("debtToEquity")),
        "dividend_yield_pct": num(i.get("dividendYield")),
        "week52_change_pct": pct(i.get("52WeekChange")),
        "pct_from_52w_high": num((price / i["fiftyTwoWeekHigh"] - 1) * 100) if price and i.get("fiftyTwoWeekHigh") else None,
        "beta": num(i.get("beta")),
        "analyst_consensus": i.get("recommendationKey"),
    }


def compare(symbols: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_compare_one, symbols))


_SORT = {
    "market_cap": "intradaymarketcap",
    "pe": "peratio.lasttwelvemonths",
    "dividend_yield": "forward_dividend_yield",
    "change_1d": "percentchange",
    "change_52w": "fiftytwowkpercentchange",
    "volume": "dayvolume",
    "price": "intradayprice",
}

YAHOO_SECTORS = [
    "Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy",
    "Financial Services", "Healthcare", "Industrials", "Real Estate", "Technology", "Utilities",
]


def screen(
    min_market_cap_cr=None, max_market_cap_cr=None, min_pe=None, max_pe=None, max_pb=None, min_roe_pct=None,
    min_dividend_yield_pct=None, min_revenue_growth_pct=None, min_eps_growth_pct=None, min_52w_change_pct=None,
    max_52w_change_pct=None, min_price=None, max_price=None, min_avg_volume=None, sector=None, industry=None,
    sort_by="market_cap", ascending=False, limit=25,
) -> dict:
    q = [EquityQuery("eq", ["exchange", "NSI"])]

    def add(op, field, value):
        if value is not None:
            q.append(EquityQuery(op, [field, value]))

    add("gt", "intradaymarketcap", None if min_market_cap_cr is None else float(min_market_cap_cr) * CRORE)
    add("lt", "intradaymarketcap", None if max_market_cap_cr is None else float(max_market_cap_cr) * CRORE)
    add("gt", "peratio.lasttwelvemonths", min_pe)
    add("lt", "peratio.lasttwelvemonths", max_pe)
    add("lt", "pricebookratio.quarterly", max_pb)
    add("gt", "returnonequity.lasttwelvemonths", min_roe_pct)
    add("gt", "forward_dividend_yield", min_dividend_yield_pct)
    add("gt", "quarterlyrevenuegrowth.quarterly", min_revenue_growth_pct)
    add("gt", "epsgrowth.lasttwelvemonths", min_eps_growth_pct)
    add("gt", "fiftytwowkpercentchange", min_52w_change_pct)
    add("lt", "fiftytwowkpercentchange", max_52w_change_pct)
    add("gt", "intradayprice", min_price)
    add("lt", "intradayprice", max_price)
    add("gt", "avgdailyvol3m", min_avg_volume)
    if sector:
        q.append(EquityQuery("eq", ["sector", sector]))
    if industry:
        q.append(EquityQuery("eq", ["industry", industry]))
    if sort_by not in _SORT:
        raise ValueError(f"sort_by must be one of {', '.join(_SORT)}")
    size = max(1, min(int(limit or 25), 250))
    res = yf.screen(EquityQuery("and", q), sortField=_SORT[sort_by], sortAsc=bool(ascending), size=size)
    members = membership()
    rows = []
    for x in res.get("quotes", []):
        sym = normalize(x.get("symbol", ""))
        price, hi = x.get("regularMarketPrice"), x.get("fiftyTwoWeekHigh")
        m = members.get(sym, {})
        rows.append(
            {
                "symbol": sym,
                "name": x.get("longName") or x.get("shortName"),
                "price": num(price),
                "change_1d_pct": num(x.get("regularMarketChangePercent")),
                "market_cap_cr": cr(x.get("marketCap")),
                "pe_ttm": num(x.get("trailingPE")),
                "pe_forward": num(x.get("forwardPE")),
                "pb": num(x.get("priceToBook")),
                "dividend_yield_pct": num(x.get("dividendYield")),
                "eps_ttm": num(x.get("epsTrailingTwelveMonths")),
                "change_52w_pct": num(x.get("fiftyTwoWeekChangePercent")),
                "pct_from_52w_high": num((price / hi - 1) * 100) if price and hi else None,
                "avg_volume_3m": num(x.get("averageDailyVolume3Month"), 0),
                "analyst_rating": x.get("averageAnalystRating"),
                "nse_industry": m.get("industry"),
                "cap_bucket": m.get("cap_bucket"),
            }
        )
    return {
        "total_matches": res.get("total"),
        "returned": len(rows),
        "sort_by": sort_by,
        "results": rows,
        "note": "Yahoo Finance screener over NSE-listed stocks. Ratios are trailing twelve months unless stated.",
    }
