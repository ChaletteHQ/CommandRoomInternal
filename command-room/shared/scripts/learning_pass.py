#!/usr/bin/env python3
"""The `learning` job — corrections the customer has already made become the
way the product writes (SPEC_LEARN1, amended 2026-09-02).

WHAT WAS WRONG. Every correction channel has been capturing for months: a
voice-corrections log per writing skill, a graded prep brief per meeting, a
structural-corrections log per document kind. The halves that turn those into
behaviour all exist and are tested — `voice_corrections.group_op_patterns`,
`prep_grading.propose_section_weights`, `exemplars.propose_exemplar_updates`.
They had NO CALLER. The passes that were supposed to run them were written as
interactive review items inside a skill's prose, and the job that runs that
skill is a silent background leg with no chat surface, so the review item was
rendered to nobody, every week, for months. Sixty-six banked corrections and
seventy graded briefs changed nothing.

WHAT THIS IS. One code job, three automatic legs, inside the existing
`maintenance` fire. No hooks, no new task, stdlib only:

  1. voice     — repeated corrections per writing skill become a learned line
                 in that skill's voice-block override.
  2. prep      — a prep section that keeps coming up empty for a meeting type
                 gets a lower weight in call-prep's config.
  3. exemplar  — repeated structural corrections on one document kind promote
                 the corrected document to that kind's workspace exemplar.

POSTURE. Every leg goes through `brain_proposals.propose(tier="auto")` FIRST,
so the shared ledger owns the cooldown and the dedup, and the refusal path is
the safety: a class with no registered reverser cannot apply at all. Each
applied change carries one `brain_batch_id` and a `previous_*` snapshot, is
narrated once in the morning brief's CHANGED line through `change_feed`, and
reverses on one `undo`. A leg that raises records its error in the receipt and
the remaining legs still run — a job that half-failed must say so rather than
look like a quiet week.

ASK AT TWO, APPLY AT FOUR (cr1#784, rulings C2, C4 and C5 of 2026-11-04). The
voice leg counts corrections of one phrase or one paragraph count, never the
kind. Below the apply number and at the ask number it raises ONE confirm-tier
question per skill and kind on the same rail every other question rides, so the
question budget counts it and the Hub lists it. At the apply number it applies
without asking. This job runs in the background, so a question never lands in
the middle of a task. Both numbers are settings (`learning_thresholds`). Every
change, asked or applied, carries the rule, what it changes from now on, the
corrections it came from and its own Undo; `learned_rules` is its record, and
that record counts whether it was reversed or corrected against.

The prep and exemplar legs keep their floors: a prep weight moves on graded
meetings, not on corrections, and an exemplar moves on a document shape, and
the ruling on cr1#778 speaks of phrase or length.

FLOORS ARE NOT TUNING. >=6 graded meetings of a type, >=3 same-direction
structural corrections on a kind, 3 phrases per skill per run, a 60-day cooldown
on anything declined, and every applied fingerprint excluded forever. They are
what make applying without asking safe; they are not knobs for making it learn
faster. A voice fingerprint names the phrase or paragraph count learned, not the
kind (cr1#778), so that exclusion closes the thing learned and leaves the kind
open.
"""
from __future__ import annotations

import argparse
import json
import secrets
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional


def _effective_fired_via(explicit):
    """`receipts.effective_fired_via`, behind an import that cannot break."""
    try:
        from receipts import effective_fired_via
    except Exception:  # noqa: BLE001 - a resolver that raises is worse
        return explicit if explicit is not None else "scheduled"
    try:
        return effective_fired_via(explicit)
    except Exception:  # noqa: BLE001
        return explicit if explicit is not None else "scheduled"

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

TASK_ID = "learning"
# The ledger `pass` name and the proposal `detector` are the same word: the
# cooldown and the applied set are scoped to this job and to nothing else.
PASS_NAME = "learning"
DETECTOR = "learning"
SOURCE_SKILL = "learning"
BATCH_PREFIX = "lrn_"

# A4/A14 — the three automatic change classes. Each has a registered reverser
# in `brain_undo.REVERSERS` and a phrase in `_CLASS_PHRASES`; `propose` refuses
# the auto tier without both, which is the mechanical half of the safety.
CLASS_VOICE = "voice_block_update"
CLASS_PREP = "prep_section_weight"
CLASS_EXEMPLAR = "exemplar_promotion"
AUTO_CLASSES = (CLASS_VOICE, CLASS_PREP, CLASS_EXEMPLAR)

# The proposal kinds these legs emit (never a customer-visible word).
KIND_VOICE = "voice_block_update"
# cr1#784: a voice rule the owner is asked about. Its own kind, because it is a
# question and not a change, and the Hub's Review tab files a row as a proposed
# rule by the word in its kind.
KIND_ASKED_RULE = "learned_rule"
KIND_PREP = "prep_section_weight"
KIND_EXEMPLAR = "exemplar_promotion"

# The generic proposal verbs the shipped auto rail uses. An auto proposal is
# applied and resolved in the same run; the affordance a person actually uses
# is `undo`, and these are the registered ids the validator accepts.
_ACTION_TUPLES = [{"action": "confirm proposal"},
                  {"action": "dismiss proposal"},
                  {"action": "snooze proposal 7d"}]

# cr1#784 - the two voice numbers are settings, in their own store. Unset, they
# read as `voice_corrections.VOICE_ASK_THRESHOLD` and `VOICE_THRESHOLD`. Neither
# goes below its floor: one correction is not a repeat, and the auto-tier grant
# for `voice_block_update` licenses applying without asking at three or more.
SETTINGS_NAME = "learning"
ASK_AT_KEY = "ask_at"
APPLY_AT_KEY = "apply_at"
ASK_FLOOR = 2
APPLY_FLOOR = 3

# The prep leg reads a 90-day window: a section that stopped being empty three
# months ago should not still be dragging a weight down.
PREP_WINDOW_DAYS = 90


class LearningJobError(RuntimeError):
    """Raised only by the CLI for an unusable workspace path."""


def _now_iso(now_iso: Optional[str] = None) -> str:
    if now_iso:
        return str(now_iso)
    return datetime.now(timezone.utc).isoformat()


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def _mint_batch_id(now_iso: str) -> str:
    stamp = str(now_iso).replace("-", "").replace(":", "")[:15]
    return f"{BATCH_PREFIX}{stamp}-{secrets.token_hex(4)}"


def learning_enabled(workspace_root) -> bool:
    """The `learning.auto_apply` switch, through its one reader.

    Default ON and FAIL-TO-DEFAULT (the SPEC_FLOW1 posture): what it gates is
    an act with a receipt and an undo, so a malformed config file must not
    silently stop the product learning. Only a literal stored `False` — the
    customer having said `stop learning from my edits` — turns it off."""
    try:
        from commitment_policy import LEARNING_AUTO_APPLY_KEY, flow_switch_enabled
        return flow_switch_enabled(workspace_root, LEARNING_AUTO_APPLY_KEY)
    except Exception:  # pragma: no cover — unreadable switch reads as ON
        return True


def learning_thresholds(workspace_root) -> dict:
    """`{ask_at, apply_at}`, through their one reader. FAIL-TO-DEFAULT, the
    posture of the switch above: a stored value that is not a whole number at
    or above its floor reads as the default, and an apply number below the ask
    number reads both as the defaults. An ask number equal to the apply number
    means nothing is asked, and a rule waits for the apply number."""
    from voice_corrections import VOICE_ASK_THRESHOLD, VOICE_THRESHOLD

    defaults = {ASK_AT_KEY: VOICE_ASK_THRESHOLD, APPLY_AT_KEY: VOICE_THRESHOLD}
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(workspace_root, SETTINGS_NAME) or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
    except Exception:  # pragma: no cover, an unreadable store reads as default
        return defaults
    out = _numbers_over_floors(cfg)
    return defaults if out[APPLY_AT_KEY] < out[ASK_AT_KEY] else out


