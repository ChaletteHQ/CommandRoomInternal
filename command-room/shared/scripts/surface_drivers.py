#!/usr/bin/env python3
"""One-command surface drivers (T2.2 scope 1e — kill the ~30-command prep).

WHY THIS EXISTS
The RV round measured a manual `commitments` fire spending ~30 shell/python
round-trips assembling the data view before the widget rendered (load →
project → count → escalate → sort → annotate → build → fit → persist), with
the double-render nit (RV-3) riding on the re-runs. Every one of those steps
is deterministic — so this module does ALL of them in ONE CLI invocation per
surface and prints exactly what the runtime needs to relay:

    python3 shared/scripts/surface_drivers.py commitments \
        --workspace <WORKSPACE> [--page N] [--page-size 15]
    python3 shared/scripts/surface_drivers.py staff-meeting \
        --workspace <WORKSPACE> [--page N] [--moves-json <file>] \
        [--fired-via scheduled|manual|catchup]
    python3 shared/scripts/surface_drivers.py waiting-on \
        --workspace <WORKSPACE> [--page N] [--chase-json <file>] \
        [--fired-via scheduled|manual|catchup]
    python3 shared/scripts/surface_drivers.py my-plate \
        --workspace <WORKSPACE> [--page N] [--status-json <file>] \
        [--personal-cap N] [--fired-via scheduled|manual|catchup]
    python3 shared/scripts/surface_drivers.py commitments \
        --workspace <WORKSPACE> --format artifact

ARTIFACT MODE (SPEC_BOARD1): `--format artifact` on the commitments surface
serializes the SAME view as the full-set triage board — one self-contained
page, no pagination, its own CR-BOARD / CR-BOARD-HTML-* markers so nobody
relays a board into show_widget or an interactive widget page into the
Artifact tool. It renders and validates; it never writes a receipt and never
freezes a page-set.

STDOUT SHAPE (fixed contract — the skill texts pin it):

    CR-PAGINATION: {"page": 1, "total_pages": 3, ...}
    CR-WIDGET-HTML-BEGIN
    <the persisted page's validated bytes, verbatim>
    CR-WIDGET-HTML-END
    CR-RECEIPT: {"task_id": ..., "status": "written"}   (only with --fired-via)

FIRE RECEIPTS (FB-7): `--fired-via <run mode>` makes the page-1 invocation
ALSO append the surface's canonical per-fire receipt (receipts.log_receipt)
inside this same call — the 2026-07-16 live staff-meeting scheduled fire
rendered its widget but never reached the prose receipt step that came after
the widget post, so the render and the receipt are now one invocation that
no orchestrator path can split. Pages 2+ (`show more`) never receipt.

The runtime relays the bytes BETWEEN the BEGIN/END markers to
`mcp__visualize__show_widget` as `widget_code`, byte-exact, and reads the
pagination line for the position/`show more` narration. Nothing else needs
running: `render_and_persist` (validators + byte-fit + persist + audit file)
already ran inside this call.

IDEMPOTENT-SINGLE-CALL (RV-3 double-render): one driver invocation per page
per fire. The persisted audit file is written once per invocation; re-running
the driver "to refresh" writes a second audit file and is exactly the
double-render defect. If the transport output for the requested page is
already in hand, relay it — never re-run.

Views are built from the CANONICAL projectors only (cru_match /
commitment_state / confirm_flow / brain_proposals) — the driver adds no
judgment, no filtering, no re-derivation. Read-only against the substrate
except for the transport's own persist into `_hq/.system/widgets/`.

stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The commitment-triage row verb sets (SKILL.md Step 3 — verbatim).
_PROMISE_VERBS = ["resolved", "push to [date]", "drop", "not mine",
                  "make task", "never track this", "skip"]
_TASK_VERBS = ["resolved", "push to [date]", "drop", "promote",
               "never track this", "skip"]
# FB-20 — how many money-class items the brief names in prose before it
# leaves the rest to the pointer's count. A render bound, never a silence:
# every capped item is still inside `queue_pointer["count"]`, and money is
# the only class that gets named at all.
MONEY_PROSE_CAP = 3


def _clock_now(workspace_root=None):
    """CLOCK1 - the corroborated UTC instant this module stamps from.

    Swaps the CLOCK SOURCE only: every window, cutoff, threshold and output
    format around it is unchanged. A machine clock that has not synced used to
    write its own wrong reading straight into the permanent record; this reads
    the same clock, cross-checked against the newest timestamp the workspace
    already holds. Falls back to the raw machine clock if the helper is
    unavailable, so a stamp can never fail for want of corroboration.

    `workspace_root` is threaded in wherever the calling function already
    has one, because a helper that has to GUESS which workspace it is in
    guesses wrong exactly when it matters: a fire's early phases run in
    their own subprocesses, before anything has registered a root.
    """
    try:
        from trusted_now import trusted_now_utc

        return trusted_now_utc(workspace_root)
    except Exception:
        import datetime as _clock_dt

        return _clock_dt.datetime.now(_clock_dt.timezone.utc)


def _pointer_line(count: int) -> str:
    """FB-20's ONE queue-pointer line — the brief's entire adjudication
    affordance now that the card is gone. Drop-empty at zero: a brief with
    nothing queued says nothing about the queue (never "0 things need your
    eyes", never an all-clear pad)."""
    if count <= 0:
        return ""
    noun = "thing needs" if count == 1 else "things need"
    return f"{count} {noun} your eyes — say `staff meeting`."


# Unconfirmed-block confirm cluster (W4b).
_CONFIRM_VERBS = ["mine", "theirs to [name]", "make task", "drop"]
_DUP_VERBS = ["merge", "keep both", "drop"]
# BUG-8330 item 13 — the triage Unowned lane's verbs (all registered; same
# family as the confirm tail): claim it, route it, or let it go.
_UNOWNED_VERBS = ["mine", "theirs to [name]", "drop"]
# pending_review rows outside the 7d escalation pin (explicit confirm-shaped
# actions only — an explicit click IS confirmation).
_PENDING_VERBS = ["resolved", "drop", "not mine"]
# SUB1 D6 — child rows get the standard per-kind dropdown MINUS the one verb
# that doesn't apply to a child: `never track this` (suppression rules key on
# capture shape — children aren't captures) stays parent-level. Everything
# else — Done, Later…, Drop, Not mine, Turn into a task / Promote, Skip —
# works on
# a child with zero special-casing (children are real commitments).
# (`add to my list` was the other parent-level carve-out until MLK1 retired
# the verb entirely — no row emits it now.)
_CHILD_PROMISE_VERBS = ["resolved", "push to [date]", "drop", "not mine",
                        "make task", "skip"]
_CHILD_TASK_VERBS = ["resolved", "push to [date]", "drop", "promote", "skip"]

_REDUCED_REASON = ("Fewer options — the owner is unconfirmed; clicking Done, "
                   "Drop, or Not mine confirms it.")

# FB-15 — the daily Waiting On chat (CTS1 Surface 1; orchestrator-commitments)
# row verb sets, post-FB-17. Delegated tasks (owner != user, effective kind
# `task`) are CRU-INELIGIBLE, so they get NO PRE-STAGED chase draft. A fully
# resolved, pre-staged chase still rides chase_rows like any other email row.
#
# WG1-A D-A4 (M ruling 2026-07-20, big-test row 13b): `nudge` is the delegated
# row's ruled PRIMARY verb, and it is CONNECTOR-FREE at render — compose-on-
# CLICK, not compose-at-render. The driver emits the bare `nudge` action id;
# apply-choices composes the chase draft (email-writer chain, draft posture)
# only when the row is tapped, so this read-only driver still touches no
# connector and scheduled fires stay connector-free. It leads the set so the
# grammar promotes it as the visible primary button. (Train-merge note
# 2026-07-21: the widget-batch interim used the bare `draft` verb for the same
# compose-on-demand behavior; D-A4's named `nudge` verb supersedes it — same
# dispatch chain, grammar-registered id. `add to my plate` is CTS1FIX D5,
# post-dating the WG1-A build; `add to my list` is retired — MLK1.)
#
# The pending_review / unowned confirm tail asks an OWNERSHIP question, so it
# carries the ownership cluster (orchestrator-commitments §452 + § "Confirm
# section actions" §1027-1033), not the opaque person-record `confirm` verb:
#   - `mine`             — CLAIMS (commitment_state.confirm_commitment_owner:
#                          sets the owner AND clears any pending_review flag)
#   - `theirs to [name]` — ROUTES (reassign_commitment, confirmed=True)
#   - `drop`             — closes an item that is nobody's (§452 dismissal)
#   - `snooze 3d`        — the deferral tail (UXR1 D1)
# `make task` is deliberately omitted: reclassifying a commitment to a task in
# place while its owner is still unknown is incoherent — `add to my plate`
# (make it MY task) is the coherent task path here. Dispatch is keyed per
# row-id, so the ONE shared cluster preserves the genuinely-different behavior
# between the two classes (mine clears the pending_review flag only where one
# is present; on a bare-unowned row it simply stamps the owner).
#
# UXR1 D1 (M ruling 2026-07-21): the confirm tail SLIMMED from five verbs to
# four. Removed from EMISSION only — `not relevant` (the dishonest twin of
# `drop` on this row: hides it 60d while the item stays open + unconfirmed)
# and `add to my plate` (the redundant twin of `mine`: both land it on My
# Plate; `mine` keeps the counterparty and the chase, the correct default for
# a captured promise). Both wire ids stay registered in verb_taxonomy and
# dispatch unchanged — old persisted widgets carrying the 5-verb rows must
# still apply. Under WG1-A's ≤4 rule the slimmed row renders as 4 buttons,
# no dropdown.
_DELEGATED_VERBS = ["nudge", "mark received", "snooze 3d", "add to my plate"]
_REVIEW_VERBS = ["mine", "theirs to [name]", "drop", "snooze 3d"]

# FB-plumbing item 6 — My Plate (CTS1 Surface 2; orchestrator-my-plate) row
# verb sets. The driver renders the CONNECTOR-FREE row classes deterministically
# (the counterparty-unresolved Promised fixup rows + the whole Personal group);
# the email-shaped status drafts for counterparty-RESOLVED Promised rows are
# connector-dependent (email-writer) so the orchestrator composes them and
# passes them verbatim as `status_rows`, exactly as waiting-on's `chase_rows`.
# Counterparty-unresolved Promised rows (Bug #103, the 49 orphaned promises):
# the reassign/make-task fixup rides two existing verbs; NEVER auto-demoted.
_MP_UNRESOLVED_VERBS = ["reassign to [name]", "make task", "push to [date]",
                        "resolved", "drop", "snooze 3d"]
# Personal (owner-me own work) — no drafts; the standard owner-me act verbs.
# (`add to my list` was in the FB-plumbing build of this set; retired by MLK1
# before this driver merged — removed at the train merge, never emitted.)
_MP_PERSONAL_VERBS = ["resolved", "push to [date]", "prep deep work",
                      "promote", "snooze 3d"]
# BUG-8330 item 5 — the RESIDUAL Promised class: a counterparty-RESOLVED
# promise the orchestrator drafted no status email for. These rows were
# ABSENT from My Plate entirely (not capped — absent); they render
# deterministically now with the owner-me act verbs (no reassign — the
# counterparty is known; no draft — that stays connector-side).
_MP_RESIDUAL_VERBS = ["resolved", "push to [date]", "drop", "snooze 3d"]
# Default Personal-group cap (CTS1 §4.2 — `my-plate` skill config
# `personal_cap`, default 7); the tail line points at `show my plate`.
_MP_PERSONAL_CAP = 7
# Default Promised-group cap (BUG-8330 item 5): explicit, with the same
# footer tail — the old behavior was an implicit cap of "however many rows
# the orchestrator happened to compose", with the overflow invisible.
_MP_PROMISED_CAP = 7


class MountStaleError(RuntimeError):
    """A surface driver refused to render because the substrate view is stale.

    SPEC SYNC1 A4, extended to the RENDER path. `preflight_freshness` was wired
    into the maintenance orchestrator only, so a driver rendering from a stale
    sandbox mount had no gate at all. Two observed outcomes, and the silent one
    is worse:

      * loud — a stale projection resurfaces a token that was already fixed on
        disk, and the render dies inside a downstream validator with an error
        naming the wrong thing (a leak, when the substrate is clean);
      * silent — the surface PUBLISHES a stale page: stale rows, stale counts,
        no indication anywhere that the view is behind.

    So the drivers refuse up front and produce NO SURFACE and NO RECORD: no
    page, no receipt, no page-set, no events. That enumeration is the claim,
    and it is what the pins assert — not "writes nothing", which would be
    false. The refusal path DOES touch disk, deliberately and only through the
    sanctioned alarm machinery:

      * `preflight_freshness` writes the `.mount_stale.json` sidecar beside
        events.jsonl (a sidecar, never an events append — an append through a
        stale view is the clobber vector itself) and renders the alert through
        `alarm_artifacts.write_alert`;
      * `substrate_alarm_lines` calls `alarm_artifacts.sweep_alerts`, which
        archives any alert whose condition has already resolved.

    All three are alarm artifacts about the refusal, never workspace state, and
    routing through them is why this class hand-authors no prose of its own.
    `lines` is `substrate_health.substrate_alarm_lines`, the same plain-English
    syncing vocabulary the health check and the morning brief already print.

    One honest edge: `preflight_freshness` retries ×3 with backoff and
    `substrate_alarm_lines` re-probes without retrying, so a staleness that
    clears between the two probes yields an empty `lines`. The fallback below
    handles it by reporting the machine detail; it is not the impossible case
    an earlier draft of this docstring implied.
    """

    def __init__(self, lines, detail=None):
        self.lines = [str(ln) for ln in (lines or [])]
        self.detail = dict(detail or {})
        # Never invent prose. With no alarm line to speak (a degenerate case —
        # a not-ok preflight always leaves at least one), the machine detail is
        # reported verbatim rather than replaced by a sentence nobody wrote.
        super().__init__("\n".join(self.lines) or json.dumps(self.detail))


def refuse_if_mount_stale(workspace_root) -> None:
    """The render-path preflight. Returns on a healthy view; raises
    `MountStaleError` on a stale one, BEFORE the caller reads or renders
    anything.

    Deliberately the FIRST statement of every driver entry point that renders
    substrate — a preflight that runs after the view is built has already paid
    for the stale read it exists to prevent.
    """
    from substrate_health import preflight_freshness, substrate_alarm_lines
    result = preflight_freshness(workspace_root)
    if result.get("ok"):
        return
    raise MountStaleError(substrate_alarm_lines(workspace_root),
                          result.get("detail"))


def _now_iso() -> str:
    return _clock_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _age_days(ts: str, now_iso: str) -> int | None:
    from event_time import parse_ts

    a, b = parse_ts(ts), parse_ts(now_iso)
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds() // 86400))


def _due_phrase(due, now_iso: str) -> str:
    if not due:
        return "undated"
    try:
        d = _dt.date.fromisoformat(str(due)[:10])
        today = _dt.date.fromisoformat(now_iso[:10])
        if d == today:
            return "due today"
        if d < today:
            return f"overdue since {d.strftime('%b')} {d.day}"
        return f"due {d.strftime('%b')} {d.day}"
    except ValueError:
        return f"due {due}"


def _people_by_id(ws: Path) -> dict:
    """id -> person record from entities.json.

    Defensive: a missing / corrupt entities.json yields an empty map (the
    caller then falls back to owner_external / a bare 'delegated' tag and the
    `add email then send` recovery verb)."""
    try:
        raw = (ws / "_hq" / "data" / "entities.json").read_text("utf-8")
        people = (json.loads(raw) or {}).get("people") or []
    except Exception:
        return {}
    out: dict = {}
    for p in people:
        pid = p.get("id")
        if pid:
            out[pid] = p
    return out


# RRF1 — the checkable clause class + the resolve verdict live in the shared
# review_reasons module now (BUG-8330 item 4: the same verdict also drives
# the loader's read-side gating and the queue's reason-scoped batch verb —
# render and gating can no longer disagree). This alias keeps the local name.
from review_reasons import NO_PERSON_RE as _RR_NO_PERSON_RE  # noqa: E402


def _display_review_reason(ws: Path, raw, cache: dict) -> str:
    """Render-time overlay for the frozen "counterparty 'X' has no person
    record" review_reason clause (RRF1 + UXC1 plain-language ruling
    2026-07-21). The STORED clause is never shown raw — "counterparty" is
    banned vocabulary (VOICE_CALIBRATION glossary): an unresolved name
    renders as "'X' isn't in your contacts yet"; if X NOW resolves to a
    person record (entity_resolve ladder — the same one brain_proposals
    uses; A6: one home for the matcher) it renders as "'X' — contact added
    ✓" so the row stops telling the CEO to do something they already did.
    Every other clause class passes through verbatim.

    DISPLAY-ONLY: the STORED review_reason is a gating input (cru_match /
    commitment_dedup / confirm_flow / identity_reconcile read it) and is
    never written back — this rewrites the rendered string only.

    `cache` is a per-driver-call memo (name -> bool): each distinct name
    costs at most one resolve_all call per render pass (resolve_all re-reads
    entities.json internally, so the memo IS the read fence), and reasons
    with no eligible clause never load the resolver at all.
    """
    raw = str(raw)
    if "has no person record" not in raw:
        return raw
    out = []
    for clause in raw.split("; "):
        m = _RR_NO_PERSON_RE.match(clause)
        if not m:
            out.append(clause)
            continue
        name = m.group(1)
        # Shared verdict (review_reasons._resolves_to_person semantics via
        # clause_still_holds): the same memo dict, the same resolver, the
        # same failure posture — a resolver failure reads as unresolved and
        # never breaks a surface render.
        from review_reasons import _resolves_to_person
        out.append(f"'{name}' — contact added ✓"
                   if _resolves_to_person(ws, name, cache)
                   else f"'{name}' isn't in your contacts yet")
    return "; ".join(out)


def _owner_display_name(ev: dict, people_by_id: dict) -> str | None:
    """Resolve the delegated-task owner's display name for the row tag.

    A delegated row is owner != M by definition, so it should name who: read a
    display name carried on the event first (legacy `owner_display` /
    `owner_name`), else resolve `owner_id` (multi-shape via `_commitment_field`)
    through entities.json, else fall back to the raw `owner_external` string.
    Returns None when nothing resolves — the caller then renders bare
    'delegated'."""
    from cru_match import _commitment_field

    d = ev.get("data") or {}
    for k in ("owner_display", "owner_name"):
        v = (d.get(k) or ev.get(k) or "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    owner_id = _commitment_field(ev, "owner_id")
    rec = people_by_id.get(owner_id) if owner_id else None
    if rec:
        name = (rec.get("name") or rec.get("canonical_name") or "").strip()
        if name:
            return name
    ext = d.get("owner_external") or ev.get("owner_external") or ""
    if isinstance(ext, str) and ext.strip():
        return ext.strip()
    return None


def _owner_email(ev: dict, people_by_id: dict) -> str | None:
    """The delegated owner's first actionable email, or None.

    `draft` is a send-class verb (chat_output_renderer Gate 6 / Bug #44): a row
    that exposes it MUST carry a valid To: address or the renderer refuses to
    ship it. Resolve `owner_id` -> the person record's canonical `emails` array
    (via `people_writer.get_person_emails`); if `owner_external` is itself an
    email, use that. None -> the caller degrades `draft` to the canonical
    `add email then send` recovery verb (no To: required)."""
    from cru_match import _commitment_field
    from people_writer import get_person_emails

    owner_id = _commitment_field(ev, "owner_id")
    rec = people_by_id.get(owner_id) if owner_id else None
    if rec:
        emails = get_person_emails(rec)
        if emails:
            return emails[0]
    ext = ((ev.get("data") or {}).get("owner_external")
           or ev.get("owner_external") or "")
    if isinstance(ext, str) and "@" in ext and "." in ext.split("@")[-1]:
        return ext.strip()
    return None


def build_commitment_triage_view(workspace_root, *, now_iso: str | None = None) -> dict:
    """The full commitment-triage data view (SKILL.md Steps 1-2, mechanized):
    canonical loader + bucket export + escalation split + age sections +
    per-row context tags + per-kind verb sets. Pure read."""
    from commitment_activity import derive_commitment_movement
    from chat_output_renderer import COMPOSED_FIELDS_KEY
    from commitment_state import (BUCKET_UNCONFIRMED, UNCONFIRMED_LANE,
                                  UNCONFIRMED_SECTION_LABEL,
                                  UNTITLED_PLACEHOLDER, bucket_of,
                                  commitment_kind, count_commitments,
                                  stale_tasks, unconfirmed_slices)
    from confirm_flow import select_unconfirmed_escalation, unconfirmed_classes
    # WALKFIX1 Item E: `_is_pending_review` is deliberately NOT imported here
    # any more. This view partitions by `commitment_state.bucket_of` — THE
    # predicate `count_commitments` counts with — and importing a second way to
    # ask the same question is how the headline and the rendered rows ended up
    # partitioned by two functions that only happened to agree.
    from cru_match import load_open_commitments
    from event_time import event_time
    from primary_user import resolve_primary_user

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=now_iso, movement=movement)
    stale_ids = {row.get("commitment_id") or row.get("id")
                 for row in stale_tasks(opens, now_iso, movement=movement)}
    esc = select_unconfirmed_escalation(opens, now_iso)
    esc_ids = {row["commitment_id"] for row in esc["pin"]}
    propose_drop_ids = {row["commitment_id"] for row in esc["propose_drop"]}

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    # SUB1 D6 — the family nests: sub-items render INSIDE their parent's row
    # (the existing sub_items shape), never as their own top-level rows.
    # Pagination is family-atomic structurally: paginate_data_view slices
    # top-level items only, so a family that doesn't fit moves whole to the
    # next page (a 12-child family alone on an oversized page degrades via
    # the transport's over_budget flag rather than splitting). Orphan
    # children (parent closed — the cascade crash window) partition
    # top-level and render as ordinary rows with a "was part of" note.
    from cru_match import partition_subitems
    top_level, sub_level = partition_subitems(opens)
    subs_by_parent: dict = {}
    for ev in sub_level:
        subs_by_parent.setdefault(
            (ev.get("data") or {}).get("parent_id"), []).append(ev)

    display_n = 0
    sections: list[dict] = []
    rr_cache: dict = {}  # RRF1 per-render-pass memo (name -> resolves now)
    # BOARD1 — the projected event behind each rendered row, so a row's
    # bucket and kind are read from THE event through the canonical
    # predicates rather than re-derived from the row's rendered text.
    by_cid: dict = {_cid(ev): ev for ev in opens}

    def _stamp(row: dict, ev) -> dict:
        """Stamp the row with its headline bucket + effective kind (BOARD1).

        The one derivation: `commitment_state.bucket_of` is the SAME predicate
        `count_commitments` counts with, and `commitment_kind` is the same
        effective-kind projection every other surface reads. The board's tabs
        and pinned strips partition on these two keys, so a tab's membership
        cannot drift from the tile above it. Unknown row keys are ignored by
        the widget renderer, so the widget path is byte-identical.
        """
        row["bucket"] = bucket_of(ev, user_id) if ev is not None else BUCKET_UNCONFIRMED
        row["kind"] = commitment_kind(ev) if ev is not None else "promise"
        return row

    # Unconfirmed block FIRST (v4.6.1 W4b escalation — never age-buried).
    # BUG-8330 item 13: rows whose ONLY amber class is `unowned` do NOT pin
    # here — this block was the "pinned block on a bounded page" 145 unowned
    # rows sat in without draining. They drain through the dedicated Unowned
    # lane below (mine / theirs to [name] / drop). pending_review and
    # suspected-duplicate escalations keep their pin.
    pin_shown = pin_escalated = 0
    unconfirmed_section_index = None
    if esc["pin"]:
        rows = []
        for r in esc["pin"]:
            _pin_ev = by_cid.get(r["commitment_id"])
            if (_pin_ev is not None
                    and unconfirmed_classes(_pin_ev) == ["unowned"]):
                continue
            display_n += 1
            dup = bool(r.get("suspected_duplicate_of"))
            lead = ""
            if r["commitment_id"] in propose_drop_ids:
                lead = (f"sat unconfirmed for {r['days_unconfirmed']} days — "
                        f"drop it? · ")
            tag = (f"{lead}captured {r['days_unconfirmed']} days ago — still "
                   f"unconfirmed"
                   + (f" · {_display_review_reason(ws, r['review_reason'], rr_cache)}"
                      if r.get("review_reason") else ""))
            row = _stamp({
                "n": r["commitment_id"], "display_n": display_n,
                # WALKFIX1 Item E — a title-less row renders the shared
                # repair placeholder, not an empty card. The live case was an
                # unowned, undated scheduling row whose card came out blank.
                "name": r.get("title") or UNTITLED_PLACEHOLDER,
                "context_tag": tag,
                "actions": _DUP_VERBS if dup else _CONFIRM_VERBS,
            }, by_cid.get(r["commitment_id"]))
            if not (r.get("title") or "").strip():
                # The placeholder is the RENDERER talking about the record, so
                # it declares its own prose and keeps facing the whole scan.
                row[COMPOSED_FIELDS_KEY] = ["name"]
            rows.append(row)
        # The rows this strip renders, and how many of them the BUCKET
        # predicate calls unconfirmed. The difference is the crossing set (the
        # ownerless escalation — a verified non-bug), and it is counted here
        # so no surface has to guess at it later.
        pin_shown = len(rows)
        pin_escalated = sum(
            1 for row in rows if row.get("bucket") == BUCKET_UNCONFIRMED)
        # BUG-8330 item 13 × WALKFIX1 merge: with unowned-only rows skipped
        # above, the strip can be EMPTY while esc["pin"] was not — guard the
        # append and take the section index inside it, or the index points at
        # whatever section lands in this slot next.
        if rows:
            unconfirmed_section_index = len(sections)
            # `lane` is the STABLE identifier. The title carries a reconciliation
            # sentence and is therefore a label that may change; three shipped
            # suites used to find this block by matching its title string and all
            # three broke the moment it did. Unknown section keys are ignored by
            # both renderers, so this is invisible on the surface.
            sections.append({"title": UNCONFIRMED_SECTION_LABEL,
                             "lane": UNCONFIRMED_LANE,
                             "count": len(rows), "items": rows})

    # Age sections, oldest first; escalation-pinned rows excluded (no
    # double-surfacing). SUB1: top-level items only — children render nested.
    # INTAKE: EVERY unconfirmed-bucket row is excluded here, not just the
    # pinned ones — an unconfirmed extraction is not an open commitment, so it
    # does not belong in an age section. The labelled "Unconfirmed" pin block
    # above is the deliberate exception; the rest are pointed at the
    # needs-your-call queue by the pointer line below.
    #
    # WALKFIX1 Item E — the membership test here is `bucket_of`, THE bucketing
    # predicate `count_commitments` counts with, not a second read of the
    # pending_review flag. The two agree today, which is exactly why the
    # substitution is safe and exactly why it was never noticed that the
    # headline and the rendered rows were partitioned by two different
    # functions. One of them changing was the off-by-one class: a row counted
    # as confirmed by one predicate and skipped as unconfirmed by the other
    # renders NOWHERE while still being in the header's total.
    aged: list[tuple[int, dict]] = []
    unowned_aged: list[tuple[int, dict]] = []
    n_pending_unpinned = 0
    for ev in top_level:
        cid = _cid(ev)
        if bucket_of(ev, user_id) == BUCKET_UNCONFIRMED:
            if cid not in esc_ids:
                n_pending_unpinned += 1
            continue
        age = _age_days(ev.get("ts") or "", now_iso)
        # BUG-8330 item 13 — unowned rows get their OWN lane instead of
        # age-burying or pin-block accumulation. The daily confirm selector
        # only admits rows ≤7 days old and the escalation pin block sat on a
        # bounded page: 145 unowned rows mathematically never drained. Same
        # membership predicate as the Unowned tile (bucket_of), so the lane
        # can never disagree with the counter above it. Suspected duplicates
        # keep their pin (the merge adjudication needs its own verbs);
        # everything else unowned drains here, esc-pinned or not.
        if (bucket_of(ev, user_id) == "unowned"
                and not (ev.get("data") or {}).get("suspected_duplicate_of")):
            unowned_aged.append((age if age is not None else -1, ev))
            continue
        if cid in esc_ids:
            continue
        aged.append((age if age is not None else -1, ev))
    aged.sort(key=lambda t: -t[0])
    unowned_aged.sort(key=lambda t: -t[0])

    def _row(ev, age):
        nonlocal display_n
        display_n += 1
        cid = _cid(ev)
        d = ev.get("data") or {}
        kind = commitment_kind(ev)
        classes = unconfirmed_classes(ev)
        pending = "pending_review" in classes
        parts = []
        if age >= 0:
            parts.append(f"{age} days old" if age != 1 else "1 day old")
        parts.append(_due_phrase(d.get("due"), now_iso))
        parts.append("task (yours)" if kind == "task" else kind)
        if cid in stale_ids:
            parts.append("still on your plate?")
        if pending and d.get("review_reason"):
            parts.append(_display_review_reason(ws, d.get("review_reason"),
                                                rr_cache))
        # SUB1 D5/D6 — parent progress chip + orphan note (loader stamps).
        kids = subs_by_parent.get(cid) or []
        n_open_k = d.get("n_subitems_open")
        n_done_k = d.get("n_subitems_done")
        if kids or isinstance(n_open_k, int):
            total_k = (n_open_k or 0) + (n_done_k or 0)
            chip = f"sub-items {n_done_k or 0}/{total_k}"
            nxt = _next_open_child(kids)
            if nxt is not None:
                chip += f" · next: {nxt}"
            parts.append(chip)
        if d.get("parent_closed") and d.get("parent_title"):
            parts.append(f"was part of: {d['parent_title']}")
        row = _stamp({
            "n": cid, "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(parts),
            "actions": (_PENDING_VERBS if pending
                        else (_TASK_VERBS if kind == "task" else _PROMISE_VERBS)),
        }, ev)
        if pending:
            row["reduced_verbs_reason"] = _REDUCED_REASON
        # SUB1 D3 — the PROPOSE-closure line (never auto-close): renders on
        # the parent row when the last open child closed.
        if d.get("all_subitems_resolved"):
            row["annotations"] = ["all sub-items done — close it?"]
        # SUB1 D6 — nested child rows: id = the child's data.id VERBATIM
        # (identity contract, Stage B); per-kind dropdown minus the
        # non-child verbs. Only OPEN children render (done ones are the
        # chip's numerator).
        if kids:
            row["sub_items"] = []
            for k in kids:
                kd = k.get("data") or {}
                k_kind = commitment_kind(k)
                summary = kd.get("title") or "(untitled)"
                if kd.get("due"):
                    summary += f" — {_due_phrase(kd.get('due'), now_iso)}"
                row["sub_items"].append({
                    "id": _cid(k),
                    "summary": summary,
                    "actions": (_CHILD_TASK_VERBS if k_kind == "task"
                                else _CHILD_PROMISE_VERBS),
                    # BOARD1 — the CHILD's own effective kind. A child of a
                    # promise can itself be a task, so a reader that needs
                    # the kind must not inherit the parent's.
                    "kind": k_kind,
                })
        return row

    def _next_open_child(kids: list) -> str | None:
        """The step to name in the progress chip: the open child with the
        earliest parseable effective due, else the first in append order."""
        if not kids:
            return None
        best_ev, best_date = None, None
        for k in kids:
            kd = k.get("data") or {}
            try:
                kd_date = _dt.date.fromisoformat(str(kd.get("due"))[:10])
            except (ValueError, TypeError):
                kd_date = None
            if kd_date is not None and (best_date is None or kd_date < best_date):
                best_ev, best_date = k, kd_date
        pick = best_ev or kids[0]
        return (pick.get("data") or {}).get("title") or None

    # BUG-8330 item 13 — the UNOWNED lane, oldest first, ahead of the age
    # sections (these are the rows nothing else ever surfaces). Stored
    # `attribution_candidates` finally render: the capture paths write them
    # and no surface ever read one — proposing them here is the wire half of
    # that census entry (item 16).
    def _unowned_row(ev, age):
        nonlocal display_n
        display_n += 1
        cid = _cid(ev)
        d = ev.get("data") or {}
        parts = []
        if age >= 0:
            parts.append(f"{age} days old" if age != 1 else "1 day old")
        parts.append(_due_phrase(d.get("due"), now_iso))
        parts.append("no owner on record — whose is this?")
        cand_names = []
        for cand in (d.get("attribution_candidates") or [])[:3]:
            if isinstance(cand, str) and cand.strip():
                cand_names.append(cand.strip())
            elif isinstance(cand, dict):
                nm = (cand.get("name") or cand.get("display_name")
                      or cand.get("person_name") or "").strip()
                if nm:
                    cand_names.append(nm)
        if cand_names:
            parts.append("maybe: " + " / ".join(cand_names))
        return _stamp({
            "n": cid, "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(parts),
            "actions": list(_UNOWNED_VERBS),
        }, ev)

    unowned_rows = [_unowned_row(ev, age) for age, ev in unowned_aged]
    if unowned_rows:
        sections.append({"title": "Unowned — oldest first",
                         "count": len(unowned_rows), "items": unowned_rows})

    old_rows = [_row(ev, age) for age, ev in aged if age >= 30]
    new_rows = [_row(ev, age) for age, ev in aged if age < 30]
    if old_rows:
        sections.append({"title": "30+ days old", "count": len(old_rows),
                         "items": old_rows})
    if new_rows:
        sections.append({"title": "The rest", "count": len(new_rows),
                         "items": new_rows})

    h = counts["headline"]
    counters = [
        {"label": "Open", "value": h["total"]},
        {"label": "You owe", "value": h["you_owe"]},
        {"label": "Owed to you", "value": h["owed_to_you"]},
        {"label": "Unowned", "value": h["unowned"]},
        {"label": "Unconfirmed", "value": h["unconfirmed"]},
    ]
    # SUB1 D6 — tiles unchanged in shape (values are top-level per D2); when
    # sub-items exist the HEADER appends the additive key — never a new tile
    # that implies a fifth bucket.
    header = f"Commitment triage — {h['total']} open, oldest first"
    if h.get("subitems_open"):
        header += f" (+{h['subitems_open']} sub-items)"
    view = {
        "source_skill": "commitment-triage",
        "header": header,
        "counters": counters,
        "sections": sections,
    }

    # WALKFIX1 Item E — THE unconfirmed derivation, computed ONCE, feeding
    # every surface that says an unconfirmed number. The tile keeps the queue
    # total unchanged; the section header and the quick-read read off this.
    slices = unconfirmed_slices(queue_total=h.get("unconfirmed"),
                                shown=pin_shown, escalated=pin_escalated)
    view["unconfirmed_slices"] = slices
    if unconfirmed_section_index is not None:
        sections[unconfirmed_section_index]["title"] = slices["section_title"]

    # INTAKE — the unconfirmed extractions this surface deliberately does NOT
    # render as rows still get one honest line saying where they went. Only
    # the ones outside the labelled pin block: those already have rows.
    # Drop-empty at zero (never "0 unconfirmed" padding). The sentence now
    # names what its number is a slice OF, from the same derivation.
    if n_pending_unpinned:
        pointer = slices.get("quick_read")
        if not pointer:
            noun = ("extraction waiting" if n_pending_unpinned == 1
                    else "extractions waiting")
            pointer = (f"{n_pending_unpinned} unconfirmed {noun} — say "
                       f"`needs your call` to clear them.")
        view["pointer"] = pointer
        # `quick_read` is the key BOTH renderers (markdown + widget) actually
        # print; `pointer` is the machine-readable twin the tests pin.
        view["quick_read"] = pointer

    # WALKFIX1 Item E — the arithmetic, on the record. Everything below comes
    # off the SAME bucketing pass, so the identity is a fact about this view
    # rather than a hope about two independent counts:
    #
    #     rows rendered = headline.total + pinned rows in the unconfirmed
    #                     bucket
    #
    # (an unconfirmed-bucket row renders ONLY when the escalation strip pinned
    # it; a pinned row from any other bucket was already inside headline.total
    # and is merely relocated into the strip). The 225-vs-226 class was a row
    # counted by one predicate and skipped by another, rendering nowhere; a
    # residual here is that class, and it is visible instead of silent.
    rendered_rows = sum(len(s.get("items") or []) for s in sections)
    view["count_reconciliation"] = {
        "headline_total": h["total"],
        "queue_total": h.get("unconfirmed"),
        "rows_rendered": rendered_rows,
        "pin_shown": pin_shown,
        "pin_escalated": pin_escalated,
        "pin_crossing": slices["crossing"],
        "queue_not_rendered": slices["remainder"],
        "residual": rendered_rows - (h["total"] + pin_escalated),
    }
    return view


# WATCHGATE §2.3 — the expiry question's verbs. Canonical ids only: "mark
# done" is the Done answer, "hold" is Still open (it quiets the row while the
# item stays open), "drop" lets it go. One tap either way, per §2.3.
_WATCH_ASK_ACTIONS = ["mark done", "hold", "drop"]
_WATCH_ASK_SECTION = "STILL OPEN?"


def build_watch_ask_rows(ask_rows: list, *, now_iso: str) -> list[dict]:
    """The expiry questions as card rows — rendered STRENGTH-AWARE per §2.1,
    because they land on the same surface §2.2 hardens and must not read like
    the confident rows beside them."""
    from watch_gate import strength_line

    out: list[dict] = []
    for i, row in enumerate(ask_rows or [], start=1):
        watch = row.get("watch") or {}
        bits = [strength_line(watch.get("reason") or "",
                              evidence=row.get("evidence") or "")]
        reasons = (row.get("stakes") or {}).get("reasons") or []
        if "overdue" in reasons:
            bits.append("the date has passed")
        out.append({
            "n": row["id"], "display_n": i,
            "name": row.get("title") or "(untitled)",
            "context_tag": " · ".join(b for b in bits if b),
            "actions": list(_WATCH_ASK_ACTIONS),
        })
    return out


def build_staff_meeting_view(workspace_root, *, now_iso: str | None = None,
                             moves_rows: list | None = None,
                             watch_rows: list | None = None) -> dict:
    """The Staff Meeting queue view (orchestrator Phase 3+5, mechanized):
    THE projector + D3 ranking + build_card_view. `moves_rows` (Phase 4's
    email-shaped rows, connector-dependent so built by the orchestrator) are
    appended as the THIS WEEK'S MOVES section when supplied.

    `watch_rows` (WATCHGATE §2.3) are the expiry questions this fire owes —
    already routed, capped and ordered by `watch_gate.run_watch_expiry`, which
    `run_surface` runs before this builds. Supplied rather than derived here
    for the reason every other write stays out of a view builder: this
    function reads, `run_surface` decides when writing is allowed.

    STAFFCUT — two RENDER-side passes run between the projector and the builder,
    in this order and never inside the projector:

      1. `proposal_digests.group_into_digests` folds each evidence class into
         ONE row carrying its members' own dispatch payloads. The audit day's 54
         sent-match rows rested on 16 distinct evidence lines (34 of them on a
         single line) — that is 16 decisions asked 54 times.
      2. `proposal_digests.bound_page` bounds what THIS FIRE renders to about
         two screens, appended sections included — the meeting fold's shipped
         volume-guard pattern applied to the queue lane. It bounds the PAGE-SET,
         never the projector: the queue keeps everything, the ranked front is
         what shows, and answering the front is what advances the rotation.

    The honest arithmetic (rows rendered vs items represented, and the bound's
    remainder) rides the section titles via `section_notes` and the D2 receipt
    via `view["receipt_extra"]`, which `run_surface` pops before rendering."""
    from brain_proposals import (build_card_view, load_open_proposals,
                                 rank_proposals)
    from proposal_digests import (bound_page, group_into_digests,
                                  section_notes)

    open_items = load_open_proposals(workspace_root, "staff-meeting",
                                     now_iso=now_iso)
    queue = rank_proposals(open_items)
    extra: list[dict] = []
    if watch_rows:
        extra.append({"title": _WATCH_ASK_SECTION,
                      "count": len(watch_rows),
                      "items": build_watch_ask_rows(
                          watch_rows, now_iso=now_iso or _now_iso())})
    # CAPTUREFLOW §C — the meeting fold. ONE section of the SAME per-meeting
    # groups the on-demand needs-your-call queue renders, from the SAME
    # builder (`needs_review_queue.staff_meeting_group_section`), answered
    # through the SAME confirm/drop fence. Not a new surface, not a new
    # scheduled task: the staff meeting is already scheduled, so this is a
    # section, not an appointment. The section carries its own volume guard
    # (whole calls, oldest first, capped, with the honest totals and a pointer
    # to the on-demand queue in its title) so it can never dominate the page.
    # Drop-empty like every other section; any failure degrades to no section
    # rather than a dead fire.
    try:
        from needs_review_queue import staff_meeting_group_section
        fold = staff_meeting_group_section(workspace_root, now_iso=now_iso)
        if fold:
            extra.append(fold)
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write(f"[surface_drivers] meeting fold skipped: {exc}\n")
    if moves_rows:
        extra.append({"title": "THIS WEEK'S MOVES", "items": list(moves_rows)})

    # STAFFCUT §3.2/3.3/3.5 — group, then RE-RANK (a digest inherits its oldest
    # member's age, so it must be re-placed in the ranked order), then bound.
    digested, digest_stats = group_into_digests(queue)
    digested = rank_proposals(digested)
    n_extra_rows = sum(len(sec.get("items") or []) for sec in extra)
    shown, bound_stats = bound_page(digested, n_extra_rows=n_extra_rows)
    # LIFECYCLE1 §7b — the tiles show the HONEST per-shape total, the same
    # convention the section titles already use, computed from the FULL open
    # queue (pre-digest, pre-bound) rather than from the page. Grouping and
    # the page bound go on governing what renders and nothing else.
    shape_totals: dict = {}
    for it in open_items:
        s = (it or {}).get("shape") or "hygiene"
        shape_totals[s] = shape_totals.get(s, 0) + 1
    view = build_card_view(shown, surface="staff-meeting",
                           extra_sections=extra or None,
                           section_notes=section_notes(shown, bound_stats),
                           shape_totals=shape_totals)
    # D2 — the per-kind + digest arithmetic the fire receipt records. Popped by
    # `run_surface` before the view reaches the renderer, so nothing new travels
    # into the widget contract.
    view["receipt_extra"] = _staff_receipt_extra(open_items, shown,
                                                 digest_stats, bound_stats,
                                                 n_extra_rows)
    return view


def _kind_counts(items) -> dict:
    """kind -> count, in descending count order (stable for reading a receipt
    by eye). An item with no kind is counted as "unknown" rather than dropped —
    a receipt that quietly omits rows is the thing D2 exists to fix."""
    counts: dict[str, int] = {}
    for it in items or []:
        key = str((it or {}).get("kind") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _staff_receipt_extra(open_items, shown, digest_stats, bound_stats,
                         n_extra_rows: int) -> dict:
    """STAFFCUT D2 — the staff-meeting receipt's per-kind counts.

    `surface_drivers._log_fire_receipt` wrote only the scalar `surfaced`, so
    load history could not be measured: the 2026-08-02 audit had to reconstruct
    23 fires from an upper-bound model because no receipt had ever recorded WHAT
    was surfaced. This is additive only — the scalar keeps its meaning (rows the
    widget showed), every existing reader is untouched, and the new keys ride
    `log_receipt(extra_data=...)`, which already exists for exactly this.

    Both halves of the digest arithmetic are recorded on purpose: a future audit
    has to be able to tell "the queue shrank" from "the rows were grouped"."""
    return {
        "open_by_kind": _kind_counts(open_items),
        "surfaced_by_kind": _kind_counts(shown),
        "queue_rows_rendered": len(shown),
        "queue_items_represented": sum(
            max(1, int((it or {}).get("digest_count") or 1)) for it in shown),
        "queue_open_total": len(open_items),
        "digest_rows": digest_stats.get("digest_rows", 0),
        "digest_items_grouped": digest_stats.get("grouped_items", 0),
        "digests_by_class": dict(digest_stats.get("by_class") or {}),
        "page_bound": {"cap": bound_stats.get("cap"),
                       "queue_budget": bound_stats.get("budget"),
                       "extra_section_rows": n_extra_rows,
                       "held_back": bound_stats.get("dropped", 0)},
    }


def build_waiting_on_view(workspace_root, *, now_iso: str | None = None,
                          chase_rows: list | None = None) -> dict:
    """The daily Waiting On chat data view (CTS1 Surface 1 —
    orchestrator-commitments, mechanized): canonical loader + `surface_split`
    five-way partition + the count headline + the deterministic delegated and
    confirm-tail sections. Pure read.

    `chase_rows` (the pre-staged chase-email items for the CRU-eligible
    owed-to-you commitments — connector-dependent, so built by the orchestrator
    exactly like build_staff_meeting_view's `moves_rows`) are appended VERBATIM
    as the leading "Waiting On" section; the driver never composes an email body
    or touches a connector. Everything else — the partition, the header counts,
    the delegated-task rows, and the unowned/unconfirmed confirm tail — is
    deterministic and built here, killing the ~30-command surface the
    orchestrator assembled live (FB-15). Owner-me rows never surface here (they
    are My Plate — the partition routes them away)."""
    from commitment_activity import derive_commitment_movement
    from commitment_state import count_commitments
    from confirm_flow import unconfirmed_classes
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from surface_split import (SURFACE_UNCONFIRMED, SURFACE_UNOWNED,
                               SURFACE_WAITING_ON, effective_kind_of,
                               partition_surfaces)

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    # BUG-8330 item 14 (fix round FX-3) — collapse suspected-duplicate
    # components BEFORE the counts are taken. A duplicate fold is an IDENTITY
    # operation, not a row filter: the folded rows are the SAME commitment, so
    # counting them separately double-counts one real promise. This is not the
    # F-47 exception below — F-47 keeps headline numbers full-set against ROW
    # FILTERS (visibility), and the fold is not one. The review's B3 shape
    # ("2 owed to you" over an empty surface) needed both halves: the cycle
    # guard in `fold_suspected_duplicates`, and the header reconciling here.
    opens, n_dup_folded = _fold_dups(opens)
    # BUG-8330 item 6 — the confidence floor lives HERE (code), not in
    # orchestrator prose. It filters ROWS only: header counts stay computed
    # over the FULL open set (the F-47 rule — headline numbers identical
    # across morning brief / this chat / commitment-triage; row filters
    # never shrink them). The receipt records how many rows the floor
    # removed, so a 70→6 collapse is visible instead of silent.
    surfaced, n_conf_filtered = _apply_confidence_floor(ws, opens)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=now_iso, movement=movement)
    part = partition_surfaces(surfaced, user_id)

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    display_n = 0
    sections: list[dict] = []
    rr_cache: dict = {}  # RRF1 per-render-pass memo (name -> resolves now)

    # 1. Chase drafts — orchestrator-supplied (connector-dependent), appended
    #    verbatim: email-shaped rows whose pre-staged nudge lives in the widget.
    if chase_rows:
        rows = []
        for r in chase_rows:
            display_n += 1
            row = dict(r)
            row.setdefault("display_n", display_n)
            rows.append(row)
        sections.append({"title": "Waiting On", "count": len(rows),
                         "items": rows})

    # 2. Delegated tasks (owner != user, effective kind `task`) — CRU-ineligible,
    #    so no pre-staged chase; the delegated set (`nudge` composes the chase
    #    on click — D-A4) per orchestrator-commitments §2.3.
    delegated = [ev for ev in part[SURFACE_WAITING_ON]
                 if effective_kind_of(ev) == "task"]
    if delegated:
        people_by_id = _people_by_id(ws)
        rows = []
        for ev in delegated:
            display_n += 1
            d = ev.get("data") or {}
            age = _age_days(ev.get("ts") or "", now_iso)
            bits = []
            if age is not None and age >= 0:
                bits.append("1 day old" if age == 1 else f"{age} days old")
            bits.append(_due_phrase(d.get("due"), now_iso))
            # A delegated row is owner != M by definition — name who we're
            # waiting on (FIX A); fall back to bare "delegated" only if no name
            # resolves.
            owner_name = _owner_display_name(ev, people_by_id)
            bits.append(
                f"delegated to {owner_name} — nudge is manual, "
                "I won't auto-chase this" if owner_name
                else "delegated — nudge is manual, I won't auto-chase this")
            _dfn = _dup_fold_note(d)
            if _dfn:
                bits.append(_dfn)
            # `nudge` (WG1-A D-A4) composes the chase on demand at dispatch
            # (no pre-staged body). Resolve the owner's email so the row
            # carries a real To:; when none is on file, degrade `nudge` to the
            # `add email then send` recovery verb so the surface still renders
            # (never a dead button — the Bug #44 principle). THIS DEGRADE IS
            # THE ONLY GUARD: renderer Gate 6 (_SEND_CLASS_ACTIONS) deliberately
            # does NOT include `nudge`, because the WG1-B D-B4 moves adapter
            # (relationship_moves.moves_rows_from_candidates) legitimately
            # emits To-less nudge rows on scheduled staff-meeting fires
            # (compose-on-click resolves the address at dispatch). Train-merge
            # review F-4 ruling 2026-07-22: keep nudge out of the frozenset;
            # this driver-level degrade is the enforcement for delegated rows.
            email = _owner_email(ev, people_by_id)
            row: dict = {
                "n": _cid(ev), "display_n": display_n,
                "name": d.get("title") or d.get("summary") or "(untitled)",
                "context_tag": " · ".join(bits),
            }
            if email:
                row["metadata"] = [["To", email]]
                row["actions"] = list(_DELEGATED_VERBS)
            else:
                row["actions"] = ["add email then send"] + [
                    v for v in _DELEGATED_VERBS if v != "nudge"]
            rows.append(row)
        sections.append({"title": "Delegated", "count": len(rows),
                         "items": rows})

    # 3. Confirm tail — unowned + pending_review (unconfirmed). The REVIEW
    #    cluster only: NEVER a pre-staged chase on an unconfirmed/unowned item
    #    (no auto-email on a guessed owner — orchestrator-commitments §458/§478).
    confirm_evs = list(part[SURFACE_UNOWNED]) + list(part[SURFACE_UNCONFIRMED])
    if confirm_evs:
        rows = []
        for ev in confirm_evs:
            display_n += 1
            d = ev.get("data") or {}
            pending = "pending_review" in unconfirmed_classes(ev)
            tag = ("captured from a chat — confirm it's yours" if pending
                   else "no owner resolved yet — whose is this?")
            if d.get("review_reason"):
                tag += f" · {_display_review_reason(ws, d['review_reason'], rr_cache)}"
            rows.append({
                "n": _cid(ev), "display_n": display_n,
                "name": d.get("title") or d.get("summary") or "(untitled)",
                "context_tag": tag,
                "actions": list(_REVIEW_VERBS),
            })
        sections.append({"title": "Needs a quick confirm", "count": len(rows),
                         "items": rows})

    h = counts["headline"]
    counters = [
        {"label": "Owed to you", "value": h["owed_to_you"]},
        {"label": "Unowned", "value": h["unowned"]},
        {"label": "Unconfirmed", "value": h["unconfirmed"]},
    ]
    header = f"Waiting On — {h['owed_to_you']} owed to you"
    if n_conf_filtered:
        # Item 6's honesty rule: the headline stays the full-set truth, so
        # any gap between it and the rows below must say WHY, on-surface.
        header += f" ({n_conf_filtered} low-confidence not shown)"
    return {
        "source_skill": "commitments",
        "header": header,
        "counters": counters,
        "sections": sections,
        "receipt_extra": {"n_filtered_by_confidence": n_conf_filtered,
                          "n_duplicates_folded": n_dup_folded},
    }


def build_my_plate_view(workspace_root, *, now_iso: str | None = None,
                        status_rows: list | None = None,
                        personal_cap: int = _MP_PERSONAL_CAP,
                        promised_cap: int = _MP_PROMISED_CAP) -> dict:
    """The daily My Plate chat data view (CTS1 Surface 2 — orchestrator-my-plate,
    mechanized): canonical loader + `surface_split` partition + the count
    headline + the two owner-me groups. Pure read.

    Mirrors `build_waiting_on_view` exactly (FB-plumbing item 6 — kill the inline
    builder scripts): the DETERMINISTIC, connector-free row classes are built
    here — the counterparty-unresolved Promised fixup rows (Bug #103) and the
    whole Personal group — while the connector-DEPENDENT email-shaped status
    drafts for counterparty-resolved Promised rows are composed by the
    orchestrator (email-writer chain) and passed VERBATIM as `status_rows`,
    appended as the LEADING rows of the Promised section (the `chase_rows`
    parallel). The driver never composes an email body or touches a connector.
    Waiting-on rows (owner != user) never surface here — the partition routes
    them to the Waiting On chat; unowned / pending_review rows are that chat's
    confirm tail, never My Plate's (this is a pure act-list).

    `personal_cap` (CTS1 §4.2, the `my-plate` skill config knob, default 7)
    caps the Personal group; the section footer carries the "+N more — say
    'show my plate' for everything" tail when rows are hidden. `show my plate`
    re-renders with the cap lifted (the orchestrator passes a large cap)."""
    from commitment_activity import derive_commitment_movement
    from commitment_state import count_commitments
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from surface_split import (SURFACE_PERSONAL, SURFACE_PROMISED,
                               counterparty_unresolved, partition_surfaces)

    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    events_path = _events_path(ws)
    opens = load_open_commitments(events_path)
    # BUG-8330 item 14 (fix round FX-3) — same identity-before-counts fold as
    # Waiting On: duplicates collapse first, so the header counts one real
    # promise once.
    opens, n_dup_folded = _fold_dups(opens)
    # BUG-8330 item 6 — same code-side floor as build_waiting_on_view: rows
    # filter, header counts stay full-set (F-47), receipt carries the delta.
    surfaced, n_conf_filtered = _apply_confidence_floor(ws, opens)
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    movement = derive_commitment_movement(events_path)
    counts = count_commitments(opens, user_person_id=user_id,
                               now_iso=now_iso, movement=movement)
    part = partition_surfaces(surfaced, user_id)
    promised = part[SURFACE_PROMISED]
    personal = part[SURFACE_PERSONAL]

    def _cid(ev) -> str:
        d = ev.get("data") or {}
        return d.get("id") or f"commitment_seq_{ev.get('seq')}"

    display_n = 0
    sections: list[dict] = []

    # === Group A — PROMISED (someone's waiting; renders FIRST) =============
    promised_rows: list[dict] = []

    # 1. Orchestrator-supplied status drafts (connector-dependent), appended
    #    verbatim: email-shaped rows whose pre-drafted status lives in the
    #    widget (the counterparty-RESOLVED, external-recipient promises).
    if status_rows:
        for r in status_rows:
            display_n += 1
            row = dict(r)
            row.setdefault("display_n", display_n)
            promised_rows.append(row)

    # 2. Counterparty-UNRESOLVED promises (deterministic, connector-free): a
    #    real promise whose counterparty linking failed. The fixup IS the
    #    action (no recipient to draft to). NEVER auto-demoted to Personal.
    for ev in promised:
        if not counterparty_unresolved(ev, user_id):
            continue
        display_n += 1
        d = ev.get("data") or {}
        age = _age_days(ev.get("ts") or "", now_iso)
        bits = []
        if age is not None and age >= 0:
            bits.append("1 day old" if age == 1 else f"{age} days old")
        bits.append(_due_phrase(d.get("due"), now_iso))
        _dfn = _dup_fold_note(d)
        if _dfn:
            bits.append(_dfn)
        bits.append("counterparty unresolved — who was this for?")
        promised_rows.append({
            "n": _cid(ev), "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(bits),
            "actions": list(_MP_UNRESOLVED_VERBS),
        })

    # 3. RESIDUAL promised rows (BUG-8330 item 5, deterministic): a
    #    counterparty-RESOLVED promise the orchestrator composed no status
    #    draft for. Before this class existed such a promise was ABSENT from
    #    the widget — not capped, absent — while the header counted it; the
    #    "+49 more" the user saw was model-composed. Draft-less is a state,
    #    not an exclusion.
    status_ids = {str(r.get("n")) for r in (status_rows or []) if r.get("n")}
    for ev in promised:
        if counterparty_unresolved(ev, user_id):
            continue
        if _cid(ev) in status_ids:
            continue
        display_n += 1
        d = ev.get("data") or {}
        age = _age_days(ev.get("ts") or "", now_iso)
        bits = []
        if age is not None and age >= 0:
            bits.append("1 day old" if age == 1 else f"{age} days old")
        bits.append(_due_phrase(d.get("due"), now_iso))
        _dfn = _dup_fold_note(d)
        if _dfn:
            bits.append(_dfn)
        promised_rows.append({
            "n": _cid(ev), "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(bits),
            "actions": list(_MP_RESIDUAL_VERBS),
        })

    # Explicit cap + honest footer (BUG-8330 item 5): the section shows at
    # most `promised_cap` rows and SAYS how many it is holding back — the
    # footer_note now survives both render paths.
    p_cap = promised_cap if promised_cap and promised_cap > 0 \
        else len(promised_rows)
    p_hidden = len(promised_rows) - min(len(promised_rows), p_cap)
    if promised_rows:
        sec = {"title": "↗ PROMISED — someone's waiting",
               "count": len(promised_rows),
               "items": promised_rows[:p_cap]}
        if p_hidden > 0:
            sec["footer_note"] = (f"+{p_hidden} more promised — say "
                                  "'show my plate' for everything")
        sections.append(sec)

    # === Group B — PERSONAL (my own work; capped) =========================
    # Sort: dated first (due soonest), then undated by most-recently-touched
    # (the movement ts, capture ts floor) — newest first. Deterministic.
    def _touch_ts(ev) -> str:
        d = ev.get("data") or {}
        mv = movement.get(_cid(ev)) if isinstance(movement, dict) else None
        if isinstance(mv, dict) and mv.get("ts"):
            return str(mv["ts"])
        return str(ev.get("ts") or "")

    def _personal_key(ev):
        d = ev.get("data") or {}
        due = d.get("due")
        due_s = str(due)[:10] if due else None
        # dated rows first (0), sorted by due asc; undated (1), newest touch first
        return (0, due_s, "") if due_s else (1, "", _neg_ts(_touch_ts(ev)))

    personal_sorted = sorted(personal, key=_personal_key)
    cap = personal_cap if personal_cap and personal_cap > 0 else len(personal_sorted)
    shown = personal_sorted[:cap]
    hidden = len(personal_sorted) - len(shown)

    personal_rows: list[dict] = []
    for ev in shown:
        display_n += 1
        d = ev.get("data") or {}
        age = _age_days(ev.get("ts") or "", now_iso)
        bits = []
        if age is not None and age >= 0:
            bits.append("1 day old" if age == 1 else f"{age} days old")
        bits.append(_due_phrase(d.get("due"), now_iso))
        _dfn = _dup_fold_note(d)
        if _dfn:
            bits.append(_dfn)
        personal_rows.append({
            "n": _cid(ev), "display_n": display_n,
            "name": d.get("title") or d.get("summary") or "(untitled)",
            "context_tag": " · ".join(bits),
            "actions": list(_MP_PERSONAL_VERBS),
        })
    if personal_rows:
        sec = {"title": "PERSONAL — your own list",
               "count": len(personal_rows), "items": personal_rows}
        if hidden > 0:
            sec["footer_note"] = (f"+{hidden} more — say 'show my plate' "
                                  "for everything")
        sections.append(sec)

    h = counts["headline"]
    counters = [
        {"label": "On your plate", "value": h["you_owe"]},
        {"label": "Promised", "value": len(promised)},
        {"label": "Personal", "value": len(personal)},
        {"label": "Waiting on others", "value": h["owed_to_you"]},
    ]
    header = (f"My Plate — {h['you_owe']} on your plate "
              f"({len(promised)} promised · {len(personal)} personal)")
    if n_conf_filtered:
        # Same on-surface honesty as Waiting On: the headline is full-set,
        # the groups are row-level — the gap must name itself.
        header += f" · {n_conf_filtered} low-confidence not shown"
    return {
        "source_skill": "commitments",
        "header": header,
        "counters": counters,
        "sections": sections,
        "receipt_extra": {"n_filtered_by_confidence": n_conf_filtered,
                          "n_duplicates_folded": n_dup_folded},
    }


def _apply_confidence_floor(ws, opens: list) -> tuple[list, int]:
    """BUG-8330 item 6 — the ONE confidence surface filter, in code.

    The floor previously existed only as orchestrator prose (a bare constant
    applied by hand over `_commitment_confidence`, whose missing→0.0 default
    silently dropped every unscored capture). `passes_surface_floor` treats
    missing as unscored (passes) and resolves the floor through
    `confidence.surface_min(ws)` — so the per-workspace calibration override
    finally moves the filter that matters. Returns (kept, n_filtered);
    defensive — any failure keeps the full set (a broken floor must never
    blank a daily surface)."""
    try:
        from confidence import surface_min
        from cru_match import passes_surface_floor
        floor = surface_min(ws)
        kept = [ev for ev in opens if passes_surface_floor(ev, floor=floor)]
        return kept, len(opens) - len(kept)
    except Exception:
        return opens, 0


def _fold_dups(surfaced: list) -> tuple[list, int]:
    """BUG-8330 item 14 — the render-time suspected-duplicate fold, applied to
    the OPEN set of the daily surfaces before rows and counts are derived from
    it (fix round FX-3: a fold is identity, not visibility, so the header must
    see the folded set or it double-counts one promise). Never applied to
    triage, whose pin block is the merge-adjudication surface and must show
    both rows. Defensive: any failure keeps the set unfolded."""
    try:
        from commitment_dedup import fold_suspected_duplicates
        return fold_suspected_duplicates(surfaced)
    except Exception:
        return surfaced, 0


def _dup_fold_note(d: dict) -> str | None:
    n = d.get("duplicate_fold_count")
    if isinstance(n, int) and n > 1:
        return f"{n} records — merge?"
    return None


def _neg_ts(ts: str) -> str:
    """Sort helper — invert an ISO ts so `sorted(asc)` yields newest-first.
    Deterministic + stdlib-only: complement each digit so a later timestamp
    sorts earlier. Non-digits pass through (they compare stably)."""
    tbl = str.maketrans("0123456789", "9876543210")
    return (ts or "").translate(tbl)


def _last_brief_ts(workspace_root, now_iso: str) -> str:
    """The CHANGED window's opening edge: the newest prior morning-brief
    receipt's timestamp, else the newest `brief_state` event's ts, else
    36 hours back (first-ever brief — one day plus slack so an overnight
    install still gets a real window, never an empty-string scan)."""
    from event_time import parse_ts
    from receipts import iter_receipts

    try:
        receipts = iter_receipts(workspace_root, task_ids=["morning-brief"])
        dts = [r["dt"] for r in receipts if r.get("dt") is not None]
        if dts:
            return max(dts).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    try:
        import json as _json
        newest = None
        p = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if '"brief_state"' not in line:
                    continue
                try:
                    ev = _json.loads(line)
                except Exception:
                    continue
                # EVGUARD sibling-rail (joined by the Slot 9 sweep) — a
                # top-level bare string containing `brief_state` clears the
                # substring pre-filter above and PARSES, so it used to reach
                # `.get()`. The AttributeError was swallowed by this function's
                # outer `except Exception: pass`, and the brief's CHANGED
                # window silently collapsed to the 36-hour floor.
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "brief_state" and ev.get("ts"):
                    newest = ev["ts"]
        if newest:
            return newest
    except Exception:
        pass
    base = parse_ts(now_iso)
    return (base - _dt.timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_morning_brief_pack(workspace_root, *, mode: str = "scheduled",
                             now_iso: str | None = None) -> dict:
    """t3 FB-9 — the morning brief's mandatory substrate blocks, assembled,
    validated, and persisted in ONE call (the t2.2 skip-proofing pattern
    that fixed commitments/staff-meeting). A live post-update fire skipped
    the brain-card widget AND the substrate-alarm line while the pre-update
    fire rendered both — instruction-layer MUSTs (FS-09) don't survive an
    orchestrator that stops early; a driver whose single output carries
    every block does.

    Returns (and persists to `_hq/.system/briefs/`) the pack:

      alarm_lines    substrate_health.substrate_alarm_lines — render
                     VERBATIM at the very top of the brief (FS-04/05/06/15).
                     This block is ALSO this entry point's mount-freshness
                     answer, and the reason it does NOT take the hard refusal
                     `run_board` / `run_surface` take. A stale view here is
                     already LOUD — the same syncing vocabulary, first block,
                     above everything — so the brief degrades honestly instead
                     of vanishing. The refusal exists on the other two because
                     a widget page and a board page carry no alarm surface at
                     all: they would publish stale rows and stale counts
                     silently.
      changed        change_feed.changes_since(<last brief ts>) — the lines
                     the CHANGED contract line MUST cite when non-empty
                     (FS-09; "Nothing material" over a non-empty feed is
                     the bug).
      brief_state    compute_and_log_brief_state's counts headline +
                     needs_attention + reconcile_stale. THE one Step-3d
                     derivation — the driver call writes the `brief_state`
                     audit event, so the orchestrator must NOT call it
                     again (one event per fire).
      watchdog_line  task_watchdog.brief_watchdog_line — append verbatim
                     when non-None (S3 light pass).
      money_lines    FB-20's ONE carve-out: money-class proposals (deal
                     signals) as one prose sentence each, propose-only —
                     "Command Room thinks [Org] is a live deal — say staff
                     meeting to confirm." Money is the one class that may
                     never go silent, so it is NAMED in the brief; the
                     adjudication still happens at the staff meeting, by
                     chat phrase, with no widget. Capped at
                     MONEY_PROSE_CAP (the pointer's count carries the rest —
                     a cap is a render bound, never a silence).
      queue_pointer  {count, line} — the live count of everything the staff
                     meeting would render, and the ONE pointer line that
                     replaces the card ("N things need your eyes — say
                     staff meeting"). count == what `staff meeting` actually
                     shows (same projector, same surface, same held/mute
                     filters), so the number can never over-promise. Zero →
                     empty line, nothing renders (drop-empty).

    FB-20 (M's ruling 2026-07-16 — "the morning brief should just be a
    morning brief"): this pack emits NO widget and NO confirm card. The
    brief is a READ-ONLY prose surface; the staff meeting is the sole
    adjudication surface (run it more often instead). A `transport` key from
    this driver is a contract violation — the T3.2 relay machinery stays
    intact for every OTHER surface, but the brief has exited the widget
    business entirely. There is nothing to relay, so nothing can be dropped
    on the way to the relay (the FB-18 failure mode is gone by construction,
    not by instruction).

    Every text line in the pack passes the chat-output leak scan before
    return — a leaking canonical line fails HERE, loudly, not in M's chat.
    Connector-dependent digest content (calendar, mail, Slack) remains the
    orchestrator's job; this pack is the substrate half — the half that
    kept getting skipped.
    """
    from chat_output_renderer import validate_chat_output
    from change_feed import changes_since
    from commitment_state import compute_and_log_brief_state
    from cru_match import load_open_commitments
    from primary_user import resolve_primary_user
    from substrate_health import substrate_alarm_lines
    from task_watchdog import brief_watchdog_line

    if mode not in ("scheduled", "manual"):
        raise ValueError(f"mode must be scheduled|manual; got {mode!r}")
    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()

    alarm_lines = list(substrate_alarm_lines(ws) or [])

    since_ts = _last_brief_ts(ws, now_iso)
    feed = changes_since(ws, since_ts, now_iso=now_iso, max_lines=3)
    changed_lines = [l.get("text", "") for l in (feed.get("lines") or [])
                     if l.get("text")]

    opens = load_open_commitments(_events_path(ws))
    try:
        user_id = resolve_primary_user(ws)
    except Exception:
        user_id = None
    state = compute_and_log_brief_state(
        ws, open_commitments=opens, user_person_id=user_id, now_iso=now_iso,
        # BRIEFFIX1 Item C / F1 — the driver is the ONE place that already
        # knows the run mode, so it is the place that stamps it. Without this
        # the audit event is identical on both paths and the receipt-ordering
        # check cannot tell a hand-run brief from a scheduled one.
        fired_via=mode)
    # CAPTUREFLOW §D — the lane is BOUND here, at the render, never at the
    # derivation: `compute_brief_state` keeps returning the full list and the
    # `brief_state` audit event keeps counting all of it, so the header counts
    # stay unfiltered (the :299 doctrine). What the brief PRINTS is the top
    # N by due-then-age plus one honest pointer line, with the 14-day rotation
    # so nothing below the fold can go permanently invisible.
    from commitment_state import cap_needs_attention
    lane = cap_needs_attention(state.get("needs_attention") or [],
                               now_iso=now_iso)
    brief_state = {
        "headline": (state.get("counts") or {}).get("headline") or {},
        "needs_attention": lane["shown"],
        "needs_attention_total": lane["n_total"],
        "needs_attention_more": lane["n_more"],
        "needs_attention_more_line": lane["more_line"],
        "reconcile_stale": state.get("reconcile_stale"),
    }

    try:
        watchdog = brief_watchdog_line(ws)
    except Exception:
        watchdog = None

    # FB-20 — the queue POINTER (not the queue). The brief names no rows and
    # renders no card; it points at the surface that adjudicates.
    #
    # WHAT THE COUNT MEANS, precisely, because STAFFCUT changed it. The count is
    # the OPEN ITEMS on the staff-meeting projection — how many things are
    # waiting. It is no longer the number of ROWS that surface renders: the
    # staff meeting now groups items into evidence-class digests and bounds the
    # page, so on a heavy week it can render ~21 rows against ~100 open items.
    # "The same number by construction" was true before those two passes existed
    # and is not true now.
    #
    # ITEMS is the honest number for a POINTER, and deliberately so. The brief
    # sentence is "N things need your eyes" — a promise about the user's
    # workload, which the queue's own presentation choices must not deflate.
    # Reporting the row count would understate what is waiting, which is the
    # FS-09 dishonesty in the other direction; the staff meeting itself then
    # states its own render arithmetic in its section titles (§3.1), so the two
    # surfaces disagree about nothing — they are answering different questions.
    from brain_proposals import load_open_proposals, money_prose_lines
    queue = load_open_proposals(ws, "staff-meeting", now_iso=now_iso)
    # Auto-tier items are applied-then-narrated, never adjudicated (LB1
    # review F5) — they are not "things that need your eyes". Redundant
    # since LB2 flipped the projector default (include_auto=False) — kept
    # as defense-in-depth; the parity pin tests the projector, not this.
    queue = [i for i in queue if i.get("tier") != "auto"]
    money_lines = money_prose_lines(queue, cap=MONEY_PROSE_CAP)
    queue_pointer = {"count": len(queue), "line": _pointer_line(len(queue))}

    # Leak-scan every text line the pack hands the orchestrator. Loud by
    # design — there is no widget validator behind this one any more.
    scannable = "\n".join(
        alarm_lines + changed_lines + ([watchdog] if watchdog else [])
        + money_lines
        + ([queue_pointer["line"]] if queue_pointer["line"] else [])
        # CAPTUREFLOW §D — the lane's overflow pointer is a text line the
        # orchestrator prints verbatim, so it is scanned like every other one.
        + ([brief_state["needs_attention_more_line"]]
           if brief_state.get("needs_attention_more_line") else [])
    )
    if scannable.strip():
        validate_chat_output(scannable)

    pack = {
        "surface": "morning-brief",
        "mode": mode,
        "now": now_iso,
        "alarm_lines": alarm_lines,
        "changed": {"since_ts": since_ts, "lines": changed_lines},
        "brief_state": brief_state,
        "watchdog_line": watchdog,
        "money_lines": money_lines,
        "queue_pointer": queue_pointer,
    }

    # Persist the pack (audit trail, parallel to the widget audit files).
    try:
        from atomic_write import atomic_write_text
        out_dir = ws / "_hq" / ".system" / "briefs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_iso[:19].replace(":", "-")
        atomic_write_text(out_dir / f"morning-pack-{stamp}.json",
                          json.dumps(pack, indent=2, ensure_ascii=False))
    except Exception:
        pass  # the pack in hand is what matters; the audit copy is best-effort

    return pack


# ---------------------------------------------------------------------------
# Fire receipts (FB-7 — the receipt writes INSIDE the driver call)
# ---------------------------------------------------------------------------

# surface -> the canonical receipts.py task its fire receipts belong to.
_SURFACE_TASKS = {"commitments": "commitment-triage",
                  "staff-meeting": "staff-meeting",
                  "waiting-on": "waiting-on",   # FB-15 (CTS1 taskId)
                  "my-plate": "my-plate"}       # FB-plumbing item 6 (CTS1 Surface 2)

# RV-3 guard: a NON-MANUAL driver re-run this close to an already-written
# non-manual receipt is the same fire re-rendering (the live 2026-07-16
# staff-meeting double-render), not a second run — one receipt, never two.
# Manual fires never dedup: two back-to-back manual sweeps are two real
# runs (F-08).
_REFIRE_RECEIPT_GUARD = _dt.timedelta(minutes=15)


def _log_fire_receipt(workspace_root, surface: str, view: dict,
                      fired_via: str, extra_data: dict | None = None) -> dict:
    """Append the surface's per-fire pack_run receipt via the canonical
    helper (receipts.log_receipt — NEVER hand-rolled JSON). Runs inside
    run_surface's page-1 invocation so the widget render and the receipt
    can never be separated (FB-7: the scheduled staff-meeting fire posted
    its widget, then the turn ended before the prose receipt step).

    `extra_data` (STAFFCUT D2, optional) rides `log_receipt(extra_data=...)`
    untouched — the per-kind counts and digest arithmetic the staff meeting now
    records. ADDITIVE ONLY: `surfaced` keeps its exact meaning (the rows the
    widget showed), the receipt's contract fields are unchanged, and
    `log_receipt` already refuses to let extra keys overwrite them. Every
    existing receipt reader is defensive about unknown keys, and legacy
    receipts that carry none keep parsing byte-identically."""
    from receipts import iter_receipts, log_receipt, normalize_fired_via

    task_id = _SURFACE_TASKS[surface]
    via = normalize_fired_via(fired_via)
    surfaced = sum(len(sec.get("items") or [])
                   for sec in view.get("sections") or [])
    if via != "manual":
        now = _clock_now(workspace_root)
        recent = iter_receipts(workspace_root, task_ids=[task_id],
                               since=now - _REFIRE_RECEIPT_GUARD)
        if any(r["fired_via"] != "manual" for r in recent):
            return {"task_id": task_id, "fired_via": via,
                    "surfaced": surfaced, "status": "deduped_refire"}
    log_receipt(workspace_root, task_id, fired_via=via, surfaced=surfaced,
                extra_data=extra_data or None)
    out = {"task_id": task_id, "fired_via": via, "surfaced": surfaced,
           "status": "written"}
    if extra_data:
        out["extra_data"] = dict(extra_data)
    return out


_SURFACE_NAME_HINTS = {"commitments": "commitment-triage",
                       "staff-meeting": "staff-meeting",
                       "waiting-on": "waiting-on",
                       "my-plate": "my-plate"}


def run_watch_expiry_pass(workspace_root, *, now_iso=None) -> dict:
    """WATCHGATE MUST-FIX-2 — the scheduled driver for stakes-routed expiry.

    §2.3 makes expiry the TERMINAL behavior of every parked item, §2.4's whole
    table is "unproven after window", and §2.7's cap is defined "per staff-
    meeting fire" — a phrase that names an orchestrator, not a pure function.
    Built but undriven, WATCHING was a one-way door: items parked and were
    never routed, never assumed, never asked about. §0 forbids exactly that
    ("never silently stops watching"), so this runs on the staff-meeting fire
    every workspace already has — no registration, no setup, no connector.

    Defensive by construction: ANY failure returns an empty result and the
    fire proceeds. An expiry pass that could take the staff meeting down with
    it would be a worse bug than the one it fixes.

    WATCHGATE N-5: "defensive" used to mean "invisible". A skipped pass wrote
    one line to STDERR — which nothing on a scheduled fire reads — and returned
    a zeroed shape indistinguishable from a healthy quiet day, so nobody ever
    learned that WATCHING had stopped routing. Per-item failures are contained
    inside `run_watch_expiry` and counted there; both counts ride the fire's
    RECEIPT (`_log_fire_receipt`'s `extra_data`), which is a record someone can
    actually read later. Additive: on a healthy pass neither key is written and
    the receipt is byte-identical to a pre-N-5 one.

    Returns run_watch_expiry's result, or a zeroed shape marked `pass_skipped`.
    """
    empty = {"assumed": [], "ask": [], "carried": [], "not_due": [],
             "results": [], "n_assumed": 0, "n_ask": 0, "n_carried": 0,
             "n_failed": 0, "failures": [], "pass_skipped": False}
    try:
        from primary_user import resolve_primary_user
        from watch_gate import run_watch_expiry

        return run_watch_expiry(
            workspace_root,
            resolved_by=resolve_primary_user(workspace_root) or "",
            source_skill="staff-meeting",
            now_iso=now_iso,
        )
    except Exception as exc:  # pragma: no cover — the fire must survive
        sys.stderr.write(f"[surface_drivers] watch expiry pass skipped: "
                         f"{exc}\n")
        return dict(empty, pass_skipped=True)


def watch_expiry_receipt_extra(pass_result) -> dict:
    """The expiry pass's health, in receipt keys — {} when it was healthy.

    Split out so the ONE place that builds it is testable without a render, and
    so a second caller cannot invent its own spelling of the same fact.
    """
    out: dict = {}
    if not isinstance(pass_result, dict):
        return out
    n_failed = int(pass_result.get("n_failed") or 0)
    if n_failed:
        out["watch_expiry_failed"] = n_failed
    if pass_result.get("pass_skipped"):
        out["watch_expiry_skipped"] = True
    return out


def _build_surface_view(surface: str, ws, *, now_iso, moves_rows,
                        chase_rows, status_rows, personal_cap,
                        promised_cap=_MP_PROMISED_CAP,
                        watch_rows=None) -> dict:
    """Build ONE surface's data view from LIVE substrate. The only place that
    reads; `run_surface` decides WHEN it is allowed to be called."""
    if surface == "commitments":
        return build_commitment_triage_view(ws, now_iso=now_iso)
    if surface == "staff-meeting":
        return build_staff_meeting_view(ws, now_iso=now_iso,
                                        moves_rows=moves_rows,
                                        watch_rows=watch_rows)
    if surface == "waiting-on":
        return build_waiting_on_view(ws, now_iso=now_iso,
                                     chase_rows=chase_rows)
    if surface == "my-plate":
        return build_my_plate_view(ws, now_iso=now_iso,
                                   status_rows=status_rows,
                                   personal_cap=personal_cap,
                                   promised_cap=promised_cap)
    raise SystemExit(
        f"unknown surface {surface!r} "
        "(supported: commitments, staff-meeting, waiting-on, my-plate)")


def run_board(workspace_root, *, now_iso: str | None = None,
              persist_dir=None) -> dict:
    """SPEC_BOARD1 — the commitments surface, serialized as an artifact board.

    This is the ARTIFACT BRANCH of the commitments pipeline, not a second
    pipeline. Read the order and note what is shared:

      0. `refuse_if_mount_stale` — SPEC SYNC1 A4's mount-freshness preflight,
         run BEFORE the read. ok=false → refuse in the existing syncing
         vocabulary, producing no page, no receipt, no page-set and no
         events. (The preflight's own alarm sidecar and alert artifact are
         written on that path by design — see `MountStaleError`.)
      1. `build_commitment_triage_view` — THE view. Identical call, identical
         helpers, identical rows to the widget path (§3: one derivation; the
         artifact mode differs only at the serialization step).
      2. `chat_output_renderer.validate_data_view` — the pre-render gate
         family the widget path runs (canonical actions, data shape, pulse
         richness, send-class emails, the voice-tell backstop). CALLED, not
         re-typed.
      3. `artifact_board.render_board_html` — the serialization. The ONLY
         step that differs from the widget path.
      4. `chat_output_renderer.scan_rendered_html` — the same leak scan, with
         the same style/script/href/data-* preparation the widget path uses.
      5. `artifact_board.validate_board_html` — the board-shaped structural
         contract (the `validate_rendered_widget` analog: that validator
         asserts the widget DOM, which this page is not).
      6. persist to `_hq/.system/widgets/` — the same audit trail the
         persisted widget pages already write to.

    NO PAGINATION (§3 full-set single page): the board is one document, so
    there is no page-set to freeze and nothing is dropped — a single render is
    its own snapshot, which is why PAGESNAP does not apply here. NO RECEIPT:
    a receipt records a FIRE of a scheduled surface; publishing a board is not
    one, and writing one would corrupt the triage fire history the load audits
    read.

    Returns {"html", "path", "file_uri", "board": {...}} — no `pagination`
    key, deliberately, so no caller can mistake this for the widget transport.
    """
    from artifact_board import (generated_at_line, lane_counts,
                                render_board_html, validate_board_html)
    from chat_output_renderer import scan_rendered_html, validate_data_view

    ws = Path(workspace_root)
    # 0. MOUNT-FRESHNESS PREFLIGHT — before the read, not after it. A board
    #    built from a stale mount either dies in a downstream validator naming
    #    the wrong cause or, worse, publishes stale rows and stale counts with
    #    nothing on the page saying so.
    refuse_if_mount_stale(ws)
    now_iso = now_iso or _now_iso()
    view = build_commitment_triage_view(ws, now_iso=now_iso)
    validate_data_view(view)
    html = render_board_html(
        view, generated_at=now_iso,
        stamp_line=generated_at_line(now_iso, ws))
    scan_rendered_html(html)
    counts = lane_counts(view)
    # Conservation: the page must carry exactly the rows THE VIEW handed over.
    #
    # Derived from the view's own sections, NOT from `lane_counts` (review
    # F-2). `lane_counts` calls the same `partition_board` the page is
    # serialized from, so a partitioner that dropped a row lowered the
    # expectation by exactly the rows it dropped and the gate agreed with
    # itself — measured at 8 rows in, 6 rendered, `expect_rows` 6, no raise.
    # That is the self-referential-assertion gotcha in its runtime form: the
    # suite caught the mutation, the gate that ships to real data did not.
    # The view's `sections` are the one count upstream of the partition.
    # (Sub-items ride nested inside their parent row, so a section's `items`
    # is exactly the top-level population `validate_board_html` counts.)
    #
    # An empty board is a legitimate answer; a board one row short is not.
    expect_rows = sum(len(s.get("items") or [])
                      for s in (view.get("sections") or []))
    validate_board_html(html, expect_rows=expect_rows)

    persist_dir = Path(persist_dir) if persist_dir is not None \
        else ws / "_hq" / ".system" / "widgets"
    persist_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso[:19].replace(":", "-")
    out_path = persist_dir / f"commitment-board_{stamp}.html"
    from atomic_write import atomic_write_text
    atomic_write_text(out_path, html)
    file_uri = "file:///" + str(out_path.resolve()).replace("\\", "/").lstrip("/")

    return {
        "html": html,
        "path": out_path,
        "file_uri": file_uri,
        "board": {
            "generated_at": now_iso,
            "generated_at_local": generated_at_line(now_iso, ws),
            "lane_counts": counts,
            "headline": {c["label"]: c["value"]
                         for c in (view.get("counters") or [])},
            "rows": sum(counts.values()),
        },
    }


def run_surface(surface: str, workspace_root, *, page: int = 1,
                page_size: int | None = None, now_iso: str | None = None,
                moves_rows: list | None = None,
                chase_rows: list | None = None,
                status_rows: list | None = None,
                personal_cap: int = _MP_PERSONAL_CAP,
                promised_cap: int = _MP_PROMISED_CAP,
                fired_via: str | None = None,
                pageset_ttl_minutes: int | None = None) -> dict:
    """Build the view + render_and_persist ONE page. Returns the transport
    dict (html / pagination / path). The CLI wraps this; tests call it
    directly.

    PAGESNAP — pages 2+ slice a SNAPSHOT, never a fresh read.

    This function used to rebuild the view from live substrate on EVERY call
    and hand `page` to a slicer that indexed the result. Page 2 was therefore
    a different query result than page 1, indexed against page 1's ordering:
    a write landing between the renders shifted every row after it, so the
    tail of page 1 reappeared atop page 2 (insert) or the rows that should
    have opened page 2 appeared on NO page at all (delete — silent, and the
    one that loses user-visible work). Observed live 2026-07-28.

    Now: page 1 builds live and FREEZES the view as the fire's page-set
    (`page_snapshot`); pages 2+ load that frozen view and slice the same list.
    Nothing is re-read between pages, so nothing shifts, and the reported
    total holds steady across the fire.

    A page-set older than `pageset_ttl_minutes` (default
    page_snapshot.DEFAULT_TTL_MINUTES), or missing/corrupt, is NOT silently
    re-read — the rebuild is announced on `transport["pagination"]` via
    `refreshed` / `refresh_reason` / `previous_total` so the surface can say
    the list changed under it. Rows applied since the snapshot are suppressed
    from later pages (`suppressed`), so an Apply on page 1 is reflected on
    page 2 without moving anything else.

    `fired_via` (scheduled | manual | catchup — the orchestrator's detected
    run mode, Phase 2.9 `receipt_fired_via`) makes the PAGE-1 invocation
    also write the surface's canonical per-fire receipt inside this same
    call (see _log_fire_receipt; the written/deduped outcome rides back on
    transport["receipt"]). Pages 2+ never receipt; omitting fired_via
    renders only (legacy callers unchanged). The snapshot path does NOT touch
    receipt behavior: the receipt still fires on page 1 only, still counts the
    live-built view it was always counting."""
    from page_snapshot import (DEFAULT_TTL_MINUTES, applied_ids_since,
                               load_pageset, save_pageset)
    from widget_transport import render_and_persist

    if fired_via is not None:
        from receipts import FIRED_VIA, normalize_fired_via
        if normalize_fired_via(fired_via) not in FIRED_VIA:
            raise ValueError(
                f"fired_via must be one of {sorted(FIRED_VIA)}; "
                f"got {fired_via!r}")

    ws = Path(workspace_root)
    if surface not in _SURFACE_NAME_HINTS:
        raise SystemExit(
            f"unknown surface {surface!r} "
            "(supported: commitments, staff-meeting, waiting-on, my-plate)")
    # MOUNT-FRESHNESS PREFLIGHT — the widget path's half of the same gate
    # `run_board` runs. This entry point is write-chained (a page-1 fire with
    # `fired_via` appends the surface's receipt), so a stale view here is the
    # stale-READ-becomes-clobbering-WRITE class, not only a stale render.
    refuse_if_mount_stale(ws)
    name_hint = _SURFACE_NAME_HINTS[surface]
    page = 1 if page is None else int(page)
    ttl = (DEFAULT_TTL_MINUTES if pageset_ttl_minutes is None
           else int(pageset_ttl_minutes))

    view = None
    suppress_ids: set = set()
    snap_note: dict = {}

    if page > 1:
        view, meta = load_pageset(ws, surface, ttl_minutes=ttl,
                                  now_iso=now_iso)
        if view is not None:
            # Rows the user applied since the snapshot froze. Derived from the
            # substrate's own audit events, so no caller has to remember to
            # register anything.
            suppress_ids = applied_ids_since(ws, meta.get("created_at"))
            snap_note = {"from_snapshot": True,
                         "snapshot_at": meta.get("created_at")}
        else:
            # NOT a silent re-read. Rebuild, start a fresh page-set so the
            # rest of this sequence is stable again, and SAY it refreshed.
            snap_note = {"refreshed": True,
                         "refresh_reason": meta.get("reason")}
            if meta.get("previous_total") is not None:
                snap_note["previous_total"] = meta["previous_total"]

    if view is None:
        watch_rows = None
        watch_pass = None
        if surface == "staff-meeting" and page == 1 and now_iso is None:
            # WATCHGATE MUST-FIX-2. Three conditions, and each one is load-
            # bearing:
            #
            #   PAGE 1 — pages 2+ read a frozen page-set, and running a WRITE
            #   pass to serve a paging request would close items the user is
            #   only scrolling past.
            #
            #   view is None — only when a view is actually being built, not
            #   on a snapshot read.
            #
            #   now_iso is None — A SIMULATED CLOCK MAY READ, IT MAY NEVER
            #   WRITE. This is the only write ever wired into `run_surface`,
            #   which was all reads before it, and it is the only clock in
            #   this call that does not float on wall time. Without this
            #   condition a render at `--now +30d` permanently closes parked
            #   items whose windows are weeks from expiring and records them
            #   as assumed-done — measured, not theorised (re-verify N-1).
            #   `--now` is a shipped CLI flag and this product is full of
            #   catch-up and backfill flows whose whole idiom is a simulated
            #   clock; nothing passes one HERE today, which is exactly why
            #   the guard belongs in code rather than in a comment telling
            #   the next person not to.
            watch_pass = run_watch_expiry_pass(ws)
            watch_rows = watch_pass.get("ask")
        view = _build_surface_view(
            surface, ws, now_iso=now_iso, moves_rows=moves_rows,
            chase_rows=chase_rows, status_rows=status_rows,
            personal_cap=personal_cap, promised_cap=promised_cap,
            watch_rows=watch_rows)
        live_view = view
        # STAFFCUT D2 — the builder's receipt arithmetic travels on the view
        # and is POPPED here, before `save_pageset` freezes it and before the
        # renderer sees it: the widget data contract gains nothing, the frozen
        # page-set stays byte-comparable to a pre-STAFFCUT one, and the receipt
        # still counts the live-built view it was always counting.
        receipt_extra = view.pop("receipt_extra", None) \
            if isinstance(view, dict) else None
        # N-5 — the expiry pass's own health, folded in. Nothing is added on a
        # healthy pass, so the receipt is unchanged when there is nothing to say.
        _watch_extra = watch_expiry_receipt_extra(watch_pass)
        if _watch_extra:
            receipt_extra = dict(receipt_extra or {})
            receipt_extra.update(_watch_extra)
        # Freeze this build as the page-set — on page 1 because that is the
        # fire's anchor, and on a refreshed page N so pages N+1... are stable
        # against the same list rather than drifting again.
        saved = save_pageset(ws, surface, view, now_iso=now_iso)
        if not saved.get("saved"):
            # A page-set we could not write is a page-set page 2 will miss and
            # announce. Never fatal: losing the snapshot must not cost the
            # user their page.
            snap_note["snapshot_unavailable"] = True
    else:
        live_view = None
        receipt_extra = None

    transport = render_and_persist(
        data_view=view,
        wrapper="fragment",
        persist_dir=ws / "_hq" / ".system" / "widgets",
        name_hint=name_hint,
        page=page,
        page_size=page_size,
        suppress_ids=suppress_ids or None,
    )
    if snap_note and transport.get("pagination") is not None:
        transport["pagination"].update(snap_note)
    if fired_via is not None and page == 1:
        transport["receipt"] = _log_fire_receipt(
            ws, surface, live_view if live_view is not None else view,
            fired_via, extra_data=receipt_extra)
    return transport


def main() -> int:
    # Review F-5: Windows pipes default to cp1252 — the output carries
    # non-ASCII (middots, warning glyphs) and would crash the CLI.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("surface",
                    choices=["commitments", "staff-meeting", "waiting-on",
                             "my-plate", "morning-brief"])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--mode", default="scheduled",
                    choices=["scheduled", "manual"],
                    help="morning-brief only: scheduled renders the confirm "
                         "card as a widget page; manual renders markdown "
                         "lines (t3 FB-9)")
    ap.add_argument("--format", dest="fmt", default="widget",
                    choices=["widget", "artifact"],
                    help="commitments only: `widget` (default, unchanged) "
                         "relays one paginated page to show_widget; "
                         "`artifact` serializes the SAME view as the "
                         "full-set, self-contained triage board for the "
                         "Artifact tool (SPEC_BOARD1). Never both in one "
                         "call — the board is a different question, not a "
                         "different page")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--page-size", type=int, default=None,
                    help="requested rows/page ceiling (the byte-fit may lower "
                         "it); default = chat_output_renderer.DEFAULT_PAGE_SIZE")
    ap.add_argument("--pageset-ttl-minutes", type=int, default=None,
                    help="how long this fire's page-set stays authoritative "
                         "before `show more` rebuilds and SAYS it refreshed "
                         "(default page_snapshot.DEFAULT_TTL_MINUTES)")
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--moves-json", default=None,
                    help="staff-meeting only: JSON file with the Phase-4 "
                         "moves rows (email-shaped item dicts)")
    ap.add_argument("--chase-json", default=None,
                    help="waiting-on only: JSON file with the pre-staged chase "
                         "rows (email-shaped item dicts, connector-dependent)")
    ap.add_argument("--status-json", default=None,
                    help="my-plate only: JSON file with the pre-staged Promised "
                         "status-draft rows (email-shaped item dicts, "
                         "connector-dependent — the chase_rows parallel)")
    ap.add_argument("--personal-cap", type=int, default=_MP_PERSONAL_CAP,
                    help="my-plate only: Personal-group row cap (CTS1 §4.2 "
                         "`personal_cap`, default 7); 'show my plate' passes a "
                         "large value to lift the cap")
    ap.add_argument("--promised-cap", type=int, default=_MP_PROMISED_CAP,
                    help="my-plate only: Promised-group row cap (BUG-8330 "
                         "item 5); the footer names what's held back")
    ap.add_argument("--fired-via", default=None,
                    choices=["scheduled", "manual", "catchup"],
                    help="the fire's run mode (the orchestrator's Phase-2.9 "
                         "receipt_fired_via); when given, the page-1 "
                         "invocation also writes the surface's canonical "
                         "per-fire receipt inside this call (FB-7)")
    args = ap.parse_args()

    try:
        return _dispatch(args)
    except MountStaleError as stale:
        # Exit nonzero having written nothing. The message is the existing
        # syncing vocabulary (substrate_alarm_lines); the durable alert was
        # already rendered through alarm_artifacts by the preflight itself.
        for line in (stale.lines or [str(stale)]):
            print(line, file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    if args.surface == "morning-brief":
        # t3 FB-9 — ONE call, every mandatory block. The orchestrator places
        # each emitted block; the CR-BRIEF-PACK line is the checklist.
        # FB-20: this surface emits PROSE ONLY — no widget block, no relay
        # banner, nothing to post to show_widget. The brief is read-only by
        # construction. (The banner + CR-WIDGET-HTML markers below this
        # branch still serve commitments / staff-meeting unchanged.)
        pack = build_morning_brief_pack(args.workspace, mode=args.mode,
                                        now_iso=args.now)
        print("CR-BRIEF-PACK: " + json.dumps(pack, ensure_ascii=False))
        return 0

    if args.fmt == "artifact":
        # SPEC_BOARD1 — the board. Its own markers on purpose: the widget
        # contract says "relay the bytes between CR-WIDGET-HTML-* to
        # show_widget byte-exact", and these bytes go to the Artifact tool
        # instead. Reusing those markers would invite exactly the wrong relay.
        if args.surface != "commitments":
            raise SystemExit(
                "--format artifact is the commitments surface only "
                f"(got {args.surface!r})")
        result = run_board(args.workspace, now_iso=args.now)
        meta = dict(result["board"])
        meta["path"] = str(result["path"])
        meta["file_uri"] = result["file_uri"]
        print("CR-BOARD: " + json.dumps(meta, ensure_ascii=False))
        print("CR-BOARD-HTML-BEGIN")
        print(result["html"])
        print("CR-BOARD-HTML-END")
        return 0

    moves_rows = None
    if args.moves_json:
        moves_rows = json.loads(Path(args.moves_json).read_text(encoding="utf-8"))
    chase_rows = None
    if args.chase_json:
        chase_rows = json.loads(Path(args.chase_json).read_text(encoding="utf-8"))
    status_rows = None
    if args.status_json:
        status_rows = json.loads(Path(args.status_json).read_text(encoding="utf-8"))

    transport = run_surface(
        args.surface, args.workspace, page=args.page,
        page_size=args.page_size, now_iso=args.now, moves_rows=moves_rows,
        chase_rows=chase_rows, status_rows=status_rows,
        personal_cap=args.personal_cap,
        promised_cap=getattr(args, "promised_cap", _MP_PROMISED_CAP),
        fired_via=args.fired_via,
        pageset_ttl_minutes=args.pageset_ttl_minutes)

    pagination = transport.get("pagination") or {}
    print("CR-PAGINATION: " + json.dumps(pagination))
    print("CR-WIDGET-HTML-BEGIN")
    print(transport["html"])
    print("CR-WIDGET-HTML-END")
    receipt = transport.get("receipt")
    if receipt is not None:
        print("CR-RECEIPT: " + json.dumps(receipt))
    return 0


__all__ = [
    "MountStaleError",
    "build_commitment_triage_view",
    "build_morning_brief_pack",
    "build_my_plate_view",
    "build_staff_meeting_view",
    "build_waiting_on_view",
    "refuse_if_mount_stale",
    "run_board",
    "run_surface",
]


if __name__ == "__main__":
    raise SystemExit(main())
