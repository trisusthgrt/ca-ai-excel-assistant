"""
Query router — strict routing BEFORE DataAgent.
1. route_query_type: schema_query | data_query | vague_query | explanation_query (deterministic, logged).
2. route_query: direct_db | vector_search (for data/explanation when fetching).
"""
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Query type for pipeline routing (BEFORE DataAgent)
# Each query MUST be classified into EXACTLY ONE of these types
SCHEMA_QUERY = "schema_query"
DATA_QUERY = "data_query"
BREAKDOWN_QUERY = "breakdown_query"
TREND_QUERY = "trend_query"
VAGUE_QUERY = "vague_query"
EXPLANATION_QUERY = "explanation_query"

# Keywords for schema questions (deterministic)
# NOTE: We err on the side of over-matching here so that follow‑up questions like
# "names of the attribute" or "attribute names" or "rows in the given uploaded file"
# are still routed to schema_query and answered from metadata ONLY (never from analyst/responder).
SCHEMA_PATTERNS = [
    r"\bhow\s+many\s+columns?\b",
    r"\bhow\s+many\s+rows?\b",
    r"\bhow\s+many\s+attributes?\b",
    r"\b(?:number\s+of|count\s+of)\s+rows?\b",
    r"\brows?\s+in\s+(?:the\s+)?(?:uploaded\s+)?file\b",
    r"\b(?:uploaded\s+)?file\s+.*\s+rows?\b",
    r"\bwhat\s+(?:are\s+)?(?:the\s+)?(?:column|attribute)s?\b",
    r"\bcolumn\s+names?\b",
    r"\bschema\s+info\b",
    r"\battributes?\s+present\b",
    r"\bwhat\s+attributes\b",
    r"\bwhich\s+columns\b",
    r"\bnames?\s+of\s+the\s+attributes?\b",
    r"\bnames?\s+of\s+attributes?\b",
    r"\battribute\s+names?\b",
    r"\battributes?\s+name[s]?\b",
    r"\brows?\s+are\s+there\b",
    r"\brows?\s+there\s+are\b",
]

# Keywords for vague / generic (infer defaults)
VAGUE_PATTERNS = [
    r"\bgive\s+chart\b",
    r"\bshow\s+(?:me\s+)?(?:the\s+)?data\b",
    r"\bdisplay\s+data\b",
    r"\bget\s+chart\b",
    r"\bplot\s+(?:it|data)\b",
]

# Intents that are explanation (why, explain, summarize)
EXPLANATION_INTENTS = {"explain", "summarize", "insights", "why"}
VECTOR_INTENTS = EXPLANATION_INTENTS


def is_schema_query_by_text(query: Optional[str] = None) -> bool:
    """
    Deterministic check: does the query text alone indicate a schema question?
    Use this BEFORE planner/semantic resolver so that row/column/attribute
    questions always get metadata-only answers.
    """
    if not query or not str(query).strip():
        return False
    q = str(query).strip().lower()
    for pat in SCHEMA_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            return True
    return False


def route_query_type(planner_output: Dict[str, Any], query: Optional[str] = None) -> str:
    """
    Classify query into exactly one type. Deterministic. Caller must log.
    Returns: schema_query | data_query | breakdown_query | trend_query | vague_query | explanation_query
    
    STRICT RULES:
    - Schema queries: NEVER touch DataAgent/Analyst/RAG
    - Breakdown queries: Verify column exists before processing
    - Trend queries: Require date + numeric columns
    - Explanation queries: Only these can use RAG
    """
    q = (query or "").strip().lower()
    intent = (planner_output.get("intent") or "").strip().lower() if planner_output else ""
    breakdown_by = planner_output.get("breakdown_by") if planner_output else None

    # 1. Schema: columns, rows, attributes (MUST use metadata ONLY)
    for pat in SCHEMA_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            logger.info("router_decision: %s (pattern match: schema)", SCHEMA_QUERY)
            return SCHEMA_QUERY

    # 2. Explanation: why, explain, summarize, insights (ONLY these can use RAG)
    if intent in EXPLANATION_INTENTS:
        logger.info("router_decision: %s (intent: %s)", EXPLANATION_QUERY, intent)
        return EXPLANATION_QUERY
    if query and "why" in q:
        logger.info("router_decision: %s (query contains 'why')", EXPLANATION_QUERY)
        return EXPLANATION_QUERY

    # 3. Breakdown: "breakdown by X", "by X", "per X" (verify column exists)
    if breakdown_by or re.search(r"\bbreakdown\s+by\b|\bby\s+[A-Z][a-zA-Z]+\b|\bper\s+[A-Z][a-zA-Z]+\b", q, re.IGNORECASE):
        if intent == "expense_breakdown" or "breakdown" in q or "by" in q:
            logger.info("router_decision: %s (breakdown pattern detected)", BREAKDOWN_QUERY)
            return BREAKDOWN_QUERY

    # 4. Trend: "trend", "over time", "chart" with dates (require date + numeric columns)
    if intent == "trend" or re.search(r"\btrend\b|\bover\s+time\b", q, re.IGNORECASE):
        dates = planner_output.get("dates") or [] if planner_output else []
        date_filter = (planner_output.get("date_filter") or {}) if planner_output else {}
        if dates or date_filter:
            logger.info("router_decision: %s (trend with dates)", TREND_QUERY)
            return TREND_QUERY

    # 5. Vague: give chart, show data (no specific date/metric) - apply defaults WITHOUT clarification
    for pat in VAGUE_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            logger.info("router_decision: %s (pattern match: vague)", VAGUE_QUERY)
            return VAGUE_QUERY
    # Vague if no dates and generic intent
    dates = planner_output.get("dates") or [] if planner_output else []
    date_filter = (planner_output.get("date_filter") or {}) if planner_output else {}
    if not dates and not date_filter and intent in ("other", "single_value", ""):
        if any(w in q for w in ("chart", "data", "show", "give", "display")):
            logger.info("router_decision: %s (no date + generic)", VAGUE_QUERY)
            return VAGUE_QUERY

    # 6. Data: totals, filters, ranges (deterministic, NO RAG)
    logger.info("router_decision: %s", DATA_QUERY)
    return DATA_QUERY


def route_query(planner_output: Dict[str, Any], query: Optional[str] = None) -> str:
    """
    Route for data fetch: direct_db vs vector_search.
    Use vector search ONLY for explanation_query.
    """
    if not planner_output:
        return "direct_db"
    intent = (planner_output.get("intent") or "").strip().lower()
    if intent in VECTOR_INTENTS:
        return "vector_search"
    if query and isinstance(query, str) and "why" in query.lower():
        return "vector_search"
    return "direct_db"
