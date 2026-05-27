"""
services/email_service.py
All email sending logic lives here.
Now using Resend API instead of Gmail SMTP.
"""
import os
import urllib.request
import urllib.error
import json
from config import STORE_EMAIL

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
SENDER = "Happy Paws Pet Store <onboarding@resend.dev>"

BRAND_PURPLE = "#534AB7"
BRAND_LIGHT  = "#EEEDFE"
BRAND_BG     = "#f7f6f2"


def _base_layout(header_title: str, header_sub: str, body_html: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a">
      <div style="background:{BRAND_PURPLE};padding:24px 32px;border-radius:12px 12px 0 0">
        <h1 style="color:white;margin:0;font-size:22px">Happy Paws Pet Store</h1>
        <p style="color:{BRAND_LIGHT};margin:6px 0 0">{header_sub}</p>
      </div>
      <div style="background:{BRAND_BG};padding:32px;border-radius:0 0 12px 12px">
        {body_html}
        <p style="font-size:14px">With love,<br><strong>The Happy Paws Team</strong></p>
      </div>
    </div>"""


def send(to_addr: str, subject: str, html_body: str) -> str:
    """Send an HTML email via Resend API."""
    try:
        recipients = list({to_addr, STORE_EMAIL} - {""})
        for recipient in recipients:
            payload = json.dumps({
                "from": SENDER,
                "to": [recipient],
                "subject": subject,
                "html": html_body,
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=payload,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                resp.read()
        return "sent"
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return f"failed: HTTP {e.code} - {body}"
    except Exception as exc:
        return f"failed: {exc}"


def send_appointment_confirmation(
    name: str, email: str, phone: str,
    appt_id: str, store: str, store_phone: str,
    date: str, time: str, reason: str,
) -> str:
    body = f"""
    <p>Hi <strong>{name}</strong>, your appointment is confirmed!</p>
    <div style="background:white;border-radius:10px;padding:20px;margin:20px 0;border:1px solid #e0dff8">
      <table style="width:100%;font-size:14px">
        <tr><td style="color:#6b6b6b;padding:4px 0">Appointment ID</td><td style="text-align:right"><strong>{appt_id}</strong></td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Store</td><td style="text-align:right">{store}</td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Store Phone</td><td style="text-align:right">{store_phone}</td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Date</td><td style="text-align:right">{date}</td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Time</td><td style="text-align:right">{time}</td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Reason</td><td style="text-align:right">{reason}</td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Your Phone</td><td style="text-align:right">{phone}</td></tr>
      </table>
    </div>
    <p style="font-size:14px;color:#6b6b6b">Please arrive 5 minutes early. To reschedule, contact us at least 2 hours before your appointment.</p>"""
    html = _base_layout("Appointment", "Appointment Confirmation", body)
    return send(email, f"Appointment Confirmed - {appt_id}", html)


def send_order_confirmation(
    name: str, email: str, order_id: str,
    item: str, item_price: float, addons: list,
    tax: float, delivery_fee: float, total: float,
    card_last4: str, method: str,
    address: str = "", pickup_store: str = "", pickup_time: str = "",
    delivery_time: str = "", delivery_from_store: str = "", store_phone: str = "",
    is_pet: bool = False,
) -> str:
    addons_rows = "".join(
        f"<tr><td style='color:#6b6b6b;padding:4px 0'>{a.get('name','')}</td>"
        f"<td style='text-align:right'>${a.get('price','')}</td></tr>"
        for a in addons
    )
    delivery_fee_str = "FREE" if delivery_fee == 0 else f"${delivery_fee:.2f}"

    if method == "pickup":
        delivery_row = (
            f"<tr><td style='color:#6b6b6b;padding:4px 0'>Pickup Store</td><td style='text-align:right'>{pickup_store}</td></tr>"
            f"<tr><td style='color:#6b6b6b;padding:4px 0'>Pickup Time</td><td style='text-align:right'>{pickup_time}</td></tr>"
        )
    else:
        delivery_row = f"<tr><td style='color:#6b6b6b;padding:4px 0'>Delivery Address</td><td style='text-align:right'>{address}</td></tr>"
        if delivery_time:
            delivery_row += f"<tr><td style='color:#6b6b6b;padding:4px 0'>Delivery Time</td><td style='text-align:right'>{delivery_time}</td></tr>"

    pet_note = ""
    if is_pet and method == "delivery":
        pet_note = (
            f"<div style='background:#e8f4fd;border-radius:8px;padding:12px;margin:16px 0;font-size:13px;color:#1a6fa8'>"
            f"<strong>Delivery Info:</strong> Your pet will be delivered from our <strong>{delivery_from_store}</strong> branch. "
            f"Our delivery staff will call you when they are on the way. "
            f"For questions, contact the store at <strong>{store_phone}</strong>.</div>"
        )

    body = f"""
    <p>Hi <strong>{name}</strong>, thank you for your order!</p>
    {pet_note}
    <div style="background:white;border-radius:10px;padding:20px;margin:20px 0;border:1px solid #e0dff8">
      <table style="width:100%;font-size:14px">
        <tr><td style="color:#6b6b6b;padding:4px 0">Order ID</td><td style="text-align:right"><strong>{order_id}</strong></td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">{item}</td><td style="text-align:right">${item_price:.2f}</td></tr>
        {addons_rows}
        <tr><td style="color:#6b6b6b;padding:4px 0">Tax (HST 13%)</td><td style="text-align:right">${tax:.2f}</td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Delivery Fee</td><td style="text-align:right">{delivery_fee_str}</td></tr>
        <tr style="border-top:1px solid #e0dff8"><td style="padding:8px 0;font-weight:700">Total</td><td style="text-align:right;font-weight:700;font-size:16px">${total:.2f}</td></tr>
        <tr><td style="color:#6b6b6b;padding:4px 0">Card</td><td style="text-align:right">**** **** **** {card_last4}</td></tr>
        {delivery_row}
      </table>
    </div>
    <p style="font-size:14px;color:#6b6b6b">Thank you for shopping with Happy Paws!</p>"""
    html = _base_layout("Order", "Order Confirmation", body)
    return send(email, f"Order Confirmed - {order_id}", html)


def send_inquiry_notification(name: str, email: str, phone: str, inquiry: str) -> str:
    body = f"""
    <h2 style="color:{BRAND_PURPLE}">New Customer Inquiry</h2>
    <table style="font-size:14px;width:100%">
      <tr><td style="color:#6b6b6b;padding:6px 0;width:120px">Name</td><td>{name}</td></tr>
      <tr><td style="color:#6b6b6b;padding:6b 0">Email</td><td>{email}</td></tr>
      <tr><td style="color:#6b6b6b;padding:6px 0">Phone</td><td>{phone}</td></tr>
      <tr><td style="color:#6b6b6b;padding:6px 0;vertical-align:top">Inquiry</td><td>{inquiry}</td></tr>
    </table>"""
    return send(email, "We received your inquiry - Happy Paws", body)
