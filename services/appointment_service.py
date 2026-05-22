"""
services/appointment_service.py
All appointment slot logic in one place.
To replace with a real database, swap the BOOKED_SLOTS dict for DB calls here.
"""
from datetime import datetime, timedelta
from config import APPOINTMENT_ADVANCE_HOURS, STORES

# In-memory store: "StoreName|YYYY-MM-DD|HH:MM AM/PM" → booking dict
BOOKED_SLOTS: dict = {}


def _slot_key(store: str, date: str, time: str) -> str:
    store_short = store.split(" - ")[0].strip()
    return f"{store_short}|{date}|{time}"


def is_available(store: str, date: str, time: str) -> bool:
    return _slot_key(store, date, time) not in BOOKED_SLOTS


def has_enough_notice(date_str: str, time_str: str) -> bool:
    """Return True if the slot is at least APPOINTMENT_ADVANCE_HOURS from now."""
    try:
        slot_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        return slot_dt >= datetime.now() + timedelta(hours=APPOINTMENT_ADVANCE_HOURS)
    except Exception:
        return True  # unparseable → let it through, server-side guard only


def book(store: str, date: str, time: str, payload: dict) -> str:
    """Reserve a slot and return the appointment ID."""
    import hashlib
    raw = (payload.get("email", "") + date + time).encode()
    appt_id = "APT" + str(abs(int(hashlib.md5(raw).hexdigest(), 16)) % 100000).zfill(5)
    BOOKED_SLOTS[_slot_key(store, date, time)] = {**payload, "appt_id": appt_id}
    return appt_id


def get_store_phone(store_name: str) -> str:
    store_short = store_name.split(" - ")[0].strip()
    for s in STORES:
        if s["name"] == store_short or s["name"] in store_name:
            return s["phone"]
    return "416-555-0101"
