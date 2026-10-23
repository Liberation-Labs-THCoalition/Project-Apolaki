"""The Kintsugi engine around the working agent: BDI intentions, the content-creation chip, and reviewers' decisions
becoming beliefs that reach the model as reference material (never through the brief)."""
import json

import pytest

from apolaki import kintsugi_core as k


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLAKI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("APOLAKI_BRAND_FILE", raising=False)
    return tmp_path / "home"


class FakeAgent:
    def __init__(self, status="queued_for_review"):
        self.calls = []
        self.status = status

    def draft_content(self, brief, content_type="social", reviewer_feedback="", max_tokens="unset"):
        self.calls.append({"brief": brief, "content_type": content_type, "reviewer_feedback": reviewer_feedback,
                           "max_tokens": max_tokens})
        return {"draft": "DRAFT", "status": self.status, "queue_id": "q1"}


def queue_item(home, item_id, content_type="social", status="pending", note=""):
    d = home / "approval-queue"
    d.mkdir(parents=True, exist_ok=True)
    item = {"id": item_id, "content": "x", "content_type": content_type, "status": status, "review_note": note}
    (d / f"{item_id}_{content_type}.json").write_text(json.dumps(item))


def engine(home, agent=None, learn=False):
    from apolaki.engine import ApolakiEngine
    return ApolakiEngine(agent or FakeAgent(), learn=learn)


def test_the_engine_loads_kintsugi():
    assert k.KINTSUGI_SOURCE != "unresolved"


def test_a_draft_is_an_intention_carried_out_by_the_chip(home):
    e = engine(home)
    out = e.draft("Announce the new tenant clinic", "social")
    intentions = e.bdi.list_intentions()
    assert out["draft"] == "DRAFT" and len(intentions) == 1
    assert intentions[0].status is k.IntentionStatus.COMPLETED and "Announce the new tenant clinic" in intentions[0].goal


def test_a_blocked_draft_is_a_failed_intention(home):
    e = engine(home, FakeAgent(status="blocked"))
    e.draft("Act now, last chance!", "social")
    assert e.bdi.list_intentions()[0].status is k.IntentionStatus.FAILED


def test_a_recorded_decision_becomes_a_belief_once(home):
    queue_item(home, "20261016_120000", status="rejected", note="too breathless, cut the superlatives")
    queue_item(home, "20261016_120001", status="pending")
    e = engine(home)
    assert e.sync_reviews() == 1 and e.sync_reviews() == 0
    b = e.bdi.list_beliefs()[0]
    assert "rejected a social draft: too breathless" in b.content and b.evidence == ["20261016_120000"]


def test_beliefs_survive_a_restart(home):
    queue_item(home, "20261016_120000", status="approved", note="this is the register")
    engine(home).sync_reviews()
    again = engine(home)
    assert [b.content for b in again.bdi.list_beliefs()] == ["Reviewers approved a social draft: this is the register"]
    assert again.sync_reviews() == 0                          # not counted twice after the restart


def test_feedback_reaches_the_model_beside_memory_never_in_the_brief(home):
    queue_item(home, "20261016_120000", status="rejected", note="no 'act now' urgency, ever")
    agent = FakeAgent()
    out = engine(home, agent, learn=True).draft("Announce the clinic", "social")
    call = agent.calls[0]
    assert call["brief"] == "Announce the clinic"                                   # the brief is untouched
    assert "<reviewer_feedback" in call["reviewer_feedback"] and "act now" in call["reviewer_feedback"]
    assert out["reviewer_feedback_used"] is True


def test_feedback_is_per_content_type_and_needs_a_note(home):
    queue_item(home, "20261016_120000", content_type="blog", status="rejected", note="too long")
    queue_item(home, "20261016_120001", content_type="social", status="approved", note="")
    e = engine(home)
    e.sync_reviews()
    assert e.feedback_block("social") == ""                  # a decision with no note has nothing to say
    assert "too long" in e.feedback_block("blog")


def test_the_feedback_block_marks_its_cut(home):
    for i in range(7):
        queue_item(home, f"20261016_12000{i}", status="rejected", note=f"note {i}")
    e = engine(home)
    e.sync_reviews()
    block = e.feedback_block("social")
    assert 'count="5" of="7"' in block and block.count("\n- ") == 5


