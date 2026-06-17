"""
routers/phone.py
Handles incoming Twilio phone calls and runs the AI Phone Agent conversation.

ARCHITECTURE (v3 — structured state):
We keep the FULL conversation history per call (not a summary) and ask the AI
to return JSON each turn: {"reply": ..., "state": {...}, "ready_to_book": bool}.
The AI maintains its own state because it actually understands the conversation —
we just persist whatever it returns. No regex guessing of name/breed/store/etc.

Twilio webhook flow:
1. POST /voice/incoming   <- Twilio calls this when someone dials the number
2. We reply with TwiML: <Say> (AI's reply) + <Gather> (listen for next turn)
3. POST /voice/respond    <- Twilio posts back what the caller said (speech or DTMF)
4. We append to history, call the AI, parse its JSON, persist state
5. When ready_to_book is true and required fields are present, we book it,
   text the customer, and email the store — then close the call.
"""
from fastapi import APIRouter, Request, Response
from datetime import datetime
import json
import re

from agents.phone_agent import build_prompt
from services.ai_service import chat as ai_chat
from services import appointment_service as appt_svc
from services.email_service import send as send_email, STORE_EMAIL
from services.sms_service import send_appointment_confirmation_sms
from config import STORES

router = APIRouter()

# All per-call data lives here, keyed by Twilio's CallSid.
# { call_sid: {"history": [...], "state": {...}, "booked": bool, "last_prompt": str, "noinput_count": int} }
CALLS: dict = {}

REQUIRED_FOR_BOOKING = ["name", "store", "date", "time", "phone"]


def _new_call() -> dict:
    return {
        "history": [],
        "state": {
            "name": None, "pet_name": None, "intent": None, "breed": None,
            "store": None, "date": None, "time": None, "reason": None, "phone": None,
        },
        "booked": False,
        "last_prompt": "",
        "noinput_count": 0,
    }


def _twiml(say_text: str, call: dict = None, gather: bool = True, hangup: bool = False, allow_keypad: bool = False) -> Response:
    """Build a TwiML response. gather=True listens for the next turn."""
    safe_text = say_text.replace("&", "and")
    if gather and call is not None:
        call["last_prompt"] = safe_text
    if gather:
        if allow_keypad:
            extra = " You can also type it on your keypad, followed by the pound key."
            input_attr = "speech dtmf"
            finish_key_attr = ' finishOnKey="#"'
        else:
            extra = ""
            input_attr = "speech"
            finish_key_attr = ""
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="{input_attr}" action="/voice/respond" method="POST" speechTimeout="3" timeout="8"
            actionOnEmptyResult="true"{finish_key_attr} language="en-US">
        <Say voice="Polly.Joanna">{safe_text}{extra}</Say>
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


@router.post("/voice/incoming")
async def voice_incoming(request: Request):
    """First webhook Twilio hits when a call comes in."""
    form = await request.form()
    call_sid = form.get("CallSid", "")

    call = _new_call()
    CALLS[call_sid] = call

    greeting = "Hi there, thanks for calling Happy Paws Pets! Can I start with your name?"
    call["history"].append({"role": "assistant", "content": greeting})
    return _twiml(greeting, call=call)


