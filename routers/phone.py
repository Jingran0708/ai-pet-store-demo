"""
routers/phone.py
Handles incoming Twilio phone calls and runs the AI Phone Agent conversation
(see agents/phone_agent.py for the full flow spec: opening, buy-pet,
product inquiry, aftersale, shared appointment sub-flow, closing).

Twilio webhook flow:
1. POST /voice/incoming   <- Twilio calls this when someone dials the number
2. We reply with TwiML: <Say> (AI's question) + <Gather input="speech"> (listen)
3. POST /voice/respond    <- Twilio posts back what the caller said (as text)
4. We run it through ai_chat() using the dedicated phone agent prompt
5. When the AI signals a confirmed booking, we book it, text the customer,
   and email the store — then close the call.
"""
from fastapi import APIRouter, Request, Response
from datetime import datetime
import re

from agents.phone_agent import build_prompt
from services.ai_service import chat as ai_chat
from services import session_service as sess
from services import appointment_service as appt_svc
from services.email_service import send as send_email, STORE_EMAIL
from services.sms_service import send_appointment_confirmation_sms
from config import STORES

router = APIRouter()

# Map Twilio's CallSid -> our internal session_id, so each call keeps its own state
CALL_SESSIONS: dict = {}


def _twiml(say_text: str, gather: bool = True, hangup: bool = False) -> Response:
    """Build a TwiML response. gather=True listens for speech next."""
    safe_text = say_text.replace("&", "and")
    if gather:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="/voice/respond" method="POST" speechTimeout="auto" language="en-US">
        <Say voice="Polly.Joanna">{safe_text}</Say>
    </Gather>
    <Say voice="Polly.Joanna">Sorry, I didn't catch that. Goodbye for now!</Say>
</Response>"""
    else:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{safe_text}</Say>
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
        "Hi there, thanks for calling Happy Paws Pets! "
        "Can I start with your name?"
    )
    return _twiml(greeting)


@router.post("/voice/respond")
async def voice_respond(request: Request):
    """Called every time Twilio finishes transcribing what the caller said."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    caller_number = form.get("From", "")
    user_text = form.get("SpeechResult", "")

    session_id = CALL_SESSIONS.get(call_sid)
    if not session_id:
        # Session lost (shouldn't normally happen) — restart politely
        state = sess.create_session("appointment")
        CALL_SESSIONS[call_sid] = state["session_id"]
        session_id = state["session_id"]

    if not user_text:
        return _twiml("Sorry, I didn't catch that — could you say that again?")

    # Capture name/pet/phone/email from this turn BEFORE building the prompt,
    # so the AI immediately knows what's already been collected.
    _extract_phone_fields(session_id, user_text)

    today_str     = datetime.now().strftime("%A, %B %d, %Y")
    base_prompt   = build_prompt(today=today_str)
    state_context = sess.build_state_context(session_id)
    known_summary = _build_known_summary(session_id)
    full_prompt   = base_prompt + state_context + known_summary

    messages = [{"role": "user", "content": user_text}]

    try:
        reply = await ai_chat(full_prompt, messages)
    except RuntimeError:
        return _twiml(
            "I'm having trouble connecting right now. Please call back in a moment, or visit our website. Goodbye!",
            gather=False, hangup=True,
        )

    sess.extract_state_from_reply(session_id, user_text, reply)

    booking_ready = "[ACTION:PHONE_BOOK_READY" in reply
    parsed_date, parsed_time = _parse_booking_tag(reply)
    spoken_reply = re.sub(r"\[ACTION:PHONE_BOOK_READY.*?\]", "", reply).strip()

    is_closing = "goodbye" in spoken_reply.lower() and not booking_ready

    if booking_ready:
        confirmation_line = _attempt_booking(session_id, parsed_date, parsed_time, caller_number)
        final_text = f"{spoken_reply} {confirmation_line}"
        return _twiml(final_text, gather=False, hangup=True)

    if is_closing:
        return _twiml(spoken_reply, gather=False, hangup=True)

    return _twiml(spoken_reply)


def _parse_booking_tag(reply: str):
    """Extract date=YYYY-MM-DD and time=HH:MM AM/PM from the action tag."""
    date_match = re.search(r"date=(\d{4}-\d{2}-\d{2})", reply)
    time_match = re.search(r"time=(\d{2}:\d{2}\s?[APap][Mm])", reply)
    date = date_match.group(1) if date_match else None
    if time_match:
        raw = time_match.group(1).upper().replace("AM", " AM").replace("PM", " PM")
        time = re.sub(r"\s+", " ", raw).strip()
    else:
        time = None
    return date, time


