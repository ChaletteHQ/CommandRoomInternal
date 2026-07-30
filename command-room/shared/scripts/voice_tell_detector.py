#!/usr/bin/env python3
"""
Save-time voice-tell detector (SPEC B2).

WHY THIS EXISTS
---------------

`shared/VOICE_CALIBRATION.md` carries a universal banned-phrase list of LLM
tells — generic-assistant language ("I'd be happy to…", "Hope this finds you
well", "Best regards") that instantly breaks the illusion the CEO wrote the
draft. For most of the plugin's life that list was prompt discipline only:
declared in prose, enforced by NOTHING (the same enforcement gap
`tests/run_lazy_draft_test.py` documents for the lazy-draft contract).

This module turns the list into a deterministic, stdlib-only gate. It scans
output text BEFORE it reaches disk and returns `fail / warn / pass` with the
offending lines, wired into:
  1. each composer's Step 2 critique as a bash-gated tool call, and
  2. `brief_writer.make_brief()` PRE-save (before Document() is built) so no
     .docx carrying an exact tell ever reaches disk.

SEVERITY MODEL (SPEC B2 D1, amended by FB-16 and SPEC DASHBAN)
--------------------------------------------------------------
  fail  — exact banned phrases (openers / fillers / preambles / closers).
          One rule per markdown bullet in VOICE_CALIBRATION.md's list.
          PLUS `dash_as_punctuation` (FB-16): em dash, en dash or spaced
          hyphen in body prose, one finding per OCCURRENCE, no pile-up
          allowance. This is a brand-voice hard rule, not a judgment call.
  warn  — structural tells (tri-colon, hedging stacks, bullets-in-email).
          These are judgment calls; the markdown itself hedges ("where prose
          fits"), so they advise, never block.
  pass  — clean.
Verdict = fail if any fail finding, else warn if any warn finding, else pass.

This block previously listed ">2 em-dashes per paragraph" under `warn`. That
pile-up warn was REPLACED by the FB-16 per-occurrence fail rule (see
`_scan_structural`), but the docstring was not updated with it, and the stale
text went on to mislead a spec author into re-specifying a severity flip that
had already shipped. Corrected under SPEC DASHBAN §3.2.

CLIENT SAFETY
-------------
This gate can BLOCK a document save on live client workspaces, so it is built
to never lose content and never fight a calibrated voice:
  - `allow_phrases` feeds a client's demonstrably-used phrases (their Voice
    Block Taboos) through untouched — those are NEVER reported. A real CEO who
    signs "Best regards" passes once that phrase is in their Voice Block.
  - `skip_quoted` ignores line-initial transcript quotes and reply blockquotes
    so a banned phrase the COUNTERPARTY said in a pulled quote never blocks the
    CEO's own save.
  - The brief_writer wrapper hard-fails the canonical-voice outbound kinds
    (FAIL_BLOCKING_KINDS) on ANY fail finding; on every other kind only the
    ALWAYS_BLOCKING_RULES findings block, and the rest stay warn-only. And it
    raises PRE-Document() so a blocked save writes no partial file — the draft
    is rewritten, never dropped.
  - `ban_dashes=False` turns the dash rule off wholesale for a client whose
    calibrated voice keeps dashes — no finding, and no pre-pass rewrite.

SYNC RULE
---------
The fail-rule table below is the machine-readable encoding of
VOICE_CALIBRATION.md's banned-phrase list. The two MUST change together —
`tests/run_voice_tell_detector_test.py` asserts the detector's fail-rule count
is >= the markdown list's bullet count, so adding a bullet without a code row
fails the battery loudly.
"""

from __future__ import annotations

import re
import sys as _sys
from pathlib import Path as _Path
from typing import Dict, List, Optional, Sequence

try:
    from vocabulary_policy import vocabulary_fail_rules
except ImportError:  # pragma: no cover — direct-path import fallback
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from vocabulary_policy import vocabulary_fail_rules


