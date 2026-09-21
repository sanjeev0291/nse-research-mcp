"""Live smoke test: calls every tool function directly (no MCP transport) and checks shapes.
Run: uv run python tests/smoke.py   (needs internet; NSE may throttle, failures print but continue)"""
from __future__ import annotations

import json
import sys
import time

from nse_research_mcp import server as S

CHECKS = [
    ("search_stocks", lambda: S.search_stocks("tata motors", 5), lambda r: isinstance(r, list) and r and "symbol" in r[0]),
    ("get_company_profile", lambda: S.get_company_profile("RELIANCE"), lambda r: r.get("sector") and r.get("market_cap_cr")),
    ("get_stock_quote", lambda: S.get_stock_quote("tcs"), lambda r: r.get("price") and r.get("week52_high")),
    ("get_price_history", lambda: S.get_price_history("INFY", "1y", "1d", 5), lambda r: r.get("bars", 0) > 200 and r["technicals"]["sma200"] and len(r["candles"]) == 5),
    ("get_price_history weekly", lambda: S.get_price_history("HDFCBANK", "6mo", "1wk", 3), lambda r: r.get("bars", 0) > 15),
    ("get_key_ratios", lambda: S.get_key_ratios("M&M"), lambda r: r["valuation"]["pe_ttm"] and r["profitability_pct"]["net_margin"] is not None and "per_share" in r),
    ("get_financials annual", lambda: S.get_financials("RELIANCE", "all", "annual"), lambda r: r["income_statement"]["rows"].get("Total Revenue") and r["balance_sheet"]["rows"].get("Total Assets")),
    ("get_financials quarterly", lambda: S.get_financials("TCS", "income", "quarterly"), lambda r: len(r["income_statement"]["periods"]) >= 4),
    ("get_shareholding", lambda: S.get_shareholding("RELIANCE", 4), lambda r: r["trend"][0]["promoter_pct"] and len(r["trend"]) == 4),
    ("get_corporate_actions", lambda: S.get_corporate_actions("RELIANCE"), lambda r: r["corporate_actions"] and "subject" in r["corporate_actions"][0]),
    ("get_announcements", lambda: S.get_announcements("RELIANCE", 3), lambda r: len(r["announcements"]) == 3 and r["announcements"][0]["subject"]),
    ("get_analyst_view", lambda: S.get_analyst_view("INFY"), lambda r: r.get("analysts") and r.get("target_mean")),
    ("compare_stocks", lambda: S.compare_stocks(["TCS", "INFY", "WIPRO", "HCLTECH"]), lambda r: r["count"] == 4 and all(s.get("pe_ttm") for s in r["stocks"])),
    ("screen_stocks", lambda: S.screen_stocks(min_market_cap_cr=20000, max_pe=15, min_roe_pct=15, sort_by="market_cap", limit=10), lambda r: r["returned"] > 0 and r["results"][0]["symbol"]),
    ("screen_stocks sector", lambda: S.screen_stocks(sector="Technology", min_market_cap_cr=50000, sort_by="change_52w", limit=5), lambda r: r["returned"] > 0),
    ("get_market_pulse", lambda: S.get_market_pulse(), lambda r: r["key_indices"] and r["key_indices"][0]["index"] == "NIFTY 50"),
    ("get_index_performance sectoral", lambda: S.get_index_performance("sectoral"), lambda r: r["count"] > 5),
    ("get_index_constituents", lambda: S.get_index_constituents("NIFTY 50"), lambda r: r["count"] == 50 and r["industry_counts"]),
    ("get_index_constituents bank", lambda: S.get_index_constituents("NIFTY BANK"), lambda r: r["count"] >= 10),
    ("get_top_movers", lambda: S.get_top_movers("NIFTY", "losers", 5), lambda r: len(r["stocks"]) == 5 and r["stocks"][0]["change_pct"] is not None),
    ("get_most_active", lambda: S.get_most_active("volume", 5), lambda r: len(r["stocks"]) == 5),
    ("get_52_week_breakouts", lambda: S.get_52_week_breakouts("high", 5), lambda r: "stocks" in r),
    ("get_fii_dii", lambda: S.get_fii_dii(), lambda r: len(r["flows"]) == 2 and r["flows"][0]["net_cr"] is not None),
    ("get_bulk_block_deals", lambda: S.get_bulk_block_deals(3), lambda r: "bulk_deals" in r),
    ("get_option_chain NIFTY", lambda: S.get_option_chain("NIFTY", None, 3), lambda r: r.get("pcr_oi") and r.get("max_pain_strike") and len(r["chain"]) == 7),
    ("get_option_chain stock", lambda: S.get_option_chain("RELIANCE", None, 2), lambda r: r.get("spot") and r["chain"]),
    ("get_market_holidays", lambda: S.get_market_holidays(), lambda r: "upcoming" in r),
    ("manage_watchlist add", lambda: S.manage_watchlist("add", ["TCS", "INFY.NS"]), lambda r: "TCS" in r["symbols"] and "INFY" in r["symbols"]),
    ("manage_watchlist get", lambda: S.manage_watchlist("get"), lambda r: r.get("quotes") and r["quotes"][0]["price"]),
    ("manage_watchlist remove", lambda: S.manage_watchlist("remove", ["TCS", "INFY"]), lambda r: "TCS" not in r["symbols"]),
    ("unknown symbol -> error", lambda: S.get_stock_quote("NOSUCHSTOCKXYZ"), lambda r: "error" in r),
    ("bad index -> error", lambda: S.get_index_constituents("NIFTY BOGUS"), lambda r: "error" in r),
]


def main() -> int:
    failures = 0
    for name, call, check in CHECKS:
        t0 = time.time()
        try:
            r = call()
            ok = bool(check(r))
        except Exception as e:  # pragma: no cover
            r, ok = {"exception": repr(e)}, False
        dt = f"{time.time() - t0:4.1f}s"
        if ok:
            print(f"PASS {dt} {name}")
        else:
            failures += 1
            print(f"FAIL {dt} {name}: {json.dumps(r, default=str)[:300]}")
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