@router.post("/voice/respond")
async def voice_respond(request: Request):
    """Called every time Twilio finishes transcribing (or keying in) what the caller said."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    caller_number = form.get("From", "")
    user_text = form.get("SpeechResult", "")
    digits = form.get("Digits", "")

    call = CALLS.get(call_sid)
    if not call:
        call = _new_call()
        CALLS[call_sid] = call

    # Keypad entry counts as input too — most often used for a phone number.
    if digits and not user_text:
        user_text = digits

    if not user_text:
        call["noinput_count"] += 1
        if call["noinput_count"] >= 3:
            return _twiml(
                "I'm having trouble hearing you, so I'll let you go for now — please feel free to call back. Goodbye!",
                gather=False, hangup=True,
            )
        name = call["state"].get("name")
        prefix = f"Sorry {name}, I didn't catch that clearly — " if name else "Sorry, I didn't catch that clearly — "
        last_q = call["last_prompt"] or "could you tell me how I can help you today?"
        return _twiml(f"{prefix}{last_q}", call=call)

    call["noinput_count"] = 0
    call["history"].append({"role": "user", "content": user_text})

    today_str = datetime.now().strftime("%A, %B %d, %Y")
    base_prompt = build_prompt(today=today_str)
    context_note = _build_context_note(call["state"], caller_number, call["booked"])
    full_prompt = base_prompt + context_note

    try:
        raw_reply = await ai_chat(full_prompt, call["history"], json_mode=True)
    except RuntimeError:
        return _twiml(
            "I'm having trouble connecting right now. Please call back in a moment, or visit our website. Goodbye!",
            gather=False, hangup=True,
        )

    print(f"[PHONE DEBUG] call_sid={call_sid} raw_ai_reply={raw_reply!r}")

    parsed = _parse_ai_json(raw_reply)

    if parsed is None:
        # Should be rare now that json_mode is enforced server-side, but keep one retry as a safety net.
        print(f"[PHONE DEBUG] call_sid={call_sid} FAILED TO PARSE JSON — retrying with correction")
        retry_prompt = full_prompt + (
            "\n\nYOUR PREVIOUS RESPONSE WAS REJECTED because it was not valid JSON. "
            f"Your previous response was: {raw_reply!r}. "
            "Take the same meaning/content as that response, but output it as ONLY the required JSON object "
            "format described above. No exceptions."
        )
        try:
            raw_reply_retry = await ai_chat(retry_prompt, call["history"], json_mode=True)
            print(f"[PHONE DEBUG] call_sid={call_sid} retry_raw_ai_reply={raw_reply_retry!r}")
            parsed = _parse_ai_json(raw_reply_retry)
        except RuntimeError:
            parsed = None

    if parsed is None:
        # Still failed after retry — fall back to plain text so the call doesn't break,
        # but state/ready_to_book are NOT trusted this turn.
        print(f"[PHONE DEBUG] call_sid={call_sid} STILL FAILED TO PARSE JSON after retry")
        spoken_reply = _strip_json_artifacts(raw_reply)
        ready_to_book = False
    else:
        spoken_reply = parsed.get("reply", "").strip() or "Sorry, could you say that again?"
        new_state = parsed.get("state", {})
        # Merge: keep any previously known field if the AI returned null/missing for it.
        for key in call["state"]:
            incoming = new_state.get(key)
            if incoming:
                call["state"][key] = incoming
        ready_to_book = bool(parsed.get("ready_to_book"))
        print(f"[PHONE DEBUG] call_sid={call_sid} ready_to_book={ready_to_book} state={call['state']}")

    call["history"].append({"role": "assistant", "content": spoken_reply})

    is_closing = "goodbye" in spoken_reply.lower() and not ready_to_book

    if ready_to_book and not call["booked"]:
        missing = [f for f in REQUIRED_FOR_BOOKING if not call["state"].get(f)]
        if missing:
            print(f"[PHONE DEBUG] call_sid={call_sid} ready_to_book=True but MISSING FIELDS: {missing}")
            # Let the caller know what's still needed instead of silently looping.
            nudge = f" I just need your {missing[0].replace('_', ' ')} to finish booking — could you give me that?"
            return _twiml(spoken_reply + nudge, call=call, allow_keypad=_wants_phone(spoken_reply))
        confirmation_line = _do_booking(call, caller_number)
        call["booked"] = True
        final_text = f"{spoken_reply} {confirmation_line}"
        call["history"].append({"role": "assistant", "content": confirmation_line})
        return _twiml(final_text, call=call, gather=True)  # keep listening in case they have more requests

    if is_closing:
        return _twiml(spoken_reply, call=call, gather=False, hangup=True)

    return _twiml(spoken_reply, call=call, allow_keypad=_wants_phone(spoken_reply))


def _wants_phone(spoken_reply: str) -> bool:
    """True if this turn's reply is asking about a phone number, so we should allow keypad entry too."""
    lower = spoken_reply.lower()
    return any(k in lower for k in ["calling from", "phone number", "different number", "text the confirmation"])