class VoiceTellError(RuntimeError):
    """Raised by the save-time gate when a fail-severity voice tell is present
    in a hard-blocking context. Carries the structured findings so the caller
    can report exactly which lines to rewrite.

    Raised PRE-save by `brief_writer.make_brief()` — no partial file exists
    when this fires, so the draft is rewritten, never silently dropped."""

    def __init__(self, message: str, findings: Optional[List[dict]] = None) -> None:
        super().__init__(message)
        self.findings: List[dict] = findings or []


# Brief kinds whose output is canonical CEO voice going OUTBOUND. A fail-
# severity tell in one of these hard-blocks the save. On every other kind
# (call_prep, past_meeting, insights, weekly_*, operator_report, dormant_scan,
# automation_*, contract_review, stress_test) the BANNED-PHRASE fail rules stay
# warn-only — those are internal-to-user briefs where a quoted tell is
# legitimate. Single source of truth; brief_writer imports this set.
#
# Do NOT widen this set to make a single rule block more broadly. It governs
# banned-phrase blocking too, so widening it silently changes enforcement for
# rules nobody ruled on — and `tests/run_voice_gate_override_test.py` pins
# `VOICE_SKILL_BY_KIND` to exactly this set. Use ALWAYS_BLOCKING_RULES below.
FAIL_BLOCKING_KINDS = frozenset(
    {"memo", "one_pager", "decision_memo", "board_pack", "followup_pack"}
)

# SPEC DASHBAN §3.1 — rules that block a save on EVERY brief kind, not just the
# outbound five. A rule earns a place here by being a brand-voice hard rule
# rather than a judgment call: it is wrong in an internal brief for the same
# reason it is wrong in an outbound memo, so kind-scoping it makes no sense.
#
# `dash_as_punctuation` is here because M ruled hard-block on every doc type
# (v5.4.0 dogfood: 847 em dashes across 69 of 82 documents in one week, the
# bulk of them on kinds this set is the only way to reach). The gate does NOT
# carry that ban alone — `dash_rewriter` resolves the routine cases before the
# scan, so what blocks here is the residue a deterministic pass declined to
# guess at.
#
# This is a RULE-scoped escalation, not a kind-scoped one: on a kind outside
# FAIL_BLOCKING_KINDS, ONLY findings whose rule is in this set block; every
# other fail finding on that kind stays warn-only exactly as before.
ALWAYS_BLOCKING_RULES = frozenset({"dash_as_punctuation"})


def _phrase_pattern(phrase: str) -> "re.Pattern[str]":
    """Compile a banned phrase into a case-insensitive, apostrophe-flexible,
    whitespace-flexible, word-boundary-anchored pattern. Canonical phrases use
    a straight apostrophe; both ' and the curly U+2019 match at runtime."""
    parts: List[str] = []
    for ch in phrase:
        if ch in "'’":
            parts.append("['’]")
        elif ch == " ":
            parts.append(r"\s+")
        else:
            parts.append(re.escape(ch))
    return re.compile(r"\b" + "".join(parts), re.IGNORECASE)


