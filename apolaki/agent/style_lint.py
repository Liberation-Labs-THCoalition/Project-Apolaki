#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style_lint.py: mechanical checks for sections 1-3 of the Liberation Labs
AI-writing pass (~/.coalition/specs/brand_style_ai_writing_pass.md).

Standalone. Python 3.9+, standard library only.

Why this exists (2026-09-23): Apolaki was handed the whole pass on a client
proposal and reported "clean across all categories". grep on the same text
found 6 em dashes, 13 negative parallelisms and 13 bold spans. Counting
occurrences across thousands of characters is not what a language model
does. This script counts. Judgement (sections 4-7) stays with the model;
see style_pass.py, which feeds this script's JSON to Apolaki as input.

Usage:
    style_lint.py FILE [--json] [--format auto|html|markdown|text]
                       [--curly-ok-in-html] [--context N] [--prose]
    style_lint.py - < file.html        (read stdin; pass --format)

Exit status:
    0  no hits in any gating check
    1  one or more hits (use as a gate)
    2  usage or read error

Checks (gating unless marked advisory):
    section 1  em_dash              U+2014, plus sentences holding two or more
               curly_quote          U+201C/D, U+2018/9 and siblings (entities decoded first)
               bold                 <strong>, <b>, **x**, __x__ (CSS font-weight is invisible)
               heading_title_case   Title Case On Every Main Word, heuristic (see is_title_case)
               emoji_marker         emoji as a bullet or inside a heading
    section 2  negative_parallelism rather than / instead of / sentence-initial "Not x" /
                                    ", not x" / "not X but Y" / "not only A ... also B" /
                                    "not X -- Y"
               copula_avoidance     serves as, stands as, marks a, offers, provides,
                                    features, boasts, ventured into, began his career as
    section 3  banned_vocabulary    the never-list, with inflections
    advisory   rule_of_three_fragments  "Verifiable. Explainable. For everyone."
               en_dash_spaced           " - " (U+2013 with spaces) doing an em dash's job
               curly_quote_html         curly quotes in HTML when --curly-ok-in-html is set

