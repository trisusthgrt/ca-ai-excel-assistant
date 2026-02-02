"""
Planner agent — structured extraction from user query (Step 6).
Returns: intent, confidence, date_filter, client, metric, needs_chart, chart_type, x_axis, y_axis.
Uses Groq LLM when available; fallback heuristics otherwise.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Intents that need a chart (trends, comparisons, distributions)
CHART_INTENTS = {"trend", "compare_dates", "expense_breakdown", "distribution"}

# Single-value intents: no chart
SINGLE_VALUE_INTENTS = {"gst_summary", "single_value", "other"}


def _parse_dates_from_llm(dates_raw: Any) -> List[str]:
    """Normalize dates to ISO YYYY-MM-DD list."""
    if dates_raw is None:
        return []
    if isinstance(dates_raw, str):
        dates_raw = [dates_raw]
    if not isinstance(dates_raw, list):
        return []
    out = []
    for d in dates_raw:
        if not d:
            continue
        s = str(d).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            out.append(s)
            continue
        try:
            from datetime import datetime
            for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(s, fmt)
                    out.append(dt.strftime("%Y-%m-%d"))
                    break
                except ValueError:
                    continue
        except Exception:
            pass
    return out


def _dates_to_date_filter(dates: List[str]) -> Dict[str, Any]:
    """Convert list of dates to date_filter: single or from/to."""
    if not dates:
        return {}
    if len(dates) == 1:
        return {"single": dates[0]}
    sorted_dates = sorted(dates)
    return {"from": sorted_dates[0], "to": sorted_dates[-1]}


def _default_structured() -> dict:
    """Default structured output when query is empty or parsing fails."""
    return {
        "intent": "other",
        "confidence": 0.0,
        "date_filter": {},
        "date_filter_type": "row_date",
        "client": None,
        "metric": None,
        "needs_chart": False,
        "chart_type": None,
        "x_axis": None,
        "y_axis": None,
        # Legacy (for data_agent, policy_guard, orchestrator)
        "dates": [],
        "client_tag": None,
        "risk_flag": False,
        "chart_scope": None,
    }


def plan(query: str) -> dict:
    """
    Extract structured output from user query.
    Returns:
      intent: string
      confidence: float (0–1)
      date_filter: { single?: string, from?: string, to?: string }
      client: string | null
      metric: string | null (e.g. gst, amount, expense)
      needs_chart: bool — true only for trends, comparisons, distributions
      chart_type: "line" | "bar" | "pie" | "stacked_bar" | null
      x_axis: string | null (e.g. date, category)
      y_axis: string | null (e.g. amount, value)
      + legacy: dates, client_tag, risk_flag, chart_scope
    """
    if not query or not str(query).strip():
        out = _default_structured()
        out["intent"] = "unknown"
        return out

    q = str(query).strip().lower()

    if not GROQ_API_KEY:
        return _plan_fallback(q, query)

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        system = """You are a query analyzer for a CA (Chartered Accountant) Excel assistant.
Extract from the user message and reply with ONLY a valid JSON object (no markdown, no explanation).

