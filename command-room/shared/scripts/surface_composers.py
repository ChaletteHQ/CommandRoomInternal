#!/usr/bin/env python3
"""surface_composers — the customer's sentence is built by CODE, then gated.

SPEC FIXTRAIN v5.30.0 lane 6 item 6.2. HOLD driver 4 of the attended test
recorded thirteen internal strings on customer surfaces — file names, record
numbers, command-line flags, script and field names. The gate that exists to
refuse exactly those strings (`chat_output_renderer.validate_chat_output`)
caught none of them, and the reason was not a gap in its patterns: **not one
of those thirteen sentences ever reached the gate.** Each was composed live by
the model, reading a SKILL.md that described a mechanic in the mechanic's own
vocabulary, and printed straight to chat.

Widening the patterns (item 6.1) does nothing about that. A sentence nobody
scans is not protected by a better scanner. So this module is the other half:
for every surface on the record, ONE function that builds the sentence and
runs it through the gate before returning it. The SKILL.md then says "print
what this returns" instead of describing what to say — which is the standing
"code at the write" fence applied to prose.

THE CHOKEPOINT is `say`. Every composer here ends in it, and it raises rather
than returning text the gate refuses. Nothing catches that raise: a composer
that cannot say something clean has a bug in its words, and the fix is the
words.

WHAT IS DELIBERATELY NOT HERE. Nothing in this module decides anything. The
maintenance composer does not judge what is due, the reprocess composer does
not dedupe, the billing door does not classify mail. They take a result
another module produced and turn it into a sentence. That separation is why
they can be pinned cheaply: given this input, exactly this English, and it
passes the gate.

AND THEN THE TURN CARRIED ON PAST IT (SPEC FIXTRAIN v5.31.0 6.1, R-25).
The attended test of v5.31.0 recorded thirteen instances again, and eleven of
them were ONE new shape: a clean gated answer, followed by an ungated
"diagnosis" paragraph naming files, functions, event names and writer ids.
`say` gates the sentence a composer built and returns it; the chat then keeps
typing, and nothing reads what it types. Seven composers, all clean, and the
reader still saw the internals — because the gate was reached by the answer
and skipped by the trailer.

`post` is the answer, and it is the wrap's shape generalised. M ruled the
class is fixed "by gating everything the chat says after a composer, not by a
denylist of words" — the patterns are already right (all thirteen strings
raise when they are fed to the gate), what was missing is a READER. So: ONE
function over the FINAL text of the turn, which returns the text the skill
renders, exactly as `quiet.wrap_post` does for the Friday wrap.

PUBLIC API
  say(text, **gate_kwargs) -> str        the compose-and-gate chokepoint
  post(text, ...) -> str                 THE WHOLE-TURN DOOR (FIXTRAIN 6.1)
  refused_line(surface) -> str           what a reader gets when it refuses
  FOREIGN_COMPOSERS                      composers living in other modules
  customer_spans(ws, rows) -> list       the customer's words, read off rows
  job_words(job_id) -> str               a job id as ordinary words
  maintenance_answer(plan, ...) -> str   maintenance Run Now (cleanup Step 0)
  reprocess_receipt(...) -> str          scan-for-commitments / reprocess chat
  decision_log_answer(...) -> str        decision-log's wrapper text
  billing_door_line(vendor) -> str       the inbox billing-domains phrase door
  add_billing_domain(ws, domain) -> dict the door's writer (path never renders)
  billing_door_receipt(result) -> str    what to say once the door was used

stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ---------------------------------------------------------------------------
# THE CHOKEPOINT
# ---------------------------------------------------------------------------

def say(text: str, *, workspace=None, surface=None, paths_text=None) -> str:
    """Return `text` only if the leak gate passes it. Raises otherwise.

    This is the single line that makes a composer a composer. A function that
    builds a sentence and returns it is prose with extra steps; a function
    that builds a sentence and cannot return it unscanned is a fence.

    The raise is `chat_output_renderer.LeakDetectedError` and is NEVER caught
    here. A refusal means this module's own words carried something internal,
    which is a bug in the words — not a customer-visible degrade to swallow.
    """
    from chat_output_renderer import validate_chat_output
    # The gate's path scanner does string work on `workspace`; every caller
    # here has a Path in hand, and a Path reaching it is a TypeError inside
    # the scanner — a crash where a refusal belongs. Coerce at the door.
    ws = str(workspace) if workspace is not None else None
    validate_chat_output(text, workspace=ws, surface=surface,
                         paths_text=paths_text)
    # AND IT STAMPS WHAT IT PASSED (FIXTRAIN 6.1 fix round 1, REVIEW F-1).
    # `post` may only be handed a `relayed` a composer actually produced.
    # This line is the stamp: everything in the register has been through the
    # full unmarked scan above, which is why declaring it to `post` can hide
    # nothing. A composer living in another module (`FOREIGN_COMPOSERS`)
    # hands its return through `say` on the way in and is stamped the same.
    return _register_composed(text)


# ---------------------------------------------------------------------------
# THE WHOLE-TURN DOOR
# ---------------------------------------------------------------------------

class TrailerRelayError(RuntimeError):
    """A composer's own answer was handed to `post` as `relayed` and is not in
    the posted text byte-exact — it was paraphrased, or it was dropped.

    Its own class, never a `LeakDetectedError`: nothing leaked, the composer
    simply was not used. That is the router-misses defect (recorded 09-13 and
    again 09-16, unchanged both times): the composer existed, was clean, and
    the model read the view file and wrote its own sentences instead."""


class DeclarationError(RuntimeError):
    """A caller DECLARED more than the door can account for.

    Its own class, never a `LeakDetectedError` and never a
    `TrailerRelayError`: nothing leaked and no composer was skipped — the
    caller told the door that some part of the turn had already been checked,
    and the door cannot verify that claim.

    REVIEW_LEAK4 F-1 (2026-09-17) is why this exists. `post` began life with
    two one-argument bypasses: `spans=[the whole trailer]` and
    `relayed=<the whole body>` both posted a paragraph full of internals,
    because a declaration exempts the vocabulary classes and nothing bounded
    how much a caller could declare. Prose is not a fence — that is this
    lane's own premise — so the bound is here, in code, and a caller cannot
    widen it with an argument.

    RE-VERIFY 1 (2026-09-17) is why it now raises where it used to allow. The
    first bound was a 50% cap and a pair of caller-supplied span strings; the
    reviewer walked through both, six times over, with two arguments instead
    of one. So the arguments themselves are gone: there is no span text to
    hand this door (it reads the rows), and there is no share of a turn to
    spend (a relay is vouched in full or refused). What remains is the one
    residual nothing in software can close — a customer who types an internal
    name onto their own row has typed it, and this door will read it back."""


# The same rule `docx_leak_scanner.blank_user_spans` applies to a document
# span: shorter than this and the "span" is a word, and blanking a word out
# of the machine text is how a gate goes quietly blind.
_POST_SPAN_MIN = 8

# ---------------------------------------------------------------------------
# THE COMPOSER-OUTPUT REGISTER (REVIEW_LEAK4 F-1; RE-VERIFY 1 N-1/N-3/N-6)
#
# `relayed` is accepted only when it is text a composer actually returned —
# IN FULL, line for line, or the door refuses. `say` is the one place a
# composer's sentence becomes a composer's sentence, so `say` stamps what it
# passed and `post` asks the register. Two properties make this sound rather
# than decorative:
#
#   * everything in the register went through `validate_chat_output` with NO
#     declaration at all, so declaring it back to `post` can hide nothing;
#   * NOTHING A SKILL CAN REACH WRITES TO IT except through the gate. That is
#     the honest statement of the boundary, and it is narrower than the one
#     this comment used to make (RE-VERIFY 1 N-6): the register is not a
#     boundary against this module's own API. `_register_composed` is
#     module-private for exactly that reason — `say` and the foreign
#     composers named in `FOREIGN_COMPOSERS` call it with THEIR OWN return,
#     and no public name in this module writes to the register at all. No
#     caller-supplied string reaches it: that was P-1, and it is closed.
#
# SAME PROCESS, WHICH IS THE ONLY PROCESS THERE IS. A skill can only hand
# `post` a composer's return if it holds that return as a value, and it can
# only hold it by having called the composer in the same interpreter. The
# register is therefore not a narrowing of what a real caller can do; it is
# the statement of what a real caller already did.
#
# THE 50% CAP IS GONE (coordinator ruling 1, 2026-09-18). It let a caller buy
# an unvouched trailer with three lines of real answer: the reviewer walked
# through it at `901305df` with a body that was 47% trailer. A door a caller
# can widen with an argument is not a door, so there is no share to spend —
# every line of `relayed` is vouched or the turn refuses. The relay composed
# behind another door (the Friday wrap's plate cut) is covered by having THAT
# door stamp it, not by a budget here.
# ---------------------------------------------------------------------------

#: Composers that live in their own module because they read that module's
#: own rows. DECLARED here, in one place, so the trailer guard's roster is
#: derived from a list rather than re-typed in the suite (REVIEW_LEAK4 F-6):
#: a named site that loses its door reds the guard instead of only the lane
#: suite.
#:
#: Each of these STAMPS ITS OWN RETURN. The first five reach the register
#: through `say`, which gates the sentence and stamps it in one call.
#: `wrap_cut` cannot: the Friday wrap's plate cut is a whole rendered block
#: carrying the customer's own row titles, and `say` gates a sentence, so it
#: stamps the text IT COMPOSED directly (RE-VERIFY_LEAK4 P-1) -- which means
#: the cut is vouched without the leak gate seeing it. Everything in the cut
#: is either this product's own render or a row title off the customer's
#: book, and a poisoned title is the write-then-declare residual under
#: another name (RE-VERIFY_LEAK4 P-3 / Q-5); gating the cut is a night-13
#: item, not a property of this door. The property that matters is the same
#: either way and it is the only one this list asserts: the stamp names the
#: composer's own output, never a string a caller handed in.
FOREIGN_COMPOSERS = {
    "chat_answer": "render_router_misses",
    "saved_to_meetings_footer": "brief_path",
    "undo_receipt_lines": "brain_undo",
    "undo_listing_lines": "brain_undo",
    "reprocess_undo_line": "brain_undo",
    "wrap_cut": "plate_view",
    "accounts_surface": "connector_config",
}

#: The subset of `FOREIGN_COMPOSERS` whose return is a BLOCK, relayed
#: through a door in a LATER section, rather than a turn rendered where the
#: composer is called. `wrap_cut` is the one: it returns a dict, the Friday
#: wrap carries `cut["text"]` down the recap and posts the whole turn at the
#: end, so the section that calls it has no door of its own and should not.
#:
#: DECLARED so the trailer guard can tell the two apart in one place
#: (RE-VERIFY_LEAK4 P-1). It changes nothing about the stamp — every name on
#: `FOREIGN_COMPOSERS` stamps its own return — and it does not excuse the
#: door: the guard requires the file that calls one of these to carry a
#: `relayed=` door somewhere in it, so deleting the wrap's post reds there.
RELAYED_BLOCK_COMPOSERS = frozenset({"wrap_cut"})

#: THERE IS NO SUCH THING AS A DOOR THAT STAMPS WHAT IT IS HANDED
#: (RE-VERIFY_LEAK4 P-1, 2026-09-18). `RELAY_STAMPERS` used to name one --
#: `quiet.wrap_post` -- on the theory that a door standing between a composer
#: and `post` may vouch for the text passing through it. It may not: the text
#: passing through it is an ARGUMENT, so the door vouched for any paragraph
#: any caller cared to hand it, and the suite that enumerated the stampers
#: read that as correct. The roster is gone and `wrap_cut` is on
#: `FOREIGN_COMPOSERS` instead, where the stamp is the composer's own return.
#:
#: The enumeration it existed for still holds, and now has one list to check
#: against: every `post(..., relayed=X)` site in the tree must trace X to a
#: composer in `FOREIGN_COMPOSERS` or to one of this module's own
#: `say`-ending composers. A relayed composer on neither reds the guard.

#: Bounded so a long-running process cannot grow one composed line at a time.
_COMPOSED_MAX_LINES = 512
_COMPOSED_HASHES: dict = {}


def _norm_line(text) -> str:
    """One line, whitespace-normalised. A relay is byte-exact by the relay
    check; the register is asked about the LINE, so a re-wrapped copy of a
    composer's own line is still that line."""
    return " ".join(str(text or "").split())


