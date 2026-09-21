# nse-research-mcp

Indian stock market research for Claude (or any MCP client), **self-hosted**. Quotes, fundamentals,
financial statements, a screener, shareholding, corporate actions, announcements, FII/DII flows, option
chains and index data, pulled straight from **NSE India** and **Yahoo Finance** by a small Python server
running on your own machine. No API keys, no sign-up, no third-party service in between.

Pairs with Zerodha's official Kite MCP, so Claude can look at your real holdings and research them in
the same conversation. A `/portfolio-review` skill for exactly that is included.

## Why self-host

- Your questions and your portfolio never leave your machine, except as plain requests to
  `nseindia.com` and `finance.yahoo.com` (the same requests a browser makes).
- No per-day call quota. Responses are cached (60 s for live data, 1 h for filings) so NSE stays happy.
- 23 tools, MIT licensed, ~800 lines of Python you can read in one sitting.

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs the right Python for you):
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Claude Code, Claude Desktop, or any MCP client. macOS and Linux tested; Windows should work.

The first start on a machine downloads about 100 MB of dependencies (numpy, pandas and friends),
which can take longer than Claude Code's 30-second server timeout. Warm it up once from a terminal
before the first session:

```bash
uvx --from git+https://github.com/sanjeev0291/nse-research-mcp nse-research-mcp --check
```

That prints the tool count and confirms both data sources respond. If you installed the Claude Code
plugin, warm up its own environment instead (the plugin runs from its install folder):

```bash
uv run --directory "$(ls -d ~/.claude/plugins/cache/nse-research-mcp/nse-research/*/ | tail -1)" nse-research-mcp --check
```

After that the server starts in a second or two. If you skip this and the first connection times out,
run `/mcp` and reconnect; the download continues where it left off.

## Install

### Claude Code, as a plugin (server + `/portfolio-review` skill)

```bash
claude plugin marketplace add sanjeev0291/nse-research-mcp
claude plugin install nse-research@nse-research-mcp
```

Restart Claude Code and run `/mcp`: `nse-research` should show as connected.

### Claude Code, server only

```bash
claude mcp add --scope user nse-research -- uvx --from git+https://github.com/sanjeev0291/nse-research-mcp nse-research-mcp
```

### Claude Desktop

Settings → Developer → Edit Config, then add (merge into an existing `mcpServers` block if you have one):

```json
{
  "mcpServers": {
    "nse-research": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/sanjeev0291/nse-research-mcp", "nse-research-mcp"]
    }
  }
}
```

If Desktop cannot find `uvx`, replace it with the full path from `which uvx` (usually `~/.local/bin/uvx`).
Restart the app and look for the tools icon.

### From a local clone (hackable)

```bash
git clone https://github.com/sanjeev0291/nse-research-mcp && cd nse-research-mcp
uv sync
claude mcp add --scope user nse-research -- uv run --directory "$PWD" nse-research-mcp
```

## Pair with your Zerodha account (optional)

Zerodha hosts an official MCP server for Kite. Nothing to install:

```bash
claude mcp add --transport http --scope user kite https://mcp.kite.trade/mcp
```

Ask Claude anything about your holdings; it calls Kite's `login` tool and gives you a link. Complete the
Kite 2FA in your browser and tell Claude you are done. Sessions expire every morning (around 6 AM IST),
so expect to log in once a day.

The hosted Kite server can also place orders. To keep Claude read-only, add this to
`~/.claude/settings.json`:

```json
{
  "permissions": {
    "deny": [
      "mcp__kite__place_order", "mcp__kite__modify_order", "mcp__kite__cancel_order",
      "mcp__kite__place_gtt_order", "mcp__kite__modify_gtt_order", "mcp__kite__delete_gtt_order"
    ]
  }
}
```

Then `/portfolio-review` (full), `/portfolio-review quick` (no research enrichment) or
`/portfolio-review TCS` (deep-dive one holding).

## Tools

