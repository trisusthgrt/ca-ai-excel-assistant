"""
Orchestrator — fixed pipeline with strict routing, schema awareness, data existence guard, smart defaults.
Router BEFORE DataAgent. Schema_query → metadata only. Vague_query → smart defaults. No data → explain + suggest.
Also applies small deterministic fixes for:
- Upload-date semantics (queries like "file uploaded on ...", "latest uploaded file")
- Month-only queries (e.g. "Total GST for Feb 2026") by expanding to full month range.
"""
import calendar
import logging
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from .planner import plan
from .data_agent import fetch_data
from .analyst import analyze
from .responder import respond
from db import mongo
from utils.policy_guard import check_policy
from utils.query_normalizer import normalize_query
from utils.chart_validator import validate_chart
from utils.query_router import (
    route_query_type,
    SCHEMA_QUERY,
    DATA_QUERY,
    VAGUE_QUERY,
    EXPLANATION_QUERY,
)

logger = logging.getLogger(__name__)

# Clarification only if confidence below this; above it use defaults for vague queries
CLARIFICATION_CONFIDENCE_THRESHOLD = 0.4


def _format_date_readable(iso_date: str) -> str:
    """Format YYYY-MM-DD to '12 Jan 2025' style."""
    if not iso_date or len(iso_date) < 10:
        return iso_date or ""
    try:
        from datetime import datetime
        dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except ValueError:
        return iso_date[:10]


def _build_schema_answer(schema: Dict[str, Any], query: str) -> str:
    """Answer schema_query from stored metadata. No DataAgent/Analyst."""
    if not schema:
        return "No file has been uploaded yet. Upload an Excel file to see column and row information."
    q = (query or "").strip().lower()
    column_names = schema.get("column_names") or []
    column_count = schema.get("column_count") or len(column_names)
    row_count = schema.get("row_count") or 0

    # If the user explicitly asks for names of attributes/columns, prefer listing
    # the actual names over counts, even if "attributes"/"columns" appear.
    if "name" in q and ("attribute" in q or "column" in q):
        if not column_names:
            return "No column names are stored for the latest file."
        return "The attributes (columns) present are: **" + "**, **".join(str(c) for c in column_names) + "**."

    if re.search(r"\b(?:how\s+many\s+)?columns?\b", q):
        return f"There are **{column_count}** column(s) in the latest uploaded file."
    if re.search(r"\b(?:how\s+many\s+)?rows?\b", q):
        return f"There are **{row_count}** row(s) in the latest uploaded file."
    if re.search(r"\b(?:how\s+many\s+)?attributes?\b", q):
        return f"There are **{column_count}** attribute(s) (columns) in the latest uploaded file."
    if re.search(r"\b(?:what|which)\s+(?:are\s+)?(?:the\s+)?(?:column|attribute)s?\b", q) or "attributes" in q:
        if not column_names:
            return "No column names are stored for the latest file."
        return "The attributes (columns) present are: **" + "**, **".join(str(c) for c in column_names) + "**."
    # Default schema summary
    if column_names:
        cols_str = ", ".join(str(c) for c in column_names[:15]) + ("..." if len(column_names) > 15 else "")
    else:
        cols_str = "(column names not stored)"
    return f"The latest uploaded file has **{column_count}** columns and **{row_count}** rows. Columns: {cols_str}"


def _build_no_data_explanation(
    planner_output: Dict[str, Any],
    client_tag: Optional[str],
    normalized_query: str,
) -> str:
    """Explain why no data exists and suggest nearby dates or alternatives. Data existence guard."""
    date_filter = planner_output.get("date_filter") or {}
    single = date_filter.get("single")
    from_d = date_filter.get("from")
    to_d = date_filter.get("to")
    client = client_tag or planner_output.get("client_tag") or planner_output.get("client")
    parts = ["No records found"]
    if client:
        parts.append(f"for **{client}**")
    if single:
        parts.append(f"on **{single}**")
    elif from_d and to_d:
        parts.append(f"between **{from_d}** and **{to_d}**")
    parts.append(".")
    nearby = mongo.get_nearby_dates_for_client(client_tag=client, limit=5)
    if nearby:
        dates_str = ", ".join(nearby[:5])
        if client:
            parts.append(f" Data exists for this client on other dates, e.g. {dates_str}.")
        else:
            parts.append(f" Data exists on other dates, e.g. {dates_str}.")
    else:
        parts.append(" No data has been uploaded yet." if not client else " No data has been uploaded for this client yet.")
    return "".join(parts)


