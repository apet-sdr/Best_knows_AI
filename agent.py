"""The Brain: an agent loop that writes code, learns from the web, remembers,
repairs its own mistakes, grows new skills, and can propose rewrites of itself.
Pure standard library."""
import difflib, html, ipaddress, json, os, re, socket, subprocess, sys, time
import urllib.parse, urllib.request
from html.parser import HTMLParser

import llm

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SKILLS = os.path.join(ROOT, "skills")
WORK, PENDING, BACKUPS = (os.path.join(DATA, d) for d in ("work", "pending", "backups"))
for _d in (DATA, SKILLS, WORK, PENDING, BACKUPS):
    os.makedirs(_d, exist_ok=True)

CORE = {"agent.py", "llm.py", "server.py", "static/index.html"}
MAX_STEPS = int(os.getenv("BRAIN_MAX_STEPS", "12"))
REFLECT = os.getenv("BRAIN_REFLECT", "1") == "1"

# ───────────────────────── memory ─────────────────────────
MEM_FILE = os.path.join(DATA, "memory.json")


def load_mem():
    try:
        with open(MEM_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_mem(m):
    with open(MEM_FILE, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)


def _words(s):
    return set(re.findall(r"[a-z0-9]{3,}", s.lower()))


def recall(query, k=6):
    q, mem = _words(query), load_mem()
    scored = [(len(q & _words(m["note"])), i, m["note"]) for i, m in enumerate(mem)]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(reverse=True)
    return [s[2] for s in scored[:k]]


def remember(note):
    note = str(note).strip()[:400]
    if not note:
        return "Empty note."
    mem = load_mem()
    if any(m["note"].lower() == note.lower() for m in mem):
        return "Already known."
    mem.append({"note": note, "t": int(time.time())})
    save_mem(mem[-500:])
    return "Saved to memory."


# ───────────────────────── tools ─────────────────────────
def run_python(code, timeout=20):
    path = os.path.join(WORK, "t%d.py" % (time.time() * 1000))
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                           timeout=timeout, cwd=WORK)
        return "exit=%d\nstdout:\n%s\nstderr:\n%s" % (p.returncode, p.stdout[-3000:], p.stderr[-2000:])
    except subprocess.TimeoutExpired:
        return "ERROR: timed out after %ss (infinite loop?)" % timeout
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _http_get(url, data=None, limit=400_000):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0 (BrainAgent)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read(limit)
        m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""))
    return raw.decode(m.group(1) if m else "utf-8", "replace")


def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def web_search(query):
    page = _http_get("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode())
    items = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                       page, re.S)
    out = []
    for href, title, snip in items[:6]:
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        out.append("- %s\n  %s\n  %s" % (_strip(title), href, _strip(snip)))
    return "\n".join(out) or "No results (search may be blocked or rate limited)."


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer") and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip and d.strip():
            self.parts.append(d.strip())


def _public_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (OSError, ValueError):
        return False
    return True


def fetch_url(url):
    if not _public_url(url):
        return "ERROR: only public http(s) URLs are allowed."
    p = _Text()
    p.feed(_http_get(url))
    return " ".join(p.parts)[:7000] or "(page had no readable text)"


_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_RUNNER = ("import json,sys,importlib.util\n"
           "s=importlib.util.spec_from_file_location('skill',sys.argv[1])\n"
           "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
           "print(json.dumps(m.run(**json.loads(sys.argv[2])),default=str))")


def list_skills():
    names = sorted(f[:-3] for f in os.listdir(SKILLS) if f.endswith(".py"))
    return names


def use_skill(name, args=None):
    if not _NAME.match(str(name)) or name not in list_skills():
        return "ERROR: unknown skill. Installed: %s" % list_skills()
    p = subprocess.run([sys.executable, "-c", _RUNNER, os.path.join(SKILLS, name + ".py"), json.dumps(args or {})],
                       capture_output=True, text=True, timeout=30, cwd=WORK)
    return ("OK: " + p.stdout[-3000:]) if p.returncode == 0 else ("ERROR:\n" + p.stderr[-2000:])


def _backup(path, tag):
    if os.path.exists(path):
        dest = os.path.join(BACKUPS, "%s.%d.bak" % (tag, time.time()))
        with open(path, "rb") as a, open(dest, "wb") as b:
            b.write(a.read())