def _line_key(line) -> str:
    """The register's key: the sha256 of the normalised line.

    A HASH, not the line (coordinator ruling 1). The register is asked
    `is this line vouched?` and never `what was composed?`, so it has no
    reason to hold a customer's row title — or this product's own sentences —
    in clear for the life of the process.
    """
    return hashlib.sha256(_norm_line(line).encode("utf-8")).hexdigest()


def _register_composed(text: str) -> str:
    """Stamp `text` as a composer's own gate-passed output, in this process.

    MODULE-PRIVATE (RE-VERIFY 1 N-6). It was `register_composed`, a public
    name, and the reviewer forged an entry with it and posted a trailer. The
    callers are `say` and the composers named in `FOREIGN_COMPOSERS`, and
    every one of them passes ITS OWN RETURN. There is no public way into the
    register from this module, and no way at all to stamp a string that came
    in as an argument from outside (RE-VERIFY_LEAK4 P-1).
    """
    for line in str(text or "").splitlines():
        if not _norm_line(line):
            continue
        key = _line_key(line)
        if key not in _COMPOSED_HASHES and len(_COMPOSED_HASHES) >= _COMPOSED_MAX_LINES:
            _COMPOSED_HASHES.pop(next(iter(_COMPOSED_HASHES)))
        _COMPOSED_HASHES[key] = None
    return text