def _extract_phone_fields(session_id: str, user_text: str) -> None:
    """
    Best-effort extraction for phone-specific fields from free-form speech.
    Order-aware: name is asked first, so the first short reply is treated as the name;
    once name is known, the next short reply (if not contact info) is treated as pet name.
    """
    state = sess.get_session(session_id)
    if not state:
        return
    c = state["collected"]
    text = user_text.strip()

    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    if email_match and not c.get("customer_email"):
        sess.update_session(session_id, {"collected.customer_email": email_match.group(0)})

    phone_match = re.search(r"(\+?1?[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text)
    if phone_match and not c.get("customer_phone"):
        sess.update_session(session_id, {"collected.customer_phone": phone_match.group(0)})

    is_contact_info = bool(email_match or phone_match)
    word_count = len(text.split())

    # Treat short, non-contact-info replies as name or pet name, in order.
    if not is_contact_info and word_count <= 6:
        cleaned = re.sub(
            r"\b(my name is|i'?m|it'?s|this is|call me|i am)\b", "", text, flags=re.IGNORECASE
        ).strip(" .,!?").title()
        if cleaned:
            if not c.get("customer_name"):
                sess.update_session(session_id, {"collected.customer_name": cleaned})
            elif not c.get("pet_name"):
                sess.update_session(session_id, {"collected.pet_name": cleaned})


def _build_known_summary(session_id: str) -> str:
    """Explicit reminder string for the AI of exactly what's already known, to stop re-asking."""
    state = sess.get_session(session_id)
    if not state:
        return ""
    c = state["collected"]
    lines = ["\n\nALREADY COLLECTED ON THIS CALL (do NOT ask for these again):"]
    if c.get("customer_name"):
        lines.append(f"- Caller's name: {c['customer_name']}")
    if c.get("pet_name"):
        lines.append(f"- Pet's name: {c['pet_name']}")
    if c.get("customer_email"):
        lines.append(f"- Email: {c['customer_email']}")
    if c.get("customer_phone"):
        lines.append(f"- Phone: {c['customer_phone']}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _attempt_booking(session_id: str, date: str, time: str, caller_number: str) -> str:
    """
    Book the appointment, text the customer, and email the store.
    Returns a spoken confirmation line for the call.
    """
    state = sess.get_session(session_id)
    if not state:
        return "Something went wrong on our end, please call back to confirm your appointment."

    c = state["collected"]
    name     = c.get("customer_name") or "Guest"
    pet_name = c.get("pet_name") or ""
    phone    = c.get("customer_phone") or caller_number or ""
    store    = STORES[0]["name"] if STORES else "Happy Paws"
    base_reason = c.get("visit_reason") or "General visit"
    reason = f"{base_reason} (Pet: {pet_name})" if pet_name else base_reason

    if not date or not time:
        return "I've noted everything down, but I couldn't quite lock in the time — our team will call you back shortly to confirm. Thanks for calling!"

    if not phone:
        return "I've got your appointment details ready, but I'll need a phone number to send your confirmation — could you give me that before we hang up?"

    if not appt_svc.has_enough_notice(date, time):
        return "That time is a bit too soon for us to prepare — appointments need at least two hours notice. Please call back with a later time, or walk in directly!"

    if not appt_svc.is_available(store, date, time):
        return "It looks like that time slot just got taken. Please call back so we can find another time that works for you!"

    appt_id = appt_svc.book(store, date, time, {
        "name": name, "phone": phone, "email": "", "reason": reason,
    })
    store_phone = appt_svc.get_store_phone(store)

    # SMS to the customer
    send_appointment_confirmation_sms(
        name=name, appt_id=appt_id, store=store, date=date, time=time, to_number=phone,
    )

    # Email to the store (not the customer) — per spec, phone bookings notify the store by email
    store_html = f"""
    <h2>New Phone Appointment Booking</h2>
    <p><strong>Confirmation #:</strong> {appt_id}</p>
    <p><strong>Customer:</strong> {name}</p>
    <p><strong>Pet:</strong> {pet_name or 'N/A'}</p>
    <p><strong>Phone:</strong> {phone}</p>
    <p><strong>Store:</strong> {store} ({store_phone})</p>
    <p><strong>Date:</strong> {date}</p>
    <p><strong>Time:</strong> {time}</p>
    <p><strong>Reason:</strong> {reason}</p>
    """
    send_email(STORE_EMAIL, f"Phone Booking Confirmed - {appt_id}", store_html)

    return f"Great news, your appointment is booked! Your confirmation number is {appt_id}, and you'll get a text message shortly. Thanks for calling Happy Paws!"
