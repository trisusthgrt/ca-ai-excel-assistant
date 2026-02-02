"""
In-memory cache for chart aggregations (daily totals, monthly totals).
Uses Decimal for accurate sums; rounds to 2 decimals for output.
"""
from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

QUANTIZE = Decimal("0.01")


def _round2(val: float) -> float:
    """Round to 2 decimal places (half-up) for money."""
    d = Decimal(str(val)).quantize(QUANTIZE, rounding=ROUND_HALF_UP)
    return float(d)

# Amount-like and date-like keys (aligned with analyst)
AMOUNT_KEYS = {"amount", "gst", "total", "value", "sum", "balance", "tax"}
DATE_KEYS = {"rowdate", "row_date", "date", "transaction date", "transaction_date"}

# In-memory cache: key -> { "rows", "daily_totals", "monthly_totals" }
# Bounded (max 128 entries) for free-tier; evict oldest on overflow
_MAX_CACHE_SIZE = 128
_cache: OrderedDict = OrderedDict()


def _numeric(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _find_key(row: dict, candidates: set) -> Optional[str]:
    for k in row:
        if k.lower() in candidates:
            return k
    return None


def build_key(planner_output: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], str]:
    """
    Build cache key from planner_output.
    Returns (date_from, date_to, client_tag, metric, date_filter_type).
    date_filter_type separates "upload date" vs "row date" queries.
    """
    if not planner_output:
        return (None, None, None, None, "row_date")
    date_filter = planner_output.get("date_filter") or {}
    date_filter_type = (planner_output.get("date_filter_type") or "row_date").strip().lower()
    if date_filter_type not in ("upload_date", "row_date"):
        date_filter_type = "row_date"
    date_from = date_filter.get("from") or date_filter.get("single")
    date_to = date_filter.get("to") or date_filter.get("single")
    if not date_from or not date_to:
        dates = planner_output.get("dates") or []
        if dates:
            date_from = date_from or (min(dates) if dates else None)
            date_to = date_to or (max(dates) if len(dates) > 1 else min(dates))
    if date_from:
        date_from = str(date_from).strip() or None
    if date_to:
        date_to = str(date_to).strip() or None
    client = planner_output.get("client_tag") or planner_output.get("client")
    if client is not None:
        client = str(client).strip() or None
    metric = planner_output.get("metric")
    if metric is not None:
        metric = str(metric).strip().lower() or None
    return (date_from, date_to, client, metric, date_filter_type)


def get(key: Tuple) -> Optional[Dict[str, Any]]:
    """Return cached value for key, or None. Moves key to end (LRU)."""
    if key not in _cache:
        return None
    _cache.move_to_end(key)
    return _cache[key].copy()


def set_value(key: Tuple, value: Dict[str, Any]) -> None:
    """Store value for key. Evicts oldest if over max size."""
    while len(_cache) >= _MAX_CACHE_SIZE and key not in _cache:
        _cache.popitem(last=False)
    _cache[key] = value
    _cache.move_to_end(key)


def compute_daily_totals(rows: List[dict]) -> List[Dict[str, Any]]:
    """Group rows by date (day), sum amount. Returns [{"date": "YYYY-MM-DD", "value": float}, ...]."""
    if not rows:
        return []
    amount_key = _find_key(rows[0], AMOUNT_KEYS)
    date_key = _find_key(rows[0], DATE_KEYS) or "rowDate"
    if not amount_key:
        for r in rows:
            for k, v in r.items():
                if _numeric(v) is not None:
                    amount_key = k
                    break
            if amount_key:
                break
    by_date: Dict[str, Decimal] = {}
    for r in rows:
        dt = r.get(date_key) or r.get("rowDate")
        if dt:
            dt = str(dt)[:10]
        else:
            dt = "Unknown"
        n = _numeric(r.get(amount_key)) if amount_key else None
        if n is not None:
            by_date[dt] = by_date.get(dt, Decimal("0")) + Decimal(str(n))
    sorted_dates = sorted(d for d in by_date.keys() if d != "Unknown") + ([ "Unknown" ] if "Unknown" in by_date else [])
    return [{"date": d, "value": _round2(float(by_date[d]))} for d in sorted_dates]


def compute_monthly_totals(rows: List[dict]) -> List[Dict[str, Any]]:
    """Group rows by month (YYYY-MM), sum amount. Returns [{"month": "YYYY-MM", "value": float}, ...]."""
    if not rows:
        return []
    amount_key = _find_key(rows[0], AMOUNT_KEYS)
    date_key = _find_key(rows[0], DATE_KEYS) or "rowDate"
    if not amount_key:
        for r in rows:
            for k, v in r.items():
                if _numeric(v) is not None:
                    amount_key = k
                    break
            if amount_key:
                break
    by_month: Dict[str, Decimal] = {}
    for r in rows:
        dt = r.get(date_key) or r.get("rowDate")
        if dt:
            s = str(dt)[:10]
            if len(s) >= 7:
                month = s[:7]  # YYYY-MM
            else:
                month = "Unknown"
        else:
            month = "Unknown"
        n = _numeric(r.get(amount_key)) if amount_key else None
        if n is not None:
            by_month[month] = by_month.get(month, Decimal("0")) + Decimal(str(n))
    sorted_months = sorted(m for m in by_month.keys() if m != "Unknown") + ([ "Unknown" ] if "Unknown" in by_month else [])
    return [{"month": m, "value": _round2(float(by_month[m]))} for m in sorted_months]


def clear() -> None:
    """Clear cache (e.g. for tests)."""
    _cache.clear()
