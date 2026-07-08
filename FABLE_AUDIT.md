# Project Apolaki — Fable Audit

**Date:** 2026-07-07
**Auditor:** CC, via Fable 5 background session
**Repo state:** `main` @ `19d5aee` (Red team testing), clean working tree, 3 commits total

---

## What It Is

Apolaki is a **prosocial marketing engine scaffold** built on Kintsugi engine patterns
(EFE scoring, fast two-stage classification, skill chips). It routes marketing requests
across 10 domains (web design, social media, content, leads, partnerships, media
relations, fundraising, brand strategy, analytics, community) with ethics guardrails
that hard-block dark patterns and escalate sensitive actions for human review.

Scale check: the "1086 files" figure is misleading — **~4,800 files on disk are almost
entirely `.venv/`** (correctly untracked and gitignored). The actual project is
**~1,930 lines of Python across 20 source files**:

| Component | File | LOC | State |
|-----------|------|-----|-------|
| Fast classifier | `apolaki/cognition/fast_classifier.py` | 298 | Working |
| Router | `apolaki/cognition/router.py` | 335 | Working (v1) |
| EFE calculator | `apolaki/cognition/efe.py` | 177 | Working |
| Ethics engine | `apolaki/security/ethics.py` | 189 | Working |
| 10 skill chips | `apolaki/skills/*/chip.py` | ~570 | **Stubs** |
| Tests | `tests/` | 361 | 47 tests, all passing |

## What Works

**Verified by running the suite: `47 passed in 2.32s` (pytest, project venv).**

1. **Ethics engine** (`security/ethics.py`) — regex-based detection across 5 dark-pattern
   categories (false urgency, guilt manipulation, astroturfing, spam, deceptive metrics)
   and 4 escalation categories (financial asks, public comms, sensitive topics, data
   sharing). Blocked verdicts come with concrete ethical alternatives — a genuinely nice
   touch. Solid for the phrasings it anticipates.

2. **Two-stage fast classifier** (`cognition/fast_classifier.py`) — deny check always
   runs first (correct ordering: red team confirms deny overrides high keyword
   confidence), then escalation keywords, then multi-domain ambiguity detection, then
   fast-allow. Clean design lifted from the Kintsugi/YOLO-classifier pattern.

3. **Router with EFE disambiguation** (`cognition/router.py`) — keyword table
   (~70 keywords → 10 domains), fast-classifier pre-screen, per-domain EFE weight
   profiles for escalated/ambiguous requests. Red team sanity tests confirm correct
   domain assignment for representative requests.

4. **Red team suite** (`tests/test_red_team.py`) — 31 adversarial tests covering dark
   pattern evasion, escalation bypass, combined attacks (denied action buried in allowed
   one), and classifier manipulation. This is the strongest part of the repo relative to
   its maturity: the commit history shows it already caught and patched 2 vulnerabilities.

5. **Hygiene** — clean package layout, `.venv` untracked, frozen dataclasses, weight-sum
   validation in `EFEWeights.__post_init__`, pyproject with pinned dev deps. Installs and
   tests run first try.

## What's Broken / Missing

Ordered by severity.

### 1. The skill chips do nothing (blocker)
Every chip's `handle()` returns a static dict: `{"status": "ready", "actions": [], "notes": [...config echo...]}`.
No LLM calls, no tool use, no content generation. The entire value proposition —
actually doing marketing work — is unimplemented. Apolaki currently classifies requests
it cannot fulfill.

### 2. Nothing composes the pipeline (blocker)
The README architecture is `Request → FastClassifier → Router → Chip → Ethics-on-output → Review gate`,
but no orchestrator exists. Router, chips, and ethics engine are only ever wired
together inside tests. Critically, **the ethics engine is never run on chip *output*** —
only on the incoming request in tests. The "final guardrail check on output" from the
README is aspirational.

### 3. No human review gate
`EthicsVerdict.FLAGGED` exists and fires correctly, but flagged items go nowhere. There
is no queue, no notification, no approval workflow. Human-in-the-loop is the load-bearing
safety property for financial asks and public comms; right now it's an enum value.

### 4. Empty promised directories
`apolaki/adapters/` and `apolaki/config/` exist but are empty (no files, not even
`__init__.py`), and `docs/` is empty despite CLAUDE.md promising "playbooks, guides"
and "Discord, Slack, web integrations". CLAUDE.md oversells the current state.

