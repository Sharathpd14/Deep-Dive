from config import settings
from llm import chat, chat_json

print(chat([{"role": "user", "content": "Say hello in five words."}], settings.cheap_model))
print(chat_json(
    [{"role": "user", "content": 'Return JSON: {"ok": true, "n": 3}'}],
    settings.cheap_model,
))