def test_no_decisions_means_no_feedback_block(home):
    agent = FakeAgent()
    out = engine(home, agent).draft("Announce the clinic", "social")
    assert agent.calls[0]["reviewer_feedback"] == "" and out["reviewer_feedback_used"] is False


# ---- review_item: the decision gets recorded at all ----------------------------------------------------------------
def test_review_item_records_the_decision(home):
    from apolaki.agent.agent import review_item
    queue_item(home, "20261016_130000")
    item = review_item("20261016_130000", "changes_requested", "lead with the clinic hours", "the comms lead")
    on_disk = json.loads((home / "approval-queue" / "20261016_130000_social.json").read_text())
    assert on_disk["status"] == "changes_requested" and on_disk["review_note"] == "lead with the clinic hours"
    assert on_disk["reviewed_by"] == "the comms lead" and on_disk["reviewed_at"] and item == on_disk


def test_review_item_refuses_an_unknown_decision_or_item(home):
    from apolaki.agent.agent import review_item
    queue_item(home, "20261016_130000")
    with pytest.raises(ValueError, match="decision"):
        review_item("20261016_130000", "maybe")
    with pytest.raises(ValueError, match="no single queue item"):
        review_item("19990101_000000", "approved")


def test_the_real_draft_path_puts_feedback_beside_memory_not_in_the_brief(home, monkeypatch):
    from apolaki.agent import agent as A
    prompts = []

    def fake_chat(messages, temperature=0.7, max_tokens=2048):
        prompts.append(messages[-1]["content"])
        A.LAST_RESPONSE.clear()
        A.LAST_RESPONSE.update({"truncated": False})
        return "Our tenant clinic opens Monday at the library."

    class Memory:
        def search(self, query, n_results=6):
            return []

    monkeypatch.setattr(A, "chat", fake_chat)
    agent = object.__new__(A.ApolakiAgent)
    agent.memory, agent.history, agent.auto_capture, agent.house_rules_count = Memory(), [{"role": "system", "content": "S"}], False, 0
    feedback = '<reviewer_feedback count="1" of="1" note="n">\n- Reviewers rejected a social draft: no urgency\n</reviewer_feedback>'
    out = agent.draft_content("Announce the clinic", "social", reviewer_feedback=feedback)
    assert out["status"] != "blocked" and prompts
    before, after = prompts[0].split("</brief>", 1)
    assert "reviewer_feedback" not in before and feedback in after