Line numbers always refer to the ORIGINAL file. HTML is stripped in a way that
keeps every newline in place.
"""
from __future__ import annotations

import argparse
import bisect
import html as htmllib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0.0"
GUIDE = "brand_style_ai_writing_pass.md, sections 1-3 (2026-09-23)"

# Private-use sentinels. They survive tag stripping, are not word characters
# (so \b still works around them), and are invisible to every check except the
# one that looks for them. Distinct characters per kind so no visible letter is
# ever glued to the following word.
BOLD_STRONG = ""   # <strong>
BOLD_B = ""        # <b>
BOLD_MD = ""       # **x** / __x__
BOLD_CLOSE = ""
HEAD_LEVELS = {1: "", 2: "", 3: "", 4: "", 5: "", 6: ""}
HEAD_CLOSE = ""
BLOCK = ""         # block boundary: paragraph, list item, table cell, <br>
BOLD_OPENERS = BOLD_STRONG + BOLD_B + BOLD_MD
HEAD_OPENERS = "".join(HEAD_LEVELS.values())
ANY_SENTINEL = BOLD_OPENERS + BOLD_CLOSE + HEAD_OPENERS + HEAD_CLOSE + BLOCK
SENT_CLASS = "[-]"

EM_DASH = "—"
EN_DASH = "–"
CURLY = "“”‘’‚‛„‟"
CURLY_NAMES = {
    "“": "left double", "”": "right double", "‘": "left single",
    "’": "right single/apostrophe", "‚": "low single", "‛": "reversed single",
    "„": "low double", "‟": "reversed double",
}

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿][️‍\U0001F3FB-\U0001F3FF]*"
)


# ---------------------------------------------------------------------------
# Text preparation. Every transform keeps the newline count of what it
# replaces so line numbers in the prepared text equal line numbers in the file.
# ---------------------------------------------------------------------------

def _newlines_only(m: "re.Match[str]") -> str:
    return "\n" * m.group(0).count("\n")


def _space_keep_newlines(m: "re.Match[str]") -> str:
    return " " + "\n" * m.group(0).count("\n")


def _block_keep_newlines(m: "re.Match[str]") -> str:
    return BLOCK + "\n" * m.group(0).count("\n")


BLOCK_TAGS = (
    "p|div|li|ul|ol|table|thead|tbody|tfoot|tr|td|th|section|article|header|footer|nav|"
    "main|aside|blockquote|figure|figcaption|dl|dt|dd|pre|details|summary|form|fieldset|"
    "address|caption|title"
)


def prepare_html(src: str) -> str:
    t = src
    t = re.sub(r"<!--.*?-->", _newlines_only, t, flags=re.S)
    t = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", _newlines_only, t, flags=re.S | re.I)
    t = re.sub(r"<strong\b[^>]*>", BOLD_STRONG, t, flags=re.I)
    t = re.sub(r"<b\b[^>]*>", BOLD_B, t, flags=re.I)
    t = re.sub(r"</(?:strong|b)\s*>", BOLD_CLOSE, t, flags=re.I)
    t = re.sub(r"<h([1-6])\b[^>]*>", lambda m: HEAD_LEVELS[int(m.group(1))], t, flags=re.I)
    t = re.sub(r"</h[1-6]\s*>", HEAD_CLOSE, t, flags=re.I)
    t = re.sub(r"<(?:br|hr)\b[^<>]*/?>", _block_keep_newlines, t, flags=re.I)
    t = re.sub(r"</?(?:%s)\b[^<>]*>" % BLOCK_TAGS, _block_keep_newlines, t, flags=re.I)
    # A <span> is inline, but in card/masthead layouts spans are stacked items
    # ("<span><b>For</b> — SHN</span>" on its own line). A closing span followed
    # only by whitespace and then a tag or a line end is treated as a boundary;
    # one followed by prose stays inline.
    t = re.sub(r"</span\s*>(?=[ \t]*(?:<|\n|$))", BLOCK, t, flags=re.I)
    # Remaining tags become a space. A tag must start with a letter or '/', so a
    # bare "<" in prose ("a < b") is left alone.
    t = re.sub(r"</?[A-Za-z][^<>]*>", _space_keep_newlines, t)
    # Entities last, so literal "&lt;strong&gt;" in prose stays prose.
    t = htmllib.unescape(t)
    return t


def prepare_markdown(src: str) -> str:
    t = src
    # Fenced code is not prose.
    t = re.sub(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$", _newlines_only, t, flags=re.S | re.M)
    # Inline code spans are not prose either; keep their width.
    t = re.sub(r"`[^`\n]+`", lambda m: " " * len(m.group(0)), t)
    # ATX headings.
    t = re.sub(
        r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t#]*$",
        lambda m: HEAD_LEVELS[len(m.group(1))] + m.group(2) + HEAD_CLOSE,
        t, flags=re.M,
    )
    # Setext headings: a text line followed by === or ---.
    t = re.sub(
        r"^(?![ \t]*$)([^\n-]+)\n[ \t]{0,3}(=+|-+)[ \t]*$",
        lambda m: HEAD_LEVELS[1 if m.group(2)[0] == "=" else 2] + m.group(1).strip() + HEAD_CLOSE + "\n",
        t, flags=re.M,
    )
    # Bold: **x** / __x__. May wrap one line break, never a blank line.
    t = re.sub(
        r"\*\*(?=\S)((?:(?!\n[ \t]*\n)(?:[^*]|\*(?!\*)))+?)(?<=\S)\*\*",
        lambda m: BOLD_MD + m.group(1) + BOLD_CLOSE, t, flags=re.S,
    )
    t = re.sub(
        r"(?<!\w)__(?=\S)((?:(?!\n[ \t]*\n)(?:[^_]|_(?!_)))+?)(?<=\S)__(?!\w)",
        lambda m: BOLD_MD + m.group(1) + BOLD_CLOSE, t, flags=re.S,
    )
    # Links and images: keep the visible text.
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    # Blockquote markers.
    t = re.sub(r"^[ \t]*>[ \t]?", "", t, flags=re.M)
    # List items become blocks; the bullet marker itself is dropped.
    t = re.sub(r"^([ \t]*)(?:[-*+•]|\d+[.)])[ \t]+", lambda m: m.group(1) + BLOCK, t, flags=re.M)
    # Horizontal rules.
    t = re.sub(r"^[ \t]{0,3}(?:\*[ \t]*){3,}$|^[ \t]{0,3}(?:-[ \t]*){3,}$|^[ \t]{0,3}(?:_[ \t]*){3,}$", BLOCK, t, flags=re.M)
    return t


def detect_format(path: str, src: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm", ".xhtml"):
        return "html"
    if ext in (".md", ".markdown", ".mdown"):
        return "markdown"
    if ext in (".txt", ".text"):
        return "text"
    head = src[:4000]
    if re.search(r"<(?:p|div|h[1-6]|body|html|span|br|strong|li|title)\b", head, re.I):
        return "html"
    return "markdown"


class Doc:
    def __init__(self, path: str, src: str, fmt: str):
        self.path = path
        self.src = src
        self.fmt = fmt
        self.text = prepare_html(src) if fmt == "html" else prepare_markdown(src)
        if self.text.count("\n") != src.count("\n"):
            # Line numbers would drift; refuse rather than report wrong lines.
            # The known cause is an entity that decodes to a line break (&#10;,
            # &#13;, &NewLine;) in HTML source.
            raise RuntimeError(
                "line count changed while preparing the text (%d -> %d): an entity "
                "that decodes to a line break (such as &#10;) is present; replace it "
                "with a literal line break or a space in the source and rerun"
                % (src.count("\n"), self.text.count("\n"))
            )
        self._starts = [0] + [i + 1 for i, c in enumerate(self.text) if c == "\n"]
        self._sentences: Optional[List[Tuple[int, int]]] = None

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self._starts, pos)

    # -- rendering helpers -------------------------------------------------

    @staticmethod
    def clean(s: str) -> str:
        for ch in BOLD_OPENERS + BOLD_CLOSE:
            s = s.replace(ch, "**")
        for lvl, ch in HEAD_LEVELS.items():
            s = s.replace(ch, "#" * lvl + " ")
        s = s.replace(HEAD_CLOSE, " ").replace(BLOCK, " ")
        return re.sub(r"\s+", " ", s).strip()

    def context(self, start: int, end: int, n: int = 40) -> str:
        a = max(0, start - n)
        b = min(len(self.text), end + n)
        left = self.clean(self.text[a:start])
        mid = self.clean(self.text[start:end])
        right = self.clean(self.text[end:b])
        return ("..." if a > 0 else "") + left + " [[" + mid + "]] " + right + ("..." if b < len(self.text) else "")

    def prose(self) -> str:
        """The normalised text as a reader (or a model) should see it: bold as
        **x**, headings as #, one paragraph per block."""
        s = self.text
        for ch in BOLD_OPENERS + BOLD_CLOSE:
            s = s.replace(ch, "**")
        for lvl, ch in HEAD_LEVELS.items():
            s = s.replace(ch, "\n\n" + "#" * lvl + " ")
        s = s.replace(HEAD_CLOSE, "\n\n").replace(BLOCK, "\n")
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in s.split("\n")]
        out: List[str] = []
        for ln in lines:
            if ln:
                out.append(ln)
            elif out and out[-1] != "":
                out.append("")
        return "\n".join(out).strip()

    # -- sentences -----------------------------------------------------------

    _BOUNDARY = re.compile(
        r"[-]"                     # block / heading edges
        r"|\n[ \t]*\n"                                     # blank line
        r"|(?<=[.!?][\"”’')\]])\s+(?=[A-Z“\"(\[])"
        r"|(?<=[.!?])\s+(?=[A-Z“\"(\[])"
    )

    def sentences(self) -> List[Tuple[int, int]]:
        if self._sentences is None:
            out: List[Tuple[int, int]] = []
            pos = 0
            for m in self._BOUNDARY.finditer(self.text):
                if m.start() > pos:
                    out.append((pos, m.start()))
                pos = m.end()
            if pos < len(self.text):
                out.append((pos, len(self.text)))
            # Drop pieces that are only whitespace/sentinels.
            self._sentences = [(a, b) for a, b in out if self.clean(self.text[a:b]).strip("* #")]
        return self._sentences

    def sentence_at(self, pos: int) -> Tuple[int, int]:
        for a, b in self.sentences():
            if a <= pos < b:
                return a, b
        return pos, min(len(self.text), pos + 200)

    def paragraphs(self) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        pos = 0
        for m in re.finditer(r"[-]|\n[ \t]*\n", self.text):
            if m.start() > pos:
                out.append((pos, m.start()))
            pos = m.end()
        if pos < len(self.text):
            out.append((pos, len(self.text)))
        return [(a, b) for a, b in out if self.clean(self.text[a:b])]


