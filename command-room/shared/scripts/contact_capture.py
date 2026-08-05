#!/usr/bin/env python3
"""Auto contact records for people the CEO actually corresponds with
(SPEC CONTACT1 — M ruled 2026-08-02: build it, TWO-WAY ONLY, hard clarity bar).

WHY THIS EXISTS
---------------
People the CEO demonstrably corresponds with sat as name-only confirm rows for
a median 43 days, because nothing created a contact record from correspondence
itself. The writer and the auto class ALREADY exist:

  * `people_writer.auto_add_person` (FS-11) — the same-name dedup gate and the
    observed-provenance email capture.
  * `brain_proposals.AUTO_ALLOWED["person_org_creation_structured_fact"]` —
    "identity from a structured connector fact (full name + address from
    mail/calendar), zero same-name/email collision, past the noise gate —
    additive only", with the archive-never-delete reverser registered in
    `brain_undo.REVERSERS`.

What was missing is the FEEDER. FS-11 only ever fired from the reconcile batch
over mention clusters (`identity_reconcile`) — i.e. from proposal rows someone
else had already captured. This module is the second feeder: it reads the Sent
folder pass `reconcile-sent` already makes and turns a two-way correspondent
into a record directly. It does NOT fork or re-implement the writer — every
create goes through `auto_add_person`, unchanged.

THE GATE (spec §2 — every bar FAILS SAFE; an unverifiable bar is False, and
False means no record)
-----------------------------------------------------------------------------
A record auto-creates only when ALL of these hold:

  1. TWO-WAY. The CEO has SENT this address a direct To/CC message from the
     declared mail backend's Sent folder. `direction` must be exactly "sent"
     and `recipient_field` must be "to" or "cc". Inbound-only NEVER qualifies —
     newsletters, cold outreach and vendors never auto-enter. A meeting
     attendee who never got a direct mail does NOT qualify either: that
     widening was offered to M and NOT taken.

  2. CLARITY — identity is STRUCTURED, never inferred.
     - Name: a real multi-token display name whose `name_source` is one of
       `STRUCTURED_NAME_SOURCES` — the counterparty's own mail headers, or a
       calendared attendee record corroborating the same address. A bare first
       name, an address-derived guess ("jsmith" -> "J Smith"), or a
       transcript-attributed speaker NEVER suffices. TRANSCRIPTS ARE NOT A
       SOURCE for this spec (M: uncalendared speaker attribution is sketchy).
       Annotation/guess markers in the name (digits, parentheses, slashes) are
       refused by the same regex `identity_reconcile` uses.
     - Email: the counterparty's actual address, stored with OBSERVED
       provenance (the message it surfaced from — the FS-11 rule, enforced
       inside the writer). `identity_reconcile.is_role_address` excludes shared
       inboxes: a role address identifies a mailbox, not a person.
     - Role/org: best-effort, NEVER blocking. Attribution runs only through the
       existing work-domain path (`org_writer.attribute_person_to_org` with
       `work_domains=` and NO `org_hint`, so the create-an-org branch is
       unreachable from here), which filters free-mail via
       `identity_reconcile.is_free_mail_domain`. Signature-block parsing is
       OUT — it is prose inference, exactly what the clarity bar excludes.

  3. EXISTING GATES UNCHANGED. A same-name collision returns `needs_confirm`
     from the writer and this module mints today's person confirm row PRE-
     FILLED with the observed address (so the human answering it can see who
     it is). The duplicate-suspect set blocks. `person_merge` stays
     confirm-forever — nothing here merges anything.

POSTURE
-------
Applied-then-narrated (PID1): a create lands as a change-feed entry with undo,
never as a confirm row. Every create is stamped with ONE `brain_batch_id` +
`brain_change_class="person_org_creation_structured_fact"` on its
`contact_captured` event, so `brain_undo.recent_auto_batches` lists it and
`brain_undo.undo_batch` archives it through the registered R1 reverser — no
new class, no new reverser, no new policy row.

THE CURSOR IS ITS OWN (`workspace.contact_capture_cursor`), NOT the sent one
-----------------------------------------------------------------------------
Riding `sent_reconcile_cursor` looked cheaper and is WRONG, for a reason that
is structural rather than stylistic: that cursor does not bound what
reconcile-sent FETCHES. Its skill deliberately fetches a 30-day window
"regardless of the cursor" on a workspace's first real run (Bug #101) and on
every manual "catch up my sent mail" — because closing an already-handled
commitment twice is a no-op, so an over-wide window is always safe THERE.
Creating contacts is not idempotent in that way: a wide fetch would mint 30
days of records in one silent fire. That is precisely the backfill the spec
ships DARK.

So capture keys on its OWN cursor. It is absent on first sight, which
initializes it to the newest message in the window and captures NOTHING — the
"go-forward from cursor-now" ruling, with no backfill run and no knob for the
user to trip. `CONTACT_BACKFILL_DAYS` is that knob and it is `None`: setting it
is M's separate decision, not a code change made in passing. Catch-up windows
and cursor replays therefore cost nothing here, and CATCHUP1's partitioned
semantics do not apply — reconcile-sent is not a partitioned job (each period
is not its own deliverable), it is a cursor-driven span that self-heals, and
this cursor is the same shape.

IDEMPOTENCY IS BY ADDRESS, AND IT SURVIVES AN UNDO
--------------------------------------------------
`already_captured` reads the append-only `contact_captured` events, keyed on
the NORMALIZED ADDRESS. The event carries its provenance (the message the
address surfaced from) for audit, but provenance is deliberately NOT part of
the key: a per-message key would let the NEXT message to the same person
re-create a record the CEO had just undone, and an undo that does not survive
one fire is not a reversal (the AUTOAPPLY §4c lesson).

Be exact about how much of that this layer is carrying, because TWO layers
agree here. The archive reverser leaves the record on file, so
`create_person`'s own dedup would also refuse a re-create — on the address, or
failing that on the exact name. What this ledger adds is that it answers
FIRST, before the writer is called, keyed on the address alone and independent
of what later happens to the record: the refusal is a clean skip rather than a
caught `DuplicatePersonError`, and the receipt's `why` says which layer spoke
(`already captured` = the ledger, `already on file` = the writer). The test
suite pins that distinction rather than the outcome, so this claim cannot pass
on its understudy.

CAP + CARRY, AND THE BOUND ON CARRYING
--------------------------------------
`CONTACT_CAPTURE_CAP` bounds creates per fire. Overflow is CARRIED, never
dropped: items are processed oldest-first, and the cursor freezes at the last
timestamp STRICTLY BEFORE the first item this fire did not finish — so a
message with more recipients than the cap moves as a whole or not at all.

Holding the cursor is right for a transient failure and ruinous for a
permanent one, so it is bounded: after `MAX_DEFER_ATTEMPTS` consecutive fires
stuck on the same address the pass gives up on it LOUDLY (`stuck_attempts`),
the cursor advances, and the receipt carries a plain sentence for the chat.
Giving up resets the count, so the next message from that correspondent gets a
fresh three tries. Counts — including the give-ups and a cursor RESET — ride
the maintenance receipt (`sent_reconcile.data`, additive — the D2 pattern);
a state only a receipt knows is a state nobody finds on a customer's machine.

CONNECTOR-AGNOSTIC
------------------
Nothing here names a provider. The caller resolves the declared mail backend
through the seam and hands in items; a thin-connector workspace hands in
nothing and this module degrades to a clean zero — no errors, no prose nags.

stdlib only; nothing here calls the network.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Policy constants — widen by RULING, never by drift.
# ---------------------------------------------------------------------------

# Per-fire create cap. Proposed from REAL Sent volume on the operator's live
# substrate (85 `sent_reconcile` receipts): median 18 messages scanned per
# fire, mean 19.8, p90 29, p95 31 — and the overwhelming majority of those
# recipients are already on file, so the steady-state new-contact yield is low
# single digits. 10 therefore sits well above the honest need while bounding a
# surprise burst at about a third of a p90 fire; three weekday fires give 30/day
# of drain capacity, so carry-over cannot accumulate in steady state. It is
# also the value the shipped sibling rail already uses
# (`identity_reconcile.STEADY_CAPS["auto_add"]`) — one number, one meaning.
CONTACT_CAPTURE_CAP = 10

# THE DARK KNOB (spec §3). None = go-forward from the cursor only. An int would
# floor the first fire at `now - N days` and backfill that window. Turning it on
# is M's separate decision and must arrive with a bounded window and a receipt
# — never as a side effect of another build.
CONTACT_BACKFILL_DAYS: Optional[int] = None

# This module's own cursor key on `entities.json` -> workspace. See the module
# docstring for why it is NOT `sent_reconcile_cursor`.
CURSOR_KEY = "contact_capture_cursor"

# How far behind `now` a stored cursor may sit before this pass stops trusting
# it and re-initializes (see `capture_floor`). Matches the widest window
# reconcile-sent documents fetching, so no fire can ever create from mail older
# than one catch-up window — the backfill fence, in one number.
CURSOR_MAX_LOOKBACK_DAYS = 30

# Allowance for an honestly fast clock when deciding a timestamp is "in the
# future" (see `usable_ts`). Small on purpose: this is skew, not a window.
CLOCK_SKEW_TOLERANCE_SECONDS = 300

# How many consecutive fires may defer on the SAME address before the pass
# stops holding the cursor for it and gives up loudly (see `stuck_attempts`).
# Three, because deferring is right for a transient failure and ruinous for a
# permanent one, and three identical failures is where "the lock was busy"
# stops being the likely explanation.
MAX_DEFER_ATTEMPTS = 3

# The ONLY name provenances that clear the clarity bar. A transcript-attributed
# speaker and an address-derived guess are deliberately absent, and an item
# that names no source at all fails safe (unverifiable -> False).
STRUCTURED_NAME_SOURCES = frozenset({"mail_header", "calendar_attendee"})

# Two-way means a DIRECT send. To/CC only, per the ruling.
DIRECT_RECIPIENT_FIELDS = frozenset({"to", "cc"})

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class ContactItemError(ValueError):
    """A caller handed in a structurally malformed item — fail loud so a bad
    extraction is visible and goes back to the extractor, never silently
    dropped and never silently written wrong (the SentItemError sibling).

    A REFUSAL is not an error: an item that simply does not clear a gate bar
    is a normal, expected outcome and lands in `refused` with its reason."""


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

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


def _now_iso() -> str:
    return _clock_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _entities_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "entities.json"


def _norm_email(email) -> str:
    return str(email or "").strip().lower()


def _norm_name(s) -> str:
    # Same semantics as people_writer._normalize_name / identity_reconcile.
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def contact_fingerprint(email) -> str:
    """The idempotency key: `contact:<normalized address>`. Address only —
    see the module docstring on why provenance is recorded but not keyed."""
    addr = _norm_email(email)
    if not addr:
        raise ContactItemError("a contact fingerprint needs an address")
    return f"contact:{addr}"


# ---------------------------------------------------------------------------
# Cursor (this module's own — see the docstring)
# ---------------------------------------------------------------------------

def read_contact_cursor(workspace_root):
    """(cursor_or_None, raw_entities_dict). Defensive about the wrapper shape,
    exactly like `reconcile_sent_commitments._read_cursor`."""
    import json

    raw = json.loads(_entities_path(workspace_root).read_text(encoding="utf-8"))
    inner = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ws = inner.get("workspace") if isinstance(inner.get("workspace"), dict) else {}
    return ws.get(CURSOR_KEY), raw


def stamp_contact_cursor(raw: dict, new_cursor) -> dict:
    """Set the cursor INTO an already-read entities doc and hand it back — the
    caller performs the single locked write.

    Deliberately not a writer: the orchestrator writes `entities.json` once per
    fire, and this pass creates PEOPLE in that same file. A second writer
    holding a doc it read before those creates would write them straight back
    out (the stale-`raw` clobber)."""
    inner = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ws = inner.get("workspace")
    if not isinstance(ws, dict):
        ws = {}
        inner["workspace"] = ws
    ws[CURSOR_KEY] = new_cursor
    return raw


def write_contact_cursor(workspace_root, new_cursor, *, source_skill: str) -> None:
    """Standalone cursor write (the manual / test path). RE-READS the doc
    first so it can never carry away a stale snapshot."""
    from atomic_write import atomic_write_json_locked

    _cur, raw = read_contact_cursor(workspace_root)
    atomic_write_json_locked(_entities_path(workspace_root),
                             stamp_contact_cursor(raw, new_cursor),
                             holder=source_skill)


def _iso(dt) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def usable_ts(value, *, now_dt=None):
    """A message timestamp this pass may act on, parsed — or None.

    None means BOTH "do not create from it" and "never let it near the
    cursor". Two shapes are refused, and the second one is why this function
    exists at all:

      * UNPARSEABLE — a display string, a dict, an int. It cannot be ordered
        against a cursor, so acting on it is guessing.
      * IN THE FUTURE — a connector or an extractor emitting `9999-12-31`
        would otherwise be written straight into the cursor and BRICK the pass
        forever: nothing can ever be newer than the year 9999, so every later
        fire finds nothing eligible and reports a clean, permanent zero. A
        small skew allowance covers an honestly fast clock.
    """
    from event_time import parse_ts

    dt = parse_ts(value) if isinstance(value, str) else None
    if dt is None:
        return None
    now_dt = now_dt or _clock_now()
    if dt > now_dt + _dt.timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS):
        return None
    return dt


def capture_floor(workspace_root, *, now_iso: Optional[str] = None,
                  newest_ts: Optional[str] = None) -> dict:
    """The go-forward floor for this fire — and the ONLY backfill fence there
    is, which is why it validates what it reads instead of trusting it.

    Returns {"floor", "floor_dt", "initialized", "backfill_days", "reset"}.
    `floor` is exclusive: only a message strictly newer than it is eligible.

    THREE WAYS A CURSOR IS NOT A CURSOR, all of them re-initializing to
    first-sight semantics (set the go-forward point to the newest message this
    window carried, create NOTHING this fire, and say so in `reset`):

      * ABSENT — the ordinary first sight.
      * UNREADABLE — junk string, dict, int, None-shaped. A value that cannot
        be parsed cannot bound anything; treating it as "no floor" would open
        the whole window.
      * REWOUND BEYOND THE WINDOW — older than `CURSOR_MAX_LOOKBACK_DAYS`.
        This is not hypothetical: the workspace is Drive-synced across
        machines, and a stale copy landing on top of a current one rewinds
        every cursor in the file. reconcile-sent's fetch is floored at its own
        cursor, so a rewound contact cursor would hand this pass months of
        Sent mail and it would create from all of it — the exact backfill the
        knob is dark to prevent. The bound matches the widest window
        reconcile-sent ever documents fetching (30 days).

    A long-closed laptop therefore also re-initializes and creates nobody for
    that period. That is the ruled direction: creating nothing is recoverable,
    a silent month of contact creation is not.

    `CONTACT_BACKFILL_DAYS` set to an int floors at `now - N days` instead —
    the knob, unwired."""
    raw_cursor, _raw = read_contact_cursor(workspace_root)
    now_iso = now_iso or _now_iso()
    now_dt = usable_ts(now_iso) or _clock_now(workspace_root)

    def _first_sight(reason):
        if CONTACT_BACKFILL_DAYS:
            floor_dt = now_dt - _dt.timedelta(days=int(CONTACT_BACKFILL_DAYS))
            return {"floor": _iso(floor_dt), "floor_dt": floor_dt,
                    "initialized": True,
                    "backfill_days": CONTACT_BACKFILL_DAYS, "reset": reason}
        # Dark: the floor IS the newest message this fire saw (so the NEXT
        # fire starts cleanly after it), or now. Nothing in the window can be
        # strictly newer than it, so nothing in the window can create.
        newest_dt = usable_ts(newest_ts, now_dt=now_dt)
        floor_dt = newest_dt or now_dt
        return {"floor": _iso(floor_dt), "floor_dt": floor_dt,
                "initialized": True, "backfill_days": None, "reset": reason}

    if raw_cursor is None or (isinstance(raw_cursor, str)
                              and not raw_cursor.strip()):
        return _first_sight("first pass on this workspace")

    cursor_dt = usable_ts(raw_cursor, now_dt=now_dt)
    if cursor_dt is None:
        return _first_sight(
            "the stored go-forward point could not be read, so it was set "
            "again from this window")
    if cursor_dt < now_dt - _dt.timedelta(days=CURSOR_MAX_LOOKBACK_DAYS):
        return _first_sight(
            f"the stored go-forward point was more than "
            f"{CURSOR_MAX_LOOKBACK_DAYS} days behind, which reads as a "
            f"restored or rewound workspace rather than a real backlog")

    return {"floor": raw_cursor, "floor_dt": cursor_dt, "initialized": False,
            "backfill_days": CONTACT_BACKFILL_DAYS, "reset": None}


# ---------------------------------------------------------------------------
# The gate (spec §2)
# ---------------------------------------------------------------------------

def _name_tokens(name) -> list:
    """Name tokens with name-punctuation stripped: "J. Smith" -> ["j", "smith"]
    so an initial reads as the one character it is."""
    return [t for t in (re.sub(r"[^a-z0-9]+", "", tok)
                        for tok in _norm_name(name).split()) if t]


def _initial_only_name(name) -> bool:
    """True when any token of the name is a single character — "J Smith",
    "J. Smith", "Skyler V".

    This is the spec's `jsmith -> "J Smith"` shape: a name whose word boundary
    was INVENTED at a letter, which is what address expansion produces and
    what a real display name does not. It is also a clarity bar in its own
    right under M's ruling — an initial plus a surname does not make an
    identity clear, so it stays a human decision whatever produced it."""
    toks = _name_tokens(name)
    return bool(toks) and any(len(t) == 1 for t in toks)


def clarity_name_ok(name, email) -> tuple:
    """(ok, reason) for bar 2's NAME half: no annotation/guess markers,
    multi-token, and no initial-plus-surname.

    THE ADDRESS-DERIVED BAR IS THESE TWO, and that is a finding rather than an
    omission. Three explicit "is this the address wearing a costume" tests were
    written and every one of them was either dead or wrong:

      * "the name squashes to the local part" and "the name is the local part
        with its separators turned into spaces" BOTH refuse
        `skyler.vance@ -> "Skyler Vance"`, which is what a correct mail header
        looks like for most work addresses. A bar that refuses the normal case
        is not a bar, it is an off switch.
      * "the name IS the local part or the whole address" cannot change an
        outcome: a local part contains no spaces, so anything it matches is
        single-token and the multi-token bar below already refuses it. It was
        removed rather than kept as decoration — a predicate that can never
        flip a verdict is exactly the kind of fence that reads as protection
        and provides none.

    What is left is what genuinely holds. The multi-token bar catches the
    address or local part pasted in as a name (the shape a connector emits for
    a message that carried no display name). `_initial_only_name` catches the
    spec's own forbidden example, `jsmith -> "J Smith"`, on the INVENTED word
    boundary. Beyond that, a real header name and a fabricated one are not
    distinguishable at this layer for a `first.last@` address — which is why
    `name_source` is a stated PROVENANCE claim rather than something re-derived
    here, exactly as `person_backlog_sweep._observed_email` and
    `identity_reconcile`'s source families work.
    """
    from identity_reconcile import _NAME_ANNOTATION_RE

    raw = str(name or "").strip()
    if not raw:
        return False, "no display name on the message header"
    if _NAME_ANNOTATION_RE.search(raw):
        return False, ("the display name carries annotation or guess markers — "
                       "a captured note is not a canonical name")
    if len(_norm_name(raw).split()) < 2:
        return False, ("a single-token display name — including the address or "
                       "its local part pasted in as one — is a permanent human "
                       "decision, never auto (Bug #19)")
    if _initial_only_name(raw):
        return False, ("an initial plus a surname is the shape an address "
                       "expansion produces, and it is not a clear identity "
                       "either way")
    return True, ""


def duplicate_suspect_surfaces(workspace_root) -> dict:
    """{"names": {normalized canonical names}, "emails": {addresses}} over the
    duplicate-suspect pairs `identity_reconcile.scan_existing_duplicates`
    finds. A candidate touching either set is refused: the identity graph is
    already ambiguous there, and adding a third record is the wrong write.

    Fails SAFE-BY-OPENNESS on an unreadable substrate only because the scanner
    itself already returns [] there and `auto_add_person`'s same-name gate is
    the defense-in-depth behind this bar."""
    from identity_reconcile import scan_existing_duplicates

    names: set = set()
    emails: set = set()
    try:
        pairs = scan_existing_duplicates(workspace_root)
    except Exception:
        return {"names": names, "emails": emails}
    from people_writer import get_person_emails

    for pair in pairs:
        for side in ("keep", "duplicate"):
            rec = pair.get(side) or {}
            n = _norm_name(rec.get("canonical_name"))
            if n:
                names.add(n)
            try:
                for e in get_person_emails(rec):
                    e = _norm_email(e)
                    if e:
                        emails.add(e)
            except Exception:
                continue
    return {"names": names, "emails": emails}


def gate_contact_item(item, *, suspects: Optional[dict] = None,
                      own_addresses: Optional[set] = None) -> dict:
    """Run the §2 gate over ONE extracted item. Pure — no substrate reads
    beyond the `suspects` and `own_addresses` sets the caller passes in.

    Returns {"ok", "bar", "reason", "email", "name", "message_id", "ts"}.
    `bar` names WHICH bar refused, so a receipt can say why without a second
    classification pass. Raises ContactItemError only for a structurally
    malformed item (no dict, no message id) — a refusal is not an error.
    """
    from identity_reconcile import is_role_address

    if not isinstance(item, dict):
        raise ContactItemError("a contact item must be a dict")
    message_id = str(item.get("message_id") or "").strip()
    if not message_id:
        raise ContactItemError("a contact item needs the message id it was "
                               "observed in — provenance is not optional")
    email = _norm_email(item.get("email"))
    name = str(item.get("display_name") or "").strip()
    ts = str(item.get("ts") or "").strip()
    out = {"ok": False, "bar": "", "reason": "", "email": email, "name": name,
           "message_id": message_id, "ts": ts}

    # --- Bar 1: TWO-WAY ----------------------------------------------------
    # Fail-safe by construction: anything that is not literally a direct
    # To/CC recipient of a message in the CEO's own Sent folder is refused,
    # including an item that forgot to say.
    if str(item.get("direction") or "").strip().lower() != "sent":
        out["bar"] = "two_way"
        out["reason"] = ("not from the CEO's own sent mail — inbound-only "
                         "correspondence never creates a record")
        return out
    if str(item.get("recipient_field") or "").strip().lower() \
            not in DIRECT_RECIPIENT_FIELDS:
        out["bar"] = "two_way"
        out["reason"] = ("not a direct To/CC recipient — only someone the CEO "
                         "addressed directly qualifies")
        return out

    # --- Bar 2: CLARITY — the address --------------------------------------
    if not email or not _EMAIL_RE.match(email):
        out["bar"] = "clarity_email"
        out["reason"] = "no usable counterparty address on the message"
        return out
    if email in (own_addresses or set()):
        # A self-CC is correspondence with nobody. Left unguarded this creates
        # a DUPLICATE RECORD OF THE USER from a secondary account, or — worse,
        # because it is silent and permanent-feeling — collides with the
        # user's own record and puts the CEO into their own confirm queue.
        out["bar"] = "own_address"
        out["reason"] = ("this is one of your own mail accounts — a message "
                         "to yourself is not correspondence with anyone")
        return out
    if is_role_address(email):
        out["bar"] = "role_address"
        out["reason"] = ("a shared/role inbox identifies a mailbox, not a "
                         "person — never creates a record")
        return out

    # --- Bar 2: CLARITY — the name and its SOURCE --------------------------
    source = str(item.get("name_source") or "").strip().lower()
    if source not in STRUCTURED_NAME_SOURCES:
        out["bar"] = "name_source"
        out["reason"] = (
            "the name did not come from a structured source (the "
            "counterparty's own mail header, or a calendared attendee record "
            "for the same address) — a transcript-attributed speaker or an "
            "unstated source never creates a record")
        return out
    ok, why = clarity_name_ok(name, email)
    if not ok:
        out["bar"] = "clarity_name"
        out["reason"] = why
        return out

    # --- Bar 3: the existing gates that can be judged from here ------------
    # (the same-name gate and create-time dedup run INSIDE the writer.)
    suspects = suspects or {"names": set(), "emails": set()}
    if email in (suspects.get("emails") or set()) or \
            _norm_name(name) in (suspects.get("names") or set()):
        out["bar"] = "duplicate_suspect"
        out["reason"] = ("this identity is already a duplicate suspect on file "
                         "— a third record is never the right write")
        return out

    out["ok"] = True
    out["reason"] = ("two-way correspondence with a structured full name and "
                     "an observed address")
    return out


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def already_captured(workspace_root, email) -> bool:
    """True when this address has ALREADY been through the capture — keyed on
    the append-only `contact_captured` event, so it survives an undo (the
    record is archived, not deleted, and the event is never rewritten).

    Shard-transparent via events_io."""
    addr = _norm_email(email)
    if not addr:
        return False
    want = f"contact:{addr}"
    try:
        from events_io import iter_events
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(_HERE))
        from events_io import iter_events

    for ev in iter_events(workspace_root):
        if ev.get("type") != "contact_captured":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(data.get("outcome") or "") in _NON_ADJUDICATING_OUTCOMES:
            continue
        if str(data.get("fingerprint") or "").strip().lower() == want:
            return True
    return False


# Ledger outcomes that record an ATTEMPT rather than an adjudication. They must
# never suppress a later try — the whole point of writing them is that the
# address has NOT been dealt with yet.
_NON_ADJUDICATING_OUTCOMES = frozenset({"deferred", "gave_up"})


def _captured_fingerprints(workspace_root) -> set:
    """Every already-ADJUDICATED fingerprint, in ONE pass —
    `already_captured`'s batch form (the per-item version re-walks the log and
    this pass runs over a whole fetch window).

    Attempt rows (`deferred` / `gave_up`) are excluded: an address this pass
    failed to write is not an address it has answered."""
    out: set = set()
    try:
        from events_io import iter_events
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(_HERE))
        from events_io import iter_events

    for ev in iter_events(workspace_root):
        if ev.get("type") != "contact_captured":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if str(data.get("outcome") or "") in _NON_ADJUDICATING_OUTCOMES:
            continue
        fp = str(data.get("fingerprint") or "").strip().lower()
        if fp:
            out.add(fp)
    return out


def stuck_attempts(workspace_root) -> dict:
    """{fingerprint: consecutive failed attempts} — the deferral counter.

    THE STALL THIS BOUNDS. Deferring a failed write is right for a TRANSIENT
    failure and catastrophic for a permanent one: an account-scope refusal, a
    schema rejection, a record the writer will never accept — each of those
    fails identically every fire, so the cursor freezes forever, everything
    newer carries forever, and none of it is visible (the counts ride an audit
    event and the chat says nothing when zero contacts were added). Three
    identical stuck fires is not a blip.

    Counted from the ledger, so it survives a restart: `deferred` rows since
    the last `gave_up` for that address. Giving up RESETS the count on
    purpose — the item is skipped loudly and the cursor moves on, and the next
    message from that same correspondent gets a fresh three tries rather than
    being written off permanently on the strength of one bad week."""
    try:
        from events_io import iter_events
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(_HERE))
        from events_io import iter_events

    counts: dict = {}
    for ev in iter_events(workspace_root):
        if ev.get("type") != "contact_captured":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        fp = str(data.get("fingerprint") or "").strip().lower()
        if not fp:
            continue
        outcome = str(data.get("outcome") or "")
        if outcome == "deferred":
            counts[fp] = counts.get(fp, 0) + 1
        elif outcome == "gave_up":
            counts[fp] = 0
    return counts


# ---------------------------------------------------------------------------
# The queue-row retirement (spec §3 — the proposal-adapter-existence gotcha)
# ---------------------------------------------------------------------------

def open_rows_satisfied_by(workspace_root, name, *, email=None) -> list:
    """Open NAME-ONLY person proposal rows this new record answers.

    Read WITHOUT `suppress_on_file` on purpose. FS-19's `suppress_on_file` is
    a RENDER FILTER and the CHANGELOG says so in as many words ("Filter-only,
    no tombstone") — the row stays open in the substrate forever, invisible on
    every surface, and any surface reading with the default False still shows
    it. So creating the record is NOT enough: a real `person_proposal_resolved`
    tombstone has to be written, which is what `capture_contacts` does. This
    reader finds the rows to retire.

    THREE bars, and the third was missing until second eyes found it:

      1. `person_proposal` rows only — an update-type row's premise is that the
         record already exists, so a create answers nothing.
      2. The exact normalized MULTI-TOKEN name, the same bar
         `find_existing_person` Tier 2 uses. A single-token row is never
         retired from here (Bug #19: "Quinn" is not necessarily this Quinn).
      3. NAME-ONLY, which is what the docstring always claimed and nothing
         enforced. A row carrying its OWN address is a row about a specific
         person at a specific mailbox. When that address differs from the one
         this record was created from, the row is a DIFFERENT person who
         happens to share a name (a second `Quinn Marsh`, at another company),
         and closing it `person_added` against this record answers a question
         that was never asked and points the answer at the wrong human. Rows
         with no address at all are the name-only shape and retire as before;
         a row whose address MATCHES is the same person and retires too.

    BAR 3 READS BOTH ADDRESS SHAPES, because the substrate holds two and they
    are written by different families. `person_backlog_sweep._observed_email`
    is THE shared F-3 attribution reader (never re-implemented here) and it
    scans `evidence` / `source_ref` PROSE — correct for a captured mention,
    and blind to a row whose address is a structured FIELD. inbox-triage's
    promote-queue writes exactly that (`data: {name, email, promote_queue:
    true, ...}`), so reading prose alone would have called it name-only and
    retired someone else's question. The structured side now comes off the
    loader (`confirm_flow.PERSON_EMAIL_KEYS`), so both shapes answer the same
    question in one place.
    """
    from confirm_flow import load_open_person_proposals
    from person_backlog_sweep import _observed_email

    want = _norm_name(name)
    if len(want.split()) < 2:
        return []
    want_email = _norm_email(email)
    rows = load_open_person_proposals(str(_events_path(workspace_root)))
    out = []
    for r in rows:
        if r.get("type") != "person_proposal":
            continue
        if _norm_name(r.get("name")) != want:
            continue
        probe = dict(r)
        probe["name"] = r.get("name")
        # Structured field first (it is an assertion, not an inference), then
        # the prose attribution. Either one naming a DIFFERENT address is
        # enough to leave the row alone.
        row_emails = {e for e in (_norm_email(r.get("email")),
                                  _norm_email(_observed_email(probe))) if e}
        if row_emails and want_email not in row_emails:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------

def _build_contact_captured_event(*, email, name, message_id, ts,
                                  source_skill, name_source,
                                  outcome: str = "created",
                                  person_id=None, batch_id=None,
                                  org_id=None, org_reason="",
                                  email_dropped=False, thread_id=None,
                                  attempts=None, reason="") -> dict:
    """The additive marker event. It is three things at once, on purpose:

      1. the UNDO anchor — `brain_batch_id` + `brain_change_class`
         (`person_org_creation_structured_fact`, the EXISTING R1 class) is
         exactly the pair `brain_undo.recent_auto_batches` groups on and
         `_changes_for_brain_batch` reads `person_id` off, so `undo` lists and
         reverses this with no change to brain_undo at all;
      2. the IDEMPOTENCY ledger — `fingerprint`, append-only, undo-proof;
      3. the PROVENANCE record — which message this identity was observed in.

    `outcome` is what makes it a LEDGER OF ADJUDICATIONS rather than a log of
    creates, which is the point second eyes made: a same-name collision writes
    one of these too (`outcome="needs_confirm"`), because otherwise every later
    message to that address minted another identical confirm row and the CEO's
    answer re-opened itself on the next mail — an unbounded re-ask, which is
    the 43-day problem this whole build exists to end, rebuilt inside the fix.
    The address has been ADJUDICATED once; whichever way the human answers it
    (add / same-as / not relevant), asking again is wrong.

    THE UNDO KEYS ARE STAMPED ONLY ON A CREATE. A collision marker names no
    record, and `brain_undo`'s reverser for this class raises without a
    `person_id` — so stamping the pair on a non-create would put a change into
    every `undo` listing that cannot be reversed. Both keys go on together or
    neither does."""
    data = {
        "outcome": outcome,
        "fingerprint": contact_fingerprint(email),
        "email": email,
        "canonical_name": name,
        "name_source": name_source,
        "source_ref": message_id,
        "observed_ts": ts,
    }
    if thread_id:
        data["thread_ref"] = thread_id
    # Attempt bookkeeping (`deferred` / `gave_up`). `attempts` is what makes
    # the stall bound survive a restart; `reason` is what makes a give-up
    # readable by a human six weeks later instead of a bare count.
    if attempts is not None:
        data["attempts"] = int(attempts)
    if reason:
        data["reason"] = str(reason)[:300]
    event: dict = {"type": "contact_captured", "source_skill": source_skill,
                   "data": data}
    if outcome == "created":
        if not person_id or not batch_id:
            raise ContactItemError(
                "a created-contact marker needs both the person id and the "
                "batch id — without them the create is not reversible")
        data["person_id"] = person_id
        data["brain_batch_id"] = batch_id
        data["brain_change_class"] = "person_org_creation_structured_fact"
        if org_id:
            data["primary_org_id"] = org_id
        if org_reason:
            data["org_attribution"] = org_reason
        if email_dropped:
            data["email_dropped_no_provenance"] = True
        event["person_ids"] = [person_id]
    return event


def _build_prefilled_confirm_row(*, name, email, message_id, ts,
                                 source_skill) -> dict:
    """The same-name collision outcome (spec §2.3): today's person confirm row,
    now PRE-FILLED with the observed address.

    The address rides `evidence` (and `source_ref`) rather than a bespoke
    field, because that is where `person_backlog_sweep._observed_email` — THE
    F-3 attribution reader every identity surface goes through — looks for it.
    A dedicated key would have been invisible to the reader that has to use
    it."""
    evidence = (f"{name} <{email}> — you sent this address a direct message "
                f"({message_id}); an existing contact shares a name, so this "
                f"one needs your call")
    return {
        "type": "person_proposal",
        "source_skill": source_skill,
        "ts": ts or None,
        "data": {
            "name": name,
            "email": email,
            "evidence": evidence,
            "source_ref": message_id,
            "review_reason": "same-name collision on an auto contact capture",
        },
    }


def own_addresses(workspace_root) -> set:
    """Every address that IS the CEO — the declared connector accounts plus
    whatever the primary user's own record carries.

    A self-CC is correspondence with nobody. Without this the pass creates a
    duplicate record OF THE USER out of their own secondary account (second
    eyes reproduced exactly that), and a same-name collision against the user's
    own record is worse than useless: it puts the CEO in their own
    needs-your-call queue.

    `connector_config.accounts()` is the shipped, populated answer and was
    simply never asked here. Empty on a workspace that has classified nothing —
    which is honest, not a failure; the primary user's record still covers the
    main address. Read-only, never raises into the pass."""
    out: set = set()
    try:
        from connector_config import accounts

        for rec in accounts(workspace_root) or []:
            addr = _norm_email(rec.get("address"))
            if addr:
                out.add(addr)
    except Exception:
        pass
    try:
        from people_writer import get_person_emails
        from primary_user import resolve_primary_user
        from entities_io import entities_collection
        import json as _json

        uid = resolve_primary_user(workspace_root)
        if uid:
            raw = _json.loads(_entities_path(workspace_root).read_text(
                encoding="utf-8"))
            for p in entities_collection(raw, "people"):
                if p.get("id") != uid:
                    continue
                for e in get_person_emails(p):
                    e = _norm_email(e)
                    if e:
                        out.add(e)
    except Exception:
        pass
    return out


def capture_contacts(
    workspace_root,
    items,
    *,
    source_skill: str = "reconcile-sent",
    now_iso: Optional[str] = None,
    cap: int = CONTACT_CAPTURE_CAP,
    batch_id: Optional[str] = None,
    apply: bool = True,
    write_cursor: bool = True,
) -> dict:
    """Run the §2 gate over a batch of extracted contact candidates and create
    the survivors through `people_writer.auto_add_person`.

    `items` — one dict per (sent message x direct recipient):
      {"message_id", "ts" (the send time, ISO), "email", "display_name",
       "name_source" ("mail_header" | "calendar_attendee"),
       "direction" ("sent"), "recipient_field" ("to" | "cc"),
       "thread_id" (optional)}

    Order of operations per item, oldest first:
      1. cursor floor  — anything at or before the floor is not eligible, and
         an unusable timestamp is refused rather than ordered against it
      2. the §2 gate   — a refusal is recorded with the bar that refused it;
         the CEO's own addresses are dropped before anything else
      3. the capture ledger — an address ALREADY ADJUDICATED (created before,
         asked about before, or created and since undone) is SKIPPED
      4. `auto_add_person` — the FS-11 writer, unchanged. `needs_confirm`
         mints the pre-filled confirm row AND a ledger entry, so the question
         is asked exactly once; `DuplicatePersonError` is a clean skip
      5. best-effort org attribution through the existing work-domain path
      6. the `contact_captured` marker — written BEFORE the create is counted
      7. retire every open name-only queue row the new record satisfies

    THE CURSOR RULE (second eyes, MF-1). The cursor freezes at the last
    timestamp STRICTLY BEFORE the first deferred item, never at the last item
    processed. Those are the same thing only when every message carries a
    distinct timestamp, and a message with fourteen recipients produces
    fourteen items sharing ONE — so the old rule advanced the cursor to that
    timestamp after creating ten of them and excluded the other four forever,
    while the receipt said they were waiting their turn. "Carries, never
    drops" has to be true in the shape that actually occurs.

    Two outcomes DEFER: an item held back by the cap, and an item whose write
    failed in a way that might not repeat. A malformed item does NOT defer —
    the extractor has to change before it could succeed, so holding the cursor
    for it would stall the pass with no path forward. The cost of moving on is
    bounded and worth naming: THAT MESSAGE is not read again, and the
    correspondent enters on their next one.

    DEFERRAL IS BOUNDED (`MAX_DEFER_ATTEMPTS`). Deferring is right for a
    transient failure and ruinous for a permanent one — an account-scope
    refusal or a schema rejection fails identically every fire, so an unbounded
    deferral freezes the cursor forever, carries everything newer forever, and
    does it invisibly. After three consecutive fires stuck on the same address
    the pass GIVES UP LOUDLY: the item lands in `gave_up`, the cursor advances
    past it, and the receipt carries a plain-English line for the chat. Giving
    up resets the counter, so the next message from that correspondent gets a
    fresh three tries rather than a life sentence.

    `apply=False` plans and writes nothing (the receipt still reports what it
    WOULD do, and `cursor_to` stays where it was — a dry run must never make a
    job look served).

    Returns the receipt; every count comes from what was ACTUALLY written.
    """
    ws = Path(workspace_root)
    now_iso = now_iso or _now_iso()
    now_dt = usable_ts(now_iso) or _clock_now(workspace_root)
    items = [i for i in (items or []) if isinstance(i, dict)]
    # Oldest first — the carry rule needs a stable order to freeze a cursor in.
    # Sorted on the PARSED time: a mix of `Z` and offset spellings does not
    # sort correctly as strings, and this order is load-bearing.
    def _sort_key(i):
        dt = usable_ts(i.get("ts"), now_dt=now_dt)
        # Unusable timestamps sort last; they are refused, never ordered.
        return (dt is None, dt or now_dt, str(i.get("message_id") or ""))

    items.sort(key=_sort_key)
    newest_ts = None
    for i in reversed(items):
        if usable_ts(i.get("ts"), now_dt=now_dt) is not None:
            newest_ts = i.get("ts")
            break

    floor_info = capture_floor(ws, now_iso=now_iso, newest_ts=newest_ts)
    floor, floor_dt = floor_info["floor"], floor_info["floor_dt"]
    batch_id = batch_id or ("cc_" + _clock_now(workspace_root).strftime("%Y%m%dT%H%M%SZ"))

    res: dict = {
        "ran": True, "applied": bool(apply), "batch_id": batch_id,
        "cursor_from": floor if not floor_info["initialized"] else None,
        "cursor_to": floor, "cursor_initialized": floor_info["initialized"],
        "cursor_reset": floor_info["reset"],
        "backfill_days": floor_info["backfill_days"],
        "added": [], "needs_confirm": [], "skipped_existing": [],
        "refused": [], "carried": [], "errors": [], "gave_up": [],
        "n_queue_rows_retired": 0, "cap": int(cap),
    }
    if floor_info["reset"]:
        res["note"] = (f"{floor_info['reset']} — nothing older was read back")

    def _finish():
        for key, bucket in (("n_added", "added"),
                            ("n_gave_up", "gave_up"),
                            ("n_needs_confirm", "needs_confirm"),
                            ("n_skipped", "skipped_existing"),
                            ("n_refused", "refused"),
                            ("n_carried", "carried"),
                            ("n_errors", "errors")):
            res[key] = len(res[bucket])
        if not apply:
            # A dry run leaves the cursor exactly where it was.
            res["cursor_to"] = res["cursor_from"]
        elif write_cursor and res["cursor_to"] and \
                res["cursor_to"] != res["cursor_from"]:
            write_contact_cursor(ws, res["cursor_to"], source_skill=source_skill)
        return res

    # THE go-forward fence, and the ONLY one. Compared as PARSED TIMES, so a
    # cursor written `...Z` and a message stamped `...+00:00` order correctly
    # against each other. An item with no usable timestamp is not silently
    # dropped here — it falls through to the gate below and is REFUSED with a
    # named bar, so the receipt says why.
    eligible = []
    for i in items:
        dt = usable_ts(i.get("ts"), now_dt=now_dt)
        if dt is None or dt > floor_dt:
            eligible.append(i)
    if not eligible:
        return _finish()

    suspects = duplicate_suspect_surfaces(ws)
    seen_fps = _captured_fingerprints(ws)
    mine = own_addresses(ws)
    in_batch: set = set()
    events_path = _events_path(ws)

    from event_gate import append_event
    from people_writer import DuplicatePersonError, auto_add_person

    # The cursor computation. `finished` collects the timestamps of items this
    # fire is DONE with, ascending; `frozen_dt` is the timestamp of the first
    # item it is NOT done with. Everything from `frozen_dt` onward — including
    # anything sharing that exact timestamp — must remain eligible next fire.
    finished: list = []
    frozen_dt = None
    attempts = stuck_attempts(ws)
    ledger: list = []      # attempt rows for this fire, appended once at the end

    def _defer(bucket, entry, dt, *, fp=None, email=None, name=None,
               message_id=None, ts=None, name_source=""):
        """Hold the cursor for this item — UNLESS it has already stalled the
        pass `MAX_DEFER_ATTEMPTS` times, in which case give up loudly and let
        the cursor through. Both branches write an attempt row, so the count
        survives a restart and a give-up is visible in the substrate rather
        than only in a receipt nobody kept."""
        nonlocal frozen_dt
        prior = attempts.get(fp, 0) if fp else 0
        if fp and prior + 1 >= MAX_DEFER_ATTEMPTS:
            ledger.append(_build_contact_captured_event(
                email=email, name=name, message_id=message_id, ts=ts,
                source_skill=source_skill, outcome="gave_up",
                name_source=name_source, attempts=prior + 1,
                reason=str(entry.get("error") or entry.get("reason") or "")))
            res["gave_up"].append({
                "email": email, "name": name, "message_id": message_id,
                "attempts": prior + 1,
                "reason": entry.get("error") or entry.get("reason") or ""})
            finished.append(dt)
            return
        if fp:
            ledger.append(_build_contact_captured_event(
                email=email, name=name, message_id=message_id, ts=ts,
                source_skill=source_skill, outcome="deferred",
                name_source=name_source, attempts=prior + 1,
                reason=str(entry.get("error") or entry.get("reason") or "")))
        if frozen_dt is None or (dt is not None and dt < frozen_dt):
            frozen_dt = dt
        res[bucket].append(entry)

    for item in eligible:
        item_dt = usable_ts(item.get("ts"), now_dt=now_dt)
        if frozen_dt is not None:
            # Past the freeze point: everything is carried, untouched.
            res["carried"].append({"email": _norm_email(item.get("email")),
                                   "message_id": item.get("message_id"),
                                   "ts": item.get("ts")})
            continue

        if item_dt is None:
            # Unparseable or future-dated. Refused with a named bar and
            # FINISHED — the timestamp came off the message, so re-reading the
            # same message produces the same unusable value, and letting it
            # near the cursor is how the pass gets bricked (see `usable_ts`).
            # The bounded cost, stated plainly: THIS message is not looked at
            # again. The correspondent enters on their next one.
            res["refused"].append({
                "email": _norm_email(item.get("email")),
                "bar": "unusable_ts",
                "reason": "the message carried no readable send time, or one "
                          "in the future — it cannot be placed in order, so it "
                          "is not acted on",
                "message_id": item.get("message_id")})
            continue

        try:
            verdict = gate_contact_item(item, suspects=suspects,
                                        own_addresses=mine)
        except ContactItemError as exc:
            # Structurally malformed — loud, and FINISHED. The extractor would
            # have to change before this item could succeed, so deferring it
            # stalls the pass with no path forward. Not "it can never succeed":
            # the bounded, honest cost is that THIS message is not read again,
            # and the same correspondent enters on their next one.
            res["errors"].append({"message_id": item.get("message_id"),
                                  "error": str(exc), "retryable": False})
            finished.append(item_dt)
            continue

        if not verdict["ok"]:
            res["refused"].append({"email": verdict["email"],
                                   "bar": verdict["bar"],
                                   "reason": verdict["reason"],
                                   "message_id": verdict["message_id"]})
            finished.append(item_dt)
            continue

        ts = verdict["ts"]
        fp = contact_fingerprint(verdict["email"])
        if fp in seen_fps or fp in in_batch:
            res["skipped_existing"].append({"email": verdict["email"],
                                            "message_id": verdict["message_id"],
                                            "why": "already captured"})
            finished.append(item_dt)
            continue

        # The cap is checked HERE — after every non-creating outcome, so a fire
        # full of refusals never burns the budget and never freezes the cursor.
        if len(res["added"]) >= int(cap):
            # No `fp`: a cap carry is not a FAILURE, so it must not count
            # toward the give-up budget. A busy week would otherwise write off
            # the very contacts the cap exists to spread out.
            _defer("carried", {"email": verdict["email"],
                               "message_id": verdict["message_id"],
                               "ts": ts}, item_dt)
            continue

        if not apply:
            res["added"].append({"email": verdict["email"],
                                 "name": verdict["name"],
                                 "message_id": verdict["message_id"],
                                 "person_id": None, "dry_run": True})
            in_batch.add(fp)
            finished.append(item_dt)
            continue

        try:
            out = auto_add_person(
                ws,
                canonical_name=verdict["name"],
                email=verdict["email"],
                # FS-11's observed-provenance rule: the address is stored ONLY
                # with the message it surfaced from. Never a guess.
                email_provenance={"via": "sent_mail",
                                  "source_ref": verdict["message_id"],
                                  "observed_ts": ts},
                source_skill=source_skill,
            )
        except DuplicatePersonError:
            # The record is already on file (including an ARCHIVED one an undo
            # left behind) — a clean skip, never an error and never a fork.
            res["skipped_existing"].append({"email": verdict["email"],
                                            "message_id": verdict["message_id"],
                                            "why": "already on file"})
            in_batch.add(fp)
            finished.append(item_dt)
            continue
        except Exception as exc:
            # A transient write failure — a lock, a race, a disk hiccup. DEFER:
            # the item must be re-offered, and advancing past it would turn a
            # momentary failure into a permanent silent skip.
            _defer("errors", {"message_id": verdict["message_id"],
                              "email": verdict["email"],
                              "error": f"{type(exc).__name__}: {exc}",
                              "retryable": True}, item_dt,
                   fp=fp, email=verdict["email"], name=verdict["name"],
                   message_id=verdict["message_id"], ts=ts,
                   name_source=str(item.get("name_source") or "").strip().lower())
            continue

        if out.get("status") == "needs_confirm":
            # FIRST: is this a collision at all? `list_same_name_people`
            # matches on a shared name TOKEN, so it fires whenever anyone on
            # file shares a first or last name — including the record that
            # already carries THIS EXACT ADDRESS. An exact email match is the
            # one signal the shipped resolver treats as unambiguous
            # (find_existing_person Tier 1), so when a candidate already holds
            # the observed address, the name and the address agree and there is
            # nothing to ask. Asking anyway is how a record this pass created a
            # moment ago (a marker write that failed and was retried) comes
            # back as a question about itself.
            from people_writer import get_person_emails

            same_addr = None
            for m in (out.get("matches") or []):
                try:
                    if any(_norm_email(e) == verdict["email"]
                           for e in get_person_emails(m)):
                        same_addr = m
                        break
                except Exception:
                    continue
            if same_addr is not None:
                res["skipped_existing"].append({
                    "email": verdict["email"],
                    "message_id": verdict["message_id"],
                    "why": "already on file"})
                in_batch.add(fp)
                finished.append(item_dt)
                continue

            # Same-name gate fired inside the writer. Mint today's confirm row,
            # PRE-FILLED with the observed address (spec §2.3) — never a fork.
            #
            # The LEDGER ENTRY rides the same append, and it is not optional:
            # without it every later message to this address minted another
            # identical row, so the CEO's answer re-opened itself on the next
            # mail. One question, asked once. Both events land in ONE locked
            # append so the row can never exist without its ledger entry.
            try:
                append_event(events_path, [
                    _build_prefilled_confirm_row(
                        name=verdict["name"], email=verdict["email"],
                        message_id=verdict["message_id"], ts=ts,
                        source_skill=source_skill),
                    _build_contact_captured_event(
                        email=verdict["email"], name=verdict["name"],
                        message_id=verdict["message_id"], ts=ts,
                        source_skill=source_skill, outcome="needs_confirm",
                        name_source=str(item.get("name_source") or "").strip(
                            ).lower(),
                        thread_id=item.get("thread_id")),
                ], holder=source_skill)
            except Exception as exc:
                _defer("errors", {"message_id": verdict["message_id"],
                                  "error": f"{type(exc).__name__}: {exc}",
                                  "retryable": True}, item_dt,
                   fp=fp, email=verdict["email"], name=verdict["name"],
                   message_id=verdict["message_id"], ts=ts,
                   name_source=str(item.get("name_source") or "").strip().lower())
                continue
            in_batch.add(fp)
            seen_fps.add(fp)
            res["needs_confirm"].append({
                "email": verdict["email"], "name": verdict["name"],
                "message_id": verdict["message_id"],
                "matches": [m.get("canonical_name") or m.get("id")
                            for m in (out.get("matches") or [])]})
            finished.append(item_dt)
            continue

        record = out.get("record") or {}
        person_id = record.get("id")
        if not person_id:
            _defer("errors", {"message_id": verdict["message_id"],
                              "error": "writer returned no record id",
                              "retryable": True}, item_dt,
                   fp=fp, email=verdict["email"], name=verdict["name"],
                   message_id=verdict["message_id"], ts=ts,
                   name_source=str(item.get("name_source") or "").strip().lower())
            continue

        # --- best-effort org, NEVER blocking -------------------------------
        org_id, org_reason = None, ""
        try:
            from org_writer import attribute_person_to_org

            domain = verdict["email"].rsplit("@", 1)[-1]
            # NO org_hint: the create-an-org branch of that function is
            # unreachable from here by construction. Free-mail is filtered
            # inside it via identity_reconcile.is_free_mail_domain, so a
            # personal address never attributes an org.
            org, org_reason = attribute_person_to_org(
                ws, person_id, work_domains=[domain],
                source_skill=source_skill)
            org_id = (org or {}).get("id")
        except Exception:
            org_id, org_reason = None, ""

        # --- the marker, BEFORE the create is counted ----------------------
        # Ordering is the fix, not decoration. The marker is the undo anchor
        # AND the idempotency ledger, so a create counted without one is a
        # record the receipt claims, `undo` cannot see, and the next fire
        # re-proposes. Writing it first means a failed append leaves the item
        # in `errors` and DEFERRED — re-offered next fire, where the record now
        # on file makes the retry a clean skip rather than a duplicate.
        try:
            append_event(events_path, [_build_contact_captured_event(
                email=verdict["email"], name=verdict["name"],
                message_id=verdict["message_id"], ts=ts,
                source_skill=source_skill, outcome="created",
                person_id=person_id, batch_id=batch_id,
                name_source=str(item.get("name_source") or "").strip().lower(),
                org_id=org_id, org_reason=org_reason,
                email_dropped=bool(out.get("email_dropped_no_provenance")),
                thread_id=item.get("thread_id"))], holder=source_skill)
        except Exception as exc:
            _defer("errors", {"message_id": verdict["message_id"],
                              "email": verdict["email"],
                              "error": f"the contact record was created but "
                                       f"could not be recorded as reversible: "
                                       f"{type(exc).__name__}: {exc}",
                              "retryable": True}, item_dt,
                   fp=fp, email=verdict["email"], name=verdict["name"],
                   message_id=verdict["message_id"], ts=ts,
                   name_source=str(item.get("name_source") or "").strip().lower())
            continue

        # --- retire the queue rows this record answers ---------------------
        retired = 0
        try:
            from confirm_flow import build_person_proposal_resolved_event

            tombs = []
            for row in open_rows_satisfied_by(ws, verdict["name"],
                                              email=verdict["email"]):
                seq = row.get("seq")
                kwargs = {}
                if not (isinstance(seq, int) and not isinstance(seq, bool)):
                    seq = None
                    kwargs["proposal_fingerprint"] = row.get("fingerprint")
                    if not kwargs["proposal_fingerprint"]:
                        continue
                tomb = build_person_proposal_resolved_event(
                    seq, resolution="person_added", source_skill=source_skill,
                    person_id=person_id,
                    note=f"contact capture {batch_id} — created from your own "
                         f"sent mail", **kwargs)
                # Batch-stamped so ONE `undo` reopens the rows alongside the
                # archive. The class stays the registered tombstone reverser's.
                tomb["data"]["brain_batch_id"] = batch_id
                tomb["data"]["brain_change_class"] = "person_proposal_tombstone"
                tombs.append(tomb)
            if tombs:
                append_event(events_path, tombs, holder=source_skill)
                retired = len(tombs)
        except Exception as exc:
            # The record and its marker both landed; only the tidy-up failed.
            # Not retryable-by-deferral: re-offering would hit the create's own
            # dedup and skip, so the rows would still be open. Loud instead.
            res["errors"].append({"message_id": verdict["message_id"],
                                  "error": f"queue retirement: "
                                           f"{type(exc).__name__}: {exc}",
                                  "retryable": False})

        res["n_queue_rows_retired"] += retired
        res["added"].append({"email": verdict["email"], "name": verdict["name"],
                             "person_id": person_id,
                             "message_id": verdict["message_id"],
                             "org_id": org_id,
                             "queue_rows_retired": retired,
                             "email_dropped_no_provenance":
                                 bool(out.get("email_dropped_no_provenance"))})
        in_batch.add(fp)
        seen_fps.add(fp)
        finished.append(item_dt)

    # THE CARRY RULE. The cursor stops at the last timestamp STRICTLY BEFORE
    # the first deferred item — not at the last item processed. Items sharing
    # one timestamp (a message with many recipients) therefore move together
    # or not at all: advancing TO a shared timestamp would exclude every
    # sibling still waiting, because eligibility is strictly-newer-than.
    if frozen_dt is None:
        res["cursor_to"] = _iso(finished[-1]) if finished else floor
    else:
        safe = [d for d in finished if d < frozen_dt]
        res["cursor_to"] = _iso(max(safe)) if safe else floor

    # The attempt rows land LAST and in ONE append: they are bookkeeping, not
    # outcomes, and a fire that crashed mid-loop should not leave a partial
    # count that pushes the next fire toward giving up early.
    if apply and ledger:
        try:
            append_event(events_path, ledger, holder=source_skill)
        except Exception as exc:  # bookkeeping must never sink the fire
            print(f"contact capture: attempt ledger not written: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
    return _finish()

__all__ = [
    "CONTACT_CAPTURE_CAP",
    "CONTACT_BACKFILL_DAYS",
    "CURSOR_KEY",
    "CURSOR_MAX_LOOKBACK_DAYS",
    "CLOCK_SKEW_TOLERANCE_SECONDS",
    "MAX_DEFER_ATTEMPTS",
    "usable_ts",
    "own_addresses",
    "stuck_attempts",
    "STRUCTURED_NAME_SOURCES",
    "DIRECT_RECIPIENT_FIELDS",
    "ContactItemError",
    "contact_fingerprint",
    "read_contact_cursor",
    "stamp_contact_cursor",
    "write_contact_cursor",
    "capture_floor",
    "clarity_name_ok",
    "duplicate_suspect_surfaces",
    "gate_contact_item",
    "already_captured",
    "open_rows_satisfied_by",
    "capture_contacts",
]
