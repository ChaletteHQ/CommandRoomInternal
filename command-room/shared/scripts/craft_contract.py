#!/usr/bin/env python3
"""The craft file contract: one shape for how a person, a business or the owner does a kind of work (SPEC CRAFT1 step 1, cr1#396).

WHY THIS EXISTS
---------------
The owner has written method notes by hand for experts during engagements,
and the product could not find, index or trust them because nothing defined
what one is. This module is that definition. Every later craft step (recall,
capture, proposals, amendments) reads and writes through it, so the whole
product agrees on one shape.

WHAT A CRAFT FILE HOLDS (owner ruling B4, 2026-11-04)
-----------------------------------------------------
Method, the things not to do, and the specifics a model could not work out by
itself. Changing facts stay out and are pointed at. Worked examples sit beside
the file (`examples_dir`), loaded when needed, never inside it. A craft file
never carries a figure.

THE SHAPE
---------
    <!-- craft: subject=<entity id> scope=<slug> status=proposed|confirmed|superseded confidence=0.0-1.0 [consent=unknown|asked|given] -->
    <!-- last-updated: YYYY-MM-DD  last-writer: <skill>  tested: YYYY-MM-DD verdict=yes|partly|no -->

    # How <name> does <scope>

    ## Rules
    - R1. When <situation>, they <action>, because <reason>.
      Evidence: <pointer>, <date>. Confidence 0.8. Confirmed by owner <date>.

    ## Order of work
    1. <step>
       Evidence: <pointer>

    ## What they never do
    - R2. They never <thing>, because <reason>.
      Evidence: <pointer>

    ## Open questions
    - <question the material did not settle>

    ## History
    - <date> proposed from <sources>. <date> confirmed R1.

Binding parts of the contract, each checked by `validate_craft`:
  - both header comments; `tested:` may be absent (or `never`) on a file not
    yet tested, and a tested date needs a verdict;
  - all five sections, once each, and no other `##` section;
  - every item in Rules is numbered R1, R2 and so on; numbers are unique
    across the file so outputs can cite them;
  - every item in Rules, Order of work and What they never do carries an
    `Evidence:` pointer (cite or drop). A rule with no pointer FAILS;
  - the scope is one of the core eight or one the owner added
    (`add_scope`); a file under a renamed scope (sell) reads as its new one.

WHERE FILES LIVE (owner ruling 2026-10-24: the folder is the link)
------------------------------------------------------------------
    <project or business folder>/craft/<scope>.md   a project or a business
    _people/<person-slug>/craft/<scope>.md           a person
    _hq/craft/<scope>.md                             the owner
    _hq/craft/firm/<scope>.md                        the firm's own method
A subfolder of `craft/` (other than `_hq/craft/firm/`) holds worked examples
for the scope it is named after, and is never read as a craft file.

THE OWNER'S OWN COMPANY
-----------------------
Read, not invented: an org whose `relationship_type` is one of
`ledgers.OWN_RELATIONSHIPS` ("self", the self-org idiom capture_gate,
end_of_day and binding_gauge key on, and "operating", the schema's "the CEO
runs it"), plus the primary user's own org from `primary_user`. A person at
none of those orgs, and not the owner, is a third party.

CONSENT (owner ruling 2026-10-24: a flag, not a block)
------------------------------------------------------
A file about a third-party person carries `consent=unknown|asked|given`,
`unknown` when the header is silent. It is informational and never fails
validation or the gate. On any other subject it is ignored with a warning.

THE PRIVACY REFUSAL
-------------------
`gate_craft_text` refuses a file whose method text (rule, step, never and
open-question bodies; never the evidence pointers, header or history) carries:
  - a money figure, found by the exemplar scrub gate's own residual layer
    (`exemplars.residual_name_candidates`);
  - any other figure: a digit anywhere in the body (B4: a craft file never
    carries a figure), a currency sign, or a number written in words next
    to a currency or magnitude word ("five thousand dollars", "ten
    percent"). A rule cross-reference such as R2 is a citation, not a
    figure, and an ordinary number word ("one step at a time") passes;
  - a third-party person's name or an email address, found by the exemplar
    scrub gate's entity layer (`exemplars.scrub_exemplar_text`) run over the
    third-party people only;
  - a name-shaped run of capitalised words that is neither the owner's nor
    the owner's company's, found by the same residual layer: an untracked
    person the record cannot place is refused, fail-closed, exactly as that
    gate refuses one on its automatic path.
NOT DETECTED, AND LEFT OPEN FOR THE OWNER: matter content (the facts of a
client matter, as against the method). The scrub this reuses knows names,
emails and figures and nothing about a matter, and telling a matter's facts
from a method sentence needs a policy no ruling has set yet.

PUBLIC API
----------
  SECTIONS, STATUSES, VERDICTS, CONSENT_VALUES, FIXED_SCOPES
  CORE_DECLARATIONS, READERS, RENAMED_SCOPES
  scope_slug(label) -> str
  known_scopes(workspace_root=None) -> list[dict]
  canonical_scope(slug) -> str
  check_declaration(label, readers, triggers=None, parent=None) -> dict
  add_scope(workspace_root, label, *, user_confirmed, readers, rule,
            triggers=None, parent=None) -> dict
  reverse_scope_added(workspace_root, seq, *, undone_by) -> dict
  parse_craft(text) -> dict
  validate_craft(text, *, workspace_root=None, entities=None, path=None) -> dict
  privacy_findings(text, *, workspace_root=None, entities=None) -> list[dict]
  gate_craft_text(text, *, workspace_root=None, entities=None, path=None) -> dict
  CraftRefused
  load_entities(workspace_root) -> dict
  owner_person_id(entities) -> str | None
  own_company_org_ids(entities) -> set[str]
  is_third_party_person(entities, person_id) -> bool
  consent_flag(header, entities) -> str | None
  person_slug(name) -> str
  craft_path(workspace_root, subject_id, scope, *, entities=None) -> Path | None
  find_craft_files(workspace_root) -> list[Path]
  is_safe_path(workspace_root, path) -> bool
  examples_dir(craft_file) -> Path
  placement(workspace_root, path, header, *, entities=None) -> dict
  entity_label(entities, subject_id) -> str

stdlib only. Read paths never raise; `add_scope` and `craft_path` raise
ValueError on a bad scope, and `gate_craft_text` raises CraftRefused.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SECTIONS = ("Rules", "Order of work", "What they never do", "Open questions",
            "History")
#: Sections whose items are method and must each cite their evidence.
EVIDENCE_SECTIONS = ("Rules", "Order of work", "What they never do")
#: Sections whose item bodies the privacy refusal reads.
METHOD_SECTIONS = ("Rules", "Order of work", "What they never do",
                   "Open questions")
STATUSES = ("proposed", "confirmed", "superseded")
VERDICTS = ("yes", "partly", "no")
CONSENT_VALUES = ("unknown", "asked", "given")
DEFAULT_CONSENT = "unknown"

#: How confidence moves (SPEC CRAFT1 section 5, cr1#401). Confidence lives on
#: the rule; the file's is the mean of its rules', raised while its last
#: held-out test said yes. The owner's confirmation raises a rule, a
#: contradiction from new material lowers it, and the third contradiction
#: marks it superseded pending the owner. Bounded so no rule is ever
#: certain and none reads as worthless while it is still on file.
CONFIDENCE_MAX = 0.95
CONFIDENCE_MIN = 0.1
CONFIRM_STEP = 0.1
TEST_STEP = 0.05
CONTRADICTION_STEP = 0.15
SUPERSEDE_AFTER = 3
#: A file marked with this comment is exempt from the stale and never-tested
#: lists (the marker the always-loaded documents use).
EVERGREEN_MARK = "<!-- evergreen -->"
_EVERGREEN_RE = re.compile(r"<!--\s*evergreen\s*-->", re.IGNORECASE)

#: The core of eight, trade-neutral (owner ruling 2026-11-18, cr1#1391;
#: the first five were the fixed list of 2026-10-24), as (slug, label).
#: Win business replaced sell: it covers selling, fundraising, enrolment and
#: referral. Every owner gets these; anything specific to one trade or one
#: person arrives as a scope the owner adds (`add_scope`) or, later, one the
#: nightly sweep proposes (cr1#400, part B of cr1#1391).
FIXED_SCOPES = (
    ("review", "review"),
    ("decide", "decide"),
    ("write", "write"),
    ("run-a-meeting", "run a meeting"),
    ("win-business", "win business"),
    ("follow-up", "follow up"),
    ("prioritize", "prioritize"),
    ("delegate", "delegate"),
)
CORE_SLUGS = tuple(s for s, _ in FIXED_SCOPES)
#: A scope that was renamed: a file still filed under the old name reads as
#: the new one until `craft_capture.move_renamed_scope_files` moves it (the
#: move keeps the old file aside and can be undone). Nothing is lost either
#: way: the old file keeps loading under its new name.
RENAMED_SCOPES = {"sell": "win-business"}

#: The skills that read craft (the reader tasks), and the one-word tasks the
#: older callers pass for two of them. A reader is a skill's folder name;
#: `apply` is the owner's own "apply [name]'s method to this" and every
#: review ask of that shape, and reads every scope.
TASK_ALIASES = {"draft": "email-writer", "memo": "memo-writer"}
READERS = (
    "email-writer", "memo-writer", "one-pager-composer", "follow-up-ritual",
    "decision-memo-composer", "boardroom", "stress-test", "needs-your-call",
    "decision-log", "check-deliverables", "contract-review", "call-prep",
    "meeting-notes", "pipeline-tracker", "commitment-triage",
    "morning-briefing", "objectives", "team-intelligence",
)
DECISION_SKILLS = ("decision-memo-composer", "boardroom", "stress-test",
                   "needs-your-call", "decision-log")

#: Where each core scope applies (cr1#1391 build item 2): the skills that
#: read it, and the words that say a task is this kind of work. A draft and
#: a memo also read the decide scope of the people and businesses they are
#: for (the ruling on cr1#397); the owner's own decide file stays out of
#: them (the ruling on cr1#399, `craft_recall.LEAD_SCOPES`).
CORE_DECLARATIONS = {
    "write": {
        "readers": ("email-writer", "memo-writer", "one-pager-composer",
                    "follow-up-ritual"),
        "triggers": ("write", "draft", "email", "memo", "letter",
                     "one-pager", "note")},
    "decide": {
        "readers": DECISION_SKILLS + ("email-writer", "memo-writer"),
        "triggers": ("decide", "decision", "choose", "choice", "options",
                     "tradeoff", "trade-off")},
    "review": {
        "readers": ("check-deliverables", "contract-review"),
        "triggers": ("review", "check", "redline", "feedback", "audit")},
    "run-a-meeting": {
        "readers": ("call-prep", "meeting-notes"),
        "triggers": ("meeting", "call", "session", "agenda", "standup",
                     "huddle", "one on one", "check-in")},
    "win-business": {
        "readers": ("call-prep", "follow-up-ritual", "pipeline-tracker"),
        "triggers": ("sales", "sell", "selling", "prospect", "pitch",
                     "proposal", "demo", "discovery", "deal",
                     "fundraising", "investor", "donor", "enrolment",
                     "enrollment", "admissions", "referral", "intro",
                     "new business")},
    "follow-up": {
        "readers": ("follow-up-ritual", "commitment-triage"),
        "triggers": ("follow up", "follow-up", "chase", "nudge",
                     "reminder", "close the loop")},
    "prioritize": {
        "readers": ("morning-briefing", "commitment-triage", "objectives"),
        "triggers": ("priority", "priorities", "prioritize", "focus",
                     "plan the day", "plan the week")},
    "delegate": {
        "readers": ("commitment-triage", "team-intelligence"),
        "triggers": ("delegate", "assign", "hand off", "handoff")},
}

CRAFT_DIR = "craft"
FIRM_DIR = "firm"
PEOPLE_DIR = "_people"
HQ_DIR = "_hq"
#: The owner's added scopes live in the ledger, nowhere else (owner ruling
#: A1: the log is the record; spec AC10: no new store).
SCOPE_ADDED_EVENT = "craft_scope_added"
SCOPE_REMOVED_EVENT = "craft_scope_removed"
SCOPE_CHANGE_CLASS = "craft_scope"

#: Folders discovery never walks into (nor hidden ones, nor anything under
#: `_hq` other than its craft folder).
_SKIP_DIRS = frozenset({"_archive", "node_modules", "__pycache__"})
#: How deep below the workspace root discovery looks for a craft folder.
_MAX_DEPTH = 6

_SLUG_RE = re.compile(r"^[a-z][a-z-]{1,39}$")
_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z -]{1,39}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

_HEAD1_RE = re.compile(r"<!--\s*craft:(.*?)-->", re.DOTALL)
_HEAD2_RE = re.compile(r"<!--\s*last-updated:(.*?)-->", re.DOTALL)
_KV_RE = re.compile(r"([A-Za-z_-]+)=(\S+)")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_ITEM_RE = re.compile(r"^ {0,1}(?:[-*]|\d+[.)])\s+(.*)$")
_RULE_ID_RE = re.compile(r"^R(\d+)\.\s*(.*)$")
_CONF_RE = re.compile(r"\bConfidence\s+(-?[0-9]+(?:\.[0-9]+)?)")
_ANNOTATION_RE = re.compile(r"\b(?:Confidence\s|Confirmed by\b)")
_DIGIT_RE = re.compile(r"\d[\d,.:%/]*")
#: A rule cross-reference inside a body ("when R2 does not apply") is a
#: citation, not a figure.
_RULE_REF_RE = re.compile(r"(?<![A-Za-z0-9])R\d+(?![A-Za-z0-9])")
#: A currency sign anywhere in method text is a money figure.
_CURRENCY_SIGN_RE = re.compile("[$\u00a2\u00a3\u00a5\u20ac\u20b9]")
_NUMBER_WORDS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    "thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|dozen|half|a|an")
_MONEY_WORDS = "dollars?|bucks|euros?|pounds?|yen|rupees?|cents?"
_MAGNITUDE_WORDS = "percent|per cent|thousand|million|billion|k"
#: A number written in words is a figure only when it is an amount: next to
#: a currency or magnitude word ("five thousand dollars", "ten percent",
#: "half a million"). "One step at a time" stays method.
_WORD_FIGURE_RE = re.compile(
    r"(?<![A-Za-z])(?:" + _NUMBER_WORDS + r")(?:[\s-]+(?:"
    + _NUMBER_WORDS + r"|and))*[\s-]+(?:(?P<money>" + _MONEY_WORDS
    + r")|" + _MAGNITUDE_WORDS + r")(?![A-Za-z])", re.IGNORECASE)


class CraftRefused(ValueError):
    """Raised by `gate_craft_text`: the file fails the shape or the privacy
    refusal. `errors` lists shape errors, `findings` the privacy findings."""

    def __init__(self, message: str, *, errors=None, findings=None):
        super().__init__(message)
        self.errors = list(errors or [])
        self.findings = list(findings or [])


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

def scope_slug(label) -> str:
    """'Run a meeting' -> 'run-a-meeting'. Raises ValueError on a label that
    is not two to forty letters, spaces or hyphens (no digits: a scope never
    carries a figure)."""
    if not isinstance(label, str):
        raise ValueError("a scope is a short phrase")
    text = " ".join(label.strip().split())
    if not _LABEL_RE.match(text):
        raise ValueError(f"a scope is two to forty letters: {label!r}")
    slug = re.sub(r"[\s-]+", "-", text.lower()).strip("-")
    if not _SLUG_RE.match(slug):
        raise ValueError(f"a scope is two to forty letters: {label!r}")
    return slug


_SCOPE_CACHE: dict = {}


def _events_file(workspace_root) -> Path:
    try:
        from events_io import active_path
        return Path(active_path(workspace_root))
    except Exception:  # noqa: BLE001 - a stripped install still answers
        return Path(workspace_root) / HQ_DIR / "data" / "events.jsonl"


def _read_owner_scopes(workspace_root) -> list:
    """The owner's added scopes, folded from the ledger in order: a
    `craft_scope_added` row adds a slug, a `craft_scope_removed` row (the
    undo) takes it back. The ledger is the only source; no file is read.
    Memoised per ledger state, so a view over many files reads it once."""
    if workspace_root is None:
        return []
    path = _events_file(workspace_root)
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if key in _SCOPE_CACHE:
        return [dict(r) for r in _SCOPE_CACHE[key]]
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:  # noqa: BLE001 - a reader never fails on a bad ledger
        return []
    live: dict = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        slug = data.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            continue
        if ev.get("type") == SCOPE_ADDED_EVENT:
            label = data.get("label")
            parent = data.get("parent")
            live[slug] = {"slug": slug,
                          "label": label if isinstance(label, str) and label
                          else slug.replace("-", " "),
                          "seq": ev.get("seq"),
                          # Declared where it applies (cr1#1391). A scope
                          # added before the declaration existed has none:
                          # it loads on "apply my method" only, as before.
                          "readers": _str_tuple(data.get("readers")),
                          "triggers": _str_tuple(data.get("triggers")),
                          "parent": parent if parent in CORE_SLUGS
                          else None}
        elif ev.get("type") == SCOPE_REMOVED_EVENT:
            live.pop(slug, None)
    out = list(live.values())
    _SCOPE_CACHE.clear()
    _SCOPE_CACHE[key] = out
    return [dict(r) for r in out]


def _str_tuple(value) -> tuple:
    if not isinstance(value, (list, tuple)):
        return ()
    out = []
    for v in value:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return tuple(out)


def known_scopes(workspace_root=None) -> list:
    """The core of eight, then the scopes the owner added in this workspace,
    as [{"slug", "label", "fixed", "readers", "triggers", "parent"}]: where
    each applies (cr1#1391). A core scope has no parent. Never raises."""
    out = [{"slug": s, "label": lab, "fixed": True,
            "readers": CORE_DECLARATIONS[s]["readers"],
            "triggers": CORE_DECLARATIONS[s]["triggers"], "parent": None}
           for s, lab in FIXED_SCOPES]
    seen = {s for s, _ in FIXED_SCOPES} | set(RENAMED_SCOPES)
    for row in _read_owner_scopes(workspace_root):
        if row["slug"] in seen:
            continue
        seen.add(row["slug"])
        out.append({"slug": row["slug"], "label": row["label"],
                    "fixed": False, "readers": row["readers"],
                    "triggers": row["triggers"], "parent": row["parent"]})
    return out


def canonical_scope(slug):
    """The scope a file filed under `slug` reads as: a renamed scope's new
    name (`RENAMED_SCOPES`, sell -> win-business), else `slug` itself."""
    return RENAMED_SCOPES.get(slug, slug)


def reader_of(task) -> str:
    """The reader a task name stands for: `draft` is email-writer, `memo` is
    memo-writer, any other name is itself."""
    return TASK_ALIASES.get(task, task)


def scope_label(slug, workspace_root=None) -> str:
    slug = canonical_scope(slug)
    for row in known_scopes(workspace_root):
        if row["slug"] == slug:
            return row["label"]
    return str(slug or "").replace("-", " ")


_TRIGGER_RE = re.compile(r"[a-z][a-z' -]{1,39}")


def check_declaration(label, readers, triggers=None, parent=None) -> dict:
    """Check where a new scope applies, before anything is written. Returns
    {"slug", "label", "readers", "triggers", "parent"}; raises ValueError
    with a plain reason when a reader is missing or unknown, or the parent is
    not one of the core eight (cr1#1391: a scope with no declared reader is
    refused). The triggers default to the scope's own words."""
    slug = scope_slug(label)
    rs = _str_tuple(readers)
    if not rs:
        raise ValueError("a new kind of work needs at least one skill that "
                         "reads it (where it applies)")
    bad = [r for r in rs if reader_of(r) not in READERS]
    if bad:
        raise ValueError("these are not skills that read craft: "
                         + ", ".join(bad))
    rs = tuple(dict.fromkeys(reader_of(r) for r in rs))
    words = " ".join(label.strip().split()).lower()
    ts = tuple(t.lower() for t in _str_tuple(triggers)) or (words,)
    for t in ts:
        if not _TRIGGER_RE.fullmatch(t):
            raise ValueError(f"a trigger is a short phrase of letters: {t!r}")
    if parent is not None and parent not in CORE_SLUGS:
        raise ValueError("the parent of a kind of work is one of the core "
                         "eight: " + ", ".join(CORE_SLUGS))
    return {"slug": slug, "label": words, "readers": rs, "triggers": ts,
            "parent": parent}


def _scope_batch_id() -> str:
    """`csc_<UTC to the second>-<8 hex>`: the undo handle for one scope
    added (the `di_` precedent)."""
    import secrets
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"csc_{stamp}-{secrets.token_hex(4)}"


def add_scope(workspace_root, label, *, user_confirmed: bool = False,
              readers=None, triggers=None, parent=None, rule=None,
              source_skill: str = "workspace-manager",
              batch_id: Optional[str] = None) -> dict:
    """The owner extends the scope list (owner ruling 2026-10-24). One
    `craft_scope_added` row through the event gate; the ledger is the record
    and no store is written (owner ruling A1, spec AC10).

    `user_confirmed=True` is the owner's word and is required: nothing adds a
    scope on its own. Every scope declares where it applies (cr1#1391):
    `readers`, the skills that read it (at least one, from `READERS`), and
    `triggers`, the words that say a task is this kind of work (the label's
    words when none are given). `parent` is the core scope it sits on, if
    any: it then loads on top of that parent, never instead of it. `rule` is
    the first rule it holds, {"text", "evidence"}, both said: a scope is
    created only with at least one cited rule, so no craft file is ever
    empty. The rule's words pass the craft privacy refusal; its text is not
    stored here (it lands in the file), only its evidence pointer.

    The row carries `brain_batch_id` and `brain_change_class: craft_scope`,
    so `brain_undo.undo_batch(ws, result["batch_ref"], ...)` takes it back
    with an additive `craft_scope_removed` row.

    Returns {"slug", "status": "added" | "already_known", "seq",
    "batch_ref"}; an already-known scope (core, renamed, or added and not
    taken back) writes nothing. Raises ValueError on a bad label, a missing
    word, no reader, or no cited rule."""
    slug = scope_slug(label)
    if user_confirmed is not True:
        raise ValueError("adding a scope needs the owner's word "
                         "(user_confirmed=True)")
    if slug in {s["slug"] for s in known_scopes(workspace_root)} \
            or slug in RENAMED_SCOPES:
        return {"slug": slug, "status": "already_known", "seq": None,
                "batch_ref": None}
    decl = check_declaration(label, readers, triggers, parent)
    rule = rule if isinstance(rule, dict) else {}
    rule_text = " ".join(str(rule.get("text") or "").split())
    evidence = " ".join(str(rule.get("evidence") or "").split())
    if not rule_text or not evidence:
        raise ValueError("a new kind of work is added only with at least one "
                         "rule that cites its evidence")
    found = privacy_findings(
        {"items": [{"section": "Rules", "index": 1, "id": "R1",
                    "body": rule_text}]},
        workspace_root=workspace_root)
    if found:
        raise ValueError("the first rule carries "
                         + ", ".join(sorted({f["kind"] for f in found})))
    if not isinstance(source_skill, str) or not source_skill.strip():
        raise ValueError("add_scope needs a source_skill")
    from event_gate import append_event
    bid = str(batch_id).strip() if batch_id else _scope_batch_id()
    written = append_event(_events_file(workspace_root), [{
        "type": SCOPE_ADDED_EVENT,
        "source_skill": source_skill,
        "data": {"slug": slug, "label": decl["label"],
                 "user_confirmed": True,
                 "readers": list(decl["readers"]),
                 "triggers": list(decl["triggers"]),
                 "parent": decl["parent"],
                 "rule_evidence": evidence,
                 # The two undo stamps travel together (day_intent's rule).
                 "brain_batch_id": bid,
                 "brain_change_class": SCOPE_CHANGE_CLASS},
    }], holder=f"craft_contract:{source_skill}")
    return {"slug": slug, "status": "added",
            "seq": written[0].get("seq") if written else None,
            "batch_ref": {"kind": "brain_batch", "batch_id": bid}}


def reverse_scope_added(workspace_root, seq, *, undone_by: str,
                        source_skill: str = "workspace-manager") -> dict:
    """Take back ONE added scope (the `brain_undo` reverser for the
    `craft_scope` class). Additive: the `craft_scope_added` row stays in
    history and a `craft_scope_removed` row naming it lands. A scope that a
    later row already took back writes nothing (`already_removed`)."""
    from event_gate import append_event
    from events_io import load_events_owner_scoped
    try:
        target_seq = int(seq)
    except (TypeError, ValueError):
        raise ValueError(f"a scope reversal needs a seq, got {seq!r}")
    events, _skipped = load_events_owner_scoped(workspace_root)
    target = next((ev for ev in events if isinstance(ev, dict)
                   and ev.get("type") == SCOPE_ADDED_EVENT
                   and ev.get("seq") == target_seq), None)
    if target is None:
        raise ValueError(f"no added scope at seq {target_seq}")
    slug = (target.get("data") or {}).get("slug")
    live = {r["slug"]: r for r in _read_owner_scopes(workspace_root)}
    if slug not in live or live[slug].get("seq") != target_seq:
        return {"status": "already_removed", "slug": slug, "seq": target_seq}
    written = append_event(_events_file(workspace_root), [{
        "type": SCOPE_REMOVED_EVENT,
        "source_skill": source_skill,
        "data": {"slug": slug, "reversal_of": target_seq,
                 "undone_by": undone_by},
    }], holder=f"craft_contract:{source_skill}")
    return {"status": "removed", "slug": slug, "seq": target_seq,
            "removal_seq": written[0].get("seq") if written else None}


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _parse_header(text: str) -> tuple[dict, list, list]:
    header: dict = {}
    errors: list = []
    warnings: list = []
    m1 = _HEAD1_RE.search(text)
    if not m1:
        errors.append("the craft header line is missing "
                      "(<!-- craft: subject=... scope=... status=... "
                      "confidence=... -->)")
    else:
        pairs = dict(_KV_RE.findall(m1.group(1)))
        for key in pairs:
            if key not in ("subject", "scope", "status", "confidence",
                           "consent"):
                warnings.append(f"the craft header has an unknown field {key}")
        header["subject"] = pairs.get("subject")
        header["scope"] = pairs.get("scope")
        header["status"] = pairs.get("status")
        header["consent"] = pairs.get("consent")
        raw_conf = pairs.get("confidence")
        header["confidence_raw"] = raw_conf
        try:
            header["confidence"] = float(raw_conf) if raw_conf else None
        except ValueError:
            header["confidence"] = None
    m2 = _HEAD2_RE.search(text)
    if not m2:
        errors.append("the second header line is missing "
                      "(<!-- last-updated: ... last-writer: ... -->)")
    else:
        body = "last-updated:" + m2.group(1)
        lu = re.search(r"last-updated:\s*(\S+)", body)
        lw = re.search(r"last-writer:\s*(\S+)", body)
        te = re.search(r"tested:\s*(.+?)(?=\s+verdict=|\s*$)", body.strip())
        vd = re.search(r"verdict=(\S+)", body)
        header["last_updated"] = lu.group(1) if lu else None
        header["last_writer"] = lw.group(1) if lw else None
        header["tested_raw"] = te.group(1).strip() if te else None
        header["verdict"] = vd.group(1) if vd else None
        tested = header["tested_raw"]
        header["tested"] = (None if tested is None
                            or tested.lower() in ("never", "none")
                            else tested)
    return header, errors, warnings


def _finish_item(item: Optional[dict]):
    if item is None:
        return None
    item["body"] = " ".join(item.pop("_body")).strip()
    ev = " ".join(item.pop("_evidence")).strip()
    item["evidence_text"] = ev
    pointer = _ANNOTATION_RE.split(ev, maxsplit=1)[0] if ev else ""
    item["evidence"] = pointer.strip(" .,;:")
    conf = _CONF_RE.search(ev)
    try:
        item["confidence"] = float(conf.group(1)) if conf else None
    except ValueError:
        item["confidence"] = None
    return item


def parse_craft(text) -> dict:
    """Parse a craft file. Never raises; problems are reported by
    `validate_craft`. Returns {"header", "title", "sections", "items",
    "rules", "unknown_sections", "duplicate_sections", "header_errors",
    "header_warnings"}, where `sections` maps each section met to its items,
    an item being {"section", "index", "id", "body", "evidence",
    "evidence_text", "confidence", "line"}."""
    if not isinstance(text, str):
        text = ""
    header, h_err, h_warn = _parse_header(text)
    title = None
    sections: dict = {}
    unknown: list = []
    dupes: list = []
    current: Optional[str] = None
    item: Optional[dict] = None
    after_evidence = False
    in_comment = False

    def close():
        nonlocal item
        done = _finish_item(item)
        if done is not None and current in sections:
            sections[current].append(done)
        item = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        if line.startswith("# ") and title is None and current is None:
            title = line[2:].strip()
            continue
        sec = _SECTION_RE.match(line)
        if sec:
            close()
            name = sec.group(1).strip()
            canon = next((s for s in SECTIONS if s.lower() == name.lower()),
                         None)
            if canon is None:
                unknown.append(name)
                current = None
                continue
            if canon in sections:
                dupes.append(canon)
            sections.setdefault(canon, [])
            current = canon
            continue
        if current is None or not stripped:
            continue
        m = _ITEM_RE.match(line)
        if m and not line.startswith("  "):
            close()
            first = m.group(1).strip()
            rid = _RULE_ID_RE.match(first)
            item = {"section": current,
                    "index": len(sections[current]) + 1,
                    "id": f"R{int(rid.group(1))}" if rid else None,
                    "_body": [rid.group(2) if rid else first],
                    "_evidence": [], "line": lineno}
            after_evidence = False
            continue
        if item is None:
            continue
        if stripped.startswith("Evidence:"):
            item["_evidence"].append(stripped[len("Evidence:"):].strip())
            after_evidence = True
        elif after_evidence:
            item["_evidence"].append(stripped)
        else:
            item["_body"].append(stripped)
    close()

    items = [it for name in SECTIONS for it in sections.get(name, [])]
    return {"header": header, "title": title, "sections": sections,
            "items": items, "rules": [it for it in items if it["id"]],
            "unknown_sections": unknown, "duplicate_sections": dupes,
            "header_errors": h_err, "header_warnings": h_warn}


def _item_ref(item: dict) -> str:
    if item.get("id"):
        return item["id"]
    word = {"Order of work": "step", "Open questions": "question",
            "History": "entry"}.get(item["section"], "item")
    return f"{word} {item['index']}"


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def _valid_date(value) -> bool:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_craft(text, *, workspace_root=None, entities=None,
                   path=None, extra_scopes=None) -> dict:
    """Check a craft file against the contract. Never raises.

    Returns {"ok", "errors", "warnings", "privacy", "consent", "header",
    "rules", "sections", "items"}. `ok` is True only when there is no shape
    error and no privacy finding. Consent never makes it False. A file
    filed under a renamed scope passes with a warning. `extra_scopes` are
    slugs about to be added (capture checks a new kind's file before it adds
    the kind)."""
    parsed = parse_craft(text)
    header = parsed["header"]
    errors = list(parsed["header_errors"])
    warnings = list(parsed["header_warnings"])

    if "subject" in header:
        subject = header.get("subject")
        if not subject or not _SUBJECT_RE.match(str(subject)):
            errors.append("the header names no subject")
        scope = header.get("scope")
        slugs = {s["slug"] for s in known_scopes(workspace_root)}
        slugs |= set(extra_scopes or ())
        if not scope:
            errors.append("the header names no scope")
        elif scope in RENAMED_SCOPES:
            warnings.append(f"the scope {scope} is now "
                            f"{scope_label(canonical_scope(scope))}; the file "
                            "reads as that until it is moved")
        elif scope not in slugs:
            errors.append(f"the scope {scope} is not on the scope list")
        status = header.get("status")
        if status not in STATUSES:
            errors.append(f"status must be one of {', '.join(STATUSES)}")
        conf = header.get("confidence")
        if conf is None or not 0.0 <= conf <= 1.0:
            errors.append("confidence must be a number from 0.0 to 1.0")
        consent = header.get("consent")
        if consent is not None and consent not in CONSENT_VALUES:
            errors.append("consent must be one of "
                          f"{', '.join(CONSENT_VALUES)}")
    if "last_updated" in header:
        if not _valid_date(header.get("last_updated")):
            errors.append("last-updated must be a date (YYYY-MM-DD)")
        if not header.get("last_writer"):
            errors.append("the header names no last-writer")
        tested = header.get("tested")
        verdict = header.get("verdict")
        if tested is not None and not _valid_date(tested):
            errors.append("tested must be a date (YYYY-MM-DD) or never")
        if tested is not None and verdict is None:
            errors.append("a tested file needs a verdict (yes, partly or no)")
        if verdict is not None and verdict not in VERDICTS:
            errors.append(f"verdict must be one of {', '.join(VERDICTS)}")
        if verdict is not None and tested is None:
            errors.append("a verdict needs the date it was tested")

    for name in SECTIONS:
        if name not in parsed["sections"]:
            errors.append(f"the {name} section is missing")
    for name in parsed["unknown_sections"]:
        errors.append(f"{name} is not a craft section "
                      f"(the sections are {', '.join(SECTIONS)})")
    for name in parsed["duplicate_sections"]:
        errors.append(f"the {name} section appears twice")

    seen_ids: set = set()
    for it in parsed["items"]:
        ref = _item_ref(it)
        if it["section"] == "Rules" and not it["id"]:
            errors.append(f"Rules {ref} is not numbered (R1, R2 and so on)")
        if it["id"]:
            if it["id"] in seen_ids:
                errors.append(f"{it['id']} is numbered twice")
            seen_ids.add(it["id"])
        if it["section"] in EVIDENCE_SECTIONS and not it["evidence"]:
            errors.append(f"{ref} in {it['section']} has no Evidence pointer")
        if it["confidence"] is not None and not 0.0 <= it["confidence"] <= 1.0:
            errors.append(f"{ref} has a confidence outside 0.0 to 1.0")
        if it["section"] == "Rules" and not it["body"]:
            errors.append(f"{ref} says nothing")
    if not parsed["rules"]:
        warnings.append("the file has no numbered rule yet")

    if path is not None and header.get("scope"):
        stem = Path(path).stem
        if stem != header["scope"]:
            errors.append(f"the file is named {stem} but its scope is "
                          f"{header['scope']}")

    ents = entities if entities is not None else (
        load_entities(workspace_root) if workspace_root is not None else None)
    consent = None
    if ents is not None and header.get("subject"):
        consent = consent_flag(header, ents)
        if header.get("consent") is not None and consent is None:
            warnings.append("consent applies only to a person outside your "
                            "company; it is ignored here")
        if not _record(ents, header["subject"]):
            warnings.append("the record this file names is not on file")

    privacy = privacy_findings(parsed, entities=ents)
    return {"ok": not errors and not privacy, "errors": errors,
            "warnings": warnings, "privacy": privacy, "consent": consent,
            "header": header, "rules": parsed["rules"],
            "sections": parsed["sections"], "items": parsed["items"]}


def gate_craft_text(text, *, workspace_root=None, entities=None,
                    path=None, extra_scopes=None) -> dict:
    """The write-time gate: return the validation result for a file that
    passes, raise CraftRefused for one that fails the shape or carries a
    figure, a third-party name or an email in its method text."""
    res = validate_craft(text, workspace_root=workspace_root,
                         entities=entities, path=path,
                         extra_scopes=extra_scopes)
    if res["ok"]:
        return res
    kinds = sorted({f["kind"] for f in res["privacy"]})
    parts = []
    if res["errors"]:
        parts.append(f"{len(res['errors'])} shape error(s)")
    if kinds:
        parts.append("it carries " + ", ".join(kinds))
    raise CraftRefused("craft file refused: " + "; ".join(parts),
                       errors=res["errors"], findings=res["privacy"])


# ---------------------------------------------------------------------------
# Privacy refusal (reuses the exemplar scrub gate)
# ---------------------------------------------------------------------------

def _names_of(rec: dict, fields=("canonical_name", "display_name",
                                  "legal_name")) -> set:
    out = set()
    for f in fields:
        v = rec.get(f)
        if isinstance(v, str) and v.strip():
            out.add(v.strip())
    for f in ("aliases", "nicknames"):
        for v in rec.get(f) or []:
            if isinstance(v, str) and v.strip():
                out.add(v.strip())
    return out


def _blank_names(text: str, names: Iterable[str]) -> str:
    for name in sorted(set(names), key=len, reverse=True):
        if len(name.strip()) < 3:
            continue
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(name)
                      + r"(?![A-Za-z0-9])", "they", text, flags=re.IGNORECASE)
    return text