def _build_context_note(state: dict, caller_number: str, already_booked: bool) -> str:
    """Tells the AI the current known state and caller's number, as ground truth to carry forward."""
    lines = ["\n\nCURRENT STATE (carry every non-null field forward unchanged unless corrected):"]
    lines.append(json.dumps(state))
    if caller_number:
        lines.append(f"\ncaller_number (the number they're calling from): {caller_number}")
    if already_booked:
        lines.append(
            "\nNOTE: an appointment was already booked earlier in this call. If the caller wants something "
            "else now, help them, and only set ready_to_book true again if they want a NEW appointment."
        )
    lines.append(
        "\n\nREMINDER — YOUR ENTIRE RESPONSE MUST BE ONE JSON OBJECT, NOTHING ELSE. "
        'Format: {"reply": "...", "state": {"name": ..., "pet_name": ..., "intent": ..., "breed": ..., '
        '"store": ..., "date": ..., "time": ..., "reason": ..., "phone": ...}, "ready_to_book": true/false}. '
        "Do not write plain conversational text outside the JSON. Do not use markdown code fences."
    )
    return "\n".join(lines)


def _parse_ai_json(raw: str):
    """Extract and parse the JSON object the AI was instructed to return. Returns None if invalid."""
    text = raw.strip()
    # Strip markdown code fences if the model added them despite instructions.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Try to find the first {...} block as a fallback.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
        return None


def _strip_json_artifacts(raw: str) -> str:
    """If JSON parsing failed entirely, at least remove obvious JSON punctuation so speech doesn't sound broken."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    if text.startswith("{"):
        # Best-effort: try to pull out a "reply" value even if the rest is malformed.
        match = re.search(r'"reply"\s*:\s*"(.*?)"\s*[,}]', text, re.DOTALL)
        if match:
            return match.group(1)
        return "Sorry, could you say that again?"
    return text


def _do_booking(call: dict, caller_number: str) -> str:
    """Book the appointment, text the customer, and email the store. Returns a spoken confirmation line."""
    s = call["state"]
    name = s.get("name") or "Guest"
    pet_name = s.get("pet_name") or ""
    store = s.get("store") or (STORES[0]["name"] if STORES else "Happy Paws")
    date = s.get("date")
    time = s.get("time")
    phone = s.get("phone") or caller_number or ""
    base_reason = s.get("reason") or "General visit"
    reason = f"{base_reason} (Pet: {pet_name})" if pet_name else base_reason

    print(f"[PHONE DEBUG] _do_booking called: store={store!r} date={date!r} time={time!r} phone={phone!r}")

    if not appt_svc.has_enough_notice(date, time):
        print(f"[PHONE DEBUG] _do_booking REJECTED: not enough notice for date={date!r} time={time!r}")
        return "That time is a bit too soon for us to prepare — appointments need at least two hours notice. Please call back with a later time, or walk in directly!"

    if not appt_svc.is_available(store, date, time):
        print(f"[PHONE DEBUG] _do_booking REJECTED: slot not available store={store!r} date={date!r} time={time!r}")
        return "It looks like that time slot just got taken. Please call back so we can find another time that works for you!"

    appt_id = appt_svc.book(store, date, time, {
        "name": name, "phone": phone, "email": "", "reason": reason,
    })
    store_phone = appt_svc.get_store_phone(store)
    print(f"[PHONE DEBUG] booked appt_id={appt_id}, now sending SMS to {phone!r}")

    sms_result = send_appointment_confirmation_sms(
        name=name, appt_id=appt_id, store=store, date=date, time=time, to_number=phone,
    )
    print(f"[PHONE DEBUG] SMS result: {sms_result!r}")

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
    email_result = send_email(STORE_EMAIL, f"Phone Booking Confirmed - {appt_id}", store_html)
    print(f"[PHONE DEBUG] Email result: {email_result!r}")

    return f"Great news, your appointment is booked! Your confirmation number is {appt_id}, and you'll get a text message shortly."
