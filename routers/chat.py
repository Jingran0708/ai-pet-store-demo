"""
routers/chat.py
POST /chat

Flow:
1. Resolve or create session
2. If flow == "diagnose": run safety classification BEFORE calling AI
   - emergency/urgent  → return pre-built safe response, no AI call
   - blocked topic     → return refusal, no AI call
   - monitor/routine   → inject safety disclaimer into prompt, call AI normally
3. Build enriched prompt (base + state context + safety injection)
4. Call AI
5. Update session state
6. Return reply + session metadata
"""
from fastapi import APIRouter, Request, HTTPException
from agents import get_prompt
from services.ai_service import chat as ai_chat
from services import session_service as sess
from services import safety_service as safety

router = APIRouter()


@router.post("/chat")
async def chat(request: Request):
    body       = await request.json()
    messages   = body.get("messages", [])
    flow       = body.get("flow", "buy-pet")
    session_id = body.get("session_id", "")
    user_text  = messages[-1]["content"] if messages else ""

    # ── 1. Resolve or create session ───────────────────────────────────────────
    state = sess.get_session(session_id) if session_id else None
    if not state:
        state      = sess.create_session(flow)
        session_id = state["session_id"]

    # ── 2. Safety check (diagnose flow only) ───────────────────────────────────
    safety_injection = ""
    pre_built_reply  = None

    if flow == "diagnose" and user_text:
        classification = safety.classify(user_text)
        severity       = classification["severity"]
        matched        = classification["matched_keywords"]

        # Log the safety event to the session
        event = safety.build_safety_event(
            session_id  = session_id,
            user_text   = user_text,
            classification = classification,
            response_type  = "pending",
        )

        if classification["blocked_topic"]:
            # Refuse immediately — no AI call
            pre_built_reply = safety.build_blocked_topic_response()
            event["response_type"] = "blocked"
            sess.log_safety_event(session_id, event)

        elif severity == "emergency":
            # Bypass AI entirely — return emergency response
            pre_built_reply = safety.build_emergency_response()
            event["response_type"] = "pre_built"
            sess.log_safety_event(session_id, event)
            sess.update_session(session_id, {
                "escalated": True,
                "escalation_count": state.get("escalation_count", 0) + 1,
            })

        elif severity == "urgent":
            # Bypass AI — return urgent response
            pre_built_reply = safety.build_urgent_response(matched)
            event["response_type"] = "pre_built"
            sess.log_safety_event(session_id, event)
            sess.update_session(session_id, {
                "escalated": True,
                "escalation_count": state.get("escalation_count", 0) + 1,
            })

        else:
            # monitor or routine — let AI answer but inject safety constraints
            safety_injection = safety.build_safety_prompt_injection(severity, matched)
            event["response_type"] = "ai_with_disclaimer"
            sess.log_safety_event(session_id, event)

    # ── 3. If pre-built reply exists, skip AI call ─────────────────────────────
    if pre_built_reply:
        # Still update session state from the exchange
        sess.extract_state_from_reply(session_id, user_text, pre_built_reply)
        current = sess.get_session(session_id)
        return {
            "choices":      [{"message": {"content": pre_built_reply}}],
            "session_id":   session_id,
            "stage":        current["stage"] if current else "unknown",
            "safety_level": classification["severity"] if flow == "diagnose" else None,
            "auto_escalated": True,
        }

    # ── 4. Build enriched prompt and call AI ───────────────────────────────────
    base_prompt   = get_prompt(flow)
    state_context = sess.build_state_context(session_id)
    full_prompt   = base_prompt + state_context + safety_injection

    try:
        reply = await ai_chat(full_prompt, messages)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # ── 5. Update session state ────────────────────────────────────────────────
    sess.extract_state_from_reply(session_id, user_text, reply)
    current = sess.get_session(session_id)

    return {
        "choices":      [{"message": {"content": reply}}],
        "session_id":   session_id,
        "stage":        current["stage"] if current else "unknown",
        "safety_level": (
            classification["severity"]
            if flow == "diagnose" and user_text
            else None
        ),
        "auto_escalated": False,
    }
