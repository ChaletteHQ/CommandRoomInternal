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
promises. So the flip is FENCED — and HELDOP1 changed what the fence is.

It used to be a measurement each workspace took of itself (a capture-precision
bar, read by a gate that scanned that workspace for census records). That was
the wrong fence, and not by a little: the only thing that can WRITE a census
record lives at repo root, outside everything a workspace ever receives, so the
directory the gate scanned was empty forever. Every workspace but the
operator's own read RED permanently, which says "your accuracy is bad" while
meaning "the instrument was never delivered". M retired that bar on 2026-08-22.

The fence now is an OPERATOR-ISSUED CAPABILITY: `operator_capability`
`held_tier_routing`, granted in the plugin payload
(`shared/config/operator_capabilities.json`) and therefore writable only by a
release. `enable_held_routing` calls `capability_status(workspace_root)` here,
which asks that module, and REFUSES while the capability is absent. That call
is the fence. It is not a warning, not a log line, and not something the caller
may skip: without the grant there is no code path in this module that returns
an enabled routing.

**Nothing a workspace can edit satisfies it.** The capability read is anchored
to the payload and takes no workspace argument at all, so writing the routing
value — or any other key, under any name — into this skill's own config grants
nothing. That is the property to protect if this module is ever refactored, and
it is pinned by removal in `tests/run_heldop1_test.py`.

**The fence is also NAMED in this fire's skill prose.** REVIEW_PREC1's N-1
finding was that the old gate module was cited by zero shipped skills — code
nothing instructs anyone to consult is inert at the moment it matters, however
correct it is. `skills/end-of-day/SKILL.md` names the capability module and
this refusal, and a guard test asserts it keeps doing so. Both halves are the
fix; either one alone is the finding restated.

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
    # SPEC OVERDUE1 D1 (M's ruling R-3: "3-4 days"). How many days past its due
    # date an item may be before the slipped block asks about it once and then
    # stops repeating it. Whole days, 1 or more; the reader is
    # `end_of_day.overdue_ask_after_days`, which falls back to its own default
    # on anything else. It lives here because this dict is the ONE full default
    # set for this fire's config — a knob declared anywhere else is a knob
    # `get_config` cannot fill in for a workspace whose saved config predates it.
    "overdue_ask_after_days": 3,
}

# The mark a held row carries. A boolean on the row, plus the reason, so a
# reader can tell a held row from a queue row without consulting a config.
HELD_FLAG = "held"
HELD_REASON_KEY = "held_reason"
HELD_REASON = "below the capture floor, held out of the queue"

# Refusal codes. Stable, UPPER_SNAKE, ordered most-structural first — the
# caller owns the words, never the fence.
REFUSED_NOT_GRANTED = "NOT_GRANTED"
REFUSED_UNKNOWN_VALUE = "UNKNOWN_VALUE"

