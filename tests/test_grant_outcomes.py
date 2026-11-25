"""The grant outcome log (2026-11-02 review, adoption 8): nothing recorded which applications were submitted, funded or
declined. A person records each outcome beside the grant hunt's seen-list; the summary is in counts with n."""
import ast
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLAKI_HOME", str(tmp_path / "home"))
    h = tmp_path / "home"
    (h / "grant-hunt").mkdir(parents=True)
    seen = {f"OPP-{i}": {"id": str(i), "title": f"Listing {i}", "first_seen": "2026-10-01"} for i in range(1, 10)}
    seen["watch:a-fund:community-tech"] = {"id": "watch:a-fund:community-tech", "title": "Community tech"}
    (h / "grant-hunt" / "seen.json").write_text(json.dumps(seen))
    return h


def rows(home):
    path = home / "grant-hunt" / "outcomes.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_an_outcome_is_recorded_beside_the_seen_list(home):
    from apolaki.grants import outcomes as o
    assert o.outcomes_path() == home / "grant-hunt" / "outcomes.jsonl"            # beside seen.json
    rec = o.record("OPP-1", "applied", "2026-10-20", by="the grants lead")
    assert len(rows(home)) == 1
    [row] = rows(home)
    assert row == rec
    assert row["key"] == "OPP-1" and row["outcome"] == "applied" and row["date"] == "2026-10-20"
    assert row["by"] == "the grants lead" and row["recorded_at"]


def test_a_watchlist_key_works_too(home):
    from apolaki.grants import outcomes as o
    o.record("watch:a-fund:community-tech", "applied", "2026-10-20", by="x")
    assert rows(home)[0]["key"] == "watch:a-fund:community-tech"


def test_a_key_the_hunt_never_saw_needs_unlisted(home):
    from apolaki.grants import outcomes as o
    with pytest.raises(ValueError, match="not in"):
        o.record("NOPE-1", "applied", "2026-10-20", by="x")
    assert rows(home) == []
    o.record("NOPE-1", "applied", "2026-10-20", by="x", unlisted=True)
    assert rows(home)[0].get("unlisted") is True


def test_without_a_seen_list_every_key_needs_unlisted(home):
    from apolaki.grants import outcomes as o
    (home / "grant-hunt" / "seen.json").unlink()
    with pytest.raises(ValueError, match="there is no .*seen.json"):
        o.record("OPP-1", "applied", "2026-10-20", by="x")
    (home / "grant-hunt" / "seen.json").write_text("{not json")
    with pytest.raises(ValueError, match="seen.json can't be read"):
        o.record("OPP-1", "applied", "2026-10-20", by="x")
    assert rows(home) == []


@pytest.mark.parametrize("kw,match", [
    ({"outcome": "won"}, "outcome"),
    ({"date": "20/10/2026"}, "YYYY-MM-DD"),
    ({"date": "2026-02-30"}, "YYYY-MM-DD"),
    ({"date": "20261020"}, "YYYY-MM-DD"),                  # fromisoformat takes this on 3.11+; the log doesn't
    ({"date": "2026-W43-2"}, "YYYY-MM-DD"),
    ({"date": (date.today() + timedelta(days=1)).isoformat()}, "future"),
    ({"by": "  "}, "who"),
    ({"reason": "the budget was over the cap"}, "source"),
    ({"source": "the program officer's email"}, "reason"),
    ({"reason": "r", "source": "s", "confidence": "medium"}, "confidence"),
])
def test_a_record_that_breaks_a_rule_is_refused_and_nothing_is_written(home, kw, match):
    from apolaki.grants import outcomes as o
    args = dict({"key": "OPP-1", "outcome": "declined", "date": "2026-10-20", "by": "x"}, **kw)
    with pytest.raises(ValueError, match=match):
        o.record(**args)
    assert rows(home) == []


def test_no_reason_without_a_source_and_a_reason_is_low_confidence_unless_said(home):
    from apolaki.grants import outcomes as o
    o.record("OPP-1", "declined", "2026-10-20", by="x", reason="over the cap", source="the decline letter")
    o.record("OPP-2", "declined", "2026-10-20", by="x", reason="out of scope", source="a call with the officer",
             confidence="high")
    a, b = rows(home)
    assert (a["reason"], a["source"], a["confidence"]) == ("over the cap", "the decline letter", "low")
    assert b["confidence"] == "high"


def test_recording_appends_and_never_rewrites(home):
    from apolaki.grants import outcomes as o
    o.record("OPP-1", "applied", "2026-10-01", by="x")
    first = (home / "grant-hunt" / "outcomes.jsonl").read_bytes()
    o.record("OPP-1", "declined", "2026-10-20", by="x")
    assert (home / "grant-hunt" / "outcomes.jsonl").read_bytes().startswith(first)


def plant(o):
    o.record("OPP-1", "applied", "2026-08-01", by="x")
    o.record("OPP-1", "awarded", "2026-09-01", by="x", reason="strong fit with the call's aims", source="award letter")
    o.record("OPP-2", "awarded", "2026-09-02", by="x")
    o.record("OPP-3", "declined", "2026-09-03", by="x", reason="budget over the cap", source="decline letter")
    o.record("OPP-4", "declined", "2026-09-04", by="x", reason="budget over the cap", source="officer's email")
    o.record("OPP-5", "declined", "2026-09-05", by="x")
    o.record("OPP-6", "no_reply", "2026-10-30", by="x")
    o.record("OPP-7", "applied", "2026-10-31", by="x")


