"""
Query normalization layer BEFORE PlannerAgent.
Uses rapidfuzz for fuzzy matching on column names, finance keywords, client names, month abbreviations.
Only corrects important tokens; similarity >= 85%; returns normalized_query and correction_map.
"""
from typing import Dict, List, Set, Tuple

# Canonical column names (lowercase) — used for fuzzy match and replacement
COLUMN_NAMES: Set[str] = {
    "gst", "amount", "expense", "tds", "total_tax", "total", "date", "rowdate",
    "category", "description", "revenue", "balance", "tax", "value", "sum",
}

# Finance keywords (lowercase)
FINANCE_KEYWORDS: Set[str] = {
    "gst", "revenue", "expense", "tax", "amount", "total", "balance",
    "tds", "refund", "deduction", "income", "payment", "receipt",
}

# Month abbreviations: lowercase key -> display form (Jan, Feb, ...)
MONTH_ABBREVS: Dict[str, str] = {
    "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "may": "May", "jun": "Jun",
    "jul": "Jul", "aug": "Aug", "sep": "Sep", "oct": "Oct", "nov": "Nov", "dec": "Dec",
}
MONTH_NAMES: Dict[str, str] = {
    "january": "January", "february": "February", "march": "March", "april": "April",
    "june": "June", "july": "July", "august": "August", "september": "September",
    "october": "October", "november": "November", "december": "December",
}

SIMILARITY_THRESHOLD = 85


def _get_client_names() -> Set[str]:
    """Load distinct client names (clientTag) from MongoDB. Returns empty set if not connected."""
    try:
        from db import mongo
        tags = mongo.get_distinct_client_tags()
        return {t for t in tags if t}
    except Exception:
        return set()


def _tokenize(query: str) -> List[str]:
    """Split query on whitespace; preserve order and original token strings."""
    if not query or not isinstance(query, str):
        return []
    return query.split()


def _is_number_or_date_part(s: str) -> bool:
    """True if token looks like a number or numeric date part (e.g. 12, 2025)."""
    s = s.strip()
    if not s:
        return True
    return s.isdigit()


def normalize_query(user_query: str) -> dict:
    """
    Normalize user query before PlannerAgent using fuzzy matching on important tokens only.
    - column names, finance keywords, client names (from MongoDB), month abbreviations
    - Replace a token ONLY if rapidfuzz similarity >= 85%.
    - Do NOT rewrite the entire sentence.
    Returns: {"normalized_query": str, "correction_map": dict (original -> corrected)}
    """
    if not user_query or not str(user_query).strip():
        return {"normalized_query": "", "correction_map": {}}

    query = str(user_query).strip()
    tokens = _tokenize(query)
    if not tokens:
        return {"normalized_query": query, "correction_map": {}}

    try:
        from rapidfuzz import fuzz
        from rapidfuzz.process import extractOne
    except ImportError:
        return {"normalized_query": query, "correction_map": {}}

    # Build choices for important tokens: column names, finance keywords, months, client names
    column_and_finance = COLUMN_NAMES | FINANCE_KEYWORDS
    month_choices = list(MONTH_ABBREVS.keys()) + list(MONTH_NAMES.keys())
    client_names = _get_client_names()
    # Canonical forms: for column/finance we use lowercase; for months we use MONTH_ABBREVS/MONTH_NAMES; for clients we use DB value
    def canonical_month(key: str) -> str:
        return MONTH_ABBREVS.get(key.lower(), MONTH_NAMES.get(key.lower(), key))

    correction_map: Dict[str, str] = {}
    normalized_tokens: List[str] = []

    for token in tokens:
        if _is_number_or_date_part(token):
            normalized_tokens.append(token)
            continue

        token_lower = token.lower()
        best_match: str | None = None
        best_score = 0

        # 1. Client names (preserve case from DB)
        if client_names:
            client_lower_to_original = {c.lower(): c for c in client_names}
            result = extractOne(token_lower, list(client_lower_to_original.keys()), scorer=fuzz.ratio)
            if result and result[1] >= SIMILARITY_THRESHOLD:
                best_match = client_lower_to_original[result[0]]
                best_score = result[1]

        # 2. Month abbreviations/names
        if best_score < SIMILARITY_THRESHOLD:
            result = extractOne(token_lower, month_choices, scorer=fuzz.ratio)
            if result and result[1] >= SIMILARITY_THRESHOLD and result[1] > best_score:
                best_match = canonical_month(result[0])
                best_score = result[1]

        # 3. Column names / finance keywords (replace with lowercase canonical)
        if best_score < SIMILARITY_THRESHOLD:
            result = extractOne(token_lower, list(column_and_finance), scorer=fuzz.ratio)
            if result and result[1] >= SIMILARITY_THRESHOLD and result[1] > best_score:
                best_match = result[0]
                best_score = result[1]

        if best_match is not None and best_score >= SIMILARITY_THRESHOLD:
            correction_map[token] = best_match
            normalized_tokens.append(best_match)
        else:
            normalized_tokens.append(token)

    # Post-pass: merge consecutive tokens that fuzzy-match a multi-word client name (e.g. "abc pvt ltd" -> "ABC Pvt Ltd")
    client_names = _get_client_names()
    if client_names and len(normalized_tokens) >= 2:
        client_lower_to_original = {c.lower(): c for c in client_names}
        max_phrase_len = min(5, len(normalized_tokens))
        i = 0
        while i < len(normalized_tokens):
            merged = False
            for length in range(max_phrase_len, 1, -1):
                if i + length > len(normalized_tokens):
                    continue
                phrase = " ".join(normalized_tokens[i : i + length])
                result = extractOne(phrase.lower(), list(client_lower_to_original.keys()), scorer=fuzz.ratio)
                if result and result[1] >= SIMILARITY_THRESHOLD:
                    canonical = client_lower_to_original[result[0]]
                    normalized_tokens = normalized_tokens[:i] + [canonical] + normalized_tokens[i + length :]
                    correction_map[phrase] = canonical
                    merged = True
                    i += 1
                    break
            if not merged:
                i += 1

    normalized_query = " ".join(normalized_tokens)
    return {"normalized_query": normalized_query, "correction_map": correction_map}
