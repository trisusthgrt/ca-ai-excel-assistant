"""
Policy guard — block evasion, reframe ambiguous tax questions, ask clarification (Step 7).
Returns action: allow | block | reframe | clarify, and message when needed.
"""
import re
from typing import Dict

# Phrases that indicate illegal evasion → block
BLOCK_PATTERNS = [
    r"\bevade\b",
    r"\bevasion\b",
    r"\bhide\s+income\b",
    r"\bundeclared\b",
    r"\bblack\s+money\b",
    r"\bunderreport\b",
    r"\bconceal\s+(income|tax)\b",
    r"\bhow\s+to\s+(evade|avoid)\s+tax\b",
    r"\bavoid\s+paying\s+tax\b",
    r"\bescape\s+tax\b",
    r"\bnot\s+pay(ing)?\s+tax\b",
    r"\bskip\s+tax\b",
]

# Ambiguous but legal → reframe (tax planning, deductions)
REFRAME_PATTERNS = [
    r"\breduce\s+tax\b",
    r"\bpay\s+less\s+tax\b",
    r"\bgive\s+less\s+tax\b",
    r"\blower\s+tax\b",
    r"\bminimiz(e|ing)\s+tax\b",
    r"\bminimis(e|ing)\s+tax\b",
    r"\bless\s+tax\b",
    r"\bhow\s+to\s+reduce\s+tax\b",
]

BLOCK_MESSAGE = (
    "I can't assist with that. For tax and compliance, please consult your "
    "Chartered Accountant or official guidelines."
)

REFRAME_MESSAGE = (
    "I'll interpret this as legal tax planning (deductions, compliance, and "
    "proper reporting). Here's what the data shows:"
)

CLARIFY_DATE_MESSAGE = (
    "Please specify the date or date range (e.g. 'GST on 12 Jan 2025' or "
    "'expenses for January 2025')."
)

CLARIFY_CLIENT_MESSAGE = (
    "Please specify which client (e.g. 'expenses for client ABC on 10 Jan')."
)


def _matches(query: str, patterns: list) -> bool:
    q = query.lower().strip()
    for pat in patterns:
        if re.search(pat, q, re.IGNORECASE):
            return True
    return False


def check_policy(query: str, planner_output: dict) -> dict:
    """
    Returns {"action": "allow" | "block" | "reframe" | "clarify", "message": str}.
    - block: illegal evasion; return safe message.
    - clarify: missing date or client when intent needs it.
    - reframe: ambiguous legal phrasing; message suggests legal framing; pipeline still runs.
    - allow: otherwise.
    """
    if not query or not str(query).strip():
        return {"action": "allow", "message": ""}

    q = query.strip()
    intent = (planner_output.get("intent") or "other").strip().lower()
    dates = planner_output.get("dates") or []
    client_tag = planner_output.get("client_tag")
    risk_flag = planner_output.get("risk_flag", False)

    # 1. Block: planner risk flag or query contains evasion phrases
    if risk_flag:
        return {"action": "block", "message": BLOCK_MESSAGE}
    if _matches(q, BLOCK_PATTERNS):
        return {"action": "block", "message": BLOCK_MESSAGE}

    # 2. Clarify: intent needs date but none provided
    date_dependent_intents = {"gst_summary", "trend", "compare_dates"}
    if intent in date_dependent_intents and not dates:
        return {"action": "clarify", "message": CLARIFY_DATE_MESSAGE}

    # 3. Clarify: query mentions "client" but no client_tag extracted
    if re.search(r"\bclient\b", q, re.IGNORECASE) and not client_tag:
        # Only clarify if it looks like they're asking for a specific client
        if re.search(r"(for|of|client)\s+\w+", q, re.IGNORECASE):
            return {"action": "clarify", "message": CLARIFY_CLIENT_MESSAGE}

    # 4. Reframe: ambiguous legal phrasing (we still allow; message for responder context)
    if _matches(q, REFRAME_PATTERNS):
        return {"action": "reframe", "message": REFRAME_MESSAGE}

    return {"action": "allow", "message": ""}