def _numbers_over_floors(cfg) -> dict:
    """The stored numbers, each that is under its floor read as its default."""
    from voice_corrections import VOICE_ASK_THRESHOLD, VOICE_THRESHOLD

    out = {ASK_AT_KEY: VOICE_ASK_THRESHOLD, APPLY_AT_KEY: VOICE_THRESHOLD}
    for key, floor in ((ASK_AT_KEY, ASK_FLOOR), (APPLY_AT_KEY, APPLY_FLOOR)):
        val = cfg.get(key) if isinstance(cfg, dict) else None
        if isinstance(val, int) and not isinstance(val, bool) and val >= floor:
            out[key] = val
    return out


def set_learning_thresholds(workspace_root, *, ask_at: Optional[int] = None,
                            apply_at: Optional[int] = None) -> dict:
    """THE writer for the two numbers. Returns them as they now read. A number
    left out keeps its stored value. A number below its floor is refused, and
    so is a pair the reader would not honour: the check is on what would be
    stored, because that is what the reader reads."""
    from skill_config_writer import load_skill_config, save_skill_config

    stored = load_skill_config(workspace_root, SETTINGS_NAME) or {}
    config = dict(stored.get("config") or {})
    for key, val, floor in ((ASK_AT_KEY, ask_at, ASK_FLOOR),
                            (APPLY_AT_KEY, apply_at, APPLY_FLOOR)):
        if val is None:
            continue
        if not isinstance(val, int) or isinstance(val, bool) or val < floor:
            raise ValueError(f"{key} must be a whole number of {floor} or more")
        config[key] = val
    pair = _numbers_over_floors(config)
    if pair[APPLY_AT_KEY] < pair[ASK_AT_KEY]:
        raise ValueError(f"{APPLY_AT_KEY} cannot be below {ASK_AT_KEY}")
    config = {key: val for key, val in config.items() if pair.get(key) == val}
    save_skill_config(workspace_root, SETTINGS_NAME, config, origin="tune")
    return learning_thresholds(workspace_root)


# The event a reader's DELETION of a learned line writes. The profile page
# (PROFILE1) is the surface that offers the deletion; this job is the thing
# that must never re-derive what was deleted. The event is the seam between
# them, so neither lane imports the other's module.
DELETION_EVENT = "learned_line_deleted"


def deleted_learned_fingerprints(workspace_root) -> set:
    """Fingerprints of learned lines the reader has DELETED.

    Deleting a learned line off the profile page has to mean the line does
    not come back — otherwise the next fire re-derives it from the same
    unchanged corpus and writes it straight back, and the person's deletion
    was theatre. A deletion is therefore read here as permanent exclusion,
    which is the same semantics the applied set already carries.

    Accepts either shape on the event's `data`: a `fingerprint` outright, or
    the natural key a voice line is identified by on the page (`skill` +
    `op`), from which the fingerprint is re-derived. Never raises — an
    unreadable book excludes nothing, which is the pre-deletion behaviour."""
    return set(_deletion_times(workspace_root))


def _deletion_times(workspace_root) -> dict:
    """`{fingerprint: [when it was deleted, ...]}`, the book's deletion rows."""
    out: dict = {}
    try:
        from events_io import iter_events
    except Exception:  # pragma: no cover
        return out
    try:
        events = iter_events(Path(workspace_root) / "_hq" / "data")
    except Exception:
        return out
    try:
        from voice_corrections import voice_proposal_fingerprint
    except Exception:  # pragma: no cover
        voice_proposal_fingerprint = None  # type: ignore
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("type") != DELETION_EVENT:
            continue
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            continue
        fp = data.get("fingerprint")
        if isinstance(fp, str) and fp.strip():
            out.setdefault(fp.strip(), []).append(ev.get("ts"))
            continue
        skill, op = data.get("skill"), data.get("op")
        if skill and op and voice_proposal_fingerprint is not None:
            out.setdefault(voice_proposal_fingerprint(str(skill), str(op)),
                           []).append(ev.get("ts"))
    return out


def _excluded_fingerprints(workspace_root, now_iso: str) -> set:
    """Cooldowns UNION applied UNION deleted. A4 — `active_cooldowns` excludes
    `applied` by design, which is right for a proposer whose own store answers
    "did this already happen". An automatic leg's store cannot answer that: a
    promoted exemplar overwrote the corrections it came from, so the same
    evidence would propose the same promotion on every fire forever.

    The third term is the PROFILE1 seam, and it lives here on purpose: all
    three legs already consult this one function, so a line the reader
    deleted is excluded everywhere in one place rather than three."""
    out: set = set()
    try:
        from proposal_ledger import active_cooldowns, applied_fingerprints
        out |= set(active_cooldowns(workspace_root, PASS_NAME, now_iso=now_iso))
        out |= set(applied_fingerprints(workspace_root, PASS_NAME))
    except Exception:  # pragma: no cover — an unreadable ledger proposes nothing
        return set()
    out |= deleted_learned_fingerprints(workspace_root)
    return out


def _voice_exclusions(workspace_root, now_iso: str, groups, *,
                      yes_to: Optional[str] = None) -> set:
    """`_excluded_fingerprints`, adjusted for books written before cr1#778.

    Those books hold the PAIR fingerprint as applied, and left alone it would
    close the kind for good, which is the bug. So a pair that is only applied
    is lifted, and the items its change named are excluded in its place: what
    is still in the block answers for itself, but a change the owner undid is
    gone from the block and would otherwise be learned straight back. A pair
    that a deletion or a cooldown names stays, and closes the kind as before.

    An undone LENGTH rule closes the shorten kind, on any book. Paragraph
    counts are one dial: an owner who took back "keep it to 2" has not asked
    for 3 instead, where a phrase taken back says nothing about the next one.

    A question the owner dismissed cools every item it named and no other
    (cr1#784). The ledger's row names only the question's first item, which a
    lapsed question may share, so the dismissal is read off the question.

    `yes_to` is the fingerprint of a yes being landed. The yes wrote its own
    applied row, and that row is not the owner closing the item, so it is set
    aside unless a change already wrote that item or the owner closed it."""
    import voice_corrections as vc
    from event_seq import event_seq
    from proposal_ledger import COOLDOWN_DAYS, _days_between, active_cooldowns

    excluded = _excluded_fingerprints(workspace_root, now_iso)
    cooling = set(active_cooldowns(workspace_root, PASS_NAME, now_iso=now_iso))
    held = deleted_learned_fingerprints(workspace_root) | cooling
    excluded -= {vc.voice_proposal_fingerprint(skill, op)
                 for (skill, op) in groups} - held
    try:
        from events_io import iter_events
        events = [ev for ev in iter_events(Path(workspace_root) / "_hq" / "data")
                  if isinstance(ev, dict) and isinstance(ev.get("data"), dict)]
    except Exception:  # pragma: no cover, an unreadable book names nothing
        return excluded
    undone = {ev["data"].get("change_ref") for ev in events
              if ev.get("type") == "brain_change_undone"
              and ev["data"].get("reverser") == CLASS_VOICE}
    written: set = set()
    for ev in events:
        if ev.get("type") == "voice_block_updated":
            written.add(ev["data"].get("fingerprint"))
            written |= set(ev["data"].get("item_fingerprints") or [])
    if yes_to not in held and yes_to not in written:
        excluded.discard(yes_to)
    questions = _voice_questions(events)
    for ev in events:
        data = ev["data"]
        question = questions.get(data.get("proposal_id"))
        age = _days_between(ev.get("ts"), now_iso)
        if (ev.get("type") == "brain_proposal_resolved" and question
                and data.get("user_action") in ("declined", "superseded")
                and question.get("fingerprint") in cooling
                and (age is None or age < COOLDOWN_DAYS)):
            excluded |= set(question.get("item_fingerprints") or [])
    for ev in events:
        if ev.get("type") != "voice_block_updated":
            continue
        data = ev["data"]
        skill, op = str(data.get("skill") or ""), str(data.get("op") or "")
        pair = vc.voice_proposal_fingerprint(skill, op)
        if data.get("fingerprint") == pair:
            excluded |= {vc.voice_item_fingerprint(skill, op, item)
                         for item in vc.items_named_in_line(
                             op, str(data.get("delta") or ""))}
        if op == vc.OP_SHORTEN and f"seq:{event_seq(ev)}" in undone:
            excluded.add(pair)
    return excluded


