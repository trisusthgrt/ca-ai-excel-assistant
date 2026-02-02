"""
Chart validator — rule-based validation before rendering (Step 9+).
validate_chart(dataframe, planner_output) -> bool.
If False: do not render chart; fallback to table with message.
"""
from typing import Any, Dict

import pandas as pd


def validate_chart(dataframe: pd.DataFrame, planner_output: Dict[str, Any]) -> bool:
    """
    Validate that chart data and planner intent are suitable for rendering.

    Rules:
    1. Minimum 2 data points required.
    2. x_axis column must exist and be date or categorical.
    3. y_axis column must exist and be numeric.
    4. For trend charts (line + trend intent): date range must be > 1 day.

    Returns True if all rules pass, False otherwise.
    """
    if dataframe is None or not isinstance(dataframe, pd.DataFrame) or dataframe.empty:
        return False
    if planner_output is None:
        planner_output = {}

    # 1. Minimum 2 data points
    if len(dataframe) < 2:
        return False

    cols = list(dataframe.columns)
    if len(cols) < 2:
        return False

    # Resolve x and y column names (planner may say "date"/"category" and "amount"/"value")
    x_name = (planner_output.get("x_axis") or "").strip().lower()
    y_name = (planner_output.get("y_axis") or "").strip().lower()
    col_lower = {str(c).strip().lower(): c for c in cols}
    x_col = col_lower.get(x_name) if x_name else cols[0]
    y_col = col_lower.get(y_name) if y_name else (cols[1] if len(cols) > 1 else None)
    if x_col is None or y_col is None:
        return False

    # 2. x_axis must exist and be date/categorical
    if x_col not in dataframe.columns:
        return False
    x_series = dataframe[x_col].dropna()
    if len(x_series) < 2:
        return False
    is_date = _is_date_like(x_series)
    is_categorical = _is_categorical_like(x_series)
    if not (is_date or is_categorical):
        return False

    # 3. y_axis must be numeric
    if y_col not in dataframe.columns:
        return False
    y_series = dataframe[y_col]
    if not pd.api.types.is_numeric_dtype(y_series):
        # Try coercing
        y_numeric = pd.to_numeric(y_series, errors="coerce")
        if y_numeric.isna().all():
            return False
        valid_count = y_numeric.notna().sum()
        if valid_count < 2:
            return False
    else:
        if y_series.dropna().empty or len(y_series.dropna()) < 2:
            return False

    # 4. Date range > 1 day for trend charts
    chart_type = (planner_output.get("chart_type") or "").strip().lower()
    intent = (planner_output.get("intent") or "").strip().lower()
    if chart_type == "line" and intent == "trend":
        if not is_date:
            return False
        dates_parsed = pd.to_datetime(dataframe[x_col], errors="coerce").dropna()
        if len(dates_parsed) < 2:
            return False
        min_d = dates_parsed.min()
        max_d = dates_parsed.max()
        if (max_d - min_d).days <= 1:
            return False

    return True


def _is_date_like(series: pd.Series) -> bool:
    """True if series looks like dates (datetime dtype or parsable)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    parsed = pd.to_datetime(series, errors="coerce")
    return parsed.notna().sum() >= min(2, len(series))


def _is_categorical_like(series: pd.Series) -> bool:
    """True if series is object/category or string-like (categorical)."""
    if series.dtype.name in ("object", "category", "string"):
        return True
    return False