def uncomposed_lines(text) -> list:
    """The lines of `text` this process never saw a composer return."""
    out = []
    for line in str(text or "").splitlines():
        if _norm_line(line) and _line_key(line) not in _COMPOSED_HASHES:
            out.append(line.strip())
    return out


def composed_here(text) -> bool:
    """True when every line of `text` came back from a composer here."""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    return bool(lines) and not uncomposed_lines(text)


#: The fields on a ledger row whose value the CUSTOMER typed. Declared, and
#: short on purpose: a title is what they wrote on the row, `said` / `meant`
#: are the two halves of a router-miss in their own words, `quote` and
#: `phrase` are text lifted verbatim out of a turn they typed. `summary`,
#: `note`, `reason` and `resolution` are this product's sentences ABOUT their
#: row and are not on this list — declaring those would let the product
#: exempt its own prose by writing it to the book first.
CUSTOMER_TYPED_FIELDS = ("title", "said", "meant", "quote", "phrase")

#: Rows are named by seq (`123`, `"123"`, `"seq:123"` — the `change_ref`
#: shape `brain_undo` mints) or by a row id (`data.id`, `data.commitment_id`,
#: or the row's own top-level `id`).
_SEQ_REF_RE = re.compile(r"^(?:seq:)?(\d+)$")


def _row_ref_keys(ref) -> tuple:
    """(seq or None, id or None) for one entry of `customer_rows`."""
    if isinstance(ref, bool):
        return (None, None)
    if isinstance(ref, int):
        return (ref, None)
    text = str(ref or "").strip()
    if not text:
        return (None, None)
    m = _SEQ_REF_RE.match(text)
    if m:
        return (int(m.group(1)), None)
    return (None, text)


