"""
Analyst agent — calculations only, no text generation (Step 6).
Uses Decimal for accurate monetary sums; rounds to 2 decimals for output.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

# Keys we treat as amount-like (summable)
AMOUNT_KEYS = {"amount", "gst", "total", "value", "sum", "balance", "tax"}
# Keys we treat as date-like (for grouping)
DATE_KEYS = {"rowdate", "row_date", "date", "transaction date", "transaction_date"}
# Keys we treat as category/description (for breakdown)
CATEGORY_KEYS = {"category", "description", "desc", "remarks", "type"}

# Monetary precision: 2 decimal places
DECIMAL_PLACES = 2
QUANTIZE = Decimal("0.01")


def _round2(val: float) -> float:
    """Round to 2 decimal places using half-up (banker-style for money)."""
    if val is None:
        return 0.0
    d = Decimal(str(val)).quantize(QUANTIZE, rounding=ROUND_HALF_UP)
    return float(d)


def _numeric(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _decimal_sum(values: List[float]) -> float:
    """Sum using Decimal for accuracy, return float rounded to 2 decimals."""
    total = Decimal("0")
    for v in values:
        if v is not None:
            try:
                total += Decimal(str(v))
            except Exception:
                pass
    return _round2(float(total))


def _find_key(row: dict, candidates: set) -> Optional[str]:
    for k in row:
        if k.lower() in candidates:
            return k
    return None


def analyze(intent: str, data: Any) -> dict:
    """
    Perform calculations only. No LLM.
    SAFETY: Run only if rows > 0 (or cached daily/monthly totals present). Never invent totals; only compute over provided rows.
    data: list of row dicts OR dict from DataAgent with "rows", "daily_totals", "monthly_totals".
    Returns structured dict: total, breakdown, series, compare, etc.
    """
    # Guard: do not run on empty data (orchestrator should not call when rows==0; this is a safety backstop)
    if isinstance(data, dict):
        rows_raw = data.get("rows") or []
        daily_totals = data.get("daily_totals")
        monthly_totals = data.get("monthly_totals")
    else:
        rows_raw = data if isinstance(data, list) else []
        daily_totals = None
        monthly_totals = None

    if not rows_raw and not daily_totals and not monthly_totals:
        return {"total": 0, "count": 0, "message": "No data for the selected filters."}

    intent = (intent or "other").strip().lower()
    # Normalize row keys to lowercase for detection
    rows = []
    for r in (rows_raw or []):
        clean = {}
        for k, v in r.items():
            if k in ("_id", "fileId", "uploadDate", "clientTag"):
                continue
            clean[k] = v
        rows.append(clean)

    if not rows and not daily_totals and not monthly_totals:
        return {"total": 0, "count": 0}

    # Detect amount column
    amount_key = None
    for r in rows:
        amount_key = _find_key(r, AMOUNT_KEYS)
        if amount_key:
            break
    if not amount_key:
        for r in rows:
            for k, v in r.items():
                if _numeric(v) is not None:
                    amount_key = k
                    break
            if amount_key:
                break

    # Detect date column
    date_key = _find_key(rows[0] if rows else {}, DATE_KEYS) or "rowDate"
    # Detect category column
    category_key = _find_key(rows[0] if rows else {}, CATEGORY_KEYS)

    amount_values: List[float] = []
    for r in rows:
        if amount_key and amount_key in r:
            n = _numeric(r[amount_key])
            if n is not None:
                amount_values.append(n)
        else:
            for k, v in r.items():
                n = _numeric(v)
                if n is not None:
                    amount_values.append(n)
                    break
    total = _decimal_sum(amount_values)

    result = {
        "total": total,
        "count": len(rows),
        "amount_key": amount_key,
    }
    # Date range (min/max rowDate) for summary and context
    row_dates = []
    for r in rows:
        d = r.get("rowDate") or r.get("rowdate") or r.get("date")
        if d:
            row_dates.append(str(d)[:10])
    if row_dates:
        result["date_range"] = {"min": min(row_dates), "max": max(row_dates)}

    if intent == "gst_summary":
        result["summary"] = "GST/amount total"
        return result

    # Full summary: breakdown by category, series by date, all numeric columns summarized
    if intent == "summarize":
        result["summary"] = "Full data summary"
        if category_key:
            by_cat: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            for r in rows:
                cat = str(r.get(category_key, "Other")).strip() or "Other"
                n = _numeric(r.get(amount_key)) if amount_key else None
                if n is not None:
                    by_cat[cat] += Decimal(str(n))
            result["breakdown"] = [{"category": k, "amount": _round2(float(v))} for k, v in sorted(by_cat.items())]
        if date_key and rows:
            by_date: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            for r in rows:
                dt = r.get(date_key) or r.get("rowDate")
                if dt:
                    dt = str(dt)[:10]
                else:
                    dt = "Unknown"
                n = _numeric(r.get(amount_key)) if amount_key else None
                if n is not None:
                    by_date[dt] += Decimal(str(n))
            sorted_dates = sorted(by_date.keys())
            result["series"] = [{"date": d, "value": _round2(float(by_date[d]))} for d in sorted_dates]
        # All column names present in the data (for narrative)
        if rows:
            result["column_names"] = list(rows[0].keys())
        return result

    if intent == "expense_breakdown" and category_key:
        by_cat: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for r in rows:
            cat = str(r.get(category_key, "Other")).strip() or "Other"
            n = _numeric(r.get(amount_key)) if amount_key else None
            if n is not None:
                by_cat[cat] += Decimal(str(n))
        result["breakdown"] = [{"category": k, "amount": _round2(float(v))} for k, v in sorted(by_cat.items())]
        result["total"] = _decimal_sum([float(v) for v in by_cat.values()])
        result["chart_type"] = "bar"
        return result

    if intent == "trend":
        if daily_totals and len(daily_totals) >= 1:
            result["series"] = [{"date": d.get("date", ""), "value": _round2(d.get("value", 0))} for d in daily_totals]
            result["chart_type"] = "line"
            if rows:
                vals = [_numeric(r.get(amount_key)) for r in rows if amount_key and _numeric(r.get(amount_key)) is not None]
                result["total"] = _decimal_sum(vals)
            else:
                result["total"] = _decimal_sum([d.get("value", 0) for d in daily_totals])
                result["count"] = len(daily_totals)
            return result
        if date_key and rows:
            by_date: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            for r in rows:
                dt = r.get(date_key) or r.get("rowDate")
                if dt:
                    dt = str(dt)[:10]
                else:
                    dt = "Unknown"
                n = _numeric(r.get(amount_key)) if amount_key else None
                if n is not None:
                    by_date[dt] += Decimal(str(n))
            sorted_dates = sorted(by_date.keys())
            result["series"] = [{"date": d, "value": _round2(float(by_date[d]))} for d in sorted_dates]
            result["total"] = _decimal_sum([float(by_date[d]) for d in sorted_dates])
            result["chart_type"] = "line"
            return result

    if intent == "compare_dates":
        agg = daily_totals or monthly_totals
        if agg and len(agg) >= 1:
            key = "date" if daily_totals else "month"
            result["compare"] = [{"date": x.get(key, ""), "total": _round2(x.get("value", 0))} for x in agg]
            result["chart_type"] = "bar"
            if rows:
                vals = [_numeric(r.get(amount_key)) for r in rows if amount_key and _numeric(r.get(amount_key)) is not None]
                result["total"] = _decimal_sum(vals)
            else:
                result["total"] = _decimal_sum([x.get("value", 0) for x in agg])
                result["count"] = len(agg)
            return result
        if date_key and rows:
            by_date: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            for r in rows:
                dt = r.get(date_key) or r.get("rowDate")
                if dt:
                    dt = str(dt)[:10]
                else:
                    dt = "Unknown"
                n = _numeric(r.get(amount_key)) if amount_key else None
                if n is not None:
                    by_date[dt] += Decimal(str(n))
            sorted_dates = sorted(by_date.keys())
            result["compare"] = [{"date": d, "total": _round2(float(by_date[d]))} for d in sorted_dates]
            result["total"] = _decimal_sum([float(by_date[d]) for d in sorted_dates])
            result["chart_type"] = "bar"
            return result

    return result