def _apply_smart_defaults(planner_output: Dict[str, Any], query: str) -> Dict[str, Any]:
    """Apply smart defaults for vague_query: metric, full date range, line chart."""
    q = (query or "").strip().lower()
    out = dict(planner_output)
    # Metric: Net_Amount or GST if tax-related
    if not out.get("metric"):
        out["metric"] = "gst" if any(w in q for w in ("gst", "tax", "vat")) else "net_amount"
    # Full date range: no filter so fetch_data gets all
    out["date_filter"] = {}
    out["dates"] = []
    # Chart: line for trend
    if "chart" in q or "show data" in q or "display" in q:
        out["needs_chart"] = True
        out["chart_type"] = "line"
        out["chart_scope"] = "Trend over time"
        out["intent"] = "trend"
    return out


def _maybe_force_upload_date(planner_output: Dict[str, Any], query: str) -> Dict[str, Any]:
    """
    If the user clearly talks about the *uploaded file* (e.g. "file uploaded on",
    "latest uploaded file"), force date_filter_type to 'upload_date'.

    This guards against LLM/planner confusing upload date with row date and ensures
    queries like "summary of the excel file uploaded on 2026-02-02" are scoped by
    uploadDate, not by rowDate across all files.
    """
    if not planner_output:
        return planner_output
    q = (query or "").strip().lower()
    # Broadly treat "uploaded on ..." or "upload date ..." as upload_date semantics
    if "upload" in q or "uploaded" in q:
        planner_output = dict(planner_output)
        planner_output["date_filter_type"] = "upload_date"
    return planner_output


def _expand_month_range_if_needed(planner_output: Dict[str, Any], query: str) -> Dict[str, Any]:
    """
    Detect month-only queries like "for Feb 2026" and expand to a full month range
    [YYYY-MM-01, YYYY-MM-last_day].

    This fixes cases where the planner/LLM collapses "Feb 2026" to a single day
    (e.g. 2026-02-28) causing "no data" even though days exist in that month.
    """
    if not planner_output:
        return planner_output

    q = (query or "").strip().lower()

    # If the query already has an explicit day like "5 Feb 2026", do nothing.
    if re.search(r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}\b", q):
        return planner_output

    # Month + year without explicit day: e.g. "feb 2026", "february 2026"
    m = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+(\d{4})\b",
        q,
    )
    if not m:
        return planner_output

    month_str, year_str = m.group(1), m.group(2)
    year = int(year_str)
    month_lookup = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    month = month_lookup.get(month_str.lower())
    if not month:
        return planner_output

    first_day = f"{year:04d}-{month:02d}-01"
    last_day = calendar.monthrange(year, month)[1]
    last_date = f"{year:04d}-{month:02d}-{last_day:02d}"

    # Only override if planner either had no date_filter or a single date inside this month
    df = planner_output.get("date_filter") or {}
    single = df.get("single")
    if df and df.get("from") and df.get("to"):
        # Already a range; leave as-is
        return planner_output
    if single:
        # If the single date is already within this month, we still prefer the full month range.
        if not isinstance(single, str) or not single.startswith(f"{year:04d}-{month:02d}-"):
            # Different month; don't override
            return planner_output

    out = dict(planner_output)
    out["date_filter"] = {"from": first_day, "to": last_date}
    out["dates"] = [first_day, last_date]
    # Month queries are always row_date based
    out["date_filter_type"] = out.get("date_filter_type", "row_date")
    return out


def _build_clarification_question(planner_output: Dict[str, Any]) -> str:
    """
    Build a clarification question from planner output when confidence < 0.7.
    Example: "Did you mean GST for 12 Jan 2025?"
    """
    if not planner_output:
        return "Your question seems ambiguous. Please add a date or rephrase (e.g. 'GST on 12 Jan 2025')."
    intent = (planner_output.get("intent") or "other").strip().lower()
    metric = (planner_output.get("metric") or "").strip() or None
    date_filter = planner_output.get("date_filter") or {}
    client = planner_output.get("client_tag") or planner_output.get("client")
    if client is not None:
        client = str(client).strip() or None

    # Prefer metric for display (e.g. GST, expense); fallback to intent phrase
    metric_phrase = (metric or "").capitalize() if metric else None
    if not metric_phrase:
        intent_phrases = {
            "gst_summary": "GST",
            "trend": "trend over time",
            "compare_dates": "comparison by date",
            "expense_breakdown": "expense breakdown",
            "distribution": "distribution",
            "single_value": "totals",
        }
        metric_phrase = intent_phrases.get(intent, "this")

    parts = []
    if metric_phrase:
        parts.append(metric_phrase)
    if date_filter.get("single"):
        date_str = _format_date_readable(date_filter["single"])
        if date_str:
            parts.append(f"for {date_str}")
    elif date_filter.get("from") and date_filter.get("to"):
        from_str = _format_date_readable(date_filter["from"])
        to_str = _format_date_readable(date_filter["to"])
        if from_str and to_str:
            parts.append(f"for {from_str} to {to_str}")
    if client:
        parts.append(f"for client {client}")

    if parts:
        return "Did you mean " + " ".join(parts) + "?"
    return (
        "Your question seems ambiguous. Please add a date or date range, "
        "client name, or rephrase (e.g. 'GST on 12 Jan 2025', 'expense trend for Jan 2025')."
    )