def customer_spans(workspace, customer_rows) -> list:
    """THE SPANS, DERIVED BY THE DOOR — never handed to it as text.

    F-1 / RE-VERIFY 1 N-2, and the coordinator's ruling 3 of 2026-09-18. The
    door used to take `spans` (the fragments) and `span_source` (where they
    came from), both as free text from the caller. Every rule over them was
    a rule about two strings the caller wrote, so `spans=X, span_source=X`
    was tautologically valid — and that degenerate shape was the ONLY worked
    example in the tree, which taught it. There is no way to verify the
    provenance of a string handed to a function, so the string is no longer
    handed over: the caller names ROWS, and this reads the customer-typed
    fields off those rows itself, through `events_io`, on the workspace the
    door was given.

    What that buys, in one sentence: to blank a paragraph out of the
    vocabulary scan, a caller now has to have persuaded the customer to type
    it onto a row of their own book first.

    A caller with no workspace declares nothing — there is no book to read,
    so there is nothing this door will take anyone's word for.
    """
    refs = [r for r in (customer_rows or ()) if _row_ref_keys(r) != (None, None)]
    if not refs or workspace is None:
        return []
    want_seqs = {seq for seq, _ in map(_row_ref_keys, refs) if seq is not None}
    want_ids = {rid for _, rid in map(_row_ref_keys, refs) if rid}

    # THE OWNER-TIER READER, not the raw one (`run_personal_firewall_test`'s
    # RAW_READ_ALLOW contract). Every surface `post` serves is the owner's own
    # chat, and the spans have to include a PERSONAL-LANE row's title: those
    # are the owner's own words too, and a door that could not blank them
    # would take a reply down over a promise they made to their spouse. The
    # org-scoped reader drops exactly those rows, so it is the wrong one here
    # -- and a raw `load_all` would add a new raw-read site to that guard's
    # list, which this door has no business doing.
    from events_io import load_events_owner_scoped
    events, _skipped = load_events_owner_scoped(workspace)
    out: list = []
    seen = set()
    for ev in events:
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        hit = False
        try:
            if ev.get("seq") is not None and int(ev.get("seq")) in want_seqs:
                hit = True
        except (TypeError, ValueError):
            pass
        if not hit and want_ids:
            for key in (ev.get("id"), data.get("id"), data.get("commitment_id")):
                if key and str(key) in want_ids:
                    hit = True
                    break
        if not hit:
            continue
        for field in CUSTOMER_TYPED_FIELDS:
            value = data.get(field)
            if isinstance(value, str) and value.strip() and value.strip() not in seen:
                seen.add(value.strip())
                out.append(value.strip())
    return out


def _missing_relay_lines(body: str, relayed: str) -> list[str]:
    """The lines of `relayed` that are not present in `body`.

    Deliberately NOT `plate_view.wrap_relay_check`, which asks the identical
    question: that function lives in a 3,500-line module this door would then
    import to post a two-line answer about which mail accounts you have. The
    shape is copied, the import is not; if the two ever need to be one, the
    helper belongs here and the plate calls it.
    """
    if not relayed:
        return []
    hay = str(body or "")
    return [ln.strip() for ln in str(relayed).splitlines()
            if ln.strip() and ln.strip() not in hay]


def _relay_out_of_order(body: str, relayed: str):
    """The first line of `relayed` that is present in `body` but not where
    the composer put it -- or None.

    RE-VERIFY_LEAK4 P-6. `_missing_relay_lines` asks "is every line there?",
    which a relay with its lines REVERSED, or with one line REPEATED, both
    satisfy: the reviewer posted both. Nothing new reached the reader either
    time, because every one of those lines was in the register and in the
    body already -- but the door's own words, and ten instruction files, said
    the relay was the composer's return byte-exact, and neither shape is.

    A relay is the composer's return, so the lines come back IN ORDER and AS
    MANY TIMES AS THE COMPOSER RETURNED THEM. One cursor through the body
    does both at once: each line is looked for after the previous line's
    match, so a reordered line runs off the end and a duplicated line needs a
    second occurrence of its own. A caller relaying a CONTIGUOUS RUN of what
    a composer returned is unaffected, which is what every site in the tree
    does.
    """
    if not relayed:
        return None
    hay = str(body or "")
    pos = 0
    for line in str(relayed).splitlines():
        text = line.strip()
        if not text:
            continue
        at = hay.find(text, pos)
        if at < 0:
            return text
        pos = at + len(text)
    return None


#: What a reader gets when even the composed answer cannot be posted.
#: A SIBLING of `surface_drivers.SURFACE_FAILED_LINES` (REVIEW_LEAK4 F-2),
#: kept here rather than there because these are the on-demand chat surfaces
#: and that roster is the three scheduled fires. Plain words, the phrase
#: offered again, nothing else - no class name, no label, no file.
POST_REFUSED_PHRASES = {
    "router-misses": "show me the router misses",
    "operator-report": "show me the operator report",
    "accounts": "what accounts do I have",
    "undo": "undo",
    "maintenance": "run maintenance now",
    "decision-log": "what did I decide",
    "inbox": "triage my inbox",
    "onboarding": "set up command room",
    "schedules": "set up command room schedules",
    "scan-for-commitments": "scan for commitments",
    "wrap": "weekly recap",
}

POST_REFUSED_FALLBACK = ("I could not put that answer together just now. Ask "
                         "me again and I will try once more.")


