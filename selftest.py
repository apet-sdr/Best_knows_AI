"""Offline test of the Brain's machinery using a scripted fake model (no LLM needed).
Run: python selftest.py"""
import json, os, shutil, sys
os.environ["BRAIN_REFLECT"] = "0"
import agent, llm

script = []
llm.chat = lambda msgs: script.pop(0)
J = json.dumps
steps = []

def run(q):
    h = []
    return agent.ask(q, h, steps.append)

# 1. self-fix: first code has a bug, agent reads the error and retries
script[:] = [
    J({"tool": "run_python", "args": {"code": "print(1/0)"}}),
    J({"tool": "run_python", "args": {"code": "print(sum(range(10)))"}}),
    J({"final": "45"}),
]
assert run("sum") == "45" and "ZeroDivisionError" in steps[1]["text"] and "45" in steps[3]["text"]
print("PASS self-repair loop")

# 2. skill with a failing test is rejected, fixed version installed, then used
bad = "def run(x):\n    return x*3\n"
good = "def run(x):\n    return x*2\n"
test = "assert run(x=4)==8"
script[:] = [J({"tool": "write_skill", "args": {"name": "double", "code": bad, "test": test}}),
             J({"tool": "write_skill", "args": {"name": "double", "code": good, "test": test}}),
             J({"tool": "use_skill", "args": {"name": "double", "args": {"x": 21}}}),
             J({"final": "42"})]
steps.clear(); run("make skill")
assert "FAILED" in steps[1]["text"] and "installed" in steps[3]["text"] and "42" in steps[5]["text"]
print("PASS skill write/test/install/use")

# 3. memory
script[:] = [J({"tool": "remember", "args": {"note": "User prefers metric units"}}), J({"final": "ok"})]
run("prefs"); assert agent.recall("which metric units do I like") == ["User prefers metric units"]
print("PASS memory recall")

# 4. self-rewrite requires approval; bad syntax rejected; rollback works
before = open("llm.py").read()
script[:] = [J({"tool": "propose_core_patch", "args": {"file": "llm.py", "new_source": "def (:", "reason": "x"}}),
             J({"tool": "propose_core_patch", "args": {"file": "llm.py", "new_source": before + "\n# patched\n", "reason": "demo"}}),
             J({"final": "proposed"})]
steps.clear(); run("improve")
assert "REJECTED" in steps[1]["text"] and open("llm.py").read() == before
pid = agent.pending_patches()[0]["id"]
agent.approve_patch(pid); assert open("llm.py").read().endswith("# patched\n")
assert agent.rollback("llm.py") and open("llm.py").read() == before
print("PASS gated self-rewrite + rollback")

# 5. SSRF guard
assert "ERROR" in agent.fetch_url("http://127.0.0.1:8000/") and "ERROR" in agent.fetch_url("file:///etc/passwd")
print("PASS private-URL guard")

# cleanup test artefacts
for f in ("skills/double.py",): os.path.exists(f) and os.remove(f)
open("data/memory.json", "w").write("[]")
print("\nAll tests passed.")
