"""
services/ai_service.py
All OpenAI communication lives here.
To swap to a different LLM (Gemini, Claude, local model), only change this file.
"""
import httpx
from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_MAX_TOKENS, OPENAI_TEMPERATURE


async def chat(system_prompt: str, messages: list, json_mode: bool = False) -> str:
    """
    Send a conversation to OpenAI and return the assistant reply text.
    If json_mode=True, forces the API to only return a valid JSON object
    (the system_prompt MUST instruct the model to produce JSON, or OpenAI rejects the request).
    Raises RuntimeError on API or network failure.
    """
    api_key = OPENAI_API_KEY.encode("ascii", errors="ignore").decode("ascii")
    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": OPENAI_MAX_TOKENS,
        "temperature": OPENAI_TEMPERATURE,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "OpenAI API error"))
    return data["choices"][0]["message"]["content"]