def _tracked_name_re(name: str):
    """A whole-word match for a tracked name. An apostrophe is part of a
    word; a one-word name matches case-sensitively, so an alias that is also
    a common word does not refuse the common word."""
    one_word = len(name.split()) == 1
    return re.compile(r"(?<![A-Za-z0-9'’])" + re.escape(name)
                      + r"(?![A-Za-z0-9'’])", 0 if one_word else re.IGNORECASE)


def privacy_findings(text, *, workspace_root=None, entities=None) -> list:
    """Every figure, third-party name, email and unplaced name in the method
    text of a craft file, as [{"kind", "section", "item", "match"}]. `text`
    may be a parse from `parse_craft`. Evidence pointers, the header and the
    History section are not read. Never raises."""
    from exemplars import residual_name_candidates, scrub_exemplar_text

    parsed = text if isinstance(text, dict) and "items" in text \
        else parse_craft(text)
    ents = entities if entities is not None else (
        load_entities(workspace_root) if workspace_root is not None else {})
    ents = ents or {}

    owner = owner_person_id(ents)
    own_orgs = own_company_org_ids(ents)
    third, own_names = [], set()
    for p in _collection(ents, "people"):
        if is_third_party_person(ents, p.get("id"), owner=owner,
                                 own_orgs=own_orgs):
            third.append(p)
        else:
            own_names |= _names_of(p)
    for o in _collection(ents, "orgs"):
        if o.get("id") in own_orgs:
            own_names |= _names_of(o)
    ws = ents.get("workspace") if isinstance(ents.get("workspace"), dict) \
        else {}
    if isinstance(ws.get("user_name"), str):
        own_names.add(ws["user_name"])
    scrub_source = {"entities": {"people": third, "persons": third}}
    # Owner ruling of 2026-11-11 on #396 (option b): every tracked org and
    # project name and alias is refused in a rule body. The owner's own
    # company is not a counterparty, so its names stay allowed, as the
    # owner's own and the team's names do. A matter's facts written in plain
    # words are not detected here; the preview and confirm step of craft
    # capture (#398) is where a person catches them.
    tracked: set = set()
    for name in ("orgs", "threads"):
        for rec in _collection(ents, name):
            if name == "orgs" and rec.get("id") in own_orgs:
                continue
            tracked |= {n for n in _names_of(rec) if len(n.strip()) >= 3}

    out: list = []
    for it in parsed.get("items") or []:
        if it["section"] not in METHOD_SECTIONS or not it.get("body"):
            continue
        ref = _item_ref(it)

        def add(kind, match):
            out.append({"kind": kind, "section": it["section"], "item": ref,
                        "match": match})

        body = it["body"]
        taken: list = []
        for name in sorted(tracked, key=len, reverse=True):
            for m in _tracked_name_re(name).finditer(body):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                add("tracked org or project name", m.group(0))
        # The third-party scrub reads the ORIGINAL text, so no own-side
        # substitution can break a third-party name apart first (an owner
        # alias "Sam" must never turn a client's "Sam Stone" into
        # "they Stone"). Own-side names are blanked only afterwards, for
        # the residual layer.
        scrubbed, replacements = scrub_exemplar_text(body, scrub_source)
        for r in replacements:
            add("email" if "@" in r["original"] else "third-party name",
                r["original"])
        blanked = _blank_names(scrubbed, own_names)
        no_money = blanked
        for cand in residual_name_candidates(blanked):
            if cand.startswith("$"):
                add("money figure", cand.rstrip(",.;: "))
                no_money = no_money.replace(cand, " ")
            else:
                add("name the record cannot place", cand)
        for m in _CURRENCY_SIGN_RE.finditer(no_money):
            add("money figure", m.group(0))
        for m in _WORD_FIGURE_RE.finditer(no_money):
            add("money figure" if m.group("money") else "figure",
                m.group(0))
        no_refs = _RULE_REF_RE.sub(" ", no_money)
        for m in _DIGIT_RE.finditer(no_refs):
            add("figure", m.group(0).rstrip(".,:"))
    return out