def write_skill(name, code, test):
    """Install a skill only if its self-test passes (otherwise the old version stays)."""
    if not _NAME.match(str(name)):
        return "ERROR: name must be lowercase letters/digits/underscore."
    tmp = os.path.join(WORK, "skilltest_%s.py" % name)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(code + "\n\n# ---- self-test ----\n" + test + "\n")
    try:
        p = subprocess.run([sys.executable, tmp], capture_output=True, text=True, timeout=30, cwd=WORK)
    except subprocess.TimeoutExpired:
        return "TEST FAILED: timed out. Skill NOT installed."
    finally:
        pass
    if p.returncode != 0:
        return "TEST FAILED, skill NOT installed. Fix and retry.\n" + p.stderr[-2000:]
    dest = os.path.join(SKILLS, name + ".py")
    _backup(dest, "skill_" + name)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(code + "\n")
    return "Skill '%s' installed (test passed). Use it with use_skill." % name


def read_own_code(file):
    if file not in CORE:
        return "ERROR: choose one of %s" % sorted(CORE)
    with open(os.path.join(ROOT, file), encoding="utf-8") as f:
        return f.read()[:20000]


def propose_core_patch(file, new_source, reason=""):
    if file not in CORE:
        return "ERROR: choose one of %s" % sorted(CORE)
    if file.endswith(".py"):
        try:
            compile(new_source, file, "exec")
        except SyntaxError as e:
            return "REJECTED: syntax error: %s. Fix and resubmit." % e
    with open(os.path.join(ROOT, file), encoding="utf-8") as f:
        old = f.read()
    diff = "".join(difflib.unified_diff(old.splitlines(1), new_source.splitlines(1), file, file + " (new)"))
    pid = str(int(time.time() * 1000))
    with open(os.path.join(PENDING, pid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": pid, "file": file, "reason": reason, "diff": diff, "new_source": new_source}, f)
    return "Patch %s submitted. It is NOT applied until the human approves it in the Updates panel." % pid


def pending_patches():
    out = []
    for fn in sorted(os.listdir(PENDING)):
        if fn.endswith(".json"):
            with open(os.path.join(PENDING, fn), encoding="utf-8") as f:
                p = json.load(f)
            out.append({k: p[k] for k in ("id", "file", "reason", "diff")})
    return out


def approve_patch(pid):
    path = os.path.join(PENDING, os.path.basename(pid) + ".json")
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    target = os.path.join(ROOT, p["file"])
    _backup(target, p["file"].replace("/", "__"))
    with open(target, "w", encoding="utf-8") as f:
        f.write(p["new_source"])
    os.remove(path)
    return p["file"]


def reject_patch(pid):
    try:
        os.remove(os.path.join(PENDING, os.path.basename(pid) + ".json"))
    except OSError:
        pass


def rollback(file):
    tag = file.replace("/", "__") if file in CORE else "skill_" + file
    baks = sorted(f for f in os.listdir(BACKUPS) if f.startswith(tag + "."))
    if not baks:
        return False
    src = os.path.join(BACKUPS, baks[-1])
    dest = os.path.join(ROOT, file) if file in CORE else os.path.join(SKILLS, file + ".py")
    with open(src, "rb") as a, open(dest, "wb") as b:
        b.write(a.read())
    os.remove(src)
    return True


TOOLS = {
    "run_python": lambda a: run_python(a["code"]),
    "web_search": lambda a: web_search(a["query"]),
    "fetch_url": lambda a: fetch_url(a["url"]),
    "remember": lambda a: remember(a["note"]),
    "list_skills": lambda a: json.dumps(list_skills()),
    "use_skill": lambda a: use_skill(a["name"], a.get("args")),
    "write_skill": lambda a: write_skill(a["name"], a["code"], a["test"]),
    "read_own_code": lambda a: read_own_code(a["file"]),
    "propose_core_patch": lambda a: propose_core_patch(a["file"], a["new_source"], a.get("reason", "")),
}

SYSTEM = """You are Brain, an autonomous agent running on the user's own computer. Today is DATE.
Reply with EXACTLY ONE JSON object per turn and nothing else:
  {"thought":"brief reasoning","tool":"<name>","args":{...}}   to use a tool
  {"final":"your answer to the user"}                          when finished
Tools:
 run_python {code}  Run Python 3 (standard library). Use print(). Use it for ALL math, logic, parsing and data work, and to test code before trusting it.
 web_search {query} Search the web.   fetch_url {url} Read a page's text.
 remember {note}    Save a short, durable fact or lesson (user preferences, corrections, how you fixed an error).
 list_skills {}     use_skill {name,args}   Reuse skills you built earlier.
 write_skill {name,code,test}  Create or improve a reusable skill. code must define run(**kwargs) returning JSON-serializable data. test is Python that asserts run() works; it is appended after code and the skill is installed ONLY if it passes.
 read_own_code {file}  Read your source: agent.py, llm.py, server.py, static/index.html.
 propose_core_patch {file,new_source,reason}  Propose a full replacement of one core file. A human must approve it; never claim it is applied.
Rules:
- If a tool returns an error, read it, fix the cause and retry. Do not give up after one failure.
- Verify before answering: compute with code, and cross-check web facts with a second source when it matters.
- Cite URLs for facts taken from the web. Never invent sources, numbers or quotes. If unsure, say what is uncertain.
- When a task will recur, turn it into a skill. When you learn something reusable, remember it.
- Keep the final answer short and lead with the answer.
Relevant memory:
MEMORY
Installed skills: SKILLS"""


def parse_json(s):
    s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.M).strip()
    i = s.find("{")
    while i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = s.find("{", i + 1)
    return None


def ask(question, history, on_step=lambda s: None):
    mem = recall(question)
    system = (SYSTEM.replace("DATE", time.strftime("%A %B %d, %Y"))
              .replace("MEMORY", "\n".join("- " + m for m in mem) or "(none)")
              .replace("SKILLS", ", ".join(list_skills()) or "(none)"))
    msgs = [{"role": "system", "content": system}] + history[-10:] + [{"role": "user", "content": question}]
    trace, answer, bad = [], None, 0
    for _ in range(MAX_STEPS):
        try:
            raw = llm.chat(msgs)
        except Exception as e:
            answer = "I couldn't reach the language model (%s). Check BRAIN_PROVIDER / that Ollama is running." % e
            break
        obj = parse_json(raw)
        if obj is None:
            bad += 1
            if bad > 3:
                answer = raw.strip()
                break
            msgs += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Reply with ONE valid JSON object only."}]
            continue
        if "final" in obj:
            answer = str(obj["final"])
            break
        tool, args = obj.get("tool"), obj.get("args") or {}
        on_step({"type": "tool", "tool": tool, "thought": obj.get("thought", ""), "args": args})
        try:
            if tool not in TOOLS:
                raise KeyError("unknown tool %r" % tool)
            result = str(TOOLS[tool](args))
        except Exception as e:
            result = "ERROR: %s: %s" % (type(e).__name__, e)
        trace.append("%s -> %s" % (tool, result[:120].replace("\n", " ")))
        on_step({"type": "result", "text": result[:1500]})
        msgs += [{"role": "assistant", "content": raw},
                 {"role": "user", "content": "TOOL RESULT:\n" + result[:6000]}]
    if answer is None:
        answer = "I ran out of steps before finishing. Ask me to continue or narrow the task."
    history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
    if REFLECT and trace:
        _reflect(question, trace, answer, on_step)
    return answer


def _reflect(question, trace, answer, on_step):
    """Self-learning: distil durable lessons from what just happened."""
    prompt = ("Task: %s\nSteps: %s\nAnswer: %s\n\nList up to 2 SHORT durable lessons worth remembering for future "
              "tasks (user preferences, facts the user taught, error patterns and their fixes). Skip trivia. "
              'Reply ONLY with JSON: {"notes":["..."]} (empty list if none).') % (question, " | ".join(trace)[:1500], answer[:500])
    try:
        obj = parse_json(llm.chat([{"role": "user", "content": prompt}])) or {}
        for n in (obj.get("notes") or [])[:2]:
            if remember(n).startswith("Saved"):
                on_step({"type": "learned", "text": str(n)})
    except Exception:
        pass
