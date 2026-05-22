"""
routers/orders.py
POST /order — calculate pricing and send order confirmation email.
"""
from fastapi import APIRouter, Request
from services.pricing_service import calculate_order, make_order_id
from services.email_service import send_order_confirmation
from config import STORES

router = APIRouter()


def _store_phone(store_name: str) -> str:
    for s in STORES:
        if s["name"] in store_name:
            return s["phone"]
    return "416-555-0101"


@router.post("/order")
async def place_order(request: Request):
    body = await request.json()

    name                = body.get("name", "")
    phone               = body.get("phone", "")
    email               = body.get("email", "")
    item                = body.get("item", "")
    item_price          = float(body.get("item_price", 0) or 0)
    addons              = body.get("addons", [])
    method              = body.get("method", "delivery")
    address             = body.get("address", "")
    pickup_store        = body.get("pickup_store", "")
    pickup_time         = body.get("pickup_time", "")
    delivery_time       = body.get("delivery_time", "")
    delivery_from_store = body.get("delivery_from_store", "Happy Paws Downtown")
    card_last4          = body.get("card_last4", "****")
    is_pet              = body.get("is_pet", False)
    is_gta              = body.get("is_gta", True)

    pricing  = calculate_order(item_price, addons, method, is_pet, is_gta)
    order_id = make_order_id(email, item)

    email_status = send_order_confirmation(
        name=name, email=email, order_id=order_id,
        item=item, item_price=item_price, addons=addons,
        tax=pricing["tax"], delivery_fee=pricing["delivery_fee"], total=pricing["total"],
        card_last4=card_last4, method=method,
        address=address, pickup_store=pickup_store, pickup_time=pickup_time,
        delivery_time=delivery_time, delivery_from_store=delivery_from_store,
        store_phone=_store_phone(delivery_from_store), is_pet=is_pet,
    )

    return {
        "success":      True,
        "order_id":     order_id,
        "total":        pricing["total"],
        "tax":          pricing["tax"],
        "delivery_fee": pricing["delivery_fee"],
        "email_status": email_status,
    }