# ---------------------------------------------------------------------------
# Entities: the owner, the owner's company, third parties
# ---------------------------------------------------------------------------

def load_entities(workspace_root) -> dict:
    """entities.json as a dict, or {} when absent or unreadable."""
    try:
        from entities_io import read_entities_json
        data = read_entities_json(Path(workspace_root) / HQ_DIR / "data"
                                  / "entities.json")
    except Exception:  # noqa: BLE001 - a reader never fails on a bad file
        return {}
    return data if isinstance(data, dict) else {}


def _collection(ents: dict, name: str) -> list:
    if not isinstance(ents, dict):
        return []
    from entities_io import entities_collection, unwrap_entities
    if not isinstance(unwrap_entities(ents).get(
            "threads" if name == "projects" else name), list):
        return []
    return [r for r in entities_collection(ents, name) if isinstance(r, dict)]


def _record(ents: dict, entity_id) -> Optional[dict]:
    if not entity_id:
        return None
    for name in ("people", "orgs", "threads"):
        for rec in _collection(ents, name):
            if rec.get("id") == entity_id:
                return dict(rec, _kind=name)
    return None


def owner_person_id(entities) -> Optional[str]:
    """The primary user's person id, through the one resolver."""
    if not isinstance(entities, dict) or not entities:
        return None
    try:
        from primary_user import resolve_primary_user_from_entities
        return resolve_primary_user_from_entities(entities)
    except Exception:  # noqa: BLE001
        return None


