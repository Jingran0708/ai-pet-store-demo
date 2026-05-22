"""
config.py - single source of truth for env vars and business constants.
Store locations and policies now live in data/json/ - not here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# API keys
OPENAI_API_KEY:     str   = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL:       str   = "gpt-4o-mini"
OPENAI_MAX_TOKENS:  int   = 600
OPENAI_TEMPERATURE: float = 0.7

# Email
GMAIL_SENDER:       str = os.getenv("GMAIL_SENDER", "").strip()
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "").strip()
STORE_EMAIL:        str = os.getenv("STORE_EMAIL", "").strip()

# Business constants (source of truth is now data/json/policies.json)
# These are kept here as Python-accessible fallbacks used by services.
from data.loader import get_appointment_policy as _ap, get_delivery_policy as _dp

_appt   = _ap()
_prod   = _dp("product")
_pet    = _dp("pet")

APPOINTMENT_ADVANCE_HOURS:        int   = _appt.get("advance_hours", 2)
SLOT_DURATION_MINUTES:            int   = _appt.get("slot_minutes", 30)
STORE_OPEN_HOUR:                  int   = _appt.get("open_hour", 11)
STORE_CLOSE_HOUR:                 int   = _appt.get("close_hour", 20)

HST_RATE:                         float = 0.13
PRODUCT_FREE_DELIVERY_THRESHOLD:  float = float(_prod.get("free_threshold", 60.0))
PRODUCT_DELIVERY_FEE:             float = float(_prod.get("standard_fee", 9.99))
PET_DELIVERY_FEE_GTA:             float = float(_pet.get("gta_fee", 50.0))

# STORES is now loaded from data/json/stores.json via data.loader.stores()
# Import it here so existing code that does `from config import STORES` still works
from data.loader import stores as _stores
STORES = _stores()
