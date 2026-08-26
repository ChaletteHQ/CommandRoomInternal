#!/usr/bin/env python3
"""
Meeting-prep v2 pipeline — the ONE generator's code layer (v4.5.2 S1).

WHY
---

The 2026-07-07/08 dogfood (FINDINGS_M_v451) found the highest-visibility
deliverable running on TWO generators:

  F-60  — the scheduled auto-prep produced a 209-word template fill while the
          on-demand 'prep me' path produced 1,683 words with a walk-out-with
          objective + CHANGED/DECIDE/NEEDED synthesis. Same surface name, two
          code paths. The auto path read no overnight email and missed the
          sweep-recovered, undated items about the exact meeting (F-44's
          blindness extended into prep).
  F-29  — the morning brief claimed "no prep brief" for a meeting whose prep
          file AND fire receipt were both on disk — the detector read a
          different signal than the writer left. Fixed by the per-brief
          receipt (`receipts.log_prep_receipt`) + this module's rule: the
          no-prep flag may only render when NO `prep_brief` receipt exists
          for that meeting id.
  F-29b — 'prep me' minted a SECOND, differently-slugged brief for the same
          meeting (`acme-bo-sample-session` vs `bo-sample`) because
          slugs came from attendee-name phrasing. Fixed by `prep_slug`: the
          slug is a pure function of the MEETING ID, so regeneration always
          resolves to the same file (refresh-in-place, never a sibling).

This module is the shared contract BOTH paths call — the scheduled auto-prep
(since SPEC BRIEFMERGE, the morning-brief fire's prep leg, which invokes
call-prep per meeting rather than carrying its own copy of the pipeline; before
that, the retired upcoming-meetings chat) and on-demand 'prep me'
(call-prep SKILL.md). The five-block layout, the visual layer's drop-empty
rule, and the sourced-talking-points floor are enforced HERE in code, not
re-improvised per fire. Depth (Standard/Deep) stays a synthesis-side concern
(the call-prep FRP1 config) — it changes how much signal the caller gathers,
never which generator runs.

THE FIVE BLOCKS (FINDINGS F-60 PROPOSAL, M-approved)
----------------------------------------------------

  1. Walk out with        -> exec_header.verdict (EXEC1; mandatory here)
  2. Changed Since Last Touch -> events + reschedules + overnight mail from
                             the declared mail backend scoped to attendee
                             addresses since the last touch (SPEC PREPSEAM1 —
                             the backend is whatever `connectors.email`
                             declares, resolved through the mail seams; naming
                             one product here meant the step read the wrong
                             inbox, or none, for every workspace that runs a
                             different one) (replaces the prior-brief-gated
                             "Since Your Last Brief" section)
  3. Decisions Needed     -> open decisions this meeting can settle
                             (decision log), with "Decisions Already On The
                             Record" as the don't-relitigate companion
  4. Owed — Both Directions -> two-column table from
                             commitment_state.match_commitments_to_meetings
                             (counterparty OR name-mention, undated included
                             — the F-44 fix carried into prep) + parked
                             discuss-later items for these attendees
  5. Talking Points / Questions to Ask -> every line cites a source
                             (code-enforced; no ungrounded filler)

VISUAL LAYER (M directive 2026-07-08)
-------------------------------------

Stat-tile band + relationship timeline strip + OWED as a two-column table.
Substrate-derived only. A tile with no data is DROPPED here (never handed to
the renderer); `brief_writer` additionally REFUSES to render an empty tile or
a one-point timeline, so an empty frame is structurally impossible.

Stdlib only. Pure helpers except the path/receipt resolvers (os.listdir).
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from brief_path import get_brief_path, get_brief_filename, _slugify  # noqa: E402
# The tree's ONE register per platform family. Imported (never copied) so the
# sourced-citation floor below can never disagree with what discovery knows
# about — SPEC PREPSEAM1 DD-2, extended to transcripts and chat by PREPSRC1.
# `tool_discovery` is stdlib-only at import.
from tool_discovery import (  # noqa: E402
    _CHAT_PLATFORM_HINTS,
    _MAIL_PLATFORM_HINTS,
    _TRANSCRIPT_PLATFORM_HINTS,
)
# The receipt side OWNS the per-source state vocabulary and its cleaner
# (SPEC PREPSEAM1 DD-3 / REVIEW_PREPSEAM1 N-3) — imported, never re-declared,
# so the render side cannot drift from what lands in events.jsonl.
from prep_leg import (  # noqa: E402
    SOURCE_ABSENT,
    SOURCE_FAILED,
    SOURCE_READ,
    normalize_sources,
)


class PrepContractError(ValueError):
    """A five-block contract violation (unsourced talking point, missing
    walk-out objective). Raised BEFORE any file is written — the caller
    rewrites the failing lines and re-assembles."""

    def __init__(self, message: str, violations: Optional[List[str]] = None):
        self.violations = violations or []
        # The violation TEXT rides `str(exc)`, not just `.violations`. The
        # scheduled path degrades a failed meeting with
        # `reason=f"{type(exc).__name__}: {exc}"` (prep_leg) and nothing
        # reads `.violations`, so a summary-only message put "1
        # prep-contract violation(s)" on the receipt and left the operator
        # with no way to learn WHICH rule failed.
        if self.violations:
            message = f"{message}: " + "; ".join(self.violations)
        super().__init__(message)


# ---------------------------------------------------------------------------
# Refresh-in-place identity (F-29b)
# ---------------------------------------------------------------------------

_MEETING_HASH_LEN = 8
_TITLE_SLUG_MAX = 40


def _meeting_hash(meeting_id: str) -> str:
    """Stable 8-hex digest of the meeting id — the identity token every
    Call_Prep filename for that meeting carries."""
    if not isinstance(meeting_id, str) or not meeting_id.strip():
        raise ValueError("meeting_id is required (the calendar event id)")
    return hashlib.sha1(meeting_id.strip().encode("utf-8")).hexdigest()[:_MEETING_HASH_LEN]


def prep_slug(meeting_id: str, title: Optional[str] = None) -> str:
    """THE slug for a prep brief — a pure function of the MEETING ID.

    F-29b's duplicate (`acme-bo-sample-session` vs `bo-sample`, one
    meeting) happened because each fire improvised a slug from attendee-name
    phrasing. Here the identity lives in the meeting-id hash suffix; the
    title prefix is readability only and never part of the identity check.

    >>> prep_slug("evt_abc123", "Bo Sample — SOD sync")[-9:] == "-" + _meeting_hash("evt_abc123")
    True
    >>> prep_slug("evt_abc123", "Bo Sample session") .endswith(_meeting_hash("evt_abc123"))
    True
    """
    h = _meeting_hash(meeting_id)
    prefix = _slugify(title or "")[:_TITLE_SLUG_MAX].strip("-")
    if prefix and prefix != "untitled":
        return f"{prefix}-{h}"
    return h


_EXISTING_BRIEF_RE = re.compile(
    r"^Call_Prep_(?P<slug>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.docx$"
)


def find_existing_prep_brief(workspace_root, meeting_id: str) -> Optional[str]:
    """Absolute path of the existing Call_Prep_*.docx for this meeting id,
    or None. Matches by the meeting-id hash embedded in the slug, so a brief
    survives title rewording and date moves. Newest date wins if historic
    duplicates exist (the pre-fix F-29b siblings)."""
    h = _meeting_hash(meeting_id)
    meetings_dir = Path(workspace_root) / "_hq" / "meetings"
    if not meetings_dir.is_dir():
        return None
    candidates = []
    for name in os.listdir(meetings_dir):
        m = _EXISTING_BRIEF_RE.match(name)
        if not m:
            continue
        slug = m.group("slug")
        if slug == h or slug.endswith("-" + h):
            candidates.append((m.group("date"), name))
    if not candidates:
        return None
    candidates.sort()
    newest = candidates[-1][1]
    return str(meetings_dir / newest).replace("\\", "/")


def resolve_prep_brief_path(
    workspace_root,
    meeting_id: str,
    *,
    title: Optional[str] = None,
    date_iso: str,
) -> dict:
    """The ONE path a prep brief for `meeting_id` may be written to.

    Refresh-in-place contract: if a brief for this meeting id already exists
    (matched by the hash suffix, regardless of title/date drift), THAT path is
    returned and the regeneration overwrites it — no sibling is ever minted.
    Otherwise the canonical new path via brief_path.get_brief_path.

    Returns {"path": <absolute str>, "slug": <slug>, "refresh": <bool>}.
    """
    existing = find_existing_prep_brief(workspace_root, meeting_id)
    if existing:
        m = _EXISTING_BRIEF_RE.match(os.path.basename(existing))
        return {"path": existing, "slug": m.group("slug"), "refresh": True}
    slug = prep_slug(meeting_id, title)
    return {
        "path": get_brief_path(str(workspace_root), "call_prep", slug, date_iso),
        "slug": slug,
        "refresh": False,
    }


# ---------------------------------------------------------------------------
# Visual layer — stat tiles (drop-empty enforced at build time)
# ---------------------------------------------------------------------------

def build_prep_tiles(
    *,
    days_since_last_touch: Optional[int] = None,
    you_owe: Optional[int] = None,
    they_owe: Optional[int] = None,
    oldest_owed_days: Optional[int] = None,
    touch_number: Optional[int] = None,
) -> List[dict]:
    """The stat-tile band data: days since last touch · owed counts (with
    oldest age when known) · engagement touch #.

    Drop rule (M directive): a tile whose datum is unknown (None) is DROPPED,
    never rendered as an empty frame. Zero is data ("0 owed") and renders.
    Returns [] when nothing is known — the caller then omits the band section
    entirely.
    """
    tiles: List[dict] = []
    if days_since_last_touch is not None:
        val = "today" if days_since_last_touch == 0 else f"{days_since_last_touch}d"
        tiles.append({"label": "Since last touch", "value": val})
    if you_owe is not None or they_owe is not None:
        # Owed counts render as one band cell each so a single unknown side
        # doesn't fabricate a zero for the other.
        if you_owe is not None:
            v = str(you_owe)
            if you_owe and oldest_owed_days is not None:
                v += f" · oldest {oldest_owed_days}d"
            tiles.append({"label": "You owe", "value": v})
        if they_owe is not None:
            v = str(they_owe)
            if you_owe is None and they_owe and oldest_owed_days is not None:
                v += f" · oldest {oldest_owed_days}d"
            tiles.append({"label": "Owed to you", "value": v})
    if touch_number is not None and touch_number > 0:
        tiles.append({"label": "Touch", "value": f"#{touch_number}"})
    return tiles


def tiles_to_counters(tiles: List[dict]) -> List[dict]:
    """The same band for the chat widget — the canonical renderer's
    `counters` shape ({label, value}), which is already what build_prep_tiles
    emits. Explicit alias so the two surfaces provably share one source."""
    return [dict(t) for t in tiles]


# ---------------------------------------------------------------------------
# Visual layer — relationship timeline strip
# ---------------------------------------------------------------------------

_TIMELINE_MAX_POINTS = 12


def build_relationship_timeline(
    points: Iterable[dict],
    *,
    current_label: str = "this meeting",
) -> List[dict]:
    """Normalize timeline points (meetings + key emails since engagement
    start, current meeting marked) for brief_writer's `timeline` element.

    Input points: {"date": "Jun 30" (display string), "label": str,
    "current": bool?} — the caller derives them from substrate events
    (meeting / interaction / email events for these attendees). Points with
    no date or no label are dropped (substrate-derived only). Fewer than 2
    surviving points -> [] (a one-point strip is an empty frame; the caller
    omits the section).

    Caps at the newest 12 points, always keeping the current-meeting marker.
    """
    norm: List[dict] = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        date = str(p.get("date") or "").strip()
        label = str(p.get("label") or "").strip()
        if not date or not label:
            continue
        norm.append({"date": date, "label": label, "current": bool(p.get("current"))})
    if not any(p["current"] for p in norm) and norm:
        # Ensure the strip always shows where TODAY sits — append the current
        # meeting as the last point when the caller didn't mark one.
        norm.append({"date": "now", "label": current_label, "current": True})
    if len(norm) < 2:
        return []
    if len(norm) > _TIMELINE_MAX_POINTS:
        current = [p for p in norm if p["current"]]
        rest = [p for p in norm if not p["current"]]
        norm = rest[-(_TIMELINE_MAX_POINTS - len(current)):] + current
    return norm


# ---------------------------------------------------------------------------
# OWED — both directions (block 4)
# ---------------------------------------------------------------------------

def _owed_cell(row: dict, now_date: Optional[str]) -> str:
    """One table cell: title + due phrase (+ confirm tag). 'no date set'
    renders plainly, never as a blank (F-44's undated items stay visible)."""
    title = (row.get("title") or "").strip() or "(untitled item)"
    due = row.get("due")
    if due:
        phrase = f"due {due}"
        if now_date and isinstance(due, str) and due < now_date:
            phrase = f"overdue (was due {due})"
    else:
        phrase = "no date set"
    cell = f"{title} — {phrase}"
    if row.get("pending_review"):
        cell += " · needs a quick confirm"
    return cell


def build_owed_table(
    matched_rows: List[dict],
    *,
    user_person_id: Optional[str],
    now_date: Optional[str] = None,
) -> Optional[dict]:
    """The OWED block's two-column table from
    `commitment_state.match_commitments_to_meetings` rows (this meeting's
    rows only — filter by meeting_id before calling).

    Direction: owner_id == user -> "You owe"; any other owner (an attendee,
    or unowned with the user as counterparty) -> "Owed to you". Undated and
    pending_review rows are INCLUDED — the matcher already guarantees no
    due-date filter (F-44); this function must not reintroduce one.

    Returns a brief_writer `table` dict ({headers, rows, column_widths}) or
    None when there is nothing owed in either direction (the caller drops
    the section — never an empty table frame).
    """
    you_owe: List[str] = []
    owed_to_you: List[str] = []
    for row in matched_rows or []:
        cell = _owed_cell(row, now_date)
        if user_person_id and row.get("owner_id") == user_person_id:
            you_owe.append(cell)
        else:
            owed_to_you.append(cell)
    if not you_owe and not owed_to_you:
        return None
    n = max(len(you_owe), len(owed_to_you))
    rows = [
        [
            you_owe[i] if i < len(you_owe) else "",
            owed_to_you[i] if i < len(owed_to_you) else "",
        ]
        for i in range(n)
    ]
    return {
        "headers": ["You owe", "Owed to you"],
        "rows": rows,
        "column_widths": [3.0, 3.0],
    }


def discuss_later_bullets(
    discuss_events: List[dict],
    *,
    attendee_person_ids: Iterable[str],
    attendee_names: Iterable[str],
) -> List[str]:
    """Parked discuss-later items (`commitment_to_discuss` events) filtered
    to these attendees, one bullet each. Matches by person_id OR by a name
    token in the event's stored person/title fields — the same
    counterparty-or-name-mention posture as the OWED matcher.

    MLK1 (2026-07-21): the list is retired — nothing writes new
    `commitment_to_discuss` events. This reader is deliberately KEPT as a
    drain-only fossil: open items are real parked intentions and must keep
    rendering until the backlog empties (deleting reader + data together
    would strand them invisibly)."""
    ids = {i for i in (attendee_person_ids or []) if i}
    name_tokens = set()
    for n in attendee_names or []:
        for tok in re.split(r"[^a-z0-9]+", str(n).lower()):
            if len(tok) >= 3:
                name_tokens.add(tok)
    out: List[str] = []
    for ev in discuss_events or []:
        if not isinstance(ev, dict) or ev.get("type") != "commitment_to_discuss":
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        pid = data.get("person_id") or data.get("counterparty_id")
        text_fields = " ".join(
            str(data.get(k) or "") for k in ("person", "person_name", "title", "summary")
        ).lower()
        matched = bool(pid and pid in ids) or any(t in text_fields for t in name_tokens)
        if matched:
            title = (data.get("title") or data.get("summary") or "").strip()
            if title:
                out.append(title)
    return out


# ---------------------------------------------------------------------------
# Block 5 — sourced talking points (no ungrounded filler, code-enforced)
# ---------------------------------------------------------------------------

# A line is "sourced" when it ends with a parenthesized cite naming where the
# claim came from — "(email, Jul 7)", "(meeting Jun 30)", "(commitment, May
# 22)", "(decision log)", "(transcript, Jul 2)". The vocabulary is the
# substrate's source families, so a decorative "(important!)" doesn't pass.
#
# THE MAIL HALF IS DERIVED, NEVER HAND-LISTED (SPEC PREPSEAM1 DD-2).
# It used to read a literal `gmail` alternative. That is a floor a talking
# point passes or fails on the CUSTOMER'S CHOICE OF MAIL PRODUCT: a line cited
# `(outlook thread, Aug 20)` carried exactly as much provenance as its Gmail
# twin and was rejected as ungrounded, so the whole five-block assembly raised
# `PrepContractError` and an Outlook or Superhuman workspace got no prep at
# all. `tool_discovery._MAIL_PLATFORM_HINTS` is the tree's ONE register of mail
# products, so reading its keys here means a provider added there joins this
# floor for free — the renamed-section-orphans class in reverse: one source of
# truth, no second list to forget.
_MAIL_SOURCE_WORDS = ("mail",) + tuple(sorted(_MAIL_PLATFORM_HINTS))

# `mail` does NOT subsume `email`: `\bmail\b` cannot match inside "email"
# (no word boundary between "e" and "m"), so both stay.
#
# The hand-written families are PATTERNS ("notes?" carries a real quantifier).
# The derived half is escaped, because a register key is data and data must
# never compile as a pattern.
#
# ONE TABLE, EVERY DERIVATION (PREPSRC1 review). The cite floor below, the
# cite→source-key crediting, and the `source_reads` alias map all read THIS
# table, so a family added here joins every derivation in one edit — two
# hand-synced lists would let a family pass the floor yet never credit,
# silently recreating the uncredited direction of the shipped defect. Each row
# is (source key, hand-written patterns, register words). Register words come
# from tool_discovery's platform registers (mail, transcripts, chat) — the
# DD-2 posture, extended.
#
# `(?<!pre-)meeting` excludes the ONE prefix that belongs to another family.
# A bare `\bmeeting\b` matched inside "pre-meeting", so the canonical operator
# cite "(you said, pre-meeting)" falsely credited Meeting transcripts. The
# first fix was `(?<![\w-])meeting`, which over-corrected: a hyphen is a word
# boundary, so it refused EVERY hyphen-prefixed spelling, and
# "(post-meeting notes, Jul 2)" — which cleared the floor before PREPSRC1 —
# began raising PrepContractError and shipping no prep at all. Excluding
# "pre-" alone keeps both directions correct.
#
# The operator family anchors on "pre-meeting" ONLY — deliberately narrow, so
# that decorative rhetoric ("Hold firm (you told me twice)") cannot clear the
# block-5 floor with no provenance behind it.
#
# "teams" rides the WORDS column, not the patterns column, because only the
# words column feeds `_SOURCE_KEY_ALIASES` below — a cite word that is not
# also an alias is a family the page can cite but a `source_reads` report can
# never name. It is hand-written because `_CHAT_PLATFORM_HINTS` keys are
# tool-id spellings (`ms365_teams`) that no human writes in a cite; the mail
# and transcript registers happen to use real citation words, so those derive
# cleanly and chat cannot. It is also a common English noun, so a cite like
# "(teams sync notes, Jul 2)" over-credits Chat messages — accepted, because
# the alternative is a Microsoft-stack workspace that can never cite chat.
_OPERATOR_SOURCE_KEY = "operator"
_SUBSTRATE_SOURCE_KEY = "substrate"

_FAMILY_TABLE = (
    ("mail", ("email",), _MAIL_SOURCE_WORDS),
    ("transcripts", (r"(?<!pre-)meeting", "transcript"),
     tuple(sorted(_TRANSCRIPT_PLATFORM_HINTS))),
    (_SUBSTRATE_SOURCE_KEY, ("commitment", "decision", "sweep"), ()),
    ("calendar", ("calendar",), ()),
    ("chat", ("chat",), ("teams",) + tuple(sorted(_CHAT_PLATFORM_HINTS))),
    ("notes", ("notes?",), ()),
    (_OPERATOR_SOURCE_KEY, ("pre-meeting",), ()),
)

# Words that satisfy the block-5 citation floor but name no CONSUMABLE source,
# so they credit nothing on the page. "call" is the case: a phone call the CEO
# remembers is a legitimate provenance claim, but routing it to the
# transcripts family put "Meeting transcripts" on a forwardable document for a
# call no transcript service ever saw. Floor-only keeps the line honest in
# both directions — the cite still passes, it just credits no source.
_FLOOR_ONLY_PATTERNS = ("call",)

_SOURCE_FAMILY_PATTERNS = tuple(
    p
    for _key, patterns, words in _FAMILY_TABLE
    for p in tuple(patterns) + tuple(re.escape(w) for w in words)
) + _FLOOR_ONLY_PATTERNS

_SOURCE_CITE_RE = re.compile(
    r"\(([^()]*\b(?:" + "|".join(_SOURCE_FAMILY_PATTERNS) + r")\b[^()]*)\)\s*$",
    re.IGNORECASE,
)


def unsourced_lines(lines: Iterable[str]) -> List[str]:
    """The lines that carry NO source cite. Empty list = all grounded."""
    bad: List[str] = []
    for line in lines or []:
        text = str(line).strip()
        if not text:
            continue
        if not _SOURCE_CITE_RE.search(text):
            bad.append(text)
    return bad


# ---------------------------------------------------------------------------
# The sources line — derived from consumption, never from the plan (PREPSRC1)
# ---------------------------------------------------------------------------
#
# One v5.14.0 prep fire shipped a .docx whose sources line was wrong in BOTH
# directions: it credited a session record that does not exist (a planned
# source that came back empty was listed as if consulted) and omitted two
# sources the render actually used (a live mail read, and facts the operator
# typed into the prep minutes before the render — neither was ever in the
# planned set). One mechanism explains both: the line described the PLAN.
#
# So the line is assembled here, from consumption evidence only. Two feeds,
# neither of which a plan can reach:
#
#   * `source_reads` — the generator's own per-source consumption report, the
#     SAME dict shape the prep leg already receipts (SPEC PREPSEAM1 DD-3:
#     `{"mail": "read" | "absent" | "failed"}`, keys extensible), cleaned by
#     the receipt side's own `normalize_sources`. Only `read` credits.
#     `absent` is OMITTED, deliberately not "marked absent": the call-prep
#     Gotchas rule (never name a missing source in the document) predates
#     this line and wins — the absence is recorded on the receipt, where it
#     belongs, not apologised for on the page.
#   * the page's CITE-MANDATED lines — talking points and questions (block-5,
#     code-enforced) plus changed-since bullets (documented floor: one fact
#     WITH its source + date). Those cites are consumption testimony, so a
#     family cited there is credited even when the caller forgot to record
#     the read (the uncredited-mail direction of the shipped defect, closed
#     structurally). Free prose bodies are never harvested — an ordinary
#     narrative parenthetical is decoration, not testimony.
#   * the substrate-built blocks, structurally — an owed table, timeline,
#     tile band, discuss list, or decisions-on-record block exists only
#     because substrate records were consumed to build it.
#
# Operator-supplied input rides its own flag: it has no connector to report a
# state and no substrate record to cite, which is exactly why the shipped line
# could never name it.

SOURCES_HEADING = "Sources"

# The hand-built-Sources tripwire (a whole-heading match, canonical spellings
# only). Anchored, not a prefix: `startswith("sources")` refused legitimate
# content headings ("Sources of risk" killed the whole render) while missing
# respellings ("Consulted Sources"). This catches the spellings a caller
# reaching for a source listing actually writes; the instruction layer
# carries the rule itself.
_HANDBUILT_SOURCES_RE = re.compile(
    r"^(?:consulted\s+|data\s+)?sources?(?:\s+(?:consulted|used|read|list))?$"
)


def _is_handbuilt_sources_heading(heading: str) -> bool:
    """True when a caller-supplied heading reads as a source LISTING.

    Trailing punctuation is stripped first: "Sources:" is the same heading as
    "Sources", and letting the colon through admitted a hand-built plan-based
    listing that then rendered NEXT TO the derived one — one document, two
    Sources sections, which is the failure this tripwire exists to prevent.
    """
    return bool(_HANDBUILT_SOURCES_RE.match(
        str(heading or "").strip().rstrip(":—-").strip().lower()))

# Source key -> the plain-language label the page carries. CLOSED vocabulary,
# deliberately: an unknown key is DROPPED from the page (its testimony stays
# on the receipt), never humanized onto it — a raw generator token on the
# CEO-facing line is the "no internal tokens" output-guard violation, and
# "forwardable-clean" (CONTRACT Rule 15) means every label here must read as
# meeting substance to a third party. Extending the vocabulary = one entry
# here (plus a `_FAMILY_TABLE` row if the source is citable per-line).
_SOURCE_KEY_LABELS = {
    "mail": "Email",
    "calendar": "Calendar",
    "transcripts": "Meeting transcripts",
    "chat": "Chat messages",
    "substrate": "Commitments and decisions on record",
    "notes": "Session notes",
    _OPERATOR_SOURCE_KEY: "Pre-meeting notes",
}

# The order the labels read on the page, and the ONLY thing that decides
# it — the declaration order above, which is a deliberate reading order
# (the connectors first, then what the workspace holds, then what the
# operator typed). Sorting the `source_reads` keys alone was not enough:
# a key can arrive from the read report OR from the page's own cites, so
# the SAME consumed set rendered "Email · Calendar" or "Calendar · Email"
# depending on which feed happened to carry each one. A brief that
# refreshes in place then rewrote its own sources line for no reason.
_LABEL_ORDER = {label: i
                for i, label in enumerate(_SOURCE_KEY_LABELS.values())}

# `source_reads` key -> canonical source key. The product-named half is
# DERIVED FROM `_FAMILY_TABLE`'s words column, not re-enumerated from the three
# registers: a generator that resolved a specific product reports under that
# product's name, and hand-listing the same registers twice is the
# two-synced-lists shape the table consolidation exists to end — it is exactly
# how "teams" came to be citable on the page yet unnameable in a report.
# The literal half is near-synonyms and singular spellings only.
#
# On G37/R1c: that guard derives its denylist from `_MAIL_PLATFORM_HINTS`
# alone, so it polices MAIL product names here and says nothing about chat or
# transcripts. This map names no mail product literally, which is what R1c
# actually requires — not the broader "no product name anywhere" an earlier
# comment here claimed.
#
# Keys must be bare tokens (`[A-Za-z0-9_]`): `normalize_sources` drops anything
# else, so a hyphenated "session-notes" would vanish with no error.
_SOURCE_KEY_ALIASES = {"email": "mail",
                       "commitments": _SUBSTRATE_SOURCE_KEY,
                       "decisions": _SUBSTRATE_SOURCE_KEY,
                       "session_notes": "notes",
                       "note": "notes",
                       "transcript": "transcripts",
                       "meetings": "transcripts"}
for _key, _patterns, _words in _FAMILY_TABLE:
    for _word in _words:
        _SOURCE_KEY_ALIASES.setdefault(_word, _key)
del _key, _patterns, _words, _word

# A platform-qualified calendar ("outlook calendar", "google calendar") is a
# CALENDAR read, not a mail read — but the mail register contributes
# "outlook", so ONE cite matched two families and the page credited an
# Email read that never happened. Invisible on Gmail workspaces, which is
# why it survived the first pass. Collapsing the qualifier before family
# matching leaves exactly one credit.
_QUALIFIED_CALENDAR_RE = re.compile(r"\b[\w-]+\s+calendar\b", re.IGNORECASE)

# Cite family -> source key, derived from the ONE `_FAMILY_TABLE` above —
# the same rows the cite floor compiles, so floor and credit cannot diverge.
_FAMILY_KEY_PATTERNS = tuple(
    (key,
     re.compile(
         r"\b(?:" + "|".join(tuple(patterns)
                             + tuple(re.escape(w) for w in words)) + r")\b",
         re.IGNORECASE))
    for key, patterns, words in _FAMILY_TABLE
)


def cited_source_keys(lines: Iterable[str]) -> List[str]:
    """The source keys a set of CITE-MANDATED lines testifies to — each
    line-final source cite mapped to its family's key. Sorted, deduped.

    Feed it ONLY lines whose contract demands a cite (talking points and
    questions, code-enforced by the block-5 floor; changed-since bullets,
    whose documented floor is one fact WITH its source + date per bullet).
    Free prose bodies are never harvested: an ordinary narrative
    parenthetical ("...(see the June call)") is decoration, not consumption
    testimony, and harvesting it fabricated credits."""
    keys = set()
    for line in lines or []:
        m = _SOURCE_CITE_RE.search(str(line).strip())
        if not m:
            continue
        cite = _QUALIFIED_CALENDAR_RE.sub("calendar", m.group(1))
        for key, pat in _FAMILY_KEY_PATTERNS:
            if pat.search(cite):
                keys.add(key)
    return sorted(keys)


def _source_label(key: str) -> str:
    """The page label for a source key, or "" for a key outside the closed
    vocabulary (the caller drops it — see `_SOURCE_KEY_LABELS`)."""
    k = str(key or "").strip().lower()
    k = _SOURCE_KEY_ALIASES.get(k, k)
    return _SOURCE_KEY_LABELS.get(k, "")


def consumed_source_labels(
    source_reads: Optional[dict] = None,
    *,
    cited_keys: Iterable[str] = (),
    operator_supplied: bool = False,
) -> List[str]:
    """The plain-language labels of every source the render CONSUMED — the
    one derivation both the page's Sources section and any chat mirror read.

    Credits are consumption-derived only: a `source_reads` entry credits IFF
    its state is `read`; a key the page's own cites testify to credits even
    unrecorded — and even over a contradicting `absent` report, because a
    fact on the page visibly claims the source and omitting the credit would
    recreate the uncredited direction of the shipped defect (fix the cite,
    not the line); operator-supplied input credits when the flag says the
    render consumed any. A planned source that came back `absent` (or
    `failed`) and contributed nothing NEVER appears — its record lives on
    the receipt, not on the page.
    """
    # The receipt side's cleaner runs first (REVIEW_PREPSEAM1 N-3, reused):
    # free-text keys drop, prose states become UNRECOGNISED_SOURCE — so a
    # generator's sentence can no more reach this page than the receipt. Every
    # surviving key is already a bare stripped token, so `_source_label` owns
    # the ONE remaining canonicalization (aliases + the closed vocabulary) and
    # the label dedup below collapses a product key and its family to one
    # entry.
    reads = normalize_sources(source_reads)
    # THE CONSUMPTION FENCE: only a source that actually resolved and was read
    # credits — widen it and the line describes the plan again.
    keys = [k for k, state in reads.items()
            if state.strip().lower() == SOURCE_READ]
    keys += [str(k).strip() for k in cited_keys]
    if operator_supplied:
        keys.append(_OPERATOR_SOURCE_KEY)
    labels = []
    for key in keys:
        label = _source_label(key)
        if label and label not in labels:
            labels.append(label)
    # Canonical order, so the line is a function of WHAT was consumed and
    # never of which feed reported it or in what order.
    # `.get`, not `__getitem__`: every label today comes from the closed
    # vocabulary, but a KeyError here would turn a vocabulary bug into a
    # dead render instead of a visibly-wrong line — and it did, reddening
    # the R5 pin by crashing the suite instead of failing its named check.
    # Unknown sorts last.
    return sorted(labels,
                  key=lambda lab: _LABEL_ORDER.get(lab, len(_LABEL_ORDER)))


def _sources_section(labels: List[str]) -> Optional[dict]:
    """The ONE spelling of the Sources section, from an already-derived
    label list. None when nothing was consumed (omit-don't-pad)."""
    if not labels:
        return None
    return {"heading": SOURCES_HEADING,
            "body": "Built from: " + " · ".join(labels) + "."}


def build_sources_section(
    source_reads: Optional[dict] = None,
    *,
    cited_keys: Iterable[str] = (),
    operator_supplied: bool = False,
) -> Optional[dict]:
    """The ONE way a Sources section reaches a prep page (PREPSRC1).

    Returns a brief_writer section dict over `consumed_source_labels`, or
    None when nothing was consumed (the caller omits the section —
    omit-don't-pad, like every other block).
    """
    return _sources_section(consumed_source_labels(
        source_reads, cited_keys=cited_keys,
        operator_supplied=operator_supplied))


# ---------------------------------------------------------------------------
# Assembly — the five-block section list (+ the consumption-derived Sources
# section, PREPSRC1), one order, both paths
# ---------------------------------------------------------------------------

def assemble_prep_sections(
    *,
    walk_out_with: str,
    meeting_details: str,
    changed_lines: Optional[List[str]] = None,
    decide_lines: Optional[List[str]] = None,
    decisions_on_record: Optional[List[str]] = None,
    owed_table: Optional[dict] = None,
    discuss_bullets: Optional[List[str]] = None,
    talking_points: Optional[List[str]] = None,
    questions: Optional[List[str]] = None,
    tiles: Optional[List[dict]] = None,
    timeline: Optional[List[dict]] = None,
    supporting_sections: Optional[List[dict]] = None,
    extra_sections: Optional[List[dict]] = None,
    changed_summary: Optional[str] = None,
    decide_summary: Optional[str] = None,
    needs: Optional[str] = None,
    source_reads: Optional[dict] = None,
    operator_supplied: bool = False,
) -> dict:
    """Compose the exec header + canonical section list for
    `brief_writer.make_brief(brief_kind="call_prep", ...)`. BOTH prep paths
    (scheduled auto-prep and on-demand 'prep me') call this — the section
    order and the block contracts live here, once.

    Contracts enforced (PrepContractError, before any render):
      - `walk_out_with` is mandatory — block 1 IS the brief's reason to exist.
      - every talking point / question carries a source cite (block 5).
      - no hand-built "Sources" section may arrive through
        `supporting_sections` / `extra_sections` (PREPSRC1) — the sources
        line is DERIVED from consumption by `build_sources_section`, never
        listed from the plan. Pass `source_reads` (the same
        `{"mail": "read"|"absent"|"failed"}` report the prep leg receipts)
        and `operator_supplied=True` when the render consumed input the CEO
        typed into the prep; the derived section is appended last.

    Drop rules applied (never an empty frame):
      - tiles: [] or None -> no "At a Glance" section.
      - timeline: <2 points -> no "Relationship Timeline" section.
      - owed_table None -> no "Owed — Both Directions" section.
      - any empty block list -> that section is omitted (omit-don't-pad).

    `supporting_sections` are pre-shaped brief_writer sections (Relationship
    Context, Where We Left Off, ...) inserted after the timeline;
    `extra_sections` (Cross-Project Insights, Risks / Watch-outs) append at
    the end. Depth (Standard/Deep) governs how much the CALLER gathers into
    these — never which generator runs.

    Returns {"exec_header": {...}, "sections": [...],
    "sources_consulted": [...]} — `sources_consulted` is the derived
    plain-language label list the appended Sources section carries (empty on
    legacy calls and when nothing was consumed); the chat Sources mirror
    reads it (call-prep SKILL.md).
    """
    violations: List[str] = []
    if not (walk_out_with or "").strip():
        violations.append(
            "walk_out_with is required — block 1 (the one-sentence concrete win) "
            "may not be omitted"
        )
    for label, lines in (("Talking Points", talking_points), ("Questions to Ask", questions)):
        for bad in unsourced_lines(lines or []):
            violations.append(
                f"{label}: no source cite — every line names where it came from "
                f"(e.g. '(email, Jul 7)'): {bad[:100]}"
            )
    # PREPSRC1 — a hand-built Sources section is the plan-based assembly this
    # module exists to end: the shipped defect credited a record that does not
    # exist and omitted the mail read and the operator's own input. The ONLY
    # sources line is the consumption-derived one appended below.
    for sec in list(supporting_sections or []) + list(extra_sections or []):
        heading = (sec or {}).get("heading") if isinstance(sec, dict) else ""
        if _is_handbuilt_sources_heading(heading):
            violations.append(
                "a hand-built Sources section is refused — the sources line is "
                "derived from consumption (pass source_reads / "
                "operator_supplied), never assembled from the planned set. "
                "If this heading names CONTENT (e.g. 'Sources of risk'), "
                "rename it so it does not read as a source listing"
            )
    if violations:
        raise PrepContractError(
            f"{len(violations)} prep-contract violation(s); "
            "rewrite the failing lines and re-assemble",
            violations,
        )

    verdict = walk_out_with.strip()
    if not verdict.lower().startswith("walk out with"):
        verdict = f"Walk out with: {verdict}"
    changed_lines = [str(x).strip() for x in (changed_lines or []) if str(x).strip()]
    decide_lines = [str(x).strip() for x in (decide_lines or []) if str(x).strip()]
    exec_header = {
        "verdict": verdict,
        "changed": (changed_summary or "").strip()
        or (changed_lines[0] if changed_lines else "Nothing new since last touch."),
        "decide": (decide_summary or "").strip()
        or (decide_lines[0] if decide_lines else "Nothing — execution call."),
        "needs": (needs or "").strip() or "Nothing from you.",
    }

    sections: List[dict] = []
    tiles = [t for t in (tiles or []) if t]
    if tiles:
        sections.append({"heading": "At a Glance", "tiles": tiles})
    sections.append({"heading": "Meeting Details", "body": meeting_details})
    timeline = timeline or []
    if len(timeline) >= 2:
        sections.append({"heading": "Relationship Timeline", "timeline": timeline})
    for sec in supporting_sections or []:
        sections.append(sec)
    if changed_lines:
        sections.append({"heading": "Changed Since Last Touch", "bullets": changed_lines})
    on_record = [str(x).strip() for x in (decisions_on_record or []) if str(x).strip()]
    if on_record:
        sections.append({"heading": "Decisions Already On The Record", "bullets": on_record})
    if decide_lines:
        sections.append({"heading": "Decisions Needed", "bullets": decide_lines})
    if owed_table:
        sections.append({"heading": "Owed — Both Directions", "table": owed_table})
    discuss = [str(x).strip() for x in (discuss_bullets or []) if str(x).strip()]
    if discuss:
        sections.append({"heading": "Parked to Discuss", "bullets": discuss})
    tp = [str(x).strip() for x in (talking_points or []) if str(x).strip()]
    if tp:
        sections.append({"heading": "Talking Points", "bullets": tp})
    qs = [str(x).strip() for x in (questions or []) if str(x).strip()]
    if qs:
        sections.append({"heading": "Questions to Ask", "bullets": qs})
    for sec in extra_sections or []:
        sections.append(sec)

    # PREPSRC1 — the sources line, derived from what THIS assembly actually
    # consumed. The feeds and the reasoning behind each are documented once,
    # above under "The sources line". Two details that live HERE because they
    # are properties of the assembly rather than of the derivation:
    #
    # The gate is `is not None`, and the difference between `{}` and omitted
    # is LOAD-BEARING, not an oversight. `source_reads={}` is a PREPSRC1-aware
    # caller saying "I am reporting my reads, and there were none" — its page
    # still gets a sources line off its own cites, which is the whole
    # uncredited-direction fix. Omitting the kwarg entirely is a legacy caller,
    # and it gets no section at all so its document is unchanged. Collapsing
    # the two to truthiness silently removes the cite-derived credit from every
    # generator that honestly reports nothing read.
    #
    # The structural substrate credit is narrower than it first looked. An
    # owed table, a discuss list and a decisions-on-record block genuinely
    # cannot exist without commitment/decision records. A tile band
    # (`build_prep_tiles` — days-since-last-touch, touch number) and a
    # timeline ("meetings + key emails", per `build_relationship_timeline`)
    # can, so crediting them put "Commitments and decisions on record" on a
    # forwardable page that consulted neither — the credited-a-record-that-
    # does-not-exist direction of the very defect this line exists to close.
    sources_consulted: List[str] = []
    if source_reads is not None or operator_supplied:
        cited = set(cited_source_keys(tp + qs + changed_lines))
        if owed_table or on_record or discuss:
            cited.add(_SUBSTRATE_SOURCE_KEY)
        sources_consulted = consumed_source_labels(
            source_reads,
            cited_keys=sorted(cited),
            operator_supplied=bool(operator_supplied),
        )
        src_section = _sources_section(sources_consulted)
        if src_section:
            sections.append(src_section)

    return {"exec_header": exec_header, "sections": sections,
            "sources_consulted": sources_consulted}


__all__ = [
    "PrepContractError",
    "prep_slug",
    "find_existing_prep_brief",
    "resolve_prep_brief_path",
    "build_prep_tiles",
    "tiles_to_counters",
    "build_relationship_timeline",
    "build_owed_table",
    "discuss_later_bullets",
    "unsourced_lines",
    "cited_source_keys",
    "consumed_source_labels",
    "build_sources_section",
    "assemble_prep_sections",
    "SOURCES_HEADING",
    "SOURCE_READ",
    "SOURCE_ABSENT",
    "SOURCE_FAILED",
]