Required keys:
- intent: one of gst_summary, expense_breakdown, trend, compare_dates, distribution, single_value, explain, summarize, insights, why, other
- confidence: number 0 to 1 (1 = clear intent, <0.7 = ambiguous)
- date_filter: object. Use {"single": "YYYY-MM-DD"} for one date, {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"} for range, {} if none
- date_filter_type: "upload_date" OR "row_date". Use "upload_date" ONLY when the user explicitly asks for data BY UPLOAD DATE (e.g. "data uploaded on 2 Feb", "upload date 2 Feb 2025", "file uploaded on X", "show data for upload date X"). Use "row_date" for dates IN THE DATA (e.g. "GST on 12 Jan", "data on 12 Jan", "transactions on X", "trend for January"). Default "row_date".
- client: client name if mentioned, else null
- metric: primary metric (e.g. gst, amount, expense, revenue) or null
- needs_chart: true ONLY for trends, comparisons, or distributions; false for single-value queries
- chart_type: "line" (trend), "bar" (breakdown/compare), "pie" (share), "stacked_bar" (composition), or null
- x_axis: e.g. "date", "category", "client" or null
- y_axis: e.g. "amount", "value", "total" or null
- risk_flag: true only if query asks for illegal tax evasion or hiding income; else false

Rules: Single-value queries (e.g. "GST on 12 Jan") must have needs_chart=false. confidence < 0.7 means ambiguous. date_filter_type = upload_date only when user says upload/uploaded; else row_date."""
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ],
            temperature=0,
            max_tokens=512,
        )
        content = (response.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        data = json.loads(content)

        intent = str(data.get("intent", "other")).strip().lower() or "other"
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        date_filter_raw = data.get("date_filter")
        if isinstance(date_filter_raw, dict):
            date_filter = {k: str(v).strip() for k, v in date_filter_raw.items() if v}
        else:
            dates = _parse_dates_from_llm(data.get("dates"))
            date_filter = _dates_to_date_filter(dates)

        client = data.get("client")
        if client is not None:
            client = str(client).strip() or None

        metric = data.get("metric")
        if metric is not None:
            metric = str(metric).strip() or None

        needs_chart = bool(data.get("needs_chart", False))
        # Enforce: needs_chart true only for trends, comparisons, distributions
        if intent in SINGLE_VALUE_INTENTS or intent in ("explain", "summarize", "insights", "why"):
            needs_chart = False
        elif intent in CHART_INTENTS:
            needs_chart = True

        chart_type_raw = data.get("chart_type")
        if chart_type_raw is not None:
            chart_type = str(chart_type_raw).strip().lower()
            if chart_type not in ("line", "bar", "pie", "stacked_bar"):
                chart_type = None
        else:
            chart_type = None
        if needs_chart and not chart_type:
            chart_type = "line" if intent == "trend" else "bar"

        x_axis = data.get("x_axis")
        if x_axis is not None:
            x_axis = str(x_axis).strip() or None
        y_axis = data.get("y_axis")
        if y_axis is not None:
            y_axis = str(y_axis).strip() or None

        risk_flag = bool(data.get("risk_flag", False))

        # Derive dates list from date_filter for legacy consumers
        if date_filter.get("single"):
            dates = [date_filter["single"]]
        elif date_filter.get("from") and date_filter.get("to"):
            dates = [date_filter["from"], date_filter["to"]]
        else:
            dates = _parse_dates_from_llm(data.get("dates", []))

        # date_filter_type: filter by upload date (when file was uploaded) vs row date (date in dataset)
        date_filter_type = (data.get("date_filter_type") or "row_date").strip().lower()
        if date_filter_type not in ("upload_date", "row_date"):
            date_filter_type = "row_date"

        chart_scope = data.get("chart_scope")
        if chart_scope is not None:
            chart_scope = str(chart_scope).strip() or None
        if not chart_scope and intent == "trend":
            chart_scope = "trend over time"
        elif not chart_scope and intent == "expense_breakdown":
            chart_scope = "breakdown by category"
        elif not chart_scope and intent == "compare_dates":
            chart_scope = "compare dates"

        return {
            "intent": intent,
            "confidence": confidence,
            "date_filter": date_filter,
            "date_filter_type": date_filter_type,
            "client": client,
            "metric": metric,
            "needs_chart": needs_chart,
            "chart_type": chart_type,
            "x_axis": x_axis,
            "y_axis": y_axis,
            "dates": dates,
            "client_tag": client,
            "risk_flag": risk_flag,
            "chart_scope": chart_scope,
        }
    except Exception:
        return _plan_fallback(q, query)


def _plan_fallback(q: str, query: str) -> dict:
    """Heuristic fallback when Groq is unavailable."""
    intent = "gst_summary"
    if "why" in q:
        intent = "why"
    elif "explain" in q:
        intent = "explain"
    elif "summarize" in q or "summary" in q:
        intent = "summarize"
    elif "insight" in q:
        intent = "insights"
    elif "trend" in q or "over time" in q:
        intent = "trend"
    elif "compare" in q or "vs" in q:
        intent = "compare_dates"
    elif "expense" in q or "breakdown" in q:
        intent = "expense_breakdown"
    elif "distribution" in q or "share" in q or "pie" in q:
        intent = "distribution"

    risk_flag = "evade" in q or "evasion" in q or "hide income" in q
    # upload_date = filter by when file was uploaded; row_date = filter by date in the dataset
    date_filter_type = "upload_date" if re.search(r"\bupload\s*(date|ed)?\s*(on)?\b", q, re.I) else "row_date"
    dates = re.findall(r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}", query)
    if not dates:
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", query)
    dates = _parse_dates_from_llm(dates) if dates else []
    date_filter = _dates_to_date_filter(dates)

    needs_chart = intent in CHART_INTENTS
    chart_type = None
    if needs_chart:
        chart_type = "line" if intent == "trend" else ("pie" if intent == "distribution" else "bar")

    x_axis = "date" if intent in ("trend", "compare_dates") else ("category" if intent == "expense_breakdown" else None)
    y_axis = "amount" if intent != "other" else None
    metric = "gst" if "gst" in q else ("expense" if "expense" in q else "amount")
    confidence = 0.6 if not dates else 0.85  # ambiguous if no dates

    chart_scope = "trend over time" if intent == "trend" else ("breakdown by category" if intent == "expense_breakdown" else ("compare dates" if intent == "compare_dates" else None))

    return {
        "intent": intent,
        "confidence": confidence,
        "date_filter": date_filter,
        "date_filter_type": date_filter_type,
        "client": None,
        "metric": metric,
        "needs_chart": needs_chart,
        "chart_type": chart_type,
        "x_axis": x_axis,
        "y_axis": y_axis,
        "dates": dates,
        "client_tag": None,
        "risk_flag": risk_flag,
        "chart_scope": chart_scope,
    }
