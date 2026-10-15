import json
import re
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from config import settings

client = OpenAI(base_url=settings.mesh_base_url, api_key=settings.mesh_api_key)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def chat(messages: list[dict], model: str, temperature: float = 0.2) -> str:
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature
    )
    return resp.choices[0].message.content or ""

def chat_json(messages: list[dict], model: str, temperature: float = 0.0) -> dict:
    """Ask for JSON, strip code fences, parse. Retries once on bad JSON."""
    msgs = list(messages)
    for attempt in range(2):
        raw = chat(msgs, model, temperature)
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            msgs = msgs + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "That was not valid JSON. Return only valid JSON."},
            ]
    raise ValueError("Model did not return valid JSON")