# Canonical fail rules — one per banned-phrase bullet in VOICE_CALIBRATION.md
# (Openers / Filler phrases / Preambles / Closers). Order is display order
# only; every rule runs regardless of which matches first.
#
# (rule_id, canonical_phrase, hint)
_FAIL_PHRASES: List[tuple[str, str, str]] = [
    # --- Openers to never use ---
    ("opener_happy_to",        "I'd be happy to",        "open with the action, not your eagerness to do it"),
    ("opener_love_to",         "I'd love to",            "state what you'll do, not that you'd love to"),
    ("opener_happy_help",      "Happy to help",          "drop it — lead with the substance"),
    ("opener_great_question",  "Great question",         "answer the question; don't praise it"),
    ("opener_great_point",     "That's a great point",   "engage the point directly, no preamble"),
    ("opener_thanks_reaching", "Thanks for reaching out","open on the topic unless a thank-you is genuinely apt"),
    # --- Filler phrases to strip ---
    ("filler_let_me_know",     "Let me know if",         "make the ask concrete or cut it"),
    ("filler_feel_free",       "Feel free to",           "say plainly what they can do"),
    ("filler_hope_helps",      "I hope this helps",      "trust the content; cut the hedge"),
    ("filler_finds_well",      "Hope this finds you well","cut it unless the CEO demonstrably uses it (allow_phrases)"),
    ("filler_dont_hesitate",   "Please don't hesitate to","say 'email me' / 'call me' directly"),
    ("filler_circling_back",   "Circling back on",       "say 'following up on [X]'"),
    ("filler_wanted_circle",   "I wanted to circle back", "say 'following up on [X]'"),
    ("filler_check_in",        "Just wanted to check in", "state the actual reason for the message"),
    ("filler_touching_base",   "Touching base",          "name the specific thing you're following up on"),
    ("filler_per_last_email",  "As per my last email",   "restate the point plainly without the jab"),
    # --- Preambles to strip ---
    ("preamble_heres_draft",   "Here's a draft",         "deliver the content; drop the framing"),
    ("preamble_heres_came_up", "Here's what I came up with","deliver the content; drop the framing"),
    ("preamble_below_draft",   "Below is a draft for your review","deliver the content; drop the framing"),
    ("preamble_put_together",  "I've put together",      "deliver the content; drop the framing"),
    # --- Closers to never use ---
    ("closer_best_regards",    "Best regards",           "use the CEO's calibrated sign-off (or none)"),
    ("closer_warm_regards",    "Warm regards",           "use the CEO's calibrated sign-off (or none)"),
    ("closer_looking_forward", "Looking forward to hearing back","cut unless it's in the Voice Block"),
]

# Per-rule pattern overrides (#v3200-2). A few banned phrases are routinely
# INTERPOLATED — a word is dropped in the middle ("I hope this **email** finds
# you well") — which defeats the literal whitespace-flexible pattern built by
# `_phrase_pattern`. Where the canonical phrase has a common interpolation slot,
# we override its compiled pattern with one that tolerates 0-2 inserted words.
# The canonical phrase + hint are unchanged; only the matcher is widened.
_PATTERN_OVERRIDES: Dict[str, "re.Pattern[str]"] = {
    # "Hope this finds you well" → also catch "hope this email/message/note
    # finds you well" (the single most common live form — v3.20.0 A2 fire).
    "filler_finds_well": re.compile(
        r"\bhope\s+this(?:\s+\w+){0,2}\s+finds\s+you\s+well",
        re.IGNORECASE,
    ),
}

# Compiled fail-rule table: (rule_id, canonical_phrase, compiled_pattern, hint).
# Use the per-rule override when present, else the generic phrase pattern.
# The tail rows come from the ONE shared vocabulary list (v4.6.1 S3, F-53
# P3a: "leverage" was hard-blocked in a docx and led an email the same day
# because the two gates kept disjoint lists — vocabulary_policy.py is now
# the single owner; the leak gate reads the same words). allow_phrases
# carve-outs apply to vocabulary rows exactly like phrase rows, so a client
# whose calibrated voice uses one of these words feeds it through.
_FAIL_RULES: List[tuple[str, str, "re.Pattern[str]", str]] = [
    (rid, phrase, _PATTERN_OVERRIDES.get(rid, _phrase_pattern(phrase)), hint)
    for rid, phrase, hint in _FAIL_PHRASES
] + [
    (rid, phrase, _phrase_pattern(phrase), hint)
    for rid, phrase, hint in vocabulary_fail_rules()
]

# Number of exact-phrase fail rules. The test asserts this is >= the markdown
# bullet count so the two can't drift silently.
FAIL_RULE_COUNT = len(_FAIL_RULES)


# --- Structural detectors (warn severity) ---

