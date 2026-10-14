# 🧠 Brain: a free, self-improving AI agent (Python + JavaScript)

Writes and runs its own code, fixes its own errors, searches the web, remembers what it learns,
builds reusable skills, and can propose rewrites of its own source (you approve them).
Python standard library only. Nothing to `pip install`.

## 1. Pick a brain (the model)
**Free and private (recommended): Ollama**
1. Install from https://ollama.com
2. `ollama pull llama3.1`   (or a stronger one that fits your PC, e.g. `qwen2.5:14b`)
3. Leave Ollama running.
   If you used another model, set `BRAIN_MODEL=thatname`.

**Free cloud tiers:** any OpenAI-compatible service (Groq, OpenRouter free models...):
    export BRAIN_PROVIDER=openai
    export OPENAI_BASE_URL=https://api.groq.com/openai/v1
    export OPENAI_API_KEY=your_key
    export BRAIN_MODEL=llama-3.3-70b-versatile

**Claude (paid, smartest):**
    export BRAIN_PROVIDER=anthropic ANTHROPIC_API_KEY=your_key

## 2. Run
    python server.py            # open http://localhost:8000
    python server.py --lan      # also use it from your phone on the same Wi-Fi (prints a secret link)
    python selftest.py          # verifies the machinery without any model

## How it learns and improves
- Writes code: `run_python` executes it; errors go back to the model, which fixes and retries.
- Learns from the Internet: `web_search` (DuckDuckGo) + `fetch_url`; cites URLs.
- Remembers: after each task it distils lessons into `data/memory.json` (view/delete in the app).
- Grows skills: `write_skill` saves reusable code to `skills/`, installed only if its own test passes.
- Rewrites itself: `propose_core_patch` shows a diff in **Updates**; you approve; it backs up and restarts.
- Recovers: `python server.py --rollback agent.py` (or any skill name) restores the last backup.

## Honest limits
- "Smart" = the model you plug in. A small local model is far weaker than Claude.
- Not always correct. It verifies with code and sources, but check important answers.
- Self-rewriting is gated on purpose: core files change only after you approve a diff.
- Not a security sandbox. Code it writes runs as you (20 s timeout). Use a VM/container if you
  don't trust the model, and never expose --lan on public networks.