# ---------------------------------------------------------------------------
# Checks. Each returns a dict with at least: section, count, hits[].
# ---------------------------------------------------------------------------

def _hit(doc: Doc, start: int, end: int, **extra: Any) -> Dict[str, Any]:
    h: Dict[str, Any] = {"line": doc.line_of(start), "text": doc.clean(doc.text[start:end])}
    h.update(extra)
    h["context"] = doc.context(start, end)
    return h


def check_em_dash(doc: Doc) -> Dict[str, Any]:
    hits = [_hit(doc, m.start(), m.end()) for m in re.finditer(EM_DASH, doc.text)]
    doubles = []
    for a, b in doc.sentences():
        n = doc.text.count(EM_DASH, a, b)
        if n >= 2:
            doubles.append({"line": doc.line_of(a), "count": n, "sentence": doc.clean(doc.text[a:b])[:300]})
    return {"section": 1, "rule": "Replace with commas, parentheses or a colon. If a sentence needs two, it needs rewriting.",
            "count": len(hits), "hits": hits, "double_sentences": doubles}


def check_curly_quote(doc: Doc) -> Dict[str, Any]:
    hits = [_hit(doc, m.start(), m.end(), kind=CURLY_NAMES.get(m.group(0), "curly"))
            for m in re.finditer("[%s]" % CURLY, doc.text)]
    return {"section": 1, "rule": "Use straight \" and '. Curly marks are a ChatGPT and DeepSeek signature.",
            "count": len(hits), "hits": hits}


