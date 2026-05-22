"""
services/safety_service.py

Safety and escalation system for the pet diagnosis assistant.

Responsibilities:
1. Scan every user message for symptom severity BEFORE calling the AI
2. Classify severity: emergency / urgent / monitor / routine
3. For emergency/urgent: return a pre-built safe response immediately,
   bypassing the AI entirely (faster + guaranteed safe wording)
4. Log all safety events to the session for staff review
5. Never give medical dosage advice or home treatment instructions

To update keywords or severity rules: edit data/json/safety_rules.json only.
"""
import json
from pathlib import Path
from functools import lru_cache
from datetime import datetime
from typing import Optional

_JSON = Path(__file__).parent.parent / "data" / "json"


@lru_cache(maxsize=1)
def _rules() -> dict:
    with open(_JSON / "safety_rules.json", encoding="utf-8") as f:
        return json.load(f)


# ── Severity classification ────────────────────────────────────────────────────

SEVERITY_ORDER = ["emergency", "urgent", "monitor", "routine"]


def classify(user_text: str) -> dict:
    """
    Scan user_text and return a classification dict:
    {
        "severity":      "emergency" | "urgent" | "monitor" | "routine",
        "matched_keywords": [...],
        "auto_escalate": bool,
        "show_vet_banner": bool,
        "blocked_topic":  bool,
    }
    """
    text  = user_text.lower()
    rules = _rules()

    matched  = []
    severity = "routine"
    blocked  = False

    # Check blocked advice topics first
    for phrase in rules.get("blocked_advice_topics", []):
        if phrase in text:
            blocked = True
            break

    # Check severity levels from highest to lowest — stop at first match
    for level in ["emergency", "urgent", "monitor"]:
        key = f"{level}_keywords"
        for kw in rules.get(key, []):
            if kw in text:
                matched.append(kw)
                if level == "emergency" and severity != "emergency":
                    severity = "emergency"
                elif level == "urgent" and severity not in ("emergency",):
                    severity = "urgent"
                elif level == "monitor" and severity not in ("emergency", "urgent"):
                    severity = "monitor"

    level_config = rules["severity_levels"].get(severity, {})
    return {
        "severity":         severity,
        "matched_keywords": list(set(matched)),
        "auto_escalate":    level_config.get("auto_escalate", False),
        "show_vet_banner":  level_config.get("show_vet_banner", False),
        "blocked_topic":    blocked,
    }


# ── Pre-built safe responses (bypass AI for high severity) ────────────────────

def build_emergency_response() -> str:
    return (
        "I'm very concerned about what you've described. "
        "This sounds like it could be a veterinary emergency — "
        "please contact a professional veterinarian immediately and do not wait.\n\n"
        "I've flagged this conversation so our Happy Paws team will follow up with you as soon as possible. "
        "Is there anything else I can help you with while you get in touch with a vet? "
        "[ACTION:ESCALATE]"
    )


def build_urgent_response(matched: list) -> str:
    symptom_note = ("The symptoms you mentioned (" + ", ".join(matched[:3]) + ") ") if matched else "The symptoms you described "
    return (
        symptom_note + "suggest your pet needs veterinary attention soon — ideally within the next few hours.\n\n"
        "Please do not wait to see if they improve on their own. "
        "We recommend contacting a professional veterinarian as soon as possible.\n\n"
        "I'm also alerting our Happy Paws team so someone can follow up with you. "
        "Would you like me to book an appointment at your original Happy Paws store as well? "
        "[ACTION:ESCALATE]"
    )


def build_blocked_topic_response() -> str:
    return (
        "I'm not able to provide advice on medication dosages or home medical treatments for pets — "
        "this needs to come from a qualified veterinarian who can properly examine your pet. "
        "I'd strongly recommend contacting a vet directly. "
        "Is there anything else I can help you with?"
    )


def build_monitor_disclaimer() -> str:
    """Returns a disclaimer string injected into the AI prompt for monitor-level symptoms."""
    return (
        "\n\nSAFETY NOTICE: The customer has described symptoms that warrant monitoring. "
        "You MUST include the following disclaimer in your response: "
        "'Please note: this is general information only and not a medical diagnosis. "
        "If symptoms worsen or persist for more than 24 hours, please consult a veterinarian.' "
        "Do NOT suggest any medication dosages or home treatments. "
        "Do NOT tell the customer their pet will be fine without professional evaluation."
    )


# ── Safety event logging ───────────────────────────────────────────────────────

def build_safety_event(
    session_id: str,
    user_text: str,
    classification: dict,
    response_type: str,  # "ai_with_disclaimer" | "pre_built" | "blocked"
) -> dict:
    """Build a safety event record for attaching to the session."""
    return {
        "timestamp":        datetime.now().isoformat(),
        "session_id":       session_id,
        "user_text_snippet": user_text[:200],
        "severity":         classification["severity"],
        "matched_keywords": classification["matched_keywords"],
        "blocked_topic":    classification["blocked_topic"],
        "response_type":    response_type,
        "auto_escalated":   classification["auto_escalate"],
    }


# ── Symptom context builder (injected into AI prompt) ─────────────────────────

def build_safety_prompt_injection(severity: str, matched: list) -> str:
    """
    Returns a string appended to the system prompt when safety flags are raised.
    Tells the AI exactly what constraints apply for this severity level.
    """
    if severity == "routine":
        return (
            "\n\nSAFETY BASELINE: Always include the disclaimer that your responses are "
            "general guidance only and not a medical diagnosis. Never suggest medication dosages."
        )
    if severity == "monitor":
        return build_monitor_disclaimer()
    if severity in ("urgent", "emergency"):
        return (
            "\n\nCRITICAL SAFETY OVERRIDE: The customer has described potentially serious symptoms. "
            "You MUST: (1) Express genuine concern, (2) Strongly recommend immediate veterinary care, "
            "(3) NOT provide home treatment advice, (4) NOT reassure them it is probably fine, "
            "(5) End with [ACTION:ESCALATE] to alert staff. "
            "This is not a situation for general guidance."
        )
    return ""
