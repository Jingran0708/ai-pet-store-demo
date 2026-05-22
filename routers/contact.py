"""
routers/contact.py
POST /contact — human escalation / inquiry submission.
"""
from fastapi import APIRouter, Request
from services.email_service import send_inquiry_notification

router = APIRouter()


@router.post("/contact")
async def contact_agent(request: Request):
    body    = await request.json()
    name    = body.get("name", "")
    email   = body.get("email", "")
    phone   = body.get("phone", "")
    inquiry = body.get("inquiry", "")

    status = send_inquiry_notification(name, email, phone, inquiry)
    return {"success": True, "email_status": status}