_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_COLON_SEP_RE = re.compile(r"(?<=[A-Za-z])\s*:\s+(?=[A-Za-z])")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+")
_HEDGE_RE = re.compile(
    r"\b(?:i\s+think|might|perhaps|possibly|could\s+potentially|it\s+may\s+be)\b",
    re.IGNORECASE,
)
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")
_BULLET_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")

# Dash-as-punctuation (FB-16): em dash, en dash, or a space-hyphen-space run.
# Hyphenated compounds ("check-in", "follow-up") carry no surrounding spaces
# and stay legal. Shared by the subject gate and the body gate.
_DASH_PUNCT_RE = re.compile(r"—|–|\s-\s")

# A standalone sign-off line ("— Sam", "— Sam Sample", "— S.", "– Bo"): a
# line that is ONLY a leading dash + a name of 1–3 capitalized tokens (a
# sign-off hierarchy's longest form is a full first + last name).
# FB-16 EXEMPTS this — the em-dash sign-off is a deliberate brand-voice
# element, not dash-as-punctuation. A dash with prose BEFORE it on the line is
# not a sign-off and is still caught.
_SIGNOFF_RE = re.compile(r"^\s*[—–]\s*[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,2}\s*$")


def _is_quoted_line(line: str) -> bool:
    """A line is treated as a quote (and skipped under skip_quoted) when it
    begins with a blockquote marker or a double-quote character. Covers
    transcript pulls (`> …`) and reply blockquotes (`"…," she said`). Inline
    quotes mid-line are NOT skipped (per SPEC B2 §8 — rewrite guidance is to
    paraphrase or blockquote)."""
    s = line.lstrip()
    if not s:
        return False
    if s[0] == ">":
        return True
    if s[0] in '"“”«':
        return True
    return False


def _normalize_allow(allow_phrases: Optional[Sequence[str]]) -> List[str]:
    out: List[str] = []
    for p in allow_phrases or []:
        if not p:
            continue
        norm = re.sub(r"\s+", " ", p.strip().lower()).replace("’", "'")
        if norm:
            out.append(norm)
    return out


def _is_allowed(matched: str, allow_norm: List[str]) -> bool:
    """A finding is suppressed when an allow_phrases entry covers the matched
    text (either direction of containment, apostrophe/whitespace normalized).
    This is the per-client Voice Block Taboos carve-out — a phrase the client
    demonstrably uses is fed through and NEVER reported."""
    if not allow_norm:
        return False
    m = re.sub(r"\s+", " ", matched.strip().lower()).replace("’", "'")
    for a in allow_norm:
        if a in m or m in a:
            return True
    return False


def _iter_paragraphs(text: str):
    """Yield (start_line_no, paragraph_text) for blank-line-delimited
    paragraphs. start_line_no is 1-based into the original text."""
    lines = text.split("\n")
    buf: List[str] = []
    start: Optional[int] = None
    for idx, line in enumerate(lines, start=1):
        if line.strip() == "":
            if buf:
                yield start, "\n".join(buf)
                buf, start = [], None
        else:
            if start is None:
                start = idx
            buf.append(line)
    if buf:
        yield start, "\n".join(buf)


def _scan_phrases(
    text: str, *, allow_norm: List[str], skip_quoted: bool
) -> List[dict]:
    findings: List[dict] = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        if skip_quoted and _is_quoted_line(line):
            continue
        for rule_id, phrase, pattern, hint in _FAIL_RULES:
            for m in pattern.finditer(line):
                matched = m.group(0)
                if _is_allowed(matched, allow_norm):
                    continue
                findings.append(
                    {
                        "rule": rule_id,
                        "severity": "fail",
                        "line_no": line_no,
                        "line": line.strip(),
                        "match": matched,
                        "hint": hint,
                    }
                )
    return findings