def test_a_kintsugi_checkout_wins_over_the_vendored_copy(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    vendor = Path(__file__).resolve().parents[2] / "apolaki" / "kintsugi_vendor" / "kintsugi"
    shutil.copytree(vendor, tmp_path / "kintsugi")
    code = "import apolaki.kintsugi_core as k, kintsugi; print(k.KINTSUGI_SOURCE); print(kintsugi.__file__)"
    env = {"KINTSUGI_PATH": str(tmp_path), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True).stdout.split()
    assert out[0] == f"checkout:{tmp_path}" and out[1].startswith(str(tmp_path))


def test_max_tokens_reaches_the_agent_only_when_set(home):
    agent = FakeAgent()
    e = engine(home, agent)
    e.draft("A grant narrative", "grant", max_tokens=8000)
    assert agent.calls[-1]["max_tokens"] == 8000
    e.draft("A post", "social")
    assert agent.calls[-1]["max_tokens"] == "unset"


@pytest.mark.parametrize("bad", [0, -1, True, "8000", 1.5])
def test_max_tokens_must_be_a_positive_integer(home, bad):
    agent = FakeAgent()
    with pytest.raises(ValueError, match="max_tokens"):
        engine(home, agent).draft("A grant narrative", "grant", max_tokens=bad)
    assert agent.calls == []


def test_max_tokens_becomes_the_model_budget_on_the_real_path(home, monkeypatch):
    from apolaki.agent import agent as A
    budgets = []

    def fake_chat(messages, temperature=0.7, max_tokens=2048):
        budgets.append(max_tokens)
        A.LAST_RESPONSE.clear()
        A.LAST_RESPONSE.update({"truncated": False})
        return "Our tenant clinic opens Monday at the library."

    class Memory:
        def search(self, query, n_results=6):
            return []

    monkeypatch.setattr(A, "chat", fake_chat)
    agent = object.__new__(A.ApolakiAgent)
    agent.memory, agent.history, agent.auto_capture, agent.house_rules_count = Memory(), [{"role": "system", "content": "S"}], False, 0
    engine(home, agent).draft("Announce the clinic", "social", max_tokens=8000)
    assert budgets and set(budgets) == {8000}
    budgets.clear()
    engine(home, agent).draft("Announce the clinic", "social")
    assert budgets and set(budgets) == {4096}


def test_the_cli_passes_max_tokens(home, monkeypatch):
    import apolaki.engine as E
    seen = {}

    class Stub:
        def __init__(self, learn=False):
            seen["learn"] = learn

        def draft(self, brief, content_type="social", max_tokens=None):
            seen.update(brief=brief, content_type=content_type, max_tokens=max_tokens)
            return {"draft": "D", "status": "queued_for_review", "queue_id": "q", "reviewer_feedback_used": False}

    monkeypatch.setattr(E, "ApolakiEngine", Stub)
    assert E.main(["draft", "A grant narrative", "--type", "grant", "--max-tokens", "8000"]) == 0
    assert seen == {"brief": "A grant narrative", "content_type": "grant", "max_tokens": 8000, "learn": False}
    assert E.main(["draft", "A post"]) == 0
    assert seen["max_tokens"] is None


# ---- learning is off unless asked for (Butlin AE-1: Thomas 10-06, Nexus 10-19) -------------------------------------
def test_by_default_a_draft_forms_no_belief_and_the_model_sees_no_feedback(home):
    queue_item(home, "20261016_120000", status="rejected", note="no 'act now' urgency, ever")
    agent = FakeAgent()
    e = engine(home, agent)
    out = e.draft("Announce the clinic", "social")
    assert agent.calls[0]["reviewer_feedback"] == "" and out["reviewer_feedback_used"] is False
    assert e.bdi.list_beliefs() == [] and not (home / "engine_beliefs.json").exists()
    assert e.bdi.list_intentions()[0].belief_ids == []


def test_with_learning_on_the_decision_becomes_a_belief_and_reaches_the_model(home):
    queue_item(home, "20261016_120000", status="rejected", note="no 'act now' urgency, ever")
    agent = FakeAgent()
    e = engine(home, agent, learn=True)
    e.draft("Announce the clinic", "social")
    assert "act now" in agent.calls[0]["reviewer_feedback"] and len(e.bdi.list_beliefs()) == 1
    assert e.bdi.list_intentions()[0].belief_ids == [e.bdi.list_beliefs()[0].id]


def test_the_cli_turns_learning_on_only_when_asked(home, monkeypatch):
    import apolaki.engine as E
    seen = {}

    class Stub:
        def __init__(self, learn=False):
            seen["learn"] = learn

        def draft(self, brief, content_type="social", max_tokens=None):
            return {"draft": "D", "status": "queued_for_review", "queue_id": "q", "reviewer_feedback_used": False}

    monkeypatch.setattr(E, "ApolakiEngine", Stub)
    E.main(["draft", "A post"])
    assert seen["learn"] is False
    E.main(["draft", "A post", "--learn"])
    assert seen["learn"] is True


def test_an_engine_built_without_saying_does_not_learn(home):
    """The default itself, not a helper's argument: grant_hunt and others build ApolakiEngine(agent)."""
    from apolaki.engine import ApolakiEngine
    queue_item(home, "20261016_120000", status="rejected", note="no 'act now' urgency, ever")
    agent = FakeAgent()
    e = ApolakiEngine(agent)
    e.draft("Announce the clinic", "social")
    assert e.learn is False and agent.calls[0]["reviewer_feedback"] == "" and e.bdi.list_beliefs() == []


# --- 10-23: thinking for the first generation only, and the revision's own budget (the grant drafter's arm E) ---
class KwargAgent(FakeAgent):
    def draft_content(self, brief, content_type="social", reviewer_feedback="", **kw):
        self.calls.append(dict(kw, content_type=content_type))
        return {"draft": "DRAFT", "status": self.status, "queue_id": "q1"}


def test_think_and_the_revision_budget_reach_the_agent_only_when_set(home):
    agent = KwargAgent()
    e = engine(home, agent)
    e.draft("A grant narrative", "grant", max_tokens=16000, think=True, revise_max_tokens=4000)
    assert agent.calls[-1] == {"content_type": "grant", "max_tokens": 16000, "think": True, "revise_max_tokens": 4000}
    e.draft("A post", "social")
    assert agent.calls[-1] == {"content_type": "social"}


@pytest.mark.parametrize("kw,err", [({"revise_max_tokens": 0}, ValueError), ({"revise_max_tokens": True}, ValueError),
                                    ({"think": "yes"}, TypeError), ({"think": 1}, TypeError)])
def test_bad_think_or_revision_budget_is_refused_before_any_call(home, kw, err):
    agent = KwargAgent()
    with pytest.raises(err):
        engine(home, agent).draft("A grant narrative", "grant", **kw)
    assert agent.calls == []


def real_agent(A):
    class Memory:
        def search(self, query, n_results=6):
            return []
    agent = object.__new__(A.ApolakiAgent)
    agent.memory, agent.history, agent.auto_capture, agent.house_rules_count = Memory(), [{"role": "system", "content": "S"}], False, 0
    return agent


def test_thinking_reaches_the_first_generation_only_and_the_revision_keeps_its_budget(home, monkeypatch):
    from apolaki.agent import agent as A
    calls = []

    def fake_chat(messages, temperature=0.7, max_tokens=2048, **kw):
        calls.append((max_tokens, kw.get("think", False)))
        A.LAST_RESPONSE.clear()
        A.LAST_RESPONSE.update({"truncated": False})
        return "Our tenant clinic opens Monday at the library."

    hit = {"line": 1, "check": "x", "detail": None, "text": "t", "rule": "r"}
    lint = iter([{"pass": False, "total_hits": 2, "failing_checks": ["x"], "hits": [hit]},
                 {"pass": True, "total_hits": 0, "failing_checks": [], "hits": []}])
    monkeypatch.setattr(A, "chat", fake_chat)
    monkeypatch.setattr(A, "lint_draft", lambda d: d)
    monkeypatch.setattr(A, "lint_summary", lambda r: next(lint))
    engine(home, real_agent(A)).draft("Announce the clinic", "grant", max_tokens=16000, think=True,
                                      revise_max_tokens=4000)
    assert calls == [(16000, True), (4000, False)]


def test_by_default_the_model_is_called_exactly_as_before(home, monkeypatch):
    from apolaki.agent import agent as A
    seen = []

    def fake_chat(messages, temperature=0.7, max_tokens=2048):    # no think argument: a call with one would fail
        seen.append(max_tokens)
        A.LAST_RESPONSE.clear()
        A.LAST_RESPONSE.update({"truncated": False})
        return "Our tenant clinic opens Monday at the library."
    monkeypatch.setattr(A, "chat", fake_chat)
    try:
        engine(home, real_agent(A)).draft("Announce the clinic", "social", max_tokens=8000)
    except TypeError as e:
        pytest.fail(f"the default path passed chat() an argument it never used to: {e}")
    assert seen and set(seen) == {8000}


def test_the_reasoning_channel_never_reaches_the_draft(home, monkeypatch):
    """Through the real chat() and its parser: the model's reasoning carries a marker, the draft must not."""
    from apolaki.agent import agent as A
    marker = "REASONING-ONLY-7f3a"

    class Resp:
        status_code = 200

        def json(self):
            return {"message": {"content": "Our tenant clinic opens Monday at the library.",
                                "thinking": f"Plan the post. {marker} Keep it short."}, "done_reason": "stop"}
    sent = []
    monkeypatch.setattr(A.requests, "post", lambda url, json=None, timeout=None: sent.append(json) or Resp())
    out = engine(home, real_agent(A)).draft("Announce the clinic", "social", max_tokens=16000, think=True)
    assert sent and sent[0]["think"] is True
    assert out["draft"] and marker not in out["draft"] and marker not in json.dumps(out)


@pytest.mark.parametrize("bad", [0, -5, True, "4000"])
def test_the_agent_refuses_a_bad_revision_budget_itself_before_any_call(monkeypatch, bad):
    """The engine checks first; this checks the agent's own guard, for callers that use it directly."""
    from apolaki.agent import agent as A
    calls = []
    monkeypatch.setattr(A, "chat", lambda *a, **k: calls.append(1) or "x")
    with pytest.raises(ValueError, match="revise_max_tokens"):
        real_agent(A).draft_content("Announce the clinic", "grant", revise_max_tokens=bad)
    assert calls == []