def check_bold(doc: Doc) -> Dict[str, Any]:
    hits = []
    by_marker = {"strong": 0, "b": 0, "markdown": 0}
    kinds = {BOLD_STRONG: "strong", BOLD_B: "b", BOLD_MD: "markdown"}
    for m in re.finditer("([%s])(.*?)(?:%s|$)" % (BOLD_OPENERS, BOLD_CLOSE), doc.text, flags=re.S):
        kind = kinds[m.group(1)]
        by_marker[kind] += 1
        inner = doc.clean(m.group(2))
        hits.append({"line": doc.line_of(m.start()), "marker": kind, "text": inner[:160]})
    return {"section": 1, "rule": "Bold marks one thing on a page, not a theme.",
            "count": len(hits), "by_marker": by_marker, "hits": hits}


TITLE_STOP = {
    "a", "an", "the", "and", "or", "but", "nor", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "are", "was", "were", "vs", "via", "per", "into", "onto", "than",
    "that", "this", "your", "our", "its", "it", "be", "not", "up", "out", "over", "under", "off",
    "if", "so", "yet", "how", "what", "when", "where", "why", "who", "which", "do", "does", "did",
    "you", "we", "they", "he", "she", "i", "my", "his", "her", "their", "us",
}


def is_title_case(text: str) -> bool:
    """Heuristic. True when a heading of three or more words capitalises every
    main word after the first, and either has three or more such words or also
    capitalises a stop word ("For", "And"). Two capitalised words alone are
    allowed, so "Working with Liberation Labs" passes and "Three Tiers, One
    Standard" does not. All-caps words are treated as acronyms and ignored."""
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", text)
    if len(words) < 3:
        return False
    rest = words[1:]
    main = [w for w in rest if w.lower() not in TITLE_STOP and not (w.isupper() and len(w) >= 2)]
    cap_stop = [w for w in rest if w.lower() in TITLE_STOP and w[0].isupper()]
    if len(main) < 2 or not all(w[0].isupper() for w in main):
        return False
    return len(main) >= 3 or len(cap_stop) >= 1


def _headings(doc: Doc) -> List[Tuple[int, int, int]]:
    """(start, level, end) for every heading."""
    out = []
    for m in re.finditer("([%s])(.*?)(?:%s|$)" % (HEAD_OPENERS, HEAD_CLOSE), doc.text, flags=re.S):
        level = [k for k, v in HEAD_LEVELS.items() if v == m.group(1)][0]
        out.append((m.start(), level, m.end()))
    return out


def check_heading_title_case(doc: Doc) -> Dict[str, Any]:
    hits = []
    heads = _headings(doc)
    for start, level, end in heads:
        txt = doc.clean(doc.text[start:end]).lstrip("# ").strip()
        if is_title_case(txt):
            hits.append({"line": doc.line_of(start), "level": level, "text": txt})
    return {"section": 1, "rule": "Sentence case. Title Case On Every Main Word is a chatbot habit.",
            "count": len(hits), "headings_checked": len(heads), "hits": hits,
            "note": "heuristic; see is_title_case() in style_lint.py"}


