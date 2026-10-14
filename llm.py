"""Pluggable LLM backends. Pure standard library, no installs.

BRAIN_PROVIDER=ollama     (default, FREE, runs on your computer, works offline)
BRAIN_PROVIDER=openai     (any OpenAI-compatible API: Groq / OpenRouter free tiers, LM Studio, OpenAI...)
BRAIN_PROVIDER=anthropic  (Claude API, paid)
"""
import json, os, urllib.request


def _post(url, body, headers, timeout=300):
    h = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, json.dumps(body).encode(), h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def chat(messages):
    """messages: [{'role': 'system'|'user'|'assistant', 'content': str}] -> reply text"""
    provider = os.getenv("BRAIN_PROVIDER", "ollama").lower()
    model = os.getenv("BRAIN_MODEL", "")

    if provider == "ollama":
        url = os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
        body = {"model": model or "llama3.1", "messages": messages, "stream": False,
                "options": {"temperature": 0.2, "num_ctx": 8192}}
        return _post(url, body, {})["message"]["content"]

    if provider == "openai":
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        key = os.getenv("OPENAI_API_KEY", "")
        body = {"model": model or "gpt-4o-mini", "messages": messages, "temperature": 0.2}
        r = _post(base + "/chat/completions", body, {"Authorization": "Bearer " + key})
        return r["choices"][0]["message"]["content"]

    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [m for m in messages if m["role"] != "system"]
        body = {"model": model or "claude-sonnet-5-5", "max_tokens": 4096, "system": system, "messages": msgs}
        r = _post("https://api.anthropic.com/v1/messages", body,
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r["content"])

    raise RuntimeError("Unknown BRAIN_PROVIDER: " + provider)
