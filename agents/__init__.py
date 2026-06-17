"""
agents/__init__.py
Agent registry — maps the frontend flow name to its system prompt.
To add a new agent: create agents/my_agent.py with SYSTEM_PROMPT,
then add one line here.

NOTE: appointment, products, and diagnose now all route to the same
merged general_agent.py, so the customer can move between booking,
product questions, and after-sales support in a single conversation
without picking a separate entry point. buy-pet stays separate.
"""
from typing import Dict, List
from agents.buy_pet_agent   import SYSTEM_PROMPT as _BUY_PET
from agents.general_agent   import SYSTEM_PROMPT as _GENERAL

REGISTRY: Dict[str, str] = {
    "buy-pet":     _BUY_PET,
    "appointment": _GENERAL,
    "products":    _GENERAL,
    "diagnose":    _GENERAL,
}

def get_prompt(flow: str) -> str:
    """Return the system prompt for a flow, falling back to buy-pet."""
    return REGISTRY.get(flow, _BUY_PET)