| Tool | What it returns | Source |
|---|---|---|
| `search_stocks` | Symbols by name or partial symbol, with ISIN, NSE industry, index membership | NSE |
| `screen_stocks` | Server-side screener over all NSE stocks: market cap, PE, PB, ROE, dividend yield, growth, 52-week change, sector | Yahoo |
| `get_company_profile` | Sector, industry, cap bucket, index membership, summary, headline valuation | Yahoo + NSE |
| `get_stock_quote` | Price, change, day range, volume vs average, 52-week range, SMA50/200, PE, PB | Yahoo |
| `get_price_history` | OHLCV candles plus returns (1w to 1y), SMA20/50/200, RSI14, drawdown, volatility | Yahoo |
| `get_key_ratios` | Valuation, margins, ROE/ROA, growth, leverage, cash flow, per-share, ownership | Yahoo |
| `get_financials` | Income statement, balance sheet, cash flow; annual or quarterly, in ₹ crore | Yahoo |
| `get_shareholding` | Promoter vs public trend by quarter from filings, plus institution split | NSE + Yahoo |
| `get_corporate_actions` | Dividends, bonus, splits, rights with ex/record dates; upcoming board meetings | NSE |
| `get_announcements` | Latest exchange filings with subject, text and PDF link | NSE |
| `get_analyst_view` | Consensus, targets and upside, recommendation trend, earnings dates and surprises | Yahoo |
| `compare_stocks` | Side-by-side metrics for up to 15 symbols | Yahoo |
| `get_market_pulse` | Market status, key indices, VIX, sector leaders/laggards, latest FII/DII | NSE |
| `get_index_performance` | Every NSE index with 1d/30d/1y change and index PE/PB/yield | NSE |
| `get_index_constituents` | Members of NIFTY 50/500, midcap, smallcap and sectoral indices with industries | NSE |
| `get_top_movers` | Gainers or losers in NIFTY, BANKNIFTY, NEXT 50, F&O or all stocks | NSE |
| `get_most_active` | Most traded by value or volume | NSE |
| `get_52_week_breakouts` | Stocks at new 52-week highs or lows today | NSE |
| `get_fii_dii` | Latest FII/FPI and DII cash buy/sell/net in ₹ crore | NSE |
| `get_bulk_block_deals` | Today's bulk, block and short-sale disclosures | NSE |
| `get_option_chain` | Chain for NIFTY/BANKNIFTY/F&O stocks: PCR, max pain, OI walls, IV, strikes around ATM | NSE |
| `get_market_holidays` | NSE trading holidays, upcoming first | NSE |
| `manage_watchlist` | Local watchlist (get with live quotes, add, remove, clear) | local + Yahoo |

## Things to ask

- "Screen NSE for companies above ₹20,000 cr market cap with PE under 15 and ROE over 15%"
- "Compare TCS, Infosys, Wipro and HCL Tech on valuation and growth"
- "Has the promoter stake in Reliance changed over the last two years?"
- "What did the FIIs do yesterday, and which sectors led?"
- "NIFTY option chain for the nearest expiry: where are the OI walls and max pain?"
- "Show me the last 5 announcements from Tata Motors"
- "Which NIFTY 500 stocks are at a 52-week low today?"
- "Run /portfolio-review" (with Kite connected)

## Limits and caveats

- NSE throttles automated traffic. The server uses a browser-like session and caching, but a burst of
  calls can still get a temporary block; wait a minute and retry. A few NSE endpoints refuse bots
  outright (per-stock quote page, historical candles); those come from Yahoo instead.
- Yahoo fundamentals can lag filings by a few days and occasionally miss a field. Verify before acting.
- Sector names in the screener are Yahoo's eleven sectors; NSE's own industry label is added where the
  stock is in NIFTY 500 / MIDCAP 150 / SMALLCAP 250 / MICROCAP 250.
- Renamed or delisted symbols return "no data"; use `search_stocks` to find the current symbol.
- Option chains exist only for F&O instruments.
- Data, not investment advice.

## Files on your machine

- Cache: `~/.cache/nse-research/` (NSE constituent and equity-master CSVs, refreshed daily)
- Watchlist: `~/.config/nse-research/watchlist.json`

Override with the `NSE_RESEARCH_CACHE` and `NSE_RESEARCH_HOME` environment variables.

## Troubleshooting

- `--check` fails on Yahoo: your network blocks `finance.yahoo.com`, or Yahoo is rate limiting; retry.
- `--check` fails on NSE with HTTP 403/503: NSE's bot protection is throttling you; wait a minute. Some
  corporate networks and VPNs are blocked outright by NSE.
- Claude Code says the server timed out on first start: dependencies were still downloading. Run the
  `--check` command above, then `/mcp` → reconnect.
- Claude Desktop shows no tools: GUI apps have a minimal PATH. Use the full path to `uvx` in the config.
- `uv` complains it cannot install Python: install Python 3.11+ yourself (Homebrew, python.org, or
  your package manager) and uv will use it.

## Development

```bash
uv sync
uv run python tests/smoke.py          # live checks of every tool (needs internet)
uv run python tests/mcp_roundtrip.py  # spawns the server over stdio like a real client
```

Layout: `src/nse_research_mcp/server.py` (tools), `nse.py` (NSE client), `yahoo.py` (Yahoo wrappers),
`symbols.py` (search, index lists), `indicators.py`, `cache.py`. Plugin manifest in `.claude-plugin/`,
MCP declaration in `.mcp.json`, the skill in `skills/portfolio-review/`.

## License

MIT
