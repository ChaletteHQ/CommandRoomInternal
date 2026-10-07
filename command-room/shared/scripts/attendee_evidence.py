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
# The company the participant record STATES IN WORDS — the connector's own
# org clause ("<Name> from <Org> <addr>") or company field. Never derived
# from a domain: a web address is not a name (SPEC_FLOW1 Lane F item 9).
_ORG_KEYS = ("org", "org_name", "company", "companyName", "company_name",
             "organization", "organisation", "employer")

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
        # The "from <Org>" clause is KEPT, not discarded — review item 9. It
        # is the connector STATING the company in words, which is the one
        # piece of evidence the org rule needs and the only one a domain
        # cannot supply.
        parts = _FROM_RE.split(label, maxsplit=1)
        label = parts[0]
        org = " ".join(parts[1].split()).strip(" ,;") if len(parts) > 1 else ""
        name = " ".join(label.split()).strip(" ,;")
        out.append({"name": name, "email": _txt(email), "org": org})
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
            org = next((_txt(item.get(k)) for k in _ORG_KEYS
                        if _txt(item.get(k))), "")
            # A dict whose only name-ish field holds an address is the
            # emails-as-names drift `build_meeting_event` refuses too.
            if name and "@" in name and not email:
                name, email = "", name
            recs = [{"name": name, "email": email, "org": org}]
        elif isinstance(item, str):
            recs = _from_prose(item) if "<" in item and ">" in item else (
                [{"name": "", "email": _txt(item), "org": ""}] if "@" in item
                else [{"name": _txt(item), "email": "", "org": ""}])
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
            out.append({"name": name, "email": email,
                        "org": _txt(rec.get("org"))})
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
    a batch id exactly and nothing here drifts with wall time.

    AN EMPTY SEED IS REFUSED, LOUDLY. It used to fall back to the literal
    `"0"`, which made `attev_0` a CONSTANT — every fire that forgot to hand
    its clock down shared one undo handle, so a single `undo` archived people
    filed days apart (review F-1, proven on the inbox rail). A caller with no
    reading of its own has no business minting a batch: it must pass its
    run's `batch_id` instead. Raising is the safe direction — the alternative
    is a silent one-way door across days."""
    stamp = re.sub(r"[^0-9A-Za-z]", "", _txt(now_iso))
    if not stamp:
        raise ValueError(
            "new_batch_id needs the caller's own `now` reading — an empty "
            "seed would mint one constant batch id for every fire, and one "
            "`undo` would then reach back across days. Pass `now_iso`, or "
            "pass the run's `batch_id`.")
    return f"{BATCH_PREFIX}{stamp}"


COLLISION_ASK = "ask"
COLLISION_VARIANT_ONLY = "variant_only"


def _create_past_variant_check(workspace_root, *, canonical_name, email,
                               email_provenance, source_skill, batch_id,
                               change_class, org_id, out) -> Optional[dict]:
    """The INVITE rail's name-collision rule. Returns a writer-shaped result,
    or None having already filled `out` with the refusal.

    Four refusals, and every one of them is a shipped relation rather than a
    new judgement:

      1. `find_existing_person` Tier 2 — an EXACT canonical name already on
         file is the real duplicate. Refused (`already_on_file`); the address
         belongs on that record, which is a different act than creating a
         second one.
      2. `find_existing_person` Tier 3 — a single-token or alias-only hit
         RAISES by design (Bug #19: "is this the same person, or a different
         person with the same first name?"). Refused as a human decision.
      3. `identity_reconcile.surname_variant` — the invite spells a name
         within a slip of somebody on file. That is rule 2's merge lane, and
         a second record would fork the very identity rule 2 exists to join.
      4. F-08 at capture — no observed provenance, no address stored. Mirrored
         here rather than inherited, because this branch does not go through
         `auto_add_person`, and a rule that only holds on the path you did not
         take is not a rule.

    WHAT IS DELIBERATELY NOT A REFUSAL: a shared name TOKEN with an unrelated
    record. See the `collision` note on the caller for the measurement that
    licensed that, and the removal proof in the lane's BUILD record.
    """
    from identity_reconcile import surname_variant
    from people_writer import (MultipleCandidatesError, create_person,
                               find_existing_person, list_same_name_people)

    try:
        exact = find_existing_person(workspace_root, name=canonical_name)
    except MultipleCandidatesError:
        out["status"] = "needs_confirm"
        out["detail"] = ("that spelling matches more than one record, or "
                         "matches one only by a nickname — a human decision")
        return None
    if exact is not None:
        out["status"] = "already_on_file"
        out["detail"] = "that exact name is already on file"
        return None

    try:
        candidates = list_same_name_people(workspace_root, canonical_name)
    except Exception as exc:
        # A roster we cannot read is not a roster with nobody in it.
        out["status"] = "needs_confirm"
        out["detail"] = f"the roster could not be read: {type(exc).__name__}"
        return None
    for cand in candidates or []:
        other = _txt(cand.get("canonical_name"))
        if other and surname_variant(canonical_name, other):
            out["status"] = "needs_confirm"
            out["detail"] = ("this spells a name already on file within a "
                             "slip — joining the two is the merge rule's "
                             "decision, not a second record")
            return None

    stored_email, dropped = email, False
    if email and not email_provenance:
        stored_email, dropped = None, True
    try:
        record = create_person(
            workspace_root,
            canonical_name=canonical_name,
            email=stored_email,
            needs_enrichment=True,
            source_skill=source_skill,
            brain_batch_id=batch_id,
            brain_change_class=change_class,
            **({"primary_org_id": org_id} if org_id else {}),
        )
    except Exception as exc:
        # `DuplicatePersonError` lands here too and means the same thing it
        # means everywhere: somebody is on file, nothing to create.
        if type(exc).__name__ == "DuplicatePersonError":
            out["status"] = "already_on_file"
            out["detail"] = str(exc)
        else:
            out["detail"] = f"{type(exc).__name__}: {exc}"
        return None
    return {"status": "added", "record": record,
            "email_dropped_no_provenance": dropped}


def seed_person_from_evidence(workspace_root, *, name, evidence,
                              batch_id: str,
                              source_ref: str = "",
                              org_id=None,
                              now_iso: Optional[str] = None,
                              source_skill: str = SOURCE_SKILL,
                              suppressed: Optional[Iterable[str]] = None,
                              ledger_ok: Optional[bool] = None,
                              collision: str = COLLISION_ASK,
                              change_class: str = CHANGE_CLASS) -> dict:
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

    `collision` — WHICH name-collision rule applies, and this is the one
    knob in this module a reviewer should read twice.

      `"ask"` (the DEFAULT, and every caller that existed before SPEC_FLOW1)
      is `auto_add_person`'s shipped gate, unchanged: ANY existing record
      sharing ANY whitespace token with this name returns `needs_confirm` and
      creates nothing.

      `"variant_only"` is the INVITE rail's, and it is NARROWER in exactly one
      respect and no other. It still refuses an exact name already on file
      (`find_existing_person` Tier 2 — the real duplicate), still refuses an
      ambiguous lookup (Tier 3 raises, Bug #19, a human decision), still
      refuses a SPELLING VARIANT of a record on file (`identity_reconcile.
      surname_variant` — that is rule 2's merge lane, not a second record),
      and still stores an address only with observed provenance. What it does
      NOT do is refuse on a SHARED TOKEN with an unrelated record.

      WHY THAT WIDENING IS CITED RATHER THAN QUIET. The token gate was written
      for a SPARSE input — "add a new person: Quinn" — where a shared first
      name really might be the person already on file. An invite hands over a
      FULL NAME and an ADDRESS THAT NOBODY ON FILE HOLDS (Tier 1 was checked
      before this call), which is a different question with a different right
      answer. Measured on the operator's own book (215 records, 63 invite
      pairs past the bar): 41 were already on file at that address, 2 shared
      an EXACT name with a record — correctly refused either way — and 20 were
      refused by nothing but a shared surname or first name with an unrelated
      record. Zero were spelling variants. Those 20 are the regression this
      lane exists to close: somebody on four invites at a company address,
      no record after four passes, four queued questions.

      The gate itself is untouched for every other caller. This is a second
      rule beside it, chosen by the caller, and the invite rail is the only
      caller that chooses it.

    `change_class` — the undo class stamped on the creation. Defaults to the
    shipped R1 class; the split path passes its own.

    Returns {"status", "person_id", "canonical_name", "email",
             "org_domain", "detail"} with status one of:
    created / suppressed / no_evidence / needs_confirm / already_on_file /
    error.
    """
    if collision not in (COLLISION_ASK, COLLISION_VARIANT_ONLY):
        raise ValueError(
            f"unknown collision rule {collision!r} "
            f"(known: {COLLISION_ASK!r}, {COLLISION_VARIANT_ONLY!r})")
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
    if collision == COLLISION_VARIANT_ONLY:
        res = _create_past_variant_check(
            workspace_root, canonical_name=canonical, email=email,
            email_provenance=provenance, source_skill=source_skill,
            batch_id=batch_id, change_class=change_class, org_id=org_id,
            out=out)
        if res is None:
            return out
    else:
        try:
            res = auto_add_person(
                workspace_root,
                canonical_name=canonical,
                email=email,
                email_provenance=provenance,
                source_skill=source_skill,
                brain_batch_id=batch_id,
                brain_change_class=change_class,
                needs_enrichment=True,
                **({"primary_org_id": org_id} if org_id else {}),
            )
        except DuplicatePersonError as exc:
            # Somebody is already on file. Nothing to create, and nothing
            # went wrong — the capture will resolve against them.
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

    # Minted only when the pass has a roster to act on. A pass that cannot
    # write anything has no business minting a batch id, and this is also what
    # keeps the byte-identical guarantee over-determined: with the
    # no-records short-circuit above mutated out, an empty roster still costs
    # exactly nothing rather than raising on a seed it does not need.
    bid = batch_id or (new_batch_id(now_iso) if by_ref else "")
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
# SPEC_FLOW1 Lane F rule 1 — THE INVITE RAIL (2026-09-07)
# ---------------------------------------------------------------------------
# WHAT CHANGED, AND WHY THE SEAM ABOVE WAS NOT ENOUGH.
#
# `seed_people_for_items` is REACTIVE: it creates a person only when a capture
# item already NAMES them and person-resolution failed on that name. So the
# attendee list settles identity for the people who happen to be spoken about
# in a promise, and for nobody else. The attended test found the hole exactly
# there: an attendee sat on four separate invites at a company address, was
# never the subject of a promise anyone extracted, and after four passes had
# no record and four queued "add this person?" proposals.
#
# M's ruling (SPEC_FLOW1 Lane F item 1) is the other half: a person ON an
# invite or a mail thread AT A COMPANY DOMAIN is CREATED, receipted, undoable
# — never a question. The evidence is not inferred and nothing is guessed: the
# invite carries the name and the address ON ONE RECORD, which is the same
# pair `find_evidence` calls the strongest identity evidence this workspace
# ever sees. What is new is that the pair no longer has to be ASKED FOR by a
# capture item before it is believed.
#
# THE BAR, in full (every clause fails safe — an unverifiable clause refuses):
#   1. the participant record carries BOTH a usable full name and an address
#      (`_name_ok` + `_email_ok`) — the pair, on the SAME record;
#   2. the address is at a COMPANY domain — `identity_reconcile.
#      is_free_mail_domain` says no, which is defensive by construction (a
#      domain it cannot read counts as free, so it refuses);
#   3. the address is not a role/shared inbox (`is_role_address`) — a mailbox
#      is not a person;
#   4. the address is not one of the user's OWN — a record of yourself from
#      your own invite is the CONTACT1 self-CC bug in another costume;
#   5. no OTHER participant of the same invite carries the same address under
#      a different name — that is a question, not evidence (`find_evidence`'s
#      ambiguity fence, same ruling);
#   6. nobody is already on file at that address (`find_existing_person`
#      Tier 1, the one tier the house treats as unambiguous);
#   7. the ignore ledger has not been spent on this name, AND was READ
#      COMPLETELY — `seed_person_from_evidence` owns that verdict (F-1);
#   8. the same-name gate inside `auto_add_person` still speaks: a collision
#      is a human decision and returns `needs_confirm`, creating nothing.
#
# NOTHING HERE IS NEW SAFETY MACHINERY. Clauses 6-8 are the shipped writer's
# own; 1-5 are the shipped bar plus the domain test M ruled. The write itself
# is `seed_person_from_evidence`, unforked, so the batch stamp, the registered
# archive reverser and the receipt are the ones already in the product.

INVITE_ORIGIN_CALENDAR = "calendar_invite"
INVITE_ORIGIN_THREAD = "mail_thread"
INVITE_ORIGINS = (INVITE_ORIGIN_CALENDAR, INVITE_ORIGIN_THREAD)

# At most this many people are created from ONE invite or thread. Deliberately
# ABOVE `AUTO_CREATE_CAP` (3) and cited rather than silently re-sized: that cap
# bounds creations driven by CAPTURE ITEMS, where three unresolved names in one
# meeting is already unusual. This pass reads the whole attendee list, and a
# six-person client call is ordinary — a cap of three there would leave half
# the room off the book and look like the rule not working. The bound is still
# real: a fifty-address distribution invite cannot mint fifty records silently.
INVITE_AUTO_CREATE_CAP = 5

# The change class the invite-rail creations carry. NOT a new class: R1 defines
# `person_org_creation_structured_fact` as "identity from a structured
# connector fact (full name + address from mail/CALENDAR)", which is this
# exactly. Its archive-never-delete reverser is already registered.
INVITE_CHANGE_CLASS = CHANGE_CLASS

# The SPLIT's own class — see `split_relation` below. It is separate from the
# creation class because the reverser is different: a split has to put the
# shared address back where it came from, and archiving alone would leave an
# archived record holding an address the original still needs to resolve by.
SPLIT_CHANGE_CLASS = "person_split"


def is_company_domain(email) -> bool:
    """Is this address at an ORGANISATION rather than a mail provider?

    Delegates the whole judgement to `identity_reconcile.is_free_mail_domain`,
    which is defensive by design: a missing, malformed or unreadable domain
    reads as FREE, so it refuses. Every caller here uses this to WITHHOLD, so
    that asymmetry points the right way — a false "free" costs one proposal a
    human answers, a false "company" costs a silent wrong record.

    PURE."""
    from identity_reconcile import is_free_mail_domain

    dom = email_domain(email)
    if not dom:
        return False
    return not is_free_mail_domain(dom)


def company_domain_participants(records, *, own_addresses=()) -> dict:
    """The §1 bar over ONE invite's / thread's participant list.

    Returns `{"eligible": ({"name","email","org_domain"}, ...), "refused":
    ({"name","email","bar"}, ...)}`. `bar` names the clause that refused, so a
    receipt and a test explain themselves without a second pass.

    THE AMBIGUITY CLAUSE IS NOT DECORATION. Two participants of one meeting
    carrying the SAME address under DIFFERENT names is either a shared mailbox
    the role test missed or two humans behind one address — both of which are
    questions. Creating one of them would make the record's identity depend on
    the order the connector listed them in, which is the failure review F-2
    demonstrated on `find_evidence` by reversing the list. Both are refused.

    (A DIFFERENT shape — the invite naming somebody the address is ALREADY on
    file under, under a different first name — is the SPLIT, and it is decided
    against the substrate rather than against the invite. See `split_relation`.)

    PURE. No clock, no substrate — `own_addresses` is handed in."""
    from identity_reconcile import is_role_address

    mine = {str(a).strip().lower() for a in (own_addresses or ()) if a}
    recs = normalize_attendee_records(records)

    # Fold by address FIRST, so the ambiguity clause is decided over the whole
    # list rather than per row.
    names_by_addr: dict = {}
    addrs_by_name: dict = {}
    # Item 9 — the STATED company names seen at each domain, folded over the
    # whole list. Two participants at one domain naming two different
    # companies is a contradiction, and a contradiction names nothing.
    orgs_by_domain: dict = {}
    for rec in recs:
        addr = _txt(rec.get("email")).lower()
        name = _txt(rec.get("name")).casefold()
        if addr:
            names_by_addr.setdefault(addr, set()).add(name)
        if name and addr:
            addrs_by_name.setdefault(name, set()).add(addr)
        org = _txt(rec.get("org"))
        if org and addr and "@" in addr:
            orgs_by_domain.setdefault(addr.split("@", 1)[1], {})[
                org.casefold()] = org

    eligible: list = []
    refused: list = []
    seen: set = set()
    for rec in recs:
        name, email = _txt(rec.get("name")), _txt(rec.get("email"))
        addr = email.lower()
        if not _email_ok(email):
            refused.append({"name": name, "email": email, "bar": "no_address"})
            continue
        if addr in seen:
            continue
        if not _name_ok(name):
            # A one-token or annotated label is the source saying it did not
            # know who this was. `Speaker 2 <s2@acme.example.com>` is a
            # transcript artefact, not a contact.
            refused.append({"name": name, "email": email, "bar": "no_full_name"})
            seen.add(addr)
            continue
        if addr in mine:
            refused.append({"name": name, "email": email, "bar": "own_address"})
            seen.add(addr)
            continue
        if is_role_address(email):
            refused.append({"name": name, "email": email, "bar": "role_address"})
            seen.add(addr)
            continue
        if not is_company_domain(email):
            refused.append({"name": name, "email": email,
                            "bar": "free_mail_domain"})
            seen.add(addr)
            continue
        # (The variable is named apart from `find_evidence`'s `distinct` on
        # purpose: `run_attendee1_mutation_test` anchors a mutation on that
        # exact line, and an anchor that matches twice is an anchor that has
        # stopped naming what it was written to name.)
        names_here = {n for n in names_by_addr.get(addr, set()) if n}
        if len(names_here) > 1:
            refused.append({"name": name, "email": email,
                            "bar": "ambiguous_address"})
            seen.add(addr)
            continue
        # THE MIRROR OF REVIEW F-2, and it is the same ruling read the other
        # way round. Two participants of ONE meeting spelled the SAME and
        # carrying DIFFERENT addresses are two humans; creating either of them
        # makes the record's address depend on the order the connector listed
        # them in, which is the exact failure the reviewer demonstrated on
        # `find_evidence` by reversing the list. `find_evidence` refuses
        # there; this refuses here, and for the same reason: picking one of
        # two is a guess at identity, and the two records that would settle it
        # are indistinguishable by name afterwards.
        if len({a for a in addrs_by_name.get(name.casefold(), set()) if a}) > 1:
            refused.append({"name": name, "email": email,
                            "bar": "ambiguous_name"})
            seen.add(addr)
            continue
        seen.add(addr)
        dom = email_domain(addr)
        stated = orgs_by_domain.get(dom) or {}
        eligible.append({"name": name, "email": addr, "org_domain": dom,
                         # One stated name at this domain, or nothing. A
                         # participant with no company clause inherits the
                         # one a colleague on the same invite stated — same
                         # domain, same invite, same company — but two
                         # different names cancel each other out.
                         "org_name": (list(stated.values())[0]
                                      if len(stated) == 1 else "")})
    return {"eligible": tuple(eligible), "refused": tuple(refused)}


# ---------------------------------------------------------------------------
# The SPLIT (SPEC_FLOW1 Lane F rule 4) — pure detection
# ---------------------------------------------------------------------------
# THE SHAPE, from the attended test: two people are on file as ONE record,
# because a merge or an early capture folded them together on the one address
# they share (a household, a shared work mailbox that is not role-shaped, a
# forwarded account). Every later proof, chase and brief then names the wrong
# person, and the only repair available was a hand edit of entities.json.
#
# THE RULE, AND IT IS DELIBERATELY THE NARROWEST ONE THAT CLOSES THE SHAPE.
# An invite or thread names <First-B> <Surname> at an address already on file
# under <First-A> <Surname>. Both names are full names. The surnames are
# IDENTICAL. The first names are neither an abbreviation of one another, nor
# an ordinary English nickname pair (`DIMINUTIVE_GROUPS`), nor within edit
# distance 2 — so a spelling slip, an initial, a clipping and a nickname all
# refuse, and only two plainly different given names split.
#
# WHAT THAT LIMIT COSTS, PLAINLY: two people sharing an address who do NOT
# share a surname are NOT split by this rule. A married couple with different
# surnames, or two colleagues on one shared mailbox, stay folded. The kickoff
# permitted a same-address-only rule; it is not taken, because with the
# surname clause the evidence is positive (one family name, two given names —
# the only readings are "two people" or "the same person misspelled", and the
# distance clause excludes the second), while without it the evidence is
# merely "two strings differ", which is how a legitimate alias becomes a
# duplicate record nobody asked for. A wrong split is a durable fork of the
# entity graph, and the graph is what every proof-based close reads.


def _first_last(name):
    toks = _txt(name).split()
    if len(toks) < 2:
        return "", ""
    return toks[0].casefold(), toks[-1].casefold()


def _abbreviation_of(a: str, b: str) -> bool:
    """Is one given name an initial or a clipping of the other? `S` / `Sam`,
    `Sam` / `Samuel`, `S.` / `Sam`. Those are ONE person written two ways.

    PREFIX-ONLY, and that is the whole of what it claims. The diminutives a
    prefix test cannot see live in `DIMINUTIVE_GROUPS` below."""
    a, b = a.strip(". ").casefold(), b.strip(". ").casefold()
    if not a or not b:
        return True
    if len(a) == 1 or len(b) == 1:
        return True
    return a.startswith(b) or b.startswith(a)


# ---------------------------------------------------------------------------
# The ENGLISH DIMINUTIVES — review F-2
# ---------------------------------------------------------------------------
# `_abbreviation_of` is a PREFIX test, so it refuses `Sam`/`Samuel` and reads
# `Bob`/`Robert` as two different people. Ten of the commonest English
# nicknames in the language are not prefixes of the name they stand for, and
# every one of them SPLIT a single human into two records on the evidence the
# reviewer measured: Bob/Robert, Bill/William, Mike/Michael, Liz/Elizabeth,
# Jack/John, Chuck/Charles, Ted/Edward, Dick/Richard, Peggy/Margaret,
# Teddy/Theodore. The trigger is ordinary — one mailbox whose connector
# display name is formal on one invite and familiar on the next.
#
# A group is ONE family of spellings that may name ONE person. Two given names
# that share ANY group are treated as THE SAME PERSON and never split. A name
# may sit in several groups (`Ted` stands for both formal names in its two
# groups; `Al` stands for three) — the test is set intersection, so a
# short form refuses against EITHER formal name it belongs to, while two
# formal names that merely share a short form still split.
#
# THE ASYMMETRY IS DELIBERATE, and it is the same one the whole family runs
# on: this table only ever WITHHOLDS a split. A pair it wrongly folds costs
# one question a human answers; a pair it wrongly splits is a durable fork of
# the entity graph. So it is generous, and English-only by design — this is
# the common English set, not a claim about names in general. A workspace
# whose people are named outside it loses nothing (the pair simply falls
# through to the distance clause, exactly as today) and gains the ability to
# carry its own table later; the shape below is a plain tuple of tuples so a
# per-workspace list can be concatenated onto it without touching this code.
DIMINUTIVE_GROUPS = (
    ("robert", "rob", "robbie", "bob", "bobby"),
    ("william", "will", "willie", "bill", "billy", "liam"),
    ("michael", "mike", "mick", "mickey", "micky"),
    ("elizabeth", "liz", "lizzie", "beth", "betsy", "betty", "eliza",
     "libby", "bess"),
    ("john", "johnny", "jack", "jon"),
    ("charles", "charlie", "chuck", "chas"),
    ("edward", "ed", "eddie", "ted", "teddy", "ned"),
    ("richard", "rich", "richie", "rick", "ricky", "dick"),
    ("margaret", "maggie", "meg", "peggy", "peg", "marge", "margie", "madge"),
    ("theodore", "theo", "ted", "teddy"),
    ("james", "jim", "jimmy", "jamie"),
    ("joseph", "joe", "joey"),
    ("thomas", "tom", "tommy"),
    ("anthony", "tony"),
    ("andrew", "andy", "drew"),
    ("benjamin", "ben", "benny", "benji"),
    ("christopher", "chris", "kit", "topher"),
    ("daniel", "dan", "danny"),
    ("david", "dave", "davey"),
    ("donald", "don", "donnie"),
    ("douglas", "doug"),
    ("frederick", "fred", "freddie", "fritz"),
    ("gerald", "gerry", "jerry"),
    ("gregory", "greg"),
    ("henry", "hank", "harry", "hal"),
    ("jeffrey", "jeff"),
    ("kenneth", "ken", "kenny"),
    ("lawrence", "larry", "laurie"),
    ("leonard", "leo", "len", "lenny"),
    ("nicholas", "nick", "nicky"),
    ("patrick", "pat", "paddy"),
    ("peter", "pete"),
    ("philip", "phillip", "phil"),
    ("raymond", "ray"),
    ("ronald", "ron", "ronnie"),
    ("samuel", "sam", "sammy"),
    ("stephen", "steven", "steve", "stevie"),
    ("timothy", "tim", "timmy"),
    ("walter", "walt", "wally"),
    ("albert", "al", "bert", "bertie"),
    ("alexander", "alex", "al", "sandy", "xander"),
    ("alfred", "al", "alf", "fred", "freddie"),
    ("arthur", "art", "artie"),
    ("eugene", "gene"),
    ("herbert", "herb", "bert"),
    ("harold", "hal", "harry"),
    ("howard", "howie"),
    ("nathaniel", "nathan", "nate"),
    ("jonathan", "jon", "jonny"),
    ("gabriel", "gabe"),
    ("norman", "norm"),
    ("russell", "russ"),
    ("stanley", "stan"),
    ("terrence", "terence", "terry"),
    ("vincent", "vince", "vinny"),
    ("zachary", "zach", "zack"),
    ("roger", "rodge"),
    ("barbara", "barb", "barbie", "babs"),
    ("catherine", "katherine", "kathryn", "cathy", "kathy", "kate", "katie",
     "kit", "kitty", "cate"),
    ("cynthia", "cindy"),
    ("deborah", "debra", "deb", "debbie"),
    ("dorothy", "dot", "dottie", "dolly"),
    ("eleanor", "ellie", "nell", "nellie"),
    ("frances", "fran", "frannie", "francie"),
    ("jennifer", "jen", "jenn", "jenny"),
    ("jessica", "jess", "jessie"),
    ("judith", "judy"),
    ("kimberly", "kim"),
    ("amanda", "mandy"),
    ("melissa", "missy", "lissa"),
    ("nancy", "nan"),
    ("pamela", "pam"),
    ("patricia", "pat", "patty", "tricia", "trish"),
    ("rebecca", "becca", "becky", "beck"),
    ("sandra", "sandy"),
    ("sarah", "sara", "sadie"),
    ("susan", "sue", "susie", "suzy"),
    ("victoria", "vicky", "vicki", "tori"),
    ("virginia", "ginny", "ginger"),
    ("isabella", "isabel", "bella", "izzy"),
    ("martha", "mattie"),
    ("mary", "molly", "polly", "mamie"),
    ("joanne", "joan", "jo"),
    ("janet", "jan"),
    ("janice", "jan"),
    ("veronica", "ronnie"),
)

_DIMINUTIVE_INDEX: dict = {}
for _gi, _group in enumerate(DIMINUTIVE_GROUPS):
    for _n in _group:
        _DIMINUTIVE_INDEX.setdefault(_n, set()).add(_gi)
_DIMINUTIVE_INDEX = {k: frozenset(v) for k, v in _DIMINUTIVE_INDEX.items()}


def diminutive_of(a: str, b: str) -> bool:
    """Are these two given names an ordinary English nickname pair — one
    person written two ways (`Bob`/`Robert`, `Peggy`/`Margaret`)?

    Set intersection over `DIMINUTIVE_GROUPS`. Two names neither of which the
    table knows are NOT a pair, so an unknown name behaves exactly as it did
    before this table existed.

    PURE."""
    ga = _DIMINUTIVE_INDEX.get(_txt(a).strip(". ").casefold())
    gb = _DIMINUTIVE_INDEX.get(_txt(b).strip(". ").casefold())
    if not ga or not gb:
        return False
    return bool(ga & gb)


def split_relation(record_name, participant_name) -> dict:
    """Do these two full names, seen on ONE shared address, name TWO people?

    Returns `{"split": bool, "why": str}`. `why` names the clause that
    refused, in words a receipt could carry.

    PURE. No clock, no substrate."""
    from identity_reconcile import _edit_distance

    out = {"split": False, "why": ""}
    if not _name_ok(record_name) or not _name_ok(participant_name):
        out["why"] = "one of the two is not a full name"
        return out
    a_first, a_last = _first_last(record_name)
    b_first, b_last = _first_last(participant_name)
    if not a_last or not b_last:
        out["why"] = "one of the two is not a full name"
        return out
    if a_last != b_last:
        out["why"] = ("the surnames differ — two people at one address with "
                      "no family name in common is a question, not evidence")
        return out
    if a_first == b_first:
        out["why"] = "the same person"
        return out
    if _abbreviation_of(a_first, b_first):
        out["why"] = ("one given name is an initial or a clipping of the "
                      "other — that is one person written two ways")
        return out
    if diminutive_of(a_first, b_first):
        # Review F-2. Consulted BEFORE the distance clause, because none of
        # these pairs is within two characters of the other and the prefix
        # test cannot see any of them: Bob is not a prefix of Robert.
        out["why"] = ("one given name is the ordinary nickname of the other "
                      "— that is one person written two ways")
        return out
    if _edit_distance(a_first, b_first, cap=2) <= 2:
        out["why"] = ("the given names are within a spelling slip of each "
                      "other — that is the variant rule's question, not a "
                      "split")
        return out
    out["split"] = True
    out["why"] = ("one family name, two plainly different given names, on one "
                  "shared address")
    return out


def find_split(workspace_root, *, name, email) -> dict:
    """Does this invite/thread participant name a SECOND person on a record
    already holding their address?

    Reads the substrate (the person at that address) and applies
    `split_relation`. Returns `{"split", "why", "person_id", "record_name"}`.

    Refuses on anything it cannot vouch for: no record at that address, a
    record with no canonical name, an ARCHIVED record (splitting somebody out
    of a record an undo already retired would resurrect half of it), a
    participant spelling that is ALREADY one of the record's own display names
    (that is the same person, spelled as they are known), or an ambiguous
    lookup."""
    from people_writer import (MultipleCandidatesError, find_existing_person,
                               get_person_display_names)

    out = {"split": False, "why": "", "person_id": "", "record_name": ""}
    addr = _txt(email).lower()
    if not addr:
        out["why"] = "no address to look up"
        return out
    try:
        rec = find_existing_person(workspace_root, email=addr)
    except MultipleCandidatesError:
        out["why"] = "the address resolves to more than one record"
        return out
    except Exception as exc:
        out["why"] = f"the roster could not be read: {type(exc).__name__}"
        return out
    if not rec:
        out["why"] = "nobody is on file at that address"
        return out
    if str(rec.get("status") or "") == "archived":
        out["why"] = "the record at that address is archived"
        return out
    out["person_id"] = str(rec.get("id") or "")
    out["record_name"] = _txt(rec.get("canonical_name"))
    known = {_txt(n).casefold() for n in get_person_display_names(rec)}
    if _txt(name).casefold() in known:
        out["why"] = "the record already answers to this spelling"
        return out
    rel = split_relation(out["record_name"], name)
    out["split"] = bool(rel["split"])
    out["why"] = rel["why"]
    return out


# ---------------------------------------------------------------------------
# The invite rail's WRITE pass
# ---------------------------------------------------------------------------

def seed_people_from_participants(
        workspace_root, records, *, source_ref="", origin=INVITE_ORIGIN_CALENDAR,
        now_iso: Optional[str] = None, batch_id: Optional[str] = None,
        cap: int = INVITE_AUTO_CREATE_CAP, source_skill: str = SOURCE_SKILL,
        own_addresses: Optional[Iterable[str]] = None,
        enabled: Optional[bool] = None) -> dict:
    """SPEC_FLOW1 Lane F rule 1 + rule 4 — create a record for every
    company-domain participant of ONE invite or thread who is not on file, and
    split a participant back out of a record they were folded into.

    NO RECORDS MEANS BYTE-IDENTICAL. With an empty participant list nothing is
    read, no writer is imported, and the result is all-zero — the same
    "no-match costs exactly today" guarantee `seed_people_for_items` gives.

    `enabled` is the `identity.auto_create` switch. `None` means READ IT (the
    default), and the read fails to ON: a malformed settings file is not a
    customer saying stop. `False` short-circuits before anything is read, and
    the participants fall through to the existing propose path unchanged.

    Idempotent by construction: clause 6 of the bar refuses anybody already at
    that address, and the writer's own dedup refuses the rest — including an
    ARCHIVED record an `undo` left behind, which is what stops the next invite
    from re-creating a record the customer just retracted.

    Returns {"created", "split", "n_created", "n_split", "n_already_on_file",
             "n_suppressed", "n_needs_confirm", "n_refused", "refused",
             "n_rows_retired", "n_link_questions", "n_orgs_created",
             "batch_id", "receipt_lines", "enabled"}.
    """
    empty = {"created": [], "split": [], "n_created": 0, "n_split": 0,
             "n_already_on_file": 0, "n_suppressed": 0, "n_needs_confirm": 0,
             "n_refused": 0, "refused": [], "n_rows_retired": 0,
             "n_link_questions": 0, "n_orgs_created": 0,
             "batch_id": None, "receipt_lines": [], "enabled": True}
    if origin not in INVITE_ORIGINS:
        raise ValueError(
            f"unknown participant origin {origin!r} (known: {INVITE_ORIGINS})")
    if enabled is None:
        try:
            from commitment_policy import (IDENTITY_AUTO_CREATE_KEY,
                                           flow_switch_enabled)
            enabled = flow_switch_enabled(workspace_root,
                                          IDENTITY_AUTO_CREATE_KEY)
        except Exception:
            # FAIL TO DEFAULT, and the default is ON. The alternative to the
            # act is a question, and a broken config must not silently put a
            # seat back on a queue of them.
            enabled = True
    if not enabled:
        empty["enabled"] = False
        return empty
    if not records:
        return empty

    gated = company_domain_participants(
        records, own_addresses=own_addresses or ())
    if not gated["eligible"]:
        out = dict(empty)
        out["refused"] = list(gated["refused"])
        out["n_refused"] = len(out["refused"])
        return out

    from person_candidates import load_suppressions_checked
    from people_writer import MultipleCandidatesError, find_existing_person

    # ONE ledger read for the whole pass, carrying its own readability verdict
    # (F-1). A per-participant read would answer one question N different ways.
    suppressed, ledger_ok = load_suppressions_checked(workspace_root)
    bid = batch_id or new_batch_id(now_iso)
    ref = _txt(source_ref)

    created: list = []
    split: list = []
    refused = list(gated["refused"])
    n_already = n_suppressed = n_needs_confirm = 0
    n_linked = 0
    n_orgs = 0

    for cand in gated["eligible"]:
        if len(created) + len(split) >= max(0, int(cap)):
            refused.append({"name": cand["name"], "email": cand["email"],
                            "bar": "cap"})
            continue

        # Clause 6 — already on file at this address? Tier 1 (email exact) is
        # the one tier the house treats as unambiguous, and it is the tier that
        # makes a second invite cost nothing.
        on_file = None
        try:
            on_file = find_existing_person(workspace_root, email=cand["email"])
        except MultipleCandidatesError:
            refused.append({"name": cand["name"], "email": cand["email"],
                            "bar": "ambiguous_on_file"})
            continue
        except Exception as exc:
            refused.append({"name": cand["name"], "email": cand["email"],
                            "bar": f"roster_unreadable:{type(exc).__name__}"})
            continue

        if on_file is not None:
            # SOMEBODY holds this address. Either it is them (nothing to do),
            # or the invite is naming a SECOND person folded onto the same
            # record — rule 4.
            rel = find_split(workspace_root, name=cand["name"],
                             email=cand["email"])
            if not rel["split"]:
                n_already += 1
                continue
            res = _apply_split(workspace_root, cand=cand, relation=rel,
                               batch_id=bid, source_ref=ref, origin=origin,
                               now_iso=now_iso, source_skill=source_skill,
                               suppressed=suppressed, ledger_ok=ledger_ok)
            status = res.get("status")
            if status == "split":
                split.append(res)
            elif status == "suppressed":
                n_suppressed += 1
            elif status == "already_on_file":
                n_already += 1
            else:
                refused.append({"name": cand["name"], "email": cand["email"],
                                "bar": f"split_{status}",
                                "reason": res.get("detail", "")})
            continue

        evidence = {"matched": True, "attendee_name": cand["name"],
                    "email": cand["email"], "org_domain": cand["org_domain"],
                    "why": f"{origin} carries this name and this address"}
        res = seed_person_from_evidence(
            workspace_root, name=cand["name"], evidence=evidence,
            batch_id=bid, source_ref=ref, now_iso=now_iso,
            source_skill=source_skill, suppressed=suppressed,
            ledger_ok=ledger_ok,
            # The invite rail's collision rule — see `seed_person_from_
            # evidence`. Tier 1 (this address) was already checked above.
            collision=COLLISION_VARIANT_ONLY)
        status = res.get("status")
        if status == "suppressed":
            n_suppressed += 1
            continue
        if status == "needs_confirm":
            n_needs_confirm += 1
            # Review F-5 — the refusal reaches rule 2's lane instead of
            # vanishing. One row, once, never a second one.
            n_linked += _queue_link_question(
                workspace_root, name=cand["name"], email=cand["email"],
                origin=origin, source_ref=ref, source_skill=source_skill)
            continue
        if status == "already_on_file":
            n_already += 1
            continue
        if status != "created" or not res.get("person_id"):
            refused.append({"name": cand["name"], "email": cand["email"],
                            "bar": f"writer_{status}",
                            "reason": res.get("detail", "")})
            continue
        org = _attach_or_create_org(
            workspace_root, person_id=res["person_id"],
            cand=dict(cand, origin=origin), batch_id=bid,
            source_skill=source_skill)
        if org["created"]:
            n_orgs += 1
        created.append({"person_id": res["person_id"],
                        "canonical_name": res.get("canonical_name")
                        or cand["name"],
                        "email": cand["email"],
                        "org_domain": res.get("org_domain") or "",
                        "origin": origin, "meeting_ref": ref,
                        "batch_id": bid,
                        "org_id": org["org_id"],
                        "org_created": org["created"],
                        "org_name": org["name"]})

    n_rows = 0
    for entry in created + split:
        n_rows += _retire_identity_rows(
            workspace_root, name=entry.get("canonical_name"),
            email=entry.get("email"), person_id=entry.get("person_id"),
            batch_id=bid, origin=origin, source_skill=source_skill)

    return {"created": created, "split": split, "n_created": len(created),
            "n_split": len(split), "n_already_on_file": n_already,
            "n_suppressed": n_suppressed, "n_needs_confirm": n_needs_confirm,
            "n_refused": len(refused), "refused": refused,
            "n_rows_retired": n_rows, "n_link_questions": n_linked,
            "n_orgs_created": n_orgs,
            "batch_id": bid if (created or split) else None,
            "receipt_lines": participant_receipt_lines(created, split),
            "enabled": True}


def _apply_split(workspace_root, *, cand, relation, batch_id, source_ref,
                 origin, now_iso, source_skill, suppressed, ledger_ok) -> dict:
    """The rule-4 write. Fenced by the SAME ignore ledger the creation path
    uses — a name the customer set aside is not split back out either."""
    if is_ignored(workspace_root, cand["name"], suppressed=suppressed,
                  ledger_ok=ledger_ok):
        return {"status": "suppressed",
                "detail": "the user set this name aside, or the ignore ledger "
                          "could not be read"}
    from people_writer import split_person_from_shared_address

    provenance = {"via": origin, "source_ref": source_ref}
    if now_iso:
        provenance["observed_ts"] = _txt(now_iso)
    try:
        res = split_person_from_shared_address(
            workspace_root,
            folded_person_id=relation["person_id"],
            canonical_name=cand["name"],
            shared_email=cand["email"],
            email_provenance=provenance,
            brain_batch_id=batch_id,
            source_skill=source_skill,
        )
    except Exception as exc:
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
    if res.get("status") != "split":
        return res
    return {"status": "split", "person_id": res.get("person_id"),
            "canonical_name": cand["name"], "email": cand["email"],
            "org_domain": cand["org_domain"], "origin": origin,
            "from_person_id": relation["person_id"],
            "from_name": relation.get("record_name") or "",
            "meeting_ref": source_ref, "batch_id": batch_id}


def _retire_identity_rows(workspace_root, *, name, email, person_id, batch_id,
                          origin, source_skill) -> int:
    """Drain the queued "add this person?" rows the new record answers.

    THE POINT OF THE WHOLE LANE IS HERE. Creating the record and leaving four
    proposals about that same person open would trade one defect for another:
    the queue is what the customer actually sees. This is CONTACT1's own drain
    (`open_rows_satisfied_by` + the shipped tombstone builder), stamped with
    THIS batch so one `undo` reopens the rows alongside the record."""
    try:
        from confirm_flow import build_person_proposal_resolved_event
        from contact_capture import open_rows_satisfied_by
        from event_gate import append_event
    except ImportError:  # pragma: no cover
        return 0
    try:
        rows = open_rows_satisfied_by(workspace_root, name, email=email)
    except Exception:
        return 0
    tombs = []
    for row in rows or []:
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
            note=f"identity {batch_id} — created from the {origin.replace('_', ' ')}",
            **kwargs)
        tomb["data"]["brain_batch_id"] = batch_id
        tomb["data"]["brain_change_class"] = "person_proposal_tombstone"
        tombs.append(tomb)
    if not tombs:
        return 0
    try:
        append_event(_events_path_for(workspace_root), tombs,
                     holder=source_skill)
    except Exception:
        # The record and its marker both landed; only the tidy-up failed. The
        # rows stay open and the next pass drains them — never a lost record.
        return 0
    return len(tombs)


