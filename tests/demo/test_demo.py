"""The hosted demo's guards and its Anthropic backend, with no network: every model call is a fake."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from apolaki.agent import agent as A
from apolaki.agent import config
from apolaki.demo import server as S
from apolaki.demo.limits import Limits, Settings
from apolaki.demo.memory import EmptyMemory

BRIEF = {"brief": "Announce our free seed swap this Saturday at the library.", "type": "social"}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(settings=None, runner=None, clock=None):
    usage = [(0, 0)]
    calls = []

    def fake_runner(brief, content_type, max_tokens):
        calls.append((brief, content_type, max_tokens))
        usage.append((usage[-1][0] + 1000, usage[-1][1] + 200))
        return {"draft": "A draft.", "status": "queued", "ethics": {"verdict": "approved"}}

    demo = S.Demo(settings or Settings(), runner or fake_runner, meter=lambda: usage[-1], clock=clock)
    return demo, calls


def test_a_good_brief_gets_a_draft_and_is_metered():
    demo, calls = make()
    status, body = demo.draft("1.1.1.1", BRIEF)
    assert status == 200 and body["draft"] == "A draft." and "Not sent or published" in body["note"]
    assert calls == [(BRIEF["brief"], "social", 500)]
    assert demo.limits.drafts_today == 1 and demo.limits.usd_today == pytest.approx(0.003 + 0.003)


@pytest.mark.parametrize("body", [None, [], {}, {"brief": 5, "type": "social"}, {"brief": "x" * 20, "type": "../x"},
                                  {"brief": "x" * 20}, {"brief": "short", "type": "social"},
                                  {"brief": "x" * 601, "type": "social"}])
def test_bad_requests_are_refused_before_any_model_call(body):
    demo, calls = make()
    assert demo.draft("1.1.1.1", body)[0] == 400 and calls == []


def test_the_per_visitor_hourly_limit_and_its_reset():
    clock = Clock()
    demo, calls = make(Settings(per_ip_per_hour=2), clock=clock)
    assert [demo.draft("a", BRIEF)[0] for _ in range(3)] == [200, 200, 429]
    assert demo.draft("b", BRIEF)[0] == 200                 # another visitor is not affected
    clock.t += 3601
    assert demo.draft("a", BRIEF)[0] == 200
    assert len(calls) == 4


def test_the_daily_draft_cap_and_the_midnight_reset():
    clock = Clock()
    demo, calls = make(Settings(max_drafts_per_day=2, per_ip_per_hour=99), clock=clock)
    assert [demo.draft(str(i), BRIEF)[0] for i in range(3)] == [200, 200, 429]
    clock.t += 86400
    assert demo.draft("z", BRIEF)[0] == 200


def test_the_spending_cap_is_checked_against_the_worst_case_before_the_call():
    s = Settings(max_usd_per_day=0.05, per_ip_per_hour=99)
    assert s.worst_case(500) == pytest.approx(2 * (6000 / 1e6 * 3 + 500 / 1e6 * 15))     # 0.051 > 0.05
    demo, calls = make(s)
    status, body = demo.draft("a", BRIEF)
    assert status == 429 and "spending limit" in body["error"] and calls == []


def test_a_failed_draft_is_still_charged_the_worst_case():
    def boom(*a):
        raise RuntimeError("secret detail that must not reach the visitor")
    demo, _ = make(runner=boom)
    status, body = demo.draft("a", BRIEF)
    assert status == 502 and "secret" not in json.dumps(body)
    assert demo.limits.drafts_today == 1 and demo.limits.usd_today >= demo.settings.worst_case(500) / 2


def test_the_off_switch(monkeypatch, tmp_path):
    demo, calls = make()
    monkeypatch.setenv("APOLAKI_DEMO_OFF", "1")
    assert demo.draft("a", BRIEF)[0] == 503
    monkeypatch.delenv("APOLAKI_DEMO_OFF")
    stop = tmp_path / "STOP"
    monkeypatch.setenv("APOLAKI_DEMO_STOP_FILE", str(stop))
    assert demo.draft("a", BRIEF)[0] == 200
    stop.write_text("x")
    assert demo.draft("a", BRIEF)[0] == 503 and len(calls) == 1


def test_a_blocked_brief_shows_the_reason_and_no_draft():
    shown = S.shape({"draft": "", "status": "blocked",
                     "ethics": {"verdict": "blocked", "reason": "makes a false claim"}})
    assert shown["draft"] == "" and "declined" in shown["note"] and "false claim" in shown["note"]


def test_the_empty_memory_reads_nothing_and_refuses_to_keep_anything():
    m = EmptyMemory()
    assert m.list_by_type(["brand"]) == [] and m.search("x") == []
    with pytest.raises(RuntimeError):
        m.store("a visitor's text")


def serve(demo, trust_proxy=False):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(demo, trust_proxy))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def post(url, data, headers=None):
    req = urllib.request.Request(url + "/draft", data=data,
                                 headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_over_http_the_page_the_draft_and_the_size_limit():
    demo, _ = make()
    httpd, url = serve(demo)
    try:
        with urllib.request.urlopen(url + "/") as r:
            page = r.read().decode()
            assert "An AI wrote what you see here" in page and "textContent" in page and "innerHTML" not in page
            assert "default-src 'none'" in r.headers["Content-Security-Policy"]
        assert post(url, json.dumps(BRIEF).encode())[0] == 200
        assert post(url, b"x" * 5000)[0] == 413
        assert post(url, b"not json")[0] == 400
        assert urllib.request.urlopen(url + "/health").read() == b"ok"
    finally:
        httpd.shutdown()


def test_the_visitor_address_comes_from_the_proxy_only_when_trusted():
    seen = []
    demo, _ = make(Settings(per_ip_per_hour=99))
    real = demo.draft
    demo.draft = lambda ip, body: (seen.append(ip), real(ip, body))[1]
    for trust, expect in ((False, "127.0.0.1"), (True, "9.9.9.9")):
        httpd, url = serve(demo, trust)
        try:
            post(url, json.dumps(BRIEF).encode(), {"X-Forwarded-For": "1.2.3.4, 9.9.9.9"})
        finally:
            httpd.shutdown()
        assert seen[-1] == expect


# ---- the Anthropic backend ------------------------------------------------------------------------------------------

def test_backend_setting(monkeypatch):
    monkeypatch.delenv("APOLAKI_BACKEND", raising=False)
    monkeypatch.delenv("APOLAKI_MODEL", raising=False)
    assert config.backend() == "ollama" and config.model() == "qwen3.8:27b-mxfp8"
    monkeypatch.setenv("APOLAKI_BACKEND", "anthropic")
    assert config.model() == "claude-haiku-5-5"
    monkeypatch.setenv("APOLAKI_BACKEND", "nonsense")
    with pytest.raises(ValueError):
        config.backend()


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_chat_with_the_anthropic_backend(monkeypatch):
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        return FakeResponse({"content": [{"type": "text", "text": "Hello."}], "stop_reason": "end_turn",
                             "usage": {"input_tokens": 12, "output_tokens": 3}})
    monkeypatch.setenv("APOLAKI_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(A.requests, "post", fake_post)
    before = dict(A.USAGE)
    out = A.chat([{"role": "system", "content": "Be plain."}, {"role": "user", "content": "Hi"}], max_tokens=50)
    assert out == "Hello." and sent["url"] == A.ANTHROPIC_URL and sent["headers"]["x-api-key"] == "test-key"
    assert sent["body"]["system"] == "Be plain." and sent["body"]["messages"] == [{"role": "user", "content": "Hi"}]
    assert sent["body"]["max_tokens"] == 50 and "temperature" not in sent["body"]
    assert A.USAGE["input_tokens"] - before["input_tokens"] == 12
    assert A.USAGE["output_tokens"] - before["output_tokens"] == 3


def test_anthropic_errors_truncation_and_a_missing_key(monkeypatch):
    monkeypatch.setenv("APOLAKI_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(A.requests, "post",
                        lambda *a, **k: FakeResponse({"type": "error", "error": {"message": "overloaded"}}))
    assert A.chat([{"role": "user", "content": "Hi"}]).startswith("[LLM Error: overloaded")
    monkeypatch.setattr(A.requests, "post", lambda *a, **k: FakeResponse(
        {"content": [{"type": "text", "text": "Cut off"}], "stop_reason": "max_tokens", "usage": {}}))
    assert A.chat([{"role": "user", "content": "Hi"}]) == "Cut off" and A.LAST_RESPONSE["truncated"] is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "needs ANTHROPIC_API_KEY" in A.chat([{"role": "user", "content": "Hi"}])


def test_an_agent_can_be_given_its_own_memory():
    mem = EmptyMemory()
    assert A.ApolakiAgent(memory=mem).memory is mem
