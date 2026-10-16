#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style_pass.py: the two-instrument house AI-writing pass (from Liberation Labs).

    instrument 1  style_lint.py   sections 1-3 (mechanical: counts, line numbers, matched text)
    instrument 2  Apolaki         sections 4-7, plus the judgement half of section 2

The split exists because of 2026-09-23: Apolaki, handed the whole pass on a
client proposal, reported "clean across all categories" while grep found
6 em dashes, 13 negative parallelisms and 13 bold spans in the same text.
Here Apolaki is never asked to count and never asked for a verdict on
sections 1-3. The linter's enumeration is its INPUT: it judges each listed
instance, then reads sections 4-7 over the whole piece. If its output contains
a verdict on a mechanical category anyway, that is reported as a MALFUNCTION
(exit 3) and never passed through as a result.

Run from the repository root, on the machine that serves the model:

    APOLAKI_MODEL=qwen3.8:27b-mxfp8 python -m apolaki.agent.style_pass FILE
        [--lint-only] [--dry-run] [--json-out OUT.json] [--hub-page]
        [--model NAME] [--max-tokens N] [--temperature T] [--use-respond]
        [--curly-ok-in-html] [--guide PATH]

Exit status:
    0  mechanical gate passed and Apolaki delivered judgement without malfunction
    1  mechanical gate failed (the linter found hits); judgement still delivered
    2  error: linter failure, model error, or an empty/unusable model answer
    3  MALFUNCTION: Apolaki rendered a verdict on a mechanical category
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
LINTER = os.path.join(HERE, "style_lint.py")
DEFAULT_MODEL = "qwen3.8:27b-mxfp8"
VERSION = "1.0.0"

# Sections 4-7 of the house AI-writing pass. Section 5 is the deployment's own voice, from its brand file
# (config.brand()["voice"]); pass --guide PATH to use a deployment's full guide instead.
GUIDE_4_7_TEMPLATE = """\
## 4. Content patterns
Inflated significance. "Marking a pivotal moment in", "reflecting broader trends in", "sparked
conversations about", "has emerged as a key player". Delete. Say the specific thing the subject
does and let the reader judge its size.
Vague attribution. "Widely regarded as", "experts say", "is of interest to researchers", "has
been praised for". Name the source or cut the claim. This also violates our own standard of
measurement over assertion.
The challenges-and-future-prospects formula. A closing paragraph shaped as "Despite these
strengths, X faces challenges... the future lies in its ability to..." Recognisable at a glance,
and it says nothing.
Promotional drift. Copy that reads like a travel brochure. Our register is a sharp organiser who
reads research papers, not a brochure.

{voice_section}
## 6. The hub-page rule
On a hub or roster page, say what a thing IS, not what it does. The category is what lets a
reader locate themselves; what it does belongs on its own page. "Eldercare companion for seniors
and their caregivers" (right) versus "Cura calls elders daily to confirm medication" (weak).

## 7. Claims discipline
Any claim about capability, price, time or performance needs the caveat that makes it true. If a
thing can do something we would not recommend, say both: capable, not recommended.
"""


def default_guide_4_7() -> str:
    from . import config
    voice = config.brand()["voice"].strip()
    if not voice.startswith("## "):
        voice = "## 5. Voice\n" + voice
    voice = re.sub(r"^## [^\n]*", "## 5. House voice", voice, count=1)
    return GUIDE_4_7_TEMPLATE.format(voice_section=voice)


# ---------------------------------------------------------------------------
# Instrument 1: the linter, run as the same subprocess a human would run.
# ---------------------------------------------------------------------------

def run_linter(target: str, curly_ok_in_html: bool) -> Dict[str, Any]:
    cmd = [sys.executable, LINTER, "--json", target]
    if curly_ok_in_html:
        cmd.append("--curly-ok-in-html")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError("style_lint.py failed (exit %d):\n%s" % (proc.returncode, proc.stderr.strip()))
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError("style_lint.py emitted unparseable JSON: %s" % e)


def render_lint_report(lint: Dict[str, Any]) -> str:
    # Re-render through the linter's own renderer so the two views never drift.
    from . import style_lint
    return style_lint.render_report(lint, context=True)


# ---------------------------------------------------------------------------
# Instrument 2: Apolaki. Two prompts, both built from the linter's output.
# ---------------------------------------------------------------------------

