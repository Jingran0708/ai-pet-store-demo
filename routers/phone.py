"""
routers/phone.py
Handles incoming Twilio phone calls and runs a voice version of the
appointment booking conversation, reusing the existing AI + session logic.

Twilio webhook flow:
1. POST /voice/incoming   <- Twilio calls this when someone dials the number
2. We reply with TwiML: <Say> (AI's question) + <Gather input="speech"> (listen)
3. POST /voice/respond    <- Twilio posts back what the caller said (as text)
4. We run it through the same ai_chat() used by the website, using flow="appointment"
5. Repeat until booking info is complete, then call appt_svc.book() + send email
6. <Say> a closing line and <Hangup/>
"""
from fastapi import APIRouter, Request, Response
from datetime import datetime
import re
from agents import get_prompt
from services.ai_service import chat as ai_chat
from services import session_service as sess
from services import appointment_service as appt_svc
from services.email_service import send_appointment_confirmation
from config import STORES

router = APIRouter()

# Map Twilio's CallSid -> our internal session_id, so each call keeps its own state
CALL_SESSIONS: dict = {}

PHONE_INSTRUCTIONS = (
    "\n\nYou are currently speaking on a PHONE CALL, not a text chat. Today's date is {today}. "
    "Keep every response to 1-2 short spoken sentences — no lists, no markdown, no action tags shown out loud. "
    "Speak warmly and naturally, like a kind human receptionist, not a script. "
    "You MUST collect, one at a time: the caller's name, their pet's name, "
    "whether they want to book an appointment, which store (read out store names if asked), "
    "a preferred date and time, the reason for the visit (including how the pet is feeling/doing, ask gently), "
    "and a phone number or email to confirm the booking. "
    "When the caller gives a date/time (e.g. 'tomorrow afternoon', 'next Tuesday at 2'), convert it to an exact "
    "calendar date and a half-hour time slot between 11 AM and 8 PM, then repeat it back clearly to confirm, "
    "e.g. 'Just to confirm, that's Tuesday June 23rd at 2:00 PM — does that work?'. "
    "Only proceed once the caller confirms the date and time out loud. "
    "Acknowledge what the caller just said before asking the next question (e.g. 'I'm sorry to hear Mochi hasn't been eating well.'). "
    "Once you have name, pet name, store, a CONFIRMED date, a CONFIRMED time, reason, and a phone or email, "
    "end your reply with exactly this tag on its own: "
    "[ACTION:PHONE_BOOK_READY date=YYYY-MM-DD time=HH:MM AM/PM] "
    "using the real confirmed values, e.g. [ACTION:PHONE_BOOK_READY date=2026-06-23 time=02:00 PM]"
)


def _twiml(say_text: str, gather: bool = True, hangup: bool = False) -> Response:
    """Build a TwiML response. gather=True listens for speech next."""
    if gather:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="/voice/respond" method="POST" speechTimeout="auto" language="en-US">
        <Say voice="Polly.Joanna">{say_text}</Say>
    </Gather>
    <Say voice="Polly.Joanna">Sorry, I didn't catch that. Goodbye for now!</Say>
</Response>"""
    else:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{say_text}</Say>
    {"<Hangup/>" if hangup else ""}
</Response>"""
    return Response(content=body, media_type="application/xml")


@router.post("/voice/incoming")
async def voice_incoming(request: Request):
    """First webhook Twilio hits when a call comes in."""
    form = await request.form()
    call_sid = form.get("CallSid", "")

    state = sess.create_session("appointment")
    CALL_SESSIONS[call_sid] = state["session_id"]

    greeting = (
        "Hi there, thanks for calling Happy Paws Pet Store! "
        "I'd be happy to help you book an appointment. Can I start with your name?"
    )
    return _twiml(greeting)