def _queue_link_question(workspace_root, *, name, email, origin,
                         source_ref, source_skill) -> int:
    """Rule 2's LANE, reached from the invite rail — review F-5.

    A participant who is a SPELLING VARIANT of somebody on file is refused as
    a create, correctly: a second record would fork the identity rule 2 exists
    to join. But until this function existed the refusal wrote NOTHING — no
    row, no event, no count that reached a surface — so rule 2 ("a spelling
    variant of a person already on file MERGES automatically") never reached
    the invite rail at all. From an invite a variant was neither created, nor
    merged, nor asked about, nor counted: the CAPTUREFLOW silent-drop class
    that this lane's receipt doctrine exists to invert.

    So the variant is written as a LINK QUESTION — the shipped
    `person_proposal` shape CONTACT1's same-name collision already uses, with
    the address carried in `evidence` prose where `person_backlog_sweep.
    _observed_email` (THE F-3 attribution reader) looks for it. From there it
    is an ordinary identity row: `classify_cluster` tiers it `merge_propose`
    (rule 2's link lane, not the add lane), and past its fortnight rule 3's
    aged default resolves it to the likeliest record or lets it go. Nobody is
    asked a new question that was not already going to be asked.

    NOT BATCH-STAMPED, deliberately. This writes a QUESTION, not an act:
    there is nothing for `undo` to take back, and stamping a change class
    with no reverser behind it is exactly what G57 forbids. The row is
    answered, aged out, or dismissed on the rails that already own it.

    Idempotent: a second invite carrying the same variant finds the row it
    wrote last time and writes nothing. Returns 1 when a row was written."""
    try:
        from contact_capture import open_rows_satisfied_by
        from event_gate import append_event
        from meeting_capture import build_person_proposal_event
    except ImportError:  # pragma: no cover
        return 0
    try:
        if open_rows_satisfied_by(workspace_root, name, email=email):
            return 0
    except Exception:
        return 0
    where = str(origin).replace("_", " ")
    ev = build_person_proposal_event(
        name,
        source_ref=_txt(source_ref) or where,
        source_skill=source_skill,
        evidence=(f"{name} <{email}> — on the {where}, spelled within a slip "
                  f"of somebody already on your list. Same person, or a "
                  f"second one?"),
        review_reason="a spelling variant of a record on file, seen on an "
                      "invite or thread",
    )
    if ev.get("type") != "person_proposal":
        # `build_person_proposal_event` re-routes a name that resolves to a
        # tracked ORG. An org is not an identity question; drop it rather
        # than file an org proposal nobody asked this rail for.
        return 0
    ev["data"]["email"] = _txt(email)
    try:
        append_event(_events_path_for(workspace_root), [ev],
                     holder=source_skill)
    except Exception:
        return 0
    return 1