def _serialize_row_value(v: Any) -> Any:
    """Convert row value to JSON/display-safe type for summary table."""
    if v is None:
        return None
    if hasattr(v, "isoformat") and str(v) != "NaT":
        return v.isoformat()[:10] if hasattr(v, "strftime") else str(v)
    if isinstance(v, (int, float, str, bool)):
        if isinstance(v, float):
            try:
                import math
                if math.isnan(v):
                    return None
            except Exception:
                pass
        return v
    return str(v)


def _rows_to_table_data(rows: List[Dict[str, Any]], limit: int = 200) -> List[Dict[str, Any]]:
    """Serialize first N rows for display (exclude _id). Used for summarize intent."""
    out = []
    for r in rows[:limit]:
        row = {}
        for k, v in r.items():
            if k == "_id":
                continue
            row[k] = _serialize_row_value(v)
        out.append(row)
    return out


def _empty_response(original_query: str, normalized_query: str, correction_map: dict) -> dict:
    """Shared empty/chart-less response shape."""
    return {
        "answer": "",
        "needs_chart": False,
        "chart_type": None,
        "chart_data": None,
        "chart_fallback_table": False,
        "chart_fallback_message": "",
        "table_data": None,
        "show_data_table": False,
        "original_query": original_query,
        "normalized_query": normalized_query,
        "correction_map": correction_map,
        "is_clarification": False,
    }