def check_emoji_marker(doc: Doc) -> Dict[str, Any]:
    hits = []
    seen = set()
    line_start = re.compile(r"^[ \t]*%s?[ \t]*(?:[-*+•]\s+)?(%s)" % (BLOCK, EMOJI_RE.pattern), re.M)
    for m in line_start.finditer(doc.text):
        ln = doc.line_of(m.start(1))
        if ln not in seen:
            seen.add(ln)
            hits.append(_hit(doc, m.start(1), m.end(1), where="line start"))
    for start, level, end in _headings(doc):
        for m in EMOJI_RE.finditer(doc.text, start, end):
            ln = doc.line_of(m.start())
            if ln not in seen:
                seen.add(ln)
                hits.append(_hit(doc, m.start(), m.end(), where="heading"))
    hits.sort(key=lambda h: h["line"])
    return {"section": 1, "rule": "Never as section markers or bullets.", "count": len(hits), "hits": hits}


# Negative parallelism. Order is priority: the first form to claim a "not" /
# "rather" / "instead" position wins, so nothing is counted twice.
#
# The comparative preference "we would rather say X than Y" is correct English
# and is NOT the tell. Only the contiguous unit "rather than" is matched; the
# split "rather ... than" is deliberately not searched for.
NP_FORMS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("rather_than", re.compile(r"\brather\s+than\b", re.I)),
    ("instead_of", re.compile(r"\binstead\s+of\b", re.I)),
    ("not_x_but_y", re.compile(
        r"\bnot\s+(?:just\s+|only\s+|merely\s+|simply\s+)?(?!but\b)"
        r"(?P<x>[^.;:!?,]{1,60}?),?\s+but\s+(?:also\s+)?(?=\w)", re.I)),
    ("not_only_also", re.compile(
        r"\bnot\s+only\b[^.;:!?]{1,80}?\b(?:it\s+is|it's|they\s+are|we\s+are|is|are)\s+also\b", re.I)),
    ("sentence_initial_not", re.compile(r"\bNot\s+(?=[a-z])")),
    ("comma_not", re.compile(r",[\s-]*(not)\s+(?=[a-z])", re.I)),
    ("not_x_dash_y", re.compile(
        r"\bnot\s+(?!only\b|just\b)(?P<x>[^.;:!?—–]{1,60}?)\s*[—–]\s*(?=\w)", re.I)),
]


def _at_sentence_start(text: str, pos: int) -> bool:
    i = pos - 1
    while i >= 0 and (text[i] in " \t “\"‘'([" or text[i] in BOLD_OPENERS):
        i -= 1
    if i < 0:
        return True
    c = text[i]
    if c == "\n" or c == BLOCK or c == HEAD_CLOSE or c in HEAD_OPENERS:
        return True
    j = i
    while j >= 0 and (text[j] in "”\"’')]" or text[j] == BOLD_CLOSE):
        j -= 1
    return j >= 0 and text[j] in ".!?:"


def check_negative_parallelism(doc: Doc) -> Dict[str, Any]:
    hits = []
    claimed = set()
    by_form: Dict[str, int] = {}
    for form, pat in NP_FORMS:
        for m in pat.finditer(doc.text):
            if form == "comma_not":
                key = m.start(1)
                start, end = m.start(), m.end()
            else:
                key = m.start()
                start, end = m.start(), m.end()
            if form in ("not_x_but_y", "not_x_dash_y"):
                x = m.group("x")
                if re.search(r"\n[ \t]*\n", x) or not doc.clean(x):
                    continue
            if form == "sentence_initial_not" and not _at_sentence_start(doc.text, m.start()):
                continue
            if key in claimed:
                continue
            claimed.add(key)
            a, b = doc.sentence_at(key)
            hits.append({
                "line": doc.line_of(key), "form": form,
                "text": doc.clean(doc.text[start:end])[:120],
                "sentence": doc.clean(doc.text[a:b])[:300],
            })
            by_form[form] = by_form.get(form, 0) + 1
    hits.sort(key=lambda h: (h["line"], h["text"]))
    return {"section": 2,
            "rule": "Say the positive claim once. If the contrast is doing real work, give it its own sentence.",
            "count": len(hits), "by_form": by_form, "hits": hits}


COPULA_FORMS: List[Tuple[str, str]] = [
    ("serves as", r"\bserv(?:e|es|ed|ing)\s+as\b"),
    ("stands as", r"\b(?:stand(?:s|ing)?|stood)\s+as\b"),
    ("marks a", r"\bmark(?:s|ed|ing)?\s+an?\b"),
    ("offers", r"\boffer(?:s|ed|ing)?\b"),
    ("provides", r"\bprovid(?:e|es|ed|ing)\b"),
    ("features", r"\bfeatur(?:e|es|ed|ing)\b"),
    ("boasts", r"\bboast(?:s|ed|ing)?\b"),
    ("ventured into", r"\bventur(?:e|es|ed|ing)\s+into\b"),
    ("began his career as", r"\bbegan\s+(?:his|her|their|its)\s+career\s+as\b"),
]
# "the features", "their offers": nouns, not the verb the rule targets.
NOUN_DETERMINER = re.compile(
    r"(?:\b(?:the|a|an|this|that|these|those|its|our|their|your|my|his|her|new|key|main|core|"
    r"two|three|four|five|six|several|many|few|all|some|no|special|job|product|any|each|every|"
    r"more|most|other|such|on|of|these|with|and|or)\s*)$", re.I)