def _attach_or_create_org(workspace_root, *, person_id, cand, batch_id,
                          source_skill) -> dict:
    """SPEC_FLOW1 Lane F item 9 (B5.5) — the NARROW org rule, as ruled.

    An org is created from an invite ONLY when the participant record states
    the company IN WORDS — the connector's own "<Name> from <Org> <addr>"
    clause, or a company field on a participant dict — and the address's
    domain is not free mail. The domain is then ATTACHED to that org as
    evidence; it is never used to NAME one. Deriving "Acme Co" from a
    domain string is the pattern-guess the whole identity family refuses,
    and this does not do it.

    THREE REFUSALS, all of them silent and all of them correct:
      * no stated company name -> nothing is created (exactly as before).
      * two participants at one domain stating two DIFFERENT companies ->
        the bar already cancelled both (`org_name` comes back empty).
      * the domain is free mail -> the bar already refused the person.

    An org ALREADY on file at that domain, or under that name, is attached to
    and never re-created — `attribute_person_to_org` is the shipped
    work-domain path and it runs first.

    Receipted and undoable: the create is stamped with THIS batch and the R1
    change class whose reverser archives an org (never deletes it), so the
    same one `undo` that retracts the person retracts the company.

    Returns {"org_id", "created", "name"}; never raises into the pass."""
    out = {"org_id": "", "created": False, "name": ""}
    domain = _txt(cand.get("org_domain")).lower()
    stated = _txt(cand.get("org_name"))
    if not person_id or not domain:
        return out
    try:
        from org_writer import (DuplicateOrgError, attribute_person_to_org,
                                create_org, find_existing_org)
        from people_writer import update_person
    except ImportError:  # pragma: no cover
        return out

    # 1. The shipped work-domain path. Attaches to an org already on file at
    #    this domain and writes no org at all.
    try:
        org, _why = attribute_person_to_org(
            workspace_root, person_id, work_domains=[domain],
            source_skill=source_skill)
    except Exception:
        org = None
    if org and org.get("id"):
        out["org_id"] = org["id"]
        out["name"] = _txt(org.get("canonical_name"))
        return out

    if not stated:
        # No name stated in words. This is where B5.5 stops, deliberately.
        return out

    # 2. An org on file under that NAME but without this domain — attach, and
    #    let the domain ride along as evidence.
    try:
        existing = find_existing_org(workspace_root, name=stated)
    except Exception:
        existing = None
    if existing and existing.get("id"):
        try:
            update_person(workspace_root, person_id,
                          primary_org_id=existing["id"],
                          source_skill=source_skill, suppress_lineage=True)
        except Exception:
            return out
        out["org_id"] = existing["id"]
        out["name"] = _txt(existing.get("canonical_name"))
        return out

    # 3. Create it, from the STATED NAME, with the domain as evidence.
    try:
        rec = create_org(
            workspace_root, canonical_name=stated, domains=[domain],
            source_skill=source_skill, needs_enrichment=True,
            inferred_from=[f"{cand.get('origin') or 'invite'}:{domain}"],
            brain_batch_id=batch_id,
            brain_change_class=INVITE_CHANGE_CLASS)
    except DuplicateOrgError:
        return out
    except Exception:
        return out
    try:
        update_person(workspace_root, person_id,
                      primary_org_id=rec["id"], source_skill=source_skill,
                      suppress_lineage=True)
    except Exception:
        pass
    out["org_id"] = rec["id"]
    out["created"] = True
    out["name"] = _txt(rec.get("canonical_name"))
    return out