def refused_line(surface=None) -> str:
    """THE ONE SENTENCE A READER GETS when `post` refuses twice.

    The recovery the skills are told to run has two steps and this is the
    second: post the composer's return ALONE (it is already gated, and the
    paragraph that could not pass is the paragraph that should not have been
    written); if even that refuses, there is no answer to give, so say so in
    one honest sentence and offer the phrase again.

    In code, not only in prose (REVIEW_LEAK4 F-2): a sentence a reader meets
    on a failure is a sentence, and this product's rule is that sentences are
    composed by code.
    """
    phrase = POST_REFUSED_PHRASES.get(str(surface or ""))
    if not phrase:
        return POST_REFUSED_FALLBACK
    return ("I could not put that answer together just now. Say `" + phrase +
            "` and I will try again.")


def _check_declarations(body: str, workspace, customer_rows,
                        relayed: str) -> list:
    """Every declaration the caller made, checked - or `DeclarationError`.

    Returns the list of fragments that may be blanked out of the machine
    text. Raises rather than trimming: a caller that over-declared has a bug
    in its call, and silently honouring half of it is how a fence goes quiet.

    THE TWO DECLARATIONS, AND WHY NEITHER IS A STRING THE CALLER WRITES
    (REVIEW_LEAK4 F-1; RE-VERIFY 1 N-1/N-2/N-3; coordinator rulings 1 and 3).

      `customer_rows` - the rows of the customer's own book this reply was
        composed around. `customer_spans` reads their customer-typed fields
        off the book through `events_io` and those ARE the spans; there is
        no span-text argument and no source argument, so there is no pair of
        strings for a caller to make agree with each other. The only way to
        blank a paragraph is for that paragraph to be the text on a row of
        the customer's book.

        AND THIS DOOR CANNOT TELL WHO WROTE THAT ROW (RE-VERIFY_LEAK4 P-3).
        An earlier version of this paragraph said the spans were
        "customer-typed by construction". They are not: `title` is on
        `CUSTOMER_TYPED_FIELDS` and this product mints titles - extraction
        writes them off transcripts and mail - so a caller can write a row
        through the ordinary gated append path and then name it in the same
        turn, and six of the seven vocabulary classes come back. What the
        row requirement bought is real and is smaller than "by
        construction": that caller has spent a durable, receipted, undoable
        write on the customer's own book to say a sentence, instead of
        passing a string to a function. It is a RESIDUAL, it is pinned in
        `run_leak4_corpus_test` beside the private-name one, and closing it
        (ignoring rows minted by this process) is a night-13 decision, not
        a property this door has today.

      `relayed` - a composer's own return, VOUCHED IN FULL by the register or
        refused. There is no share a caller can spend: one line of `relayed`
        this process did not see a composer return is a refusal, whatever the
        rest of the turn looks like. The relay composed in another module
        (`plate_view.wrap_cut`'s plate cut) is vouched by THAT COMPOSER
        stamping its own return, which is why the cap this rule replaced is
        gone -- and never by a door it passes through on the way
        (RE-VERIFY_LEAK4 P-1: a door is handed its relay, so a door that
        stamps vouches for anything).
    """
    body = str(body or "")
    relayed = str(relayed or "")

    unvouched = uncomposed_lines(relayed)
    if unvouched:
        raise DeclarationError(
            "a relay is a composer's own return and this one is not: " +
            str(len(unvouched)) + " line(s) of it never came back from a "
            "composer in this run. Relay what a composer returned, not the "
            "reply. First unvouched line: " + repr(unvouched[0][:60]) +
            " (FIXTRAIN 6.1, REVIEW F-1, RE-VERIFY N-1/N-3).")

    # WHAT MAY BE BLANKED: the vouched relay's own lines, and the customer's
    # words read off the rows they were composed around. The two are not the
    # same thing and the distinction is `quiet.wrap_post`'s, kept here
    # deliberately - a relay is whole lines a composer produced, a customer
    # span is a fragment a sentence was composed AROUND.
    out = [ln.strip() for ln in relayed.splitlines() if ln.strip()]
    out.extend(customer_spans(workspace, customer_rows))
    return out


