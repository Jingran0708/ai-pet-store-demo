"""
services/sms_service.py
Sends SMS confirmations via Twilio.
Used by the phone agent to confirm appointments to the customer by text
(email confirmations for phone bookings go to the store, not the customer).
"""
import os
from twilio.rest import Client

TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "").strip()


def send_sms(to_number: str, body: str) -> str:
    """Send a plain-text SMS. Returns 'sent' or 'failed: <reason>'."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        return "failed: Twilio credentials not configured"
    if not to_number:
        return "failed: no recipient phone number"
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            body=body,
        )
        return "sent"
    except Exception as exc:
        return f"failed: {exc}"


def send_appointment_confirmation_sms(
    name: str, appt_id: str, store: str, date: str, time: str, to_number: str,
) -> str:
    body = (
        f"Happy Paws Pets: Hi {name}! Your appointment is confirmed at {store} "
        f"on {date} at {time}. Confirmation #: {appt_id}. "
        f"Please arrive 5 minutes early. See you soon!"
    )
    return send_sms(to_number, body)
