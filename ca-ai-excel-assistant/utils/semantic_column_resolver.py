"""
Semantic Column Resolver — structured resolution for natural-language Excel queries.

GOAL: Allow users to query Excel data using natural language WITHOUT knowing exact
column names. Resolves user terms to actual columns deterministically, safely,
without hallucination. Does NOT rewrite user queries; returns a STRUCTURED OBJECT
consumed by the PlannerAgent and downstream agents.

CORE RULES:
- Work ONLY on the LATEST uploaded file schema.
- NEVER guess when ambiguity exists.
- Return resolved columns, group_by, filters; unresolved/ambiguous listed separately.
- Ask clarification ONLY when unresolved_concepts or ambiguous_concepts non-empty.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Minimum similarity (0-100) to accept a column match. No guess below this.
SIMILARITY_THRESHOLD = 85

# ---------------------------------------------------------------------------
# CANONICAL CONCEPT MODEL (financial/Excel concepts)
# Each concept maps to at most ONE column, or is unresolved/ambiguous.
# ---------------------------------------------------------------------------
CANONICAL_CONCEPTS = frozenset({
    "date",
    "gst_amount",
    "net_amount",
    "total_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "discount",
    "customer",
    "branch",
    "region",
    "category",
    "subcategory",
    "payment_method",
    "sales_person",
    "state",
    "country",
})

# canonical_concept -> list of variants (user terms / column-name hints)
CANONICAL_VARIANT_MAP: Dict[str, List[str]] = {
    "date": [
        "date",
        "day",
        "transaction date",
        "bill date",
        "invoice date",
        "rowdate",
        "transactiondate",
        "billdate",
        "invoicedate",
        "dt",
    ],
    "gst_amount": [
        "gst",
        "tax",
        "gst amount",
        "gstamount",
        "tax amount",
        "taxamount",
        "gst_amt",
    ],
    "net_amount": [
        "net",
        "net value",
        "net amount",
        "netvalue",
        "netamount",
    ],
    "total_amount": [
        "total",
        "total value",
        "gross",
        "gross amount",
        "totalamount",
        "totalvalue",
        "grossamount",
    ],
    "cgst_amount": [
        "cgst",
        "cgst amount",
        "cgstamount",
    ],
    "sgst_amount": [
        "sgst",
        "sgst amount",
        "sgstamount",
    ],
    "igst_amount": [
        "igst",
        "igst amount",
        "igstamount",
    ],
    "discount": [
        "discount",
        "discount amount",
        "discountamount",
        "disc",
    ],
    "customer": [
        "customer",
        "client",
        "party",
        "agency",
        "agent",
        "vendor",
        "supplier",
        "buyer",
        "dealer",
        "distributor",
        "customer name",
        "client name",
        "party name",
        "agency name",
        "agent name",
        "customername",
        "clientname",
        "partyname",
        "agencyname",
        "vendorname",
        "suppliername",
    ],
    "branch": [
        "branch",
        "office",
        "location",
        "office location",
        "branch name",
        "branchname",
        "officelocation",
        "outlet",
        "store",
    ],
    "region": [
        "region",
        "region name",
        "regionname",
        "zone",
        "area",
        "territory",
    ],
    "category": [
        "category",
        "transaction type",
        "type",
        "transactiontype",
        "category name",
        "categoryname",
    ],
    "subcategory": [
        "subcategory",
        "sub category",
        "sub type",
        "subtype",
        "transaction subtype",
        "transactionsubtype",
        "subcategory name",
    ],
    "payment_method": [
        "payment",
        "payment mode",
        "payment method",
        "paymentmethod",
        "paymentmode",
    ],
    "sales_person": [
        "sales person",
        "salesperson",
        "executive",
        "sales executive",
        "salesexecutive",
        "sales executive name",
    ],
    "state": [
        "state",
        "statename",
    ],
    "country": [
        "country",
        "countryname",
    ],
}


def _normalize_for_match(s: str) -> str:
    """Lowercase, remove spaces/underscores/punctuation for matching."""
    if not isinstance(s, str):
        s = str(s or "")
    s = s.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = s.replace(" ", "").replace("_", "")
    return s


# ---------------------------------------------------------------------------
# STAGE 1 — Term → Canonical Concept (partial matches supported)
# Map user words to canonical concepts; e.g. customer/client/party → customer,
# subtype → subcategory, date/day → date, tax → gst_amount.
# ---------------------------------------------------------------------------
def _stage1_terms_to_concepts(query: str) -> List[str]:
    """
    Stage 1: Detect which canonical concepts are mentioned in the query.
    Supports partial matches (e.g. "day" in "today", "tax" in "taxable").
    Returns list of concept names ordered by first occurrence.
    """
    if not query or not str(query).strip():
        return []
    q_norm = _normalize_for_match(query)
    found: List[Tuple[int, str]] = []  # (position, concept)
    for concept, variants in CANONICAL_VARIANT_MAP.items():
        for v in variants:
            v_norm = _normalize_for_match(v)
            if v_norm and len(v_norm) >= 2 and v_norm in q_norm:
                pos = q_norm.index(v_norm)
                found.append((pos, concept))
                break
    seen = set()
    out = []
    for _, c in sorted(found, key=lambda x: x[0]):
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# STAGE 2 — Canonical Concept → Column (fuzzy >= 85%, exactly one column)
# ---------------------------------------------------------------------------
def _stage2_concept_to_column(
    concept: str,
    normalized_columns: List[Tuple[str, str]],
) -> Tuple[Optional[str], Optional[List[str]], str]:
    """
    Find best column match for a concept using fuzzy similarity.
    Returns (resolved_column, ambiguous_candidates, status).
    status: "resolved" | "ambiguous" | "unresolved"
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        # Fallback: exact substring match only (no fuzzy)
        fuzz = None

    variants = CANONICAL_VARIANT_MAP.get(concept, [])
    if not variants or not normalized_columns:
        return None, None, "unresolved"

    # Build variant norms for this concept
    variant_norms = [_normalize_for_match(v) for v in variants if _normalize_for_match(v)]

    # Score each column: best similarity of any variant vs normalized column name
    scores: List[Tuple[str, float]] = []
    for col_original, col_norm in normalized_columns:
        best = 0.0
        for v_norm in variant_norms:
            if fuzz is not None:
                # ratio: 0-100
                score = fuzz.ratio(v_norm, col_norm)
            else:
                # No fuzzy: accept only exact match (do not guess on substring)
                score = 100.0 if v_norm == col_norm else 0.0
            if score > best:
                best = score
        if best >= SIMILARITY_THRESHOLD:
            scores.append((col_original, best))

    if not scores:
        return None, None, "unresolved"

    # Sort by score descending; take best
    scores.sort(key=lambda x: -x[1])
    best_score = scores[0][1]
    ties = [col for col, sc in scores if sc == best_score]

    if len(ties) == 1:
        return ties[0], None, "resolved"
    # Multiple columns at same best score -> ambiguous
    return None, ties, "ambiguous"