def test_the_summary_is_in_counts_with_n_and_no_percentages(home):
    from apolaki.grants import outcomes as o
    plant(o)
    s = o.summary()
    assert s["n"] == 7 and s["counts"] == {"awarded": 2, "declined": 3, "no_reply": 1, "applied": 1}
    text = o.summary_text(s)
    assert "n = 7 applications" in text
    assert "awarded 2 of 7" in text and "declined 3 of 7" in text and "no reply 1 of 7" in text
    assert "still waiting 1 of 7" in text and "of the 5 decided, 2 awarded and 3 declined" in text
    assert "%" not in text and "signal to watch" not in text          # 5 decided: past the watch line
    assert "declined: budget over the cap (2); sources: decline letter (low confidence); officer's email" in text
    assert "awarded: strong fit with the call's aims (1); source: award letter (low confidence)" in text
    assert "2 decided outcomes have no reason on record" in text


def test_under_five_decided_is_a_signal_to_watch(home):
    from apolaki.grants import outcomes as o
    o.record("OPP-1", "awarded", "2026-09-01", by="x")
    o.record("OPP-2", "declined", "2026-09-02", by="x")
    o.record("OPP-3", "no_reply", "2026-09-03", by="x")
    text = o.summary_text(o.summary())
    assert "of the 2 decided" in text and "Under 5 decided: a signal to watch, not a finding." in text


def test_the_latest_record_is_the_state(home):
    from apolaki.grants import outcomes as o
    o.record("OPP-1", "applied", "2026-09-01", by="x")
    o.record("OPP-1", "declined", "2026-10-01", by="x")
    o.record("OPP-1", "awarded", "2026-10-02", by="x", reason="the decline was a mistake", source="their correction")
    s = o.summary()
    assert s["n"] == 1 and s["counts"]["awarded"] == 1 and s["counts"]["declined"] == 0


def test_since_counts_applications_whose_latest_record_falls_in_the_period(home):
    from apolaki.grants import outcomes as o
    plant(o)
    s = o.summary(since="2026-10-01")
    assert s["n"] == 2 and s["counts"] == {"awarded": 0, "declined": 0, "no_reply": 1, "applied": 1}
    assert "since 2026-10-01" in o.summary_text(s)
    assert o.summary(since="2026-10-30")["n"] == 2                  # a record on the day itself counts
    assert o.summary(since="2026-10-31")["n"] == 1


def test_an_empty_log_says_so(home):
    from apolaki.grants import outcomes as o
    assert o.summary()["n"] == 0
    assert "no outcomes recorded" in o.summary_text(o.summary())


def test_the_cli_records_and_summarises(home, capsys):
    from apolaki.grants.__main__ import main
    assert main(["outcome", "OPP-1", "applied", "--date", "2026-10-20", "--by", "the grants lead"]) == 0
    assert main(["outcome", "OPP-1", "no-reply", "--date", "2026-10-25", "--by", "the grants lead"]) == 0
    assert rows(home)[-1]["outcome"] == "no_reply"
    assert main(["outcome", "OPP-2", "declined", "--date", "2026-10-20", "--by", "x", "--reason", "r"]) == 2
    assert "source" in capsys.readouterr().err
    assert main(["outcomes"]) == 0
    out = capsys.readouterr().out
    assert "n = 1 application" in out and "no reply 1 of 1" in out


def test_the_list_shows_each_application_with_its_title(home, capsys):
    from apolaki.grants.__main__ import main
    main(["outcome", "OPP-3", "declined", "--date", "2026-10-20", "--by", "x", "--reason", "late", "--source", "email"])
    capsys.readouterr()
    assert main(["outcomes", "--list"]) == 0
    out = capsys.readouterr().out
    assert "OPP-3" in out and "Listing 3" in out and "declined" in out and "late" in out


# ---- it feeds nothing ------------------------------------------------------------------------------------------
ALLOWED_READERS = {"apolaki/grants/outcomes.py", "apolaki/grants/__main__.py"}


def test_nothing_else_in_the_package_reads_the_outcome_log():
    """Triage and learning never read outcomes. A future feed must go through apolaki.learning (switch, pause, caps,
    log) and Thomas's decision; this list is the test's own, so adding a reader means editing it on purpose."""
    found = []
    for p in sorted((ROOT / "apolaki").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel in ALLOWED_READERS:
            continue
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "outcomes" in node.module:
                found.append(f"{rel}:{node.lineno} imports {node.module}")
            if isinstance(node, ast.ImportFrom) and any(a.name == "outcomes" for a in node.names):
                found.append(f"{rel}:{node.lineno} imports outcomes")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "outcomes.jsonl" in node.value:
                found.append(f"{rel}:{node.lineno} names outcomes.jsonl")
    assert found == []


def test_the_scan_sees_a_reader_when_there_is_one(tmp_path):
    """The scan above can fire: the same walk over a planted reader finds it."""
    planted = "from apolaki.grants import outcomes\nLOG = 'grant-hunt/outcomes.jsonl'\n"
    tree = ast.parse(planted)
    hits = [n for n in ast.walk(tree) if (isinstance(n, ast.ImportFrom) and any(a.name == "outcomes" for a in n.names))
            or (isinstance(n, ast.Constant) and isinstance(n.value, str) and "outcomes.jsonl" in n.value)]
    assert len(hits) == 2


def test_learning_is_unchanged_by_recorded_outcomes(home):
    from apolaki.engine import ApolakiEngine
    from apolaki.grants import outcomes as o
    plant(o)

    class Agent:
        calls = []

        def draft_content(self, brief, content_type="social", reviewer_feedback="", **kw):
            self.calls.append(reviewer_feedback)
            return {"draft": "D", "status": "queued_for_review", "queue_id": "q"}
    agent = Agent()
    e = ApolakiEngine(agent, learn=True)
    e.draft("A grant concept paper", "grant")
    assert e.bdi.list_beliefs() == [] and agent.calls == [""]
