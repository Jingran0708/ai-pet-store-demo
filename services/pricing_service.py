"""
services/pricing_service.py
Single place for all price calculations.
Tax rate, delivery thresholds, and fee amounts come from config.py.
"""
from config import (
    HST_RATE,
    PRODUCT_FREE_DELIVERY_THRESHOLD,
    PRODUCT_DELIVERY_FEE,
    PET_DELIVERY_FEE_GTA,
)


def calculate_order(
    item_price: float,
    addons: list,        # [{"name": str, "price": float}]
    method: str,         # "delivery" | "pickup"
    is_pet: bool = False,
    is_gta: bool = True,
) -> dict:
    """
    Returns a dict with subtotal, tax, delivery_fee, and total.
    All values are rounded to 2 decimal places.
    """
    addons_total = sum(float(a.get("price", 0) or 0) for a in (addons or []))
    subtotal     = round(item_price + addons_total, 2)
    tax          = round(subtotal * HST_RATE, 2)

    if method == "delivery":
        if is_pet:
            fee = PET_DELIVERY_FEE_GTA if is_gta else round(subtotal * 0.08 + 30, 2)
        else:
            fee = 0.0 if subtotal >= PRODUCT_FREE_DELIVERY_THRESHOLD else PRODUCT_DELIVERY_FEE
    else:
        fee = 0.0

    total = round(subtotal + tax + fee, 2)
    return {"subtotal": subtotal, "tax": tax, "delivery_fee": fee, "total": total}


def make_order_id(email: str, item: str) -> str:
    return "HP" + str(abs(hash(email + item)) % 100000).zfill(5)