def _book(workspace_root) -> list:
    """The ledger's events with a dict `data`; an unreadable book reads empty."""
    try:
        from events_io import iter_events
        return [ev for ev in iter_events(Path(workspace_root) / "_hq" / "data")
                if isinstance(ev, dict) and isinstance(ev.get("data"), dict)]
    except Exception:  # pragma: no cover
        return []


def _voice_questions(events) -> dict:
    """Every voice question ever raised, proposal id to its `data`."""
    return {ev["data"].get("proposal_id"): ev["data"] for ev in events
            if ev.get("type") == "brain_proposal"
            and ev["data"].get("kind") == KIND_ASKED_RULE}


def _open_questions(workspace_root, now_iso: str) -> dict:
    """The voice questions still open, a lapsed one with no tombstone included."""
    from brain_proposals import open_brain_proposals

    questions = _voice_questions(_book(workspace_root))
    return {row["id"]: questions[row["id"]]
            for row in open_brain_proposals(workspace_root, now_iso=now_iso,
                                            include_lapsed=True)
            if row["id"] in questions}


def _already_asked(workspace_root, groups, now_iso: str) -> set:
    """Item fingerprints the leg must not ask about now: an open question names
    them, or an earlier one did and no new correction has come in since. An
    unanswered question is not asked again on the same evidence. A question
    retired because one of its items reached the apply number says nothing
    about the others, so it holds nothing back."""
    import voice_corrections as vc

    counts: dict = {}
    for (skill, op), entries in groups.items():
        for entry in entries:
            fp = vc.voice_item_fingerprint(skill, op, entry.get("op_arg"))
            counts[fp] = counts.get(fp, 0) + 1
    events = _book(workspace_root)
    retired = {ev["data"].get("proposal_id") for ev in events
               if ev.get("type") == "brain_proposal_resolved"
               and ev["data"].get("user_action") == "skipped"}
    still_open = _open_questions(workspace_root, now_iso)
    out: set = set()
    for pid, question in _voice_questions(events).items():
        if pid in retired and pid not in still_open:
            continue
        for fp, asked_at in (question.get("item_counts") or {}).items():
            if pid in still_open or counts.get(fp, 0) <= asked_at:
                out.add(fp)
    return out


def _retire_questions(workspace_root, item_fingerprints, now_iso: str) -> int:
    """Close the open questions a rule about to apply makes moot. `skipped` is
    the ledger's no-cooldown word: the owner said nothing, so nothing cools."""
    from brain_proposals import resolve_proposal

    wanted = set(item_fingerprints or [])
    moot = [pid for pid, question
            in _open_questions(workspace_root, now_iso).items()
            if wanted & set(question.get("item_fingerprints") or [])]
    for pid in moot:
        resolve_proposal(workspace_root, pid, "skipped",
                         resolved_by=SOURCE_SKILL, source_skill=SOURCE_SKILL,
                         note="reached the number that applies it")
    return len(moot)


def _retire_covered_questions(workspace_root, skill: str, block: str,
                              now_iso: str) -> None:
    """Close the open questions a rule just written leaves nothing to ask: a
    looser length under a tighter one, a phrase the block now holds."""
    import voice_corrections as vc
    from brain_proposals import resolve_proposal

    for pid, question in _open_questions(workspace_root, now_iso).items():
        if question.get("skill") == skill and not any(
                vc._item_open(skill, question.get("op"), item, block, set())
                for item in _items_of_change(question)):
            resolve_proposal(workspace_root, pid, "skipped",
                             resolved_by=SOURCE_SKILL, source_skill=SOURCE_SKILL,
                             note="a rule already covers it")


def _items_open_to_a_yes(workspace_root, question: dict, now_iso: str) -> list:
    """The items of a question a yes may still land. The owner can close one
    between the question and the yes, by deleting the line or undoing a rule,
    and a rule can come to cover it, so a yes is checked at landing the way a
    proposal is checked at the job: the same exclusions, the same item test."""
    import voice_corrections as vc

    skill, op = question.get("skill"), question.get("op")
    closed = _voice_exclusions(workspace_root, now_iso, {(skill, op): []},
                               yes_to=question.get("fingerprint"))
    if vc.voice_proposal_fingerprint(skill, op) in closed:
        return []
    override = vc.load_voice_block_override(workspace_root, skill) or {}
    block = override.get("markdown") or ""
    return [item for item in _items_of_change(question)
            if vc._item_open(skill, op, item, block, closed)]


def _unclaimed_line(workspace_root, question: dict, events) -> tuple:
    """`(line, items)` when the block holds a learned line naming this
    question's items that no change has recorded, else `(None, [])`. That is a
    yes whose block write landed and whose book write did not: the line is in
    force with no record and no Undo, so it is recorded, never written again."""
    import voice_corrections as vc

    claimed = {ev["data"].get("learned_line") for ev in events
               if ev.get("type") == "voice_block_updated"}
    override = vc.load_voice_block_override(
        workspace_root, str(question.get("skill") or "")) or {}
    for line in reversed((override.get("markdown") or "").split("\n")):
        if line.startswith("- Learned ") and line not in claimed:
            named = [item for item in _items_of_change(question)
                     if vc._line_names(question.get("op"), line, item)]
            if named:
                return line, named
    return None, []


def _narrowed(question: dict, items: list) -> dict:
    """A question cut down to the items a yes may land. Only a phrase question
    has more than one item. Every sentence is rebuilt, so nothing names a
    phrase that did not land."""
    import voice_corrections as vc

    if len(items) == len(_items_of_change(question)):
        return question
    skill, op = question["skill"], question["op"]
    fingerprints = [vc.voice_item_fingerprint(skill, op, i) for i in items]
    counts = question.get("item_counts") or {}
    count = sum(counts.get(fp, 0) for fp in fingerprints)
    rule, effect = vc.rule_sentences(skill, op, items, None)
    return {**question, "phrases": items, "fingerprint": fingerprints[0],
            "item_fingerprints": fingerprints,
            "item_counts": {fp: counts.get(fp, 0) for fp in fingerprints},
            "evidence_rows": count, "rule": rule, "effect": effect,
            "plain": vc.plain_sentence(skill, op, items, None, count),
            "corrections": [c for c in question.get("corrections") or []
                            if c.get("item") in items],
            "rests_on": None}


