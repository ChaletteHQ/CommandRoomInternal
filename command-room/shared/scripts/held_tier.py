#!/usr/bin/env python3
"""
held_tier — the Decision-1 capture routing flip, shipped DARK (SPEC EOD1 §2.4).

WHAT THE FLIP IS
================
CAPTUREFLOW's ruling (M, 2026-08-01) stands unchanged: a below-floor capture is
NEVER silently dropped and never deleted. It goes to the REVIEW tier, where it
becomes one tap in the queue instead of a lost promise.

Decision 1 of the Bookends reconciliation proposes a second disposition for the
weakest of those rows: a HELD tier. Held means out of sight — not queued, not
badged, not counted — and retrievable on request. The row is still WRITTEN and
still on disk forever; what changes is that it stops asking.

WHY IT SHIPS OFF, AND WHY THE SWITCH REFUSES
--------------------------------------------
The 5 PM sort removes the client-side review queue, and that queue is the only
net under the roughly one-in-three meeting-derived captures that were never
promises. So M's ruling ties the flip to a measured bar, and
`precision_gate.py` is that bar in code:

    >=90% of meeting-derived items routed to the open book are verified
    promises, on a >=100-item labelled sample, holding two consecutive
    dogfood weeks.

`enable_held_routing` calls `precision_gate.precision_gate_status(workspace_root)`
and REFUSES while it is not passing. That call is the fence. It is not a
warning, not a log line, and not something the caller may skip: with the gate
red there is no code path in this module that returns an enabled routing.

**The gate is also NAMED in this fire's skill prose.** REVIEW_PREC1's N-1
finding was that `precision_gate.py` was cited by zero shipped skills — code
nothing instructs anyone to consult is inert at the moment it matters, however
correct it is. `skills/end-of-day/SKILL.md` names the module and this refusal,
and a guard test asserts it keeps doing so. Both halves are the fix; either one
alone is the finding restated.

THE DEFAULT IS OFF AND OFF IS BYTE-IDENTICAL
--------------------------------------------
With the flip off, `apply_held_routing` returns the routing it was handed,
unchanged, with an empty held lane. That is asserted rather than assumed: a
dark feature that quietly perturbs the live path is not dark.

Read-mostly. `enable_held_routing` is the ONE writer and it writes through
`skill_config_writer.save_skill_config` (the FRP1 store), never a hand-rolled
file. Stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# The config store this knob lives in: the End of Day fire's own FRP1 config.
CONFIG_SKILL = "end-of-day"

# The knob. Two values, and the default is the pre-EOD1 behavior.
CONFIG_KEY = "weak_capture_routing"
ROUTING_REVIEW = "review"
ROUTING_HELD = "held"
DEFAULT_ROUTING = ROUTING_REVIEW

# Every knob the End of Day fire owns, so `get_config` has a full default set
# and a v+1 knob cannot break an old saved config.
CONFIG_DEFAULTS = {
    # FRP1 first-run decisions (SPEC EOD1 §4).
    "tone": "scoreboard",          # scoreboard | journal
    "slipped_section": "on",       # on | off
    "sign_off": "on",              # on | off
    # Decision 1. DARK. See the module docstring.
    CONFIG_KEY: DEFAULT_ROUTING,
}

# The mark a held row carries. A boolean on the row, plus the reason, so a
# reader can tell a held row from a queue row without consulting a config.
HELD_FLAG = "held"
HELD_REASON_KEY = "held_reason"
HELD_REASON = "below the capture floor, held out of the queue"

# Refusal codes. Stable, UPPER_SNAKE, ordered most-structural first — the
# caller owns the words (the DONT-PRINT-THE-GATE'S-SENTENCE lesson from
# precision_gate's own docstring).
REFUSED_GATE_RED = "GATE_RED"
REFUSED_UNKNOWN_VALUE = "UNKNOWN_VALUE"

# The one sentence a caller may print when the switch refuses. It lives here
# so the wording is pinned once, and it deliberately says what would have to
# change rather than naming a threshold the reader cannot act on.
REFUSAL_LINE = (
    "I am not turning that on yet. Holding weak captures out of sight is only "
    "safe once the capture precision measurement has held its bar for two "
    "weeks running, and it has not."
)


def config_defaults() -> dict:
    """A fresh copy of the knob defaults. Never hand back the module dict — a
    caller mutating it would move the default for the whole process."""
    return dict(CONFIG_DEFAULTS)


def _load_config(workspace_root) -> dict:
    try:
        from skill_config_writer import get_config
        return get_config(workspace_root, CONFIG_SKILL, config_defaults())
    except Exception:  # noqa: BLE001 — an unreadable config is the DEFAULT,
        # which is OFF. A config read that fails must never fail OPEN.
        return config_defaults()


def gate_status(workspace_root) -> dict:
    """The capture-precision gate's verdict, verbatim.

    ONE call site for the gate inside this module, so the fence is one thing
    to find and one thing to remove in a red-proof. Never raises: an
    unreadable report is a week that does not count, which is a FAIL, and a
    gate that crashes is a gate that gets removed.
    """
    from precision_gate import precision_gate_status
    try:
        status = precision_gate_status(workspace_root)
    except Exception:  # noqa: BLE001
        return {"status": "FAIL", "pass": False, "blockers": ["NO_REPORTS"],
                "weeks_found": 0, "reports_found": 0, "weeks": []}
    if not isinstance(status, dict):
        return {"status": "FAIL", "pass": False, "blockers": ["NO_REPORTS"],
                "weeks_found": 0, "reports_found": 0, "weeks": []}
    return status


def flip_status(workspace_root) -> dict:
    """Is the held-tier routing ON right now?

    Returns:
      {"requested": "review"|"held"|<whatever is stored>,
       "enabled": bool,          # the ONLY field the routing path reads
       "gate_pass": bool,
       "gate": <precision_gate_status>,
       "blockers": [CODE, ...],
       "default": "review"}

    `enabled` is `requested == "held"` AND the gate passes. The second half is
    not belt-and-braces: a workspace can carry a stored `held` from a week when
    the gate was green, and the bar is "holding for two consecutive weeks",
    which a workspace can fall out of. The routing asks this function every
    time rather than trusting a value written once.
    """
    cfg = _load_config(workspace_root)
    requested = cfg.get(CONFIG_KEY, DEFAULT_ROUTING)
    gate = gate_status(workspace_root)
    gate_pass = bool(gate.get("pass"))
    blockers = list(gate.get("blockers") or [])
    if requested not in (ROUTING_REVIEW, ROUTING_HELD):
        # An unreadable value is the DEFAULT, and it says so rather than
        # guessing which of the two the workspace meant.
        return {"requested": requested, "enabled": False,
                "gate_pass": gate_pass, "gate": gate,
                "blockers": [REFUSED_UNKNOWN_VALUE] + blockers,
                "default": DEFAULT_ROUTING}
    enabled = (requested == ROUTING_HELD) and gate_pass
    if requested == ROUTING_HELD and not gate_pass:
        blockers = [REFUSED_GATE_RED] + blockers
    return {"requested": requested, "enabled": enabled,
            "gate_pass": gate_pass, "gate": gate, "blockers": blockers,
            "default": DEFAULT_ROUTING}


def enable_held_routing(workspace_root, *, origin: str = "m_action") -> dict:
    """Turn the flip ON. REFUSES while the capture-precision gate is red.

    Returns `{"status": "enabled"|"refused", "blockers": [...],
    "gate": <status>, "line": REFUSAL_LINE|None}`.

    Nothing is written on a refusal — not the value, not a "pending" marker,
    not an event. A stored request that is inert is the shape that makes a
    dark feature look enabled to the next reader.
    """
    gate = gate_status(workspace_root)
    if not gate.get("pass"):
        return {"status": "refused",
                "blockers": [REFUSED_GATE_RED] + list(gate.get("blockers") or []),
                "gate": gate, "line": REFUSAL_LINE}
    from skill_config_writer import get_config, save_skill_config
    cfg = get_config(workspace_root, CONFIG_SKILL, config_defaults())
    cfg[CONFIG_KEY] = ROUTING_HELD
    save_skill_config(workspace_root, CONFIG_SKILL, cfg,
                      is_reconfigure=True, origin=origin)
    return {"status": "enabled", "blockers": [], "gate": gate, "line": None}


def disable_held_routing(workspace_root, *, origin: str = "m_action") -> dict:
    """Turn it back off. NEVER gated — reverting to the shipped default is
    always allowed, and a switch that is hard to turn off is a switch nobody
    turns on."""
    from skill_config_writer import get_config, save_skill_config
    cfg = get_config(workspace_root, CONFIG_SKILL, config_defaults())
    cfg[CONFIG_KEY] = ROUTING_REVIEW
    save_skill_config(workspace_root, CONFIG_SKILL, cfg,
                      is_reconfigure=True, origin=origin)
    return {"status": "disabled", "blockers": [], "line": None}


def is_floor_gated(event: dict) -> bool:
    """Is this a below-floor capture (the rows the flip is about)?

    Keyed on `data.floor_gated`, the marker `meeting_capture` already stamps —
    "because a future config toggle needs something to key on", in that
    module's own words. Fusion refusals (`fusion_unverified`) are a DIFFERENT
    question and are never held: the guardrail establishing that a phrase is
    absent from the transcript it is attributed to is exactly the case that
    must keep asking.
    """
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return bool(data.get("floor_gated"))


def apply_held_routing(routed: dict, workspace_root, *,
                       status: Optional[dict] = None) -> dict:
    """Post-process a `meeting_capture.route_meeting_captures` return.

    With the flip OFF (the shipped default, and the state every workspace is
    in): returns the routing UNCHANGED plus an empty `held` lane and
    `held_routing: "review"`. The book / review / observed lists are the same
    objects that came in.

    With the flip ON: every FLOOR-GATED review row moves to `held`, stamped
    `data.held = True` and `data.held_reason`, and leaves `n_review`. It gains
    `n_held` on the summary — the audit half, so a held row is invisible on the
    SURFACES and fully visible to a reviewer reading the fire's own record.
    Held rows are still appended by the caller: held is a disposition, never a
    deletion.

    `status` is accepted so a caller that already asked can pass the answer
    rather than paying for a second gate read; when omitted this asks
    `flip_status`, which asks the gate.
    """
    src = routed if isinstance(routed, dict) else {}
    state = status if isinstance(status, dict) else flip_status(workspace_root)
    summary = dict(src.get("summary") or {})

    if not state.get("enabled"):
        out = dict(src)
        out["held"] = []
        out["held_routing"] = ROUTING_REVIEW
        summary["n_held"] = 0
        out["summary"] = summary
        return out

    review_in = list(src.get("review") or [])
    review_out, held = [], []
    for ev in review_in:
        if isinstance(ev, dict) and is_floor_gated(ev):
            data = ev.setdefault("data", {})
            data[HELD_FLAG] = True
            data[HELD_REASON_KEY] = HELD_REASON
            held.append(ev)
        else:
            review_out.append(ev)

    out = dict(src)
    out["review"] = review_out
    out["held"] = held
    out["held_routing"] = ROUTING_HELD
    summary["n_review"] = len(review_out)
    summary["n_held"] = len(held)
    out["summary"] = summary
    return out


def appendable(routed: dict) -> list:
    """Everything the caller appends in ONE locked write: book + review +
    observed + HELD.

    Held rows are in this list on purpose. "Out of sight" is a property of the
    surfaces, not of the disk: a held capture the fire never wrote could not be
    retrieved on request, which is the one promise the held tier makes.
    """
    src = routed if isinstance(routed, dict) else {}
    return (list(src.get("book") or []) + list(src.get("review") or [])
            + list(src.get("observed") or []) + list(src.get("held") or []))


def held_ids(routed: dict) -> list:
    """The commitment ids this fire held, for the confirm block's fence."""
    out = []
    for ev in (routed or {}).get("held") or []:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        cid = data.get("id") or data.get("commitment_id")
        if cid:
            out.append(str(cid))
    return out


