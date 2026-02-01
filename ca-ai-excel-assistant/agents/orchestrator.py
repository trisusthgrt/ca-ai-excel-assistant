"""
Orchestrator — fixed pipeline: User Query → normalize_query() → PlannerAgent (receives ONLY normalized query).
Single run per query; no loops; deterministic flow.
"""
import logging
from typing import Any, Dict, Optional

from .planner import plan
from .data_agent import fetch_data
from .analyst import analyze
from .responder import respond
from utils.policy_guard import check_policy
from utils.query_normalizer import normalize_query

logger = logging.getLogger(__name__)


def run(query: str) -> dict:
    """
    Run pipeline: User Query → normalize_query() → Planner (only normalized query) → Policy → Data → Analyst → Response.
    Returns {"answer": str, "chart_type": str|None, "chart_data": dict|None, "original_query": str, "normalized_query": str, "correction_map": dict}.
    """
    if not query or not str(query).strip():
        return {
            "answer": "Please ask a question (e.g. GST on 12 Jan 2025, or expenses for client ABC).",
            "chart_type": None,
            "chart_data": None,
            "original_query": "",
            "normalized_query": "",
            "correction_map": {},
        }

    original_query = str(query).strip()

    # Query normalization BEFORE Planner — Planner receives ONLY the normalized query
    norm_result = normalize_query(original_query)
    normalized_query = norm_result.get("normalized_query") or original_query
    correction_map = norm_result.get("correction_map") or {}

    # Log: original query, normalized query, corrections applied
    logger.info("original query: %s", original_query)
    logger.info("normalized query: %s", normalized_query)
    logger.info("corrections applied: %s", correction_map)

    planner_output = plan(normalized_query)
    policy_result = check_policy(normalized_query, planner_output)
    action = (policy_result.get("action") or "allow").strip().lower()
    policy_message = policy_result.get("message") or ""

    if action == "block":
        return {
            "answer": policy_message or "I can't assist with that request.",
            "chart_type": None,
            "chart_data": None,
            "original_query": original_query,
            "normalized_query": normalized_query,
            "correction_map": correction_map,
        }
    if action == "clarify":
        return {
            "answer": policy_message or "Please specify the date or client you're asking about.",
            "chart_type": None,
            "chart_data": None,
            "original_query": original_query,
            "normalized_query": normalized_query,
            "correction_map": correction_map,
        }

    # confidence < 0.7 means query is ambiguous — ask user to rephrase or specify
    confidence = float(planner_output.get("confidence", 1.0))
    if confidence < 0.7:
        return {
            "answer": (
                "Your question seems ambiguous. Please add a date or date range, "
                "client name, or rephrase (e.g. 'GST on 12 Jan 2025', 'expense trend for Jan 2025')."
            ),
            "chart_type": None,
            "chart_data": None,
            "original_query": original_query,
            "normalized_query": normalized_query,
            "correction_map": correction_map,
        }

    data = fetch_data(planner_output)
    intent = planner_output.get("intent") or "other"
    analyst_output = analyze(intent, data)
    answer = respond(
        planner_output,
        analyst_output,
        normalized_query,
        policy_action=action,
        policy_message=policy_message if action == "reframe" else None,
    )

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

    return {
        "answer": answer,
        "chart_type": chart_type,
        "chart_data": chart_data,
        "original_query": original_query,
        "normalized_query": normalized_query,
        "correction_map": correction_map,
    }