def _own_relationships() -> frozenset:
    try:
        from ledgers import OWN_RELATIONSHIPS
        return frozenset(OWN_RELATIONSHIPS)
    except Exception:  # noqa: BLE001 - a stripped install still answers
        return frozenset({"self", "operating"})


def _person_org_ids(person: dict) -> set:
    return {v for v in (person.get("org_id"), person.get("primary_org_id"))
            if isinstance(v, str) and v}


def own_company_org_ids(entities) -> set:
    """The org ids that are the owner's own company: the self and operating
    orgs, and the primary user's own org."""
    own_rel = _own_relationships()
    out = {o.get("id") for o in _collection(entities, "orgs")
           if str(o.get("relationship_type") or "").strip().lower() in own_rel
           and o.get("id")}
    owner = owner_person_id(entities)
    for p in _collection(entities, "people"):
        if owner and p.get("id") == owner:
            out |= _person_org_ids(p)
    return out


def is_third_party_person(entities, person_id, *, owner=None,
                          own_orgs=None) -> bool:
    """True when `person_id` is a person on file who is neither the owner nor
    at the owner's own company."""
    if not person_id:
        return False
    owner = owner if owner is not None else owner_person_id(entities)
    if person_id == owner:
        return False
    own_orgs = own_orgs if own_orgs is not None \
        else own_company_org_ids(entities)
    for p in _collection(entities, "people"):
        if p.get("id") == person_id:
            return not (_person_org_ids(p) & own_orgs)
    return False


