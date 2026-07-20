#!/usr/bin/env python3
"""
append_event() gatekeeper (Phase 1 Foundation, F1 — 2026-07).

Built as an EXTENSION of atomic_write.atomic_append_jsonl, never a second
append path: the gate logic below is invoked INSIDE atomic_append_jsonl's
events.jsonl branch, so every existing caller — skill prose templates,
shared/scripts writers, orchestrators — is gated from day one without
migrating a single call site. `append_event()` is the canonical named entry
for new code; it applies the same gate in STRICT mode (unknown event types
reject instead of warn) and then delegates the actual locked write to
atomic_append_jsonl. seq allocation and ts stamping already live inside
atomic_append_jsonl's writer-lock critical section (v3.13.8.3 / SPEC A1);
this module adds the identity + validation layer on top:

  1. Type normalization — known drift is corrected on append
     (`commitment_update` → `commitment_updated`).
  2. Commitment identity — `type: commitment` events get `data.id` minted as
     `cmt_<ULID>` at write time when the caller didn't set one (ratified
     2026-07-01: cmt_<ulid>, minted at write time). Ids are written, never
     synthesized-only.
  3. Commitment kind — `data.kind` (promise | task | scheduling | agenda) is
     REQUIRED AT CAPTURE on commitment events (ratified 2026-07-01; flipped
     from stamp-promise in Phase 2 Stage D once the producers classified at
     write time). A provided kind is validated against the enum (bad value =
     reject). A MISSING kind REJECTS on both entries (Phase 4 2026-07-02 —
     the F1 burn-in ended with the Phase 1-3 writer migrations). READ-side, a
     historic event with no kind is a `promise` forever
     (commitment_state.commitment_kind — never backfilled on disk).
  4. Fail-loud closure identity — `type: commitment_resolved` with no
     readable id field is REJECTED (EventGateError). This exact silent
     failure produced 291 dead-letter closures in the live substrate.
  5. Reminder-lane hard rules (v4.6.0 W4a) — `reminder` / `reminder_updated` /
     `reminder_cleared` events REQUIRE `data.origin == "user_explicit"`
     (unconditional reject on anything else: no skill, sweep, or scheduled
     task may ever mint a reminder — reminders exist because the USER said
     "remind me"). `reminder` gets `data.id` minted as `rem_<ULID>` when the
     caller didn't set one; `reminder_updated` / `reminder_cleared` without a
     readable `data.reminder_id` are dead letters and reject. Builders live in
     shared/scripts/reminders.py.
  6. Schema-enum validation for ALL event families — the type must be
     registered in shared/data-schemas/events.schema.json (see
     event_types.py for the enum-home decision). An unknown type raises on
     BOTH entries — append_event() and the legacy atomic_append_jsonl path —
     as of Phase 4 (2026-07-02). Register new types per shared/EVENT_TYPES.md
     before writing them.

Escape hatch: CR_EVENT_GATE=0 disables the gate entirely (emergencies only —
e.g. replaying a quarantined batch that predates the contract). The write
path itself (lock, seq, ts, atomic rename) is unaffected by the env var.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, List

try:
    from event_types import (
        COMMITMENT_CLOSURE_ID_FIELDS,
        KIND_VALUES,
        LEGACY_SEQ_ID_RE,
        is_known_type,
    )
except ImportError:
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from event_types import (
        COMMITMENT_CLOSURE_ID_FIELDS,
        KIND_VALUES,
        LEGACY_SEQ_ID_RE,
        is_known_type,
    )


class EventGateError(ValueError):
    """An event was rejected at append time. Fail loud, not silent — silent
    success is the disease (291 id-less closures, 5 spellings of one type)."""


# Known type drift corrected on append. Extend here (with a CHANGELOG note)
# when a new drift spelling is discovered — readers never learn drift names.
TYPE_NORMALIZATION = {
    "commitment_update": "commitment_updated",
}

_KIND_DEFAULT = "promise"

# Reminder lane (v4.6.0 W4a). Reminders are user-minted pins, NOT commitments —
# they never enter buckets, counts, chase, or triage, and ONLY the user may
# create one. The gate enforces both identities unconditionally (like the
# id-less-closure rule): origin must be the literal below, and mutation events
# must reference the reminder they touch.
REMINDER_TYPES = frozenset({"reminder", "reminder_updated", "reminder_cleared"})
REMINDER_ORIGIN = "user_explicit"

# Crockford base32 (no I, L, O, U) — standard ULID alphabet.
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    """26-char ULID: 48-bit ms timestamp + 80 bits of randomness, Crockford
    base32. Stdlib-only (no dependency); lexically sortable by mint time."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts_ms << 80) | rand
    chars = []
    for shift in range(125, -1, -5):
        chars.append(_ULID_ALPHABET[(value >> shift) & 0x1F])
    return "".join(chars)


