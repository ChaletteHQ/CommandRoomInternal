#!/usr/bin/env python3
"""OFFERS1 — where a ruling says act, nothing offers.

THE DEFECT THIS CLOSES (attended test v5.31.0, "Findings not tied to one
step"): eight surfaces in two days offered to do a thing a ruling already
says the product does. "Say the word and I'll close it" on an own-word
report; a three-way fork over six rows the customer's own sent mail had
proved dead; "want me to capture both" after an operator report; "say the
word and I'll rewrite the flagged lines"; an offer to clear doubled entries
on the decision log instead of saying plainly that this log never loses a
line.

M's rule (2026-09-03, pinned): a reversible automatic act beats a question,
every time. An offer is the offer form of a question — it hands the work
back to the reader in a politer sentence — so it is a defect on every
surface, not a style.

WHY THIS IS A MODULE AND NOT A PARAGRAPH IN A SKILL FILE. Every one of the
eight sites already had a prose ban when it offered: `people-crm` bans it at
its Gate 2b, `decision-log` at its append-only contract, `operator-report`
and `cleanup` in so many words. `exit_doors.apply_fact_closes`' own
docstring names the sentence it exists to replace. This tree has now proved
twice, on two lanes, that PROSE IS NOT A FENCE (spec night 13, ruling 13).

THE MODEL IS `plate_view.scan_no_pending_question` and it is followed
deliberately: the same flatten-first probe, the same customer-span blanking,
the same "the fix is the sentence, never the fence" error, and the same
exported prose-marker list so a template scan can tell a BAN from an
INSTANCE. This module owns no surface, so no surface owns the fence.

THE NARROWING MATTERS MORE THAN THE WIDTH (spec 6.1). An offer over
something the product may NOT do by itself is legitimate and must pass — an
unconfirmed add-person proposal is adjudicated by the reader, and asking is
the honest shape. What is refused is an offer whose object is a CLOSE, a
capture, a merge, a repair or a rewrite over rows the record already holds.
That is why the object roster below is short and why `add` and `save` are
not in it: both name acts the reader's own judgement governs.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ---------------------------------------------------------------------------
# THE SITES
# ---------------------------------------------------------------------------
# One name per recorded site, so a refusal says WHERE and a suite can pin the
# site rather than the pattern. `own-word no match` is here for the record:
# it closed in code before this lane (`exit_doors.own_word_no_match_line`)
# and is the shape every other composed line below copies.
SITE_OWN_WORD = "own-word no match"
SITE_PULL_UP = "pull up <person>"
SITE_DECISION_LOG = "decision log"
SITE_OPERATOR_REPORT = "operator report"
SITE_ROUTER_MISSES = "router misses"
SITE_CHECK_DELIVERABLES = "check my deliverables"
SITE_SHOW_SCHEDULING = "show scheduling"
SITE_ONBOARDING_PROBE = "onboarding probe"

RECORDED_SITES = (
    SITE_OWN_WORD, SITE_PULL_UP, SITE_DECISION_LOG, SITE_OPERATOR_REPORT,
    SITE_ROUTER_MISSES, SITE_CHECK_DELIVERABLES, SITE_SHOW_SCHEDULING,
    SITE_ONBOARDING_PROBE,
)


# ---------------------------------------------------------------------------
# THE SHAPES
# ---------------------------------------------------------------------------
# EVERY gap between tokens is `\s+`, never a literal space, and the probe is
# flattened before matching — both, for the reason `plate_view` gives: a
# composed reply is joined with newlines, so an offer can wrap mid-phrase and
# a pattern written with a plain space walks straight past it.
_GAP = r"(?:[^.]|\.(?=\d))"
_I_WILL = r"(?:i\s*(?:'|’)?\s*ll|i\s+will|we\s*(?:'|’)?\s*ll|we\s+will)"

# The object roster: the five classes named by the ruling — a CLOSE, a
# capture, a merge, a repair, a rewrite — over rows the record already holds.
# Deliberately SHORT. `add`, `save`, `send` and `draft` are NOT here: they
# name acts the reader's own judgement governs, and an offer over one of
# those is the honest shape, not the defect.
_OFFER_VERB_SRC = (
    r"(?:clos(?:e|ing)|clear(?:ing)?|captur(?:e|ing)|merg(?:e|ing)|"
    r"combin(?:e|ing)|collaps(?:e|ing)|de-?dup(?:e|ing)|deduplicat(?:e|ing)|"
    r"repair(?:ing)?|fix(?:ing)?|rewrit(?:e|ing)|smooth(?:ing)?|"
    r"tidy(?:ing)?|clean(?:ing)?|reopen(?:ing)?|remov(?:e|ing)|"
    r"delet(?:e|ing)|apply(?:ing)?|sort(?:ing)?|mark(?:ing)?)"
)

# THE VERB MUST GOVERN ROWS. A roster verb on its own is a word, not an act:
# the first cut refused "a **clear owner**" in the meeting write-up and the
# word `Apply` on a button label, both of them ordinary product English. So
# the object is the verb PLUS what it acts on — a pronoun, a determiner, a
# number or a particle. Measured on the customer corpus, this is the
# narrowing that takes the over-block count to zero.
_OFFER_OBJ_TAIL = (
    r"(?:\s+(?:it|them|they|those|these|that|this|the|both|all|any|each|"
    r"a|an|my|your|his|her|their|our|up|out|off|down|back|everything|"
    r"anything|\d+)\b)"
)
OFFER_OBJECT_SRC = _OFFER_VERB_SRC + _OFFER_OBJ_TAIL
OFFER_OBJECT = re.compile(OFFER_OBJECT_SRC, re.I)

# THE LEFT EDGE OF AN OBJECT IS NOT `\b`. A word boundary sits happily after
# a hyphen, so `sole-closing a rule` read as the roster verb `closing` and a
# question about how someone works was refused as an offer (review round 1,
# F-6: four instances of one sentence on the workspace's own prose). A
# hyphenated compound is one word and its tail is not this product's verb.
#
# EXCEPT WHERE THE PREFIX IS THE ACT ITSELF. `sole-`, `hand-` and `self-`
# say WHO does the thing, so `sole-closing` is a word about a person's
# habits and not this product's verb. But `auto-`, `bulk-` and `mass-` say
# HOW MUCH of the act the product proposes to do — they make the verb MORE
# of an act, not less of one — so `want me to just auto-close that` is the
# offer this lane exists to refuse, and the flat lookbehind let it through
# (review re-verify 1, the price named in fix round 2). The left edge is an
# ALTERNATION: no word character or hyphen to the left, OR one of the
# act-naming prefixes. A prefix joins this list only if the compound is
# still the product doing the act.
_ACT_PREFIXES = ("auto", "bulk", "mass")
_OBJ_LEFT = (r"(?:(?<![\w-])"
             + "".join(rf"|(?<=\b{p}-)" for p in _ACT_PREFIXES)
             + ")")

# TIER A — the idiom M named, refused over ANY object.
#
# "Say the word and I'll …" is not a phrasing, it is THE offer form: it says
# the product knows what to do, has decided not to do it, and is waiting to
# be asked. It reached a customer three times in two days on three different
# surfaces. Nothing a surface legitimately says needs it: where the product
# may not act, it states that in one sentence and names the door.
_TIER_A = (
    rf"(?:just\s+)?say\s+the\s+word\s+and\s+{_I_WILL}",
    r"(?:just|only)\s+say\s+the\s+word\b",
)

# TIER A, GATED — the same idiom with four fewer letters.
#
# "I can tidy them up — just say." is the offer form with `the word` left
# out, and it walked through the first cut (review round 1, F-5). So this
# arm is gated two ways.
#
# ONE: a roster object in the SAME sentence — `_GAP` cannot cross a full
# stop — read in both orders, because the recorded shape puts the act
# first and the invitation last.
#
# TWO: `just say` FOLLOWED BY THE PHRASE ITSELF is not an offer, it is the
# point-at-target form this product is told to use — "just say `needs your
# call`", "Just say 'set my timezone to Denver' and I'll fix it". The
# reader is being handed the words, not asked for permission. What is
# refused is the invitation with nothing to say after it.
_SAY_A_PHRASE = "(?!\\s*[`'\"“‘])"
_JUST_SAY = rf"(?:just|only)\s+say\b{_SAY_A_PHRASE}"
_TIER_A_GATED = (
    rf"{_JUST_SAY}{_GAP}{{0,60}}?{_OBJ_LEFT}{OFFER_OBJECT_SRC}\b",
    rf"{_OBJ_LEFT}{OFFER_OBJECT_SRC}\b{_GAP}{{0,60}}?{_JUST_SAY}",
)

# TIER B — an offer opener, over a roster object within one sentence.
_TIER_B_OPENERS = (
    r"want\s+me\s+to",
    r"(?:do|would)\s+you\s+want\s+me\s+to",
    r"would\s+you\s+like\s+me\s+to",
    r"shall\s+i",
    r"should\s+i",
    rf"{_I_WILL}\s+(?:happily\s+)?(?=.{{0,40}}\bif\s+you\b)",
    r"happy\s+to",
    r"i\s+can\s+(?=.{0,60}\bif\s+you\b)",
    r"let\s+me\s+know\s+if\s+you(?:\s*(?:'|’)?\s*d)?\s+"
    r"(?:like|want)(?:\s+me)?\s+to",
)
# The object's left edge is `_OBJ_LEFT`, not `\b`, here too: the three arms
# read the same roster and must read its left edge the same way, or the
# next hyphenated compound after a Tier-B opener reproduces F-6 in the one
# tier that was left behind (review re-verify 1, the at-merge note).
_TIER_B = tuple(rf"\b{o}\b{_GAP}{{0,40}}?{_OBJ_LEFT}{OFFER_OBJECT_SRC}\b"
                for o in _TIER_B_OPENERS)

# TIER C — a fork: a QUESTION offering two or more verbs over the same rows,
# at least one of them a roster object. This is the `pull up` shape — six
# rows the sent mail had proved dead, and three ways to say so.
_TIER_C = (
    rf"{_OBJ_LEFT}{OFFER_OBJECT_SRC}\b{_GAP}{{0,60}}?"
    rf"(?:\s+or\s+|\s*/\s*|\s+—\s+or\s+)"
    rf"{_GAP}{{0,60}}?\?",
    # A NUMBERED MENU CROSSES A FULL STOP. "1) Close the six dead rows.
    # 2) Keep them open. 3) Show me the mail." is the same menu as the one
    # written without full stops, and `_GAP` stops at a period — so the
    # first cut refused only the menu that happened to have no sentences
    # in it (review round 1, F-5). This arm alone spans sentences. The
    # probe is flattened before it is matched, so `.` reaches the whole
    # reply.
    rf"(?<![\w-])1\s*[).]\s.{{0,60}}?{_OBJ_LEFT}{OFFER_OBJECT_SRC}\b"
    rf".{{0,80}}?(?<![\w-])2\s*[).]",
    # A LETTERED MENU IS A MENU. "Options: (a) close all six, (b) leave
    # them, (c) ask me later." carries no digit and no offer opener and
    # walked through the first cut (F-5). TWO arms are required, and a
    # roster object, so an ordinary parenthesised "(a)" in a sentence is
    # not a menu on its own.
    #
    # A LETTER INSIDE A RUNNING CLAUSE IS NOT A LIST MARKER. Two arms and
    # a roster verb were not enough: "The report covers (a) how we close
    # the quarter and (b) the headcount plan", "the bucket bypasses (a)
    # the due-date requirement ... (b) the aging floor", "ask whether to
    # (a) skip this connector ... (b) pause" are ordinary writing, and
    # this arm refused every one of them (review re-verify 1, N-2 —
    # including one paragraph in the product's own reference files). A
    # real menu's first arm OPENS A LIST: it starts the reply, or it
    # follows the colon, semicolon or dash that introduced the list. So
    # the first letter is anchored to that boundary; a letter mid-clause
    # is not a menu marker.
    rf"(?:^|(?<=[:;—])|(?<=[:;—]\s))\s*\(?\s*a\s*\)"
    rf"(?=.{{0,200}}?(?<![\w-])\(?\s*b\s*\))"
    rf"(?=.{{0,200}}?{_OBJ_LEFT}{OFFER_OBJECT_SRC}\b)",
)

OFFER_SHAPE_PATTERNS = tuple(
    (name, re.compile(p, re.I))
    for name, group in (("offer idiom", _TIER_A + _TIER_A_GATED),
                        ("offer over an act", _TIER_B),
                        ("a fork over an act", _TIER_C))
    for p in group
)


# A sentence that FORBIDS the shape is not an instance of it — the same
# distinction `plate_view.ASK_FENCE_PROSE_MARKERS` draws, extended with the
# words a ban on OFFERING uses. They name the act of SAYING or OFFERING,
# never negation in general: a bare "never" would make the marker a
# whole-sentence kill switch (the defect `plate_view` records at its own
# list, re-verify F-2c).
OFFER_FENCE_PROSE_MARKERS = (
    "never offer", "never offers", "never offered", "not offered",
    "is never an offer", "never an offer", "not an offer", "no offer",
    "do not offer", "don't offer", "offering to", "instead of offering",
    "rather than offering", "never a menu", "never a fork",
    "refuses", "refused", "raises",
)


def fence_prose_markers() -> tuple:
    """Every marker that tells a BAN from an INSTANCE — this module's, plus
    `plate_view`'s, which is exported for exactly this reuse (spec 6.1). ONE
    list, shared, so the two template scans cannot disagree about which
    sentence is a fence."""
    try:
        from plate_view import ASK_FENCE_PROSE_MARKERS as shared
    except Exception:  # noqa: BLE001 - a scan without the plate still runs
        shared = ()
    return tuple(OFFER_FENCE_PROSE_MARKERS) + tuple(shared)


def _blank_customer_spans(text: str) -> str:
    """The customer's own words are never scanned for an offer — they are
    allowed to contain any sentence at all. Same marks, same blanking, as the
    plate's two gates, so the three cannot disagree about whose words these
    are."""
    try:
        from plate_view import _blank_user_spans
    except Exception:  # noqa: BLE001
        return str(text or "")
    return _blank_user_spans(str(text or ""))


class OfferError(ValueError):
    """A surface offered to do a thing a ruling says it does.

    M's rule of 2026-09-03: a reversible automatic act beats a question. An
    offer is that question in a politer sentence. THE FIX IS THE SENTENCE,
    NEVER THE FENCE: act and say so with a receipt and an `undo`, or say in
    one sentence that it was not done. `exit_doors.own_word_no_match_line` is
    the shape both halves copy."""


def scan_no_offer(text, *, where: str = "this surface") -> None:
    """Raise `OfferError` if `text` offers to do a thing the product does.

    Runtime fence. It does NOT apply the prose markers: a customer surface
    never says "never offer to close things" — only a template does, and
    `scan_skill_text` is the scan that reads templates.
    """
    probe = " ".join(_blank_customer_spans(text).split())
    if not probe:
        return
    for label, pat in OFFER_SHAPE_PATTERNS:
        m = pat.search(probe)
        if m:
            raise OfferError(
                f"{where}: {label} — {m.group(0)[:80]!r}. A close, a capture, "
                "a merge, a repair or a rewrite the record already proves is "
                "DONE where the product notices it, with a receipt and an "
                "`undo` (M's rulings 2026-09-03 and 2026-09-07); where it may "
                "not act, it says so in one sentence and offers nothing. "
                "Rewrite the sentence.")


def compose(where: str, line):
    """THE ONE RELAY every fenced site's sentence goes through.

    Every composed line below ends here, and every site's own snippet calls
    it on the whole reply before `surface_composers.post`. It returns what it
    was given, so a caller cannot use it and discard it by accident.
    """
    if isinstance(line, (list, tuple)):
        scan_no_offer("\n".join(str(x) for x in line), where=where)
        return line
    scan_no_offer(str(line or ""), where=where)
    return line


# ---------------------------------------------------------------------------
# THE TEMPLATE SCAN
# ---------------------------------------------------------------------------
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"”’')\]]*\s+")


def _sentences(line: str) -> list:
    return [s for s in _SENTENCE_SPLIT.split(line) if s.strip()]


_BLOCK_LEAD = re.compile(r"^\s*(?:>+\s*|[-*+]\s+|\d+[.)]\s+|#+\s+)")


def _blocks(text: str) -> list:
    """A template's paragraphs, each flattened to one string, with the line
    number it starts on.

    A MARKDOWN PARAGRAPH WRAPS, AND AN OFFER WRAPS WITH IT. The first cut of
    this scan read line by line and missed the onboarding probe's own offer
    outright — "if I've got you wrong, say the word and\\nI'll adjust it" —
    because the idiom straddled the wrap. The runtime fence flattens for
    exactly this reason (`plate_view` re-verify F-2a); so does this.
    """
    out = []
    buf: list = []
    start = 1
    for n, raw in enumerate(str(text or "").splitlines(), start=1):
        if not raw.strip():
            if buf:
                out.append((start, " ".join(buf)))
                buf = []
            continue
        if not buf:
            start = n
        buf.append(_BLOCK_LEAD.sub("", raw).strip())
    if buf:
        out.append((start, " ".join(buf)))
    return out


def scan_skill_text(text, *, where: str = "this file") -> list:
    """Every offer-shaped SENTENCE in a template, with the line its block
    starts on.

    PER SENTENCE, NOT PER LINE, ON PURPOSE. A SKILL.md paragraph is one line,
    so a per-line prose exemption lets one "never offer" at the top of a
    paragraph exempt every sentence under it — the shape GUARD2 is fixing in
    the corrections guard this same night (spec residual 5, N-8). A ban and
    an instance are told apart sentence by sentence here.

    Returns `[(lineno, matched, label), ...]`; the only reader is a suite on
    this tree's own files.
    """
    markers = fence_prose_markers()
    found = []
    for start, block in _blocks(str(text or "")):
        for sentence in _sentences(block):
            low = sentence.lower()
            if any(mk in low for mk in markers):
                continue
            probe = " ".join(sentence.split())
            for label, pat in OFFER_SHAPE_PATTERNS:
                m = pat.search(probe)
                if m:
                    found.append((start, m.group(0)[:80], label))
                    break
    return found


def scan_skill_file(path, *, where=None) -> list:
    p = Path(path)
    return scan_skill_text(p.read_text(encoding="utf-8"),
                           where=where or p.parent.name)


# ---------------------------------------------------------------------------
# THE COMPOSED LINES — one per site that still had to improvise one
# ---------------------------------------------------------------------------
# Each is ONE sentence (two at most), it names no id, no score and no row
# number, it ends in a full stop, and it either states an act that HAPPENED
# with its `undo`, or states plainly that nothing was done. None of them asks
# for anything back. `exit_doors.own_word_no_match_line` is the model.

NOTHING_PROVED_LINE = ("Nothing with them was proved finished, so I closed "
                       "nothing and left the list as it is.")


def fact_close_lines(result) -> list:
    """What the person surface says about the fact-proved close it just ran.

    THE REGRESSION (attended test v5.31.0, B1.1): `pull up <person>` listed
    six rows the customer's own sent mail had proved dead and offered a
    three-way fork over them. `exit_doors.apply_fact_closes` had already
    been built to close exactly those rows — its own docstring names the
    sentence "say the word and I'll close them" as what it replaces — and
    the surface still forked, because there was NO COMPOSED SENTENCE for the
    other two endings: nothing closed, and something held back.

    So all three endings are composed here and the site relays them.
    """
    result = result or {}
    receipt = str(result.get("receipt_line") or "").strip()
    withheld = int(result.get("n_withheld") or 0)
    lines = []
    if receipt:
        lines.append(receipt)
    if withheld == 1:
        lines.append("One more looks finished, but closing on evidence is "
                     "off, so it stands.")
    elif withheld > 1:
        lines.append(f"{withheld} more look finished, but closing on "
                     "evidence is off, so they stand.")
    if not lines:
        lines.append(NOTHING_PROVED_LINE)
    return compose(SITE_PULL_UP, lines)


def decision_log_duplicates_line(n: int) -> str:
    """The decision log's honest sentence about doubled entries.

    THE REGRESSION (B5.4): the log offered to clear four doubled entries
    "instead of clearing them". It could not have cleared them: this skill's
    own append-only contract says a duplicate line is QUARANTINED through the
    workspace cleanup and never deleted, and the four on the record are a
    repair on the operator path with M's word. So the surface neither offers
    nor acts — it says what it sees, and stops.

    AND IT NAMES NO DOOR (review round 1, F-2; coordinator ruling 1). The
    first cut ended "say `clean up my workspace` and the duplicates are set
    aside there", and that command cannot do it: cleanup marks duplicate
    event SEQS (`seq_health.detect_and_mark`), and a doubled decision HEADING
    is two distinct decisions with two distinct seqs, which that scan never
    sees. Directing the reader to a verb that does not exist is the same
    defect as offering one, one sentence over. The sentence ends at the
    honest half. A door that sets them aside is a new act and a new lane.
    """
    n = int(n or 0)
    if n <= 0:
        return compose(SITE_DECISION_LOG, "")
    if n == 1:
        return compose(SITE_DECISION_LOG,
                       "One decision below is logged twice. This log never "
                       "loses a line, so nothing was removed.")
    return compose(SITE_DECISION_LOG,
                   f"{n} decisions below are logged twice. This log never "
                   "loses a line, so nothing was removed.")


def duplicate_decisions(decisions) -> int:
    """How many of the OPEN decision rows handed in repeat one already logged.

    THE POPULATION IS THE CALLER'S, AND IT IS OPEN DECISIONS ONLY (review
    round 1, F-3; coordinator ruling 2). The wiring in `decision-log` hands
    this `decision_match.load_open_decisions`, so the count is over the open
    decisions and no other bucket. The rendered log also shows resolved and
    superseded decisions; counting those too is a bigger number over a wider
    population (on one private copy of a real book: 6 doubled out of 710
    open, against 29 doubled out of 1,144 decision events in all). The
    narrower number is the safer one and it is the one the shipped snippet
    feeds, so the sentence says what this count saw — not what every bucket
    of the log holds.

    THE ROWS COME IN; THIS MODULE READS NO BOOK. The canonical decision
    loader (`decision_match.load_open_decisions`) is the one reader on that
    path, and it is on the privacy firewall's D4(c) allowlist because its
    projection is owner-facing by construction. A second, unscoped ledger
    read from a fence module is exactly what that allowlist exists to stop,
    and a fence is not a surface: it has no business opening the book.

    Identity is the row's own heading — by whichever field that era's writer
    filled, `render_decision_log._decision_title`, the same heading the
    rendered log shows — on the same day. A repeat months later is a decision
    taken again, not a duplicate entry.
    """
    try:
        from render_decision_log import _decision_title as title_of
    except Exception:  # noqa: BLE001
        def title_of(data):
            d = data or {}
            for f in ("title", "summary", "decision"):
                if str(d.get(f) or "").strip():
                    return str(d[f]).strip()
            return ""
    seen = set()
    dupes = 0
    for ev in (decisions or []):
        if not isinstance(ev, dict):
            continue
        if str(ev.get("type") or "decision") != "decision":
            continue
        title = " ".join(str(title_of(ev.get("data") or {}) or "").lower().split())
        if not title:
            continue
        key = (title, str(ev.get("ts") or "")[:10])
        if key in seen:
            dupes += 1
        else:
            seen.add(key)
    return dupes


def deliverable_findings_line(*, flagged: int = 0, warned: int = 0,
                              scanned: int = 0) -> str:
    """What `check my deliverables` says after it scans.

    THE REGRESSION (attended test v5.31.0): "Say the word and I'll rewrite
    the flagged lines in your voice" and "Want me to smooth them?". This
    skill does not edit files — its own "What this skill does NOT do" says
    so, and widening what a surface may do is not this lane's (spec 6.2).
    So the line says what was found and that nothing was changed, and names
    the door that writes: the email or memo composer the reader already has.
    """
    flagged, warned, scanned = int(flagged or 0), int(warned or 0), int(scanned or 0)
    if flagged > 0:
        what = "one spot" if flagged == 1 else f"{flagged} spots"
        line = (f"Worth a look before any of these go out — {what} to "
                "change. I have not touched the files; ask for the rewrite "
                "and it goes through your own words.")
    elif warned > 0:
        what = "a spot" if warned == 1 else "a couple of spots"
        line = (f"Nothing that would embarrass you; {what} read a little "
                "AI-shaped. I have not touched the files.")
    elif scanned <= 0:
        line = ("Nothing was produced recently to check, so there was "
                "nothing to read.")
    else:
        what = ("1 recent document" if scanned == 1
                else f"{scanned} recent documents")
        line = (f"Scanned {what} — all clean. Nothing reads AI-written and "
                "nothing private leaked. Good to send.")
    return compose(SITE_CHECK_DELIVERABLES, line)


def schedule_card_line(n_items: int) -> str:
    """The one sentence beside the `show scheduling` card.

    THE REGRESSION (B2.9): the card rendered empty over a real book, the
    chat hand-printed nine rows with positional numbers, and offered twice.
    SCHEDVIEW1 fixed the key; this is the sentence for the ending that has
    no rows, so the next empty card has a composed answer instead of an
    improvisation.
    """
    n = int(n_items or 0)
    line = ("Nothing on your plate is waiting on a date right now."
            if n <= 0 else "")
    return compose(SITE_SHOW_SCHEDULING, line)


def onboarding_resume_line(phase_words: str = "") -> str:
    """The one verb on a half-finished setup over a book that has rows in it.

    ONBOARDGUARD1 refused the restart IN CODE; what was left was the
    sentence, and a sentence that offers a restart the code then refuses is
    a promised verb that cannot act. One verb, stated.
    """
    phase = " ".join(str(phase_words or "").split())
    tail = f" partway through {phase}" if phase else " partway through"
    return compose(SITE_ONBOARDING_PROBE,
                   "We started setting up your Command Room a while back "
                   f"and stopped{tail}. I'll pick up where we left off.")