def consent_flag(header, entities) -> Optional[str]:
    """The consent flag for a file's subject: the header's value, else
    `unknown`, for a third-party person; None for any other subject."""
    subject = (header or {}).get("subject")
    if not is_third_party_person(entities, subject):
        return None
    value = (header or {}).get("consent")
    return value if value in CONSENT_VALUES else DEFAULT_CONSENT


def entity_label(entities, subject_id) -> str:
    """A plain name for a file's subject. Never an id."""
    if subject_id and subject_id == owner_person_id(entities):
        return "You"
    rec = _record(entities, subject_id)
    if rec is None:
        return "someone not on file"
    name = (rec.get("canonical_name") or rec.get("display_name")
            or rec.get("legal_name") or "a record with no name")
    if rec["_kind"] == "orgs" and subject_id in own_company_org_ids(entities):
        return f"{name} (your firm)"
    return str(name)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def person_slug(name) -> str:
    """'Sam Sample' -> 'sam-sample': the folder name under `_people/`."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return slug or "person"


def _folder_of(thread: dict) -> Optional[str]:
    folder = str(thread.get("folder_name") or "").strip().replace("\\", "/")
    folder = folder.strip("/")
    if not folder or ".." in folder.split("/"):
        return None
    return folder


def _thread_org(thread: dict) -> set:
    return {v for v in (thread.get("org_id"), thread.get("affiliation_id"))
            if isinstance(v, str) and v}


def _subject_folder(entities, subject_id) -> Optional[str]:
    """The folder (relative, posix) a subject's craft files live in."""
    if not subject_id:
        return None
    if subject_id == owner_person_id(entities):
        return f"{HQ_DIR}/{CRAFT_DIR}"
    if subject_id in own_company_org_ids(entities) and _record(
            entities, subject_id) is not None:
        return f"{HQ_DIR}/{CRAFT_DIR}/{FIRM_DIR}"
    rec = _record(entities, subject_id)
    if rec is None:
        return None
    if rec["_kind"] == "people":
        return (f"{PEOPLE_DIR}/{person_slug(rec.get('canonical_name'))}"
                f"/{CRAFT_DIR}")
    if rec["_kind"] == "threads":
        folder = _folder_of(rec)
        return f"{folder}/{CRAFT_DIR}" if folder else None
    folders = sorted({_folder_of(t) for t in _collection(entities, "threads")
                      if subject_id in _thread_org(t) and _folder_of(t)})
    if len(folders) == 1:
        return f"{folders[0]}/{CRAFT_DIR}"
    return None


