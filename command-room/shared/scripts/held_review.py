#!/usr/bin/env python3
"""
held_review — "show me what you'd hide", and the two phrases either side of it.

WHAT THIS IS FOR
================
The held tier routes the weakest captures out of sight. `held_tier` owns the
routing and `operator_capability` owns the grant that lets it be switched on at
all. Neither of them answers the only question worth asking before switching
it on: **what, exactly, would stop being asked about?**

This module is that answer, and the two deliberate phrases around it.

  1. THE READING CHAIR. `build_would_hold_view` / `render_review_page` show the
     rows the held tier WOULD hide — every below-floor capture on file, grouped
     by the call it came from, counts first, each carrying the plain-words
     reason it was refused at the capture floor. Rendering IS the review.
  2. THE ENABLE. `turn_on_held` is the ONE caller of
     `held_tier.enable_held_routing`. It refuses unless all three of these are
     true, and writes NOTHING when it refuses.
  3. THE DISABLE. `turn_off_held` — the same shape, and never fenced.

WHAT THE ENABLE REQUIRES, AND WHY IT IS THREE THINGS
----------------------------------------------------
  a. The rows have been LOOKED AT in this workspace at least once. A switch
     that can be thrown without seeing what it hides is the switch the whole
     spec exists to avoid.
  b. The PAYLOAD GRANT (`operator_capability.held_tier_routing`, read through
     `held_tier.capability_status`). A release action. HELDOP1's fence,
     untouched here: this module never reads the grant file itself and never
     writes the routing value by any path but `enable_held_routing`.
  c. A WORKSPACE-LOCAL OPERATOR FLAG, in this workspace's own entities.

(c) is not belt-and-braces and it is not a second copy of (b). The grant ships
in the payload, and `scripts/promote_core_to_clients.py` copies
`command-room/shared/config/` verbatim to every client repo — so the grant is
FLEET-WIDE by construction (REVIEW_HELDOP1 F-2). Without (c), the day the
operator grants the capability so he can enable the flip on his own book is the
day every workspace on that release can enable it too, by saying a phrase that
ships in the skill. The grant answers "is this disposition safe to have?"; the
local flag answers "is this the workspace it was turned on for?" They are
different questions and they have different writers.

THE LOCAL FLAG IS A PRODUCT BOUNDARY, NOT A TAMPER BOUNDARY
-----------------------------------------------------------
It lives in `entities.json`'s `workspace` block and is written through
`workspace_settings.set_workspace_settings` — the canonical writer, so the
change carries its own `workspace_setting_changed` receipt and cannot be made
without one. A person with a text editor can set it; that is fine and it is
said out loud here rather than overclaimed. What it must never be is a KNOB:
no shipped skill text names it, nothing offers to set it, and no phrase
reaches its writer. It is not in the payload either, deliberately — a
per-workspace grant inside the payload is a self-declared id every client can
read and restate (REVIEW_HELDOP1 §3(a)), and divergence written by the promote
flow is a different build (§3(b)).

The name of the flag is checked ABSENT from every shipped skill file by
`tests/run_heldreview1_test.py`, with a non-vacuity control, because a knob
nobody was offered is one sentence of prose away from being a knob.

WHAT THE REVIEW MAY NOT DO
--------------------------
It does not confirm and it does not drop — those stay on the ordinary queue.
Mixing "is this list acceptable?" with "resolve these rows" would make a
review destructive (DD-4). Its only verb is an OBJECTION: one capture saying
this row would be wrong to hide, recorded once and never asked about again. An
objection is a note, not a new pile to clear.

Read-mostly. Three writes exist and each is one append: the render receipt, an
objection, and — through `held_tier` alone — the routing value. Stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SOURCE_SKILL = "held-review"

# The workspace-local operator flag (SPEC_HELDREVIEW1 §9). A `workspace.*` key
# in entities.json, written ONLY through `set_local_flag` below, which goes
# through the canonical `workspace_settings` writer so the receipt and the
# write are one call. NEVER named in shipped skill prose — see the docstring.
LOCAL_FLAG_KEY = "held_tier_operator_workspace"

# The receipt the review writes when it renders, and the objection a row can
# carry. Both are registered in shared/data-schemas/events.schema.json.
RENDERED_EVENT = "held_review_rendered"
OBJECTION_EVENT = "held_review_objection"

# The review surface's ONE verb. Registered in `verb_taxonomy` so the renderer
# will draw it; deliberately NOT any of QUEUE_ROW_ACTIONS — a review that can
# confirm or drop is not a review (DD-4).
OBJECTION_ACTION = "wrong to hide"

# Blocker codes, most-structural first. The caller owns the words; this module
# hands back codes, booleans and one pinned sentence per refusal.
REFUSED_NOT_REVIEWED = "NOT_REVIEWED"
REFUSED_NOT_GRANTED = "NOT_GRANTED"
REFUSED_NOT_THIS_WORKSPACE = "NOT_THIS_WORKSPACE"

# --- The refusal sentences -------------------------------------------------
#
# Each is pinned by its literal words in `tests/run_heldreview1_test.py`, and
# each is pinned NEGATIVELY as well: none of them may imply that anyone reads
# the owner's captures to decide this (REVIEW_HELDOP1 F-1/F-7/F-8 — the same
# defect has now been found three times in three files, so the sentences that
# replaced it are held from both directions).

# (a) The phrase used before the rows have been looked at. ONE sentence, and
# it names the phrase that shows them rather than describing a procedure.
LOOK_FIRST_LINE = (
    "Have a look first — say \"show me what you'd hide\" and read the rows "
    "this would stop asking you about, then say it again."
)

# (c) The grant is present but this is not a workspace it was turned on for.
# Says where the decision lives, names no flag, and clears the reader in the
# same breath: their own numbers were never the question.
NOT_THIS_WORKSPACE_LINE = (
    "I am not turning that on here. Holding weak captures out of sight is "
    "switched on for a workspace by the people who build Command Room, not "
    "from inside the workspace — and the list you just read is yours to keep "
    "either way. Nothing about your own numbers is holding it back."
)

# What ENABLING changes, said plainly and in full, so the confirmation is a
# description of the new behaviour rather than a word like "done". Rendered by
# `turn_on_held`; the disable twin is below it.
ENABLED_LINE = (
    "Held is on. From the next end-of-day pass, captures that fall below the "
    "floor stop appearing in the queue, stop being counted, and stop being "
    "asked about. They are still written and still on disk — nothing is "
    "deleted, and you can ask for them at any time. Say \"turn off held\" to "
    "put them back in the queue."
)
DISABLED_LINE = (
    "Held is off. Below-floor captures go back to the queue from the next "
    "end-of-day pass, and they are asked about again. Nothing was lost while "
    "it was on — held rows were written the whole time."
)
ALREADY_OFF_LINE = (
    "Held was already off — below-floor captures are already going to the "
    "queue. Nothing changed."
)

# One objection per row, ever. Saying it twice is the same objection, not a
# second one — the whole point of the verb is that it leaves nothing to clear.
ALREADY_OBJECTED_LINE = (
    "Already noted — you told me this one would be wrong to hide."
)
OBJECTION_NOTE = "you said at review this one would be wrong to hide"


# ---------------------------------------------------------------------------
# The workspace-local operator flag
# ---------------------------------------------------------------------------

def _entities(workspace_root) -> dict:
    """The entities.json `workspace` block, or {}. Never raises — an
    unreadable register means the flag is ABSENT, which is the refusing
    answer."""
    import json

    path = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — see above: unreadable is absent.
        return {}
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("entities") if isinstance(raw.get("entities"), dict) else None
    container = inner if inner is not None else raw
    ws = container.get("workspace")
    return ws if isinstance(ws, dict) else {}


def local_flag_status(workspace_root) -> dict:
    """Is this the workspace the operator turned the held tier on for?

    Returns `{"key": LOCAL_FLAG_KEY, "set": bool, "source": <path>}`.

    `is True` and not truthiness, for the same reason the payload grant uses
    identity: a string `"true"`, a `1`, or a leftover non-empty value are all
    shapes a hurried edit produces and none of them is a decision.
    """
    ws = _entities(workspace_root)
    return {
        "key": LOCAL_FLAG_KEY,
        "set": ws.get(LOCAL_FLAG_KEY) is True,
        "source": str(Path(workspace_root) / "_hq" / "data" / "entities.json"),
    }


def set_local_flag(workspace_root, value: bool, *,
                   source_skill: str = SOURCE_SKILL,
                   triggered_by: str = "operator_explicit") -> dict:
    """Set (or clear) the workspace-local operator flag.

    THE WRITER, and the only one. It goes through
    `workspace_settings.set_workspace_settings`, so the change and its
    `workspace_setting_changed` receipt are one call and cannot separate —
    the same path the timezone and Balance-window settings take.

    Nothing in any shipped skill reaches this function and nothing offers it:
    this is the operator's own action on the operator's own box, taken
    deliberately and outside any phrase. See the module docstring for why it
    is not a knob.
    """
    from workspace_settings import set_workspace_settings

    return set_workspace_settings(
        workspace_root, {LOCAL_FLAG_KEY: bool(value)},
        source_skill=source_skill, triggered_by=triggered_by)


# ---------------------------------------------------------------------------
# Has the review been read in this workspace?
# ---------------------------------------------------------------------------

def _events_dir(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data"


def _iter(workspace_root, etype: str):
    """Every event of one type, oldest first. Never raises."""
    try:
        from events_io import iter_events
        for ev in iter_events(_events_dir(workspace_root)):
            if isinstance(ev, dict) and ev.get("type") == etype:
                yield ev
    except Exception:  # noqa: BLE001 — an unreadable log means NOT SEEN.
        return


def review_receipts(workspace_root) -> list:
    """Every `held_review_rendered` receipt in this workspace, oldest first."""
    return list(_iter(workspace_root, RENDERED_EVENT))


def has_reviewed(workspace_root) -> bool:
    """Has the scoped view rendered here at least once?

    Keyed on the RECEIPT the render writes, not on a config value and not on a
    file on disk: the question is whether a person was shown the rows, and the
    only honest evidence of that is the render having happened."""
    for _ in _iter(workspace_root, RENDERED_EVENT):
        return True
    return False


def objections(workspace_root) -> dict:
    """`{commitment_id: ts}` for every row already objected to."""
    out: dict = {}
    for ev in _iter(workspace_root, OBJECTION_EVENT):
        cid = (ev.get("data") or {}).get("commitment_id")
        if cid:
            out.setdefault(str(cid), ev.get("ts"))
    return out


# ---------------------------------------------------------------------------
# The reading chair
# ---------------------------------------------------------------------------

def build_would_hold_view(workspace_root, *, now_iso: Optional[str] = None,
                          group_by: Optional[str] = None) -> dict:
    """The rows the held tier would hide, grouped by the call they came from.

    A thin call onto `needs_review_queue.build_queue_view` with its
    `would_hold` scope — the rows, the numbering and every field are the
    ordinary queue's, because the whole point is that M is looking at the same
    rows the queue would have shown him. PURE READ."""
    import needs_review_queue as nrq

    return nrq.build_queue_view(
        workspace_root, now_iso=now_iso,
        group_by=group_by or nrq.GROUP_MEETING,
        scope=nrq.SCOPE_WOULD_HOLD)


def build_review_data_view(view: dict, *,
                           already_objected: Optional[dict] = None) -> dict:
    """The scoped view as a `render_and_persist` data view — one section per
    call, rows carrying the SAME context tag the queue draws
    (`needs_review_queue._row_context_tag`, which already prints a floor-gated
    row's reason last), and exactly one verb.

    NO confirm, NO drop, NO already-done, NO not-mine (DD-4). A row already
    objected to carries no verb at all: the objection is recorded, and offering
    the button again would turn one note into a thing to keep answering."""
    from needs_review_queue import _row_context_tag

    already = already_objected or {}
    sections = []
    for group in view.get("groups") or []:
        items = []
        for row in group.get("items") or []:
            # CLUSTER1 — the reading chair folds too (same view
            # annotations), DISPLAY-ONLY by this surface's own DD-4 fence:
            # the cluster line carries "+N folded" and the read-only expand,
            # and NO verb beyond the objection — never `keep as one`, never
            # anything that resolves. A review that can merge is not a
            # review.
            if row.get("folded_into"):
                continue
            cid = row.get("commitment_id")
            item = {
                "n": cid,
                "display_n": row.get("display_n"),
                "name": row.get("title"),
                "context_tag": _row_context_tag(row),
                "data": {"id": cid},
                "actions": ([] if str(cid) in already
                            else [OBJECTION_ACTION]),
            }
            cluster = row.get("cluster")
            if cluster:
                from commitment_cluster import folded_line
                item["context_tag"] += (f" · +{cluster['n_folded']} folded "
                                        f"— the same real-world item")
                item["folded_rows"] = [
                    folded_line(f.get("display_n"),
                                f.get("title") or "(untitled)",
                                f.get("group") or "")
                    for f in cluster["folded"]]
            items.append(item)
        if items:
            # `count` alone — the shared renderer appends "(N)" to any titled
            # section carrying one, so a count baked into the title here
            # rendered twice: "… — AUG 10 (1) (1)". The renderer owns that
            # chrome; this producer only says what the number is.
            sections.append({"title": group.get("name"),
                             "count": len(items), "items": items})
    return {
        "source_skill": SOURCE_SKILL,
        "header": view.get("header") or "",
        "sections": sections,
    }


def render_review_page(workspace_root, *, page: int = 1,
                       persist_dir=None, now_iso: Optional[str] = None,
                       max_rows: Optional[int] = None,
                       write_receipt: bool = True) -> dict:
    """Build the scoped view and render ONE page through the canonical
    transport. The skill's single call.

    Group-aware paging first (whole calls only — a split call asks half a
    question), then `widget_transport.render_and_persist` with an EXPLICIT
    page + page_size so the byte-budget fit runs. The returned `html` is what
    the caller relays, byte-exact; nothing here or downstream restyles it.

    Writes ONE `held_review_rendered` receipt per render. That receipt is what
    `has_reviewed` reads, and it is the only reason this otherwise-pure read
    writes at all: the enable's first condition is that a person was shown the
    rows, and a claim about what someone saw needs evidence, not a flag.
    """
    import needs_review_queue as nrq
    from widget_transport import render_and_persist

    ws = Path(workspace_root)
    view = build_would_hold_view(ws, now_iso=now_iso)
    data_view = build_review_data_view(view,
                                       already_objected=objections(ws))
    page_view = nrq.paginate_groups(data_view, page=page, max_rows=max_rows)
    gp = page_view.pop("group_pagination")
    rows = max(1, gp["rows_on_page"])
    # SPEC_WIDGETRO1 §2-1 (CUT-C item 9, ATTENDED_TEST_v5.28.0 B5.1) — the
    # reading chair renders READ-ONLY: its one row verb stays, the shared
    # batch footer (Apply all / Reset / `Snooze rest (1 day)`) does not
    # render, and the transport's validator reds the page if it ever does.
    # The apply-choices dispatcher fence on `skip` / `skip all` stays as the
    # second belt (a persisted pre-cut page can still carry the footer).
    transport = render_and_persist(
        data_view=page_view, wrapper="fragment",
        persist_dir=str(persist_dir or (ws / "_hq" / ".system" / "widgets")),
        page=1, page_size=rows, read_only=True)
    fitted = (transport.get("pagination") or {}).get("total_pages") or 1
    if fitted > 1:
        gp = dict(gp)
        gp["group_split_by_budget"] = True
    if write_receipt:
        _append(ws, {
            "type": RENDERED_EVENT,
            "source_skill": SOURCE_SKILL,
            "data": {"n_rows": view.get("total", 0),
                     "page": gp.get("page", 1),
                     "total_pages": gp.get("total_pages", 1),
                     "header": view.get("header", "")},
        })
    transport["group_pagination"] = gp
    transport["view"] = view
    return transport


def _append(workspace_root, event: dict) -> None:
    """One gated append. Never raises out of a read surface."""
    try:
        from event_gate import append_event
        append_event(_events_dir(workspace_root) / "events.jsonl", event,
                     holder=SOURCE_SKILL)
    except Exception as exc:  # pragma: no cover — the view must still render
        sys.stderr.write(f"[held_review] receipt skipped: {exc}\n")


# ---------------------------------------------------------------------------
# The one verb: an objection
# ---------------------------------------------------------------------------

def capture_objection(workspace_root, commitment_id, *, note: str = "",
                      review_reason: str = "") -> dict:
    """Record that ONE row would be wrong to hide.

    One capture. Not a queue, not a confirm, not a drop, and nothing that
    comes back to be cleared later — the row stays exactly where it was, in
    the state it was in. Saying it twice about the same row is the same
    objection: the second call writes nothing and says so.
    """
    cid = str(commitment_id or "").strip()
    if not cid:
        return {"status": "ignored", "commitment_id": "", "line": ""}
    if cid in objections(workspace_root):
        return {"status": "already_captured", "commitment_id": cid,
                "line": ALREADY_OBJECTED_LINE}
    _append(workspace_root, {
        "type": OBJECTION_EVENT,
        "source_skill": SOURCE_SKILL,
        "data": {"commitment_id": cid, "note": str(note or ""),
                 "review_reason": str(review_reason or ""),
                 "basis": OBJECTION_NOTE},
    })
    return {"status": "captured", "commitment_id": cid,
            "line": "Noted — I will not hide that one."}


# ---------------------------------------------------------------------------
# The enable and the disable
# ---------------------------------------------------------------------------

def enable_status(workspace_root) -> dict:
    """Everything the enable phrase checks, in one read and in one shape.

    Returns::

        {"reviewed": bool,
         "granted": bool,          # the PAYLOAD grant, via held_tier
         "local": bool,            # the workspace-local operator flag
         "ready": bool,            # all three
         "blockers": [CODE, ...],  # empty iff ready
         "capability": <held_tier.capability_status>,
         "flag": <local_flag_status>}

    `granted` is read through `held_tier.capability_status` and nowhere else:
    HELDOP1's fence is that the grant is payload-anchored and takes no
    workspace argument, and a second reader in this module is a second place
    for that property to be lost.
    """
    import held_tier as ht

    capability = ht.capability_status(workspace_root)
    granted = bool(capability.get("granted"))
    flag = local_flag_status(workspace_root)
    local = bool(flag.get("set"))
    reviewed = has_reviewed(workspace_root)

    blockers = []
    if not reviewed:
        blockers.append(REFUSED_NOT_REVIEWED)
    if not granted:
        blockers.append(REFUSED_NOT_GRANTED)
    if not local:
        blockers.append(REFUSED_NOT_THIS_WORKSPACE)
    return {"reviewed": reviewed, "granted": granted, "local": local,
            "ready": not blockers, "blockers": blockers,
            "capability": capability, "flag": flag}


def turn_on_held(workspace_root, *, origin: str = "m_reviewed") -> dict:
    """Turn the held tier on for this workspace. REFUSES on any of the three.

    Returns `{"status": "enabled"|"refused", "blockers": [...],
    "line": <one sentence>, "status_detail": <enable_status>}`.

    **A refusal writes NOTHING** — not the routing value, not a pending
    marker, not an event, not a receipt. A stored request that is inert is the
    shape that makes a dark feature look enabled to the next reader
    (`held_tier.enable_held_routing`'s own contract, preserved here because
    this is its caller).

    On success it calls `held_tier.enable_held_routing` and NOTHING ELSE
    writes the value. This module does not import `skill_config_writer`, does
    not know the config key, and cannot set the routing by any other path —
    the fence stays HELDOP1's, proven by removal in the suite.
    """
    import held_tier as ht

    st = enable_status(workspace_root)
    if not st["reviewed"]:
        return {"status": "refused", "blockers": list(st["blockers"]),
                "line": LOOK_FIRST_LINE, "status_detail": st}
    if not st["granted"]:
        # HELDOP1 owns this sentence. Repeating it here in different words
        # would be a second story about the same refusal.
        return {"status": "refused", "blockers": list(st["blockers"]),
                "line": ht.REFUSAL_LINE, "status_detail": st}
    if not st["local"]:
        return {"status": "refused", "blockers": list(st["blockers"]),
                "line": NOT_THIS_WORKSPACE_LINE, "status_detail": st}

    res = ht.enable_held_routing(workspace_root, origin=origin)
    if res.get("status") != "enabled":
        # The fence said no on its own read. Carry its answer through
        # unchanged rather than deciding a second time.
        return {"status": "refused",
                "blockers": list(res.get("blockers") or []),
                "line": res.get("line") or ht.REFUSAL_LINE,
                "status_detail": st}
    return {"status": "enabled", "blockers": [], "line": ENABLED_LINE,
            "status_detail": st}


def turn_off_held(workspace_root, *, origin: str = "m_reviewed") -> dict:
    """Turn it back off. The same shape, and DELIBERATELY NOT FENCED.

    Symmetry here is of shape, not of gates. `held_tier.disable_held_routing`
    is ungated by contract — reverting to the shipped default is always
    allowed, and a switch that is hard to turn off is a switch nobody turns
    on. Fencing the way out would also mean a workspace whose grant was later
    withdrawn could be stuck asking to leave. Already-off is a no-op that says
    so rather than writing an idempotent no-change.
    """
    import held_tier as ht

    state = ht.flip_status(workspace_root)
    if state.get("requested") != ht.ROUTING_HELD:
        return {"status": "unchanged", "blockers": [],
                "line": ALREADY_OFF_LINE, "status_detail": state}
    res = ht.disable_held_routing(workspace_root, origin=origin)
    return {"status": res.get("status", "disabled"), "blockers": [],
            "line": DISABLED_LINE, "status_detail": state}


# ---------------------------------------------------------------------------
# CLI — the OPERATOR's side of this module, and deliberately not a phrase.
#
#   python3 shared/scripts/held_review.py status <WORKSPACE>
#   python3 shared/scripts/held_review.py set-local-flag <WORKSPACE> on|off
#
# `set-local-flag` is the operator turning the held tier on for ONE workspace,
# on their own box, having read the rows. It is reachable only by typing it:
# no skill names it, no phrase routes to it, and no widget button dispatches
# it. That is the whole design (see the module docstring) — it is a product
# boundary held by nothing being offered, not a lock.
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    import json

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("command", choices=["status", "set-local-flag"])
    ap.add_argument("workspace")
    ap.add_argument("value", nargs="?", choices=["on", "off"])
    args = ap.parse_args(argv)

    if args.command == "status":
        print(json.dumps(enable_status(args.workspace), ensure_ascii=False,
                         indent=1))
        return 0
    if args.value is None:
        ap.error("set-local-flag needs on|off")
    res = set_local_flag(args.workspace, args.value == "on")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


__all__ = [
    "SOURCE_SKILL", "LOCAL_FLAG_KEY", "RENDERED_EVENT", "OBJECTION_EVENT",
    "OBJECTION_ACTION",
    "REFUSED_NOT_REVIEWED", "REFUSED_NOT_GRANTED", "REFUSED_NOT_THIS_WORKSPACE",
    "LOOK_FIRST_LINE", "NOT_THIS_WORKSPACE_LINE", "ENABLED_LINE",
    "DISABLED_LINE", "ALREADY_OFF_LINE", "ALREADY_OBJECTED_LINE",
    "local_flag_status", "set_local_flag",
    "review_receipts", "has_reviewed", "objections",
    "build_would_hold_view", "build_review_data_view", "render_review_page",
    "capture_objection", "enable_status", "turn_on_held", "turn_off_held",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
