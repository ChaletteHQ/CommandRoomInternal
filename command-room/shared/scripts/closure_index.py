"""closure_index — THE commitment-closure chain, one implementation.

Extracted from cru_match.load_open_commitments' fold (BUG-8330 item 1).
Three surfaces had rolled private, weaker closure chains — workspace-map and
the daily-command-center builder missed `commitment_superseded`, the
`data.target_id` closer field, the F3 seq aliases, and Stage D reopens
(their flat resolved-id sets meant a reopened item stayed hidden forever);
email_outcomes missed the seq aliases and reopens. Every divergence was a
surface silently disagreeing with the loader about what is open.

From now on there is ONE chain. Consumers:
  - cru_match.load_open_commitments      (read-side projection)
  - commitment_state                     (write-side pre-flight mirror)
  - build_workspace_map_input            (workspace-map artifact)
  - build_dcc_input                      (daily command center artifact)
  - email_outcomes.commitment_punctuality (punctuality read)

Semantics (unchanged from the loader):
  - Closers: commitment_resolved / thread_resolved / commitment_superseded.
  - Id chain: data.commitment_id → data.thread_id → data.id →
    data.target_id → top-level commitment_id → thread_id → id.
  - F3 seq aliases: data.commitment_seq and data.source_event_seq both close
    the commitment EVENT at that seq, regardless of any id field present.
  - Order-aware reopens (Stage D): a commitment is closed iff its LATEST
    closure comes after its LATEST reopen in append order.

Do not re-implement any part of this fold locally. If a consumer needs a
field this module doesn't fold, extend the module.
"""

from __future__ import annotations

CLOSER_TYPES = ("commitment_resolved", "thread_resolved", "commitment_superseded")

# The one closer-id chain lives in event_types (the shared vocabulary home) —
# write-accept (event_gate) and read-honor (this fold) both walk the SAME
# list. BUG-8330 item 3 found data.thread_id honored on read but absent from
# the gate's accept list, and top-level target_id accepted nowhere; deriving
# both sides from one constant closes that class. Local fallback keeps
# import order safe (mirrors commitment_state's posture).
try:
    from event_types import (
        COMMITMENT_CLOSURE_ID_CHAIN as CLOSURE_ID_FIELDS,
        COMMITMENT_CLOSURE_SEQ_FIELDS as CLOSURE_SEQ_FIELDS,
        LEGACY_SEQ_ID_RE as _LEGACY_SEQ_ID_RE,
    )
except Exception:  # pragma: no cover
    import re as _re
    CLOSURE_ID_FIELDS = (
        ("data", "commitment_id"),
        ("data", "thread_id"),
        ("data", "id"),
        ("data", "target_id"),
        ("", "commitment_id"),
        ("", "thread_id"),
        ("", "id"),
        ("", "target_id"),
    )
    CLOSURE_SEQ_FIELDS = ("commitment_seq", "source_event_seq")
    _LEGACY_SEQ_ID_RE = _re.compile(r"^(?:commitment_seq_|event_|seq_)?0*(\d+)$")