def post(text: str, *, surface: Optional[str] = None, workspace=None,
         customer_rows: Optional[Iterable] = None,
         relayed: str = "") -> str:
    """THE ONE DOOR EVERY REPLY GOES THROUGH - and it returns the text.

    SPEC FIXTRAIN v5.31.0 6.1 (R-25, HOLD driver 1). `say` gates ONE
    sentence, at the moment a composer builds it. That is the right shape for
    a composer and the wrong shape for a TURN: eleven of the thirteen leaks
    on the v5.31.0 record were appended AFTER a clean composed answer, by a
    chat that had already finished using the composer. Nothing in the tree
    read those paragraphs. This does.

    The skill calls this ONCE, with the final text of its whole reply - the
    composed answer and every sentence it would otherwise have typed around
    it - and prints WHAT THIS RETURNS, as the entire reply. A sentence that
    is not inside this return is a sentence nothing scanned, which is the
    defect, not a shortcut.

    THE ARGUMENTS, AND WHAT EACH ONE ADMITS.

      `relayed` - a gated composer's own output, to be relayed byte-exact.
        It is checked for PRESENCE, IN THE COMPOSER'S OWN ORDER AND
        MULTIPLICITY (`TrailerRelayError` when a line of it is missing, or
        when the lines are shuffled or one is repeated -- RE-VERIFY_LEAK4
        P-6), which is the only in-code answer to a model that reads a file
        and paraphrases instead of calling the composer. It is also
        checked for ORIGIN, IN FULL: every line of it must be one the
        register saw a composer return in this run, or `DeclarationError`.
        There is no share of a turn a caller may buy with a real answer.
      `customer_rows` - the rows of the customer's own book this reply was
        composed around, named by seq (`123`, `"seq:123"`) or row id. THE
        DOOR READS THEM, through `events_io`, and builds the declared spans
        itself from `CUSTOMER_TYPED_FIELDS`. There is no span-text argument:
        a caller cannot tell this door what the customer typed, it can only
        tell it which of their rows to go and read. With no `workspace`
        there is no book, so nothing is declared.

        WHAT IT CANNOT DO IS TELL WHO WROTE THE ROW (RE-VERIFY_LEAK4 P-3).
        `title` is a customer-typed field and this product also mints
        titles, so a caller that writes a row through the ordinary gated
        append path and names it in the same turn re-admits six of the
        seven vocabulary classes. That is a RECORDED RESIDUAL, pinned in
        `run_leak4_corpus_test`, not a hole in the shape: the cost of
        saying a sentence went from a string in a call to a durable,
        receipted, undoable write on the customer's own book.
      everything else - this product's own words, scanned in full.

    WHAT A DECLARATION ACTUALLY BLANKS, stated correctly (REVIEW_LEAK4 F-1
    corrects the first version of this paragraph, which said the opposite).
    A declared fragment is blanked out of the copy the VOCABULARY classes
    read - `chat_output_renderer.USER_TEXT_BLANKED_LABELS`, 32 labels wide,
    including `internal _hq path`, `internal data file`, `internal module
    reference`, `internal vocabulary roster`, `plugin script name`,
    `internal spec code` and `event seq leak`. It is NOT true that paths and
    ids are read regardless of what was declared: only `internal entity ID`,
    `internal commitment/proposal ID` and `opaque-id leak` survive a
    declaration, alongside the absolute-path scan, which reads the whole
    text because a machine path is dead on every other machine. That is
    exactly why the bound above is in code: a declaration is powerful, so it
    is not a thing a caller may hand itself.

    IT RAISES AND IT DOES NOT DEGRADE. `LeakDetectedError` from the gate,
    `TrailerRelayError` from the relay check, `DeclarationError` from the
    bound; the `except` below catches none of them in the sense that matters
    - it writes ONE receipt and re-raises, which is `quiet.wrap_post`'s shape
    (REVIEW_LEAK4 F-2). A reply that cannot pass is a reply whose extra
    paragraph should not have been written: post the composer's return
    alone, and if even that refuses, post `refused_line(surface)`.

    A denylist of words is explicitly NOT the fix (M, 2026-09-17). All
    thirteen recorded strings already raise when the gate is given them; what
    was missing was a reader, and this is the reader.
    """
    body = str(text or "")
    try:
        missing = _missing_relay_lines(body, str(relayed or ""))
        if missing:
            raise TrailerRelayError(
                (surface or "this surface") + ": the composed answer is "
                "relayed byte-exact and " + str(len(missing)) + " of its "
                "lines are not in the post (FIXTRAIN 6.1). First missing: " +
                repr(missing[0]))

        jumbled = _relay_out_of_order(body, str(relayed or ""))
        if jumbled is not None:
            raise TrailerRelayError(
                (surface or "this surface") + ": the relay is the composer's "
                "return, so its lines appear in the post IN THE COMPOSER'S "
                "OWN ORDER and as many times as it returned them; this one "
                "is shuffled or repeats a line (RE-VERIFY_LEAK4 P-6). First "
                "line out of order: " + repr(jumbled[:60]))

        declared = _check_declarations(body, workspace, customer_rows,
                                       relayed)
        vocab_text = None
        if declared:
            from docx_leak_scanner import blank_user_spans
            vocab_text = blank_user_spans(
                body, [s for s in declared if len(s) >= _POST_SPAN_MIN])

        from chat_output_renderer import validate_chat_output
        ws = str(workspace) if workspace is not None else None
        validate_chat_output(body, workspace=ws, surface=surface,
                             vocab_text=vocab_text)
    except Exception as exc:
        # A REFUSAL THAT LEAVES NO TRACE IS A TURN THAT JUST STOPS (F-2).
        # `quiet.wrap_post` writes ONE `surface_failed` receipt and re-raises;
        # so does this. `log_surface_failed` never raises and puts the
        # exception's CLASS on the receipt, never its message - a refusal
        # message names the label that refused, which is the one thing that
        # must not travel. Without a workspace root this behaves exactly as
        # before, for a preview or a test probe that has no book.
        if workspace is not None:
            try:
                from surface_drivers import log_surface_failed
                log_surface_failed(workspace, str(surface or "chat"), exc,
                                   mode="manual")
            except Exception:  # noqa: BLE001 - the re-raise is the point
                pass
        raise
    return body


# ---------------------------------------------------------------------------
# NAMES IN WORDS
# ---------------------------------------------------------------------------

