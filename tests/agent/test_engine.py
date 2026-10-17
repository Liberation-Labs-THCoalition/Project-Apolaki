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

    def draft_content(self, brief, content_type="social", reviewer_feedback=""):
        self.calls.append({"brief": brief, "content_type": content_type, "reviewer_feedback": reviewer_feedback})
        return {"draft": "DRAFT", "status": self.status, "queue_id": "q1"}


def queue_item(home, item_id, content_type="social", status="pending", note=""):
    d = home / "approval-queue"
    d.mkdir(parents=True, exist_ok=True)
    item = {"id": item_id, "content": "x", "content_type": content_type, "status": status, "review_note": note}
    (d / f"{item_id}_{content_type}.json").write_text(json.dumps(item))


def engine(home, agent=None):
    from apolaki.engine import ApolakiEngine
    return ApolakiEngine(agent or FakeAgent())


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
    out = engine(home, agent).draft("Announce the clinic", "social")
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