def _rests_on(workspace_root, proposal: dict) -> str:
    """The corrections a rule rests on, in one sentence with their dates."""
    from change_feed import _act_date, _and_list
    import voice_corrections as vc

    days: list = []
    for ref in proposal.get("corrections") or []:
        if ref.get("timestamp"):
            day = _act_date(workspace_root, ref["timestamp"])
            if day not in days:
                days.append(day)
    count = proposal.get("evidence_rows", 0)
    times = f"{count} time" + ("" if count == 1 else "s")
    if proposal.get("op") == vc.OP_BAN_PHRASE:
        pronoun = "it" if len(proposal.get("phrases") or []) == 1 else "them"
        what = f"You rewrote {pronoun} out {times}"
    elif proposal.get("target_blocks") is None:
        what = f"You cut drafts down {times}"
    else:
        what = f"You cut drafts to that {times}"
    return what + (": " + _and_list(days) if days else "") + "."


def _change_fields(workspace_root, proposal: dict) -> dict:
    """What a learned change carries from the question to the rule (cr1#784).
    `title` and `note` are the two the proposal projector keeps on its row."""
    fields = {key: proposal.get(key) for key in (
        "skill", "op", "section", "phrases", "target_blocks", "plain", "rule",
        "effect", "corrections", "item_fingerprints", "item_counts",
        "evidence_rows")}
    fields["rests_on"] = (proposal.get("rests_on")
                          or _rests_on(workspace_root, proposal))
    fields["title"] = proposal.get("rule")
    fields["note"] = proposal.get("effect")
    return fields


def _ask(workspace_root, proposal: dict) -> Optional[dict]:
    """ONE confirm-tier question on the shared rail, so the question budget
    counts it, the expiry engine ages it and the Hub lists it like any other.
    Nothing is written to the voice block until the owner says yes.

    The Review tab shows a row's title and its line, and the evidence only when
    there is no line, so the line says everything the title does not: what
    changes, what it rests on, and the Undo a yes comes with."""
    from brain_proposals import propose

    fields = _change_fields(workspace_root, proposal)
    res = propose(
        workspace_root,
        kind=KIND_ASKED_RULE,
        fingerprint=proposal["fingerprint"],
        evidence=fields["rests_on"],
        action_tuples=list(_ACTION_TUPLES),
        tier="confirm",
        detector=DETECTOR,
        render_line=(f"Make this a rule? {proposal['effect']} "
                     f"{fields['rests_on']} Say yes and one `undo` takes it back."),
        extra=fields,
    )
    return res if res.get("status") == "proposed" else None


def _propose_auto(workspace_root, *, kind: str, change_class: str,
                  fingerprint: str, evidence: str, render_line: str,
                  extra: Optional[dict] = None) -> Optional[dict]:
    """One auto proposal through the shared rail. Returns the propose result,
    or None when the rail declined it (cooldown / open duplicate)."""
    from brain_proposals import propose

    res = propose(
        workspace_root,
        kind=kind,
        fingerprint=fingerprint,
        evidence=evidence,
        action_tuples=list(_ACTION_TUPLES),
        tier="auto",
        change_class=change_class,
        detector=DETECTOR,
        render_line=render_line,
        extra=extra or None,
    )
    return res if res.get("status") == "proposed" else None


def _settle(workspace_root, proposal_id: str, fingerprint: str,
            summary: str) -> None:
    """Resolve the auto proposal applied and record the fingerprint as applied
    in the shared ledger — the idempotency half of A4."""
    from brain_proposals import resolve_proposal
    from proposal_ledger import append_decision

    resolve_proposal(workspace_root, proposal_id, "applied",
                     resolved_by=SOURCE_SKILL, source_skill=SOURCE_SKILL)
    append_decision(workspace_root, pass_name=PASS_NAME,
                    fingerprint=fingerprint, user_action="applied",
                    summary=summary)


# ---------------------------------------------------------------------------
# Leg 1 — voice
# ---------------------------------------------------------------------------

def _baked_voice_block(skill: str) -> str:
    """The plugin-side `## Voice Block` section of a skill's SKILL.md — what
    the learned delta merges INTO when the workspace has no override yet."""
    path = _HERE.parent.parent / "skills" / skill / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    marker = "\n## Voice Block"
    idx = text.find(marker)
    if idx < 0:
        return ""
    body = text[idx + 1:]
    nxt = body.find("\n## ", 1)
    return body[:nxt] if nxt > 0 else body


def _block_body(markdown) -> str:
    """The `## Voice Block` body of an override file, without the writer's
    header - what `apply_voice_delta` composes on and what the writer wraps."""
    text = markdown or ""
    at = text.find("## Voice Block")
    return text[at:] if at >= 0 else text


def _write_voice_change(workspace_root, proposal: dict, *, stamps: dict,
                        now_iso: str, existing: dict, proposal_id,
                        confirmed_by: Optional[str] = None,
                        written_line: Optional[str] = None) -> None:
    """The one place a learned line lands: the block, then its event. `stamps`
    are the undo keys, so each change can be taken back on its own.
    `written_line` is a line already in the block with no record: the event is
    written for it and the block is left alone."""
    import voice_corrections as vc
    from event_gate import append_event

    skill = proposal["skill"]
    if written_line:
        before = vc.without_learned_line(
            existing.get(skill) or "", written_line,
            section=str(proposal.get("section") or ""))
        had_override = vc.has_owner_lines(before, _baked_voice_block(skill))
        previous_markdown = before if had_override else None
    else:
        had_override = bool(existing.get(skill))
        previous_markdown = existing.get(skill) or None
        # fix-round review (LC 3): the override file is header + block; the
        # writer wraps anything that does not start at the block, so basing
        # on the whole file nested a header per fire. Base on the block body.
        base = _block_body(previous_markdown) or _baked_voice_block(skill)
        merged = vc.apply_voice_delta(base, proposal, learned_on=now_iso)
        vc.write_voice_block_override(
            workspace_root, skill, merged,
            calibration_level="calibrated",
            sample_count=proposal["evidence_rows"])
        # NIGHT 11a fix round (N-4, L-2): the next proposal for THIS skill in
        # this fire bases on the file just written, and snapshots it as its
        # `previous_markdown` - otherwise the second op silently overwrote
        # the first's bullet while both fingerprints settled as applied.
        _disk = vc.load_voice_block_override(workspace_root, skill) or {}
        existing[skill] = _disk.get("markdown") or merged
    fields = _change_fields(workspace_root, proposal)
    for shown_on_the_row_only in ("title", "note", "plain"):
        fields.pop(shown_on_the_row_only)
    append_event(_events_path(workspace_root), {
        "type": "voice_block_updated",
        "source_skill": SOURCE_SKILL,
        "data": {
            **fields,
            "delta": proposal["plain"],
            # cr1#784: the line this change added, which is what its Undo
            # takes back. An older change's line is found by what it names.
            "learned_line": (written_line
                             or vc.learned_bullet(proposal, learned_on=now_iso)),
            # A2 — the FULL prior file (or null when there was none) plus
            # the had_override flag. A path is not a snapshot; the undo
            # has to be able to put the bytes back without the file.
            "previous_markdown": previous_markdown,
            "had_override": had_override,
            "fingerprint": proposal["fingerprint"],
            "proposal_id": proposal_id,
            **({"confirmed_by": confirmed_by} if confirmed_by else {}),
            **stamps,
            "brain_change_class": CLASS_VOICE,
            "detector": DETECTOR,
        },
    }, holder=SOURCE_SKILL)
    # The rule has landed and has its record. A question left open here is tidied
    # by the next write, and is no reason to say this one did not save.
    try:
        _retire_covered_questions(workspace_root, skill, existing[skill], now_iso)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[learning] a covered question stayed open: {exc}\n")


