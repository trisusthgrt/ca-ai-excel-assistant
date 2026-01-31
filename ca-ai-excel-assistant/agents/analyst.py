"""
Analyst agent — calculations only, no text generation (Step 6).
Input: planner intent + retrieved rows. Output: structured dict (totals, series, breakdown).
"""
from collections import defaultdict
from typing import Any, Dict, List, Optional

# Keys we treat as amount-like (summable)
AMOUNT_KEYS = {"amount", "gst", "total", "value", "sum", "balance", "tax"}
# Keys we treat as date-like (for grouping)
DATE_KEYS = {"rowdate", "row_date", "date", "transaction date", "transaction_date"}
# Keys we treat as category/description (for breakdown)
CATEGORY_KEYS = {"category", "description", "desc", "remarks", "type"}


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


def analyze(intent: str, data: List[dict]) -> dict:
    """
    Perform calculations only. No LLM.
    Returns structured dict: total, breakdown, series, compare, etc.
    """
    if not data:
        return {"total": 0, "count": 0, "message": "No data for the selected filters."}

    intent = (intent or "other").strip().lower()
    # Normalize row keys to lowercase for detection
    rows = []
    for r in data:
        clean = {}
        for k, v in r.items():
            if k in ("_id", "fileId", "uploadDate", "clientTag"):
                continue
            clean[k] = v
        rows.append(clean)

    if not rows:
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

    total = 0.0
    for r in rows:
        if amount_key and amount_key in r:
            n = _numeric(r[amount_key])
            if n is not None:
                total += n
        else:
            for k, v in r.items():
                n = _numeric(v)
                if n is not None:
                    total += n
                    break

    result = {
        "total": round(total, 2),
        "count": len(rows),
        "amount_key": amount_key,
    }

    if intent == "gst_summary":
        result["summary"] = "GST/amount total"
        return result

    if intent == "expense_breakdown" and category_key:
        by_cat = defaultdict(float)
        for r in rows:
            cat = str(r.get(category_key, "Other")).strip() or "Other"
            n = _numeric(r.get(amount_key)) if amount_key else None
            if n is not None:
                by_cat[cat] += n
        result["breakdown"] = [{"category": k, "amount": round(v, 2)} for k, v in sorted(by_cat.items())]
        result["chart_type"] = "bar"
        return result

    if intent == "trend" and date_key:
        by_date = defaultdict(float)
        for r in rows:
            dt = r.get(date_key) or r.get("rowDate")
            if dt:
                dt = str(dt)[:10]
            else:
                dt = "Unknown"
            n = _numeric(r.get(amount_key)) if amount_key else None
            if n is not None:
                by_date[dt] += n
        sorted_dates = sorted(by_date.keys())
        result["series"] = [{"date": d, "value": round(by_date[d], 2)} for d in sorted_dates]
        result["chart_type"] = "line"
        return result

    if intent == "compare_dates" and date_key:
        by_date = defaultdict(float)
        for r in rows:
            dt = r.get(date_key) or r.get("rowDate")
            if dt:
                dt = str(dt)[:10]
            else:
                dt = "Unknown"
            n = _numeric(r.get(amount_key)) if amount_key else None
            if n is not None:
                by_date[dt] += n
        sorted_dates = sorted(by_date.keys())
        result["compare"] = [{"date": d, "total": round(by_date[d], 2)} for d in sorted_dates]
        result["chart_type"] = "bar"
        return result

    return result