def new_commitment_id() -> str:
    """Mint a canonical commitment id: cmt_<ULID> (ratified 2026-07-01)."""
    return "cmt_" + _ulid()


def new_reminder_id() -> str:
    """Mint a canonical reminder id: rem_<ULID> (v4.6.0 W4a — same alphabet,
    distinct prefix so a reminder id can never be mistaken for a commitment)."""
    return "rem_" + _ulid()


def _gate_enabled() -> bool:
    return os.environ.get("CR_EVENT_GATE", "1") != "0"


def _has_closure_id(ev: dict) -> bool:
    data = ev.get("data")
    data = data if isinstance(data, dict) else {}
    for field in COMMITMENT_CLOSURE_ID_FIELDS:
        if data.get(field) not in (None, ""):
            return True
    # Legacy flat shape — a top-level id is still readable by cru_match.
    for field in ("commitment_id", "id"):
        if ev.get(field) not in (None, ""):
            return True
    return False


def gate_events(
    events: List[dict],
    *,
    strict_enum: bool = True,
    holder: str = "event_gate",
) -> List[dict]:
    """Validate + enrich a batch of events bound for events.jsonl.

    Returns NEW event dicts (callers' dicts are never mutated — same contract
    as atomic_append_jsonl's stamp step). Raises EventGateError on the
    fail-loud conditions documented in the module docstring. strict_enum
    defaults to True (Phase 4 2026-07-02); passing False restores the F1
    burn-in warn-only posture and exists for controlled replay tooling only —
    no production writer may pass it.
    """
    if not _gate_enabled():
        return events

    out: List[dict] = []
    for i, ev in enumerate(events):
        ev = {**ev}

        # 1. Normalize known type drift.
        etype = ev.get("type")
        if etype in TYPE_NORMALIZATION:
            etype = TYPE_NORMALIZATION[etype]
            ev["type"] = etype

        # 6. Schema-enum validation — every family, from day one.
        if not isinstance(etype, str) or not etype:
            msg = f"event {i} has no 'type' (holder={holder})"
            if strict_enum:
                raise EventGateError(msg)
            sys.stderr.write(f"[event_gate] {msg}\n")
        elif not is_known_type(etype):
            msg = (
                f"event type '{etype}' is not registered in "
                f"events.schema.json (holder={holder}) — register it per "
                f"shared/EVENT_TYPES.md before writing it"
            )
            if strict_enum:
                raise EventGateError(msg)
            sys.stderr.write(f"[event_gate] {msg}\n")

        # 2 + 3. Commitment identity + kind.
        if etype == "commitment":
            data = ev.get("data")
            data = {**data} if isinstance(data, dict) else {}
            if data.get("id") in (None, "") or (
                isinstance(data.get("id"), str) and not data["id"].strip()
            ):
                data["id"] = new_commitment_id()
            else:
                # v4.5.2 R1c — explicit ids must ROUND-TRIP through
                # normalize_commitment_id (custom-string ids like
                # commit_navid_… are legitimate; the failure modes aren't):
                #   - non-string ids never match the read-side exact-id index;
                #   - leading/trailing whitespace breaks the strip()-then-look-
                #     up on close (trimmed here, not rejected);
                #   - an id shaped like a legacy seq alias (bare digits,
                #     seq_N, event_N, commitment_seq_N) SHADOWS seq
                #     references — a closure meaning "the event at seq 86"
                #     would resolve to this commitment instead. Rejected.
                raw_id = data["id"]
                if not isinstance(raw_id, str):
                    raise EventGateError(
                        f"commitment event has a non-string data.id "
                        f"{raw_id!r} (holder={holder}) — an explicit id must "
                        "be a string that resolves back via "
                        "normalize_commitment_id. Pass a distinct string id "
                        "or omit data.id to have a cmt_<ulid> minted."
                    )
                trimmed = raw_id.strip()
                if LEGACY_SEQ_ID_RE.match(trimmed):
                    raise EventGateError(
                        f"commitment event data.id {raw_id!r} collides with "
                        f"the legacy seq-alias namespace (bare digits / "
                        f"seq_N / event_N / commitment_seq_N; holder="
                        f"{holder}). Closures referencing that spelling mean "
                        "'the commitment at seq N' — this id would shadow "
                        "them and closures would land on the wrong "
                        "commitment. Use a distinct string id (e.g. "
                        "cmt_<ulid> or commit_<name>_<date>_<n>) or omit "
                        "data.id to have one minted."
                    )
                data["id"] = trimmed
            # SUB1 § 3 — parent_id sanity (the referential checks the gate
            # CAN do without reads; existence/depth live in the writer,
            # commitment_state.add_subitems, like every closer):
            #   - non-string never matches the read-side exact-id index;
            #   - self-parent is incoherent;
            #   - a seq-alias-shaped parent_id (bare digits / seq_N / event_N
            #     / commitment_seq_N) is the R1c shadowing class — the writer
            #     stamps the parent's canonical data.id VERBATIM, so any
            #     alias spelling here is a hand-built append.
            if "parent_id" in data:
                pid = data.get("parent_id")
                if not isinstance(pid, str) or not pid.strip():
                    raise EventGateError(
                        f"commitment event has a non-string/empty "
                        f"data.parent_id {pid!r} (holder={holder}) — a "
                        "sub-item must carry its parent's canonical data.id "
                        "verbatim. Write through "
                        "commitment_state.add_subitems."
                    )
                if pid.strip() == data.get("id"):
                    raise EventGateError(
                        f"commitment event is its own parent "
                        f"(data.parent_id == data.id == {pid!r}; "
                        f"holder={holder}) — a sub-item cannot parent "
                        "itself. Write through commitment_state.add_subitems."
                    )
                if LEGACY_SEQ_ID_RE.match(pid.strip()):
                    raise EventGateError(
                        f"commitment event data.parent_id {pid!r} is shaped "
                        f"like a legacy seq alias (holder={holder}) — the "
                        "parent reference must be the parent's canonical "
                        "data.id verbatim (cmt_<ulid>), never a seq "
                        "spelling. Write through "
                        "commitment_state.add_subitems."
                    )
            # CTS1 §5 — kind/counterparty consistency check, WARN-LEVEL ONLY
            # and NEW WRITES ONLY (RULED 2026-07-16: 49 live rows already
            # violate the promise half — a substrate-wide scan would fire 49
            # warnings on day one; historical rows converge via the §8.2
            # drip/batch fixup, never via warnings). The invariant that keeps
            # the two surfaces un-blurry:
            #   kind: task    ⇒ counterparty empty (a task with a counterparty
            #                   is a promise wearing the wrong label)
            #   kind: promise ⇒ counterparty signal present OR pending_review
            #                   (a promise with nobody on the other end is
            #                   either mis-linked — Bug #103 — or a task)
            # Never rejects: extraction legitimately fails to LINK real
            # counterparties, and blocking the capture would lose the item.
            # The warning is the writer-side nudge; surfaces stay correct
            # either way because they classify on effective kind (§2.2).
            _cts1_kind = data.get("kind")
            if _cts1_kind in ("task", "promise"):
                try:
                    from commitment_parties import counterparty_ids as _cp_ids
                    from commitment_parties import counterparty_names as _cp_names
                    _has_cp = bool(
                        _cp_ids(data) or _cp_names(data)
                        or data.get("owner_external")
                        or data.get("requester_id")
                        or data.get("requester_person_id")
                    )
                    if _cts1_kind == "task" and _has_cp:
                        sys.stderr.write(
                            f"[event_gate] CTS1 §5 warn (holder={holder}): "
                            f"commitment kind=task carries a counterparty — "
                            f"a task is self-owed by definition; if someone "
                            f"is waiting on this, capture it as kind=promise "
                            f"(title={str(data.get('title') or '')[:60]!r})\n"
                        )
                    elif (
                        _cts1_kind == "promise"
                        and not _has_cp
                        and not data.get("pending_review")
                    ):
                        sys.stderr.write(
                            f"[event_gate] CTS1 §5 warn (holder={holder}): "
                            f"commitment kind=promise carries no counterparty "
                            f"signal — it will render 'counterparty "
                            f"unresolved' on My Plate; link the counterparty "
                            f"at capture when known, or classify kind=task "
                            f"if only the user's clock is running "
                            f"(title={str(data.get('title') or '')[:60]!r})\n"
                        )
                except Exception:
                    pass  # the check is advisory — never let it break a write
            kind = data.get("kind")
            if kind in (None, ""):
                # Stage D flip: kind is REQUIRED AT CAPTURE. Strict path
                # rejects; legacy path warns loudly + stamps the
                # behavior-preserving default (burn-in posture, same as the
                # enum check) so an un-migrated long-tail writer degrades
                # visibly instead of breaking a customer workspace.
                msg = (
                    f"commitment event carries no data.kind (holder={holder}) "
                    f"— kind is required at capture (Phase 2 Stage D): "
                    f"classify as one of {sorted(KIND_VALUES)} when writing "
                    f"(counterparty promise → promise; self-owed → task; "
                    f"scheduling intent → scheduling; discuss item → agenda)"
                )
                if strict_enum:
                    raise EventGateError(msg)
                sys.stderr.write(f"[event_gate] {msg}\n")
                data["kind"] = _KIND_DEFAULT
            elif kind not in KIND_VALUES:
                raise EventGateError(
                    f"commitment event has invalid data.kind {kind!r} "
                    f"(allowed: {sorted(KIND_VALUES)}; holder={holder})"
                )
            ev["data"] = data
        elif isinstance(etype, str) and etype.startswith("commitment"):
            # Kind is only required on creation events, but a bad value on
            # any commitment-family event is still drift — reject it.
            data = ev.get("data")
            kind = data.get("kind") if isinstance(data, dict) else None
            if kind not in (None, "") and kind not in KIND_VALUES:
                raise EventGateError(
                    f"{etype} event has invalid data.kind {kind!r} "
                    f"(allowed: {sorted(KIND_VALUES)}; holder={holder})"
                )

        # 5 (reminder lane, v4.6.0 W4a). Origin is a HARD identity: reminders
        # exist only because the user explicitly asked. Rejection is
        # unconditional (both entries, strict or not) — a sweep/skill-minted
        # reminder is a defect, never a burn-in warning.
        if etype in REMINDER_TYPES:
            data = ev.get("data")
            data = {**data} if isinstance(data, dict) else {}
            if data.get("origin") != REMINDER_ORIGIN:
                raise EventGateError(
                    f"{etype} event carries data.origin={data.get('origin')!r} "
                    f"(holder={holder}) — reminders are minted by the USER "
                    f"only: data.origin must be '{REMINDER_ORIGIN}'. No skill, "
                    "sweep, or scheduled task may create or mutate a reminder "
                    "on its own. Build through shared/scripts/reminders.py."
                )
            if etype == "reminder":
                if data.get("id") in (None, ""):
                    data["id"] = "rem_" + _ulid()
            elif data.get("reminder_id") in (None, ""):
                raise EventGateError(
                    f"{etype} event carries no data.reminder_id "
                    f"(holder={holder}) — an id-less reminder mutation is a "
                    "dead letter; pass the reminder's data.id (rem_<ulid>) "
                    "verbatim."
                )
            ev["data"] = data

        # 4. Fail-loud rejection of id-less closures.
        if etype == "commitment_resolved" and not _has_closure_id(ev):
            raise EventGateError(
                "commitment_resolved event carries no readable commitment id "
                f"(need one of data.{'/data.'.join(COMMITMENT_CLOSURE_ID_FIELDS)}; "
                f"holder={holder}). An id-less closure is a dead letter — it "
                "closes nothing. Pass the commitment's data.id verbatim "
                "(cmt_<ulid> or legacy commitment_seq_<n>)."
            )

        # 4b (v4.6.0 S4) — the same dead-letter rule for the reassign and
        # unmute references: an event that names no target routes/clears
        # nothing. Both types are written through their canonical writers
        # (commitment_state.reassign_commitment / mute_ledger.clear_dismissal),
        # which always stamp the ids; this gate catches hand-built appends.
        if etype == "commitment_reassigned":
            data = ev.get("data")
            data = data if isinstance(data, dict) else {}
            if not _has_closure_id(ev):
                raise EventGateError(
                    "commitment_reassigned event carries no readable commitment "
                    f"id (holder={holder}) — an id-less reassignment is a dead "
                    "letter. Write through commitment_state.reassign_commitment."
                )
            if not (data.get("new_owner_id") or data.get("new_counterparty_id")):
                raise EventGateError(
                    "commitment_reassigned event names no new_owner_id or "
                    f"new_counterparty_id (holder={holder}) — a reassignment "
                    "must route the item somewhere. Write through "
                    "commitment_state.reassign_commitment."
                )
        # 4c (v4.6.0 MC1) — the same dead-letter rule for a per-person
        # receipt: a `commitment_partial_received` that names no commitment
        # records a delivery against nothing. Written through
        # commitment_state.mark_partial_received (always stamps the id); this
        # catches hand-built appends.
        if etype == "commitment_partial_received" and not _has_closure_id(ev):
            raise EventGateError(
                "commitment_partial_received event carries no readable "
                f"commitment id (holder={holder}) — an id-less partial "
                "receipt records a delivery against nothing. Write through "
                "commitment_state.mark_partial_received."
            )
        if etype == "chat_dismissal_cleared":
            data = ev.get("data")
            data = data if isinstance(data, dict) else {}
            if not (
                data.get("dismissal_seq") not in (None, "")
                or data.get("target_id") not in (None, "")
                or data.get("fingerprint") not in (None, "")
            ):
                raise EventGateError(
                    "chat_dismissal_cleared event references no dismissal "
                    f"(need data.dismissal_seq, data.target_id, or "
                    f"data.fingerprint; holder={holder}) — an unanchored clear "
                    "unmutes nothing. Write through mute_ledger.clear_dismissal."
                )

        out.append(ev)
    return out