def land_answered_rules(workspace_root, *, now_iso: Optional[str] = None) -> list:
    """Write every rule the owner said yes to that has not landed yet. Returns
    the questions that landed.

    A yes is a `brain_proposal_resolved` on the question, written by whichever
    surface took the answer. Learning went automatic because its confirm steps
    never ran, so a yes must not depend on one surface calling one function:
    `confirm_asked_rule` lands it at once, and the job lands any yes that got
    no further than the tombstone. Either way a yes lands only what is still
    open (`_items_open_to_a_yes`). A yes that wrote the block and failed at the
    book is finished here too, from the line it left (`_unclaimed_line`). One
    yes that cannot be written never stops the rest, and is tried again."""
    import voice_corrections as vc
    from proposal_ledger import append_decision

    now_iso = _now_iso(now_iso)
    events = _book(workspace_root)
    questions = _voice_questions(events)
    done = {ev["data"].get("proposal_id") for ev in events
            if ev.get("type") == "voice_block_updated"}
    landed: list = []
    for ev in events:
        data = ev["data"]
        pid = data.get("proposal_id")
        if (ev.get("type") != "brain_proposal_resolved" or pid not in questions
                or pid in done
                or data.get("user_action") not in ("applied", "edited")):
            continue
        done.add(pid)
        question = questions[pid]
        try:
            items = _items_open_to_a_yes(workspace_root, question, now_iso)
            line = None
            if not items:
                line, items = _unclaimed_line(workspace_root, question, events)
            if not items:
                continue
            rule = _narrowed(question, items)
            skill = rule["skill"]
            override = vc.load_voice_block_override(workspace_root, skill) or {}
            batch_id = _mint_batch_id(now_iso)
            _write_voice_change(
                workspace_root, rule, stamps={"brain_batch_id": batch_id},
                now_iso=now_iso, existing={skill: override.get("markdown") or ""},
                proposal_id=pid, confirmed_by=data.get("resolved_by"),
                written_line=line)
        except Exception as exc:  # noqa: BLE001, the next run tries it again
            sys.stderr.write(f"[learning] a yes did not land: {exc}\n")
            continue
        # The rail's own ledger row names the question's first item.
        for fingerprint in rule.get("item_fingerprints") or []:
            if fingerprint != question.get("fingerprint"):
                append_decision(workspace_root, pass_name=PASS_NAME,
                                fingerprint=fingerprint, user_action="applied",
                                summary=rule.get("plain") or "")
        landed.append({**rule, "proposal_id": pid, "batch_id": batch_id})
    return landed


def confirm_asked_rule(workspace_root, proposal_id: str, *, resolved_by: str,
                       source_skill: str = "apply-choices",
                       now_iso: Optional[str] = None) -> dict:
    """THE yes to a voice question, for whichever surface carries the answer.
    A no needs nothing here: `brain_proposals.resolve_proposal(..., "declined")`
    is all of it, and `_voice_exclusions` cools every item the question named.

    Returns `{status, proposal_id}`, plus `rule`, `line` and `batch_ref` when
    the rule landed. `batch_ref` is that rule's own Undo. `apply-choices` names
    this function, so its status words are ones `apply_audit` knows: `applied`,
    `already_resolved`, `no_question`, `refused` for a yes to something the
    owner has since closed or a rule now covers, which also closes the
    question, and `failed` for a yes that did not land. Never raises. A yes the
    book took stands, and a second press or the next job finishes it. A yes the
    book did not take leaves the question open, and the line says press again."""
    from brain_proposals import resolve_proposal

    events = _book(workspace_root)
    question = _voice_questions(events).get(proposal_id)
    if question is None:
        return {"status": "no_question", "proposal_id": proposal_id}
    try:
        if (not _items_open_to_a_yes(workspace_root, question, _now_iso(now_iso))
                and not _unclaimed_line(workspace_root, question, events)[0]):
            res = resolve_proposal(workspace_root, proposal_id, "skipped",
                                   resolved_by=SOURCE_SKILL,
                                   source_skill=SOURCE_SKILL,
                                   note="closed before the yes")
            if res.get("status") != "resolved":
                return {"status": "already_resolved", "proposal_id": proposal_id}
            return {"status": "refused", "proposal_id": proposal_id,
                    "line": "Nothing changed: you took that back earlier, or a "
                            "rule already covers it."}
        res = resolve_proposal(workspace_root, proposal_id, "applied",
                               resolved_by=resolved_by, source_skill=source_skill)
    except Exception as exc:  # noqa: BLE001, a yes never raises into a surface
        sys.stderr.write(f"[learning] a yes was not recorded: {exc}\n")
        return {"status": "failed", "proposal_id": proposal_id,
                "line": "That did not save, so the question is still open. "
                        "Please press yes again."}
    try:
        landed = [q for q in land_answered_rules(workspace_root, now_iso=now_iso)
                  if q["proposal_id"] == proposal_id]
    except Exception as exc:  # noqa: BLE001, the yes is on file and will finish
        sys.stderr.write(f"[learning] a yes did not land: {exc}\n")
        landed = []
    if not landed:
        if res.get("status") != "resolved":
            return {"status": "already_resolved", "proposal_id": proposal_id}
        return {"status": "failed", "proposal_id": proposal_id,
                "line": "That did not save. Your yes stands, and it will be "
                        "finished on the next run."}
    rule = landed[0]
    return {"status": "applied", "proposal_id": proposal_id,
            "rule": rule.get("rule"),
            "line": f"{rule.get('rule')} {rule.get('effect')} "
                    "Say `undo` to put it back.",
            "batch_ref": {"kind": "brain_batch", "batch_id": rule["batch_id"]}}


def run_voice_leg(workspace_root, *, apply: bool, now_iso: str,
                  batch_id: str) -> dict:
    """Repeated corrections per writing skill become a learned line in that
    skill's voice-block override: at the apply number on its own, at the ask
    number once the owner says yes (cr1#784)."""
    import voice_corrections as vc

    landed = land_answered_rules(workspace_root, now_iso=now_iso) if apply else []
    rows = vc.load_corrections(workspace_root)
    groups = vc.group_op_patterns(rows)
    skills = sorted({skill for (skill, _op) in groups})
    existing = {}
    for skill in skills:
        override = vc.load_voice_block_override(workspace_root, skill)
        existing[skill] = (override or {}).get("markdown") or ""
    excluded = _voice_exclusions(workspace_root, now_iso, groups)
    numbers = learning_thresholds(workspace_root)
    proposals = vc.propose_voice_updates(
        groups, existing_overrides=existing, cooldown_fingerprints=excluded,
        threshold=numbers[APPLY_AT_KEY])
    leg = {"corrections_read": len(rows), "groups": len(groups),
           "proposed": len(proposals), "applied": 0, "skipped": 0,
           "asked": 0, "landed": len(landed), "questions": [],
           "lines": [q.get("plain") or "" for q in landed], "skills": []}

    def questions_now() -> list:
        return vc.propose_voice_updates(
            groups, existing_overrides=existing,
            cooldown_fingerprints=excluded | _already_asked(
                workspace_root, groups, now_iso),
            threshold=numbers[ASK_AT_KEY], ceiling=numbers[APPLY_AT_KEY])

    if not apply:
        leg["lines"] = [p["plain"] for p in proposals]
        leg["skills"] = sorted({p["skill"] for p in proposals})
        leg["questions"] = [p["rule"] for p in questions_now()]
        return leg

    from commitment_policy import group_stamps
    from event_gate import append_event
    events_path = _events_path(workspace_root)
    reviewed_through = {}
    for proposal in proposals:
        skill = proposal["skill"]
        # Before the proposal, because the rail refuses a second row on a
        # fingerprint an open question already holds.
        _retire_questions(workspace_root, proposal["item_fingerprints"], now_iso)
        res = _propose_auto(
            workspace_root, kind=KIND_VOICE, change_class=CLASS_VOICE,
            fingerprint=proposal["fingerprint"],
            evidence=proposal["plain"], render_line=proposal["plain"],
            extra=_change_fields(workspace_root, proposal))
        if res is None:
            leg["skipped"] += 1
            continue
        # One group per learned change under this run: `undo` on the run takes
        # back the fire, and each rule's own Undo takes back that rule alone.
        _write_voice_change(
            workspace_root, proposal,
            stamps=group_stamps(batch_id, proposal["fingerprint"]),
            now_iso=now_iso, existing=existing,
            proposal_id=res["proposal_id"])
        _settle(workspace_root, res["proposal_id"], proposal["fingerprint"],
                proposal["plain"])
        # One line can learn several phrases; each is remembered on its own.
        from proposal_ledger import append_decision
        for fingerprint in proposal["item_fingerprints"][1:]:
            append_decision(workspace_root, pass_name=PASS_NAME,
                            fingerprint=fingerprint, user_action="applied",
                            summary=proposal["plain"])
        reviewed_through[skill] = now_iso
        leg["applied"] += 1
        leg["lines"].append(proposal["plain"])
        leg["skills"].append(skill)
    # The ask comes after the applies: a phrase just applied is in the block,
    # and a question retired above frees the items it also named.
    for proposal in questions_now():
        if _ask(workspace_root, proposal):
            leg["asked"] += 1
            leg["questions"].append(proposal["rule"])
    if reviewed_through:
        # Staleness math unchanged: one review marker per fire, naming how far
        # each skill's corpus has been read.
        append_event(events_path, {
            "type": "voice_calibration_review",
            "source_skill": SOURCE_SKILL,
            "data": {"reviewed_through": reviewed_through,
                     "brain_batch_id": batch_id, "detector": DETECTOR},
        }, holder=SOURCE_SKILL)
    leg["skills"] = sorted(set(leg["skills"]))
    return leg