def check_copula_avoidance(doc: Doc) -> Dict[str, Any]:
    hits = []
    by_form: Dict[str, int] = {}
    for name, pat in COPULA_FORMS:
        for m in re.finditer(pat, doc.text, flags=re.I):
            if name in ("offers", "features"):
                before = re.sub(SENT_CLASS, "", doc.text[max(0, m.start() - 24):m.start()])
                if NOUN_DETERMINER.search(before):
                    continue
            hits.append(_hit(doc, m.start(), m.end(), form=name))
            by_form[name] = by_form.get(name, 0) + 1
    hits.sort(key=lambda h: h["line"])
    return {"section": 2, "rule": "Use is / are / has instead of marketing verbs.",
            "count": len(hits), "by_form": by_form, "hits": hits}


BANNED: List[Tuple[str, str, str]] = [
    ("emphasizing", r"emphasi[sz](?:e|es|ed|ing)", ""),
    ("enhance", r"enhanc(?:e|es|ed|ing|ement|ements)", ""),
    ("highlighting", r"highlight(?:s|ed|ing)", ""),
    ("showcasing", r"showcas(?:e|es|ed|ing)", ""),
    ("crucial", r"crucial(?:ly)?", ""),
    ("pivotal", r"pivotal", ""),
    ("underscore", r"underscor(?:e|es|ed|ing)", ""),
    ("vibrant", r"vibran(?:t|cy)", ""),
    ("align with", r"(?:align(?:s|ed|ing)?|alignment)\s+with", ""),
    ("fostering", r"foster(?:s|ed|ing)?(?!\s+(?:care|child|children|home|homes|parent|parents|family|families))", ""),
    ("bolstered", r"bolster(?:s|ed|ing)?", ""),
    ("enduring", r"enduring", ""),
    ("landscape", r"landscapes?", "figurative? the rule bans the figurative sense"),
    ("tapestry", r"tapestr(?:y|ies)", ""),
    ("testament", r"testaments?", ""),
    ("delve", r"delv(?:e|es|ed|ing)", ""),
    ("intricate", r"intrica(?:te|tely|cy|cies)", ""),
    ("interplay", r"interplay", ""),
    ("meticulous", r"meticulous(?:ly)?", ""),
    ("garner", r"garner(?:s|ed|ing)?", ""),
    ("boasts", r"boast(?:s|ed|ing)?", ""),
    ("seamless", r"seamless(?:ly)?", ""),
    ("robust", r"robust(?:ly|ness)?", ""),
    ("comprehensive", r"comprehensive(?:ly)?", ""),
    ("leverage", r"leverag(?:e|es|ed|ing)", ""),
    ("innovative", r"innovative(?:ly)?", ""),
    ("empowering", r"empower(?:s|ed|ing|ment)?", ""),
    ("revolutionary", r"revolutionar(?:y|ies)|revolutioni[sz](?:e|es|ed|ing)", ""),
    ("cutting-edge", r"cutting[- ]edge", ""),
    ("groundbreaking", r"ground[- ]?breaking", ""),
    ("synergy", r"synerg(?:y|ies|istic|i[sz]e|i[sz]es)", ""),
    ("solution", r"solutions?", "product noun? the rule bans 'solution' as a noun for a product"),
]


def check_banned_vocabulary(doc: Doc) -> Dict[str, Any]:
    hits = []
    by_word: Dict[str, int] = {}
    for word, pat, note in BANNED:
        for m in re.finditer(r"\b(?:%s)\b" % pat, doc.text, flags=re.I):
            h = _hit(doc, m.start(), m.end(), word=word)
            if note:
                h["note"] = note
            hits.append(h)
            by_word[word] = by_word.get(word, 0) + 1
    hits.sort(key=lambda h: (h["line"], h["context"]))
    # "One is coincidence. Four in a paragraph is a fingerprint."
    max_para = 0
    if hits:
        paras = doc.paragraphs()
        positions = []
        for word, pat, note in BANNED:
            positions.extend(m.start() for m in re.finditer(r"\b(?:%s)\b" % pat, doc.text, flags=re.I))
        for a, b in paras:
            n = sum(1 for p in positions if a <= p < b)
            max_para = max(max_para, n)
    return {"section": 3, "rule": "Never-list. One is coincidence. Four in a paragraph is a fingerprint.",
            "count": len(hits), "by_word": by_word, "max_in_one_paragraph": max_para, "hits": hits}


