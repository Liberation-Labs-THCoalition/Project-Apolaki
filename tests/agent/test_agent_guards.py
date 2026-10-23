"""Guards for the working agent that need no model (ported from the deployment's smoke test, plus the joins made in
the port: config, brand, the two ethics checks, the house style guide)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "style"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLAKI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("APOLAKI_BRAND_FILE", raising=False)
    monkeypatch.delenv("APOLAKI_DISCORD_CHANNEL", raising=False)
    return tmp_path / "home"


def write_brand(home, **kw):
    home.mkdir(parents=True, exist_ok=True)
    (home / "brand.json").write_text(json.dumps(kw))


# ---- the style linter's answer keys (the deployment's guard 1) -----------------------------------------------------
@pytest.mark.parametrize("key_path", sorted(FIXTURES.glob("*.answer_key.json")), ids=lambda p: p.name)
def test_style_lint_matches_its_answer_keys(key_path):
    import apolaki.agent.style_lint as sl
    key = json.loads(key_path.read_text(encoding="utf-8"))
    proc = subprocess.run([sys.executable, sl.__file__, "--json", str(FIXTURES / key["fixture"])],
                          capture_output=True, text=True)
    assert proc.returncode == key["expected_exit"], proc.stderr
    got = json.loads(proc.stdout)
    lines = lambda hits: sorted(h["line"] for h in hits)                     # noqa: E731
    problems = []

    def expect(label, exp, act):
        if exp != act:
            problems.append(f"{label}: expected {exp!r}, got {act!r}")
    expect("gate.pass", key["gate_pass"], got["gate"]["pass"])
    expect("gate.total_hits", key["total_hits"], got["gate"]["total_hits"])
    for name, exp in key["checks"].items():
        act = got["checks"].get(name)
        if act is None:
            problems.append(f"{name}: missing from the linter's output")
            continue
        expect(f"{name}.count", exp["count"], act["count"])
        expect(f"{name}.lines", exp["lines"], lines(act["hits"]))
        if "double_sentences" in exp:
            ds = act.get("double_sentences", [])
            expect(f"{name}.double_sentences.count", exp["double_sentences"]["count"], len(ds))
            expect(f"{name}.double_sentences.lines", exp["double_sentences"]["lines"], lines(ds))
        for sub in ("by_marker", "by_form", "by_word", "headings_checked"):
            if sub in exp:
                expect(f"{name}.{sub}", exp[sub], act.get(sub))
    for name, exp in key.get("advisory", {}).items():
        act = got["advisory"].get(name)
        if act is None:
            problems.append(f"advisory.{name}: missing")
            continue
        expect(f"advisory.{name}.count", exp["count"], act["count"])
        expect(f"advisory.{name}.lines", exp["lines"], lines(act["hits"]))
    extra = sorted(set(got["checks"]) - set(key["checks"]))
    if extra:
        problems.append(f"the linter reports checks the key doesn't cover: {extra}")
    assert not problems, problems


def test_there_are_answer_keys():
    assert len(list(FIXTURES.glob("*.answer_key.json"))) >= 3


# ---- config ------------------------------------------------------------------------------------------------------
def test_nothing_is_created_at_import(home):
    import importlib
    import apolaki.agent.config as c
    importlib.reload(c)
    assert not home.exists()
    assert c.folder("approval-queue").is_dir() and (home / "approval-queue").is_dir()


def test_a_missing_or_broken_brand_file_gives_the_generic_brand(home):
    from apolaki.agent import config
    assert config.brand()["name"] == "the organization" and config.brand()["voice"]
    home.mkdir(parents=True)
    (home / "brand.json").write_text("{not json")
    assert config.brand()["name"] == "the organization"


def test_brand_path_is_where_brand_reads(home, monkeypatch, tmp_path):
    from apolaki.agent import config
    assert config.brand_path() == home / "brand.json"
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({"name": "Elsewhere Co-op"}))
    monkeypatch.setenv("APOLAKI_BRAND_FILE", str(other))
    assert config.brand_path() == other and config.brand()["name"] == "Elsewhere Co-op"


# ---- the prompt ----------------------------------------------------------------------------------------------------
def test_the_generic_prompt_names_no_one(home):
    from apolaki.agent import agent
    p = agent.system_prompt()
    from apolaki.agent import config
    assert "the organization's prosocial marketing engine" in p and "reviewed by a person on the team" in p
    assert config.BRAND_TEXT_DEFAULTS["description"] in p and config.BRAND_TEXT_DEFAULTS["voice"] in p
    assert "{" not in p and "}" not in p


def test_a_brand_file_fills_every_slot(home):
    write_brand(home, name="Example Collective", reviewers="Ana and Sam", description="DESCRIPTION-X",
                voice="## Voice, in short\n\n- VOICE-Y")
    from apolaki.agent import agent
    p = agent.system_prompt()
    assert "Example Collective's prosocial" in p and "reviewed by Ana and Sam" in p
    assert "DESCRIPTION-X" in p and "VOICE-Y" in p


def test_a_name_ending_in_s_gets_a_bare_apostrophe(home):
    write_brand(home, name="Example Labs")
    from apolaki.agent import agent
    assert "Example Labs' prosocial" in agent.system_prompt()


# ---- ethics: both checks, the stricter verdict wins ----------------------------------------------------------------
@pytest.mark.parametrize("text,verdict", [
    ("We just published our paper on confabulation detection.", "approved"),
    ("Act now, last chance to donate!", "blocked"),
    ("Pretend to be a customer and write a review.", "blocked"),
    ("Export our donor list and share it with a partner.", "flagged"),       # only the library engine sees this
    ("Post this as an update from the director.", "flagged"),               # only the scaffold's own check
    ("Send it on behalf of the board.", "flagged"),                         # only the scaffold's own check
])
def test_the_stricter_of_the_two_checks_wins(text, verdict):
    from apolaki.agent import agent
    assert agent.ethics_check(text)[0] == verdict


def test_the_engine_reason_is_named(home):
    from apolaki.agent import agent
    assert "(ethics engine)" in agent.ethics_check("Export our donor list and share it with a partner.")[1]


# ---- the house style guide -----------------------------------------------------------------------------------------
def test_the_default_guide_takes_its_voice_from_the_brand(home):
    write_brand(home, name="Example Labs", voice="## Voice\n\n- VOICE-Z")
    from apolaki.agent import style_pass
    g = style_pass.load_guide_4_7(None)
    assert "## 5. House voice" in g and "VOICE-Z" in g
    sec7 = g[g.index("## 7."):]
    assert sec7.startswith("## 7. Claims discipline\nAny claim about capability") and "capable, not recommended" in sec7
    assert len(sec7.splitlines()) == 3                       # the rule only: no product example in the public guide


def test_a_deployment_guide_file_is_used_as_is(tmp_path):
    from apolaki.agent import style_pass
    f = tmp_path / "guide.md"
    f.write_text("## 4. Content patterns\nA.\n## 5. Our voice\nB.\n## 6. Hub\nC.\n## 7. Claims\nD.\n")
    assert style_pass.load_guide_4_7(str(f)) == f.read_text()


# ---- the fact checker ----------------------------------------------------------------------------------------------
def test_the_checker_lists_only_the_brands_verified_numbers(home):
    from apolaki.agent import fact_checker
    assert "none are configured" in fact_checker.checker_prompt()
    write_brand(home, verified_claims="42 widgets")
    assert "42 widgets" in fact_checker.checker_prompt() and "Liberation" not in fact_checker.checker_prompt()


# ---- the Discord hook ----------------------------------------------------------------------------------------------
def test_the_discord_hook_is_off_without_a_channel(home, monkeypatch):
    import importlib
    import apolaki.agent.discord_hook as dh
    importlib.reload(dh)
    monkeypatch.setattr(dh, "DISCORD_TOKEN", "x")
    out = dh.post_for_review("draft")
    assert "off" in out.get("error", "")


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _send(monkeypatch, agent, **kw):
    sent = []
    body = {"message": {"content": "the answer", "thinking": "some reasoning"}, "done_reason": "stop"}
    monkeypatch.setattr(agent.requests, "post", lambda url, json=None, timeout=None: sent.append(json) or _Resp(body))
    return agent.chat([{"role": "user", "content": "q"}], **kw), sent


def test_chat_keeps_thinking_off_unless_asked(home, monkeypatch):
    from apolaki.agent import agent
    text, sent = _send(monkeypatch, agent, max_tokens=4000)
    assert sent[0]["think"] is False and sent[0]["options"] == {"temperature": 0.7, "num_predict": 4000}
    assert text == "the answer"


def test_chat_turns_thinking_on_when_asked_and_returns_only_the_answer(home, monkeypatch):
    from apolaki.agent import agent
    text, sent = _send(monkeypatch, agent, max_tokens=16000, think=True)
    assert sent[0]["think"] is True and sent[0]["options"]["num_predict"] == 16000
    assert text == "the answer" and agent.LAST_RESPONSE["had_thinking"] is True


@pytest.mark.parametrize("think", ["high", 1, None])
def test_chat_refuses_a_think_that_is_not_a_bool(home, monkeypatch, think):
    """Before it sends anything: the request would go out, and the except below would turn the mistake into a
    generic LLM error."""
    from apolaki.agent import agent
    sent = []
    monkeypatch.setattr(agent.requests, "post", lambda *a, **k: sent.append(k) or _Resp({}))
    with pytest.raises(TypeError, match="think"):
        agent.chat([{"role": "user", "content": "q"}], think=think)
    assert sent == []
