"""
Normalize column names, dates (ISO), amounts (numbers) — Step 4.
"""
import re
from typing import List, Optional, Tuple

import pandas as pd

# Known aliases: Excel header -> normalized name
COLUMN_ALIASES = {
    "gst": "gst",
    "amount": "amount",
    "value": "amount",
    "total": "amount",
    "date": "rowdate",
    "transaction date": "rowdate",
    "rowdate": "rowdate",
    "transaction_date": "rowdate",
    "transactiondate": "rowdate",  # Handle camelCase variant
    "description": "description",
    "desc": "description",
    "category": "category",
    "remarks": "remarks",
    "notes": "remarks",
}

# Column names that look like dates (after lower/strip)
DATE_LIKE = {"date", "rowdate", "transaction date", "transaction_date", "transactiondate", "dt", "transaction_dt"}

# Column names that look like amounts (after lower/strip)
AMOUNT_LIKE = {"amount", "value", "total", "gst", "tax", "sum", "balance"}


def _normalize_column_name(name: str) -> str:
    """Lowercase, strip, replace spaces/special chars with underscore."""
    if not isinstance(name, str) or not name.strip():
        return "unknown"
    s = name.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s or "unknown"


def _map_alias(col: str) -> str:
    """Map known alias to canonical name."""
    return COLUMN_ALIASES.get(col, col)


def _to_iso_date(val) -> Optional[str]:
    """
    Convert value to ISO date string (YYYY-MM-DD) or None.
    ALWAYS uses pd.to_datetime() for robust parsing.
    """
    if pd.isna(val):
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    try:
        # Use pd.to_datetime() with error handling for robust parsing
        dt = pd.to_datetime(val, errors='coerce')
        if pd.isna(dt):
            return None
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _to_amount(val) -> Optional[float]:
    """Strip currency/symbols, commas; convert to float or None."""
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    # Remove common symbols and commas
    s = re.sub(r"[^\d.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    Normalize DataFrame:
    - Column names: lowercase, strip, replace special chars; map known aliases.
    - Dates: detect date-like columns, convert to ISO (YYYY-MM-DD).
    - Amounts: detect amount-like columns, strip symbols/commas, convert to float.
    Returns (normalized_df, list of normalized column names).
    """
    if df is None or df.empty:
        return pd.DataFrame(), []

    out = df.copy()
    # Normalize column names and apply aliases
    new_cols = []
    for c in out.columns:
        n = _normalize_column_name(str(c))
        n = _map_alias(n)
        new_cols.append(n)
    # Make unique: first occurrence keeps name, duplicates get _1, _2, ...
    seen = {}
    unique = []
    for c in new_cols:
        if c in seen:
            seen[c] += 1
            unique.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            unique.append(c)
    out.columns = unique

    # Convert date-like columns to ISO using pd.to_datetime() for robust parsing
    for c in out.columns:
        base = c.split("_")[0].lower() if "_" in c else c.lower()
        if base in DATE_LIKE or "date" in c.lower():
            # Use pd.to_datetime() directly on the entire Series for better performance and robustness
            try:
                out[c] = pd.to_datetime(out[c], errors='coerce').dt.strftime("%Y-%m-%d")
                # Convert NaT to None for consistency
                out[c] = out[c].where(pd.notna(out[c]), None)
            except Exception:
                # Fallback to row-by-row if Series conversion fails
                out[c] = out[c].apply(_to_iso_date)

    # Convert amount-like columns to float
    for c in out.columns:
        base = c.split("_")[0].lower() if "_" in c else c.lower()
        if base in AMOUNT_LIKE or "amount" in c.lower() or "gst" in c.lower() or "total" in c.lower():
            out[c] = out[c].apply(_to_amount)

    return out, list(out.columns)


def get_rowdate_column_name(normalized_columns: List[str]) -> Optional[str]:
    """
    Return the first column name that represents row date.
    Checks for: rowdate, date, transactiondate, transaction_date, etc.
    """
    # Priority order: prefer exact matches first
    priority_names = ["rowdate", "date", "transactiondate", "transaction_date"]
    for pname in priority_names:
        for c in normalized_columns:
            if c.lower() == pname:
                return c
    # Fallback: any column containing "date"
    for c in normalized_columns:
        if "date" in c.lower():
            return c
    return None
