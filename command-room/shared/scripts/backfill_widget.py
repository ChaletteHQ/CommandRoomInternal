#!/usr/bin/env python3
"""BACKFILL2 — R3b: the binding backfill as a one-tap Cowork widget.

BACKFILL1 (`backfill_bindings.py`) is the ENGINE and is not touched here: it
derives the candidate rows (the gauge's own walk), refuses name-only and
contested rows, writes one `reclassification` per accepted row in one
`bkf_` batch, records every rejection with its evidence tier in the
`binding_backfill_run` receipt, rebuilds the gauge on apply, and reverses
through `brain_undo`. This module is the SURFACE: it turns the propose
report into a `render_and_persist` data view (rows grouped by project,
three verbs per row — `bind` / `not this project` / `skip`), and turns the
apply-choices tuples that come back into ONE `backfill_bindings.apply` call.

THE NO-AUTO FENCE, RESTATED FOR THE WIDGET PATH. Nothing here decides
anything. A row reaches `apply` ONLY as an explicit `bind` or
`not this project` tuple the user tapped; `skip` writes a one-day mute and
adjudicates nothing; an unknown verb on a binding row is refused, never
coerced into a bind. "Apply all" applies the taps, not the page.

THE SNAPSHOT FENCE RIDES THE PAYLOAD. The wire id of every row is
`bind:<seq>:<snapshot_max_seq>` — the propose snapshot the row was derived
from is baked into the id the button carries, so a click that lands hours
later (Cowork chats are persistent) hands `apply` the snapshot the user
actually READ, and `backfill_bindings.apply` refuses if the substrate has
moved past it (its own exact-match fence). Rows from two different renders
in one batch are refused wholesale — one adjudication, one snapshot.

DISPATCH HOME. The rows render with `source_skill: "needs-your-call"` (the
skill that owns "the operator adjudicates rows") and a `bind:` wire-id
prefix, exactly as the `pcand:` person-candidate rows do on the same source
— apply-choices routes on the prefix, and everything else on that source is
untouched.

PRECISION DATASET. `bind` → an accepted seq; `not this project` → a rejected
seq. Both reach `backfill_bindings.apply`, whose receipt records each with
its evidence tier — the same record the CLI sitting produces. The widget
adds no second ledger.

stdlib + sibling shared/scripts modules only.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import backfill_bindings as bb  # noqa: E402

SOURCE_SKILL = "needs-your-call"
WIRE_PREFIX = "bind:"
# The mute ledger's `surface` / `item_class` for a skipped row — what
# `show muted` shows and what the upstream filter in `render_backfill_page`
# reads back through `active_dismissal_target_ids`.
MUTE_SURFACE = "binding-review"
MUTE_ITEM_CLASS = "binding_candidate"

BIND_ACTION = "bind"
REJECT_ACTION = "not this project"
SKIP_ACTION = "skip"
# ONE list, read by the data-view builder — the same discipline as
# `needs_review_queue.QUEUE_ROW_ACTIONS`.
ROW_ACTIONS = [BIND_ACTION, REJECT_ACTION, SKIP_ACTION]

_WIRE_RE = re.compile(r"^bind:(\d+):(\d+)$")

# Plain words for the engine's corroborator names — the evidence the user
# weighs. Never the machine tokens.
_EVIDENCE_WORDS = {
    "person_overlap": "same people",
    "org_link": "same organization",
    "source_ref_adjacency": "same meeting or thread",
    "temporal_cluster": "same week as its other work",
}


def wire_id(seq: int, snapshot_max_seq: int) -> str:
    return f"{WIRE_PREFIX}{int(seq)}:{int(snapshot_max_seq)}"


def parse_wire_id(n) -> Optional[tuple]:
    """`(seq, snapshot_max_seq)` for a binding row's wire id, None for
    anything else (a different source's id, or a malformed one)."""
    m = _WIRE_RE.match(str(n or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def mute_target(seq: int) -> str:
    """The mute ledger's `target_id` for a skipped row: `bind:<seq>` — the
    record's stable identity WITHOUT the snapshot. Every append (a skip's own
    dismissal included) advances the substrate's high-water mark, so a mute
    keyed on the full wire id would stop matching the very next render."""
    return f"{WIRE_PREFIX}{int(seq)}"


# ---------------------------------------------------------------------------
# Row shaping (the ONLY place binding rows are shaped for a widget)
# ---------------------------------------------------------------------------


def _date_words(ts: str) -> str:
    if not ts:
        return "undated"
    try:
        d = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _kind_words(event_type: str) -> str:
    t = (event_type or "").replace("_", " ").strip()
    return t or "record"


def _evidence_words(row: dict) -> str:
    corr = [_EVIDENCE_WORDS.get(c, c.replace("_", " "))
            for c in row.get("corroborators") or []]
    if not corr:
        return "evidence: the name alone"
    return "evidence: the name + " + ", ".join(corr)


def _binding_words(row: dict, name_by_tid: dict) -> str:
    refs = row.get("current_refs") or []
    if not refs:
        return "not filed under any project yet"
    names = [name_by_tid.get(t, "another project") for t in refs]
    return ("already filed under " + ", ".join(names)
            + " — binding ADDS this project alongside")


def _k_line(t: dict) -> str:
    """The per-project footer: what this project's taps buy (spec §M.2 —
    k-to-READY, in words). Never the gauge's own vocabulary."""
    k = int(t.get("k_to_ready") or 0)
    n = int(t.get("proposable_rows") or 0)
    if k == 0:
        return "This project's history already holds up on its own."
    if t.get("ready_reachable"):
        noun = "binding" if k == 1 else "bindings"
        return (f"{k} more confirmed {noun} and this project's history "
                f"holds up on its own — {n} on offer here.")
    return (f"Binding alone can't get this project there ({k} needed, {n} "
            f"on offer) — each bind still makes its history truer.")


def _row_item(row: dict, snapshot: int, display_n: int,
              name_by_tid: dict) -> dict:
    preview = (row.get("preview") or "").strip()
    kind = _kind_words(row.get("event_type") or "")
    name = preview or f"a {kind} with no readable text"
    term = row.get("matched_term") or ""
    tag = " · ".join(b for b in (
        _date_words(row.get("ts") or ""),
        kind,
        f"names “{term}”" if term else "",
        _evidence_words(row),
        _binding_words(row, name_by_tid),
    ) if b)
    return {
        "n": wire_id(row["seq"], snapshot),   # wire id — snapshot rides it
        "display_n": display_n,
        "name": name,
        "context_tag": tag,
        "actions": list(ROW_ACTIONS),
    }


def _thread_names(workspace_root) -> dict:
    """thread id -> display name off entities.json, for the "already filed
    under …" words on bound-elsewhere rows (the report names only in-scope
    threads). Any failure degrades to an empty map."""
    try:
        import json

        from entities_io import entities_collection, unwrap_entities

        doc = json.loads((Path(workspace_root) / "_hq" / "data"
                          / "entities.json").read_text(encoding="utf-8"))
        ent = unwrap_entities(doc) if isinstance(doc, dict) else {}
        return {t["id"]: str(t.get("canonical_name") or t.get("display_name")
                              or t["id"])
                for t in entities_collection(ent, "threads")
                if isinstance(t, dict) and t.get("id")}
    except Exception:  # pragma: no cover — names are a nicety, never a gate
        return {}


def build_backfill_data_view(report: dict, *, muted_ids: Iterable[str] = (),
                             header: str | None = None,
                             thread_names: dict | None = None) -> dict:
    """The propose report as a `render_and_persist` data view — one SECTION
    per project, rows numbered across the page, the k-to-READY line as the
    section footer. `muted_ids` (live `skip` mutes, read upstream by the
    caller) are dropped BEFORE shaping — the mute filter runs in the view
    build, never in the transport."""
    snapshot = int(report["snapshot_max_seq"])
    muted = {str(m) for m in muted_ids}
    name_by_tid = dict(thread_names or {})
    name_by_tid.update({t["thread_id"]: t["thread_name"]
                        for t in report.get("per_thread") or []})
    for r in report.get("rows") or []:
        name_by_tid.setdefault(r["thread_id"], r.get("thread_name") or "")
        for ref in r.get("current_refs") or []:
            name_by_tid.setdefault(ref, "another project")

    rows_by_tid: dict = {}
    for r in report.get("rows") or []:
        if mute_target(r["seq"]) in muted:
            continue
        rows_by_tid.setdefault(r["thread_id"], []).append(r)

    sections = []
    display_n = 0
    for t in report.get("per_thread") or []:
        rows = rows_by_tid.get(t["thread_id"]) or []
        if not rows:
            continue
        items = []
        for r in rows:
            display_n += 1
            items.append(_row_item(r, snapshot, display_n, name_by_tid))
        sections.append({
            "title": t["thread_name"],
            "count": len(items),
            "items": items,
            "footer_note": _k_line(t),
        })
    n_rows = display_n
    default_header = (
        f"Project bindings — {n_rows} past "
        f"{'record names' if n_rows == 1 else 'records name'} a project "
        "they were never filed under. Bind it, or say it's not that "
        "project; nothing moves until you tap.")
    return {
        "source_skill": SOURCE_SKILL,
        "header": header or default_header,
        "sections": sections,
    }


def _live_muted_ids(workspace_root, now_iso: Optional[str]) -> set:
    """Wire ids with a live `skip` mute — the standing liveness filter,
    scoped to this lane's prefix. Any failure degrades to no filter."""
    try:
        import events_io
        from mute_ledger import active_dismissal_target_ids

        events, _skipped = events_io.load_events_owner_scoped(workspace_root)
        now = now_iso or _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return {t for t in active_dismissal_target_ids(events, now)
                if str(t).startswith(WIRE_PREFIX)}
    except Exception:  # pragma: no cover — the page must still render
        return set()


def render_backfill_page(workspace_root, *, page: int = 1, persist_dir=None,
                         now_iso: Optional[str] = None, extra_thread_ids=(),
                         max_rows: Optional[int] = None) -> dict:
    """Propose (read-only), drop live-muted rows, shape, page by WHOLE
    project, render ONE page through the canonical transport. The skill's
    single call.

    Returns the transport dict plus `group_pagination`, `report` (the
    propose report — `snapshot_max_seq` is what every row's wire id
    carries), `n_rows`, and `empty` (True with a one-line `line` when there
    is nothing to adjudicate — nothing is rendered then)."""
    from needs_review_queue import paginate_groups
    from widget_transport import render_and_persist

    ws = Path(workspace_root)
    report = bb.propose(ws, extra_thread_ids=extra_thread_ids, now_iso=now_iso)
    muted = _live_muted_ids(ws, now_iso)
    data_view = build_backfill_data_view(report, muted_ids=muted,
                                         thread_names=_thread_names(ws))
    n_rows = sum(len(s["items"]) for s in data_view["sections"])
    if not n_rows:
        r = report["refused"]
        held = sum(1 for x in (report.get("rows") or [])
                   if mute_target(x["seq"]) in muted)
        line = ("Nothing to file — every past record that names a project is "
                "either already filed under it or too thin to propose")
        if held:
            line += f" ({held} snoozed until tomorrow)"
        line += "."
        return {"empty": True, "line": line, "report": report, "n_rows": 0,
                "refused": r}
    page_view = paginate_groups(data_view, page=page, max_rows=max_rows)
    gp = page_view.pop("group_pagination")
    rows = max(1, gp["rows_on_page"])
    transport = render_and_persist(
        data_view=page_view, wrapper="fragment",
        persist_dir=str(persist_dir or (ws / "_hq" / ".system" / "widgets")),
        name_hint="binding-review", page=1, page_size=rows)
    fitted = (transport.get("pagination") or {}).get("total_pages") or 1
    if fitted > 1:
        gp = dict(gp)
        gp["group_split_by_budget"] = True
    transport["group_pagination"] = gp
    transport["report"] = report
    transport["n_rows"] = n_rows
    transport["empty"] = False
    return transport


# ---------------------------------------------------------------------------
# Apply — the rail's one call for `bind:` rows
# ---------------------------------------------------------------------------


def skip_rows(workspace_root, wire_ids: Iterable[str], *,
              source_skill: str = SOURCE_SKILL,
              now_iso: Optional[str] = None) -> list:
    """`skip` on binding rows: one standard `chat_dismissal` per wire id —
    1 day (the taxonomy's TTL for `skip`, the number the button states),
    `data.target_id` = the wire id verbatim (what the upstream filter reads
    back), the Loop-2 fingerprint stamped. Adjudicates NOTHING: the row is
    not accepted, not rejected, not recorded in the precision dataset — it
    simply stops rendering until tomorrow."""
    from event_gate import append_event
    from surface_preferences import dismissal_fingerprint
    from verb_taxonomy import mute_ttl_days

    parsed = [(str(w), parse_wire_id(w)) for w in wire_ids]
    ids = [(w, p[0]) for w, p in parsed if p]
    if not ids:
        return []
    ttl = mute_ttl_days(SKIP_ACTION) or 1
    if now_iso:
        now = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
    else:
        now = _dt.datetime.now(_dt.timezone.utc)
    until = (now + _dt.timedelta(days=int(ttl))).strftime("%Y-%m-%dT%H:%M:%SZ")
    fp = dismissal_fingerprint(MUTE_SURFACE, MUTE_ITEM_CLASS, None)
    events = [{
        "type": "chat_dismissal",
        "source_skill": source_skill,
        "primary_thread_id": "",
        "data": {
            "target_id": mute_target(seq),
            "surface": MUTE_SURFACE,
            "item_class": MUTE_ITEM_CLASS,
            "fingerprint": fp,
            "snooze_until": until,
            "via": "backfill_widget.skip_rows",
        },
    } for _w, seq in ids]
    append_event(Path(workspace_root) / "_hq" / "data" / "events.jsonl",
                 events, holder=source_skill)
    return [{"n": w, "action": SKIP_ACTION, "status": "dismissed",
             "target_id": mute_target(seq), "snooze_until": until}
            for w, seq in ids]


def apply_choices(workspace_root, choices: list, *, applied_by: str,
                  now_iso: Optional[str] = None,
                  source_skill: str = SOURCE_SKILL) -> dict:
    """Dispatch the `bind:` tuples of one Apply — the rail's ONE call.

    `choices` = the apply-choices tuples (`{n, action, ...}`); tuples whose
    `n` is not a binding wire id are ignored here (they belong to another
    handler on the same source). `applied_by` = the operator identity the
    rail already resolved (`primary_user.resolve_primary_user`); an EMPTY
    one refuses the whole call — an unattributed adjudication is not a
    precision record.

      bind              -> accepted seq   (one `bkf_` batch per Apply)
      not this project  -> rejected seq   (recorded with its tier, never
                                           re-proposed)
      skip              -> one-day mute, nothing adjudicated
      anything else     -> refused for that row, nothing written

    All bind / not-this-project rows in one Apply must carry the SAME
    snapshot (one render = one adjudication); `backfill_bindings.apply`
    then refuses if the substrate moved past it. Returns
    {status, results:[{n, action, status, ...}], batch_id, batch_ref,
    n_applied, n_rejected, n_skipped, n_refused, receipt, summary} — each
    `results` entry is that tuple's `handler_result` for the audit event."""
    results: list = []
    accepts: dict = {}
    rejects: dict = {}
    skips: list = []
    snapshots: set = set()

    for ch in choices or []:
        if not isinstance(ch, dict):
            continue
        n = ch.get("n")
        parsed = parse_wire_id(n)
        if parsed is None:
            continue
        seq, snap = parsed
        action = str(ch.get("action") or "").strip().lower()
        if action == BIND_ACTION:
            accepts[seq] = str(n)
            snapshots.add(snap)
        elif action == REJECT_ACTION:
            rejects[seq] = str(n)
            snapshots.add(snap)
        elif action == SKIP_ACTION:
            skips.append(str(n))
        else:
            # The no-auto fence's dispatch half: an unfamiliar verb on a
            # binding row never becomes a bind by default.
            results.append({"n": str(n), "action": action, "status": "refused",
                            "detail": "not a verb this row offers — nothing "
                                      "was written for it"})

    if not accepts and not rejects and not skips:
        # Refusals only (unfamiliar verbs) → the batch IS refused; no
        # binding rows at all → an honest no-op. Nothing written either way.
        return {"status": "refused" if results else "noop",
                "results": results, "batch_id": None,
                "batch_ref": None, "n_applied": 0, "n_rejected": 0,
                "n_skipped": 0, "n_refused": len(results),
                "summary": ("Those aren't answers this list takes — nothing "
                            "was written." if results else
                            "No binding rows in this batch — nothing "
                            "written.")}

    if (accepts or rejects) and not (applied_by or "").strip():
        for seq, n in list(accepts.items()) + list(rejects.items()):
            results.append({
                "n": n, "action": BIND_ACTION if seq in accepts
                else REJECT_ACTION, "status": "refused",
                "detail": "no operator identity on file — an unattributed "
                          "adjudication is not a precision record"})
        return {"status": "refused", "results": results, "batch_id": None,
                "batch_ref": None, "n_applied": 0, "n_rejected": 0,
                "n_skipped": 0, "n_refused": len(results),
                "summary": ("I can't record who decided these until this "
                            "workspace knows who you are — nothing was "
                            "written. Snoozing still works.")}

    if len(snapshots) > 1:
        for seq, n in list(accepts.items()) + list(rejects.items()):
            results.append({
                "n": n, "action": BIND_ACTION if seq in accepts
                else REJECT_ACTION, "status": "refused",
                "detail": "rows from two different renders in one batch — "
                          "one adjudication, one snapshot"})
        return {"status": "refused", "results": results, "batch_id": None,
                "batch_ref": None, "n_applied": 0, "n_rejected": 0,
                "n_skipped": 0, "n_refused": len(results),
                "summary": ("Those rows came from two different pages of "
                            "this list — say `review project bindings` for "
                            "a fresh one and answer from that. Nothing was "
                            "written.")}

    overlap = sorted(set(accepts) & set(rejects))
    if overlap:
        for seq in overlap:
            results.append({"n": accepts[seq], "action": BIND_ACTION,
                            "status": "refused",
                            "detail": "the same row was both bound and "
                                      "refused in one batch"})
            accepts.pop(seq, None)
            rejects.pop(seq, None)

    # ORDER MATTERS: the engine's apply runs FIRST. A skip's own dismissal is
    # an append, and every append advances the substrate's high-water mark —
    # writing the mutes first would make the same Apply's binds read as
    # stale against the snapshot the user just tapped on.
    batch_id = None
    batch_ref = None
    receipt = None
    n_applied = n_rejected = 0
    engine_status = None
    if accepts or rejects:
        (snapshot,) = tuple(snapshots)
        res = bb.apply(workspace_root,
                       accept_seqs=sorted(accepts), reject_seqs=sorted(rejects),
                       snapshot_max_seq=snapshot, applied_by=applied_by,
                       source_skill=source_skill, now_iso=now_iso)
        engine_status = res.get("status")
        if engine_status == "stale_snapshot":
            for seq, n in list(accepts.items()) + list(rejects.items()):
                results.append({
                    "n": n, "action": BIND_ACTION if seq in accepts
                    else REJECT_ACTION, "status": "stale_snapshot",
                    "detail": res.get("summary")})
        else:
            applied_seqs = {r["seq"] for r in
                            (res.get("receipt") or {}).get("applied_rows") or []}
            rejected_seqs = {r["seq"] for r in
                             (res.get("receipt") or {}).get("rejected") or []}
            for seq, n in accepts.items():
                results.append({"n": n, "action": BIND_ACTION,
                                "status": "applied" if seq in applied_seqs
                                else "unchanged"})
            for seq, n in rejects.items():
                results.append({"n": n, "action": REJECT_ACTION,
                                "status": "recorded" if seq in rejected_seqs
                                else "unchanged"})
            batch_id = res.get("batch_id")
            batch_ref = res.get("batch_ref")
            receipt = res.get("receipt")
            n_applied = int(res.get("n_applied") or 0)
            n_rejected = int(res.get("n_rejected") or 0)

    # skip — a mute, not an adjudication; written AFTER the engine's apply.
    skip_results = skip_rows(workspace_root, skips, source_skill=source_skill,
                             now_iso=now_iso) if skips else []
    results.extend(skip_results)

    n_refused = sum(1 for r in results if r["status"] == "refused")
    n_skipped = len(skip_results)
    if engine_status == "stale_snapshot":
        status = "stale_snapshot"
        summary = ("This list is out of date — the workspace changed since "
                   "it was drawn. Say `review project bindings` for a fresh "
                   "one; nothing was written.")
    elif n_applied:
        status = "applied"
        summary = _summary(n_applied, n_rejected, n_skipped, n_refused,
                           receipt, batch_id)
    elif n_rejected:
        status = "recorded"
        summary = _summary(0, n_rejected, n_skipped, n_refused, receipt, None)
    elif n_skipped:
        status = "dismissed"
        summary = _summary(0, 0, n_skipped, n_refused, None, None)
    else:
        status = "refused" if n_refused else "unchanged"
        summary = _summary(0, 0, 0, n_refused, None, None)
    return {
        "status": status,
        "results": results,
        "batch_id": batch_id,
        "batch_ref": batch_ref,
        "n_applied": n_applied,
        "n_rejected": n_rejected,
        "n_skipped": n_skipped,
        "n_refused": n_refused,
        "receipt": receipt,
        "summary": summary,
    }


def _summary(n_applied, n_rejected, n_skipped, n_refused, receipt,
             batch_id) -> str:
    bits = []
    if n_applied:
        bits.append(f"Filed {n_applied} under "
                    f"{'its' if n_applied == 1 else 'their'} project")
    if n_rejected:
        bits.append(f"marked {n_rejected} as not that project (I won't "
                    f"suggest {'it' if n_rejected == 1 else 'them'} again)")
    if n_skipped:
        bits.append(f"snoozed {n_skipped} until tomorrow")
    if n_refused:
        bits.append(f"left {n_refused} untouched")
    s = "; ".join(bits) if bits else "Nothing changed"
    s = s[0].upper() + s[1:] + "."
    if receipt and n_applied:
        before, after = receipt.get("ready_before"), receipt.get("ready_after")
        if after is not None and before is not None and after != before:
            s += (f" {after - before} more "
                  f"{'project' if after - before == 1 else 'projects'} now "
                  "have a history that holds up on its own.")
    if batch_id:
        s += " Say `undo` to reverse the sitting."
    return s


def undo_sitting(workspace_root, batch_id: str, *, undone_by: str,
                 source_skill: str = SOURCE_SKILL) -> dict:
    """Reverse one Apply's `bkf_` batch through the standing undo lane and
    rebuild the gauge once — `backfill_bindings.undo`, unchanged."""
    return bb.undo(workspace_root, batch_id, undone_by=undone_by,
                   source_skill=source_skill)


__all__ = [
    "SOURCE_SKILL",
    "WIRE_PREFIX",
    "MUTE_SURFACE",
    "MUTE_ITEM_CLASS",
    "BIND_ACTION",
    "REJECT_ACTION",
    "SKIP_ACTION",
    "ROW_ACTIONS",
    "wire_id",
    "parse_wire_id",
    "build_backfill_data_view",
    "render_backfill_page",
    "skip_rows",
    "apply_choices",
    "undo_sitting",
]
