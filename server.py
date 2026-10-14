"""Local web server + phone-friendly UI for the Brain.
  python server.py            -> http://localhost:8000 (this computer only)
  python server.py --lan      -> reachable from your phone on the same Wi-Fi (protected by a secret token)
  python server.py --rollback agent.py   -> restore the last backup of a core file or skill
"""
import argparse, json, os, secrets, socket, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent

HISTORY, LOCK, TOKEN = [], threading.Lock(), None
INDEX = os.path.join(agent.ROOT, "static", "index.html")


def restart():
    os.execv(sys.executable, [sys.executable] + sys.argv)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _ok(self):
        if TOKEN is None or self.headers.get("X-Brain-Token") == TOKEN:
            return True
        self._send({"error": "unauthorized"}, 401)
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            with open(INDEX, "rb") as f:
                b = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif not self._ok():
            return
        elif path == "/api/memory":
            self._send(agent.load_mem())
        elif path == "/api/skills":
            self._send(agent.list_skills())
        elif path == "/api/pending":
            self._send(agent.pending_patches())
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        if not self._ok():
            return
        path, d = self.path, self._body()
        if path == "/api/ask":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()

            def emit(o):
                try:
                    self.wfile.write((json.dumps(o) + "\n").encode())
                    self.wfile.flush()
                except OSError:
                    pass
            with LOCK:
                try:
                    emit({"type": "final", "text": agent.ask(d.get("q", ""), HISTORY, emit)})
                except Exception as e:
                    emit({"type": "final", "text": "Internal error: %s" % e})
        elif path == "/api/memory/delete":
            m = agent.load_mem()
            if 0 <= d.get("i", -1) < len(m):
                m.pop(d["i"])
                agent.save_mem(m)
            self._send({"ok": True})
        elif path == "/api/memory/clear":
            agent.save_mem([])
            self._send({"ok": True})
        elif path == "/api/pending/approve":
            f = agent.approve_patch(d["id"])
            self._send({"ok": True, "file": f, "restarting": True})
            threading.Timer(1.0, restart).start()
        elif path == "/api/pending/reject":
            agent.reject_patch(d["id"])
            self._send({"ok": True})
        elif path == "/api/rollback":
            ok = agent.rollback(d["file"])
            self._send({"ok": ok})
            if ok:
                threading.Timer(1.0, restart).start()
        else:
            self._send({"error": "not found"}, 404)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true", help="allow phones on your Wi-Fi (uses a secret token)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--rollback", metavar="NAME")
    a = ap.parse_args()
    if a.rollback:
        print("restored" if agent.rollback(a.rollback) else "no backup found")
        sys.exit()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        tf = os.path.join(agent.DATA, "token.txt")
        TOKEN = open(tf).read().strip() if os.path.exists(tf) else secrets.token_urlsafe(16)
        open(tf, "w").write(TOKEN)
    srv = ThreadingHTTPServer((host, a.port), H)
    url = "http://%s:%d/%s" % (lan_ip() if a.lan else "localhost", a.port, ("?t=" + TOKEN) if TOKEN else "")
    print("\n🧠 Brain running. Open:", url, "\n   Provider:", os.getenv("BRAIN_PROVIDER", "ollama"), "\n")
    srv.serve_forever()