def load_guide_4_7(path: Optional[str]) -> str:
    if not path:
        return default_guide_4_7()
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^## 4\..*?(?=^## How to run|\Z)", text, flags=re.S | re.M)
    if not m:
        raise RuntimeError("could not find sections 4-7 in %s" % path)
    return re.sub(r"\n-{3,}\n", "\n", m.group(0)).strip() + "\n"


def build_stage_a(lint: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    np_hits = lint["checks"]["negative_parallelism"]["hits"]
    bold_hits = lint["checks"]["bold"]["hits"]
    parts: List[str] = []
    parts.append(
        "You are doing the judgement half of the house AI-writing pass on the piece of "
        "copy at the end of this message.\n\n"
        "A mechanical linter has already enumerated every instance listed below. The counts are "
        "settled facts, produced by a script that read the file. Do not re-count them, do not "
        "dispute them, do not look for more, and do not comment on em dashes, quotation marks, "
        "headings, vocabulary or verbs: none of that is your job in this pass.\n"
    )
    if np_hits:
        parts.append(
            "PART 1. NEGATIVE PARALLELISM: %d instance%s.\n"
            "The rule: say the positive claim once; if the contrast is doing real work, give it its "
            "own sentence.\n"
            "For EACH instance decide whether it is doing real work, displacing an assumption this "
            "reader actually holds, or whether it is reflex. Keep the ones that earn it. Use exactly "
            "this format, one line per instance, every instance, no preamble and no summary:\n\n"
            "NP#<n>: KEEP | <one sentence: which assumption it displaces>\n"
            "NP#<n>: CUT | <one sentence: why it is reflex, and the positive claim to keep>\n\n"
            "Instances:" % (len(np_hits), "" if len(np_hits) == 1 else "s")
        )
        for i, h in enumerate(np_hits, 1):
            parts.append('NP#%d (line %d, form "%s"): "%s"' % (i, h["line"], h["form"].replace("_", " "), h["sentence"]))
        parts.append("")
    if len(bold_hits) > 1:
        parts.append(
            "PART 2. BOLD: %d spans. The rule: bold marks one thing on a page, not a theme. Choose the "
            "ONE span that earns it. Format, one line:\n\n"
            "BOLD: keep #<k> | <one sentence why>\n\n"
            "Spans:" % len(bold_hits)
        )
        for i, h in enumerate(bold_hits, 1):
            parts.append('B#%d (line %d): "%s"' % (i, h["line"], h["text"]))
        parts.append("")
    parts.append("--- THE PIECE (context only; do not review it here) ---")
    parts.append(lint["prose"])
    parts.append("--- END ---")
    return "\n".join(parts), np_hits, bold_hits


def build_stage_b(lint: Dict[str, Any], guide_4_7: str, hub_page: bool) -> str:
    hub = ("This piece IS a hub or roster page: apply section 6 to every roster line."
           if hub_page else
           "This piece is NOT a hub or roster page unless the text itself is a roster; if it is not, "
           "write exactly \"not applicable\" under section 6.")
    return (
        "Read the WHOLE piece of copy at the end of this message against the judgement sections of "
        "the house AI-writing pass, reproduced below.\n\n"
        "Sections 1 to 3 of the guide (punctuation, typography, bold, headings, sentence shapes, "
        "banned vocabulary, marketing verbs) were checked by a script and are not part of this pass. "
        "Do not count anything. Do not report on em dashes, quotation marks, bold, headings, banned "
        "words or verbs, and do not say any of those is clean: a script has already produced those "
        "numbers, and your job is the part a script cannot do.\n\n"
        "Report only what you actually find, quoting the exact text each time. If a section has no "
        "findings, write exactly \"no findings\" under its heading. Do not invent findings to seem "
        "thorough. Be terse.\n\n"
        "Use these headings, in this order:\n"
        "## 2b. Rule of three\n"
        "(three adjectives, or three short parallel phrases, used to make a thin point sound thorough)\n"
        "## 4a. Inflated significance\n"
        "## 4b. Vague attribution\n"
        "## 4c. Challenges-and-future-prospects closing\n"
        "## 4d. Promotional drift\n"
        "## 5. Voice\n"
        "## 6. Hub-page rule\n"
        "## 7. Claims discipline\n\n"
        "%s\n\n"
        "Format for each finding: quote, then problem, then fix, one line each.\n\n"
        "The guide, sections 4 to 7:\n\n%s\n"
        "--- THE PIECE ---\n%s\n--- END ---"
    ) % (hub, guide_4_7, lint["prose"])


def load_apolaki(model: Optional[str]) -> Tuple[Any, Any]:
    # MODEL is read at import time in apolaki_agent, so the environment must be
    # settled before the import.
    if model:
        os.environ["APOLAKI_MODEL"] = model
    else:
        os.environ.setdefault("APOLAKI_MODEL", DEFAULT_MODEL)
    from . import agent as apolaki_agent
    agent = apolaki_agent.ApolakiAgent()
    return apolaki_agent, agent


def ask(mod: Any, agent: Any, prompt: str, temperature: float, max_tokens: int, use_respond: bool) -> str:
    """Send one prompt to Apolaki.

    Default path: apolaki_agent.chat() with the agent's own system prompt and
    loaded memory context (agent.history). ApolakiAgent.respond() is avoided
    because (a) it auto-stores a "decision" memory whenever the prompt contains
    substrings like "decide" or "plan", which any reviewed text can contain, so
    every review would pollute Apolaki's memory; (b) it caps num_predict at
    2048, which a per-instance judgement over a long list can exceed; (c) its
    ethics re-roll is for drafts, not reviews. --use-respond restores it.
    """
    if use_respond:
        return agent.respond(prompt)
    messages = list(agent.history) + [{"role": "user", "content": prompt}]
    return mod.chat(messages, temperature=temperature, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Malfunction detection: a verdict on sections 1-3 in the model's output.
# ---------------------------------------------------------------------------

MECH_TERMS_FULL = (
    r"em[\s-]?dash|en[\s-]?dash|\bdashes\b|curly|smart[\s-]?quot|straight[\s-]?quot|typograph|"
    r"apostroph|\bbold(?:face|ing|ed)?\b|\bemphasi[sz]|heading case|title case|sentence case|"
    r"\bemoji|negative parallel|parallelism|banned (?:vocab|word|term)|\bvocabulary\b|never-list|"
    r"\bcopula|serves as|stands as|marketing verb"
)
# In stage A the model is told about negative parallelism and bold, so those
# terms alone are not evidence of a verdict.
MECH_TERMS_STAGE_A = (
    r"em[\s-]?dash|en[\s-]?dash|\bdashes\b|curly|smart[\s-]?quot|straight[\s-]?quot|typograph|"
    r"apostroph|heading case|title case|sentence case|\bemoji|banned (?:vocab|word|term)|"
    r"\bvocabulary\b|never-list|\bcopula|serves as|stands as|marketing verb"
)
VERDICT_TERMS = (
    r"\bclean\b|\bzero\b|\bnone\b|no (?:instances?|occurrences?|em dashes|curly|bold|issues?|problems?|violations?)|"
    r"not (?:found|present|used|detected)|used sparingly|\bsparingly\b|well within|\bacceptable\b|"
    r"\bappropriate(?:ly)?\b|nothing to flag|\bi (?:count|counted|found|see|detect)\b|"
    r"\b\d+ (?:em dashes|instances|occurrences|bold spans|curly)|there (?:is|are) (?:no|zero|\d+)|"
    r"\bfine\b|\bok(?:ay)?\b|\bpasses\b|\bcompliant\b|\bminimal\b|\bmoderate\b|\bexcessive\b|\boverused\b"
)
COUNT_CLAIM = (
    r"\bi (?:count|counted|find|found|see|detect|identified|noticed) (?:only )?(?:\d+|no|zero|one|two|three|four|five|six|seven|eight|nine|ten|several|a few|many)\b|"
    r"\bthere (?:is|are) (?:no|zero|only |\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
)
GLOBAL_VERDICT = (
    r"clean across|all categories|(?:the )?(?:text|copy|piece|proposal|document|draft) (?:is|looks|reads) clean|"
    r"overall,? (?:it is )?clean|no (?:issues|problems) (?:found|detected) (?:in|across) (?:any|all)"
)
STAGE_A_LINE = re.compile(r"^\s*(?:NP#\d+\s*:\s*(?:KEEP|CUT)\b|BOLD\s*:\s*keep\s*#?\d+)", re.I)
HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s+|\d+[.)]\s+\S)")
BOLD_ONLY_LINE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:?\s*$")


