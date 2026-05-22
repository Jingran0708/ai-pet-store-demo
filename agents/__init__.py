"""
agents/__init__.py
Agent registry — maps the frontend flow name to its system prompt.
To add a new agent: create agents/my_agent.py with SYSTEM_PROMPT,
then add one line here.
"""
from typing import Dict, List
from agents.buy_pet_agent   import SYSTEM_PROMPT as _BUY_PET
from agents.appointment_agent import SYSTEM_PROMPT as _APPOINTMENT
from agents.products_agent  import SYSTEM_PROMPT as _PRODUCTS
from agents.diagnose_agent  import SYSTEM_PROMPT as _DIAGNOSE

REGISTRY: Dict[str, str] = {
    "buy-pet":     _BUY_PET,
    "appointment": _APPOINTMENT,
    "products":    _PRODUCTS,
    "diagnose":    _DIAGNOSE,
}

def get_prompt(flow: str) -> str:
    """Return the system prompt for a flow, falling back to buy-pet."""
    return REGISTRY.get(flow, _BUY_PET)
