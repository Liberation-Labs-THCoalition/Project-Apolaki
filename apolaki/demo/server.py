"""The hosted demo: one brief in, one draft out, nothing kept.

    ANTHROPIC_API_KEY=... python -m apolaki.demo           # then open http://127.0.0.1:8080

What it does: a visitor types a short brief; Apolaki drafts the copy through its real engine (brief screen, ethics check,
style lint, ethics check on the draft). What it does not do: send, post, queue for a person, or remember anything.
Each request runs alone, in a fresh temporary APOLAKI_HOME that is deleted afterwards, with an empty read-only memory.

Settings (environment): PORT (8080), HOST (127.0.0.1; set 0.0.0.0 only behind a host that terminates TLS),
APOLAKI_DEMO_TRUST_PROXY=1 (use the last X-Forwarded-For entry as the visitor's address), APOLAKI_DEMO_OFF=1 or a file at
$APOLAKI_DEMO_STOP_FILE (switch the demo off), and the limits in apolaki.demo.limits. The model is APOLAKI_MODEL
(default claude-haiku-5-5). The key is read from ANTHROPIC_API_KEY only.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .limits import Limits, Settings

TYPES = {"social": 500, "email": 700, "blog_post": 1000}      # content type -> the most tokens a draft may use
BRAND = Path(__file__).resolve().parent.parent.parent / "brand.example.json"
RUN_LOCK = threading.Lock()                                    # one draft at a time: the home folder is process-wide

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Apolaki demo</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#5c5b55;--card:#fff;--line:#d9d6cc;--acc:#8a3b12;--warn:#fff3e0}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#eceae3;--mut:#a8a69c;--card:#1f1f1c;--line:#3a3935;--acc:#e8a070;--warn:#2a2217}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:42rem;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.4rem;margin:.2rem 0}
.note{background:var(--warn);border:1px solid var(--line);border-radius:8px;padding:.75rem 1rem;margin:1rem 0}
label{display:block;margin:1rem 0 .25rem;font-weight:600}
textarea,select,button{font:inherit;width:100%;box-sizing:border-box;padding:.6rem;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
textarea{min-height:7rem}
button{background:var(--acc);color:#fff;border:0;margin-top:1rem;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:1rem;margin-top:1.25rem}
pre{white-space:pre-wrap;word-wrap:break-word;font:inherit;margin:0}
.mut{color:var(--mut);font-size:.9rem}
</style></head><body><main>
<h1>Apolaki, a drafting demo</h1>
<p class="mut">Apolaki drafts copy for community organizations, in an organization's own voice. This demo uses a made-up one, "Example Collective".</p>
<div class="note"><strong>An AI wrote what you see here.</strong> Nothing is sent or published. In real use, a person reviews and approves every piece before it goes anywhere. Your brief goes to Anthropic's API to be answered; this demo does not keep it, and deletes its working files after each request. Please don't type anything private.</div>
<label for="brief">What should it write? (a sentence or two)</label>
<textarea id="brief" maxlength="600" placeholder="Announce our free seed swap this Saturday at the library, 10am to 1pm."></textarea>
<label for="type">Kind of piece</label>
<select id="type"><option value="social">Social post</option><option value="email">Email</option><option value="blog_post">Short blog post</option></select>
<button id="go">Draft it</button>
<div id="out" class="card" hidden aria-live="polite"><pre id="draft"></pre><p id="meta" class="mut"></p></div>
<p class="mut">Limits: a few drafts per hour per visitor, and a daily cap for the whole demo. Licensed Hippocratic License 3.0 with Liberation Labs' modules.</p>
</main>
<script>
const $=id=>document.getElementById(id);
$("go").onclick=async()=>{
  const b=$("go"); b.disabled=true; b.textContent="Drafting...";
  $("out").hidden=false; $("draft").textContent=""; $("meta").textContent="";
  try{
    const r=await fetch("/draft",{method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({brief:$("brief").value,type:$("type").value})});
    const d=await r.json();
    if(!r.ok){$("draft").textContent=d.error||"Something went wrong.";}
    else{$("draft").textContent=d.draft||"(no draft)"; $("meta").textContent=d.note;}
  }catch(e){$("draft").textContent="The demo could not be reached.";}
  b.disabled=false; b.textContent="Draft it";
};
</script></body></html>"""


