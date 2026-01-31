"""
Policy guard — block evasion, reframe ambiguous tax questions, ask clarification.
Step 1 skeleton — placeholder only.
"""


def check_policy(query: str, planner_output: dict) -> dict:
    """
    Placeholder: returns allow by default.
    Returns {"action": "allow"|"block"|"reframe"|"clarify", "message": str}.
    """
    return {"action": "allow", "message": ""}
