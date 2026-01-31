"""
Orchestrator — runs fixed pipeline: Planner → Data → Analyst → Response.
No AI logic yet (Step 1 skeleton).
"""
from .planner import plan
from .data_agent import fetch_data
from .analyst import analyze
from .responder import respond


def run(query: str) -> dict:
    """Placeholder: runs pipeline and returns empty response."""
    planner_output = plan(query)
    data = fetch_data(planner_output)
    analyst_output = analyze(planner_output.get("intent", ""), data)
    answer = respond(planner_output.get("intent", ""), analyst_output, query)
    return {"answer": answer, "chart_type": None, "chart_data": None}
