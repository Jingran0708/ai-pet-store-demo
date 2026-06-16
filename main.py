"""
main.py — application entry point.
Registers all routers and mounts static files.
To add a new feature: create a router in routers/ and add one line here.

Run with:  uvicorn main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routers.chat         import router as chat_router
from routers.appointments import router as appointments_router
from routers.orders       import router as orders_router
from routers.contact      import router as contact_router
from routers.sessions     import router as sessions_router
from routers.phone        import router as phone_router

app = FastAPI(title="Happy Paws AI Support Platform", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register all routers ───────────────────────────────────────────────────────
app.include_router(chat_router)
app.include_router(appointments_router)
app.include_router(orders_router)
app.include_router(contact_router)
app.include_router(sessions_router)
app.include_router(phone_router)

# ── Serve frontend ─────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")

# ── Test email ─────────────────────────────────────────────────────────────────
@app.get("/test-email")
def test_email():
    from services.email_service import send
    result = send(
        to_addr="jingran0708@gmail.com",  # 改成你自己的邮箱
        subject="Test Email from Happy Paws",
        html_body="<p>This is a test email from Render. If you see this, email is working!</p>"
    )
    return {"result": result}
