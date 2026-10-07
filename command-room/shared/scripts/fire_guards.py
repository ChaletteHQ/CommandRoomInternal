#!/usr/bin/env python3
"""The two questions a scheduled fire asks before it does anything (FIX3 F3-2).

The bootloader used to ask them with three `python3 -c` bodies pasted into its
own prose. That was the defect the re-walk of 2026-09-21 caught (ruling
R-RW-3): a python body in the instruction layer is text the model retypes, it
opens the customer's workspace from wherever the model happens to be, and the
battery cannot see any of it because prose is not code. Guard G69 exists to
stop exactly that, and the TEMPLATE was carrying three of them.

So the two questions move here, as functions, and the template renders one
`plan run_helper` form for each:

    root_guard(root)      -> {"blocked": bool, "state": str, "detail": str}
    retirement(task_id)   -> {"retired": bool, "line": str}

Night M3 (RETIRE1 R-2, SAFETY0) adds a third question, asked the same way:

    scheduled_writer(root) -> {"foreign": bool, "declared": str|None,
                               "forwarded": str|None, "line": str}

READ AND COMPUTE ONLY. `root_guard` is PATHREPAIR1's verdict with the APPLY
leg removed: it plans, it reports, and it never writes — no fingerprint
backfill, no config rewrite, no event. That is not a narrowing of the guard, it
is where the guard already stood on this path (R-HEAL1-4: the merged fire does
not run the repair; the health check and the weekly cleanup own it). A fire's
job is to notice and stop, not to fix the registration underneath itself.

THE ONE DELIBERATE EXCEPTION TO THE ALLOW-LIST'S RULE 3 (ruling R-FIX3-2).
`ok: false` from either of these two helpers is the FALL-THROUGH — the chat
PROCEEDS — for the reason the template has always stated in words: a live chat
must never be silenced by a hiccup in the guard that was only ever a belt. The
cost of guessing wrong in that direction is one unchecked fire; the cost of
guessing wrong in the other is a working chat that goes quiet and never says
why. Both functions are written so that reaching a verdict is the only way to
get a blocking answer: every failure below answers "not blocked", "not
retired".

3.10-safe, stdlib only: this runs in the sandbox VM and in the container.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

#: What a stale-but-repairable registration is called here. PATHREPAIR1's own
#: vocabulary has no word for it, because on its path the answer is a repair;
#: on a fire's path nothing repairs, so the state needs a name of its own.
STATE_STALE = "STALE"


def _quiet(state: str, detail: str = "") -> Dict[str, Any]:
    return {"blocked": False, "state": state, "detail": detail}


def root_guard(root) -> Dict[str, Any]:
    """Does this workspace's own registration still agree with where it is?

    `blocked` is True on ONE answer only: the registration is demonstrably
    dead and cannot be resolved without guessing — two candidate folders, or
    none, or nothing to compare a candidate against. Everything else is quiet:

      ALIVE    the stored root is this root. Nothing to say.
      UNKNOWN  the fire is inside a session mount, or the registration belongs
               to another machine's own entry. Neither is evidence the folder
               moved (PATHREPAIR1 v2, rulings 1/2/3), so nothing alarms and
               nothing repairs — on this path, every fire in a container is
               this answer, forever, silently.
      STALE    the stored root is dead and a repair WOULD succeed. The fire
               does not perform it; it proceeds and lets the health check
               correct the record.
      DEAD     blocked. The template's message is the whole turn, and the
               chat stops.
    """
    try:
        import path_repair as pr
    except Exception:  # noqa: BLE001 — the fall-through, stated above
        return _quiet("UNAVAILABLE", "the root guard could not be loaded")
    try:
        reg = pr.registration(root)
        vantage = pr.current_vantage(root)
        plan = pr.plan_repair(reg, trusted_candidate=root, vantage=vantage)
    except Exception as exc:  # noqa: BLE001
        return _quiet("UNAVAILABLE", type(exc).__name__)

    verdict = str(plan.get("class") or "")
    if verdict == "alive":
        return _quiet(getattr(pr, "STATE_ALIVE", "ALIVE"))
    if verdict == "unknown":
        return _quiet(getattr(pr, "STATE_UNKNOWN", "UNKNOWN"),
                      str(plan.get("reason") or ""))
    if verdict == "repaired":
        # A repair would succeed — and this is the one caller that must not
        # perform it. Say so and carry on.
        return _quiet(STATE_STALE,
                      "the registration is repairable; the health check "
                      "owns the repair, not this fire")
    return {
        "blocked": True,
        "state": getattr(pr, "STATE_DEAD", "DEAD"),
        "detail": verdict or "unrepairable",
    }


def retirement(task_id: str) -> Dict[str, Any]:
    """Has this chat been retired, and if so what is its one sentence?

    Membership AND class come from the registry, never from a name anyone
    remembers. A RENAMED row is deliberately NOT retired: its orchestrator is
    the live successor pack, so that chat keeps firing the real surface. Every
    other retired class — eliminated, readiness, and whatever the registry
    grows later — lands here, which is the safe direction, because the file
    such an id resolves to is a stub that can only post one line anyway.

    `line` is `schedule_config.retirement_line`'s own output, stripped. That
    function is the ONE renderer for this sentence: the template prints what
    comes back here, and the stub the orchestrator would otherwise reach
    prints the same string, so the two paths cannot answer one customer two
    ways.
    """
    blank = {"retired": False, "line": ""}
    tid = str(task_id or "").strip()
    if not tid:
        return blank
    try:
        import schedule_config as sc
    except Exception:  # noqa: BLE001 — the fall-through, stated above
        return blank
    try:
        if not sc.is_retired_task(tid) or sc.is_renamed_task(tid):
            return blank
        line = (sc.retirement_line(tid) or "").strip()
    except Exception:  # noqa: BLE001
        return blank
    if not line:
        return blank
    return {"retired": True, "line": line}


def scheduled_writer(root) -> Dict[str, Any]:
    """Is this fire the workspace's declared scheduled writer? (SAFETY0)

    `declared` is the workspace's declaration
    (`schedule_config.scheduled_writer`), `forwarded` the writer id this
    fire was HANDED — `writer_identity.forwarded_pair`, read from the
    process environment the rendered command set. `foreign` is True when a
    writer is declared and the forwarded id is not it — including when there
    is no forwarded id at all: an unidentified fire on a declared workspace is
    exactly the un-merged computer's shape once its plugin updates. When
    foreign, `line` is the one sentence that is the whole turn; otherwise
    `line` is "".

    IDENTITY BY HANDING, NEVER BY READING (D-1). This never reads
    `_hq/.system/writer_identity.json` and never derives an id: the other
    computer mounts the same folder, so anything read there would let it
    pass as this one.

    PER COMPUTER (HYGIENE3, R-RW3-2, D-4). The writer id is account +
    folder name, so two computers of one account derive the same id. When
    the declaration carries a `device_digest` AND this fire was handed a
    `CR_DEVICE_WORKSPACE` (every rendered line forwards it), the digest of
    that path must match too; a different folder path is another computer,
    and the answer is `foreign` with `device_mismatch: True`. A declaration
    without a digest, or a fire without the path, compares ids only - an
    old declaration stays valid (no migration), and the direction is still
    fail-open.

    READ ONLY — it reads the declaration and the environment, and writes
    nothing (the allow-list's write-scan walks it). Every failure answers
    `foreign: False`, the R-FIX3-2 direction: a guard that cannot reach a
    verdict never silences a chat.
    """
    out: Dict[str, Any] = {"foreign": False, "declared": None,
                           "forwarded": None, "line": ""}
    try:
        import schedule_config as sc
        import writer_identity as wi
    except Exception:  # noqa: BLE001 — the fall-through, stated above
        return out
    try:
        decl = sc.scheduled_writer(root)
        pair = wi.forwarded_pair(dict(os.environ))
        declared = decl.get("writer_id") if isinstance(decl, dict) else None
        forwarded = pair[0] if pair else None
        foreign = declared is not None and forwarded != declared
        device_mismatch = False
        if declared is not None and not foreign:
            want = decl.get("device_digest") if isinstance(decl, dict) else None
            here = sc.device_digest(os.environ.get(sc.DEVICE_WORKSPACE_ENV))
            if want and here and here != want:
                foreign = device_mismatch = True
        line = sc.SCHEDULED_WRITER_LINES["foreign"] if foreign else ""
    except Exception:  # noqa: BLE001
        return out
    answer = {"foreign": bool(foreign), "declared": declared,
              "forwarded": forwarded, "line": line}
    if device_mismatch:
        answer["device_mismatch"] = True
    return answer


__all__ = ["STATE_STALE", "retirement", "root_guard", "scheduled_writer"]


def _main(argv: Optional[list] = None) -> int:
    """A tiny CLI, for a human looking at one workspace by hand."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="The two read-only questions a scheduled fire asks first.")
    parser.add_argument("--root", help="workspace root, for the root guard")
    parser.add_argument("--task-id", help="task id, for the retirement probe")
    args = parser.parse_args(argv)
    out: Dict[str, Any] = {}
    if args.root:
        out["root_guard"] = root_guard(args.root)
    if args.task_id:
        out["retirement"] = retirement(args.task_id)
    print(json.dumps(out, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