def _scan_structural(
    text: str, *, context: str, skip_quoted: bool,
    allow_norm: Optional[List[str]] = None, ban_dashes: bool = True,
) -> List[dict]:
    allow_norm = allow_norm or []
    findings: List[dict] = []
    for start, para in _iter_paragraphs(text):
        first_line = para.split("\n", 1)[0]
        if skip_quoted and _is_quoted_line(first_line):
            continue

        # Dash-as-punctuation (FB-16) — the product-level hard ban in body
        # prose: em dash, en dash, or spaced hyphen, at FAIL severity, one
        # finding per occurrence. This REPLACES the old em-dash PILE-UP warn,
        # which only fired at >2 em-dashes per paragraph — so a quick-drafted
        # email body with a single "we shipped — fast" scored `pass` and
        # slipped the gate entirely (the seam FB-16 closes; the subject gate
        # already banned dashes, the body gate never did). Default-on;
        # `ban_dashes=False` turns it off for a client whose calibrated voice
        # keeps dashes, and a demonstrably-used dashed phrase in a client's
        # allow_phrases feeds its line through. The standalone "— Matthew"
        # sign-off line is EXEMPT. Runs on body paragraphs only (structural
        # scope), so list/table/matrix cells are untouched.
        if ban_dashes:
            for offset, line in enumerate(para.split("\n")):
                if skip_quoted and _is_quoted_line(line):
                    continue
                if _SIGNOFF_RE.match(line):
                    continue
                if allow_norm and _is_allowed(line, allow_norm):
                    continue
                # Strip a leading list marker so an indented "  - item" bullet
                # is not itself read as a spaced-hyphen; a real dash inside the
                # bullet text is still caught.
                scan_line = _BULLET_MARKER_RE.sub("", line)
                for m in _DASH_PUNCT_RE.finditer(scan_line):
                    findings.append(
                        {
                            "rule": "dash_as_punctuation",
                            "severity": "fail",
                            "line_no": start + offset,
                            "line": line.strip(),
                            "match": m.group(0),
                            "hint": "no dashes as punctuation — use a comma, a "
                                    "colon, or rewrite (the “— Name” sign-off "
                                    "is exempt)",
                        }
                    )

        # Tri-colon construction — >=2 word:word colon separators in a line.
        no_time = _TIME_RE.sub(" ", para)
        if len(_COLON_SEP_RE.findall(no_time)) >= 2:
            findings.append(
                {
                    "rule": "structural_tri_colon",
                    "severity": "warn",
                    "line_no": start,
                    "line": first_line.strip(),
                    "match": "tri-colon construction",
                    "hint": "rewrite the colon-chained clauses as prose where prose fits",
                }
            )

        # Hedging stack — >=2 hedge tokens in a single sentence.
        for sentence in _SENTENCE_SPLIT_RE.split(para):
            if len(_HEDGE_RE.findall(sentence)) >= 2:
                findings.append(
                    {
                        "rule": "structural_hedging_stack",
                        "severity": "warn",
                        "line_no": start,
                        "line": sentence.strip()[:120],
                        "match": "hedging stack",
                        "hint": "commit or cut — stacked hedges read as evasive",
                    }
                )
                break

        # Bullets-in-email — only meaningful when the surface is an email/Slack
        # body. Brief composers legitimately use bullets, so this is gated on
        # context=="email" to avoid false positives on memos/one-pagers.
        if context == "email":
            bullet_lines = [
                ln for ln in para.split("\n") if _BULLET_LINE_RE.match(ln)
            ]
            if len(bullet_lines) >= 2:
                findings.append(
                    {
                        "rule": "structural_bullets_in_email",
                        "severity": "warn",
                        "line_no": start,
                        "line": bullet_lines[0].strip(),
                        "match": f"{len(bullet_lines)} bullet lines in email body",
                        "hint": "the CEO would write this as prose — collapse the bullets",
                    }
                )
    return findings


def _verdict(findings: List[dict]) -> str:
    if any(f["severity"] == "fail" for f in findings):
        return "fail"
    if any(f["severity"] == "warn" for f in findings):
        return "warn"
    return "pass"


