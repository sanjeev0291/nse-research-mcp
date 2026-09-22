"""Holdings summary computed from Zerodha's Kite MCP (shared by the desk UI and the alerts engine)."""
from __future__ import annotations

from . import symbols
from .kite_client import KiteClient


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def holdings_summary(kite: KiteClient) -> dict:
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
    sector_rows = sorted(
        ({"industry": k, "value": round(v, 2), "weight_pct": round(v / current * 100, 2) if current else None} for k, v in sectors.items()),
        key=lambda x: -x["value"],
    )
    totals = {
        "count": len(out), "invested": round(invested, 2), "current": round(current, 2), "pnl": round(current - invested, 2),
        "pnl_pct": round((current / invested - 1) * 100, 2) if invested else None, "day_pnl": round(day_pnl, 2),
        "top3_weight_pct": round(sum(r["weight_pct"] or 0 for r in out[:3]), 2) if out else None,
    }
    return {"holdings": out, "totals": totals, "sectors": sector_rows}