def _items_of_change(data: dict) -> list:
    """What one change learned: its own fields, or for a change written before
    cr1#784 what its sentence names."""
    import voice_corrections as vc

    op = str(data.get("op") or "")
    if op == vc.OP_BAN_PHRASE and data.get("phrases"):
        return list(data["phrases"])
    if op == vc.OP_SHORTEN and "target_blocks" in data:
        return [data["target_blocks"]]
    return vc.items_named_in_line(op, str(data.get("delta") or ""))


def learned_rules(workspace_root, *, now_iso: Optional[str] = None) -> list:
    """Every learned voice rule and every question about one, newest first,
    with its record (cr1#784). Read-only; the one reader a surface needs.

    Each row: `{status, skill, op, items, rule, effect, rests_on, corrections,
    undo, proposal_id, seq, asked_at, applied_at, n_reversed,
    n_corrected_against}`. `status` is `asked`, `confirmed` (a yes not yet
    written), `refused` (a yes to something closed since), `declined`, `lapsed`
    or `retired` for a question. For a change it is `live` while the block still
    says the rule and `reversed` once it does not. `undo` is the batch ref that
    takes back this change and nothing else, and is None once there is nothing
    to take back.

    The counts are derived, not stored: the record cannot drift from the book.
    `n_reversed` counts an Undo and a later deletion off the profile page. A
    deletion keeps the line off the page and out of learning but leaves it in
    the block, so that rule is reversed by the owner and still `live`.
    `n_corrected_against` counts later corrections in that skill that write the
    banned phrase back in or take a whole draft past the learned length, up to
    the first reversal of either kind."""
    import voice_corrections as vc
    from event_seq import event_seq
    from event_time import event_time, parse_ts

    now_iso = _now_iso(now_iso)
    events = _book(workspace_root)
    questions = _voice_questions(events)
    deleted = _deletion_times(workspace_root)
    undone_at = {ev["data"].get("change_ref"): parse_ts(event_time(ev))
                 for ev in events if ev.get("type") == "brain_change_undone"}
    settled: dict = {}
    for ev in events:
        pid = ev["data"].get("proposal_id")
        if ev.get("type") == "brain_proposal_expired":
            settled[pid] = "lapsed"
        elif ev.get("type") == "brain_proposal_resolved":
            settled[pid] = {"skipped": "retired", "applied": "confirmed",
                            "edited": "confirmed"}.get(
                                ev["data"].get("user_action"), "declined")
    still_open = _open_questions(workspace_root, now_iso)
    rows = [vc.normalize_correction_row(row)
            for row in vc.load_corrections(workspace_root)]
    opened = {pid: event_time(ev) for ev in events
              if ev.get("type") == "brain_proposal"
              for pid in [ev["data"].get("proposal_id")] if pid in questions}

    def card(data: dict) -> dict:
        return {"skill": data.get("skill"), "op": data.get("op"),
                "items": _items_of_change(data),
                "rule": data.get("rule") or data.get("delta") or "",
                "effect": data.get("effect") or "",
                "rests_on": data.get("rests_on") or "",
                "corrections": list(data.get("corrections") or []),
                "proposal_id": data.get("proposal_id"),
                "n_reversed": 0, "n_corrected_against": 0}

    out: list = []
    landed: set = set()
    for ev in events:
        if ev.get("type") != "voice_block_updated":
            continue
        data = ev["data"]
        landed.add(data.get("proposal_id"))
        seq = event_seq(ev)
        record = card(data)
        applied_at = parse_ts(event_time(ev))
        undone = f"seq:{seq}" in undone_at
        keys = set(data.get("item_fingerprints") or []) | {
            data.get("fingerprint"),
            vc.voice_proposal_fingerprint(str(data.get("skill") or ""),
                                          str(data.get("op") or ""))}
        deletions = sorted(
            when for key in keys for when in map(parse_ts, deleted.get(key, []))
            if when and applied_at and when > applied_at)
        reversals = [when for when in [undone_at.get(f"seq:{seq}")] + deletions
                     if when]
        ended = min(reversals) if reversals else None
        record["n_reversed"] = (1 if undone else 0) + (1 if deletions else 0)
        skill = str(data.get("skill") or "")
        override = vc.load_voice_block_override(workspace_root, skill) or {}
        block = override.get("markdown") or ""
        baked = _baked_voice_block(skill)
        in_force = not undone and bool(
            vc.line_of_change(block, data, baseline=baked)
            or vc.items_still_named(block, data, baseline=baked))
        for row in rows:
            when = parse_ts((row or {}).get("timestamp"))
            if (row and when and applied_at and when > applied_at
                    and row.get("skill") == data.get("skill")
                    and (ended is None or when < ended)
                    and any(vc.row_goes_against(data.get("op"), item, row)
                            for item in record["items"])):
                record["n_corrected_against"] += 1
        record.update({
            "status": "live" if in_force else "reversed",
            "seq": seq, "asked_at": opened.get(data.get("proposal_id")),
            "applied_at": event_time(ev),
            "undo": ({"kind": "brain_batch",
                      "batch_id": data.get("brain_batch_id")}
                     if in_force else None)})
        out.append(record)
    for pid, question in questions.items():
        if pid in landed:
            continue
        record = card(question)
        status = "asked" if pid in still_open else settled.get(pid, "lapsed")
        if (status == "confirmed"
                and not _items_open_to_a_yes(workspace_root, question, now_iso)
                and not _unclaimed_line(workspace_root, question, events)[0]):
            status = "refused"
        record.update({
            "status": status,
            "seq": None, "asked_at": opened.get(pid), "applied_at": None,
            "undo": None})
        out.append(record)
    out.sort(key=lambda r: str(r["applied_at"] or r["asked_at"] or ""),
             reverse=True)
    return out