### 5. Regex-only ethics is brittle by design
The red team tests pass because they test phrasings the regexes were written against.
Trivial paraphrases sail through: "write a glowing quote pretending you tried our
product" (astroturfing, no pattern match), "make the numbers look more impressive"
(deceptive metrics, no match), "time-pressure the donors" (false urgency, no match).
Regex is the right *fast path* — it is not a sufficient *only* path. The classifier
docstring already anticipates "full EFE + optional LLM" for Stage 2; no LLM exists
anywhere in the codebase (only dependency is pydantic, which is itself unused).

### 6. Minor bugs
- **Keyword substring matching** (`router.py:281`): `re.findall(re.escape(keyword))`
  matches inside words and across phrases — "copy" matches "copyright", and "social
  media post" hits both `social_media` (via "social media", "post") *and*
  `media_relations` (via the "media" inside "social media"), inflating multi-domain
  escalations. Use word boundaries and subtract overlapping-phrase hits.
- **`evaluate_batch` loses correspondence** (`ethics.py:172`): sorting checks by
  severity discards which input produced which verdict. Return `(item, check)` pairs
  or sort indices.
- **Chip docstring drift**: e.g. `fundraising/chip.py` header claims
  `risk=0.4, ambiguity=0.5, epistemic=0.6` (sums to 1.5); the real
  `FUNDRAISING_WEIGHTS` is 0.4/0.3/0.3. Several chips have similar stale headers —
  they'd fail `EFEWeights` validation if instantiated as written.
- **Escalation keywords use raw substring**: "political" matches "apolitical",
  "charge" matches "supercharged". Cheap fix with word boundaries; over-escalation is
  the safe failure direction, so low priority.

## Verdict

**A well-built skeleton with excellent safety instincts and no muscles.** The
ethics-first architecture is real (deny-before-route ordering, red team suite in the
third commit ever) and the code quality is good. But it is a routing-and-refusal system
with nothing on the other side of the route. Nothing here is *broken* — it all does what
it says — it's that the doing layer doesn't exist yet.

## Upgrade Plan

Phased, each phase shippable on its own.

### Phase 1 — Spine (unblocks everything)
1. **`apolaki/engine.py` orchestrator**: `classify → route → chip.handle → ethics.evaluate(output) → deliver | queue-for-review`. Async, returns a typed `EngineResult`. This closes gap #2 and makes gap #3 addressable.
2. **Review gate v1**: a `ReviewQueue` that persists flagged items (JSON file or SQLite) with approve/reject methods, plus a CLI to list and act on pending items. Discord adapter can consume this later.
3. Fix the minor bugs (word-boundary matching, `evaluate_batch` pairing, chip docstrings). ~1 hour of work, do it in the same PR.

### Phase 2 — Make one chip real
Pick **content_creation** (highest request volume, lowest external-integration burden).
Back `handle()` with the Claude API (`claude-sonnet-5` default; the marketer sets voice
via a config-driven system prompt). Output flows through the Phase 1 ethics-on-output
check — which is exactly where regex ethics starts earning its keep, since it now
screens *generated* content, not just requests. Prove the loop end-to-end, then
template the pattern for social_media and fundraising.

### Phase 3 — LLM ethics stage 2
Mirror the two-stage classifier: regex fast path stays (it's ~1ms and catches the
obvious), but ESCALATED/uncertain content gets an LLM judgment against the CLAUDE.md
allowed/denied lists. Extend the red team suite with paraphrase attacks (the three
examples in finding #5 above should become failing-then-passing tests). This is the
Kintsugi BoundaryGuardian pattern — port it rather than rebuild.

### Phase 4 — Adapters and config
- `config/`: `settings.py` (pydantic — finally using the dependency) + `values.yaml`
  for org voice, escalation thresholds ($1000 is currently hardcoded in a regex),
  and review-gate routing.
- `adapters/discord/`: the Coalition already runs Discord infra; wire the review queue
  and a request channel there first. Slack/web after.

### Phase 5 — Docs honesty
Fill `docs/` with a real playbook per implemented domain, or trim CLAUDE.md/README to
match reality. An ethics-forward project shouldn't have aspirational docs describing
capabilities that don't exist.

**Sequencing note:** resist implementing all 10 chips before Phase 3. Every real chip
widens the surface the regex-only ethics layer has to cover alone. Two or three real
chips + LLM ethics beats ten chips + regex.