def run_draft(brief: str, content_type: str, max_tokens: int) -> dict:
    """One draft through the real engine, in a fresh temporary home that is always deleted. Returns the engine's dict."""
    home = tempfile.mkdtemp(prefix="apolaki-demo-")
    saved = {k: os.environ.get(k) for k in ("APOLAKI_HOME", "APOLAKI_BACKEND", "APOLAKI_BRAND_FILE")}
    try:
        os.environ.update(APOLAKI_HOME=home, APOLAKI_BACKEND="anthropic")
        os.environ.pop("APOLAKI_BRAND_FILE", None)
        shutil.copy(BRAND, Path(home) / "brand.json")
        from ..agent.agent import ApolakiAgent
        from ..engine import ApolakiEngine
        from .memory import EmptyMemory
        engine = ApolakiEngine(agent=ApolakiAgent(memory=EmptyMemory()))
        return engine.draft(brief, content_type=content_type, max_tokens=max_tokens)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(home, ignore_errors=True)


def tokens_billed() -> tuple[int, int]:
    from ..agent import agent
    return agent.USAGE["input_tokens"], agent.USAGE["output_tokens"]


class Demo:
    def __init__(self, settings: Settings | None = None, runner=run_draft, meter=tokens_billed, clock=None) -> None:
        self.settings = settings or Settings.from_env()
        self.limits = Limits(self.settings) if clock is None else Limits(self.settings, clock=clock)
        self.runner, self.meter = runner, meter

    def is_off(self) -> bool:
        stop = os.environ.get("APOLAKI_DEMO_STOP_FILE")
        return os.environ.get("APOLAKI_DEMO_OFF") == "1" or bool(stop and Path(stop).exists())

    def draft(self, ip: str, body: object) -> tuple[int, dict]:
        """(HTTP status, JSON body). Validates, checks every limit before any model call, runs one draft, meters it."""
        if self.is_off():
            return 503, {"error": "The demo is switched off for now."}
        if not isinstance(body, dict) or not isinstance(body.get("brief"), str) or body.get("type") not in TYPES:
            return 400, {"error": "Send a brief and one of: " + ", ".join(TYPES) + "."}
        brief, content_type = " ".join(body["brief"].split()), body["type"]
        s = self.settings
        if not s.brief_min <= len(brief) <= s.brief_max:
            return 400, {"error": f"Please write between {s.brief_min} and {s.brief_max} characters."}
        max_tokens = TYPES[content_type]
        why = self.limits.check(ip, max_tokens)
        if why:
            return 429, {"error": why}
        with RUN_LOCK:
            before = self.meter()
            try:
                result = self.runner(brief, content_type, max_tokens)
            except Exception:
                after = self.meter()
                used_in = max(after[0] - before[0], s.worst_case_input_tokens)   # unknown: charge the worst case
                self.limits.record(ip, used_in, max(after[1] - before[1], max_tokens))
                return 502, {"error": "The draft could not be made. Please try again later."}
            after = self.meter()
        self.limits.record(ip, after[0] - before[0], after[1] - before[1])
        return 200, shape(result)


def shape(result: dict) -> dict:
    """What a visitor is shown: the draft, and what the engine's checks said, in a sentence."""
    ethics = result.get("ethics") or {}
    verdict = ethics.get("verdict")
    if result.get("status") == "blocked" or verdict == "blocked":
        return {"draft": "", "note": "Apolaki declined this brief: " + str(ethics.get("reason") or "it failed an ethics check")[:300]}
    note = ("Drafted by an AI model. Not sent or published. Check every detail: a draft can add things your brief did not "
            "say, and in real use a person reviews it first.")
    if verdict and verdict != "approved":
        note += f" The ethics check said: {verdict}" + (f" ({str(ethics.get('reason'))[:200]})" if ethics.get("reason") else "") + "."
    return {"draft": str(result.get("draft") or ""), "note": note}


def make_handler(demo: Demo, trust_proxy: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ApolakiDemo"

        def log_message(self, fmt, *args):      # no access log: nothing about a visitor or a brief is written down
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _ip(self) -> str:
            if trust_proxy:
                forwarded = self.headers.get("X-Forwarded-For", "")
                if forwarded.strip():
                    return forwarded.split(",")[-1].strip()
            return self.client_address[0]

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/health":
                self._send(200, b"ok", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/draft":
                return self._send(404, b"not found", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 < length <= 4096:
                return self._send(413, b'{"error": "That request is too large."}', "application/json")
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body = None
            status, payload = demo.draft(self._ip(), body)
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    return Handler


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; the demo needs it in the environment.")
        return 2
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8080"))
    demo = Demo()
    server = ThreadingHTTPServer((host, port), make_handler(demo, os.environ.get("APOLAKI_DEMO_TRUST_PROXY") == "1"))
    s = demo.settings
    print(f"Apolaki demo on http://{host}:{port}  (daily cap {s.max_drafts_per_day} drafts / ${s.max_usd_per_day:.2f} estimated, "
          f"{s.per_ip_per_hour} per visitor per hour)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
