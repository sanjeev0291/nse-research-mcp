---
name: portfolio-review
description: Review the user's Zerodha portfolio through the Kite MCP (holdings, positions, margins) and enrich it with the self-hosted nse-research MCP (sector, valuation, analyst view, technicals). Use when the user asks to analyze, review, or summarize their stocks, holdings, portfolio, or P&L, or asks how their portfolio is doing. Read-only; never places orders.
---

# Portfolio review (Zerodha Kite MCP + nse-research MCP)

Arguments: `$ARGUMENTS`
- empty → full review
- `quick` → Kite data only, skip research enrichment
- a symbol (e.g. `RELIANCE`) → deep-dive on that one holding

## 1. Get a Kite session

- In a new conversation, call `mcp__kite__login` first. It returns a warning and a login URL.
- Show the warning verbatim and the URL as a clickable link. Ask the user to log in with Kite 2FA, then wait for them to say they are done. Do not poll.
- Kite sessions expire every morning (around 6 AM IST). A "Please log in first" error at any point means: call `login` again.
- If the Kite MCP is not connected at all, say so and offer to review a list of symbols the user pastes instead.

## 2. Pull account data (Kite)

Call, paginating where the tool supports it:
- `mcp__kite__get_holdings` (long-term equity holdings: average_price, last_price, quantity, pnl, day_change_percentage)
- `mcp__kite__get_positions` (intraday / F&O positions, may be empty)
- `mcp__kite__get_margins` (available cash)
- `mcp__kite__get_mf_holdings` only for a full review

Compute with a small Python snippet (never guess arithmetic):
- invested = Σ avg_price × qty; current = Σ last_price × qty; overall P&L ₹ and %
- day P&L ₹ from day change
- weight of each holding = current value / total current value
- top 5 by weight, top 3 winners and losers by % P&L
- concentration: largest single weight, combined weight of top 3
- number of holdings under 2% weight (long tail)

## 3. Enrich with research (nse-research) — skip if `quick`

These tools are local and free, so enrich every holding, not just the top few:
- `mcp__nse-research__compare_stocks` with all holding symbols (batches of 15) → sector, NSE industry, PE, PB, ROE, growth, 52-week change, % from 52-week high, analyst consensus, in one call per batch.
- `mcp__nse-research__get_market_pulse` once → index moves, VIX, FII/DII, sector leaders and laggards for context.
- For the top 3 holdings by weight, `mcp__nse-research__get_price_history` (1y, daily, 5 candles) → RSI, SMA50/200, drawdown.

Then compute sector allocation from the weights and the `sector` / `nse_industry` fields. If nse-research is unavailable or a symbol has no data, say so in one line and continue. Never let a failed enrichment block the review.

## 4. Report

Keep it tight. Use these sections, tables where the data is tabular:
1. Summary: invested, current value, overall P&L (₹ and %), today's P&L, available cash. One line on when the data was fetched and whether the market is open (NSE: 9:15 to 15:30 IST, Mon–Fri).
2. Holdings by weight: symbol, qty, avg, LTP, value, weight %, P&L %, PE, % from 52w high.
3. Sector allocation.
4. Winners and losers.
5. Observations: concentration risk, sector tilt, long tail of tiny positions, holdings down more than 20% from average cost, stocks far below their 200-day average, anything unusual. Facts first, then at most three things worth the user's attention.

Symbol deep-dive (when `$ARGUMENTS` is a symbol): the holding's numbers from Kite, then from nse-research: `get_stock_quote`, `get_price_history` (1y daily), `get_key_ratios`, `get_financials` (quarterly income), `get_shareholding`, `get_analyst_view`, `get_corporate_actions`, and the latest 5 `get_announcements`. Summarise what changed recently and how the stock is valued versus its own history and peers (`compare_stocks` with 3–4 peers from the same NSE industry, found via `get_index_constituents`).

## Rules

- Read-only. Never call `place_order`, `modify_order`, `cancel_order`, `place_gtt_order`, `modify_gtt_order`, or `delete_gtt_order` from this skill. If the user asks to trade, point them to the Kite tools and their permission settings and let them decide.
- This is analysis of the user's own data, not investment advice. Do not recommend buying or selling; describe what the numbers show.
- Never print the Kite login URL's session token anywhere other than the link the user clicks.
