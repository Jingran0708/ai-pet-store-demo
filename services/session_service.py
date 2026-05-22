"""
services/session_service.py

Server-side conversation state tracking.
Each customer session has a unique session_id and a structured state dict
that tracks exactly where they are in the support flow.

To swap to a real database (Redis, Postgres) later:
replace the SESSION_STORE dict with DB calls — the rest of the code doesn't change.
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional

# ── In-memory store ────────────────────────────────────────────────────────────
# session_id (str) → SessionState dict
SESSION_STORE: dict = {}

# Sessions older than this are cleaned up automatically
SESSION_TTL_MINUTES = 120


# ── Stage definitions per flow ─────────────────────────────────────────────────
# Stages are ordered. Each stage has the field it's trying to collect.
FLOW_STAGES = {
    "buy-pet": [
        {"stage": "greeting",       "collects": None},
        {"stage": "pet_type",       "collects": "pet_type"},        # cat / dog
        {"stage": "experience",     "collects": "experience"},      # first-time / experienced
        {"stage": "living",         "collects": "living"},          # apartment / house
        {"stage": "family",         "collects": "family"},          # kids / other pets
        {"stage": "budget",         "collects": "budget"},          # price range
        {"stage": "lifestyle",      "collects": "lifestyle"},       # active / calm / busy
        {"stage": "recommending",   "collects": None},              # AI recommends breed
        {"stage": "kitten_shown",   "collects": "selected_kitten"}, # specific kitten chosen
        {"stage": "purchase_method","collects": "purchase_method"}, # delivery / pickup / visit
        {"stage": "checkout",       "collects": None},              # form shown
        {"stage": "completed",      "collects": None},
    ],
    "appointment": [
        {"stage": "greeting",       "collects": None},
        {"stage": "reason",         "collects": "visit_reason"},    # see pet / browse / after-sales / other
        {"stage": "booking",        "collects": None},              # form shown
        {"stage": "completed",      "collects": None},
    ],
    "products": [
        {"stage": "greeting",       "collects": None},
        {"stage": "product_inquiry","collects": "product_name"},
        {"stage": "quantity",       "collects": "quantity"},
        {"stage": "delivery_method","collects": "delivery_method"},
        {"stage": "checkout",       "collects": None},
        {"stage": "completed",      "collects": None},
    ],
    "diagnose": [
        {"stage": "greeting",       "collects": None},
        {"stage": "verify_purchase","collects": "purchased_from_us"},  # yes / no
        {"stage": "order_number",   "collects": "order_number"},
        {"stage": "verify_email",   "collects": "purchase_email"},
        {"stage": "confirm_pet",    "collects": "confirmed_pet"},
        {"stage": "purchase_age",   "collects": "days_since_purchase"},
        {"stage": "symptoms",       "collects": "symptoms"},
        {"stage": "advice_given",   "collects": None},
        {"stage": "completed",      "collects": None},
    ],
}


def _empty_state(flow: str) -> dict:
    return {
        "session_id":    "",
        "flow":          flow,
        "stage":         "greeting",
        "turn_count":    0,
        "created_at":    datetime.now().isoformat(),
        "last_active":   datetime.now().isoformat(),
        "escalated":     False,
        "escalation_count": 0,
        "collected": {
            # buy-pet
            "pet_type":        None,   # "cat" | "dog"
            "experience":      None,   # "first-time" | "experienced"
            "living":          None,   # "apartment" | "house"
            "family":          None,   # free text
            "budget":          None,   # free text e.g. "$800-1200"
            "lifestyle":       None,   # "active" | "calm" | "busy"
            "selected_breed":  None,
            "selected_kitten": None,
            "purchase_method": None,   # "delivery" | "pickup" | "visit"
            # appointment
            "visit_reason":    None,
            # products
            "product_name":    None,
            "quantity":        None,
            "delivery_method": None,
            # diagnose
            "purchased_from_us": None,
            "order_number":    None,
            "purchase_email":  None,
            "confirmed_pet":   None,
            "days_since_purchase": None,
            "symptoms":        None,
            # shared
            "customer_name":   None,
            "customer_email":  None,
            "customer_phone":  None,
        },
        # safety tracking (diagnose flow)
        "safety": {
            "highest_severity":  "routine",   # "emergency"|"urgent"|"monitor"|"routine"
            "auto_escalated":    False,
            "escalation_reason": None,        # free text set at escalation time
            "events":            [],          # list of safety event dicts
            "blocked_topics":    [],          # list of blocked topic attempts
        },
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def create_session(flow: str) -> dict:
    """Create a new session and return its full state."""
    session_id = str(uuid.uuid4())
    state = _empty_state(flow)
    state["session_id"] = session_id
    SESSION_STORE[session_id] = state
    _cleanup_old_sessions()
    return state


def get_session(session_id: str) -> Optional[dict]:
    """Return a session state or None if not found / expired."""
    state = SESSION_STORE.get(session_id)
    if not state:
        return None
    # Check TTL
    last = datetime.fromisoformat(state["last_active"])
    if datetime.now() - last > timedelta(minutes=SESSION_TTL_MINUTES):
        del SESSION_STORE[session_id]
        return None
    return state


def update_session(session_id: str, updates: dict) -> Optional[dict]:
    """
    Merge updates into a session. Supports dot-notation for collected fields.
    Example: update_session(sid, {"collected.pet_type": "cat", "stage": "experience"})
    """
    state = get_session(session_id)
    if not state:
        return None

    for key, value in updates.items():
        if key.startswith("collected."):
            field = key.split(".", 1)[1]
            state["collected"][field] = value
        else:
            state[key] = value

    state["last_active"] = datetime.now().isoformat()
    state["turn_count"]  = state.get("turn_count", 0) + 1
    SESSION_STORE[session_id] = state
    return state


def extract_state_from_reply(session_id: str, user_text: str, ai_reply: str) -> dict:
    """
    Automatically detect and store facts from the conversation.
    Called after every AI reply to keep collected fields up to date.
    Returns the updated state.
    """
    state = get_session(session_id)
    if not state:
        return {}

    updates = {}
    u = user_text.lower()
    r = ai_reply.lower()

    collected = state["collected"]
    flow      = state["flow"]

    # ── Pet type ──────────────────────────────────────────────────────────────
    if collected["pet_type"] is None and flow == "buy-pet":
        if any(w in u for w in ["cat", "kitten"]):
            updates["collected.pet_type"] = "cat"
        elif any(w in u for w in ["dog", "puppy"]):
            updates["collected.pet_type"] = "dog"

    # ── Experience ────────────────────────────────────────────────────────────
    if collected["experience"] is None and flow == "buy-pet":
        if any(w in u for w in ["first time", "first-time", "never had", "no experience", "beginner", "new to"]):
            updates["collected.experience"] = "first-time"
        elif any(w in u for w in ["experienced", "had pets", "have had", "owned", "previous"]):
            updates["collected.experience"] = "experienced"

    # ── Living situation ───────────────────────────────────────────────────────
    if collected["living"] is None and flow == "buy-pet":
        if any(w in u for w in ["apartment", "condo", "flat", "studio"]):
            updates["collected.living"] = "apartment"
        elif any(w in u for w in ["house", "home", "backyard", "garden"]):
            updates["collected.living"] = "house"

    # ── Budget ────────────────────────────────────────────────────────────────
    if collected["budget"] is None and flow == "buy-pet":
        import re
        # Match patterns like "$800", "$800-1200", "800 to 1200", "around $1000"
        budget_match = re.search(r"\$?\d{3,4}(?:\s*[-–to]+\s*\$?\d{3,4})?", u)
        if budget_match:
            updates["collected.budget"] = budget_match.group(0).strip()

    # ── Lifestyle ─────────────────────────────────────────────────────────────
    if collected["lifestyle"] is None and flow == "buy-pet":
        if any(w in u for w in ["active", "energetic", "outdoor", "run", "hike", "sport"]):
            updates["collected.lifestyle"] = "active"
        elif any(w in u for w in ["calm", "quiet", "relax", "laid back", "homebody"]):
            updates["collected.lifestyle"] = "calm"
        elif any(w in u for w in ["busy", "work a lot", "travel", "not home much"]):
            updates["collected.lifestyle"] = "busy"

    # ── Purchase method ───────────────────────────────────────────────────────
    if collected["purchase_method"] is None and flow == "buy-pet":
        if "delivery" in u or "deliver" in u or "home delivery" in u:
            updates["collected.purchase_method"] = "delivery"
        elif "pick up" in u or "pickup" in u or "collect" in u:
            updates["collected.purchase_method"] = "pickup"
        elif "in person" in u or "visit" in u or "see" in u:
            updates["collected.purchase_method"] = "visit"

    # ── Visit reason (appointment flow) ───────────────────────────────────────
    if collected["visit_reason"] is None and flow == "appointment":
        if any(w in u for w in ["see", "view", "look at", "specific pet"]):
            updates["collected.visit_reason"] = "see_pet"
        elif any(w in u for w in ["browse", "look around", "shopping"]):
            updates["collected.visit_reason"] = "browse"
        elif any(w in u for w in ["after-sales", "after sales", "issue", "problem", "sick", "unwell"]):
            updates["collected.visit_reason"] = "after_sales"

    # ── Purchased from us (diagnose flow) ─────────────────────────────────────
    if collected["purchased_from_us"] is None and flow == "diagnose":
        if any(w in u for w in ["yes", "yeah", "yep", "from happy paws", "from you", "from your"]):
            updates["collected.purchased_from_us"] = True
        elif any(w in u for w in ["no", "nope", "not from", "elsewhere", "other store"]):
            updates["collected.purchased_from_us"] = False

    # ── Order number (diagnose flow) ──────────────────────────────────────────
    if collected["order_number"] is None and flow == "diagnose":
        import re
        order_match = re.search(r"\bHP\d{5}\b", u.upper())
        if order_match:
            updates["collected.order_number"] = order_match.group(0)

    # ── Stage advancement based on action tags in AI reply ────────────────────
    if "[ACTION:CHECKOUT" in ai_reply:
        updates["stage"] = "checkout"
    elif "[ACTION:SHOW_APPT_FORM]" in ai_reply:
        updates["stage"] = "booking"
    elif "[ACTION:PRODUCT_ORDER" in ai_reply:
        updates["stage"] = "checkout"
    elif "[ACTION:ESCALATE]" in ai_reply or "[ACTION:CONTACT_FORM]" in ai_reply:
        updates["escalated"] = True
        updates["escalation_count"] = state.get("escalation_count", 0) + 1

    if updates:
        update_session(session_id, updates)

    return get_session(session_id)


def build_state_context(session_id: str) -> str:
    """
    Build a short context string injected into the system prompt.
    This tells the AI what has already been collected so it never re-asks.
    """
    state = get_session(session_id)
    if not state:
        return ""

    c = state["collected"]
    flow = state["flow"]
    lines = ["\n\n--- CONVERSATION STATE (do not re-ask collected info) ---"]

    if flow == "buy-pet":
        if c["pet_type"]:       lines.append(f"Pet type confirmed: {c['pet_type']}")
        if c["experience"]:     lines.append(f"Owner experience: {c['experience']}")
        if c["living"]:         lines.append(f"Living situation: {c['living']}")
        if c["family"]:         lines.append(f"Family situation: {c['family']}")
        if c["budget"]:         lines.append(f"Budget confirmed: {c['budget']}")
        if c["lifestyle"]:      lines.append(f"Lifestyle: {c['lifestyle']}")
        if c["selected_breed"]: lines.append(f"Breed selected: {c['selected_breed']}")
        if c["selected_kitten"]:lines.append(f"Kitten chosen: {c['selected_kitten']}")
        if c["purchase_method"]:lines.append(f"Purchase method: {c['purchase_method']}")

        # Tell the AI which intake questions are still missing
        missing = []
        if not c["experience"]:  missing.append("owner experience")
        if not c["living"]:      missing.append("living situation")
        if not c["family"]:      missing.append("family situation")
        if not c["budget"]:      missing.append("budget")
        if not c["lifestyle"]:   missing.append("lifestyle")
        if missing:
            lines.append(f"Still need to collect: {', '.join(missing)}")
        else:
            lines.append("All intake info collected. Proceed to recommendation if not yet done.")

    elif flow == "appointment":
        if c["visit_reason"]: lines.append(f"Visit reason: {c['visit_reason']}")

    elif flow == "products":
        if c["product_name"]:    lines.append(f"Product of interest: {c['product_name']}")
        if c["quantity"]:        lines.append(f"Quantity: {c['quantity']}")
        if c["delivery_method"]: lines.append(f"Delivery method: {c['delivery_method']}")

    elif flow == "diagnose":
        if c["purchased_from_us"] is True:  lines.append("Purchase verified: YES from Happy Paws")
        if c["purchased_from_us"] is False: lines.append("Purchase verified: NOT from Happy Paws — recommend vet only")
        if c["order_number"]:      lines.append(f"Order number: {c['order_number']}")
        if c["purchase_email"]:    lines.append(f"Purchase email: {c['purchase_email']}")
        if c["confirmed_pet"]:     lines.append(f"Pet confirmed: {c['confirmed_pet']}")
        if c["symptoms"]:          lines.append(f"Reported symptoms: {c['symptoms']}")

    # Shared customer info
    if c["customer_name"]:  lines.append(f"Customer name: {c['customer_name']}")
    if c["customer_email"]: lines.append(f"Customer email: {c['customer_email']}")

    if state.get("escalated"):
        lines.append(f"Note: customer has requested human escalation {state['escalation_count']} time(s).")

    lines.append(f"Turn count: {state['turn_count']}")
    lines.append("--- END STATE ---")
    return "\n".join(lines)


def get_session_summary(session_id: str) -> dict:
    """
    Return a clean summary dict for staff handoff or analytics.
    """
    state = get_session(session_id)
    if not state:
        return {}
    c = state["collected"]
    return {
        "session_id":   session_id,
        "flow":         state["flow"],
        "stage":        state["stage"],
        "turn_count":   state["turn_count"],
        "escalated":    state["escalated"],
        "created_at":   state["created_at"],
        "customer": {
            "name":  c["customer_name"],
            "email": c["customer_email"],
            "phone": c["customer_phone"],
        },
        "intent": {
            "pet_type":        c.get("pet_type"),
            "budget":          c.get("budget"),
            "selected_breed":  c.get("selected_breed"),
            "purchase_method": c.get("purchase_method"),
            "visit_reason":    c.get("visit_reason"),
            "product_name":    c.get("product_name"),
            "order_number":    c.get("order_number"),
            "symptoms":        c.get("symptoms"),
        },
        "safety": state.get("safety", {}),
    }


def log_safety_event(session_id: str, event: dict) -> None:
    """Attach a safety event to the session and update highest severity."""
    state = get_session(session_id)
    if not state:
        return
    severity_order = ["routine", "monitor", "urgent", "emergency"]
    current  = state["safety"]["highest_severity"]
    incoming = event.get("severity", "routine")
    if severity_order.index(incoming) > severity_order.index(current):
        state["safety"]["highest_severity"] = incoming
    state["safety"]["events"].append(event)
    if event.get("auto_escalated"):
        state["safety"]["auto_escalated"] = True
    if event.get("blocked_topic") and event.get("user_text_snippet"):
        state["safety"]["blocked_topics"].append(event["user_text_snippet"])
    state["last_active"] = datetime.now().isoformat()
    SESSION_STORE[session_id] = state


def _cleanup_old_sessions():
    """Remove sessions older than SESSION_TTL_MINUTES. Called on each new session."""
    cutoff = datetime.now() - timedelta(minutes=SESSION_TTL_MINUTES)
    expired = [
        sid for sid, s in SESSION_STORE.items()
        if datetime.fromisoformat(s["last_active"]) < cutoff
    ]
    for sid in expired:
        del SESSION_STORE[sid]