def _events_path_for(workspace_root):
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _safe_display(value, fallback="a contact") -> str:
    """The LEAK2 seam — review F-13. Every customer-facing name this module
    composes goes through `identity_reconcile._display_name`, which delegates
    to `narration_names.safe_name` when LEAK2 is present and keeps its
    contract when it is not: an internal id is never printed as a name."""
    try:
        from identity_reconcile import _display_name

        return _display_name(_txt(value), fallback=fallback)
    except Exception:
        return _txt(value) or fallback


def participant_receipt_line(entry) -> str:
    """ONE line per act, in the customer's words. A silent create is the
    CAPTUREFLOW silent-drop class inverted, so this is not optional output."""
    e = entry or {}
    name = _safe_display(e.get("canonical_name"))
    where = ("the invite" if str(e.get("origin")) == INVITE_ORIGIN_CALENDAR
             else "the mail thread")
    if e.get("from_person_id"):
        other = _safe_display(e.get("from_name"), fallback="another record")
        return (f"{name} was filed under {other} — they share an address, so "
                f"{name} now has their own record. Say undo to put it back.")
    if e.get("org_created") and _txt(e.get("org_name")):
        # Item 9 — the company is a second act in the same batch, so it gets
        # said out loud. A company added silently is the silent-drop class.
        return (f"Added {name} from {where}, and {_txt(e['org_name'])} with "
                f"them — say undo to remove.")
    return (f"Added {name} from {where} — say undo to remove.")