def craft_path(workspace_root, subject_id, scope, *,
               entities=None, pending=None) -> Optional[Path]:
    """Where the craft file for (subject, scope) lives, or None when the
    subject is not on file or has no single folder. Raises ValueError on a
    scope that is not on this workspace's list (`pending` names one about to
    be added, which capture places before it adds it)."""
    if scope not in {s["slug"] for s in known_scopes(workspace_root)} \
            and not (pending and scope == pending):
        raise ValueError(f"the scope {scope!r} is not on the scope list")
    ents = entities if entities is not None else load_entities(workspace_root)
    folder = _subject_folder(ents, subject_id)
    if folder is None:
        return None
    return Path(workspace_root) / folder / f"{scope}.md"


def examples_dir(craft_file) -> Path:
    """Worked examples for a craft file sit beside it, in a folder named for
    its scope: `craft/review.md` -> `craft/review/`."""
    p = Path(craft_file)
    return p.parent / p.stem


def is_safe_path(workspace_root, path) -> bool:
    """True when `path` is no symlink itself, and it and every folder above
    it, up to the workspace root, resolve inside the root. A craft file is
    never read through a link (cr1#396 review finding 3)."""
    try:
        root = Path(workspace_root).resolve()
        p = Path(path)
        if p.is_symlink():
            return False
        rel = p.absolute().relative_to(Path(workspace_root).absolute())
        cur = Path(workspace_root).absolute()
        for part in rel.parts:
            cur = cur / part
            if cur.is_symlink():
                return False
        p.resolve().relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return False
    return True