def load_held(workspace_root, *, limit: Optional[int] = None) -> list:
    """Everything currently held, newest first — the "retrievable on request"
    half of the promise.

    Nothing else in the product reads this: a held row is out of every queue,
    every badge and every count by construction, and the ONE way back to it is
    a person asking.
    """
    from event_refs import load_events

    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    rows = []
    for ev in load_events(path):
        if not isinstance(ev, dict) or ev.get("type") != "commitment":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if not data.get(HELD_FLAG):
            continue
        rows.append({"commitment_id": data.get("id"),
                     "title": data.get("title"),
                     "ts": ev.get("ts"),
                     "reason": data.get(HELD_REASON_KEY) or HELD_REASON,
                     "source_ref": data.get("source_ref")})
    rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return rows[:limit] if limit else rows


__all__ = [
    "CONFIG_SKILL", "CONFIG_KEY", "CONFIG_DEFAULTS",
    "ROUTING_REVIEW", "ROUTING_HELD", "DEFAULT_ROUTING",
    "HELD_FLAG", "HELD_REASON_KEY", "HELD_REASON",
    "REFUSED_GATE_RED", "REFUSED_UNKNOWN_VALUE", "REFUSAL_LINE",
    "config_defaults", "gate_status", "flip_status",
    "enable_held_routing", "disable_held_routing",
    "is_floor_gated", "apply_held_routing", "appendable", "held_ids",
    "load_held",
]
