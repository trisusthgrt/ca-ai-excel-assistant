"""
Response agent — generates final answer and applies safety policy (Step 6).
Uses Groq for elaborate natural-language response when allowed.
"""
import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SAFE_BLOCK_MESSAGE = "I can't assist with that. For tax and compliance matters, please rely on your Chartered Accountant or official guidelines."


def _context_string(planner_output: dict) -> str:
    """Build context (upload date vs data date, client, metric) for the answer."""
    parts = []
    date_filter = planner_output.get("date_filter") or {}
    date_filter_type = (planner_output.get("date_filter_type") or "row_date").strip().lower()
    if date_filter.get("single"):
        if date_filter_type == "upload_date":
            parts.append(f"upload date: {date_filter['single']}")
        else:
            parts.append(f"data date: {date_filter['single']}")
    elif date_filter.get("from") and date_filter.get("to"):
        if date_filter_type == "upload_date":
            parts.append(f"upload date range: {date_filter['from']} to {date_filter['to']}")
        else:
            parts.append(f"data date range: {date_filter['from']} to {date_filter['to']}")
    client = planner_output.get("client_tag") or planner_output.get("client")
    if client:
        parts.append(f"client: {client}")
    metric = planner_output.get("metric")
    if metric:
        parts.append(f"metric: {metric}")
    return "; ".join(parts) if parts else ""


def _format_fallback_answer(planner_output: dict, analyst_output: dict, is_summary: bool = False) -> str:
    """Build an elaborate fallback answer from analyst output and planner context.
    When is_summary is True, include all attributes (column names), date range, and full breakdown/series (no truncation).
    """
    total = analyst_output.get("total", 0)
    count = analyst_output.get("count", 0)
    amount_key = analyst_output.get("amount_key") or "amount"
    context = _context_string(planner_output)
    parts = []
    if context:
        parts.append(f"**Context:** {context}.")
    # Column names / attributes (for summary: full list)
    column_names = analyst_output.get("column_names") or []
    if column_names:
        parts.append(f"**Attributes (columns):** {', '.join(str(c) for c in column_names)}.")
    # Date range when present
    date_range = analyst_output.get("date_range") or {}
    if date_range.get("min") and date_range.get("max"):
        parts.append(f"**Date range in data:** {date_range['min']} to {date_range['max']}.")
    parts.append(f"**Total {amount_key.replace('_', ' ').title()}:** {total:,.2f} (from {count} row(s)).")
    # Breakdown: full list for summary, else cap at 15
    breakdown_limit = 999 if is_summary else 15
    if "breakdown" in analyst_output and analyst_output["breakdown"]:
        parts.append("**Breakdown by category:**")
        for b in analyst_output["breakdown"][:breakdown_limit]:
            parts.append(f"  • {b.get('category', '?')}: {b.get('amount', 0):,.2f}")
        if not is_summary and len(analyst_output["breakdown"]) > 15:
            parts.append(f"  … and {len(analyst_output['breakdown']) - 15} more.")
    # Series: full list for summary, else cap at 10
    series_limit = 999 if is_summary else 10
    if "series" in analyst_output and analyst_output["series"]:
        parts.append("**Trend (by date):**")
        for s in analyst_output["series"][:series_limit]:
            parts.append(f"  • {s.get('date', '?')}: {s.get('value', 0):,.2f}")
        if not is_summary and len(analyst_output["series"]) > 10:
            parts.append(f"  … and {len(analyst_output['series']) - 10} more dates.")
    # Compare: full list for summary, else cap at 10
    compare_limit = 999 if is_summary else 10
    if "compare" in analyst_output and analyst_output["compare"]:
        parts.append("**Comparison by date:**")
        for c in analyst_output["compare"][:compare_limit]:
            parts.append(f"  • {c.get('date', '?')}: {c.get('total', 0):,.2f}")
        if not is_summary and len(analyst_output["compare"]) > 10:
            parts.append(f"  … and {len(analyst_output['compare']) - 10} more.")
    return "\n\n".join(parts)


def respond(
    planner_output: dict,
    analyst_output: dict,
    question: str,
    policy_action: Optional[str] = None,
    policy_message: Optional[str] = None,
) -> str:
    """
    Generate natural-language answer. If policy says block or planner risk_flag, return safe message.
    Otherwise format analyst result into an elaborate response (with Groq if available).
    """
    risk_flag = planner_output.get("risk_flag", False)
    if risk_flag or (policy_action == "block"):
        return policy_message or SAFE_BLOCK_MESSAGE
    if policy_action == "clarify":
        return policy_message or "Please specify the date or client you're asking about."
    reframe_prefix = (policy_message + " ") if (policy_action == "reframe" and policy_message) else ""

    # No data
    if not analyst_output or analyst_output.get("count", 0) == 0:
        msg = analyst_output.get("message", "No data found for the selected filters. Upload Excel and try again.") if analyst_output else "No data found for the selected filters. Upload Excel and try again."
        return reframe_prefix + msg

    context = _context_string(planner_output)
    intent = (planner_output.get("intent") or "").strip().lower()
    is_summary = intent == "summarize"
    # For summary, pass full data (increase cap and tokens so LLM can list attributes and key figures)
    summary = json.dumps(analyst_output, default=str, indent=0)[:6000 if is_summary else 3000]

    if not GROQ_API_KEY:
        return reframe_prefix + _format_fallback_answer(planner_output, analyst_output, is_summary=is_summary)

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        system = """You are an assistant for a Chartered Accountant firm. You answer based ONLY on the provided data summary (JSON) and context.
Give an elaborate, clear answer in plain language:
1. Start with the context (date range, client, metric) when relevant.
2. If the user asked for a summary or "all details", list every attribute (column name) from the data, then state the date range in the data, then the main total and row count.
3. State the main total or outcome with the exact numbers from the data.
4. If there is a breakdown, series, or comparison, include key points (for summary requests, mention all categories and dates; otherwise top categories and trend).
5. End with a brief interpretation (e.g. "Total GST for this period is X" or "Expenses are highest in category Y").
Use proper number formatting (e.g. 1,234.56). For summary requests write a comprehensive answer covering every attribute and key figure; otherwise 4-8 sentences. No legal or tax advice. No markdown formatting."""
        user = f"Context: {context or 'none'}\n\nQuestion: {question}\n\nData summary:\n{summary}\n\nAnswer:"
        max_tokens = 1024 if is_summary else 512
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        content = (response.choices[0].message.content or "").strip()
        return reframe_prefix + (content if content else _format_fallback_answer(planner_output, analyst_output, is_summary=is_summary))
    except Exception:
        return reframe_prefix + _format_fallback_answer(planner_output, analyst_output, is_summary=is_summary)