# ---------------------------------------------------------------------------
# Leg 2 — prep section weights
# ---------------------------------------------------------------------------

def run_prep_leg(workspace_root, *, apply: bool, now_iso: str,
                 batch_id: str) -> dict:
    """A prep section that keeps coming up empty for a meeting type gets a
    lower weight in call-prep's config."""
    import prep_grading as pg
    from skill_config_writer import load_skill_config

    since = None
    try:
        base = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
        since = (base - timedelta(days=PREP_WINDOW_DAYS)).isoformat()
    except Exception:  # pragma: no cover — an unparseable now reads all history
        since = None
    rows = pg.load_prep_feedback(workspace_root, since_iso=since)
    stats = pg.aggregate_section_stats_normalized(rows)
    stored = load_skill_config(workspace_root, "call-prep") or {}
    cfg = stored.get("config") if isinstance(stored, dict) else None
    existing = (cfg or {}).get("section_weights") or {}
    excluded = _excluded_fingerprints(workspace_root, now_iso)
    # ONE act on this rail: the shipped drop at the shipped floor. The
    # "render it shorter at half the evidence" tier was built and taken back
    # out before merge — see the note in `prep_grading`. On a book with no
    # section over the floor this leg correctly applies nothing, and the
    # brief says nothing, which is the honest outcome rather than a smaller
    # bar invented to produce one.
    proposals = pg.propose_section_weights(
        stats, existing_weights=existing, cooldown_fingerprints=excluded)
    leg = {"feedback_read": len(rows), "sections": len(stats),
           "proposed": len(proposals), "applied": 0, "skipped": 0,
           "lines": [], "meeting_types": []}
    if not apply:
        # Even the dry run's sentence goes through the one renderer, over the
        # change the write WOULD make. There is no second composer.
        leg["lines"] = [
            line for line in (
                pg.weight_change_line(
                    {"meeting_type": p["meeting_type"], "section": p["section"],
                     "from": pg.section_weight(cfg or {}, p["meeting_type"],
                                               p["section"]),
                     "to": p["weight"]},
                    rendered=p.get("rendered"), empty=p.get("empty"))
                for p in proposals) if line]
        leg["meeting_types"] = sorted({p["meeting_type"] for p in proposals})
        return leg

    from event_gate import append_event
    events_path = _events_path(workspace_root)
    for proposal in proposals:
        res = _propose_auto(
            workspace_root, kind=KIND_PREP, change_class=CLASS_PREP,
            fingerprint=proposal["fingerprint"],
            evidence=proposal["plain"], render_line=proposal["plain"],
            extra={"meeting_type": proposal["meeting_type"],
                   "section": proposal["section"]})
        if res is None:
            leg["skipped"] += 1
            continue
        written = pg.set_section_weights(workspace_root, [proposal])
        if not written["changes"]:
            leg["skipped"] += 1
            continue
        # THE SENTENCE IS DERIVED FROM THE WRITE, NOT FROM THE PROPOSAL. If
        # the write moved nothing the renderer returns "" and there is no
        # line — a surface must never announce an effect it did not produce.
        lines = [ln for ln in
                 (pg.weight_change_line(change,
                                        rendered=proposal.get("rendered"),
                                        empty=proposal.get("empty"))
                  for change in written["changes"]) if ln]
        delta = " ".join(lines)
        append_event(events_path, {
            "type": "prep_weights_updated",
            "source_skill": SOURCE_SKILL,
            "data": {
                # `changes` carries the from-value per row: the reverser puts
                # the config back from this and never re-derives it.
                "changes": written["changes"],
                # The job's own sentence rides the event, so `change_feed`
                # renders what the job decided rather than composing a second
                # sentence about the same act that can drift from it — and it
                # is the sentence derived from the WRITE, in the past tense,
                # not the proposer's question.
                "delta": delta,
                "fingerprint": proposal["fingerprint"],
                "brain_batch_id": batch_id,
                "brain_change_class": CLASS_PREP,
                "detector": DETECTOR,
            },
        }, holder=SOURCE_SKILL)
        _settle(workspace_root, res["proposal_id"], proposal["fingerprint"],
                delta or proposal["plain"])
        leg["applied"] += 1
        if delta:
            leg["lines"].append(delta)
        leg["meeting_types"].append(proposal["meeting_type"])
    leg["meeting_types"] = sorted(set(leg["meeting_types"]))
    return leg


# ---------------------------------------------------------------------------
# Leg 3 — exemplar promotion
# ---------------------------------------------------------------------------

def run_exemplar_leg(workspace_root, *, apply: bool, now_iso: str,
                     batch_id: str) -> dict:
    """Repeated structural corrections on one document kind promote the
    corrected document to that kind's workspace exemplar.

    A5 — the AUTOMATIC leg passes no `confirmed_residuals`, so any name-shaped
    token the workspace entity list cannot vouch for raises
    `ExemplarScrubError` and the promotion is REFUSED and counted. A refusal
    is a correct outcome here, not an error: a real name inside a gold
    standard is worse than a missed promotion, every time."""
    import exemplars as ex

    rows = ex.load_structural_corrections(workspace_root)
    excluded = _excluded_fingerprints(workspace_root, now_iso)
    proposals = ex.propose_exemplar_updates(
        rows, cooldown_fingerprints=excluded)
    leg = {"corrections_read": len(rows), "proposed": len(proposals),
           "applied": 0, "skipped": 0, "refused_scrub": 0,
           "no_candidate_text": 0, "lines": [], "kinds": []}
    by_key = ex.group_correction_patterns(rows)
    if not apply:
        # Even the dry run's sentence goes through the one renderer, over the
        # change the write WOULD make. `p["plain"]` is the proposer's
        # QUESTION and it must not reach a surface from here either.
        leg["lines"] = [
            line for line in (
                ex.promotion_change_line(
                    {"kind": p["kind"], "direction": p["direction"],
                     "section": p.get("section"), "count": p.get("count"),
                     "rotated": bool(
                         (ex.get_exemplar(p["kind"], workspace_root) or {})
                         .get("source") == "workspace")})
                for p in proposals) if line]
        leg["kinds"] = sorted({p["kind"] for p in proposals})
        return leg

    from event_gate import append_event
    events_path = _events_path(workspace_root)
    for proposal in proposals:
        key = (proposal["kind"], proposal["direction"],
               ex._norm(proposal.get("section") or ""))
        group = by_key.get(key) or []
        candidate = ""
        for row in reversed(group):
            text = (row.get("doc") or "").strip()
            if text:
                candidate = text
                break
        if not candidate:
            leg["no_candidate_text"] += 1
            continue
        current = ex.get_exemplar(proposal["kind"], workspace_root) or {}
        previous_text = (current.get("text")
                         if current.get("source") == "workspace" else None)
        res = _propose_auto(
            workspace_root, kind=KIND_EXEMPLAR, change_class=CLASS_EXEMPLAR,
            fingerprint=proposal["fingerprint"],
            evidence=proposal["plain"], render_line=proposal["plain"],
            extra={"exemplar_kind": proposal["kind"],
                   "direction": proposal["direction"]})
        if res is None:
            leg["skipped"] += 1
            continue
        try:
            written = ex.promote_workspace_exemplar(
                workspace_root, proposal["kind"], candidate,
                confirmed_residuals=None)
        except ex.ExemplarScrubError:
            leg["refused_scrub"] += 1
            from brain_proposals import resolve_proposal
            resolve_proposal(workspace_root, res["proposal_id"], "declined",
                             resolved_by=SOURCE_SKILL,
                             source_skill=SOURCE_SKILL,
                             note="refused by the exemplar scrub gate")
            continue
        # THE SENTENCE IS DERIVED FROM THE WRITE, NOT FROM THE PROPOSAL —
        # same rule as the prep rail. `proposal["plain"]` asks "make that the
        # standard layout?", which is the retired review widget's question;
        # putting it on the morning brief asks the customer to approve
        # something the job already did, and then offers an undo in the same
        # breath. `rotated` is the write's own fact: whether this replaced a
        # standard the workspace already had.
        delta = ex.promotion_change_line(
            {"kind": proposal["kind"], "direction": proposal["direction"],
             "section": proposal.get("section"),
             "count": proposal.get("count"),
             "rotated": bool((written or {}).get("rotated"))})
        append_event(events_path, {
            "type": "exemplar_promoted",
            "source_skill": SOURCE_SKILL,
            "data": {
                "kind": proposal["kind"],
                "direction": proposal["direction"],
                "previous_text": previous_text,
                "delta": delta,
                "fingerprint": proposal["fingerprint"],
                "brain_batch_id": batch_id,
                "brain_change_class": CLASS_EXEMPLAR,
                "detector": DETECTOR,
            },
        }, holder=SOURCE_SKILL)
        _settle(workspace_root, res["proposal_id"], proposal["fingerprint"],
                delta or proposal["plain"])
        leg["applied"] += 1
        if delta:
            leg["lines"].append(delta)
        leg["kinds"].append(proposal["kind"])
    leg["kinds"] = sorted(set(leg["kinds"]))
    return leg


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------

