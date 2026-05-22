"""
routers/appointments.py
POST /check-slot  — check if a slot is free and has enough notice
POST /appointment — book a slot and send confirmation email
"""
from fastapi import APIRouter, Request
from services import appointment_service as appt_svc
from services.email_service import send_appointment_confirmation

router = APIRouter()


@router.post("/check-slot")
async def check_slot(request: Request):
    body  = await request.json()
    store = body.get("store", "")
    date  = body.get("date", "")
    time  = body.get("time", "")
    return {
        "available":     appt_svc.is_available(store, date, time),
        "enough_notice": appt_svc.has_enough_notice(date, time),
    }


@router.post("/appointment")
async def book_appointment(request: Request):
    body   = await request.json()
    name   = body.get("name", "")
    phone  = body.get("phone", "")
    email  = body.get("email", "")
    store  = body.get("store", "")
    date   = body.get("date", "")
    time   = body.get("time", "")
    reason = body.get("reason", "")

    if not appt_svc.has_enough_notice(date, time):
        return {"success": False, "error": "Appointments must be booked at least 2 hours in advance. Please walk in or choose a later time."}
    if not appt_svc.is_available(store, date, time):
        return {"success": False, "error": "That time slot is already taken. Please choose a different time."}

    appt_id     = appt_svc.book(store, date, time, {"name": name, "phone": phone, "email": email, "reason": reason})
    store_short = store.split(" - ")[0].strip()
    store_phone = appt_svc.get_store_phone(store)

    email_status = send_appointment_confirmation(
        name=name, email=email, phone=phone,
        appt_id=appt_id, store=store_short, store_phone=store_phone,
        date=date, time=time, reason=reason,
    )
    return {"success": True, "appt_id": appt_id, "email_status": email_status}