def resolve_semantic_columns(
    query: str,
    schema: Optional[Dict[str, Any]],
    file_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve user query to structured column mapping for the LATEST file schema.

    DOES NOT rewrite the user query. Returns a structured object.

    Inputs:
        query: normalized user query text.
        schema: latest file schema (column_names, normalized_column_names, etc.).
        file_id: optional latest file_id for logging.

    Output:
        {
          "resolved": { "date": "TransactionDate", "gst_amount": "GSTAmount", ... },
          "group_by": ["CustomerName"],
          "filters": {},
          "unresolved_concepts": [],
          "ambiguous_concepts": [],
          "ambiguous_details": { "amount": ["col1", "col2"] }
        }

    Clarification is needed only when unresolved_concepts or ambiguous_concepts non-empty.
    """
    empty_result = {
        "resolved": {},
        "group_by": [],
        "filters": {},
        "unresolved_concepts": [],
        "ambiguous_concepts": [],
        "ambiguous_details": {},
    }

    if not query or not str(query).strip():
        return empty_result

    if not schema:
        logger.warning("semantic_resolver: no schema provided")
        return empty_result

    # Column names from schema (normalized for matching)
    column_names = schema.get("column_names") or schema.get("normalized_column_names") or []
    if not column_names:
        logger.warning("semantic_resolver: no column_names in schema")
        return empty_result

    normalized_columns = [(c, _normalize_for_match(c)) for c in column_names]

    # STAGE 1 — Term → Canonical Concept
    detected = _stage1_terms_to_concepts(query)
    logger.info(
        "semantic_resolver: file_id=%s query=%s detected_concepts=%s",
        file_id or "N/A",
        query[:80],
        detected,
    )

    resolved: Dict[str, str] = {}
    unresolved_concepts: List[str] = []
    ambiguous_concepts: List[str] = []
    ambiguous_details: Dict[str, List[str]] = {}

    # STAGE 2 — Canonical Concept → Column (fuzzy >= 85%, exactly one)
    for concept in detected:
        col, candidates, status = _stage2_concept_to_column(concept, normalized_columns)
        if status == "resolved" and col:
            resolved[concept] = col
        elif status == "ambiguous" and candidates:
            ambiguous_concepts.append(concept)
            ambiguous_details[concept] = list(candidates)
        else:
            unresolved_concepts.append(concept)

    # STAGE 3 — Structured output: group_by vs filter logic
    # "by X" / "per X" / "breakdown by X" → group_by. "on X" (e.g. on 12 Jan, for client Y) → filter (handled by planner).
    # Date columns are VALID group_by keys; never block grouping by date.
    group_by: List[str] = []
    q_lower = query.strip().lower()
    for concept in ("date", "customer", "branch", "region", "category", "subcategory", "state", "country", "payment_method", "sales_person"):
        if concept not in CANONICAL_VARIANT_MAP:
            continue
        for v in CANONICAL_VARIANT_MAP[concept]:
            v_lower = v.lower().strip()
            # Match "by X", "per X", "breakdown by X" on query WITH spaces so "breakdown by customer" matches
            v_esc = re.escape(v_lower)
            if re.search(r"\b(?:by|per|breakdown\s+by)\s+" + v_esc + r"\b", q_lower) or \
               re.search(r"\b" + v_esc + r"\s*(?:wise|by)\b", q_lower) or \
               (concept == "date" and re.search(r"by\s+date|group\s+by\s+date", q_lower)):
                if concept in resolved and resolved[concept] not in group_by:
                    group_by.append(resolved[concept])
                break

    # STAGE 3 — Structured output (NO TEXT REWRITE). PlannerAgent consumes this ONLY.
    result = {
        "resolved_columns": resolved,
        "resolved": resolved,  # backward compat for get_amount_column_for_metric etc.
        "group_by": group_by,
        "filters": {},
        "unresolved_concepts": unresolved_concepts,
        "ambiguous_concepts": ambiguous_concepts,
        "ambiguous_details": ambiguous_details,
    }

    logger.info(
        "semantic_resolver: file_id=%s resolved=%s group_by=%s unresolved=%s ambiguous=%s",
        file_id or "N/A",
        result["resolved"],
        result["group_by"],
        result["unresolved_concepts"],
        result["ambiguous_concepts"],
    )

    return result


def build_clarification_message(
    resolution: Dict[str, Any],
    schema: Optional[Dict[str, Any]],
) -> str:
    """
    Build a single clarification message when unresolved or ambiguous.
    List available columns. Ask ONCE per query.
    """
    unresolved = resolution.get("unresolved_concepts") or []
    ambiguous = resolution.get("ambiguous_concepts") or []
    ambiguous_details = resolution.get("ambiguous_details") or {}

    if not unresolved and not ambiguous:
        return ""

    # Schema authority: use original_column_names (exact Excel headers) in user-facing message
    orig = (schema or {}).get("original_column_names") or (schema or {}).get("column_names") or (schema or {}).get("normalized_column_names") or []
    cols_str = ", ".join(str(c) for c in orig) if orig else "no columns"

    parts = [
        "I couldn't uniquely match some terms to columns in the latest uploaded file."
    ]
    if unresolved:
        parts.append(" Unclear which column to use for: " + ", ".join(f"**{c}**" for c in unresolved) + ".")
    if ambiguous:
        for c in ambiguous:
            cands = ambiguous_details.get(c, [])
            if cands:
                parts.append(f" For **{c}**, multiple columns match: {', '.join(cands)}. Please specify one.")
    parts.append(f" Available columns: {cols_str}.")
    return " ".join(parts)


def get_amount_column_for_metric(resolution: Dict[str, Any], metric_hint: Optional[str] = None) -> Optional[str]:
    """
    Return the single best amount column from resolution for the given metric hint.
    PlannerAgent / Analyst use this; no guessing. NEVER substitute (e.g. GST for NetValue).
    Priority by hint: gst/tax → gst_amount, discount → discount, net → net_amount, total/gross → total_amount.
    """
    resolved = resolution.get("resolved_columns") or resolution.get("resolved") or {}
    if metric_hint:
        h = (metric_hint or "").strip().lower()
        if "gst" in h or "tax" in h:
            return resolved.get("gst_amount") or resolved.get("cgst_amount") or resolved.get("sgst_amount") or resolved.get("igst_amount")
        if "discount" in h:
            return resolved.get("discount")
        if "net" in h:
            return resolved.get("net_amount")
        if "total" in h or "gross" in h:
            return resolved.get("total_amount")
    return (
        resolved.get("gst_amount")
        or resolved.get("net_amount")
        or resolved.get("total_amount")
        or resolved.get("discount")
        or resolved.get("cgst_amount")
        or resolved.get("sgst_amount")
        or resolved.get("igst_amount")
    )


def get_date_column(resolution: Dict[str, Any]) -> Optional[str]:
    """Return the resolved date column, or None."""
    r = resolution.get("resolved_columns") or resolution.get("resolved") or {}
    return r.get("date")


def get_breakdown_column_for_term(term: str, resolution: Dict[str, Any]) -> Optional[str]:
    """
    Map a user/planner term (e.g. 'agency', 'customer') to the resolved column when that term
    is a variant of a resolved concept. So 'agency' -> customer -> CustomerName.
    """
    if not term or not str(term).strip():
        return None
    resolved = resolution.get("resolved_columns") or resolution.get("resolved") or {}
    term_norm = _normalize_for_match(term)
    if not term_norm:
        return None
    for concept, col in resolved.items():
        variants = CANONICAL_VARIANT_MAP.get(concept) or []
        for v in variants:
            if _normalize_for_match(v) == term_norm or term_norm in _normalize_for_match(v) or _normalize_for_match(v) in term_norm:
                return col
    return None


def needs_clarification(resolution: Dict[str, Any]) -> bool:
    """True if we must ask the user for clarification (unresolved or ambiguous)."""
    u = resolution.get("unresolved_concepts") or []
    a = resolution.get("ambiguous_concepts") or []
    return len(u) > 0 or len(a) > 0
