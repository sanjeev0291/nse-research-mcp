"""Symbol normalisation, local search over NSE's listed-equity master, and index membership."""
from __future__ import annotations

import csv
import io

from . import nse
from .cache import ttl_cache

INDEX_FILES = {
    "NIFTY 50": "ind_nifty50list.csv",
    "NIFTY NEXT 50": "ind_niftynext50list.csv",
    "NIFTY 100": "ind_nifty100list.csv",
    "NIFTY 200": "ind_nifty200list.csv",
    "NIFTY 500": "ind_nifty500list.csv",
    "NIFTY MIDCAP 50": "ind_niftymidcap50list.csv",
    "NIFTY MIDCAP 100": "ind_niftymidcap100list.csv",
    "NIFTY MIDCAP 150": "ind_niftymidcap150list.csv",
    "NIFTY SMALLCAP 100": "ind_niftysmallcap100list.csv",
    "NIFTY SMALLCAP 250": "ind_niftysmallcap250list.csv",
    "NIFTY MICROCAP 250": "ind_niftymicrocap250_list.csv",
    "NIFTY BANK": "ind_niftybanklist.csv",
    "NIFTY IT": "ind_niftyitlist.csv",
    "NIFTY AUTO": "ind_niftyautolist.csv",
    "NIFTY PHARMA": "ind_niftypharmalist.csv",
    "NIFTY FMCG": "ind_niftyfmcglist.csv",
    "NIFTY METAL": "ind_niftymetallist.csv",
    "NIFTY REALTY": "ind_niftyrealtylist.csv",
    "NIFTY ENERGY": "ind_niftyenergylist.csv",
    "NIFTY FINANCIAL SERVICES": "ind_niftyfinancelist.csv",
    "NIFTY HEALTHCARE": "ind_niftyhealthcarelist.csv",
    "NIFTY CONSUMER DURABLES": "ind_niftyconsumerdurableslist.csv",
    "NIFTY OIL & GAS": "ind_niftyoilgaslist.csv",
    "NIFTY PSU BANK": "ind_niftypsubanklist.csv",
    "NIFTY PRIVATE BANK": "ind_nifty_privatebanklist.csv",
    "NIFTY MEDIA": "ind_niftymedialist.csv",
    "NIFTY INFRA": "ind_niftyinfralist.csv",
    "NIFTY PSE": "ind_niftypselist.csv",
    "NIFTY CPSE": "ind_niftycpselist.csv",
    "NIFTY MNC": "ind_niftymnclist.csv",
}


def normalize(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    for suffix in (".NS", ".BO", ".NSE", "-EQ"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def yahoo_ticker(symbol: str) -> str:
    return normalize(symbol) + ".NS"


@ttl_cache(3600)
def equity_master() -> list[dict]:
    text = nse.archive_text("content/equities/EQUITY_L.csv", "EQUITY_L.csv")
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if not r.get("SYMBOL"):
            continue
        rows.append(
            {
                "symbol": r["SYMBOL"],
                "name": r.get("NAME OF COMPANY", ""),
                "series": r.get("SERIES", ""),
                "isin": r.get("ISIN NUMBER", ""),
                "listed_on": r.get("DATE OF LISTING", ""),
                "face_value": r.get("FACE VALUE", ""),
            }
        )
    return rows


def index_constituents(index: str) -> list[dict]:
    key = index.strip().upper()
    if key not in INDEX_FILES:
        raise ValueError(f"Unknown index '{index}'. Known: {', '.join(INDEX_FILES)}")
    file = INDEX_FILES[key]
    text = nse.archive_text(f"content/indices/{file}", file)
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if not r.get("Symbol"):
            continue
        rows.append(
            {
                "symbol": r["Symbol"],
                "name": r.get("Company Name", ""),
                "industry": r.get("Industry", ""),
                "isin": r.get("ISIN Code", ""),
            }
        )
    return rows


@ttl_cache(3600)
def membership() -> dict[str, dict]:
    """symbol -> {industry, indices[], cap_bucket} using NSE's index constituent files."""
    out: dict[str, dict] = {}
    buckets = {
        "NIFTY 50": "large",
        "NIFTY NEXT 50": "large",
        "NIFTY MIDCAP 150": "mid",
        "NIFTY SMALLCAP 250": "small",
        "NIFTY MICROCAP 250": "micro",
        "NIFTY 500": None,
    }
    for idx, bucket in buckets.items():
        try:
            rows = index_constituents(idx)
        except Exception:
            continue
        for r in rows:
            e = out.setdefault(r["symbol"], {"industry": r["industry"], "indices": [], "cap_bucket": None})
            e["indices"].append(idx)
            if bucket and not e["cap_bucket"]:
                e["cap_bucket"] = bucket
    return out


def search(query: str, limit: int = 10) -> list[dict]:
    q = (query or "").strip().upper()
    if not q:
        return []
    tokens = [t for t in q.replace("&", " & ").split() if t]
    scored = []
    for r in equity_master():
        sym, name = r["symbol"], r["name"].upper()
        score = 0
        if sym == q:
            score = 100
        elif sym.startswith(q):
            score = 80
        elif q in sym:
            score = 60
        elif name.startswith(q):
            score = 70
        elif q in name:
            score = 50
        elif tokens and all(t in name or t in sym for t in tokens):
            score = 40
        if score:
            if r["series"] != "EQ":
                score -= 5
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["symbol"]))
    members = membership()
    result = []
    for _, r in scored[:limit]:
        m = members.get(r["symbol"], {})
        result.append({**r, "industry": m.get("industry"), "indices": m.get("indices", []), "cap_bucket": m.get("cap_bucket")})
    return result