_LEGS = (("voice", run_voice_leg),
         ("prep", run_prep_leg),
         ("exemplar", run_exemplar_leg))


def run_learning_job(workspace_root, *, apply: bool = True,
                     now_iso: Optional[str] = None,
                     fired_via=None) -> dict:
    """The `learning` MAINTENANCE_JOBS entry point. Runs the three automatic
    legs in order, each wrapped: one leg raising never aborts the job, and its
    error text lands in the receipt rather than in nobody's hands.

    `apply=False` is a dry run — it proposes nothing, writes nothing, and
    deliberately writes NO receipt, so the job stays due (the
    `identity_reconcile --apply` posture the maintenance prompt depends on).

    Returns {ran, switch_on, batch_id, legs, errors, receipt}."""
    # FIX3 F3-6: a literal default IS an explicit value by the time the
    # resolver sees it (the FIX2 M-3 lesson), so this signature says
    # nothing and the seat answers. A legacy or local seat still reads
    # `scheduled`, byte for byte; a merged seat with nothing forwarded
    # reads `manual`, which is what a typed brief actually is.
    fired_via = _effective_fired_via(fired_via)
    now_iso = _now_iso(now_iso)
    batch_id = _mint_batch_id(now_iso)
    switch_on = learning_enabled(workspace_root)
    result = {"ran": True, "switch_on": switch_on, "batch_id": batch_id,
              "apply": bool(apply), "legs": {}, "errors": [],
              "lines": [], "receipt": None}
    if not switch_on:
        # The customer said stop. Nothing is proposed and nothing is written,
        # but the job still FIRED — a skip that leaves no trace reads exactly
        # like a job that never ran.
        result["ran"] = False
        if apply:
            result["receipt"] = _log(workspace_root, fired_via, result)
        return result

    for name, fn in _LEGS:
        try:
            result["legs"][name] = fn(workspace_root, apply=apply,
                                      now_iso=now_iso, batch_id=batch_id)
            result["lines"].extend(result["legs"][name].get("lines") or [])
        except Exception as exc:  # loud per-leg, contained per-job
            result["legs"][name] = {"error": f"{type(exc).__name__}: {exc}"}
            result["errors"].append({"leg": name,
                                     "error": f"{type(exc).__name__}: {exc}"})
    if apply:
        result["receipt"] = _log(workspace_root, fired_via, result)
    return result


def _log(workspace_root, fired_via: str, result: dict) -> dict:
    from receipts import log_receipt

    legs = result.get("legs") or {}
    applied = sum(int((legs.get(n) or {}).get("applied") or 0)
                  for n in ("voice", "prep", "exemplar"))
    return log_receipt(
        workspace_root, TASK_ID,
        receipt_type="pack_run",
        fired_via=fired_via,
        surfaced=applied,
        extra_data={
            "switch_on": result.get("switch_on"),
            "brain_batch_id": result.get("batch_id"),
            "n_applied": applied,
            "legs": {n: {k: v for k, v in (legs.get(n) or {}).items()
                         if k not in ("lines", "questions")}
                     for n in ("voice", "prep", "exemplar")},
            "errors": result.get("errors") or [],
        },
    )


def validate_learning_ran(workspace_root) -> dict:
    """Enforcement binds to the receipt artifact, never to narration (the
    reconcile-sent doctrine)."""
    from receipts import iter_receipts

    latest = None
    for r in iter_receipts(workspace_root, task_ids=[TASK_ID]):
        latest = r
    if latest is None:
        return {"ok": False, "ran": False,
                "reason": "no learning receipt on file"}
    return {"ok": True, "ran": True, "receipt": latest}


def _narrate(result: dict) -> str:
    if not result.get("switch_on"):
        return "Learning from your edits is off for this workspace — nothing changed."
    lines = result.get("lines") or []
    head = ("Dry run — nothing written." if not result.get("apply")
            else f"Applied {len(lines)} change(s).")
    body = "\n".join("  - " + line for line in lines) or "  (nothing at the floors)"
    questions = (result.get("legs") or {}).get("voice", {}).get("questions") or []
    if questions:
        body += "\n" + "\n".join("  ? " + line for line in questions)
    errs = result.get("errors") or []
    tail = ("\n" + "\n".join(f"  ! {e['leg']}: {e['error']}" for e in errs)) if errs else ""
    return f"{head}\n{body}{tail}"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="perform the writes (default: dry-run, no receipt)")
    ap.add_argument("--triggered-by", default=None,
                    help="the surface that asked for this run")
    ap.add_argument("--fired-via", default=None,
                    choices=("scheduled", "manual", "catchup"))
    ap.add_argument("--now", default=None, help="ISO now override (tests)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    # FIX3 F3-6: export what this run was asked by, so every composer
    # below reads it from one place instead of being threaded through
    # a dozen signatures.
    if getattr(args, "triggered_by", None):
        os.environ["CR_TRIGGERED_BY"] = str(args.triggered_by)
    ws = Path(args.workspace)
    if not ws.is_dir():
        raise LearningJobError(f"workspace not found: {args.workspace}")
    result = run_learning_job(ws, apply=args.apply, now_iso=args.now,
                              fired_via=args.fired_via)
    print(_narrate(result))
    if args.json:
        print(json.dumps(result, default=str))
    return 0


__all__ = [
    "TASK_ID", "PASS_NAME", "DETECTOR", "BATCH_PREFIX",
    "CLASS_VOICE", "CLASS_PREP", "CLASS_EXEMPLAR", "AUTO_CLASSES",
    "PREP_WINDOW_DAYS", "DELETION_EVENT", "learning_enabled",
    "SETTINGS_NAME", "ASK_AT_KEY", "APPLY_AT_KEY", "learning_thresholds",
    "set_learning_thresholds", "land_answered_rules", "confirm_asked_rule",
    "learned_rules",
    "deleted_learned_fingerprints", "run_voice_leg", "run_prep_leg",
    "run_exemplar_leg", "run_learning_job", "validate_learning_ran",
]


if __name__ == "__main__":
    raise SystemExit(main())
