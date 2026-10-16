"""Apolaki Fact Checker — adversarial verification agent.

Reads a draft document, extracts every factual claim, classifies
each as PUBLISHED/EXTERNAL/UNSOURCED/PROJECTION, and flags
likely confabulations.

Run after any Apolaki draft before human review.
"""
import os
import json
import requests

from . import config

OLLAMA_BASE = config.ollama_base()
# Same model family as the agent; a thinking model. Until 2026-09-23 this defaulted
# to qwen3:30b-a3b, read only message.content, and never set think=false, so a run
# whose token budget went to reasoning returned "" -> no JSON -> [] -> "0 suspect
# claims": a clean verdict on an absence, the same defect the agent had.
MODEL = os.environ.get("CHECKER_MODEL", os.environ.get("APOLAKI_MODEL", "qwen3.8:27b-mxfp8"))

_CHECKER_TEMPLATE = """You are a hostile fact-checker. Your ONLY job is to find claims that might be fabricated.

Read this document and extract EVERY factual claim (statistics, percentages, dollar amounts, user counts, accuracy metrics, market sizes, citations to external organizations).

For each claim, classify it:
{verified_line}
- EXTERNAL: Cites an external source (McKinsey, NHS, Gartner, EU regulation). Flag for human verification — might be real, might be fabricated.
- SUSPECT: A specific number with no clear source. Likely confabulated. Flag with HIGH priority.
- PROJECTION: Future-looking claim (revenue projections, target metrics). Label clearly.

Output as JSON array:
[
  {{"claim": "exact text of claim", "classification": "VERIFIED|EXTERNAL|SUSPECT|PROJECTION", "reason": "why you classified it this way", "priority": "HIGH|MEDIUM|LOW"}}
]

Be paranoid. If in doubt, flag it. False positives are better than letting a fabricated statistic into a grant application.

DOCUMENT:
"""


def checker_prompt() -> str:
    """The checker's prompt with the brand file's verified numbers (key "verified_claims")."""
    b = config.brand()
    line = b.get("verified_claims_line") or (
        "- VERIFIED: Cites the organization's own published work with specific numbers it lists as verified: "
        + (b.get("verified_claims") or "none are configured, so nothing is VERIFIED on that ground") + ". These are REAL.")
    return _CHECKER_TEMPLATE.format(verified_line=line)



def check_document(text: str) -> list[dict] | None:
    """Run fact-checker on a document.

    Returns a list of claims, or None when the checker produced nothing parseable.
    None is not an empty list: an empty list means "no claims found", None means
    "the check did not happen". Callers must not report None as zero.
    """
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": checker_prompt() + text[:6000]}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.1, "num_predict": 3000},
            },
            timeout=600,
        )
        data = resp.json()
        if data.get("error"):
            print(f"Checker error: {data['error']}")
            return None
        msg = data.get("message", {}) or {}
        content = (msg.get("content") or "").strip()
        for close in ("</think>", "</thinking>"):
            if close in content:
                content = content.rsplit(close, 1)[1]
        if not content:
            print("Checker error: model returned no content (keys: %s)" % ",".join(sorted(msg.keys())))
            return None
        if data.get("done_reason") == "length":
            print("Checker warning: output truncated at num_predict; claim list may be incomplete")
        import re
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
            except json.JSONDecodeError as e:
                print(f"Checker error: JSON did not parse ({e}); raw head: {content[:200]!r}")
                return None
            return parsed if isinstance(parsed, list) else None
        print(f"Checker error: no JSON array in output; raw head: {content[:200]!r}")
    except Exception as e:
        print(f"Checker error: {e}")
    return None


def check_file(filepath: str) -> dict:
    """Check a grant draft file and return report."""
    with open(filepath) as f:
        text = f.read()

    claims = check_document(text)
    if claims is None:
        print(f"\n{'='*60}")
        print(f"FACT CHECK: {os.path.basename(filepath)}")
        print(f"{'='*60}")
        print("  CHECK DID NOT RUN: the checker produced no parseable claim list. Not a clean result.")
        return {"file": filepath, "checked": False, "total_claims": None,
                "verified": [], "external": [], "suspect": [], "projections": []}

    report = {
        "file": filepath,
        "checked": True,
        "total_claims": len(claims),
        "verified": [c for c in claims if c.get("classification") == "VERIFIED"],
        "external": [c for c in claims if c.get("classification") == "EXTERNAL"],
        "suspect": [c for c in claims if c.get("classification") == "SUSPECT"],
        "projections": [c for c in claims if c.get("classification") == "PROJECTION"],
    }

    print(f"\n{'='*60}")
    print(f"FACT CHECK: {os.path.basename(filepath)}")
    print(f"{'='*60}")
    print(f"  Total claims: {report['total_claims']}")
    print(f"  Verified: {len(report['verified'])}")
    print(f"  External (verify): {len(report['external'])}")
    print(f"  SUSPECT (likely fabricated): {len(report['suspect'])}")
    print(f"  Projections: {len(report['projections'])}")

    if report["suspect"]:
        print(f"\n  🚨 SUSPECT CLAIMS:")
        for c in report["suspect"]:
            print(f"    - {c['claim'][:100]}...")
            print(f"      Reason: {c['reason']}")

    if report["external"]:
        print(f"\n  ⚠️ EXTERNAL (needs human verification):")
        for c in report["external"]:
            print(f"    - {c['claim'][:100]}...")

    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        check_file(sys.argv[1])
    else:
        # Check all grant drafts
        draft_dir = str(config.folder("grant-drafts"))
        for fname in os.listdir(draft_dir):
            if fname.endswith("_draft.md"):
                check_file(os.path.join(draft_dir, fname))