# The one sentence a caller may print when the switch refuses. It lives here so
# the wording is pinned once, and it names NO measurement: the reader — an
# owner, in their own workspace — has no measurement to act on and never did,
# and a refusal that implies otherwise is read as an accusation about their own
# accuracy. It says what is true instead, including the last line, which is the
# whole correction (HELDOP1 D3).
#
# The middle clause is deliberate too (REVIEW_HELDOP1 F-1). It says the people
# who build this are satisfied it is SAFE — never that they have READ anything,
# because to an owner "once we have looked at what it would hide" describes us
# reading THEIR captures, which is not what happens and not a thing to imply in
# a sentence whose whole job is to reassure. The pin carries these words
# literally.
REFUSAL_LINE = (
    "I am not turning that on. Holding weak captures out of sight is not a "
    "setting in this workspace — it is switched on in the Command Room release "
    "itself, once the people who build it are satisfied it is safe. Nothing "
    "about your own numbers is holding it back."
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


def capability_status(workspace_root=None) -> dict:
    """Has the operator issued the held-tier capability with this release?

    ONE call site for the fence inside this module, so it is one thing to find
    and one thing to remove in a red-proof — the same single-seam design the
    measurement gate had, pointed at the thing that actually decides now.

    `workspace_root` is accepted so the seam keeps the shape of what it
    replaced, and it is DELIBERATELY UNUSED. That is not an oversight to tidy
    up later: a capability that could be read out of a workspace is a
    capability that workspace could grant itself, which is the entire fence.
    Anyone refactoring this signature should read the pin in
    `tests/run_heldop1_test.py` first.

    Never raises. Anything unreadable is NOT GRANTED — a fence that crashes is
    a fence that gets removed, and one that fails open is not a fence.
    """
    del workspace_root  # see above: the fence is that this is never consulted.
    try:
        import operator_capability as _oc
        status = _oc.capability_status(_oc.HELD_TIER_ROUTING)
    except Exception:  # noqa: BLE001
        return {"capability": "held_tier_routing", "granted": False,
                "blockers": ["UNREADABLE_CAPABILITY_FILE", REFUSED_NOT_GRANTED],
                "granted_on": None, "source": None}
    if not isinstance(status, dict):
        return {"capability": "held_tier_routing", "granted": False,
                "blockers": ["UNREADABLE_CAPABILITY_FILE", REFUSED_NOT_GRANTED],
                "granted_on": None, "source": None}
    return status


def flip_status(workspace_root) -> dict:
    """Is the held-tier routing ON right now?

    Returns:
      {"requested": "review"|"held"|<whatever is stored>,
       "enabled": bool,          # the ONLY field the routing path reads
       "granted": bool,
       "capability": <operator_capability.capability_status>,
       "blockers": [CODE, ...],
       "default": "review"}

    `enabled` is `requested == "held"` AND the capability is granted. The
    second half is not belt-and-braces, and it is the reason this is a function
    rather than a stored boolean: a workspace can carry a `held` value written
    while a release granted the capability and still be running a release that
    does not. The routing asks this function EVERY time rather than trusting a
    value written once — a stored request is a request, never a grant.
    """
    cfg = _load_config(workspace_root)
    requested = cfg.get(CONFIG_KEY, DEFAULT_ROUTING)
    capability = capability_status(workspace_root)
    granted = bool(capability.get("granted"))
    blockers = list(capability.get("blockers") or [])
    if requested not in (ROUTING_REVIEW, ROUTING_HELD):
        # An unreadable value is the DEFAULT, and it says so rather than
        # guessing which of the two the workspace meant.
        return {"requested": requested, "enabled": False,
                "granted": granted, "capability": capability,
                "blockers": [REFUSED_UNKNOWN_VALUE] + blockers,
                "default": DEFAULT_ROUTING}
    enabled = (requested == ROUTING_HELD) and granted
    return {"requested": requested, "enabled": enabled,
            "granted": granted, "capability": capability,
            "blockers": blockers, "default": DEFAULT_ROUTING}


def enable_held_routing(workspace_root, *, origin: str = "m_action") -> dict:
    """Turn the flip ON. REFUSES unless the operator issued the capability.

    Returns `{"status": "enabled"|"refused", "blockers": [...],
    "capability": <status>, "line": REFUSAL_LINE|None}`.

    Nothing is written on a refusal — not the value, not a "pending" marker,
    not an event. A stored request that is inert is the shape that makes a
    dark feature look enabled to the next reader.

    This is the ONE writer, and HELDREVIEW1's enable phrase is its caller: the
    review is what earns the grant, and the grant is a release, so nothing that
    runs inside a workspace can substitute for either.
    """
    capability = capability_status(workspace_root)
    if not capability.get("granted"):
        return {"status": "refused",
                "blockers": [REFUSED_NOT_GRANTED]
                + [b for b in (capability.get("blockers") or [])
                   if b != REFUSED_NOT_GRANTED],
                "capability": capability, "line": REFUSAL_LINE}
    from skill_config_writer import get_config, save_skill_config
    cfg = get_config(workspace_root, CONFIG_SKILL, config_defaults())
    cfg[CONFIG_KEY] = ROUTING_HELD
    save_skill_config(workspace_root, CONFIG_SKILL, cfg,
                      is_reconfigure=True, origin=origin)
    return {"status": "enabled", "blockers": [], "capability": capability,
            "line": None}


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
    rather than paying for a second capability read; when omitted this asks
    `flip_status`, which asks the fence.
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
    "REFUSED_NOT_GRANTED", "REFUSED_UNKNOWN_VALUE", "REFUSAL_LINE",
    "config_defaults", "capability_status", "flip_status",
    "enable_held_routing", "disable_held_routing",
    "is_floor_gated", "apply_held_routing", "appendable", "held_ids",
    "load_held",
]