@router.post("/voice/respond")
async def voice_respond(request: Request):
    """Called every time Twilio finishes transcribing what the caller said."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    user_text = form.get("SpeechResult", "")

    session_id = CALL_SESSIONS.get(call_sid)
    if not session_id:
        # Session lost (shouldn't normally happen) — restart politely
        state = sess.create_session("appointment")
        CALL_SESSIONS[call_sid] = state["session_id"]
        session_id = state["session_id"]

    if not user_text:
        return _twiml("Sorry, I didn't catch that — could you say that again?")

    base_prompt   = get_prompt("appointment")
    state_context = sess.build_state_context(session_id)
    today_str     = datetime.now().strftime("%A, %B %d, %Y")
    full_prompt   = base_prompt + state_context + PHONE_INSTRUCTIONS.format(today=today_str)

    messages = [{"role": "user", "content": user_text}]

    try:
        reply = await ai_chat(full_prompt, messages)
    except RuntimeError:
        return _twiml(
            "I'm having trouble connecting right now. Please call back in a moment, or visit our website. Goodbye!",
            gather=False, hangup=True,
        )

    sess.extract_state_from_reply(session_id, user_text, reply)

    # Try to capture name/pet/phone/email from this turn too (lightweight phone-specific parsing)
    _extract_phone_fields(session_id, user_text)

    booking_ready = "[ACTION:PHONE_BOOK_READY" in reply
    parsed_date, parsed_time = _parse_booking_tag(reply)
    spoken_reply = re.sub(r"\[ACTION:PHONE_BOOK_READY.*?\]", "", reply).strip()

    if booking_ready:
        confirmation_line = _attempt_booking(session_id, parsed_date, parsed_time)
        final_text = f"{spoken_reply} {confirmation_line}"
        return _twiml(final_text, gather=False, hangup=True)

    return _twiml(spoken_reply)


def _parse_booking_tag(reply: str):
    """Extract date=YYYY-MM-DD and time=HH:MM AM/PM from the action tag."""
    date_match = re.search(r"date=(\d{4}-\d{2}-\d{2})", reply)
    time_match = re.search(r"time=(\d{2}:\d{2}\s?[APap][Mm])", reply)
    date = date_match.group(1) if date_match else None
    time = time_match.group(1).upper().replace("AM", " AM").replace("PM", " PM").replace("  ", " ").strip() if time_match else None
    return date, time


def _extract_phone_fields(session_id: str, user_text: str) -> None:
    """Very light extraction for phone/email mentioned by voice (best-effort)."""
    state = sess.get_session(session_id)
    if not state:
        return
    c = state["collected"]

    if not c.get("customer_email"):
        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", user_text)
        if email_match:
            sess.update_session(session_id, {"collected.customer_email": email_match.group(0)})

    if not c.get("customer_phone"):
        phone_match = re.search(r"(\+?1?[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", user_text)
        if phone_match:
            sess.update_session(session_id, {"collected.customer_phone": phone_match.group(0)})

    if not c.get("customer_name") and len(user_text.split()) <= 4 and state["turn_count"] <= 1:
        sess.update_session(session_id, {"collected.customer_name": user_text.strip().title()})


def _attempt_booking(session_id: str, date: str, time: str) -> str:
    """Book the appointment using collected info + confirmed date/time; return a spoken confirmation line."""
    state = sess.get_session(session_id)
    if not state:
        return "Something went wrong on our end, please call back to confirm your appointment."

    c = state["collected"]
    name  = c.get("customer_name") or "Guest"
    email = c.get("customer_email") or ""
    phone = c.get("customer_phone") or ""
    store = STORES[0]["name"] if STORES else "Happy Paws"
    reason = c.get("visit_reason") or "General visit"

    if not date or not time:
        return "I've noted everything down, but I couldn't quite lock in the time — our team will call you back shortly to confirm. Thanks for calling!"

    if not (email or phone):
        return "I've got your appointment details ready, but I'll need a phone number or email to send your confirmation — could you give me that before we hang up?"

    if not appt_svc.has_enough_notice(date, time):
        return "That time is a bit too soon for us to prepare — appointments need at least two hours notice. Please call back with a later time, or walk in directly!"

    if not appt_svc.is_available(store, date, time):
        return "It looks like that time slot just got taken. Please call back so we can find another time that works for you!"

    appt_id = appt_svc.book(store, date, time, {
        "name": name, "phone": phone, "email": email, "reason": reason,
    })
    store_phone = appt_svc.get_store_phone(store)

    if email:
        send_appointment_confirmation(
            name=name, email=email, phone=phone,
            appt_id=appt_id, store=store, store_phone=store_phone,
            date=date, time=time, reason=reason,
        )

    return f"Great news, your appointment is booked! Your confirmation number is {appt_id}. Thanks for calling Happy Paws!"
