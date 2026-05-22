"""
services/ai_service.py
All OpenAI communication lives here.
To swap to a different LLM (Gemini, Claude, local model), only change this file.
"""
import httpx
from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_MAX_TOKENS, OPENAI_TEMPERATURE


async def chat(system_prompt: str, messages: list) -> str:
    """
    Send a conversation to OpenAI and return the assistant reply text.
    Raises RuntimeError on API or network failure.
    """
    api_key = OPENAI_API_KEY.encode("ascii", errors="ignore").decode("ascii")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": [{"role": "system", "content": system_prompt}] + messages,
                "max_tokens": OPENAI_MAX_TOKENS,
                "temperature": OPENAI_TEMPERATURE,
            },
        )
    data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "OpenAI API error"))
    return data["choices"][0]["message"]["content"]
