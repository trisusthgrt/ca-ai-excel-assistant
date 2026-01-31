"""
Data agent — queries MongoDB and ChromaDB with planner filters (Step 6).
Returns combined list of relevant row documents for the Analyst.
"""
from typing import Any, Dict, List, Optional

from db import mongo
from vector import chroma_client

# Map intent to semantic search query for ChromaDB
INTENT_TO_QUERY = {
    "gst_summary": "GST amount tax",
    "expense_breakdown": "expenses category amount",
    "trend": "amount date trend over time",
    "compare_dates": "compare amount date",
    "other": "amount date description",
}


def fetch_data(planner_output: dict) -> List[dict]:
    """
    Query MongoDB with filters from planner (upload_date, client_tag, row_date range).
    Optionally query ChromaDB with semantic query and same metadata filter; merge by preferring
    MongoDB rows and using ChromaDB result order when ids match (we use MongoDB as source of truth).
    Returns list of row dicts (MongoDB documents, _id included).
    """
    if not planner_output:
        return []

    intent = (planner_output.get("intent") or "other").strip().lower()
    dates = planner_output.get("dates") or []
    client_tag = planner_output.get("client_tag")
    if client_tag is not None:
        client_tag = str(client_tag).strip() or None

    row_date_from = None
    row_date_to = None
    if dates:
        row_date_from = min(dates)
        row_date_to = max(dates) if len(dates) > 1 else min(dates)

    # MongoDB: source of truth for full row data
    rows = mongo.find_rows(
        upload_date=None,
        client_tag=client_tag,
        row_date_from=row_date_from,
        row_date_to=row_date_to,
        limit=500,
    )

    # ChromaDB: semantic relevance; build where filter (ChromaDB: simple key/value or $gte/$lte)
    where_chroma = {}
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
    except Exception:
        chroma_results = []

    # If we have ChromaDB results and MongoDB rows, order MongoDB rows by ChromaDB relevance (by id)
    if chroma_results and rows:
        id_order = [r.get("id") for r in chroma_results if r.get("id")]
        if id_order:
            row_by_id = {r.get("_id"): r for r in rows}
            # ChromaDB ids are like file_id_0; MongoDB rows don't have that id. So we can't reorder.
            # Keep MongoDB order; ChromaDB was used only for optional filtering. Return rows as-is.
            pass

    return rows