# Job ids that do not survive a mechanical de-hyphenation into something a
# person would recognise. Everything else goes through `job_words`' generic
# path, so a job added tomorrow reads sensibly without an edit here.
_JOB_PHRASES = {
    "reconcile-sent": "closing things your sent mail already finished",
    "reconcile-chat": "closing things your chat already finished",
    "root-repair": "checking your workspace is where I left it",
    "question-expiry": "retiring questions nobody answered",
    "age-out": "resting items that have gone quiet",
    "past-meetings": "writing up meetings you have not looked at yet",
}


def job_words(job_id: Any) -> str:
    """A maintenance job id as ordinary words.

    The ids themselves are internal vocabulary — several are on the gate's own
    roster — so no surface ever prints one. An unknown id de-hyphenates, which
    is honest and readable; an empty one says so rather than guessing.
    """
    if not isinstance(job_id, str) or not job_id.strip():
        return "a background job"
    key = job_id.strip()
    if key in _JOB_PHRASES:
        return _JOB_PHRASES[key]
    return key.replace("-", " ").replace("_", " ")


_TIME_RE = re.compile(r"T(\d{2}):(\d{2})")


def _slot_words(slot: Any) -> str:
    """An ISO slot as a clock time a person reads. Never the ISO string."""
    if not isinstance(slot, str):
        return ""
    m = _TIME_RE.search(slot)
    if not m:
        return ""
    hour, minute = int(m.group(1)), int(m.group(2))
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    return f"{display}:{minute:02d}{suffix}"