def find_craft_files(workspace_root) -> list:
    """Every craft file in the workspace: the `*.md` children of each
    `craft/` folder, plus `_hq/craft/firm/`. Skips the archive, hidden
    folders, `_hq` outside its craft folder, examples folders, and anything
    that is a symlink or resolves outside the workspace root."""
    root = Path(workspace_root)
    found: list = []
    if not root.is_dir():
        return found

    def md_files(folder: Path, names) -> list:
        out = []
        for name in names:
            p = folder / name
            if p.suffix == ".md" and p.is_file() and is_safe_path(root, p):
                out.append(p)
        return out

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel = here.relative_to(root).parts
        if here.name == CRAFT_DIR:
            if is_safe_path(root, here):
                found.extend(md_files(here, filenames))
                firm = here / FIRM_DIR
                if (rel == (HQ_DIR, CRAFT_DIR) and firm.is_dir()
                        and is_safe_path(root, firm)):
                    found.extend(md_files(firm, sorted(
                        p.name for p in firm.iterdir())))
            dirnames[:] = []
            continue
        keep = []
        for d in dirnames:
            if d.startswith(".") or d in _SKIP_DIRS:
                continue
            if (here / d).is_symlink():
                continue
            if rel == (HQ_DIR,) and d != CRAFT_DIR:
                continue
            if len(rel) + 1 > _MAX_DEPTH:
                continue
            keep.append(d)
        dirnames[:] = keep
    return sorted(found)