# Advisory: the greppable half of "rule of three" (three fragment-sentences of
# up to three words each) and spaced en dashes doing an em dash's job. Neither
# gates; the guide's rule-of-three definition needs judgement, which is
# style_pass.py's job.
RULE_OF_THREE = re.compile(
    r"(?<![\w,;:—])(?:[\w'’\-]+(?:[ \t]+[\w'’\-]+){0,2}\.[ \t]+){2}[\w'’\-]+(?:[ \t]+[\w'’\-]+){0,2}\.(?![\w])"
)


def advisory_rule_of_three(doc: Doc) -> Dict[str, Any]:
    hits = [_hit(doc, m.start(), m.end()) for m in RULE_OF_THREE.finditer(doc.text)]
    return {"section": 2, "rule": "Three short parallel fragments used to make a thin point sound thorough.",
            "count": len(hits), "hits": hits, "gating": False}


def advisory_en_dash_spaced(doc: Doc) -> Dict[str, Any]:
    hits = [_hit(doc, m.start(1), m.end(1)) for m in re.finditer(r"[ \t ](%s)[ \t ]" % EN_DASH, doc.text)]
    return {"section": 1, "rule": "A spaced en dash is an em dash wearing a hat.", "count": len(hits), "hits": hits,
            "gating": False}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

CHECK_ORDER = [
    ("em_dash", check_em_dash),
    ("curly_quote", check_curly_quote),
    ("bold", check_bold),
    ("heading_title_case", check_heading_title_case),
    ("emoji_marker", check_emoji_marker),
    ("negative_parallelism", check_negative_parallelism),
    ("copula_avoidance", check_copula_avoidance),
    ("banned_vocabulary", check_banned_vocabulary),
]

LABELS = {
    "em_dash": "Em dashes",
    "curly_quote": "Curly quotes/apostrophes",
    "bold": "Bold spans",
    "heading_title_case": "Headings in Title Case",
    "emoji_marker": "Emoji as markers",
    "negative_parallelism": "Negative parallelism",
    "copula_avoidance": "Copula avoidance",
    "banned_vocabulary": "Banned vocabulary",
}


def lint_text(path: str, src: str, fmt: str = "auto", curly_ok_in_html: bool = False) -> Dict[str, Any]:
    if fmt == "auto":
        fmt = detect_format(path, src)
    doc = Doc(path, src, fmt)
    checks: Dict[str, Any] = {}
    for name, fn in CHECK_ORDER:
        checks[name] = fn(doc)
    advisory: Dict[str, Any] = {
        "rule_of_three_fragments": advisory_rule_of_three(doc),
        "en_dash_spaced": advisory_en_dash_spaced(doc),
    }
    if curly_ok_in_html and fmt == "html":
        # Open question in the guide (2026-09-23): curly quotes in typeset HTML
        # may be correct typesetting. Reported, not gated, when asked.
        cq = checks.pop("curly_quote")
        cq["gating"] = False
        cq["note"] = "demoted to advisory by --curly-ok-in-html"
        advisory["curly_quote_html"] = cq
    failing = [n for n, c in checks.items() if c["count"] > 0]
    total = sum(c["count"] for c in checks.values())
    prose = doc.prose()
    return {
        "tool": "style_lint",
        "version": VERSION,
        "guide": GUIDE,
        "file": path,
        "format": fmt,
        "options": {"curly_ok_in_html": curly_ok_in_html},
        "stats": {
            "chars": len(src),
            "lines": src.count("\n") + 1,
            "prose_chars": len(prose),
            "words": len(re.findall(r"\w+", prose)),
            "paragraphs": len(doc.paragraphs()),
            "sentences": len(doc.sentences()),
            "headings": checks["heading_title_case"]["headings_checked"],
        },
        "checks": checks,
        "advisory": advisory,
        "gate": {"pass": not failing, "total_hits": total, "failing_checks": failing},
        "prose": prose,
    }