def scan_text(
    text: str,
    *,
    context: str = "email",
    allow_phrases: Optional[Sequence[str]] = None,
    skip_quoted: bool = True,
    ban_dashes: bool = True,
) -> Dict:
    """Scan `text` for voice tells.

    Args:
      text: the output text to scan (email body, brief paragraph, etc.).
      context: "email" enables the bullets-in-email structural check; any
        other value (e.g. "brief") leaves bullets alone.
      allow_phrases: per-client calibrated phrases to feed through untouched
        (Voice Block Taboos). A finding whose match is covered by one of these
        is suppressed — NEVER reported. CLIENT SAFETY hook.
      skip_quoted: when True, line-initial blockquotes and double-quoted lines
        are ignored (transcript pulls, reply blockquotes).
      ban_dashes: FB-16 product-level ban on dashes-as-punctuation in body
        prose (default-on, every client). Set False to override for a client
        whose calibrated voice keeps dashes. The "— Name" sign-off is always
        exempt; a demonstrably-used dashed phrase can also be fed through
        allow_phrases.

    Returns:
      {"verdict": "fail"|"warn"|"pass", "findings": [ {rule, severity,
       line_no, line, match, hint}, ... ]}
    """
    if text is None:
        text = ""
    allow_norm = _normalize_allow(allow_phrases)
    findings = _scan_phrases(text, allow_norm=allow_norm, skip_quoted=skip_quoted)
    findings += _scan_structural(
        text, context=context, skip_quoted=skip_quoted,
        allow_norm=allow_norm, ban_dashes=ban_dashes,
    )
    return {"verdict": _verdict(findings), "findings": findings}


def scan_subject(
    subject: str,
    *,
    allow_phrases: Optional[Sequence[str]] = None,
) -> Dict:
    """The subject-line gate (v4.6.1 S3; F-53 / F-47 P2d — em dashes in
    email subjects twice in one dogfood day; the body gate never saw
    subjects).

    Fails on:
      - any dash used as punctuation (em dash, en dash, spaced hyphen) —
        the BRAND_VOICE hard rule, applied to subjects with NO pile-up
        allowance (unlike the body's warn-at->2 structural rule)
      - the universal banned phrases + shared vocabulary words (a subject
        that LEADS with "Circling back" or "leverage" is still the voice)

    Returns the same {"verdict", "findings"} shape as scan_text. Empty /
    missing subjects are the threading rule's problem (email-writer Phase
    3.5 Rule 12), not this gate's — they pass here.
    """
    if not subject:
        return {"verdict": "pass", "findings": []}
    allow_norm = _normalize_allow(allow_phrases)
    findings = _scan_phrases(subject, allow_norm=allow_norm, skip_quoted=False)
    for m in _DASH_PUNCT_RE.finditer(subject):
        findings.append(
            {
                "rule": "subject_dash",
                "severity": "fail",
                "line_no": 1,
                "line": subject.strip(),
                "match": m.group(0),
                "hint": "no dashes as punctuation in subject lines — "
                        "use a comma, a colon, or rewrite",
            }
        )
    return {"verdict": _verdict(findings), "findings": findings}


def _cell_strings(value) -> List[str]:
    """Flatten an arbitrarily-nested table/matrix cell container into strings."""
    out: List[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_cell_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_cell_strings(v))
    else:
        out.append(str(value))
    return out


