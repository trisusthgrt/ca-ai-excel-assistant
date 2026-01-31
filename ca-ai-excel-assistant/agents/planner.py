"""
Planner agent — extracts intent, dates, clientTag, risk flag from user query (Step 6).
Uses Groq LLM for structured extraction.
"""
import json
import os
import re
from typing import Any, List, Optional

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEFAULT_MODEL = "llama-3.3-70b-versatile"  # or llama-3.1-8b-instant for speed


def _parse_dates_from_llm(dates_raw: Any) -> List[str]:
    """Normalize dates to ISO YYYY-MM-DD list. Handles string, list, or single date."""
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
        # Already ISO-like
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            out.append(s)
            continue
        # Try common formats (e.g. "12 Jan 2025")
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


def plan(query: str) -> dict:
    """
    Use Groq to extract: intent, dates, client_tag, risk_flag.
    Returns structured dict for policy guard and Data agent.
    """
    if not query or not query.strip():
        return {
            "intent": "unknown",
            "dates": [],
            "client_tag": None,
            "risk_flag": False,
            "chart_type": None,
            "chart_scope": None,
        }

    if not GROQ_API_KEY:
        # Fallback: simple heuristics
        q = query.lower()
        intent = "gst_summary"
        if "trend" in q or "over time" in q:
            intent = "trend"
        elif "compare" in q or "vs" in q:
            intent = "compare_dates"
        elif "expense" in q or "breakdown" in q:
            intent = "expense_breakdown"
        risk = "evade" in q or "evasion" in q or "hide income" in q
        # Crude date extraction: look for Jan 2025, 12 Jan, etc.
        dates = re.findall(r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}", q)
        if not dates:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", q)
        client_tag = None
        chart_type = "line" if intent == "trend" else ("bar" if intent in ("expense_breakdown", "compare_dates") else None)
        chart_scope = "trend over time" if intent == "trend" else ("breakdown by category" if intent == "expense_breakdown" else ("compare dates" if intent == "compare_dates" else None))
        return {
            "intent": intent,
            "dates": _parse_dates_from_llm(dates) if dates else [],
            "client_tag": client_tag,
            "risk_flag": risk,
            "chart_type": chart_type,
            "chart_scope": chart_scope,
        }

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        system = """You are a query analyzer for a CA (Chartered Accountant) Excel assistant.
Extract from the user message:
1. intent: one of gst_summary, expense_breakdown, trend, compare_dates, other
2. dates: list of dates mentioned (use YYYY-MM-DD). For "January 2025" use date range 2025-01-01 to 2025-01-31. For "12 Jan 2025" use that single date.
3. client_tag: client name/tag if mentioned, else null
4. risk_flag: true only if the query clearly asks for illegal tax evasion or hiding income; false for legal tax planning
5. chart_type: "line" if trend over time, "bar" if breakdown by category or compare dates, else null
6. chart_scope: short label for the chart (e.g. "GST trend January", "expense breakdown by category", "compare 10 Jan vs 11 Jan"), else null

Reply with ONLY a valid JSON object, no markdown, no explanation. Keys: intent, dates, client_tag, risk_flag, chart_type, chart_scope."""
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
        # Strip markdown code block if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        data = json.loads(content)
        intent = str(data.get("intent", "other")).strip() or "other"
        dates = _parse_dates_from_llm(data.get("dates"))
        client_tag = data.get("client_tag")
        if client_tag is not None:
            client_tag = str(client_tag).strip() or None
        risk_flag = bool(data.get("risk_flag", False))
        chart_type = data.get("chart_type")
        if chart_type is not None:
            chart_type = str(chart_type).strip().lower() or None
        chart_scope = data.get("chart_scope")
        if chart_scope is not None:
            chart_scope = str(chart_scope).strip() or None
        return {
            "intent": intent,
            "dates": dates,
            "client_tag": client_tag,
            "risk_flag": risk_flag,
            "chart_type": chart_type,
            "chart_scope": chart_scope,
        }
    except Exception:
        # Fallback
        return {
            "intent": "other",
            "dates": [],
            "client_tag": None,
            "risk_flag": False,
            "chart_type": None,
            "chart_scope": None,
        }
