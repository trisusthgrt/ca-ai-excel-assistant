"""
Data agent — queries MongoDB and ChromaDB with planner filters (Step 6).
Returns dict with rows + cached daily/monthly aggregations. Checks cache first; recomputes on miss.
"""
from typing import Any, Dict, Optional

from db import mongo
from vector import chroma_client
from utils.aggregation_cache import (
    build_key,
    get as cache_get,
    set_value as cache_set,
    compute_daily_totals,
    compute_monthly_totals,
)
from utils.query_router import route_query

# Map intent to semantic search query for ChromaDB
INTENT_TO_QUERY = {
    "gst_summary": "GST amount tax",
    "expense_breakdown": "expenses category amount",
    "trend": "amount date trend over time",
    "compare_dates": "compare amount date",
    "other": "amount date description",
}


def fetch_data(planner_output: dict, query: Optional[str] = None) -> Dict[str, Any]:
    """
    Query with planner filters. Check aggregation cache first; on miss fetch from MongoDB.
    Use vector search (ChromaDB) only when route_query returns "vector_search"; skip for direct_db.
    Returns dict: {"rows": list, "daily_totals": list, "monthly_totals": list}.
    """
    if not planner_output:
        return {"rows": [], "daily_totals": [], "monthly_totals": []}

    cache_key = build_key(planner_output)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    import logging
    logger = logging.getLogger(__name__)

    intent = (planner_output.get("intent") or "other").strip().lower()
    dates = planner_output.get("dates") or []
    date_filter = planner_output.get("date_filter") or {}
    date_filter_type = (planner_output.get("date_filter_type") or "row_date").strip().lower()
    file_id = planner_output.get("file_id") or None
    if file_id is not None:
        file_id = str(file_id).strip() or None
    client_tag = planner_output.get("client_tag")
    if client_tag is not None:
        client_tag = str(client_tag).strip() or None

    # SINGLE DATASET AUTHORITY: non-upload_date queries MUST have file_id (latest file)
    if date_filter_type != "upload_date" and not file_id:
        logger.warning("data_agent: abort - file_id required for row_date query (single dataset authority)")
        return {"rows": [], "daily_totals": [], "monthly_totals": []}

    # Filter by upload date (when file was uploaded) or by row date (date in the dataset)
    # Use date_filter (from/to) for proper range handling; dates list is legacy fallback
    upload_date_filter = None
    row_date_from = None
    row_date_to = None
    
    if date_filter_type == "upload_date":
        upload_date_filter = date_filter.get("single") or date_filter.get("from") or (min(dates) if dates else None)
        if upload_date_filter:
            upload_date_filter = str(upload_date_filter).strip()
    else:
        # Use date_filter.from/to for proper range handling (inclusive)
        if date_filter.get("single"):
            row_date_from = str(date_filter["single"]).strip()
            row_date_to = row_date_from  # Single date: same for from and to
        elif date_filter.get("from") and date_filter.get("to"):
            row_date_from = str(date_filter["from"]).strip()
            row_date_to = str(date_filter["to"]).strip()
        elif dates:
            # Legacy fallback: use dates list
            row_date_from = min(dates)
            row_date_to = max(dates) if len(dates) > 1 else min(dates)
    
    if row_date_from and row_date_to:
        logger.info("data_agent_date_filter: type=row_date from=%s to=%s (inclusive)", row_date_from, row_date_to)
    elif upload_date_filter:
        logger.info("data_agent_date_filter: type=upload_date upload_date=%s", upload_date_filter)
    
    rows = mongo.find_rows(
        upload_date=upload_date_filter,
        client_tag=client_tag,
        row_date_from=row_date_from,
        row_date_to=row_date_to,
        file_id=file_id,
        limit=500,
    )

    # ======================================================================
    # RAG USAGE — ONLY for explanation queries (explain, summarize, insights, "why")
    # RAG is FORBIDDEN for: schema queries, totals, filters, group-by, trends
    # ======================================================================
    route = route_query(planner_output, query)
    if route == "vector_search":
        # RAG allowed only for summarize/explain. Retrieval MUST be scoped to latest file_id.
        where_chroma = {}
        if file_id:
            where_chroma["fileId"] = file_id  # MANDATORY: scope to latest file
        if client_tag:
            where_chroma["clientTag"] = client_tag
        if row_date_from and row_date_from == row_date_to:
            where_chroma["rowDate"] = row_date_from
        try:
            search_text = INTENT_TO_QUERY.get(intent, INTENT_TO_QUERY["other"])
            chroma_results = chroma_client.query(
                text=search_text,
                n_results=min(100, max(20, len(rows) or 20)),
                where=where_chroma if where_chroma else None,
            )
            # Safety: if any chunk has different fileId, do not use RAG context (abort RAG path)
            if file_id and chroma_results:
                for item in chroma_results:
                    if str((item.get("metadata") or {}).get("fileId") or "") != str(file_id):
                        logger.warning("data_agent: RAG abort - retrieved chunk from different file_id")
                        chroma_results = []
                        break
            # Currently chroma results are not passed to responder; when they are, use only chroma_results here
        except Exception:
            pass
    # For non-explanation queries, RAG is skipped (structured data only)

    daily_totals = compute_daily_totals(rows)
    monthly_totals = compute_monthly_totals(rows)
    result = {
        "rows": rows,
        "daily_totals": daily_totals,
        "monthly_totals": monthly_totals,
    }
    cache_set(cache_key, result)
    return result