def render_report(r: Dict[str, Any], context: bool = True) -> str:
    out: List[str] = []
    st = r["stats"]
    out.append("style_lint %s: %s" % (r["version"], r["file"]))
    out.append("format=%s  chars=%d  prose_chars=%d  lines=%d  words=%d  paragraphs=%d  headings=%d" % (
        r["format"], st["chars"], st["prose_chars"], st["lines"], st["words"], st["paragraphs"], st["headings"]))
    out.append("guide: %s" % r["guide"])
    out.append("")
    for name, c in r["checks"].items():
        label = "[%d] %s" % (c["section"], LABELS.get(name, name))
        extra = ""
        if name == "bold":
            extra = "  (" + ", ".join("%s %d" % (k, v) for k, v in c["by_marker"].items() if v) + ")" if c["count"] else ""
        elif name == "negative_parallelism" and c["count"]:
            extra = "  (" + ", ".join("%s %d" % (k, v) for k, v in c["by_form"].items()) + ")"
        elif name == "copula_avoidance" and c["count"]:
            extra = "  (" + ", ".join("%s %d" % (k, v) for k, v in c["by_form"].items()) + ")"
        elif name == "banned_vocabulary" and c["count"]:
            extra = "  (max in one paragraph: %d)" % c["max_in_one_paragraph"]
        elif name == "heading_title_case":
            extra = "  (%d headings checked)" % c["headings_checked"]
        out.append("%-34s %3d hit%s%s" % (label, c["count"], "" if c["count"] == 1 else "s", extra))
        for h in c["hits"]:
            if name == "negative_parallelism":
                out.append("    L%-4d [%s] %s" % (h["line"], h["form"], h["sentence"]))
            elif name == "bold":
                out.append("    L%-4d <%s> %s" % (h["line"], h["marker"], h["text"]))
            elif name == "heading_title_case":
                out.append("    L%-4d h%d: %s" % (h["line"], h["level"], h["text"]))
            elif name == "banned_vocabulary":
                note = ("  (%s)" % h["note"]) if h.get("note") else ""
                out.append("    L%-4d %s%s" % (h["line"], h["context"] if context else h["text"], note))
            elif name == "copula_avoidance":
                out.append("    L%-4d [%s] %s" % (h["line"], h["form"], h["context"] if context else h["text"]))
            else:
                out.append("    L%-4d %s" % (h["line"], h["context"] if context else h["text"]))
        if name == "em_dash" and c["double_sentences"]:
            out.append("    sentences with two or more em dashes: %d" % len(c["double_sentences"]))
            for d in c["double_sentences"]:
                out.append("      L%-4d (%d) %s" % (d["line"], d["count"], d["sentence"]))
    out.append("")
    out.append("advisory (not gating):")
    for name, c in r["advisory"].items():
        out.append("    %-30s %3d" % (name, c["count"]))
        for h in c["hits"][:20]:
            out.append("        L%-4d %s" % (h["line"], h.get("context", h.get("text", ""))))
    out.append("")
    g = r["gate"]
    if g["pass"]:
        out.append("GATE: PASS (no hits in sections 1-3)")
    else:
        out.append("GATE: FAIL: %d hit%s across %d check%s (%s)" % (
            g["total_hits"], "" if g["total_hits"] == 1 else "s", len(g["failing_checks"]),
            "" if len(g["failing_checks"]) == 1 else "s", ", ".join(g["failing_checks"])))
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mechanical checks for sections 1-3 of the AI-writing pass.")
    ap.add_argument("file", help="plain text, Markdown or HTML file; '-' for stdin")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the human report")
    ap.add_argument("--format", default="auto", choices=["auto", "html", "markdown", "text"])
    ap.add_argument("--curly-ok-in-html", action="store_true",
                    help="report curly quotes in HTML as advisory instead of gating (guide open question)")
    ap.add_argument("--no-context", action="store_true", help="human report: matched text only, no context")
    ap.add_argument("--prose", action="store_true", help="print the normalised prose and exit")
    args = ap.parse_args(argv)

    try:
        if args.file == "-":
            src = sys.stdin.read()
            path = "<stdin>"
        else:
            path = args.file
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
    except OSError as e:
        print("style_lint: cannot read %s: %s" % (args.file, e), file=sys.stderr)
        return 2

    try:
        result = lint_text(path, src, args.format, args.curly_ok_in_html)
    except RuntimeError as e:
        print("style_lint: %s" % e, file=sys.stderr)
        return 2

    if args.prose:
        print(result["prose"])
        return 0
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_report(result, context=not args.no_context))
    return 0 if result["gate"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