def _as_seq(value):
    """Coerce a seq-ish value (int or digit-string) to int; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def closer_target_id(ev: dict) -> str:
    """The id a closer event closes, walking CLOSURE_ID_FIELDS in order."""
    d = ev.get("data") or {}
    for scope, field in CLOSURE_ID_FIELDS:
        v = (d if scope == "data" else ev).get(field)
        if v:
            return str(v)
    return ""


def closer_target_seqs(ev: dict) -> list:
    """The F3-amnesty seq aliases a closer references (both fields fold)."""
    d = ev.get("data") or {}
    out = []
    for field in CLOSURE_SEQ_FIELDS:
        v = _as_seq(d.get(field))
        if v is not None:
            out.append(v)
    return out


def reopen_target(ev: dict):
    """(target_id, target_seq) a commitment_reopened event reopens."""
    d = ev.get("data") or {}
    target = (
        d.get("commitment_id") or d.get("target_id")
        or ev.get("commitment_id")
    )
    return (str(target) if target else "", _as_seq(d.get("commitment_seq")))


class ClosureIndex:
    """Order-aware closure state over one pass of events.

    Maps target → last file index for closures and reopens; `is_closed`
    compares them. Also keeps index → event for consumers that need the
    effective closing event itself (punctuality reads its timestamp).
    """

    __slots__ = (
        "closed_ids_at", "closed_seqs_at",
        "reopened_ids_at", "reopened_seqs_at",
        "event_at",
    )

    def __init__(self):
        self.closed_ids_at: dict = {}
        self.closed_seqs_at: dict = {}
        self.reopened_ids_at: dict = {}
        self.reopened_seqs_at: dict = {}
        self.event_at: dict = {}

    def fold(self, idx: int, ev: dict) -> None:
        """Fold one event (append order = idx). Non-closure events are a no-op,
        so callers may stream every event through without pre-filtering."""
        et = ev.get("type") or ev.get("event") or ""
        if et in CLOSER_TYPES:
            cid = closer_target_id(ev)
            if cid:
                self.closed_ids_at[cid] = idx
            for sv in closer_target_seqs(ev):
                self.closed_seqs_at[sv] = idx
            self.event_at[idx] = ev
        elif et == "commitment_reopened":
            target, sv = reopen_target(ev)
            if target:
                self.reopened_ids_at[target] = idx
            if sv is not None:
                self.reopened_seqs_at[sv] = idx

    def _last_close(self, cid: str, seq) -> int:
        seq_ok = isinstance(seq, int) and not isinstance(seq, bool)
        return max(
            self.closed_ids_at.get(cid, -1),
            self.closed_seqs_at.get(seq, -1) if seq_ok else -1,
        )

    def _last_reopen(self, cid: str, seq) -> int:
        seq_ok = isinstance(seq, int) and not isinstance(seq, bool)
        return max(
            self.reopened_ids_at.get(cid, -1),
            self.reopened_seqs_at.get(seq, -1) if seq_ok else -1,
        )

    def is_closed(self, cid: str, seq=None) -> bool:
        """Closed iff the LATEST closure (id chain or F3 seq alias) comes
        after the LATEST reopen (Stage D undo). Never closed → False."""
        return self._last_close(cid, seq) > self._last_reopen(cid, seq)

    def closing_event(self, cid: str, seq=None):
        """The event that EFFECTIVELY closed (cid, seq), or None if the item
        is not currently closed. This is the latest standing closure — after
        a reopen + re-close it is the re-close, not the first closure."""
        last_close = self._last_close(cid, seq)
        if last_close <= self._last_reopen(cid, seq):
            return None
        return self.event_at.get(last_close)


def build_closure_index(events) -> ClosureIndex:
    """One pass over `events` (append order) → a ClosureIndex."""
    index = ClosureIndex()
    for idx, ev in enumerate(events):
        if isinstance(ev, dict):
            index.fold(idx, ev)
    return index


# ---------------------------------------------------------------------------
# Resolvability (BUG-8330 item 3) — shared by the audit AND the write gate
# ---------------------------------------------------------------------------

def commitment_key(ev: dict) -> str:
    """Canonical id of a commitment event — cru_match._commitment_id's rule
    (data.id → top-level id → the synthesized commitment_seq_<n> fallback)."""
    d = ev.get("data") or {}
    return d.get("id") or ev.get("id") or f"commitment_seq_{ev.get('seq', '?')}"


def build_commitment_universe(events):
    """(by_id, by_seq) over the commitment events in `events` — the
    resolution universe closures are checked against."""
    by_id: dict = {}
    by_seq: dict = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if (ev.get("type") or ev.get("event")) == "commitment":
            by_id[commitment_key(ev)] = ev
            seq = ev.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                by_seq[seq] = ev
    return by_id, by_seq


def resolve_closure_target(ev: dict, by_id: dict, by_seq: dict):
    """The commitment EVENT a closer resolves to, or None when nothing
    matches. Mirrors normalize_commitment_id's amnesty WITHOUT raising:
    exact id → legacy seq spelling → seq-alias fields. Extracted from
    audit_closure_integrity so the write gate rejects what the audit would
    later flag — same resolver, both ends (BUG-8330 item 3: the gate's
    presence check let `cmt_TYPO` through clean)."""
    raw = closer_target_id(ev)
    if raw:
        raw_s = raw.strip()
        if raw_s in by_id:
            return by_id[raw_s]
        m = _LEGACY_SEQ_ID_RE.match(raw_s)
        if m:
            target = by_seq.get(int(m.group(1)))
            if target is not None:
                return target
    for s in closer_target_seqs(ev):
        target = by_seq.get(s)
        if target is not None:
            return target
    return None


__all__ = [
    "CLOSER_TYPES",
    "CLOSURE_ID_FIELDS",
    "CLOSURE_SEQ_FIELDS",
    "ClosureIndex",
    "build_closure_index",
    "build_commitment_universe",
    "closer_target_id",
    "closer_target_seqs",
    "commitment_key",
    "reopen_target",
    "resolve_closure_target",
]