def detect_malfunction(text: str, stage: str) -> List[Dict[str, str]]:
    """Scan a model answer for verdicts or counts on sections 1-3.

    A line is a hit when it names a mechanical category and carries a verdict
    or count word, when it is a global "clean" verdict, or when it carries a
    verdict word underneath a heading that names a mechanical category (the
    2026-09-23 audit put "Clean." on the line after a "NEGATIVE PARALLELISM"
    heading). In stage A the model has been told about negative parallelism
    and bold, so those two terms alone are not evidence on a line that follows
    the requested "NP#n: KEEP|CUT" format; every other line is held to the
    full list.
    """
    hits: List[Dict[str, str]] = []
    if not text:
        return hits
    heading_terms = MECH_TERMS_STAGE_A if stage == "A" else MECH_TERMS_FULL
    under_mech_heading = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        on_format = stage == "A" and bool(STAGE_A_LINE.match(line))
        is_heading = bool(HEADING_LINE.match(line)) or (line.isupper() and len(line) > 3)
        # A bold-only line is a heading when it names a category ("**Em dashes**")
        # and a verdict when it does not ("**Clean.**").
        if BOLD_ONLY_LINE.match(line) and re.search(heading_terms, line, re.I):
            is_heading = True
        if is_heading:
            under_mech_heading = bool(re.search(heading_terms, line, re.I))
        mech = MECH_TERMS_STAGE_A if on_format else MECH_TERMS_FULL
        reason = None
        if re.search(GLOBAL_VERDICT, line, re.I):
            reason = "global clean verdict"
        elif re.search(mech, line, re.I) and re.search(VERDICT_TERMS, line, re.I):
            reason = "verdict on a mechanical category"
        elif re.search(mech, line, re.I) and re.search(COUNT_CLAIM, line, re.I):
            reason = "count claim on a mechanical category"
        elif under_mech_heading and not on_format and not is_heading and (
                re.search(VERDICT_TERMS, line, re.I) or re.search(COUNT_CLAIM, line, re.I)):
            reason = "verdict under a mechanical-category heading"
        if reason:
            hits.append({"stage": stage, "reason": reason, "line": line[:240]})
    return hits


