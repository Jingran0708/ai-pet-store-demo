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
from data.loader import cat_breeds, dog_breeds
from config import STORES

router = APIRouter()

# Map Twilio's CallSid -> our internal session_id, so each call keeps its own state
CALL_SESSIONS: dict = {}
# Map Twilio's CallSid -> the last thing we said, so a silent/unclear response can be re-asked naturally
LAST_PROMPT: dict = {}
NOINPUT_COUNT: dict = {}


def _twiml(say_text: str, call_sid: str = "", gather: bool = True, hangup: bool = False) -> Response:
    """Build a TwiML response. gather=True listens for speech next."""
    safe_text = say_text.replace("&", "and")
    if gather and call_sid:
        LAST_PROMPT[call_sid] = safe_text
    if gather:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="/voice/respond" method="POST" speechTimeout="3" timeout="8" actionOnEmptyResult="true" language="en-US">
        <Say voice="Polly.Joanna">{safe_text}</Say>
    </Gather>
    <Redirect method="POST">/voice/respond</Redirect>
</Response>"""
    else:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{safe_text}</Say>
    {"<Hangup/>" if hangup else ""}
</Response>"""
    return Response(content=body, media_type="application/xml")


def _twiml_phone_entry(say_text: str, call_sid: str = "") -> Response:
    """
    Special Gather for phone-number steps: accepts EITHER spoken digits OR keypad (DTMF) input,
    since phone numbers are often easier and more reliable to type than to say aloud.
    Keypad entry ends with '#'.
    """
    safe_text = say_text.replace("&", "and")
    if call_sid:
        LAST_PROMPT[call_sid] = safe_text
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech dtmf" action="/voice/respond" method="POST" speechTimeout="3" timeout="10"
            finishOnKey="#" actionOnEmptyResult="true" language="en-US">
        <Say voice="Polly.Joanna">{safe_text} You can also type it on your keypad, followed by the pound key.</Say>
    </Gather>
    <Redirect method="POST">/voice/respond</Redirect>
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
    return _twiml(greeting, call_sid=call_sid)


@router.post("/voice/noinput")
async def voice_noinput(request: Request):
    """Called when <Gather> times out with no speech detected at all."""
    form = await request.form()
    call_sid = form.get("CallSid", "")

    count = NOINPUT_COUNT.get(call_sid, 0) + 1
    NOINPUT_COUNT[call_sid] = count

    if count >= 3:
        return _twiml(
            "I'm having trouble hearing you, so I'll let you go for now — please feel free to call back. Goodbye!",
            gather=False, hangup=True,
        )

    last_question = LAST_PROMPT.get(call_sid, "Could you tell me how I can help you today?")
    state = sess.get_session(CALL_SESSIONS.get(call_sid, ""))
    name = state["collected"].get("customer_name") if state else None
    prefix = f"Sorry {name}, I didn't catch that — " if name else "Sorry, I didn't catch that — "

    return _twiml(f"{prefix}{last_question}", call_sid=call_sid)