def move_confidence(value, delta) -> float:
    """`value` moved by `delta`, kept between CONFIDENCE_MIN and
    CONFIDENCE_MAX and rounded to two places. A missing value starts at the
    middle."""
    try:
        base = float(value)
    except (TypeError, ValueError):
        base = 0.5
    return round(min(CONFIDENCE_MAX, max(CONFIDENCE_MIN, base + delta)), 2)


def file_confidence(text, verdict=None, *, default: float = 0.5) -> float:
    """The file's confidence: the mean of its numbered rules', raised by
    TEST_STEP while its last held-out test said yes (cr1#401: a passed test
    raises the file). `default` when no rule carries one."""
    parsed = text if isinstance(text, dict) else parse_craft(text)
    values = [it["confidence"] for it in parsed.get("items") or []
              if it.get("id") and isinstance(it.get("confidence"), float)]
    base = round(sum(values) / len(values), 2) if values else default
    if verdict == "yes":
        # Never lower: a mean already above the cap stays where it is.
        return max(base, move_confidence(base, TEST_STEP))
    return base


def is_evergreen(text) -> bool:
    """True when the file carries the evergreen marker."""
    return isinstance(text, str) and bool(_EVERGREEN_RE.search(text))


def placement(workspace_root, path, header, *, entities=None) -> dict:
    """Does a record point at this craft file? Returns {"placed": bool,
    "reason": str | None}. The reason is plain words, never an id."""
    root = Path(workspace_root)
    ents = entities if entities is not None else load_entities(root)
    try:
        rel = Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = Path(path).as_posix()
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    subject = (header or {}).get("subject")

    if folder == f"{HQ_DIR}/{CRAFT_DIR}":
        owners = {owner_person_id(ents)} - {None}
        folder_ok = bool(owners)
    elif folder == f"{HQ_DIR}/{CRAFT_DIR}/{FIRM_DIR}":
        folder_ok = bool(own_company_org_ids(ents))
    elif folder.startswith(PEOPLE_DIR + "/") and folder.count("/") == 2:
        slug = folder.split("/")[1]
        folder_ok = any(person_slug(p.get("canonical_name")) == slug
                        for p in _collection(ents, "people"))
    else:
        owner_folder = folder[:-len("/" + CRAFT_DIR)].lower() \
            if folder.endswith("/" + CRAFT_DIR) else None
        folder_ok = owner_folder is not None and any(
            (_folder_of(t) or "").lower() == owner_folder
            for t in _collection(ents, "threads"))
    if not folder_ok:
        return {"placed": False,
                "reason": "no record points at the folder it sits in"}
    if not subject or (_record(ents, subject) is None
                       and subject != owner_person_id(ents)):
        return {"placed": False,
                "reason": "the record it names is not on file"}
    expected = _subject_folder(ents, subject)
    if expected is None or expected.lower() != folder.lower():
        rec = _record(ents, subject) or {}
        if rec.get("_kind") == "orgs" and folder.endswith("/" + CRAFT_DIR):
            base = folder[:-len("/" + CRAFT_DIR)].lower()
            if any((_folder_of(t) or "").lower() == base
                   for t in _collection(ents, "threads")
                   if subject in _thread_org(t)):
                return {"placed": True, "reason": None}
        return {"placed": False,
                "reason": "its record points at a different folder"}
    return {"placed": True, "reason": None}


__all__ = [
    "SECTIONS", "EVIDENCE_SECTIONS", "METHOD_SECTIONS", "STATUSES",
    "VERDICTS", "CONSENT_VALUES", "DEFAULT_CONSENT", "FIXED_SCOPES",
    "SCOPE_ADDED_EVENT", "SCOPE_REMOVED_EVENT", "SCOPE_CHANGE_CLASS",
    "CORE_SLUGS", "RENAMED_SCOPES", "READERS", "TASK_ALIASES",
    "DECISION_SKILLS", "CORE_DECLARATIONS", "canonical_scope", "reader_of",
    "check_declaration",
    "CraftRefused", "reverse_scope_added",
    "scope_slug", "known_scopes", "scope_label", "add_scope",
    "parse_craft", "validate_craft", "gate_craft_text", "privacy_findings",
    "load_entities", "owner_person_id", "own_company_org_ids",
    "is_third_party_person", "consent_flag", "entity_label",
    "person_slug", "craft_path", "examples_dir", "find_craft_files",
    "is_safe_path",
    "placement",
    "CONFIDENCE_MAX", "CONFIDENCE_MIN", "CONFIRM_STEP", "TEST_STEP",
    "CONTRADICTION_STEP", "SUPERSEDE_AFTER", "EVERGREEN_MARK",
    "move_confidence", "file_confidence", "is_evergreen",
]