def append_event(
    path,
    events,
    holder: str = "append_event",
) -> None:
    """Canonical gated append to events.jsonl (Phase 1 F1).

    Same signature semantics as atomic_write.atomic_append_jsonl (single dict
    or list of dicts; `path` is the events.jsonl path; omit seq/ts — they are
    auto-stamped inside the writer lock). Both entries gate in STRICT mode
    (Phase 4 2026-07-02); this one remains the canonical named entry for new
    code. Delegates the actual write to atomic_append_jsonl — there is
    exactly one append path.
    """
    if isinstance(events, dict):
        events = [events]
    if not isinstance(events, list):
        raise TypeError(
            f"append_event expects list[dict] or dict, got {type(events).__name__}"
        )
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise TypeError(
                f"append_event entries must be dicts; entry {i} is "
                f"{type(ev).__name__}: {ev!r}"
            )

    events = gate_events(events, strict_enum=True, holder=holder)

    try:
        from atomic_write import atomic_append_jsonl
    except ImportError:
        from pathlib import Path as _Path

        sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from atomic_write import atomic_append_jsonl

    atomic_append_jsonl(path, events, holder=holder)


__all__ = [
    "EventGateError",
    "TYPE_NORMALIZATION",
    "REMINDER_TYPES",
    "REMINDER_ORIGIN",
    "new_commitment_id",
    "new_reminder_id",
    "gate_events",
    "append_event",
]
