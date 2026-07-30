#!/usr/bin/env python3
"""Phase-ordering meta-guard (SPEC FOLDERGUARD §2.5).

The generalizable assertion behind the phantom-folder bug: **a finding must not
disappear during a fire unless something claims credit for fixing it.**

The bug this exists to catch ran undetected for weeks because it was
self-concealing. Phase 3a raised `C9.thread_folder_missing`. Phases 3.5a/3.5b
then ran *later in the same fire* and created exactly those folders. The next
week's scan found C9 = 0 and reported clean. Nothing converged, nothing was
repaired, and the run record never mentioned it — a later phase had silently
satisfied an earlier phase's complaint by creating the very thing whose absence
was the finding.

No single phase is wrong in isolation, which is why per-phase assertions miss
it. The defect is only visible *across* the fire: findings at scan time vs.
findings at end of fire, reconciled against `actions_taken[]`.

Not specific to C9. Any check that vanishes with no matching remediation entry
is either a silent self-heal (fine, but say so) or a phase writing over a
finding it should have surfaced (not fine). Both are worth a line in the run
record; today neither produces one.

Usage inside a cleanup fire:

    import phase_order_guard as pog
    before = pog.snapshot(ic.run_checks(root))      # Phase 3a
    ...                                             # phases run, actions_taken[] accrues
    after = pog.snapshot(ic.run_checks(root))       # end of fire
    unexplained = pog.reconcile(before, after, actions_taken)
    if unexplained:
        print(pog.format_report(unexplained))

stdlib only.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = ["snapshot", "reconcile", "format_report", "key_of"]


def _norm(s: Any) -> str:
    return (s or "").strip().lower()


def key_of(finding: Any) -> tuple[str, str]:
    """Identity of a finding: (check, subject), case-folded.

    Deliberately NOT the message — messages carry counts and paths that churn
    between runs, and a churned message would read as "disappeared" every fire.
    """
    if isinstance(finding, dict):
        return (_norm(finding.get("check")), _norm(finding.get("subject")))
    return (_norm(getattr(finding, "check", "")), _norm(getattr(finding, "subject", "")))


def snapshot(findings: Iterable[Any]) -> list[dict]:
    """Freeze a findings list into plain dicts safe to hold across a fire.

    Accepts `integrity_check.Finding` objects or their `as_dict()` form.
    """
    out: list[dict] = []
    for f in findings or []:
        if isinstance(f, dict):
            d = dict(f)
        elif hasattr(f, "as_dict"):
            d = f.as_dict()
        else:
            d = {
                "check": getattr(f, "check", ""),
                "severity": getattr(f, "severity", ""),
                "subject": getattr(f, "subject", ""),
                "message": getattr(f, "message", ""),
            }
        out.append(d)
    return out


def _action_text(action: Any) -> str:
    """Flatten an actions_taken[] entry to searchable text.

    Entries are free-form — cleanup records plain strings today, and richer
    dicts in places. Both must be searchable or the guard produces false
    "unexplained" noise on a run that did the right thing.
    """
    if isinstance(action, str):
        return _norm(action)
    if isinstance(action, dict):
        return _norm(" ".join(str(v) for v in action.values() if v is not None))
    return _norm(str(action))


_TOKEN_RE = re.compile(r"[a-z0-9_.]+")


def _tokens(text: str) -> set[str]:
    """Whole tokens of an action line, plus the head of each dotted token.

    `c9.thread_folder_missing` contributes both itself and `c9`, so a bare `c9`
    mention and the full id are compared the same way — by EQUALITY against a
    whole token. Substring comparison is what let `c1` match `c10`/`c11`/`c12`,
    so an action naming `C10.orphan_folder` explained a vanished `C1` finding.
    """
    out: set[str] = set()
    for tok in _TOKEN_RE.findall(text or ""):
        tok = tok.strip(".")
        if not tok:
            continue
        out.add(tok)
        if "." in tok:
            out.add(tok.split(".")[0])
    return out


def _names_subject(subject: str, text: str) -> bool:
    """True iff `text` names `subject` as a whole phrase, not as a substring.

    Boundary-anchored on both ends, so `project_100` does NOT name `project_10`,
    and a remediation entry for `project_007` does not explain `project_008`.
    Subjects are free text (thread ids, folder names with spaces, aliases), so
    this is a phrase match rather than a token-set lookup.
    """
    if not subject:
        return False
    pat = r"(?<![0-9a-z_])" + re.escape(subject) + r"(?![0-9a-z_])"
    return re.search(pat, text) is not None


def _explained_by(finding: dict, actions: list[str]) -> bool:
    """True if some actions_taken[] entry claims THIS finding.

    Subject-keyed, and deliberately no longer permissive. The original matcher
    accepted a bare check-id mention anywhere in the run log, which meant one
    line reading "Phase 3d flagged c9 findings for the user" explained EVERY C9
    disappearance — and a mass C9 disappearance is precisely the bug this guard
    was written to catch. Permissiveness was traded for the guard's own headline
    case, so the trade is off: a finding that carries a subject is explained
    only by an entry naming that subject.

    A finding with no subject has nothing else to key on, so it falls back to an
    exact check-id match — equality against whole tokens, never a substring.
    """
    check = _norm(finding.get("check"))
    subject = _norm(finding.get("subject"))
    check_head = check.split(".")[0] if check else ""
    for text in actions:
        if subject:
            if _names_subject(subject, text):
                return True
            continue
        toks = _tokens(text)
        if (check and check in toks) or (check_head and check_head in toks):
            return True
    return False


def reconcile(before: Iterable[Any],
              after: Iterable[Any],
              actions_taken: Iterable[Any] | None = None) -> list[dict]:
    """Findings present at scan, absent at end of fire, and claimed by nobody.

    Returns the unexplained disappearances, each with a `reason`. An empty list
    is the healthy case: everything that vanished has a remediation entry behind
    it.
    """
    before_snap = snapshot(before)
    after_keys = {key_of(f) for f in snapshot(after)}
    actions = [_action_text(a) for a in (actions_taken or [])]

    unexplained: list[dict] = []
    for f in before_snap:
        if key_of(f) in after_keys:
            continue  # still open — not our problem
        if _explained_by(f, actions):
            continue  # something claimed credit
        item = dict(f)
        item["reason"] = (
            "finding disappeared during the fire with no matching actions_taken[] "
            "entry — a later phase may have satisfied it by writing the thing "
            "whose absence was the finding")
        unexplained.append(item)
    return unexplained


def format_report(unexplained: list[dict]) -> str:
    """One-line-per-finding report for the run log. Empty string when clean."""
    if not unexplained:
        return ""
    lines = [f"PHASE-ORDER GUARD — {len(unexplained)} finding(s) vanished unexplained:"]
    for f in unexplained:
        subject = f.get("subject") or "?"
        lines.append(f"  - {f.get('check')} [{subject}] {f.get('message', '')}".rstrip())
    return "\n".join(lines)
