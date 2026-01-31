"""
Response agent — generates final answer and applies safety policy (Step 6).
Uses Groq for natural-language response when allowed.
"""
import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SAFE_BLOCK_MESSAGE = "I can't assist with that. For tax and compliance matters, please rely on your Chartered Accountant or official guidelines."


def respond(
    planner_output: dict,
    analyst_output: dict,
    question: str,
    policy_action: Optional[str] = None,
    policy_message: Optional[str] = None,
) -> str:
    """
    Generate natural-language answer. If policy says block or planner risk_flag, return safe message.
    Otherwise format analyst result into a clear response (with Groq if available).
    """
    risk_flag = planner_output.get("risk_flag", False)
    if risk_flag or (policy_action == "block"):
        return policy_message or SAFE_BLOCK_MESSAGE
    if policy_action == "clarify":
        return policy_message or "Please specify the date or client you're asking about."
    reframe_prefix = (policy_message + " ") if (policy_action == "reframe" and policy_message) else ""

    # No data
    if not analyst_output or analyst_output.get("count", 0) == 0:
        msg = analyst_output.get("message", "No data found for the selected filters. Upload Excel and try again.")
        return reframe_prefix + msg

    # Build a short summary for the LLM
    summary = json.dumps(analyst_output, default=str)[:2000]

    if not GROQ_API_KEY:
        # Fallback: simple text from analyst output
        total = analyst_output.get("total", 0)
        count = analyst_output.get("count", 0)
        parts = [f"Total: {total} (from {count} rows)."]
        if "breakdown" in analyst_output:
            for b in analyst_output["breakdown"][:10]:
                parts.append(f"- {b.get('category', '?')}: {b.get('amount', 0)}")
        if "series" in analyst_output:
            for s in analyst_output["series"][:5]:
                parts.append(f"- {s.get('date', '?')}: {s.get('value', 0)}")
        if "compare" in analyst_output:
            for c in analyst_output["compare"][:5]:
                parts.append(f"- {c.get('date', '?')}: {c.get('total', 0)}")
        return reframe_prefix + " ".join(parts)

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        system = """You are a concise assistant for a Chartered Accountant firm. You answer based only on the provided data summary (JSON).
Give a short, clear answer in plain language. No legal or tax advice. No markdown. 2-4 sentences max."""
        user = f"Question: {question}\nData summary: {summary}\nAnswer:"
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=256,
        )
        content = (response.choices[0].message.content or "").strip()
        return reframe_prefix + (content if content else "No response generated.")
    except Exception:
        total = analyst_output.get("total", 0)
        count = analyst_output.get("count", 0)
        return reframe_prefix + f"Total: {total} (from {count} rows). See data above for details."
