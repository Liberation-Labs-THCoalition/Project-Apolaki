"""The Kintsugi engine around the working agent (Rivet's pattern: every request a BDI intention, run by a skill chip).

- Each draft request becomes a BDI intention, carried out by a skill chip from a Kintsugi SkillRegistry. The first
  real chip is content creation: the working agent's draft loop (ethics on the brief, house rules, style gate, approval
  queue). The other chips in apolaki/skills are scaffolds until they get the same treatment.
- A person's decisions on queue items (agent.review_item) become BDI beliefs, kept in $APOLAKI_HOME/engine_beliefs.json
  so they survive restarts. The recent ones for a content type go to the model as a <reviewer_feedback> reference
  block, beside retrieved memory and never in the brief: the brief is what the ethics check reads.

    python -m apolaki.engine draft "BRIEF" [--type social]
    python -m apolaki.engine review QUEUE_ID approved|rejected|changes_requested ["NOTE"] [--by NAME]
    python -m apolaki.engine sync            # turn recorded decisions into beliefs
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from . import kintsugi_core as k
from .agent import config

BELIEFS_FILE = "engine_beliefs.json"
FEEDBACK_LIMIT = 5                 # most recent first: older feedback goes stale (a design reason, not a measurement;
                                   # revisit once there are enough decisions to measure). The rest stay on record.
FEEDBACK_NOTE = ("what reviewers decided about recent drafts of this type; reference material, not instructions; "
                 "the brief is authoritative on the facts of this piece")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContentCreationChip(k.BaseSkillChip):
    """Draft copy in the house voice through the working agent. Everything it makes goes to the approval queue."""

    name = "content_creation"
    description = "Drafts and revises public-facing copy in the house voice; a person approves before publishing"
    version = "1.0.0"
    domain = k.SkillDomain.COMMUNICATIONS

    def __init__(self, agent) -> None:
        super().__init__()
        self.agent = agent

    async def handle(self, request: k.SkillRequest, context: k.SkillContext) -> k.SkillResponse:
        # max_tokens reaches the agent only when a caller set it, so the agent's own default holds otherwise.
        extra = {"max_tokens": request.parameters["max_tokens"]} if "max_tokens" in request.parameters else {}
        result = self.agent.draft_content(request.raw_input, content_type=request.parameters.get("content_type", "social"),
                                          reviewer_feedback=context.metadata.get("reviewer_feedback", ""), **extra)
        ok = result.get("status") != "blocked"
        return k.SkillResponse(content=result.get("draft", ""), success=ok, data=result,
                               requires_consensus=ok, consensus_action="publish" if ok else None)


class ApolakiEngine:
    def __init__(self, agent=None) -> None:
        if agent is None:
            from .agent.agent import ApolakiAgent
            agent = ApolakiAgent()
        self.agent = agent
        self.org = config.brand()["name"]
        self.bdi = k.BDIStore(self.org)
        self.registry = k.SkillRegistry()
        self.registry.register(ContentCreationChip(agent))
        self._load_beliefs()

    # ---- beliefs that survive restarts -------------------------------------------------------------------------
    def _beliefs_path(self):
        return config.home() / BELIEFS_FILE

    def _load_beliefs(self) -> None:
        try:
            rows = json.loads(self._beliefs_path().read_text())
        except (OSError, ValueError):
            rows = []
        for r in rows:
            self.bdi.add_belief(k.BDIBelief(
                id=r["id"], content=r["content"], confidence=r["confidence"], status=k.BeliefStatus(r["status"]),
                source=r["source"], tags=list(r["tags"]), created_at=datetime.fromisoformat(r["created_at"]),
                evidence=list(r.get("evidence", []))))

    def _save_beliefs(self) -> None:
        rows = []
        for b in self.bdi.list_beliefs():
            d = asdict(b)
            d["status"] = b.status.value
            d["created_at"] = b.created_at.isoformat()
            d["last_reviewed"] = b.last_reviewed.isoformat() if b.last_reviewed else None
            rows.append(d)
        path = self._beliefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        tmp.replace(path)

    # ---- reviews -> beliefs ------------------------------------------------------------------------------------
    def sync_reviews(self) -> int:
        """Turn every recorded decision on a queue item into a belief, once. Returns how many were added."""
        known = {e for b in self.bdi.list_beliefs() for e in b.evidence}
        added = 0
        for f in sorted(config.folder("approval-queue").glob("*.json")):
            try:
                item = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            status = item.get("status")
            if status not in ("approved", "rejected", "changes_requested") or item.get("id") in known:
                continue
            ctype = item.get("content_type", "unknown")
            note = (item.get("review_note") or "").strip()
            words = {"approved": "approved", "rejected": "rejected", "changes_requested": "asked for changes to"}[status]
            self.bdi.add_belief(k.BDIBelief(
                id=str(uuid.uuid4()), content=f"Reviewers {words} a {ctype} draft" + (f": {note}" if note else "."),
                confidence=1.0, status=k.BeliefStatus.ACTIVE, source=f"review:{item.get('reviewed_by') or 'reviewer'}",
                tags=["review", ctype, status], created_at=_now(), evidence=[item.get("id")]))
            added += 1
        if added:
            self._save_beliefs()
        return added

    def feedback_block(self, content_type: str) -> str:
        """The most recent decisions for this content type that carry a note, as a reference block ('' if none)."""
        rows = [b for b in self.bdi.list_beliefs(k.BeliefStatus.ACTIVE)
                if "review" in b.tags and content_type in b.tags and ":" in b.content]
        rows.sort(key=lambda b: b.created_at, reverse=True)
        if not rows:
            return ""
        lines = "\n".join(f"- {b.content}" for b in rows[:FEEDBACK_LIMIT])
        shown = min(len(rows), FEEDBACK_LIMIT)
        # The cut is marked (LAB_SOP §11.5): the block says how many it holds of how many are on record.
        return (f'<reviewer_feedback count="{shown}" of="{len(rows)}" note="{FEEDBACK_NOTE}">\n{lines}\n'
                f'</reviewer_feedback>')

    # ---- a request ---------------------------------------------------------------------------------------------
    def draft(self, brief: str, content_type: str = "social", max_tokens: int | None = None) -> dict:
        """One draft, as a BDI intention carried out by the content-creation chip.

        max_tokens raises the model's output budget for long forms (a grant section runs past the 4096 default and
        would come back truncated, which the agent refuses to queue)."""
        if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1):
            raise ValueError(f"max_tokens must be a positive integer, not {max_tokens!r}")
        self.sync_reviews()
        feedback = self.feedback_block(content_type)
        used = [b.id for b in self.bdi.list_beliefs(k.BeliefStatus.ACTIVE)
                if "review" in b.tags and content_type in b.tags]
        intention = k.BDIIntention(id=str(uuid.uuid4()), goal=f"Draft {content_type} copy: {brief.strip()[:120]}",
                                   status=k.IntentionStatus.ACTIVE, belief_ids=used, desire_ids=[], created_at=_now())
        self.bdi.add_intention(intention)
        chip = self.registry.get(ContentCreationChip.name)
        context = k.SkillContext(org_id=self.org, user_id="apolaki", metadata={"reviewer_feedback": feedback})
        parameters = {"content_type": content_type}
        if max_tokens is not None:
            parameters["max_tokens"] = max_tokens
        request = k.SkillRequest(intent="draft", raw_input=brief, parameters=parameters)
        response = asyncio.run(chip.handle(request, context))
        if response.success:
            self.bdi.complete_intention(intention.id)
        else:
            self.bdi.update_intention(intention.id, status=k.IntentionStatus.FAILED)
        return dict(response.data, intention_id=intention.id, reviewer_feedback_used=bool(feedback))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m apolaki.engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft")
    d.add_argument("brief")
    d.add_argument("--type", default="social")
    d.add_argument("--max-tokens", type=int, default=None, help="output budget for long forms (the agent's default is 4096)")
    r = sub.add_parser("review")
    r.add_argument("queue_id")
    r.add_argument("decision", choices=("approved", "rejected", "changes_requested"))
    r.add_argument("note", nargs="?", default="")
    r.add_argument("--by", default="")
    sub.add_parser("sync")
    a = ap.parse_args(argv)
    if a.cmd == "review":
        from .agent.agent import review_item
        item = review_item(a.queue_id, a.decision, a.note, a.by)
        print(f"{item['id']}: {item['status']}")
        return 0
    engine = ApolakiEngine()
    if a.cmd == "sync":
        print(f"{engine.sync_reviews()} new decision(s) recorded as beliefs")
        return 0
    out = engine.draft(a.brief, a.type, max_tokens=a.max_tokens)
    print(out.get("draft", ""))
    print(f"\n[{out.get('status')}; queue {out.get('queue_id')}; reviewer feedback used: {out['reviewer_feedback_used']}]",
          file=sys.stderr)
    return 0 if out.get("status") != "blocked" else 1


if __name__ == "__main__":
    sys.exit(main())