def run(query: str, clarification_context: Optional[Dict[str, Any]] = None) -> dict:
    """
    Run pipeline with strict routing, schema awareness, data existence guard, smart defaults.
    clarification_context: {"normalized_query": str, "confirmed": bool} — if confirmed for same query, never ask again; use defaults.
    """
    if not query or not str(query).strip():
        out = _empty_response("", "", {})
        out["answer"] = "Please ask a question (e.g. GST on 12 Jan 2025, how many rows, give chart)."
        return out

    original_query = str(query).strip()
    norm_result = normalize_query(original_query)
    normalized_query = norm_result.get("normalized_query") or original_query
    correction_map = norm_result.get("correction_map") or {}

    logger.info("original query: %s", original_query)
    logger.info("normalized query: %s", normalized_query)
    logger.info("corrections applied: %s", correction_map)

    planner_output = plan(normalized_query)
    policy_result = check_policy(normalized_query, planner_output)
    action = (policy_result.get("action") or "allow").strip().lower()
    policy_message = policy_result.get("message") or ""

    if action == "block":
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = policy_message or "I can't assist with that request."
        return out
    if action == "clarify":
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = policy_message or "Please specify the date or client you're asking about."
        return out

    # Deterministic fixes on top of planner output BEFORE routing / data:
    # - Ensure upload-date semantics when user explicitly talks about uploaded file
    # - Expand month-only queries (e.g. "Feb 2026") to full month ranges
    planner_output = _maybe_force_upload_date(planner_output, normalized_query)
    planner_output = _expand_month_range_if_needed(planner_output, normalized_query)

    # Strict router BEFORE DataAgent (deterministic, logged)
    route_type = route_query_type(planner_output, normalized_query)
    logger.info("router_decision: %s", route_type)

    # Schema query: answer from metadata only; DO NOT call DataAgent or Analyst
    if route_type == SCHEMA_QUERY:
        schema = mongo.get_latest_file_schema()
        answer = _build_schema_answer(schema, normalized_query)
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = answer
        logger.info("data_row_count: N/A (schema_query)")
        logger.info("chart_rendered: false")
        return out

    # For non-schema queries, default scope to latest file so that "today's" or
    # latest-upload questions operate on the most recent upload only, unless
    # a specific uploadDate/file filter is already present (date_filter_type=upload_date)
    # or an explicit file_id is set.
    latest_meta = mongo.get_latest_file_meta()
    latest_file_id = latest_meta.get("file_id") if latest_meta else None
    date_filter_type = (planner_output.get("date_filter_type") or "row_date").strip().lower()
    if latest_file_id and route_type != SCHEMA_QUERY and date_filter_type != "upload_date":
        # Do not override if caller already specified a file_id.
        if not planner_output.get("file_id"):
            planner_output = dict(planner_output)
            planner_output["file_id"] = latest_file_id

    confidence = float(planner_output.get("confidence", 1.0))
    clarification_confirmed = (
        clarification_context is not None
        and clarification_context.get("confirmed") is True
        and clarification_context.get("normalized_query") == normalized_query
    )

    # Clarification: only if confidence < 0.4 and not already confirmed (one per query; no infinite loops)
    if confidence < CLARIFICATION_CONFIDENCE_THRESHOLD and not clarification_confirmed:
        clarification = _build_clarification_question(planner_output)
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = clarification
        out["is_clarification"] = True
        logger.info("data_row_count: N/A (clarification)")
        logger.info("chart_rendered: false")
        return out

    # Vague query: apply smart defaults (metric, full date range, line chart)
    if route_type == VAGUE_QUERY:
        planner_output = _apply_smart_defaults(planner_output, normalized_query)

    data = fetch_data(planner_output, normalized_query)
    rows = data.get("rows") or []
    data_row_count = len(rows)
    logger.info("data_row_count: %s", data_row_count)

    # Data existence guard: if rows == 0, DO NOT call Analyst; explain and suggest alternatives
    if data_row_count == 0:
        client_tag = planner_output.get("client_tag") or planner_output.get("client")
        answer = _build_no_data_explanation(planner_output, client_tag, normalized_query)
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = answer
        logger.info("chart_rendered: false")
        return out

    # AnalystAgent runs only if rows > 0 (never invent totals)
    intent = planner_output.get("intent") or "other"
    analyst_output = analyze(intent, data)

    # Summarize intent: enrich with schema column names and attach raw data sample for UI
    show_data_table = False
    summary_table_data: Optional[List[Dict[str, Any]]] = None
    if intent == "summarize":
        schema = mongo.get_latest_file_schema()
        column_names = schema.get("column_names") or []
        if column_names:
            analyst_output = dict(analyst_output)
            analyst_output["column_names"] = column_names
        summary_table_data = _rows_to_table_data(rows, limit=200)
        show_data_table = True

    answer = respond(
        planner_output,
        analyst_output,
        normalized_query,
        policy_action=action,
        policy_message=policy_message if action == "reframe" else None,
    )

    needs_chart = bool(planner_output.get("needs_chart", False))
    chart_type = planner_output.get("chart_type") or analyst_output.get("chart_type")
    chart_scope = planner_output.get("chart_scope")
    chart_data = None
    if analyst_output.get("series"):
        chart_data = {
            "x": [s.get("date") for s in analyst_output["series"]],
            "y": [s.get("value") for s in analyst_output["series"]],
            "labels": ["date", "value"],
        }
    elif analyst_output.get("breakdown"):
        chart_data = {
            "x": [b.get("category") for b in analyst_output["breakdown"]],
            "y": [b.get("amount") for b in analyst_output["breakdown"]],
            "labels": ["category", "amount"],
        }
    elif analyst_output.get("compare"):
        chart_data = {
            "x": [c.get("date") for c in analyst_output["compare"]],
            "y": [c.get("total") for c in analyst_output["compare"]],
            "labels": ["date", "total"],
        }
    if chart_data is not None and chart_scope:
        chart_data["title"] = chart_scope

    # Render chart ONLY if needs_chart and chart validation passes; otherwise show dataframe
    chart_fallback_table = False
    chart_fallback_message = ""
    table_data: Optional[List[Dict[str, Any]]] = None
    if chart_data is not None and chart_type:
        labels = chart_data.get("labels") or ["x", "y"]
        x_label = labels[0] if len(labels) > 0 else "x"
        y_label = labels[1] if len(labels) > 1 else "y"
        df_chart = pd.DataFrame({x_label: chart_data.get("x") or [], y_label: chart_data.get("y") or []})
        if not needs_chart:
            chart_fallback_table = True
            chart_fallback_message = "Showing data as table (chart not requested for this query)."
            table_data = df_chart.to_dict(orient="records")
            chart_type = None
            chart_data = None
        elif not validate_chart(df_chart, planner_output):
            chart_fallback_table = True
            chart_fallback_message = "Not enough data to generate chart, showing table instead."
            table_data = df_chart.to_dict(orient="records")
            chart_type = None
            chart_data = None

    chart_rendered = bool(chart_type and chart_data and not chart_fallback_table)
    logger.info("chart_rendered: %s", chart_rendered)

    # For summarize: show raw data table; do not override table_data if chart fallback already set it
    out_table_data = table_data
    if show_data_table and summary_table_data is not None:
        out_table_data = summary_table_data

    return {
        "answer": answer,
        "needs_chart": needs_chart,
        "chart_type": chart_type,
        "chart_data": chart_data,
        "chart_fallback_table": chart_fallback_table,
        "chart_fallback_message": chart_fallback_message,
        "table_data": out_table_data,
        "show_data_table": show_data_table,
        "original_query": original_query,
        "normalized_query": normalized_query,
        "correction_map": correction_map,
        "is_clarification": False,
    }
