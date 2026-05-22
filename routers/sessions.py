"""
routers/sessions.py
POST /session/start        — create a new session, return session_id + initial state
GET  /session/{session_id} — inspect a session (useful for staff dashboard / debugging)
GET  /session/{session_id}/summary — clean summary for staff handoff
"""
from fastapi import APIRouter, Request, HTTPException
from services.session_service import create_session, get_session, get_session_summary

router = APIRouter(prefix="/session")


@router.post("/start")
async def start_session(request: Request):
    body = await request.json()
    flow = body.get("flow", "buy-pet")
    state = create_session(flow)
    return {
        "session_id": state["session_id"],
        "flow":       state["flow"],
        "stage":      state["stage"],
    }


@router.get("/{session_id}")
async def get_session_state(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return state


@router.get("/{session_id}/summary")
async def session_summary(session_id: str):
    summary = get_session_summary(session_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return summary
