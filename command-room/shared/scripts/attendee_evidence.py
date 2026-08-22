#!/usr/bin/env python3
"""attendee_evidence — attendee lists as identity EVIDENCE (SPEC ATTENDEE1).

WHAT THIS EXISTS TO DO
======================
PERSONLOOP1 closed the person loop but kept a human tap on every person, and
it was right to: a transcript spelling must never become a contact on its own.
The evidence that settles most of those questions is upstream of the
transcript — a calendar/Granola attendee record carrying a FULL NAME and an
EMAIL ADDRESS. That is not a guess about who was in the room; it is the
strongest identity evidence this workspace ever sees, and today it is thrown
away at ingest and then re-asked as a proposal.

THE BAR, AND IT IS THE WHOLE DESIGN (§0-2)
==========================================
A name is EVIDENCED when, and only when:

  1. it exact-normalizes (`person_candidates.name_key` — casefold + whitespace
     collapse, NOTHING else) onto the name of an attendee record, AND
  2. that same attendee record carries an email address, AND
  3. the attendee record belongs to the SOURCE MEETING the capture came from.

A name-only attendee match is NOT evidence. It falls through to the existing
PERSONLOOP1 propose path, byte-identically. There is deliberately NO fuzzy or
phonetic matching here: the alias graph handles spellings AFTER a record
exists, exactly as it does today, and a resolver that guesses at identity is
the one thing this whole family of code refuses to be.

⚠ THE PAIR IS THE SCARCE THING — READ THIS BEFORE ADDING A PROVIDER
===================================================================
The shipped `meeting` event does NOT carry name+email PAIRS and never has.
`meeting_capture.build_meeting_event` splits its input into two FLAT, PARALLEL,
UNALIGNED lists (BUG-8244):

    data.attendees[]           invitee EMAILS, verbatim, lowercased
    data.attendees_external[]  display NAMES with no entities match

and `meeting_discovery.normalize_meeting` collapses a backend participant to a
SINGLE token (`_participant_token`: email or id or name — whichever it finds
first). Both transforms are load-bearing for their own readers and neither is
wrong; but between them the name↔email correspondence is DESTROYED before
anything is persisted. Nothing on disk can be re-paired: the two lists differ
in length and their order carries no relationship.

Consequences, and they are the reason this module is shaped the way it is:

  * `attendee_records_from_meeting_event` — the SUBSTRATE provider — therefore
    yields records that carry a name OR an email, never both. Under the bar
    above, that provider structurally produces ZERO evidence. It is shipped
    anyway, and deliberately: it is the honest reading of what the substrate
    holds, it keeps a caller from hand-rolling a worse one, and the day a
    pair-bearing field is persisted it becomes live with no call-site change.
  * The pair DOES exist at the connector (a Granola participant record reads
    `Name (note creator) from Org <email>`). So the evidence-bearing provider
    is an INJECTED one: the caller that already fetched the meeting hands the
    records in. `normalize_attendee_records` accepts every shape observed —
    dicts, that prose form, and plain strings — and PRESERVES the pair.

Everything above the write line is PURE: no clock is read, no substrate is
touched, nothing is cached. `find_evidence` is a function of its arguments.

THE TWO FENCES
==============
1. **AN IGNORED NAME IS NEVER AUTO-CREATED, AND THE IGNORE WINS OVER THE
   EVIDENCE — INCLUDING WHEN THE LEDGER CANNOT BE READ.** PERSONLOOP1's
   suppression ledger says "stop asking me about this name"; a build that
   answered the question by itself anyway would have read a user's explicit
   "no" as permission. So `seed_person_from_evidence` consults the ledger
   BEFORE anything else and returns `suppressed` without calling a writer.

   The read goes through `person_candidates.load_suppressions_checked`, NOT
   `load_suppressions`. That is the whole point of review F-1: the plain
   loader catches `Exception` in both of its blocks and always returns a set,
   and the realistic corruption shape does not even raise — `events_io.
   _iter_file` skips unparseable interior lines silently and by design. So a
   suppression sitting on a clobbered line simply disappeared, and this fence
   read it as consent. It now requires the ledger to have been read
   COMPLETELY; a ledger that was not reads as IGNORED.

   ONE EXCEPTION, DELIBERATE: an ABSENT ledger is a clean read, not
   corruption. A workspace with no events.jsonl has nothing suppressed, so
   there is no decision to honour; failing closed there would disable the rail
   on every fresh install and buy no safety. Corruption is "the file is there
   and I cannot vouch for what it says" — only that spends a user's "no".

2. **NO ALIASES ON THE AUTO RAIL** — and that is a deliberate, load-bearing
   omission, not a shortcut. See `WHY NO ALIASES` below.

WHY NO ALIASES (deviation from §3-2/§3-3, and the house already ruled it)
=========================================================================
`people_writer` has `add_person_alias` and NO removal path, so every alias an
auto rail writes is a write its reverser cannot take back — an undo that
leaves residue is not a reversal. AUTOAPPLY §4a hit exactly this and answered
it the same way (`brain_undo._reverse_person_link`: "the auto path writes NO
alias, so the record itself is already alias-free ... precisely so this
reverser stays COMPLETE"), and `contact_capture` writes none either.

Here the omission also costs nothing measurable, which is why it is safe as
well as consistent: the bar is EXACT-NORMALIZED name match, and
`entity_resolve._normalize` (lowercase + whitespace collapse) is the SAME
normalization as `person_candidates.name_key`. So every spelling that could
have become an alias under this bar differs from the canonical name only by
case or whitespace — and resolves through `exact_canonical` with no alias at
all. An alias here would buy a resolution that already works, at the price of
an incomplete undo.

stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

SOURCE_SKILL = "meeting-notes"

# §3-3 — at most this many auto-creations per fire, on any surface. A
# backlogged workspace drains over days rather than minting a folder of
# contacts in one silent pass. Deliberately larger than PERSONLOOP1's
# PROPOSAL_CAP (2): a proposal costs the reader attention, and an evidenced
# creation costs them nothing but a receipt line they can undo.
AUTO_CREATE_CAP = 3

# The change class the creations are stamped with. NOT a new class: R1 (M
# ruling 2026-07-14) defines this one as "identity from a STRUCTURED CONNECTOR
# FACT (full name + address from mail/CALENDAR), zero same-name/email
# collision, past the noise gate — additive only", which is precisely what an
# attendee record carrying a name and an email is. Its archive-never-delete
# reverser is already registered in `brain_undo.REVERSERS`, so undo works with
# no new reverser, no new policy row and no new event type.
CHANGE_CLASS = "person_org_creation_structured_fact"

# The class the DRAINED CLEARS are stamped with, so one `undo` reopens them
# alongside the record. Also not new: `commitment_confirm`'s registered
# reverser routes through `needs_review_queue.undo_confirm_items` ->
# `restore_review_flags`, the additive mirror of the `clear_review_flags`
# event the drain writes. Same event, same reverser, whichever gesture wrote
# it.
CLEAR_CHANGE_CLASS = "commitment_confirm"

BATCH_PREFIX = "attev_"

# The connector's prose participant form, e.g.
#   "Sam Sample (note creator) from Acme Co <sam@acme.example.com>"
# Captured as (label, email); the label is then stripped of its parenthetical
# and of a trailing "from <org>" clause.
_PROSE_ENTRY_RE = re.compile(r"([^,<]+?)\s*<([^>]+)>")
_PAREN_RE = re.compile(r"\([^)]*\)")
_FROM_RE = re.compile(r"\bfrom\b", re.IGNORECASE)

_NAME_KEYS = ("name", "display_name", "displayName", "full_name",
              "canonical_name", "label")
_EMAIL_KEYS = ("email", "email_address", "emailAddress", "address", "mail")

# A name has to look like a NAME. One token is not an identity (`contact_
# capture.clarity_name_ok` refuses a bare first name for the same reason: a
# solo first name identifies nobody), and annotation markers mean the source
# was guessing.
_ANNOTATION_RE = re.compile(r"[\d()\[\]/\\|]")


def _txt(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _email_ok(value) -> bool:
    """Is this a usable address? Deliberately shallow — the address is
    EVIDENCE that the source knew who this was, and it is stored with observed
    provenance by the writer; this is not an RFC validator."""
    v = _txt(value).lower()
    return "@" in v and "." in v.split("@")[-1] and " " not in v


def _name_ok(value) -> bool:
    """Multi-token, annotation-free. A one-token attendee label is not an
    identity, and `Speaker 2` / `att-7 (guest)` are the source telling you it
    did not know."""
    v = _txt(value)
    if not v or _ANNOTATION_RE.search(v):
        return False
    return len(v.split()) >= 2


def email_domain(email) -> str:
    """The domain half, lowercased, or "". PROPOSED org evidence only — §0-5
    is explicit that a domain never ASSERTS an org link, and nothing in this
    module writes one."""
    v = _txt(email).lower()
    return v.split("@", 1)[1] if "@" in v else ""


# ---------------------------------------------------------------------------
# Normalization — every observed attendee shape, with the PAIR preserved
# ---------------------------------------------------------------------------

def _from_prose(raw: str) -> list:
    out = []
    for m in _PROSE_ENTRY_RE.finditer(raw or ""):
        label, email = m.group(1), m.group(2)
        label = _PAREN_RE.sub(" ", label)
        label = _FROM_RE.split(label)[0]
        name = " ".join(label.split()).strip(" ,;")
        out.append({"name": name, "email": _txt(email)})
    return out


def normalize_attendee_records(raw) -> tuple:
    """One backend attendee blob -> `({"name","email"}, ...)`, PAIR PRESERVED.

    Accepts, because all three shapes are observed in the wild:
      * a list of dicts — `{"name"|"display_name"|..., "email"|"address"|...}`
      * the connector's prose block — `"A Name from Org <a@x>, B <b@y>"`
      * a list of plain strings — an "@" makes it an email, otherwise a name

    A record may legally carry a name with no email or an email with no name;
    the BAR (not this function) is what refuses those. Order is preserved and
    exact duplicates are folded. Idempotent: re-normalizing its own output
    returns it unchanged.

    PURE. No clock, no substrate, no cache.
    """
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)):
        items = _from_prose(raw if isinstance(raw, str) else raw.decode(
            "utf-8", "replace"))
    elif isinstance(raw, dict):
        items = [raw]
    else:
        try:
            items = list(raw)
        except TypeError:
            return ()

    out: list = []
    seen: set = set()
    for item in items:
        if isinstance(item, dict):
            name = next((_txt(item.get(k)) for k in _NAME_KEYS
                         if _txt(item.get(k))), "")
            email = next((_txt(item.get(k)) for k in _EMAIL_KEYS
                          if _txt(item.get(k))), "")
            # A dict whose only name-ish field holds an address is the
            # emails-as-names drift `build_meeting_event` refuses too.
            if name and "@" in name and not email:
                name, email = "", name
            recs = [{"name": name, "email": email}]
        elif isinstance(item, str):
            recs = _from_prose(item) if "<" in item and ">" in item else (
                [{"name": "", "email": _txt(item)}] if "@" in item
                else [{"name": _txt(item), "email": ""}])
        else:
            continue
        for rec in recs:
            name, email = _txt(rec.get("name")), _txt(rec.get("email"))
            if not name and not email:
                continue
            key = (name.casefold(), email.casefold())
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "email": email})
    return tuple(out)


def attendee_records_from_meeting_event(ev) -> tuple:
    """THE SUBSTRATE PROVIDER — and it structurally yields NO evidence.

    Read the module header before touching this. A shipped `meeting` event
    holds emails in `data.attendees` and names in `data.attendees_external`,
    two flat lists whose correspondence was destroyed by the writer. So this
    returns name-only and email-only records, never a pair, and every one of
    them fails the bar.

    That is the correct behaviour, not a gap to paper over. Pairing the two
    lists positionally would INVENT a name↔email correspondence the substrate
    does not contain — the resolver would then auto-create a contact off an
    alignment nobody observed, which is a worse failure than creating none.
    Shipped so a caller with only substrate in hand gets an honest empty
    answer through the same seam.

    PURE.
    """
    d = ev.get("data") if isinstance(ev, dict) and isinstance(
        ev.get("data"), dict) else {}
    raw: list = []
    for name in (d.get("attendees_external") or []):
        if isinstance(name, str) and "@" not in name:
            raw.append({"name": name, "email": ""})
    for key in ("attendees", "attendee_emails", "invitees"):
        for addr in (d.get(key) or []):
            if isinstance(addr, str) and "@" in addr:
                raw.append({"name": "", "email": addr})
    return normalize_attendee_records(raw)


# ---------------------------------------------------------------------------
# THE BAR (pure)
# ---------------------------------------------------------------------------

def find_evidence(name, attendee_records) -> dict:
    """Does `name` meet the §0-2 bar against these attendee records?

    Returns `{"matched", "attendee_name", "email", "org_domain", "why"}`.
    `matched` is True ONLY for an exact-normalized name match to a record that
    ALSO carries an email — the two halves are checked on the SAME record, so
    a name matched on one row can never borrow an address from another.

    `why` names the half that refused, so a receipt or a test explains itself
    without a second classification pass.

    PURE. No clock, no substrate.
    """
    from person_candidates import name_key

    out = {"matched": False, "attendee_name": "", "email": "",
           "org_domain": "", "why": ""}
    key = name_key(name)
    if not key:
        out["why"] = "no name to match"
        return out

    name_hit = False
    matches: list = []
    for rec in (attendee_records or ()):
        if not isinstance(rec, dict):
            continue
        cand = _txt(rec.get("name"))
        if name_key(cand) != key:
            continue
        # The NAME half has to stand on its own before the email can settle
        # anything: a one-token or annotated attendee label is the source
        # saying it did not know who this was.
        if not _name_ok(cand):
            out["why"] = "attendee name is not a usable identity"
            continue
        name_hit = True
        email = _txt(rec.get("email"))
        if not _email_ok(email):
            continue
        matches.append((cand, email))

    # ⚠ AMBIGUITY REFUSES (review F-2). EVERY name-key match is collected
    # before anything is decided, because returning the FIRST one made the
    # created contact's address ORDER-DEPENDENT: two different humans spelled
    # the same way, in one meeting, produced a permanent record whose email
    # depended on the order the connector happened to list them in, and the
    # reviewer demonstrated exactly that by reversing the list.
    #
    # Two distinct addresses under one name is not weak evidence, it is a
    # QUESTION — and picking one of two is precisely the "guess at identity"
    # this module refuses to make. The house already rules this way on the
    # adjacent path: `resolve_candidate` treats MultipleCandidatesError as a
    # human decision (Bug #19), never an auto-route. So this falls through to
    # the existing propose path with nothing written.
    #
    # DISTINCT is measured CASEFOLDED, which is what keeps the legitimate case
    # working: one person listed twice with the same address in different case
    # is ONE address, still qualifies, and still creates one record.
    distinct = {e.casefold() for _, e in matches}
    if len(distinct) > 1:
        out["why"] = ("two attendees of this meeting share this name and "
                      "carry different email addresses — that is a question, "
                      "not evidence")
        return out
    if matches:
        cand, email = matches[0]
        out.update({"matched": True, "attendee_name": cand,
                    "email": email.lower(),
                    "org_domain": email_domain(email),
                    "why": "attendee record carries a name and an email"})
        return out
    out["why"] = ("attendee matched by name only — no email on that record"
                  if name_hit else "no attendee of this meeting by that name")
    return out


def evidence_for_capture(name, *, source_ref, records_by_ref) -> dict:
    """The bar, scoped to the capture's OWN source meeting (§0-1(b)).

    The scoping is not incidental. A name+email attendee of SOME meeting says
    nothing about a capture that came out of a different one, and matching
    across the whole history is how a shared first name becomes the wrong
    contact. So only `records_by_ref[source_ref]` is consulted — one meeting,
    the one this capture is a record of.

    PURE.
    """
    ref = _txt(source_ref)
    if not ref:
        return {"matched": False, "attendee_name": "", "email": "",
                "org_domain": "", "why": "capture carries no source_ref",
                "meeting_ref": ""}
    records = (records_by_ref or {}).get(ref)
    out = find_evidence(name, normalize_attendee_records(records))
    out["meeting_ref"] = ref
    if not records:
        out["why"] = "no attendee records for this meeting"
    return out


# ---------------------------------------------------------------------------
# The fence (reads the ledger, writes nothing)
# ---------------------------------------------------------------------------

def is_ignored(workspace_root, name, *, org_id=None,
               suppressed: Optional[Iterable[str]] = None,
               ledger_ok: Optional[bool] = None) -> bool:
    """Has the user said "not a person" about this name? (§3-2 fence.)

    THE IGNORE WINS OVER THE EVIDENCE, always — and over a ledger we cannot
    read, which is the harder half.

    `person_candidates.load_suppressions` fails OPEN (an unreadable log yields
    an empty set) because for the PROPOSE path one extra question is the right
    cost of doubt. Here the direction inverts: doubt must not become a WRITE.
    So this reads through `load_suppressions_checked`, which reports whether
    the ledger was read COMPLETELY, and a ledger that was not reads as
    IGNORED. Nothing is lost when that happens except an automatic decision
    nobody could verify — the propose path still surfaces the name.

    ⚠ REVIEW F-1 — THIS USED TO BE A CLAIM, NOT A FENCE. The previous version
    called `load_suppressions`, which catches `Exception` in both of its
    blocks and always returns a set: it never raises and never returns None,
    so this function's `except: return True` and `suppressed is None: return
    True` were unreachable on every production path. And the realistic
    corruption shape never raises anyway — `events_io._iter_file` skips
    unparseable interior lines silently and by design — so a suppression event
    on a clobbered line vanished and the fire auto-created the suppressed
    person, receipt and all. Proved end to end by the reviewer against three
    corruption shapes. The fence is now measured rather than asserted.

    `suppressed` / `ledger_ok` let ONE caller read the ledger once per pass and
    hand both halves down. They travel together: supplying keys without the
    readability verdict is refused, because "here are the keys, never mind
    where they came from" is precisely the hole this finding closed.
    """
    from person_candidates import (is_suppressed, load_suppressions_checked,
                                   name_key)

    key = name_key(name)
    if not key:
        return True
    if suppressed is None:
        suppressed, ledger_ok = load_suppressions_checked(workspace_root)
    elif ledger_ok is None:
        raise ValueError(
            "is_ignored got pre-loaded suppression keys with no `ledger_ok` "
            "verdict. The two travel together: a caller that reads the ledger "
            "itself must also say whether it could read it, or an unreadable "
            "ledger silently becomes an empty one — review F-1.")
    if not ledger_ok:
        return True
    return is_suppressed(suppressed, key, org_id)


# ---------------------------------------------------------------------------
# The write — ONE call, and it is the only thing here that writes
# ---------------------------------------------------------------------------

def new_batch_id(now_iso: str) -> str:
    """`attev_<stamp>` from the caller's OWN clock reading. No clock is read
    in this module (G14): the fire hands its `now` down, so a fixture can pin
    a batch id exactly and nothing here drifts with wall time."""
    stamp = re.sub(r"[^0-9A-Za-z]", "", _txt(now_iso)) or "0"
    return f"{BATCH_PREFIX}{stamp}"


def seed_person_from_evidence(workspace_root, *, name, evidence,
                              batch_id: str,
                              source_ref: str = "",
                              org_id=None,
                              now_iso: Optional[str] = None,
                              source_skill: str = SOURCE_SKILL,
                              suppressed: Optional[Iterable[str]] = None,
                              ledger_ok: Optional[bool] = None) -> dict:
    """Create ONE person from attendee evidence. The single write path here.

    Order of business, and the order is the safety:
      1. the evidence must actually have matched (`evidence["matched"]`);
      2. the IGNORE FENCE — a suppressed name returns `suppressed` and NO
         writer is called;
      3. `people_writer.auto_add_person`, unforked, which brings its own two
         guardrails: the same-name dedup gate (a collision returns
         `needs_confirm` and creates nothing — Bug #19 is a human decision)
         and observed-provenance email capture (the address is stored ONLY
         because it arrived with the meeting it was observed in).

    The record is stamped `brain_batch_id` + `brain_change_class` so ONE
    `undo` archives it through the reverser already registered for this class.
    NO ALIAS IS WRITTEN — see WHY NO ALIASES in the module header.

    NO ORG IS ASSERTED (§0-5). `org_domain` travels back on the result as
    PROPOSED evidence for a later human-gated link; `primary_org_id` is passed
    through only when the CALLER already resolved one, and never derived from
    a domain here.

    Returns {"status", "person_id", "canonical_name", "email",
             "org_domain", "detail"} with status one of:
    created / suppressed / no_evidence / needs_confirm / already_on_file /
    error.
    """
    out = {"status": "error", "person_id": None, "canonical_name": "",
           "email": "", "org_domain": "", "detail": ""}
    ev = evidence or {}
    if not ev.get("matched"):
        out["status"] = "no_evidence"
        out["detail"] = str(ev.get("why") or "the bar was not met")
        return out

    canonical = _txt(ev.get("attendee_name")) or _txt(name)
    email = _txt(ev.get("email"))
    out["canonical_name"] = canonical
    out["email"] = email
    out["org_domain"] = _txt(ev.get("org_domain"))

    # FENCE 2 (§3-2). Checked on BOTH the capture's spelling and the
    # attendee's, because an ignore is about the human, and the two strings
    # normalize the same way under the bar but need not be the same string.
    # An unreadable ledger reads as IGNORED here (review F-1) — `is_ignored`
    # owns that verdict and this call site cannot opt out of it.
    for candidate in {name, canonical}:
        if is_ignored(workspace_root, candidate, org_id=org_id,
                      suppressed=suppressed, ledger_ok=ledger_ok):
            out["status"] = "suppressed"
            out["detail"] = ("the user set this name aside, or the ignore "
                             "ledger could not be read — either way an "
                             "ignore wins over attendee evidence")
            return out

    from people_writer import DuplicatePersonError, auto_add_person

    provenance = {"via": "meeting_attendee", "source_ref": _txt(source_ref)}
    if now_iso:
        provenance["observed_ts"] = _txt(now_iso)
    try:
        res = auto_add_person(
            workspace_root,
            canonical_name=canonical,
            email=email,
            email_provenance=provenance,
            source_skill=source_skill,
            brain_batch_id=batch_id,
            brain_change_class=CHANGE_CLASS,
            needs_enrichment=True,
            **({"primary_org_id": org_id} if org_id else {}),
        )
    except DuplicatePersonError as exc:
        # Somebody is already on file. Nothing to create, and nothing went
        # wrong — the capture will resolve against them.
        out["status"] = "already_on_file"
        out["detail"] = str(exc)
        return out
    except (ValueError, KeyError) as exc:
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out

    if res.get("status") == "needs_confirm":
        # The same-name gate spoke. NEVER auto-route a collision.
        out["status"] = "needs_confirm"
        out["detail"] = ("an existing contact shares a name token — that is a "
                         "human decision, not an automatic one")
        return out
    record = res.get("record") or {}
    out["status"] = "created"
    out["person_id"] = record.get("id")
    if res.get("email_dropped_no_provenance"):
        out["detail"] = "email not stored (no observed provenance)"
    return out


# ---------------------------------------------------------------------------
# The ingest seam's item pass (§3-2)
# ---------------------------------------------------------------------------

def _unresolved_capture_names(item, workspace_root=None) -> list:
    """The names on ONE capture item that person-resolution FAILED on.

    Mirrors exactly the two conditions `capture_gate.gate_commitment_data`
    stamps on — read from that function, never re-derived from the reason
    string — so this seam can only ever act where the stamp would have fired:

      * a counterparty NAME with no counterparty ID  ->  "counterparty 'X'
        has no person record"
      * a promise with no `owner_id`, whose subject is named on
        `owner_external`  ->  "no resolved owner"

    F-28 THREADED (guard `run_guard_f28_workspace_threading_test.py`).
    `workspace_root` reaches `counterparty_names`, which then drops a name
    resolving exactly to an id the item already carries.

    BE HONEST ABOUT WHAT THAT BUYS HERE: nothing observable. F-28's filter
    only engages when the item carries ids, and this branch requires
    `not cp_ids` to fire at all, so the threaded and un-threaded readings are
    identical for every item this seam can act on. It is threaded because the
    guard pins the SHAPE across all roster readers deliberately — it has to
    catch call sites nobody has written yet — and because relying on "our
    branch happens to make it moot" is how a fence goes inert the day the
    branch changes. Compliance, not a behaviour claim.

    Returns `[(role, name), ...]`. Anything else on the item is untouched, so
    a capture the gate would have passed clean is not inspected at all.
    """
    from commitment_parties import counterparty_ids, counterparty_names

    if not isinstance(item, dict):
        return []
    out: list = []
    cp_ids = counterparty_ids(item)
    cp_names = counterparty_names(item, workspace_root=workspace_root)
    if cp_names and not cp_ids:
        for nm in cp_names:
            if _txt(nm):
                out.append(("counterparty", _txt(nm)))
    if str(item.get("kind") or "").strip() == "promise" \
            and not _txt(item.get("owner_id")):
        owner = _txt(item.get("owner_external"))
        if owner:
            out.append(("owner", owner))
    return out


def seed_people_for_items(items, *, workspace_root, source_ref,
                          attendee_records=None, records_by_ref=None,
                          now_iso: Optional[str] = None,
                          batch_id: Optional[str] = None,
                          cap: int = AUTO_CREATE_CAP,
                          source_skill: str = SOURCE_SKILL) -> dict:
    """§3-2 — THE INGEST SEAM. Patch the items whose identity the attendee
    list settles, BEFORE the pending stamp would be minted.

    NO ATTENDEE RECORDS MEANS BYTE-IDENTICAL. With `attendee_records` and
    `records_by_ref` both empty this returns the SAME item dicts, by identity,
    and an empty `created` list — nothing is copied, nothing is consulted, no
    writer is imported. That is the "no-match = exactly today" guarantee, and
    the suite asserts it on object identity rather than equality.

    WHY IT PATCHES THE ITEM AND NOT THE STAMP. `capture_gate.
    gate_commitment_data` is the safety inversion: pending_review defaults ON
    whenever attribution is not confidently resolved, and absence of the flag
    is not consent. This seam does not touch it, weaken it, or teach it a new
    exemption — `capture_gate.py` is byte-identical on this branch. It supplies
    the RESOLUTION the gate was looking for and could not find, by filling in
    `counterparty_id` / `owner_id` from a record that now exists. The gate then
    mints, or declines to mint, on exactly its own shipped rules. A capture
    that still fails resolution still routes pending, exactly as today.

    Returns {"items", "created", "n_created", "n_suppressed", "n_no_evidence",
             "n_needs_confirm", "n_patched", "batch_id", "receipt_lines"}.
    `items` is a NEW list when anything was patched (the patched entries are
    shallow copies — the caller's dicts are never mutated) and the ORIGINAL
    list object when nothing was.
    """
    staged = list(items or [])
    by_ref = dict(records_by_ref or {})
    if attendee_records is not None and _txt(source_ref):
        by_ref.setdefault(_txt(source_ref), attendee_records)
    if not by_ref:
        return {"items": items if items is not None else [], "created": [],
                "n_created": 0, "n_suppressed": 0, "n_no_evidence": 0,
                "n_needs_confirm": 0, "n_already_on_file": 0, "n_patched": 0,
                "batch_id": None, "receipt_lines": []}

    from person_candidates import load_suppressions_checked, name_key

    # ONE ledger read for the whole pass, and it carries its own verdict. A
    # per-item read would give N different answers to one question if the log
    # moved mid-fire; a read without the verdict would turn an unreadable
    # ledger into an empty one, which is review F-1 exactly.
    suppressed, ledger_ok = load_suppressions_checked(workspace_root)

    bid = batch_id or new_batch_id(now_iso or "")
    created: list = []
    # name_key -> person_id, so two captures naming one person in one fire
    # create ONE record and both get patched. Without it the second create
    # hits the writer's dedup and the second capture stays pending on a
    # person that now exists.
    minted: dict = {}
    n_suppressed = n_no_evidence = n_needs_confirm = 0
    # Review F-4 — `already_on_file` gets its OWN counter. It used to fall
    # into `n_no_evidence`, which telemetered as `n_evidence_absent`: the
    # evidence was present and good, the person was simply already there.
    # A count that says the opposite of what happened is a count nobody can
    # tune on.
    n_already_on_file = 0
    patched_idx: dict = {}

    for idx, item in enumerate(staged):
        if not isinstance(item, dict):
            continue
        ref = _txt(item.get("source_ref")) or _txt(source_ref)
        patch: dict = {}
        for role, raw_name in _unresolved_capture_names(item, workspace_root):
            key = name_key(raw_name)
            pid = minted.get(key)
            if pid is None:
                if len(created) >= max(0, int(cap)):
                    continue
                ev = evidence_for_capture(raw_name, source_ref=ref,
                                          records_by_ref=by_ref)
                if not ev.get("matched"):
                    n_no_evidence += 1
                    continue
                res = seed_person_from_evidence(
                    workspace_root, name=raw_name, evidence=ev,
                    batch_id=bid, source_ref=ref, now_iso=now_iso,
                    source_skill=source_skill, suppressed=suppressed,
                    ledger_ok=ledger_ok)
                status = res.get("status")
                if status == "suppressed":
                    n_suppressed += 1
                    continue
                if status == "needs_confirm":
                    n_needs_confirm += 1
                    continue
                if status == "already_on_file":
                    n_already_on_file += 1
                    continue
                if status != "created" or not res.get("person_id"):
                    n_no_evidence += 1
                    continue
                pid = res["person_id"]
                minted[key] = pid
                created.append({
                    "person_id": pid,
                    "canonical_name": res.get("canonical_name") or raw_name,
                    "org_domain": res.get("org_domain") or "",
                    "meeting_ref": ref,
                    "batch_id": bid,
                })
            if role == "counterparty":
                patch["counterparty_id"] = pid
            else:
                patch["owner_id"] = pid
        if patch:
            patched_idx[idx] = patch

    if not patched_idx:
        return {"items": items if items is not None else [], "created": created,
                "n_created": len(created), "n_suppressed": n_suppressed,
                "n_no_evidence": n_no_evidence,
                "n_needs_confirm": n_needs_confirm,
                "n_already_on_file": n_already_on_file, "n_patched": 0,
                "batch_id": bid if created else None,
                "receipt_lines": receipt_lines(created)}

    out_items = list(staged)
    for idx, patch in patched_idx.items():
        merged = dict(out_items[idx])
        merged.update(patch)
        # The name STAYS beside the id it resolved to. `counterparty_names`
        # drops a name that exactly resolves to an id already present (F-28),
        # so the roster reads clean, and the as-heard spelling survives in
        # history — which is what a later alias decision would need.
        out_items[idx] = merged
    return {"items": out_items, "created": created, "n_created": len(created),
            "n_suppressed": n_suppressed, "n_no_evidence": n_no_evidence,
            "n_needs_confirm": n_needs_confirm,
            "n_already_on_file": n_already_on_file,
            "n_patched": len(patched_idx),
            "batch_id": bid, "receipt_lines": receipt_lines(created)}


# ---------------------------------------------------------------------------
# Receipt + telemetry (§0-4, §3-5)
# ---------------------------------------------------------------------------

def receipt_line(entry, *, n_cleared: int = 0) -> str:
    """ONE line per auto-created person (§0-4). Auto-creation with no receipt
    is the CAPTUREFLOW silent-drop class inverted, so this is not optional
    output — the surface renders it or the creation was silent.

    Names the person (the reader has to know WHO was added to their book —
    this is the one place a name is the payload rather than a leak), says what
    it was derived from, says what it unblocked when it unblocked something,
    and advertises the standing undo.

    ⚠ IT SAYS WHAT ACTUALLY HAPPENED (review F-4). `status ==
    "already_on_file"` means the evidence matched somebody who was ALREADY a
    contact — the rows drained, which is real and worth reporting, but nobody
    was added. "Added <name>" there is a receipt for work that did not occur,
    and it advertises an undo that would archive a record this fire never
    created. The already-on-file wording claims the drain and nothing else."""
    e = entry or {}
    name = _txt(e.get("canonical_name")) or "a contact"
    cleared = (f" — {n_cleared} waiting "
               f"{'capture' if n_cleared == 1 else 'captures'} cleared"
               if n_cleared else "")
    if str(e.get("status") or "") == "already_on_file":
        return (f"{name} was already on your list — the meeting's attendee "
                f"list confirmed it{cleared}.")
    return (f"Added {name} from the meeting's attendee list{cleared} — say "
            f"undo to remove.")


def receipt_lines(created, *, cleared_by_person=None) -> list:
    cleared = dict(cleared_by_person or {})
    return [receipt_line(e, n_cleared=int(cleared.get(e.get("person_id"), 0)))
            for e in (created or [])]


def telemetry(result) -> dict:
    """§3-5 — the auto half of `person_candidate_counts`. Counts only, never a
    name: this rides a persisted receipt, and "counts only" is that contract.
    Additive keys, so no existing number moves."""
    r = result or {}
    return {
        "n_auto_created": int(r.get("n_created") or 0),
        "n_auto_rows_cleared": int(r.get("n_cleared") or 0),
        "n_evidence_suppressed": int(r.get("n_suppressed") or 0),
        "n_evidence_absent": int(r.get("n_no_evidence") or 0),
        "n_evidence_needs_confirm": int(r.get("n_needs_confirm") or 0),
        # Review F-4 — evidence matched a person already on file. Its own key
        # because it used to land in `n_evidence_absent`, which said the
        # opposite of what happened: the evidence was present and good.
        "n_evidence_already_on_file": int(r.get("n_already_on_file") or 0),
    }


__all__ = [
    "AUTO_CREATE_CAP",
    "CHANGE_CLASS",
    "CLEAR_CHANGE_CLASS",
    "BATCH_PREFIX",
    "email_domain",
    "normalize_attendee_records",
    "attendee_records_from_meeting_event",
    "find_evidence",
    "evidence_for_capture",
    "is_ignored",
    "new_batch_id",
    "seed_person_from_evidence",
    "seed_people_for_items",
    "receipt_line",
    "receipt_lines",
    "telemetry",
]