@router.post("/voice/respond")
async def voice_respond(request: Request):
    """Called every time Twilio finishes transcribing what the caller said."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    caller_number = form.get("From", "")
    user_text = form.get("SpeechResult", "")
    digits = form.get("Digits", "")

    session_id = CALL_SESSIONS.get(call_sid)
    if not session_id:
        # Session lost (shouldn't normally happen) — restart politely
        state = sess.create_session("appointment")
        CALL_SESSIONS[call_sid] = state["session_id"]
        session_id = state["session_id"]

    # Keypad entry of a phone number — treat this as the user's "spoken" text too,
    # so the rest of the pipeline (extraction, AI context) sees it consistently.
    if digits and not user_text:
        user_text = digits
        sess.update_session(session_id, {"collected.confirmed_phone": digits})

    if not user_text:
        last_question = LAST_PROMPT.get(call_sid, "Could you tell me how I can help you today?")
        state = sess.get_session(session_id)
        name = state["collected"].get("customer_name") if state else None
        prefix = f"Sorry {name}, I didn't catch that clearly — " if name else "Sorry, I didn't catch that clearly — "
        return _twiml(f"{prefix}can you say that again?", call_sid=call_sid)

    NOINPUT_COUNT[call_sid] = 0

    # Capture name/pet/phone/email from this turn BEFORE building the prompt,
    # so the AI immediately knows what's already been collected.
    _extract_phone_fields(session_id, user_text)
    _maybe_confirm_caller_number(session_id, call_sid, user_text, caller_number)

    today_str     = datetime.now().strftime("%A, %B %d, %Y")
    base_prompt   = build_prompt(today=today_str)
    state_context = sess.build_state_context(session_id)
    known_summary = _build_known_summary(session_id, caller_number)
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
    parsed_store, parsed_date, parsed_time = _parse_booking_tag(reply)
    spoken_reply = re.sub(r"\[ACTION:PHONE_BOOK_READY.*?\]", "", reply).strip()

    # Lock in store/date/time as soon as the AI states them clearly in a confirmation sentence,
    # so they're never re-asked even before the final booking tag appears.
    _lock_in_confirmed_fields(session_id, spoken_reply, parsed_store, parsed_date, parsed_time)

    is_closing = "goodbye" in spoken_reply.lower() and not booking_ready

    if booking_ready:
        confirmed = sess.get_session(session_id)["collected"] if sess.get_session(session_id) else {}
        final_store = parsed_store or confirmed.get("confirmed_store")
        final_date = parsed_date or confirmed.get("confirmed_date")
        final_time = parsed_time or confirmed.get("confirmed_time")
        final_phone = confirmed.get("confirmed_phone")
        confirmation_line = _attempt_booking(session_id, final_store, final_date, final_time, caller_number, final_phone)
        final_text = f"{spoken_reply} {confirmation_line}"
        return _twiml(final_text, call_sid=call_sid, gather=False, hangup=True)

    if is_closing:
        return _twiml(spoken_reply, call_sid=call_sid, gather=False, hangup=True)

    asking_for_phone = _is_asking_for_phone(spoken_reply, session_id)
    if asking_for_phone:
        return _twiml_phone_entry(spoken_reply, call_sid=call_sid)

    return _twiml(spoken_reply, call_sid=call_sid)


def _is_asking_for_phone(spoken_reply: str, session_id: str) -> bool:
    """True if this AI reply is asking the caller about a phone number for SMS confirmation."""
    state = sess.get_session(session_id)
    if state and state["collected"].get("confirmed_phone"):
        return False  # already locked in, no need for keypad mode anymore
    lower = spoken_reply.lower()
    keywords = ["calling from", "phone number", "different number", "text the confirmation", "send the confirmation"]
    return any(k in lower for k in keywords)


def _parse_booking_tag(reply: str):
    """Extract store=NAME, date=YYYY-MM-DD, and time=HH:MM AM/PM from the action tag."""
    store_match = re.search(r"store=([^,\]]+?)(?:\s+date=|\s*\])", reply)
    date_match  = re.search(r"date=(\d{4}-\d{2}-\d{2})", reply)
    time_match  = re.search(r"time=(\d{2}:\d{2}\s?[APap][Mm])", reply)
    store = store_match.group(1).strip() if store_match else None
    date = date_match.group(1) if date_match else None
    if time_match:
        raw = time_match.group(1).upper().replace("AM", " AM").replace("PM", " PM")
        time = re.sub(r"\s+", " ", raw).strip()
    else:
        time = None
    return store, date, time


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

    # Breed can be mentioned in a longer sentence too (e.g. "I'm looking for a Golden Retriever"),
    # so check on every turn, not just short replies.
    if not c.get("confirmed_breed"):
        breed = _match_breed(text)
        if breed:
            sess.update_session(session_id, {"collected.confirmed_breed": breed})

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


def _match_store(text: str) -> str:
    """Find which known store name is mentioned in a piece of text, if any."""
    text_lower = text.lower()
    for s in STORES:
        short_name = s["name"].split(" - ")[0].strip()
        # match on the distinguishing part of the name (e.g. "Downtown", "Midtown", "East End")
        distinguishing = short_name.replace("Happy Paws", "").strip()
        if distinguishing and distinguishing.lower() in text_lower:
            return s["name"]
        if short_name.lower() in text_lower:
            return s["name"]
    return ""


def _match_breed(text: str) -> str:
    """Find which known cat/dog breed is mentioned in a piece of text, if any."""
    text_lower = text.lower()
    all_breeds = list(cat_breeds().keys()) + list(dog_breeds().keys())
    for breed in all_breeds:
        if breed.lower() in text_lower:
            return breed
    return ""


_AFFIRM_WORDS = ["yes", "yeah", "yep", "sure", "that's fine", "that works", "okay", "ok", "correct", "use this one", "use that"]
_DENY_WORDS = ["no", "nope", "different number", "use another", "not that one"]


def _maybe_confirm_caller_number(session_id: str, call_sid: str, user_text: str, caller_number: str) -> None:
    """
    If the AI's last question offered to use the caller's current number, interpret this reply
    as agreement (lock in caller_number), disagreement (wait for them to give a new one), or
    a directly-spoken new number (lock that in instead).
    """
    state = sess.get_session(session_id)
    if not state:
        return
    c = state["collected"]
    if c.get("confirmed_phone"):
        return  # already locked in, nothing to do

    last_question = LAST_PROMPT.get(call_sid, "").lower()
    asked_about_number = "calling from" in last_question or "number you're calling" in last_question or "use a different number" in last_question
    if not asked_about_number:
        return

    text_lower = user_text.strip().lower()

    # A new number spoken directly takes priority over yes/no wording
    phone_match = re.search(r"(\+?1?[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", user_text)
    if phone_match:
        sess.update_session(session_id, {"collected.confirmed_phone": phone_match.group(0)})
        return

    if any(word in text_lower for word in _AFFIRM_WORDS) and caller_number:
        sess.update_session(session_id, {"collected.confirmed_phone": caller_number})
        return
    # If they said no/different number without giving one yet, leave confirmed_phone unset
    # so the AI naturally asks them to say the new number next.


def _lock_in_confirmed_fields(session_id: str, spoken_reply: str, parsed_store: str, parsed_date: str, parsed_time: str) -> None:
    """
    Once the AI's reply contains a clear confirmation sentence mentioning store/date/time,
    save them to session state so they're never re-asked, even on turns before the final booking tag.
    """
    state = sess.get_session(session_id)
    if not state:
        return
    c = state["collected"]
    lower = spoken_reply.lower()
    looks_like_confirmation = any(
        phrase in lower for phrase in ["just to confirm", "to confirm", "does that work", "is that correct"]
    )

    if not looks_like_confirmation and not parsed_store:
        return

    if not c.get("confirmed_store"):
        store = parsed_store or _match_store(spoken_reply)
        if store:
            sess.update_session(session_id, {"collected.confirmed_store": store})

    if parsed_date and not c.get("confirmed_date"):
        sess.update_session(session_id, {"collected.confirmed_date": parsed_date})
    if parsed_time and not c.get("confirmed_time"):
        sess.update_session(session_id, {"collected.confirmed_time": parsed_time})


def _build_known_summary(session_id: str, caller_number: str = "") -> str:
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
    if c.get("confirmed_breed"):
        lines.append(f"- Breed already discussed: {c['confirmed_breed']} (do not ask 'what breed are you looking for' again)")
    if c.get("confirmed_store"):
        lines.append(f"- CONFIRMED store: {c['confirmed_store']} (do not ask again)")
    if c.get("confirmed_date"):
        lines.append(f"- CONFIRMED date: {c['confirmed_date']} (do not ask again)")
    if c.get("confirmed_time"):
        lines.append(f"- CONFIRMED time: {c['confirmed_time']} (do not ask again)")
    if c.get("customer_email"):
        lines.append(f"- Email: {c['customer_email']}")
    if c.get("confirmed_phone"):
        lines.append(f"- CONFIRMED phone number for SMS: {c['confirmed_phone']} (do not ask again)")
    elif caller_number:
        lines.append(
            f"- Caller's number (the number they're calling from right now): {caller_number} "
            "(you may offer to use this for the SMS confirmation)"
        )
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _attempt_booking(session_id: str, store: str, date: str, time: str, caller_number: str, confirmed_phone: str = None) -> str:
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
    phone    = confirmed_phone or c.get("confirmed_phone") or c.get("customer_phone") or caller_number or ""
    store    = store or c.get("confirmed_store") or (STORES[0]["name"] if STORES else "Happy Paws")
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
