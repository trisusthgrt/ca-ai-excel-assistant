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
from utils.semantic_column_resolver import (
    resolve_semantic_columns,
    build_clarification_message,
    needs_clarification,
    get_amount_column_for_metric,
    get_date_column,
    get_breakdown_column_for_term,
)
from utils.query_router import (
    route_query_type,
    is_schema_query_by_text,
    SCHEMA_QUERY,
    DATA_QUERY,
    BREAKDOWN_QUERY,
    TREND_QUERY,
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
    """
    Answer schema_query from stored metadata ONLY.
    SCHEMA AUTHORITY: Use original_column_names (exact Excel headers) in user responses.
    Include row_count, column_count, min_date, max_date. NEVER use normalized names in answers.
    """
    if not schema:
        return "No file has been uploaded yet. Upload an Excel file to see column and row information."
    q = (query or "").strip().lower()
    # Original Excel headers for user-facing answers
    original_names = schema.get("original_column_names") or schema.get("column_names") or []
    column_count = schema.get("column_count") or len(original_names)
    row_count = schema.get("row_count") or 0
    min_date = schema.get("min_date")
    max_date = schema.get("max_date")

    if "name" in q and ("attribute" in q or "column" in q):
        if not original_names:
            return "No column names are stored for the latest file."
        return "The attributes (columns) present are: **" + "**, **".join(str(c) for c in original_names) + "**."

    if re.search(r"\b(?:how\s+many\s+)?columns?\b", q):
        return f"There are **{column_count}** column(s) in the latest uploaded file."
    if re.search(r"\b(?:how\s+many\s+)?rows?\b", q):
        return f"There are **{row_count}** row(s) in the latest uploaded file."
    if re.search(r"\b(?:how\s+many\s+)?attributes?\b", q):
        return f"There are **{column_count}** attribute(s) (columns) in the latest uploaded file."
    if re.search(r"\b(?:what|which)\s+(?:are\s+)?(?:the\s+)?(?:column|attribute)s?\b", q) or "attributes" in q:
        if not original_names:
            return "No column names are stored for the latest file."
        return "The attributes (columns) present are: **" + "**, **".join(str(c) for c in original_names) + "**."
    
    # Default schema summary: original column names, optional date range from metadata
    if original_names:
        cols_str = ", ".join(str(c) for c in original_names[:15]) + ("..." if len(original_names) > 15 else "")
    else:
        cols_str = "(column names not stored)"
    out = f"The latest uploaded file has **{column_count}** columns and **{row_count}** rows. Columns: {cols_str}"
    if min_date and max_date:
        out += f" Data in this file spans from **{min_date}** to **{max_date}**."
    return out


def _build_no_data_explanation(
    planner_output: Dict[str, Any],
    client_tag: Optional[str],
    normalized_query: str,
    file_id: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> str:
    """Explain why no data exists; suggest nearby dates scoped to latest file (single-dataset authority)."""
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
    nearby = mongo.get_nearby_dates_for_client(client_tag=client, file_id=file_id, limit=5)
    if nearby:
        dates_str = ", ".join(nearby[:5])
        if client:
            parts.append(f" Data exists for this client on other dates, e.g. {dates_str}.")
        else:
            parts.append(f" Data exists on other dates, e.g. {dates_str}.")
    else:
        parts.append(" No data has been uploaded yet." if not client else " No data has been uploaded for this client yet.")
    if schema and (schema.get("min_date") or schema.get("max_date")):
        min_d = schema.get("min_date")
        max_d = schema.get("max_date")
        if min_d and max_d:
            parts.append(f" The latest file contains data from {min_d} to {max_d}.")
    return "".join(parts)


def _apply_smart_defaults(
    planner_output: Dict[str, Any],
    query: str,
    resolution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Vague query defaults: metric=NetValue, group_by=TransactionDate, full date range.
    DO NOT ask clarification. Defaults apply ONLY to vague queries.
    """
    q = (query or "").strip().lower()
    out = dict(planner_output)
    # Metric: NetValue (net_amount) unless query explicitly asks for GST/tax
    if not out.get("metric"):
        out["metric"] = "gst" if any(w in q for w in ("gst", "tax", "vat")) else "net_amount"
    else:
        # Keep planner metric but ensure we use NetValue for truly vague ("give chart", "show data")
        if not re.search(r"\b(gst|tax|net|total|discount|amount|value)\b", q):
            out["metric"] = "net_amount"
    # Full date range
    out["date_filter"] = {}
    out["dates"] = []
    # Default group_by to date column from resolution when available
    if resolution:
        date_col = (resolution.get("resolved_columns") or resolution.get("resolved") or {}).get("date")
        if date_col and not out.get("breakdown_by"):
            out["breakdown_by"] = date_col
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


def _expand_next_n_days_if_needed(planner_output: Dict[str, Any], query: str) -> Dict[str, Any]:
    """
    Detect queries like "next 3 days from X" or "next N days from Y" and expand to date range.
    Example: "next 3 days from 5 Mar 2026" -> from: 2026-03-05, to: 2026-03-07 (inclusive).
    
    Rules:
    - start_date = X (the base date)
    - end_date = X + (N - 1) days (so "next 3 days" means 3 days total: day 0, day 1, day 2)
    - Use inclusive filtering (>= start_date AND <= end_date)
    """
    if not planner_output:
        return planner_output
    
    q = (query or "").strip().lower()
    
    # Pattern: "next N days from X" or "next N days for X" or "N days from X"
    # Match: "next 3 days from 5 mar 2026" or "3 days from 2nd mar 2026"
    pattern = re.search(
        r"\bnext\s+(\d+)\s+days?\s+(?:from|for|starting|beginning)\s+(.+?)(?:\s|$)",
        q,
        re.IGNORECASE,
    )
    if not pattern:
        # Try without "next": "3 days from X"
        pattern = re.search(
            r"\b(\d+)\s+days?\s+(?:from|for|starting|beginning)\s+(.+?)(?:\s|$)",
            q,
            re.IGNORECASE,
        )
    
    if not pattern:
        return planner_output
    
    n_days = int(pattern.group(1))
    date_str = pattern.group(2).strip()
    
    # Parse the base date
    from datetime import datetime, timedelta
    base_date = None
    
    # Try common date formats
    date_formats = [
        "%d %b %Y",  # "5 Mar 2026"
        "%d %B %Y",  # "5 March 2026"
        "%d-%m-%Y",  # "05-03-2026"
        "%Y-%m-%d",  # "2026-03-05"
        "%d/%m/%Y",  # "05/03/2026"
    ]
    
    for fmt in date_formats:
        try:
            base_date = datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    
    if not base_date:
        # Try to extract from existing date_filter if planner already parsed it
        existing_filter = planner_output.get("date_filter") or {}
        if existing_filter.get("single"):
            try:
                base_date = datetime.strptime(existing_filter["single"], "%Y-%m-%d")
            except ValueError:
                pass
    
    if not base_date:
        return planner_output
    
    # Calculate range: start_date = base_date, end_date = base_date + (N - 1) days
    start_date = base_date
    end_date = base_date + timedelta(days=n_days - 1)
    
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    out = dict(planner_output)
    out["date_filter"] = {"from": start_str, "to": end_str}
    out["dates"] = [start_str, end_str]
    out["date_filter_type"] = out.get("date_filter_type", "row_date")
    
    logger.info("expanded_next_n_days: query=%s n_days=%s base_date=%s start=%s end=%s", 
                query, n_days, base_date.strftime("%Y-%m-%d"), start_str, end_str)
    
    return out


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

    # ------------------------------------------------------------------
    # EARLY SCHEMA DETECTION — before PlannerAgent / Semantic Resolver
    # Any question about rows/columns/attributes MUST use metadata ONLY.
    # This ensures "how many rows are in the given uploaded file" and
    # "how many rows are there" both get the same schema answer.
    # ------------------------------------------------------------------
    if is_schema_query_by_text(normalized_query):
        latest_schema = mongo.get_latest_file_schema()
        latest_meta = mongo.get_latest_file_meta()
        latest_file_id = latest_meta.get("file_id") if latest_meta else None
        answer = _build_schema_answer(latest_schema, normalized_query)
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = answer
        logger.info("router_decision: %s (early) file_id=%s data_row_count=N/A chart_rendered=false",
                   SCHEMA_QUERY, latest_file_id or "N/A")
        return out

    # ------------------------------------------------------------------
    # Semantic Column Resolver — structured resolution (NO query rewriting).
    # Resolver returns: resolved columns, group_by, unresolved/ambiguous.
    # PlannerAgent consumes this; it must NEVER parse column names from raw text.
    # ------------------------------------------------------------------
    latest_schema = mongo.get_latest_file_schema()
    latest_meta = mongo.get_latest_file_meta()
    latest_file_id = latest_meta.get("file_id") if latest_meta else None
    resolution = resolve_semantic_columns(normalized_query, latest_schema, file_id=latest_file_id)

    # Ask clarification ONLY if unresolved or ambiguous (once per query).
    if needs_clarification(resolution):
        msg = build_clarification_message(resolution, latest_schema)
        if msg:
            out = _empty_response(original_query, normalized_query, correction_map)
            out["answer"] = msg
            out["is_clarification"] = True
            logger.info("file_id=%s semantic_resolver: clarification (unresolved/ambiguous) chart_rendered=false",
                       latest_file_id or "N/A")
            return out

    # Plan with ORIGINAL query text (resolver does NOT rewrite query).
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
    # - Expand "next N days from X" queries to proper date ranges
    # - Expand month-only queries (e.g. "Feb 2026") to full month ranges
    planner_output = _maybe_force_upload_date(planner_output, normalized_query)
    planner_output = _expand_next_n_days_if_needed(planner_output, normalized_query)
    planner_output = _expand_month_range_if_needed(planner_output, normalized_query)

    # Merge structured resolution into planner_output (PlannerAgent never parses column names from text).
    # Entity resolution: always use resolved column names (e.g. "customer" -> CustomerName).
    planner_output = dict(planner_output)
    amount_col = get_amount_column_for_metric(resolution, planner_output.get("metric"))
    if amount_col:
        planner_output["amount_column"] = amount_col
    date_col = get_date_column(resolution)
    if date_col:
        planner_output["date_column"] = date_col
    resolved_map = resolution.get("resolved_columns") or resolution.get("resolved") or {}
    group_by = resolution.get("group_by") or []
    # Prefer resolved column names so "breakdown by customer/agency" uses CustomerName
    if group_by:
        planner_output["breakdown_by"] = group_by[0]
    elif planner_output.get("breakdown_by"):
        planner_breakdown = (planner_output.get("breakdown_by") or "").strip()
        # Concept name match (e.g. planner said "customer")
        if planner_breakdown and resolved_map.get(planner_breakdown.lower().replace(" ", "_")):
            planner_output["breakdown_by"] = resolved_map[planner_breakdown.lower().replace(" ", "_")]
        else:
            # Variant match: planner said "agency" -> customer -> CustomerName
            col = get_breakdown_column_for_term(planner_breakdown, resolution)
            if col:
                planner_output["breakdown_by"] = col

    # ======================================================================
    # STRICT ROUTER — BEFORE DataAgent (deterministic, logged)
    # ======================================================================
    route_type = route_query_type(planner_output, normalized_query)
    
    # Log router decision with file_id
    logger.info("router_decision: %s file_id=%s", route_type, latest_file_id or "N/A")

    # ======================================================================
    # SCHEMA QUERY — NEVER touch DataAgent/Analyst/RAG
    # ======================================================================
    if route_type == SCHEMA_QUERY:
        answer = _build_schema_answer(latest_schema, normalized_query)
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = answer
        logger.info("file_id=%s router=%s data_row_count=N/A chart_rendered=false", 
                   latest_file_id or "N/A", route_type)
        return out

    # ======================================================================
    # SINGLE SOURCE OF TRUTH — enforce latest file_id for ALL non-schema queries
    # ======================================================================
    date_filter_type = (planner_output.get("date_filter_type") or "row_date").strip().lower()
    if latest_file_id and date_filter_type != "upload_date":
        # ALWAYS inject latest file_id unless explicitly filtering by upload_date
        if not planner_output.get("file_id"):
            planner_output = dict(planner_output)
            planner_output["file_id"] = latest_file_id
            logger.info("enforced_latest_file: file_id=%s", latest_file_id)

    confidence = float(planner_output.get("confidence", 1.0))
    clarification_confirmed = (
        clarification_context is not None
        and clarification_context.get("confirmed") is True
        and clarification_context.get("normalized_query") == normalized_query
    )

    # ======================================================================
    # METRIC SAFETY — if user asked for a metric that is unresolved, error (no fallback)
    # ======================================================================
    unresolved = resolution.get("unresolved_concepts") or []
    metric_concepts = {"gst_amount", "net_amount", "total_amount", "discount", "cgst_amount", "sgst_amount", "igst_amount"}
    requested_metric = (planner_output.get("metric") or "").strip().lower()
    q_lower = normalized_query.lower()
    for concept in unresolved:
        if concept not in metric_concepts:
            continue
        if concept == "gst_amount" and ("gst" in q_lower or "tax" in q_lower or requested_metric in ("gst", "tax")):
            out = _empty_response(original_query, normalized_query, correction_map)
            out["answer"] = "A column for GST/tax amount does not exist in the latest uploaded file."
            logger.info("file_id=%s metric_safety: requested=gst unresolved", latest_file_id or "N/A")
            return out
        if concept == "discount" and ("discount" in q_lower or requested_metric == "discount"):
            out = _empty_response(original_query, normalized_query, correction_map)
            out["answer"] = "A column for discount does not exist in the latest uploaded file."
            logger.info("file_id=%s metric_safety: requested=discount unresolved", latest_file_id or "N/A")
            return out
        if concept == "net_amount" and ("net" in q_lower or requested_metric == "net_amount"):
            out = _empty_response(original_query, normalized_query, correction_map)
            out["answer"] = "A column for net amount does not exist in the latest uploaded file."
            logger.info("file_id=%s metric_safety: requested=net_amount unresolved", latest_file_id or "N/A")
            return out
        if concept == "total_amount" and ("total" in q_lower or "gross" in q_lower or requested_metric in ("total", "gross")):
            out = _empty_response(original_query, normalized_query, correction_map)
            out["answer"] = "A column for total/gross amount does not exist in the latest uploaded file."
            logger.info("file_id=%s metric_safety: requested=total_amount unresolved", latest_file_id or "N/A")
            return out

    # ======================================================================
    # BREAKDOWN QUERY — verify column exists before processing (schema authority: list original names)
    # ======================================================================
    if route_type == BREAKDOWN_QUERY:
        breakdown_by = planner_output.get("breakdown_by")
        if breakdown_by:
            column_names = latest_schema.get("column_names") or []
            original_names = latest_schema.get("original_column_names") or column_names
            breakdown_normalized = breakdown_by.lower().replace("_", "").replace(" ", "")
            matching_col = None
            for col in column_names:
                if col.lower().replace("_", "").replace(" ", "") == breakdown_normalized:
                    matching_col = col
                    break
            if not matching_col:
                cols_str = ", ".join(str(c) for c in original_names) if original_names else "no columns"
                out = _empty_response(original_query, normalized_query, correction_map)
                out["answer"] = (
                    f"I couldn't find a column named '{breakdown_by}' in the latest uploaded file. "
                    f"Available columns are: {cols_str}. "
                    f"Please use one of these column names for the breakdown."
                )
                logger.info("file_id=%s router=%s breakdown_column_not_found=%s available_columns=%s",
                           latest_file_id or "N/A", route_type, breakdown_by, original_names)
                return out
            
            # Column exists — update breakdown_by to exact column name
            if matching_col != breakdown_by:
                planner_output = dict(planner_output)
                planner_output["breakdown_by"] = matching_col
                logger.info("breakdown_column_resolved: user_term=%s resolved_column=%s", 
                           breakdown_by, matching_col)

    # ======================================================================
    # VAGUE QUERY — apply defaults WITHOUT clarification
    # metric=NetValue, group_by=TransactionDate (date column), full date range
    # ======================================================================
    if route_type == VAGUE_QUERY:
        planner_output = _apply_smart_defaults(planner_output, normalized_query, resolution=resolution)
        logger.info("vague_query_defaults_applied: metric=%s breakdown_by=%s date_range=full chart_type=%s",
                   planner_output.get("metric"), planner_output.get("breakdown_by"), planner_output.get("chart_type"))

    # ======================================================================
    # CLARIFICATION — only if confidence < 0.4 (NOT for vague queries with defaults)
    # ======================================================================
    if confidence < CLARIFICATION_CONFIDENCE_THRESHOLD and not clarification_confirmed and route_type != VAGUE_QUERY:
        clarification = _build_clarification_question(planner_output)
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = clarification
        out["is_clarification"] = True
        logger.info("file_id=%s router=%s data_row_count=N/A chart_rendered=false (clarification)",
                   latest_file_id or "N/A", route_type)
        return out

    # ======================================================================
    # DATA FETCHING — structured data ONLY (NO RAG for data/breakdown/trend queries)
    # ======================================================================
    date_filter = planner_output.get("date_filter") or {}
    date_filter_type = (planner_output.get("date_filter_type") or "row_date").strip().lower()
    
    # Log final computed date range
    if date_filter_type == "row_date":
        if date_filter.get("single"):
            logger.info("date_range_used: type=row_date single=%s (inclusive)", date_filter["single"])
        elif date_filter.get("from") and date_filter.get("to"):
            logger.info("date_range_used: type=row_date from=%s to=%s (inclusive)", 
                       date_filter["from"], date_filter["to"])
    elif date_filter_type == "upload_date":
        upload_d = date_filter.get("single") or date_filter.get("from")
        if upload_d:
            logger.info("date_range_used: type=upload_date upload_date=%s", upload_d)
    
    # Fetch data — RAG is FORBIDDEN for data/breakdown/trend queries
    # (RAG only allowed for explanation_query, handled separately)
    use_rag = (route_type == EXPLANATION_QUERY)
    data = fetch_data(planner_output, normalized_query if use_rag else None)
    rows = data.get("rows") or []
    data_row_count = len(rows)
    
    # Per-query consolidated log (production): file_id, router, concepts, resolved, unresolved, group_by, filters, date_range, row_count, rag_used
    resolved_map = resolution.get("resolved_columns") or resolution.get("resolved") or {}
    resolution_group_by = resolution.get("group_by") or []
    resolution_filters = resolution.get("filters") or {}
    date_filter = planner_output.get("date_filter") or {}
    date_range_str = str(date_filter.get("from") or date_filter.get("single") or "") + ".." + str(date_filter.get("to") or date_filter.get("single") or "")
    detected = list(resolved_map.keys()) + (resolution.get("unresolved_concepts") or []) + (resolution.get("ambiguous_concepts") or [])
    logger.info(
        "query_log: file_id=%s router_decision=%s detected_concepts=%s resolved_columns=%s unresolved_concepts=%s group_by=%s filters=%s date_range=%s row_count_after_filter=%s rag_used=%s",
        latest_file_id or "N/A",
        route_type,
        detected,
        resolved_map,
        resolution.get("unresolved_concepts") or [],
        resolution_group_by,
        resolution_filters,
        date_range_str or "full",
        data_row_count,
        use_rag,
    )

    # ======================================================================
    # DATA EXISTENCE GUARD — if rows == 0, explain WHY and mention available date range
    # ======================================================================
    if data_row_count == 0:
        client_tag = planner_output.get("client_tag") or planner_output.get("client")
        answer = _build_no_data_explanation(
            planner_output, client_tag, normalized_query,
            file_id=latest_file_id, schema=latest_schema,
        )
        out = _empty_response(original_query, normalized_query, correction_map)
        out["answer"] = answer
        logger.info("file_id=%s router=%s data_row_count=0 chart_rendered=false",
                   latest_file_id or "N/A", route_type)
        return out

    # ======================================================================
    # ANALYST AGENT — pure computation (NO LLM, NO RAG)
    # ======================================================================
    intent = planner_output.get("intent") or "other"
    breakdown_by = planner_output.get("breakdown_by")
    
    # For breakdown queries, ensure breakdown_by is set
    if route_type == BREAKDOWN_QUERY and not breakdown_by:
        # Try to extract from query if not set by planner
        breakdown_match = re.search(r"\b(?:breakdown\s+by|by|per)\s+([A-Z][a-zA-Z]+)", normalized_query, re.IGNORECASE)
        if breakdown_match:
            breakdown_by = breakdown_match.group(1)
            planner_output = dict(planner_output)
            planner_output["breakdown_by"] = breakdown_by
    
    amount_column = planner_output.get("amount_column")
    analyst_output = analyze(intent, data, breakdown_by=breakdown_by, amount_column=amount_column)
    
    # For breakdown queries, verify breakdown was computed (never fallback to totals)
    if route_type == BREAKDOWN_QUERY:
        if not analyst_output.get("breakdown"):
            # Breakdown failed — explain why
            out = _empty_response(original_query, normalized_query, correction_map)
            out["answer"] = (
                f"Could not compute breakdown by '{breakdown_by}'. "
                f"This column may not contain distinct values, or the data may be empty for the selected date range."
            )
            logger.info("file_id=%s router=%s breakdown_failed=%s",
                       latest_file_id or "N/A", route_type, breakdown_by)
            return out

    # Summarize intent: enrich with schema column names and attach raw data sample for UI
    show_data_table = False
    summary_table_data: Optional[List[Dict[str, Any]]] = None
    if intent == "summarize":
        schema = mongo.get_latest_file_schema()
        # Schema authority: expose original Excel headers to responder (not normalized names)
        original_names = schema.get("original_column_names") or schema.get("column_names") or []
        if original_names:
            analyst_output = dict(analyst_output)
            analyst_output["column_names"] = original_names
        summary_table_data = _rows_to_table_data(rows, limit=200)
        show_data_table = True

    # ======================================================================
    # RESPONDER AGENT — generate answer (RAG ONLY for explanation_query)
    # ======================================================================
    # For explanation queries, pass query for RAG; for others, pass None to disable RAG
    responder_query = normalized_query if route_type == EXPLANATION_QUERY else None
    
    answer = respond(
        planner_output,
        analyst_output,
        responder_query,
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

    # ======================================================================
    # CHART RENDERING — only for trend queries with >= 2 data points
    # ======================================================================
    chart_rendered = False
    if route_type == TREND_QUERY:
        # Trend queries require >= 2 data points for chart
        if analyst_output.get("series") and len(analyst_output["series"]) >= 2:
            chart_rendered = bool(chart_type and chart_data and not chart_fallback_table)
        else:
            chart_rendered = False
            chart_fallback_table = True
            chart_fallback_message = "Trend chart requires at least 2 data points. Showing table instead."
    else:
        chart_rendered = bool(chart_type and chart_data and not chart_fallback_table)
    
    logger.info("file_id=%s router=%s chart_rendered=%s",
               latest_file_id or "N/A", route_type, chart_rendered)

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