def _count(n: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


# ---------------------------------------------------------------------------
# MAINTENANCE RUN NOW — cleanup/SKILL.md Step 0
#
# The recorded leak (item 3) was an "Internal record" paragraph: a raw record
# number, a command-line flag, the job ids verbatim, and a field name with the
# word UNKNOWN after it. All four came from printing the dispatcher's plan
# dictionary, which is a machine's report to another machine. This composer
# takes the same dictionary and says what HAPPENED.
# ---------------------------------------------------------------------------

def maintenance_answer(plan: dict, *, completed: Sequence = (),
                       failed: Sequence = (), findings: Iterable = (),
                       workspace=None) -> str:
    """The whole customer-facing answer to "run my maintenance".

    `plan` is `maintenance_dispatcher.dispatch_plan`'s return value; the rest
    is what the fire actually did. Returns ONE gated string.

    Nothing due is a first-class answer, not an error: the honest line plus
    the next slot, and nothing else. The dictionary's other keys — the
    workspace-repair verdict, the gap guard, the skipped-disabled ids — are
    read for MEANING here and never rendered; a customer whose workspace moved
    gets told that in words, once.
    """
    plan = plan if isinstance(plan, dict) else {}
    due = [d for d in (plan.get("due") or []) if isinstance(d, dict)]

    repair = plan.get("root_repair") or {}
    if isinstance(repair, dict) and repair.get("blocked"):
        return say("I could not find your workspace where it used to be, so I "
                   "stopped rather than run anything against the wrong folder. "
                   "Open Command Room from its usual place and ask me again.",
                   workspace=workspace)

    gap = plan.get("min_gap") or {}
    if isinstance(gap, dict) and gap.get("skipped"):
        return say("Everything here ran a few minutes ago, so there is "
                   "nothing to repeat.", workspace=workspace)

    if not due:
        line = "Nothing's due — everything ran on schedule."
        nxt = plan.get("next_up")
        if isinstance(nxt, dict):
            when = _slot_words(nxt.get("slot"))
            tail = f" at {when}" if when else ""
            line += f" Next up: {job_words(nxt.get('job_id'))}{tail}."
        return say(line, workspace=workspace)

    n_done = len(list(completed))
    n_failed = len(list(failed))
    found = [f.strip() for f in (findings or [])
             if isinstance(f, str) and f.strip()]

    lines: list[str] = []
    if n_done and not found and not n_failed:
        lines.append(f"{_count(n_done, 'job')} ran; nothing needed you.")
    elif n_done:
        lines.append(f"{_count(n_done, 'job')} ran.")
    else:
        lines.append("Nothing finished this time.")
    lines.extend(f"- {f}" for f in found)
    if n_failed:
        lines.append(f"{_count(n_failed, 'job')} did not finish; I will try "
                     "again at the next run.")
    return say("\n".join(lines), workspace=workspace)


# ---------------------------------------------------------------------------
# REPROCESS / SCAN FOR COMMITMENTS — scan-for-commitments/SKILL.md
#
# The recorded leak (item 2) named the activity log's file name, the dedup key
# as a pair of field names, and the counter variable. What the customer wanted
# to know was what got added and what was already there.
# ---------------------------------------------------------------------------

def reprocess_receipt(*, added: int, already_had: int = 0,
                      scanned: Optional[int] = None,
                      source_label: str = "your meetings and mail",
                      workspace=None) -> str:
    """The one sentence a reprocess or a commitment scan ends on.

    `already_had` is the count the duplicate check turned away. It is said as
    "already had", never as the key that produced it and never as a counter
    name — the customer's question is "did you double my list", and the answer
    to that is a number of items, not the mechanism that produced it.
    """
    added = max(0, int(added or 0))
    already_had = max(0, int(already_had or 0))
    head = f"Read back through {source_label}"
    if isinstance(scanned, int) and scanned > 0:
        head += f" ({_count(scanned, 'item')})"
    if added and already_had:
        body = (f" — {_count(added, 'new promise', 'new promises')} on your "
                f"list, and {already_had} I already had.")
    elif added:
        body = f" — {_count(added, 'new promise', 'new promises')} on your list."
    elif already_had:
        body = f" — nothing new; I already had {_count(already_had, 'item')}."
    else:
        body = " — nothing new to add."
    return say(head + body, workspace=workspace)


# ---------------------------------------------------------------------------
# DECISION LOG — decision-log/SKILL.md
#
# The recorded leak (item 7, a regression from 09-07) was the wrapper text
# around the log: the activity file's name and the view's path. Neither is the
# answer to "what did we decide".
# ---------------------------------------------------------------------------

def decision_log_answer(*, logged: Optional[str] = None,
                        found: Sequence = (), topic: Optional[str] = None,
                        workspace=None) -> str:
    """The prose this skill wraps around a decision — logged or retrieved.

    `logged` is the decision just written, in the customer's own phrasing.
    `found` is a list of already-composed one-line summaries for a retrieval.
    Both go through the gate; neither ever names where the record lives.
    """
    if logged:
        return say(f"Logged: {logged.strip()}", workspace=workspace)
    rows = [r.strip() for r in (found or []) if isinstance(r, str) and r.strip()]
    about = f" about {topic.strip()}" if topic and topic.strip() else ""
    if not rows:
        return say(f"I have no decision on record{about} yet.",
                   workspace=workspace)
    head = f"{_count(len(rows), 'decision')} on record{about}:"
    return say("\n".join([head] + [f"- {r}" for r in rows]),
               workspace=workspace)


# ---------------------------------------------------------------------------
# INBOX BILLING DOMAINS — inbox-triage/SKILL.md
#
# The recorded leak (item 9) was the triage telling the customer to go and
# edit a file under the workspace's internal folder, by path. The customer
# does not have a text editor open on that folder and should not need one: the
# door is a PHRASE, the product does the write, and the path never appears in
# anything anyone reads.
# ---------------------------------------------------------------------------

BILLING_DOMAINS_REL = Path("_hq") / "data" / "known-billing-domains.txt"


def billing_door_line(vendor: str = "Stone Supply", *, workspace=None) -> str:
    """The offer sentence. Names the phrase to say, never the file."""
    v = (vendor or "").strip() or "a vendor"
    return say(f"If mail from them is always billing, say `treat {v} as "
               f"billing` and I will stop sorting it as noise.",
               workspace=workspace)


def _domain_of(value: str) -> str:
    """The sender domain from a vendor phrase, an address, or a bare domain."""
    s = (value or "").strip().lower()
    if "@" in s:
        s = s.rsplit("@", 1)[-1]
    return s.strip().strip("<>").strip("/")


def add_billing_domain(workspace_root, vendor_or_domain: str) -> dict:
    """Write one sender domain into the workspace's billing-sender list.

    THE DOOR'S WRITER. Returns `{"domain", "added", "total"}` — counts and the
    domain itself, which is the customer's own vendor and theirs to see. The
    FILE is never in the return value and never in any sentence built from it.

    Idempotent, order-preserving, and it creates the list on first use. A
    value with no dot in it is not a domain and is refused rather than written
    (a customer saying `treat Stone as billing` needs a follow-up question,
    not a junk line in their list).
    """
    domain = _domain_of(vendor_or_domain)
    if not domain or "." not in domain:
        return {"domain": domain, "added": False, "total": 0,
                "needs_domain": True}
    path = Path(workspace_root) / BILLING_DOMAINS_REL
    existing: list[str] = []
    if path.exists():
        existing = [ln.strip().lower()
                    for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
    if domain in existing:
        return {"domain": domain, "added": False, "total": len(existing)}
    from atomic_write import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(existing + [domain]) + "\n")
    return {"domain": domain, "added": True, "total": len(existing) + 1}


def billing_door_receipt(result: dict, *, workspace=None) -> str:
    """What to say once the door was used. Counts and the vendor, no path."""
    result = result if isinstance(result, dict) else {}
    domain = (result.get("domain") or "").strip()
    if result.get("needs_domain"):
        return say("Which sender should I treat as billing? Give me the "
                   "address it comes from and I will remember it.",
                   workspace=workspace)
    if not result.get("added"):
        return say(f"I already treat mail from {domain} as billing.",
                   workspace=workspace)
    return say(f"Done — mail from {domain} counts as billing from now on.",
               workspace=workspace)


__all__ = [
    "say",
    "post",
    "TrailerRelayError",
    "DeclarationError",
    "FOREIGN_COMPOSERS",
    "RELAYED_BLOCK_COMPOSERS",
    "CUSTOMER_TYPED_FIELDS",
    "customer_spans",
    "composed_here",
    "uncomposed_lines",
    "refused_line",
    "POST_REFUSED_PHRASES",
    "POST_REFUSED_FALLBACK",
    "job_words",
    "maintenance_answer",
    "reprocess_receipt",
    "decision_log_answer",
    "billing_door_line",
    "add_billing_domain",
    "billing_door_receipt",
    "BILLING_DOMAINS_REL",
]