def parse_stage_a(raw: str, np_hits: List[Dict[str, Any]], bold_hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    verdicts: Dict[int, Dict[str, str]] = {}
    for m in re.finditer(r"^\s*NP#(\d+)\s*:\s*(KEEP|CUT)\b\s*\|?\s*(.*)$", raw, flags=re.I | re.M):
        n = int(m.group(1))
        if 1 <= n <= len(np_hits) and n not in verdicts:
            verdicts[n] = {"verdict": m.group(2).upper(), "reason": m.group(3).strip()}
    bold = None
    m = re.search(r"^\s*BOLD\s*:\s*keep\s*#?(\d+)\s*\|?\s*(.*)$", raw, flags=re.I | re.M)
    if m:
        k = int(m.group(1))
        bold = {"keep": k, "reason": m.group(2).strip(),
                "span": bold_hits[k - 1]["text"] if 1 <= k <= len(bold_hits) else None}
    rows = []
    for i, h in enumerate(np_hits, 1):
        v = verdicts.get(i)
        rows.append({"n": i, "line": h["line"], "form": h["form"], "sentence": h["sentence"],
                     "verdict": v["verdict"] if v else "UNANSWERED",
                     "reason": v["reason"] if v else ""})
    return {"negative_parallelism": rows, "bold": bold,
            "answered": len(verdicts), "expected": len(np_hits)}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

BAR = "=" * 78
BANG = "!" * 78


def malfunction_banner(hits: List[Dict[str, str]]) -> str:
    lines = [BANG,
             "!! MALFUNCTION: Apolaki rendered a verdict or a count on a mechanical category",
             "!! (sections 1-3). It was never asked to. This is the exact failure this pipeline",
             "!! exists to catch (2026-09-23: \"clean across all categories\" against 6/13/13",
             "!! from grep). The style_lint.py numbers above are authoritative; the model's",
             "!! statements below are to be IGNORED, not weighed.",
             "!! Offending lines:"]
    for h in hits:
        lines.append("!!   [stage %s, %s] %s" % (h["stage"], h["reason"], h["line"]))
    lines.append("!! Exit status 3.")
    lines.append(BANG)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Two-instrument AI-writing pass: style_lint.py, then Apolaki.")
    ap.add_argument("file", help="text, Markdown or HTML file to review")
    ap.add_argument("--lint-only", action="store_true", help="run instrument 1 only")
    ap.add_argument("--dry-run", action="store_true", help="build and print the Apolaki prompts; do not call the model")
    ap.add_argument("--json-out", help="write the combined result as JSON to this path")
    ap.add_argument("--hub-page", action="store_true", help="tell Apolaki the hub-page rule (section 6) applies")
    ap.add_argument("--model", default=None, help="Ollama model name (default: $APOLAKI_MODEL or %s)" % DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=2500)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--use-respond", action="store_true", help="route through ApolakiAgent.respond() (see ask())")
    ap.add_argument("--curly-ok-in-html", action="store_true", help="pass through to style_lint.py")
    ap.add_argument("--guide", default=None, help="a deployment's full style guide to read sections 4-7 from")
    args = ap.parse_args(argv)

    t0 = time.time()
    result: Dict[str, Any] = {"tool": "style_pass", "version": VERSION, "file": args.file,
                              "instruments": {"mechanical": "style_lint.py", "judgement": None}}

    # ---- instrument 1 ----------------------------------------------------
    try:
        lint = run_linter(args.file, args.curly_ok_in_html)
    except RuntimeError as e:
        print("style_pass: %s" % e, file=sys.stderr)
        return 2
    result["lint"] = {k: v for k, v in lint.items() if k != "prose"}
    mech_pass = lint["gate"]["pass"]

    print(BAR)
    print("LIBERATION LABS AI-WRITING PASS: two instruments")
    print("file: %s   format: %s   prose chars: %d   words: %d" % (
        args.file, lint["format"], lint["stats"]["prose_chars"], lint["stats"]["words"]))
    print("instrument 1: style_lint.py %s (sections 1-3, mechanical)" % lint["version"])
    print(BAR)
    print()
    print("[INSTRUMENT 1: MECHANICAL, style_lint.py]")
    print(render_lint_report(lint))
    print()

    if args.lint_only:
        result["result"] = {"mechanical_pass": mech_pass, "judgement": "skipped (--lint-only)", "exit": 0 if mech_pass else 1}
        _write_json(args.json_out, result)
        return 0 if mech_pass else 1

    # ---- instrument 2 ----------------------------------------------------
    try:
        guide_4_7 = load_guide_4_7(args.guide)
    except (OSError, RuntimeError) as e:
        print("style_pass: %s" % e, file=sys.stderr)
        return 2
    prompt_a, np_hits, bold_hits = build_stage_a(lint)
    need_a = bool(np_hits) or len(bold_hits) > 1
    prompt_b = build_stage_b(lint, guide_4_7, args.hub_page)
    result["stage_a"] = {"needed": need_a, "prompt_chars": len(prompt_a)}
    result["stage_b"] = {"prompt_chars": len(prompt_b)}

    if args.dry_run:
        print("[INSTRUMENT 2: JUDGEMENT, Apolaki] DRY RUN: prompts only, model not called")
        print()
        if need_a:
            print("--- stage A prompt (%d chars) ---" % len(prompt_a))
            print(prompt_a)
            print()
        else:
            print("--- stage A: nothing to judge (no negative parallelism, at most one bold span) ---")
        print("--- stage B prompt (%d chars) ---" % len(prompt_b))
        print(prompt_b)
        result["result"] = {"mechanical_pass": mech_pass, "judgement": "dry run", "exit": 0 if mech_pass else 1}
        _write_json(args.json_out, result)
        return 0 if mech_pass else 1

    try:
        mod, agent = load_apolaki(args.model)
    except Exception as e:  # import or memory-store failure
        print("style_pass: could not load Apolaki: %s" % e, file=sys.stderr)
        return 2
    model_name = mod.MODEL
    result["instruments"]["judgement"] = "Apolaki / %s" % model_name
    print("[INSTRUMENT 2: JUDGEMENT, Apolaki / %s] (sections 4-7, plus judgement over the enumerated instances)" % model_name)
    print()

    malfunctions: List[Dict[str, str]] = []
    errors: List[str] = []

    # stage A
    if need_a:
        ta = time.time()
        raw_a = ask(mod, agent, prompt_a, args.temperature, args.max_tokens, args.use_respond)
        dt_a = time.time() - ta
        result["stage_a"].update({"raw": raw_a, "seconds": round(dt_a, 1)})
        if not raw_a.strip() or raw_a.startswith("[LLM Error"):
            errors.append("stage A: model returned %s" % (repr(raw_a[:160]) if raw_a else "nothing"))
        else:
            parsed = parse_stage_a(raw_a, np_hits, bold_hits)
            result["stage_a"]["parsed"] = parsed
            malfunctions.extend(detect_malfunction(raw_a, "A"))
            print("-- stage A: negative parallelism, per instance (%d enumerated by the linter; %d answered; %.0fs) --" % (
                parsed["expected"], parsed["answered"], dt_a))
            for row in parsed["negative_parallelism"]:
                print("  NP#%-2d L%-4d %-6s | %s" % (row["n"], row["line"], row["verdict"], row["reason"] or "(no reason given)"))
                print("            \"%s\"" % row["sentence"][:160])
            keep = sum(1 for r in parsed["negative_parallelism"] if r["verdict"] == "KEEP")
            cut = sum(1 for r in parsed["negative_parallelism"] if r["verdict"] == "CUT")
            un = sum(1 for r in parsed["negative_parallelism"] if r["verdict"] == "UNANSWERED")
            print("  summary: keep %d, cut %d, unanswered %d" % (keep, cut, un))
            if np_hits and parsed["answered"] == 0:
                errors.append("stage A: no per-instance verdicts could be parsed from the model's answer")
            if len(bold_hits) > 1:
                print("-- stage A: bold (%d spans enumerated by the linter) --" % len(bold_hits))
                if parsed["bold"]:
                    print("  keep #%d: \"%s\" | %s" % (parsed["bold"]["keep"], parsed["bold"]["span"], parsed["bold"]["reason"]))
                else:
                    print("  (no BOLD line parsed from the model's answer)")
            off = [ln for ln in raw_a.splitlines() if ln.strip() and not STAGE_A_LINE.match(ln)]
            if off:
                print("-- stage A: lines outside the requested format (%d) --" % len(off))
                for ln in off[:12]:
                    print("  | %s" % ln.strip()[:200])
            print()
    else:
        print("-- stage A: nothing to judge (no negative parallelism; at most one bold span) --")
        print()

    # stage B
    tb = time.time()
    raw_b = ask(mod, agent, prompt_b, args.temperature, args.max_tokens, args.use_respond)
    dt_b = time.time() - tb
    result["stage_b"].update({"raw": raw_b, "seconds": round(dt_b, 1)})
    if not raw_b.strip() or raw_b.startswith("[LLM Error"):
        errors.append("stage B: model returned %s" % (repr(raw_b[:160]) if raw_b else "nothing"))
    else:
        malfunctions.extend(detect_malfunction(raw_b, "B"))
        print("-- stage B: sections 4-7, free judgement over the whole piece (%.0fs) --" % dt_b)
        for ln in raw_b.splitlines():
            print("  | %s" % ln.rstrip())
        print()

    try:
        agent.close()
    except Exception:
        pass

    # ---- verdict ---------------------------------------------------------
    result["malfunction"] = {"detected": bool(malfunctions), "hits": malfunctions}
    result["errors"] = errors
    print("[MALFUNCTION CHECK] %s" % ("none detected: Apolaki stayed inside sections 4-7 and the enumerated instances"
                                      if not malfunctions else "DETECTED (%d)" % len(malfunctions)))
    if malfunctions:
        print(malfunction_banner(malfunctions))
    print()
    print(BAR)
    if errors:
        for e in errors:
            print("ERROR: %s" % e)
        print("RESULT: judgement NOT delivered; an absent answer is not a pass. Exit 2.")
        code = 2
    elif malfunctions:
        print("RESULT: mechanical gate %s (%d hits) | judgement TAINTED by malfunction | exit 3" % (
            "PASS" if mech_pass else "FAIL", lint["gate"]["total_hits"]))
        code = 3
    else:
        print("RESULT: mechanical gate %s (%d hits) | judgement delivered | malfunction none | exit %d" % (
            "PASS" if mech_pass else "FAIL", lint["gate"]["total_hits"], 0 if mech_pass else 1))
        code = 0 if mech_pass else 1
    print("elapsed %.0fs" % (time.time() - t0))
    print(BAR)
    result["result"] = {"mechanical_pass": mech_pass, "exit": code}
    _write_json(args.json_out, result)
    return code


def _write_json(path: Optional[str], result: Dict[str, Any]) -> None:
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print("json written to %s" % path)


if __name__ == "__main__":
    sys.exit(main())