def participant_receipt_lines(created, split=()) -> list:
    return [participant_receipt_line(e) for e in list(created or ())
            + list(split or ())]


def participant_telemetry(result) -> dict:
    """Counts only, never a name — this rides a persisted receipt."""
    r = result or {}
    return {
        "n_invite_created": int(r.get("n_created") or 0),
        "n_invite_split": int(r.get("n_split") or 0),
        "n_invite_already_on_file": int(r.get("n_already_on_file") or 0),
        "n_invite_suppressed": int(r.get("n_suppressed") or 0),
        "n_invite_needs_confirm": int(r.get("n_needs_confirm") or 0),
        "n_invite_refused": int(r.get("n_refused") or 0),
        "n_invite_rows_retired": int(r.get("n_rows_retired") or 0),
        # Review F-5 — the variant the rail withheld, now written as a link
        # question rather than dropped in silence.
        "n_invite_link_questions": int(r.get("n_link_questions") or 0),
        # Item 9 (B5.5) — companies created from a STATED company name on the
        # invite, never from a domain string.
        "n_invite_orgs_created": int(r.get("n_orgs_created") or 0),
    }



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
    name = _safe_display(e.get("canonical_name"))
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
    "COLLISION_ASK",
    "COLLISION_VARIANT_ONLY",
    "seed_people_for_items",
    "INVITE_ORIGIN_CALENDAR",
    "INVITE_ORIGIN_THREAD",
    "INVITE_ORIGINS",
    "INVITE_AUTO_CREATE_CAP",
    "INVITE_CHANGE_CLASS",
    "SPLIT_CHANGE_CLASS",
    "is_company_domain",
    "company_domain_participants",
    "DIMINUTIVE_GROUPS",
    "diminutive_of",
    "split_relation",
    "find_split",
    "seed_people_from_participants",
    "participant_receipt_line",
    "participant_receipt_lines",
    "participant_telemetry",
    "receipt_line",
    "receipt_lines",
    "telemetry",
]