def check_sections(
    sections: Sequence[Dict],
    *,
    brief_kind: str,
    allow_phrases: Optional[Sequence[str]] = None,
    ban_dashes: bool = True,
) -> Dict:
    """brief_writer adapter. Flattens `sections` (the same shape make_brief
    receives) into scannable text and applies the gate.

    Phrase rules run over ALL surfaced text — body paragraphs, bullets, and
    table / matrix cells (a banned phrase hidden in a table cell is still
    found). Structural rules run over `body` paragraphs ONLY — bullets and
    table/matrix cells are legitimately list-shaped, so em-dash / tri-colon /
    bullets-in-email checks would false-positive there (SPEC B2 §8).

    Returns the same {"verdict", "findings"} shape as scan_text.
    """
    allow_norm = _normalize_allow(allow_phrases)
    findings: List[dict] = []

    # Phrase scan: every text surface in the section, including list/table cells.
    phrase_lines: List[str] = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        heading = sec.get("heading")
        if isinstance(heading, str):
            phrase_lines.append(heading)
        body = sec.get("body")
        if isinstance(body, str):
            phrase_lines.extend(body.split("\n"))
        for bullet in sec.get("bullets") or []:
            phrase_lines.extend(_cell_strings(bullet))
        table = sec.get("table")
        if isinstance(table, dict):
            phrase_lines.extend(_cell_strings(table.get("headers")))
            phrase_lines.extend(_cell_strings(table.get("rows")))
        matrix = sec.get("matrix")
        if isinstance(matrix, dict):
            phrase_lines.extend(_cell_strings(matrix.get("headers_row")))
            phrase_lines.extend(_cell_strings(matrix.get("headers_col")))
            phrase_lines.extend(_cell_strings(matrix.get("cells")))

    findings += _scan_phrases(
        "\n".join(phrase_lines), allow_norm=allow_norm, skip_quoted=True
    )

    # Structural scan: body paragraphs only.
    body_blob = "\n\n".join(
        sec.get("body", "")
        for sec in (sections or [])
        if isinstance(sec, dict) and isinstance(sec.get("body"), str)
    )
    if body_blob.strip():
        findings += _scan_structural(
            body_blob, context="brief", skip_quoted=True,
            allow_norm=allow_norm, ban_dashes=ban_dashes,
        )

    return {"verdict": _verdict(findings), "findings": findings}


def summarize_findings(findings: List[dict], *, limit: int = 10) -> str:
    """Compact human-readable summary for a VoiceTellError message or stderr."""
    lines = []
    for f in findings[:limit]:
        lines.append(
            f"  [{f['severity']}:{f['rule']}] line {f['line_no']}: "
            f"{f['match']!r} — {f['hint']}"
        )
    if len(findings) > limit:
        lines.append(f"  …and {len(findings) - limit} more")
    return "\n".join(lines)


__all__ = [
    "scan_text",
    "scan_subject",
    "check_sections",
    "summarize_findings",
    "VoiceTellError",
    "FAIL_BLOCKING_KINDS",
    "ALWAYS_BLOCKING_RULES",
    "FAIL_RULE_COUNT",
]


def _main(argv: List[str]) -> int:
    import sys

    context = "email"
    paths: List[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--context":
            i += 1
            if i < len(argv):
                context = argv[i]
        else:
            paths.append(a)
        i += 1

    if len(paths) != 1:
        print(
            "usage: voice_tell_detector.py <file|-> [--context email|brief|subject]",
            file=sys.stderr,
        )
        return 2

    target = paths[0]
    if target == "-":
        text = sys.stdin.read()
    else:
        from pathlib import Path

        p = Path(target)
        if not p.exists():
            print(f"file not found: {target}", file=sys.stderr)
            return 2
        text = p.read_text(encoding="utf-8", errors="replace")

    if context == "subject":
        result = scan_subject(text.strip("\n"))
    else:
        result = scan_text(text, context=context)
    verdict = result["verdict"]
    findings = result["findings"]

    if verdict == "pass":
        print("OK: no voice tells detected")
        return 0
    if verdict == "warn":
        print(f"WARN: {len(findings)} structural tell(s) — review, save not blocked")
        print(summarize_findings(findings))
        return 0
    # fail
    fails = [f for f in findings if f["severity"] == "fail"]
    print(f"FAIL: {len(fails)} banned phrase(s) — rewrite before saving")
    print(summarize_findings(findings))
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
