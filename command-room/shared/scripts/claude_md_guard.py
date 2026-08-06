#!/usr/bin/env python3
"""Before/after diff guard for CLAUDE.md edits (CLAUDEMD1 Defect B — a
cleanup pass deleted an operating rule and nothing said so).

A compression step that summarises a section loses any bullet the summariser
judged non-essential, without diffing what it dropped — so any rule expressed
as a single bullet inside a compressible section is at risk. This guard makes
the drop visible: every edit pass over CLAUDE.md snapshots the file first,
then reports what `removed_lines(before, after)` returns — BY CONTENT, never
as a count. Same HONEST1 posture as cleanup's brain reporting: collect and
surface, never swallow.

Rewording is allowed; dropping is not. A before-line counts as PRESERVED when
some after-line shares enough of its tokens (Jaccard >= 0.5 by default —
a reworded sentence keeps most of its content words; a deleted one matches
nothing). Generated LIVE-STATE blocks are machine-owned and excluded from
the diff (render_claude_md redraws them wholesale by design).

stdlib only.
"""
from __future__ import annotations

import re

_GENERATED_RE = re.compile(
    r"<!--\s*LIVE-STATE:.*?/LIVE-STATE:[^>]*-->", re.S | re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Imperative operating-rule markers. Deliberately broad: a false positive
# costs one verbatim-preserved line; a false negative silently loses a rule.
_RULE_RE = re.compile(
    r"\b(never|always|must(?:\s+not)?|do\s+not|don'?t|only\s+(?:when|if|after)|"
    r"nothing\s+(?:touches|is|gets)|until\s+the\s+user|require[sd]?|"
    r"no\s+\w+\s+(?:without|until|unless)|before\s+(?:any|every))\b",
    re.IGNORECASE)


def _strip_generated(text: str) -> str:
    return _GENERATED_RE.sub("", text)


def _content_lines(text: str) -> list[str]:
    out = []
    for ln in _strip_generated(text).splitlines():
        s = ln.strip()
        if not s or set(s) <= {"-", "=", "*", "#", "_", "|", " "}:
            continue  # blank lines and pure rules/dividers aren't content
        out.append(s)
    return out


def _tokens(line: str) -> set[str]:
    return set(_TOKEN_RE.findall(line.lower()))


def _preserved(before_line: str, after_lines_tokens: list[set[str]],
               threshold: float) -> bool:
    bt = _tokens(before_line)
    if not bt:
        return True
    for at in after_lines_tokens:
        union = bt | at
        if union and len(bt & at) / len(union) >= threshold:
            return True
    return False


def is_rule_language(line: str) -> bool:
    """Does this line carry an imperative operating instruction?"""
    return bool(_RULE_RE.search(line))


def removed_lines(before: str, after: str, *, threshold: float = 0.5) -> list[str]:
    """Content lines present in `before` with no counterpart (exact or
    reworded) in `after`, hand-authored regions only."""
    after_tokens = [_tokens(l) for l in _content_lines(after)]
    return [l for l in _content_lines(before)
            if not _preserved(l, after_tokens, threshold)]


def report(before: str, after: str, *, threshold: float = 0.5) -> dict:
    """The reporting contract for any pass that edits CLAUDE.md.

    `ok` means nothing was dropped. When it is False the caller MUST surface
    every line in `removed` verbatim, and for each line in `removed_rules`
    either restore it verbatim or refuse the compression of its section and
    say why. A backup on disk is recovery, not disclosure."""
    removed = removed_lines(before, after, threshold=threshold)
    rules = [l for l in removed if is_rule_language(l)]
    return {"removed": removed, "removed_rules": rules, "ok": not removed}
