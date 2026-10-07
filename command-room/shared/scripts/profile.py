#!/usr/bin/env python3
"""The workspace profile — "my profile" / "what do you know about me".

SPEC_SURFACES2 PROFILE1, absorbing SPEC_LEARN2 and the style card; design
memo "The coaching surfaces" section 2c.

WHAT IT IS
----------
One page, rendered from the structured layers, never hand-written. What the
product knows about a seat is scattered across six stores today — the
workspace record, the persona knobs, the per-skill settings, the free-text
directives, the learned layer and the lines in the workspace instructions —
so nobody can see it and nobody can correct it. This module composes all of
them into one read surface where every single line says where it came from,
and where any learned line can be deleted by a word.

THE RULE THAT SHAPES EVERY LINE
-------------------------------
Stated outranks learned, always. A learned line is a proposal the seat can
delete; a stated line is a fact the learning job may never overwrite. And
deleting a learned line does not just remove it from the page: the deletion
is written as a stated override for THIS page (read back through
`filter_learned`, which every layer's rows pass through before they render)
AND as a `learned_line_deleted` event on the book, which is where the
learning job reads it (`learning_pass.deleted_learned_fingerprints`) so the
same line is never re-derived. The two modules never import each other; they
meet at that one event.

THE FENCE
---------
Zero lines without provenance. Every content bullet leaves this module
through `_line()`, which refuses a blank provenance, and `render()` runs
`assert_every_line_has_provenance` over the finished block before returning
it. There are exactly four provenance shapes and they are the only ones a
line may carry:

    asked <date>                      the seat said so (onboarding or since)
    learned <date>, <evidence>        the product read it off behaviour
    uploaded <date>                   the seat brought it in (an assessment)
    on file since <date>              on the workspace record from setup

The fourth exists because the workspace record carries facts (a timezone, a
brain name) that predate the provenance lane. It is still provenance: it says
which store holds it and since when. It is never a substitute for the other
three when one of them is known.

Empty sections say so in ONE line, from the registered `HONESTY_LINES` set —
those are the only non-bullet content lines the page may carry, and the fence
asserts it. Nothing is padded.

Views are outputs, never inputs (WORKSPACE_API): `_hq/views/PROFILE.md` is
regenerated wholesale on every render and nothing reads it back.

Reads: entities.json, the skill_config store, the learned stores, the
coaching object, and the full event history through `events_io` and nothing
else. Writes: only through `skill_config_writer` and `atomic_write_*`.

THIS MODULE'S NAME IS OWED A RENAME - BEFORE THE VERSION IT SHIPS IN
-------------------------------------------------------------------
`profile` is a standard-library module (the pure-Python profiler), and
183 shared scripts and 530 test files put this directory on `sys.path`, so
in essentially every process of this product `import profile` is this file.
The collision bites in BOTH directions and both were measured:

  * with this directory on the path first, `import cProfile` dies at its own
    line 24 - `AttributeError: module 'profile' has no attribute 'run'` -
    and `python -m profile <script>` exits 0 printing NOTHING, which reads
    as "the script produced no output" rather than as a broken import;
  * with the stdlib imported first, `sys.modules` caches the profiler under
    that name and a later `import profile` in another module gets the
    profiler, so a call into this file dies as a bare AttributeError and the
    work it was doing is silently dropped.

Nothing in the tree imports `cProfile` today, so no customer path is broken
and the name ships as it stands. The rename is `workspace_profile`, one
commit of its own, BEFORE the version that carries this file - not inside a
fix round, because it touches every reader. The general rule this earned: a
new module under `shared/scripts/` is checked against the stdlib module list
AND a bare substring grep of the whole tree before it is named.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atomic_write import atomic_write_text  # noqa: E402

# --------------------------------------------------------------------------
# The store this surface owns
# --------------------------------------------------------------------------

# One skill key in the canonical settings store. Registered in
# shared/data-schemas/skill_config.schema.json in this same commit
# (CONTRACT Rule 29), so an unknown key is refused LOUDLY at write.
PROFILE_SKILL_KEY = "profile"

# The switch (SPEC_SURFACES2 "Switches"): per workspace, DEFAULT ON, off by
# phrase, FAIL-TO-DEFAULT. Same contract as commitment_policy.flow_switch_
# enabled — only a literal False turns it off, because what it gates is an
# act with a receipt and an undo, and a typo in a config file is not a
# customer saying stop.
REGENERATE_INSTRUCTIONS_KEY = "regenerate_instructions"
REGENERATE_INSTRUCTIONS_DEFAULT = True

# Config keys this surface owns inside that store.
STATED_OVERRIDES_KEY = "stated_overrides"
ASSESSMENT_KEY = "assessment"
BOUNDARIES_KEY = "boundaries"
BOUNDARIES_SET_AT_KEY = "boundaries_set_at"
COACHING_LAYER_KEY = "coaching_layer"
# NIGHT 11a fix round (N-1): the client's workstreams in their own order and
# words, written by onboarding_profile.record_how_they_think.
WORKSTREAMS_KEY = "workstreams"

# --------------------------------------------------------------------------
# Sections (design memo 2c), in order
# --------------------------------------------------------------------------

SECTION_WHO = "who"
SECTION_THINK = "how_i_think"
SECTION_LIKE = "how_i_like_it"
SECTION_WORKING_ON = "working_on"
SECTION_BROUGHT = "brought"
SECTION_LEARNED = "learned"
SECTION_BOUNDARIES = "boundaries"

SECTIONS: tuple = (
    (SECTION_WHO, "Who you are"),
    (SECTION_THINK, "How you think"),
    (SECTION_LIKE, "How you like it"),
    (SECTION_WORKING_ON, "What you are working on"),
    (SECTION_BROUGHT, "What you brought"),
    (SECTION_LEARNED, "What I learned"),
    (SECTION_BOUNDARIES, "Boundaries"),
)
SECTION_KEYS = tuple(k for k, _t in SECTIONS)
SECTION_TITLES = dict(SECTIONS)

# --------------------------------------------------------------------------
# Provenance — the four shapes, and nothing else
# --------------------------------------------------------------------------

ORIGIN_ASKED = "asked"
ORIGIN_LEARNED = "learned"
ORIGIN_UPLOADED = "uploaded"
ORIGIN_ON_FILE = "on_file"
ORIGINS = (ORIGIN_ASKED, ORIGIN_LEARNED, ORIGIN_UPLOADED, ORIGIN_ON_FILE)

# The literal opening words a provenance phrase may take. The fence matches
# on these, so a new origin without a phrase here cannot render.
PROVENANCE_PREFIXES = ("asked ", "learned ", "uploaded ", "on file since ")

# The only non-bullet content lines the page may carry. A section with
# nothing in it says so once, in the seat's own terms, and stops.
HONESTY_LINES = {
    SECTION_WHO: "Nothing on file yet - onboarding fills this in.",
    SECTION_THINK: "Nothing yet - say how you'd like this organized and it "
                   "lands here.",
    SECTION_LIKE: "Product defaults, nothing personalized yet.",
    SECTION_WORKING_ON: "Nothing - every seat starts observed, and that is a "
                        "complete way to use this.",
    SECTION_BROUGHT: "Nothing brought in.",
    SECTION_LEARNED: "Nothing yet - I start adapting after a pattern repeats.",
    SECTION_BOUNDARIES: "None set.",
}


class ProfileLineError(ValueError):
    """A line was built without provenance. Never caught - it is a bug in
    the composer, and the whole point of the surface is that it cannot
    happen."""


def _date_only(value: Optional[str], workspace_root=None) -> str:
    """A YYYY-MM-DD day from an ISO timestamp, localized where tz.py is
    installed (the style_card precedent: a knob changed late-evening-local
    must not be provenance-stamped a day forward)."""
    if not value or not isinstance(value, str):
        return ""
    try:
        from tz import localize_date as _localize_date
        day = _localize_date(value, str(workspace_root) if workspace_root else None)
        if day:
            return day
    except Exception:
        pass
    return value[:10]


def provenance_phrase(origin: str, date: str, evidence: str = "") -> str:
    """The rendered provenance for one line. Raises on an unknown origin or a
    missing date - a line with nothing to say about where it came from does
    not render at all."""
    if origin not in ORIGINS:
        raise ProfileLineError(f"unknown provenance origin {origin!r}")
    if not date:
        raise ProfileLineError(f"provenance for {origin!r} carries no date")
    if origin == ORIGIN_ASKED:
        return f"asked {date}"
    if origin == ORIGIN_UPLOADED:
        return f"uploaded {date}"
    if origin == ORIGIN_ON_FILE:
        return f"on file since {date}"
    ev = (evidence or "").strip()
    if not ev:
        raise ProfileLineError("a learned line must carry its evidence")
    return f"learned {date}, {ev}"


def _line(section: str, key: str, text: str, origin: str, date: str,
          evidence: str = "") -> dict:
    """Build one profile line. THE fence at the source: no text, no
    provenance, no line."""
    text = (text or "").strip()
    if not text:
        raise ProfileLineError(f"{key}: a profile line with no text")
    phrase = provenance_phrase(origin, date, evidence)
    return {"section": section, "key": key, "text": text, "origin": origin,
            "date": date, "evidence": evidence, "provenance": phrase}


def render_line(line: dict) -> str:
    return f"- {line['text']} - {line['provenance']}"


def lines_missing_provenance(block: str) -> List[str]:
    """Every line of a rendered block that is neither a heading, a blank, a
    registered honesty line, nor a bullet carrying one of the four
    provenance shapes. The acceptance gate reads this and expects []."""
    bad: List[str] = []
    honesty = set(HONESTY_LINES.values())
    for raw in (block or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("#") or line.startswith("**"):
            continue
        if line.startswith("_") and line.endswith("_"):
            inner = line.strip("_").strip()
            if inner in honesty or inner.startswith("Change any of it"):
                continue
            bad.append(line)
            continue
        if line.startswith("- "):
            body = line[2:]
            marker = " - "
            idx = body.rfind(marker)
            if idx < 0:
                bad.append(line)
                continue
            tail = body[idx + len(marker):]
            if not any(tail.startswith(p) for p in PROVENANCE_PREFIXES):
                bad.append(line)
            continue
        bad.append(line)
    return bad


def assert_every_line_has_provenance(block: str) -> None:
    """THE fence, run inside render() before the block leaves this module.
    Remove this call and a line without provenance ships."""
    bad = lines_missing_provenance(block)
    if bad:
        raise ProfileLineError(
            "profile lines without provenance: " + " | ".join(bad[:5]))


# --------------------------------------------------------------------------
# One pass over the ledger for every date this page needs
# --------------------------------------------------------------------------

def _sweep(workspace_root) -> dict:
    """ONE read of the event lane (through events_io, never a raw file read)
    collecting every provenance date the page needs:

      style      (layer, knob) -> {origin, ts}     the style_changed lane
      config     skill_name    -> ts               the settings lane
      profile    ts                                this surface's own writes
      first_ts                                     the workspace's own start
    """
    out = {"style": {}, "config": {}, "profile": "", "first_ts": "",
           "started_ts": "", "assessment": "", "coaching": ""}
    # OWNER-TIER read. `load_events_owner_scoped` is the reader for a surface
    # the seat is looking at about themselves - the defensive shard-transparent
    # load with the skipped channel, no account mask and no personal-lane drop,
    # because an owner surface legitimately sees its own book. It is also the
    # reason this module is not a new raw-read site: the raw read stays inside
    # events_io, where the structural guard allows it.
    try:
        from events_io import load_events_owner_scoped
    except ImportError:
        return out
    try:
        rows, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("ts") or "")
        if ts and not out["first_ts"]:
            out["first_ts"] = ts
        etype = row.get("type")
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if etype == "onboarding_checkpoint" and ts and not out["started_ts"]:
            # The workspace's own start, and the STABLE source for the
            # `on file since` shape. Preferred over "the first row in the
            # file", which moves the day a zero-event workspace writes its
            # first event and would make the page non-reproducible.
            out["started_ts"] = ts
        if etype == "style_changed":
            for change in data.get("changes") or []:
                if not isinstance(change, dict):
                    continue
                knob = change.get("knob")
                if knob:
                    out["style"][(str(data.get("layer")), str(knob))] = {
                        "origin": str(data.get("origin") or ""), "ts": ts}
        elif etype in ("skill_first_run_configured", "skill_reconfigured"):
            name = str(data.get("skill_name") or "")
            if name:
                out["config"][name] = ts
            if name == PROFILE_SKILL_KEY:
                out["profile"] = ts
                snap = data.get("config_snapshot")
                if isinstance(snap, dict):
                    if snap.get(ASSESSMENT_KEY):
                        out["assessment"] = ts
                    if snap.get(COACHING_LAYER_KEY):
                        out["coaching"] = ts
    return out


def _entities(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _workspace_since(workspace_root, sweep: dict) -> str:
    """The day this workspace's record starts. Used for the `on file since`
    shape and nothing else."""
    for source in (sweep.get("started_ts"), sweep.get("first_ts")):
        day = _date_only(source, workspace_root)
        if day:
            return day
    day = _date_only(str(_entities(workspace_root).get("last_updated") or ""),
                     workspace_root)
    return day or _dt.date.today().isoformat()


def load_profile_config(workspace_root) -> dict:
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(workspace_root, PROFILE_SKILL_KEY) or {}
    except Exception:
        return {}
    cfg = stored.get("config") if isinstance(stored, dict) else None
    return cfg if isinstance(cfg, dict) else {}


# --------------------------------------------------------------------------
# Stated overrides — the deletion mechanism, and the reason it sticks
# --------------------------------------------------------------------------

def stated_overrides(workspace_root) -> dict:
    """key -> {"suppressed": bool, "value": ..., "origin": "asked",
    "date": "YYYY-MM-DD"}. Written by `delete_learned_line` and by any stated
    correction; read by `filter_learned`.

    WHAT IS READ TODAY, PLAINLY: the `suppressed` flag, and only that. No
    layer reads `value` back, so a stated value is written down and does not
    yet replace the line the page renders. Saying it did would be the same
    class of untruth as a receipt that lies, so this says it does not. Wiring
    the value into the composer is owed, and it is a behaviour change with its
    own pin, not a docstring."""
    ov = load_profile_config(workspace_root).get(STATED_OVERRIDES_KEY)
    return ov if isinstance(ov, dict) else {}


def learned_suppressions(workspace_root) -> frozenset:
    """The learned keys the seat has deleted. THE reader every learning
    writer passes its candidates through - a deleted line that comes back
    next Monday is the failure this exists to prevent."""
    return frozenset(
        k for k, v in stated_overrides(workspace_root).items()
        if isinstance(v, dict) and v.get("suppressed") is True)


def filter_learned(workspace_root, candidates: Iterable) -> List:
    """Drop every candidate whose key the seat has deleted.

    Accepts dicts carrying a `key` (or `fingerprint`) and plain strings.
    Stated outranks learned: this filter is the mechanical form of that
    sentence, and it is the ONE function a learning job has to call."""
    suppressed = learned_suppressions(workspace_root)
    if not suppressed:
        return list(candidates or ())
    kept = []
    for c in candidates or ():
        if isinstance(c, dict):
            key = str(c.get("key") or c.get("fingerprint") or "")
        else:
            key = str(c)
        if key and key in suppressed:
            continue
        kept.append(c)
    return kept


def _save_profile_config(workspace_root, cfg: dict, *, origin: str,
                         previous: Optional[dict] = None,
                         batch_id: Optional[str] = None) -> None:
    from skill_config_writer import save_skill_config
    extra: Dict[str, Any] = {}
    if batch_id:
        extra["brain_batch_id"] = batch_id
        extra["brain_change_class"] = "profile_change"
        extra["previous_config"] = previous if isinstance(previous, dict) else {}
        # NIGHT 11a fix round (REVIEW_NIGHT11A_MERGED_TREE N-2): the three
        # anchors `brain_undo._reverse_commitment_preset` reads, so the
        # "Say undo to put it back" the receipts promise is a real path -
        # `skill_name` is stamped by the writer itself. A write with no
        # snapshot (never the case for the page's own doors, which all pass
        # `previous`) restores by clearing the store, which is what "before"
        # was.
        extra["prev_config_present"] = isinstance(previous, dict)
        extra["prev_config"] = dict(previous) if isinstance(previous, dict) else None
    save_skill_config(workspace_root, PROFILE_SKILL_KEY, cfg,
                      origin=origin, event_extra=extra or None)


def _new_batch_id(prefix: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{prefix}-{stamp}"


# --------------------------------------------------------------------------
# The deletion seam - the event the learning job reads
# --------------------------------------------------------------------------

# A deletion is only permanent if the job that DERIVES learned lines knows
# about it. That job lives in another module and must not import this one
# (and this one must not import it), so the two meet at an event: this
# surface writes it, `learning_pass.deleted_learned_fingerprints` reads it
# and unions what it names into the exclusions every leg already consults.
# The name is the registered one in shared/EVENT_TYPES.md - a different
# spelling here is a deletion that silently does not stick.
DELETION_EVENT = "learned_line_deleted"

# The two key families the learning job can re-derive, and therefore the two
# that need a join key on the event. Everything else on this page - a persona
# knob, a never-track rule - has no proposer to exclude, and its deletion is
# recorded rather than joined.
VOICE_KEY_PREFIX = "learned.voice."
PREP_KEY_PREFIX = "learned.prep."

# The skill that owns the profile page, so the event says who wrote it.
DELETION_SOURCE_SKILL = "workspace-manager"


def _voice_ops() -> List[str]:
    """The ops a voice line can be proposed under - asked of the module that
    OWNS that vocabulary rather than spelled out here, because a vocabulary
    copied into a second file is a vocabulary that goes stale.

    A voice line on this page is per SKILL ("I write your email drafts the
    way you rewrite them"); the learning job proposes per (skill, op). One
    deleted line is therefore one join key per op."""
    try:
        from voice_corrections import VOICE_OP_SECTIONS
    except Exception:
        return []
    try:
        return sorted(str(op) for op in VOICE_OP_SECTIONS if str(op).strip())
    except Exception:  # pragma: no cover - a malformed vocabulary joins nothing
        return []


def _prep_fingerprint(meeting_type: str, section: str) -> str:
    """The section-weight fingerprint, minted by the module that mints the
    proposer's - never re-implemented here. Normalized through that module's
    own join-key helpers when they exist, because the job aggregates on
    normalized names; on a tree without them the raw spelling is what the
    proposer used and is what joins."""
    try:
        import prep_grading as pg
    except Exception:
        return ""
    mint = getattr(pg, "_fp", None)
    if not callable(mint):
        return ""
    norm_type = getattr(pg, "normalize_meeting_type", None)
    norm_section = getattr(pg, "normalize_section_name", None)
    mtype = norm_type(meeting_type) if callable(norm_type) else meeting_type
    sect = norm_section(section) if callable(norm_section) else section
    try:
        return str(mint(mtype, sect))
    except Exception:  # pragma: no cover
        return ""


def deletion_join_keys(key: str) -> List[dict]:
    """The payloads ONE deleted line writes, in the shapes the learning job's
    reader looks up: a `fingerprint` outright, or the natural `{skill, op}`
    a voice line is listed under. The profile `key` rides on every payload so
    the row is readable by a person and by any later reader that joins on it.

    A key naming nothing the job can re-derive still writes ONE payload with
    the key alone. The deletion is a fact worth recording even where there is
    nothing to exclude, and a reader that finds neither shape ignores it."""
    key = str(key or "").strip()
    if not key:
        return []
    if key.startswith(VOICE_KEY_PREFIX):
        skill = key[len(VOICE_KEY_PREFIX):].strip()
        if skill:
            ops = _voice_ops()
            if ops:
                return [{"key": key, "skill": skill, "op": op} for op in ops]
            return [{"key": key, "skill": skill}]
    if key.startswith(PREP_KEY_PREFIX):
        rest = key[len(PREP_KEY_PREFIX):]
        meeting_type, _, section = rest.partition(".")
        if meeting_type.strip() and section.strip():
            fingerprint = _prep_fingerprint(meeting_type, section)
            if fingerprint:
                return [{"key": key, "meeting_type": meeting_type,
                         "section": section, "fingerprint": fingerprint}]
    return [{"key": key}]


def _write_deletion_event(workspace_root, key: str) -> int:
    """Append the deletion to the book, through the canonical gated writer.

    Never raises. The override is already written and the page is already
    right by the time this runs; a book that cannot be appended to must not
    turn "forget that" into an error the seat sees. Returns how many rows
    were written, which is what the caller pins."""
    payloads = deletion_join_keys(key)
    if not payloads:
        return 0
    try:
        from event_gate import append_event
    except Exception:  # pragma: no cover
        return 0
    path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    rows = [{"type": DELETION_EVENT,
             "source_skill": DELETION_SOURCE_SKILL,
             "data": payload} for payload in payloads]
    try:
        append_event(path, rows)
    except Exception:
        return 0
    return len(rows)


def delete_learned_line(workspace_root, key: str, *,
                        today: Optional[str] = None) -> dict:
    """Delete one learned line by its key, and write the deletion twice: as a
    STATED override in this surface's own store, and as a `learned_line_
    deleted` event carrying the join key the learning job excludes on.

    The override is what keeps the line off the page. The event is what keeps
    the job from deriving it again from the same unchanged evidence - without
    it the line is suppressed on the page while being written straight back
    into the store underneath, which is a deletion that only looks like one.

    Returns the receipt: what was deleted, how many join rows were written,
    and the plain sentence that says it will not come back."""
    key = str(key or "").strip()
    if not key:
        raise ValueError("delete_learned_line needs a key")
    day = today or _dt.date.today().isoformat()
    cfg = dict(load_profile_config(workspace_root))
    previous = json.loads(json.dumps(cfg))
    overrides = dict(cfg.get(STATED_OVERRIDES_KEY) or {})
    overrides[key] = {"suppressed": True, "origin": ORIGIN_ASKED, "date": day}
    cfg[STATED_OVERRIDES_KEY] = overrides
    _save_profile_config(workspace_root, cfg, origin="tune",
                         previous=previous,
                         batch_id=_new_batch_id("profile-delete"))
    # The second half of "it does not come back": the override keeps the line
    # off THIS page, the event keeps the learning job from deriving it again.
    written = _write_deletion_event(workspace_root, key)
    return {"key": key, "date": day, "deletion_rows": written,
            "receipt": "Gone, and I won't learn it again - you've said so, "
                       "and what you say outranks what I read."}


def state_override(workspace_root, key: str, value: Any, *,
                   today: Optional[str] = None) -> dict:
    """A stated value for a key - the same store the deletion uses, so the
    same precedence rule covers both."""
    day = today or _dt.date.today().isoformat()
    cfg = dict(load_profile_config(workspace_root))
    previous = json.loads(json.dumps(cfg))
    overrides = dict(cfg.get(STATED_OVERRIDES_KEY) or {})
    overrides[str(key)] = {"suppressed": False, "value": value,
                           "origin": ORIGIN_ASKED, "date": day}
    cfg[STATED_OVERRIDES_KEY] = overrides
    _save_profile_config(workspace_root, cfg, origin="tune",
                         previous=previous,
                         batch_id=_new_batch_id("profile-state"))
    return {"key": str(key), "value": value, "date": day}


# --------------------------------------------------------------------------
# The layers
# --------------------------------------------------------------------------

def _who_lines(workspace_root, sweep, since) -> List[dict]:
    data = _entities(workspace_root)
    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    out: List[dict] = []
    name = str(ws.get("user_first_name") or "").strip()
    if name:
        out.append(_line(SECTION_WHO, "who.name", f"You go by {name}",
                         ORIGIN_ASKED, since))
    brain = str(ws.get("brain_name") or "").strip()
    if brain:
        out.append(_line(SECTION_WHO, "who.brain_name",
                         f"You named me {brain}", ORIGIN_ASKED, since))
    tz = str(ws.get("user_timezone") or "").strip()
    if tz:
        tz_day = _date_only(str(ws.get("tz_set_at") or ""), workspace_root) or since
        origin = (ORIGIN_ASKED if ws.get("tz_set_by") == "user_explicit"
                  else ORIGIN_ON_FILE)
        out.append(_line(SECTION_WHO, "who.timezone",
                         f"You work in {tz}", origin, tz_day))
    shape = str(ws.get("shape") or "").strip()
    if shape:
        article = "an" if shape[:1].lower() in "aeiou" else "a"
        out.append(_line(SECTION_WHO, "who.shape",
                         f"Your book is shaped like {article} {shape} business",
                         ORIGIN_ON_FILE, since))
    cals = ws.get("personal_calendars")
    if isinstance(cals, list) and cals:
        n = len(cals)
        out.append(_line(SECTION_WHO, "who.personal_calendars",
                         f"{n} personal calendar{'' if n == 1 else 's'} "
                         f"sit{'s' if n == 1 else ''} alongside the business "
                         "one", ORIGIN_ASKED, since))
    return out


# The ten customization axes CUSTOM2 owns in skill_config.schema.json. This
# surface READS them through the schema and never defines them: a key that
# does not exist yet simply renders nothing. Named here so the reader is
# explicit about what it is looking for.
CUSTOM2_ORGANIZATION_KEYS = ("organization", "leads_with", "shape",
                             "vocabulary")

_AXIS_EN = {
    "organization": "Your brief is organized by {v}",
    "leads_with": "It leads with {v}",
    "shape": "Shaped as {v}",
    "vocabulary": "You call things: {v}",
    "section_depth": "Section depth: {v}",
    "thresholds": "Your thresholds: {v}",
    "filters": "Filtered: {v}",
    "when": "Delivered {v}",
    "depth": "Depth: {v}",
    "tone": "Tone: {v}",
    "sign_off": "Sign-off: {v}",
    "lens": "Lens: {v}",
    "going_quiet": "The going-quiet line: {v}",
    "slipped_section": "The slipped section: {v}",
    "voice": "Voice: {v}",
    "coaching_layer": "Coaching layer: {v}",
}

_SETTINGS_SKILLS = ("morning-briefing", "end-of-day", "weekly-recap")

#: LEARNFIX1 1.5 — the three settings skills in the words a customer calls
#: them. A collapsed How-you-think line names the surfaces it covers, and it
#: names them the way the surfaces are advertised, never by their skill key.
_SETTINGS_SURFACE_EN = {
    "morning-briefing": "your morning brief",
    "end-of-day": "your day close",
    "weekly-recap": "your weekly wrap",
}


def _plain_value(value: Any) -> str:
    """A stored value in words. Returns "" for anything with nothing to say -
    an empty list, an empty map, a blank string - and the caller DROPS the
    line rather than render a label with nothing after it. Say less when the
    book is thin."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, dict):
        # The one-knob wrapper (`{"enabled": true}`) reads as the knob, not
        # as "enabled on" - the wrapper is plumbing and the reader is not
        # being shown plumbing.
        if set(value) == {"enabled"}:
            return _plain_value(value["enabled"])
        parts = [f"{str(k).replace('_', ' ')} {_plain_value(v)}".strip()
                 for k, v in value.items() if _plain_value(v)]
        return ", ".join(parts)
    if isinstance(value, list):
        return ", ".join(x for x in (_plain_value(v) for v in value) if x)
    return str(value).replace("_", " ").strip()


def _configured_skills(workspace_root) -> Dict[str, dict]:
    """skill -> stored config. Read through the canonical store's own reader
    so the axes CUSTOM2 owns are picked up the day they land, without this
    module naming a single one of them."""
    out: Dict[str, dict] = {}
    d = Path(workspace_root) / "_hq" / "data" / "skill_config"
    if not d.is_dir():
        return out
    try:
        from skill_config_writer import load_skill_config
    except ImportError:
        return out
    for f in sorted(d.glob("*.json")):
        name = f.stem
        try:
            stored = load_skill_config(workspace_root, name) or {}
        except Exception:
            continue
        cfg = stored.get("config") if isinstance(stored, dict) else None
        if isinstance(cfg, dict) and cfg:
            out[name] = {"config": cfg,
                         "configured_at": str(stored.get("configured_at") or "")}
    return out


def _think_lines(workspace_root, sweep, since) -> List[dict]:
    """How you think: the organizing axes and your own words for things.

    LEARNFIX1 1.5 — ONE LINE PER SETTING, not one per skill. This used to
    emit a line for every (skill, axis) pair, and CUSTOM2's `group my brief
    by workstream` writes `organization: workstream` onto BOTH
    `morning-briefing` and `end-of-day` — so "Your brief is organized by
    workstream" printed twice on the page (record B6.1). The grouping key is
    the AXIS AND ITS VALUE, which is what the customer actually set; the
    surfaces it reached are a clause on the one line, not a reason to print
    it again. The line keeps the key of the first surface carrying it, so a
    stated override recorded against that key still binds."""
    out: List[dict] = []
    configured = _configured_skills(workspace_root)
    # (axis, rendered value) -> {key, text, day, surfaces}
    grouped: Dict[tuple, dict] = {}
    order: List[tuple] = []
    for skill in _SETTINGS_SKILLS:
        entry = configured.get(skill)
        if not entry:
            continue
        day = (_date_only(sweep["config"].get(skill), workspace_root)
               or _date_only(entry["configured_at"], workspace_root) or since)
        for axis in CUSTOM2_ORGANIZATION_KEYS:
            if axis not in entry["config"] or not _plain_value(
                    entry["config"][axis]):
                continue
            value = _plain_value(entry["config"][axis])
            template = _AXIS_EN.get(axis, "{v}")
            group_key = (axis, value)
            if group_key not in grouped:
                grouped[group_key] = {
                    "key": f"think.{skill}.{axis}",
                    "text": template.format(v=value),
                    "day": day,
                    "surfaces": [],
                }
                order.append(group_key)
            grouped[group_key]["surfaces"].append(
                _SETTINGS_SURFACE_EN.get(skill, skill.replace("-", " ")))
    for group_key in order:
        g = grouped[group_key]
        text = g["text"]
        if len(g["surfaces"]) > 1:
            names = g["surfaces"]
            joined = (" and ".join(names) if len(names) == 2
                      else ", ".join(names[:-1]) + " and " + names[-1])
            text = f"{text} — the same on {joined}"
        out.append(_line(SECTION_THINK, g["key"], text, ORIGIN_ASKED,
                         g["day"]))
    # NIGHT 11a fix round (N-1): the client's own ordering and naming of
    # their workstreams, recorded at onboarding under the profile store's own
    # key — stated, dated to the call, and carried into the instruction block
    # like every other How-you-think line.
    try:
        stated_ws = load_profile_config(workspace_root).get(WORKSTREAMS_KEY)
    except Exception:
        stated_ws = None
    if isinstance(stated_ws, str) and stated_ws.strip():
        day = (_date_only(sweep["config"].get(PROFILE_SKILL_KEY), workspace_root)
               or since)
        out.append(_line(SECTION_THINK, "think.workstreams.stated",
                         "Your workstreams, in your order: " + stated_ws.strip(),
                         ORIGIN_ASKED, day))
    threads = _entities(workspace_root).get("threads")
    threads = threads if isinstance(threads, list) else []
    active = [t for t in threads
              if isinstance(t, dict) and str(t.get("status") or "") == "active"]
    if active:
        n = len(active)
        out.append(_line(SECTION_THINK, "think.workstreams",
                         f"You run {n} workstream{'' if n == 1 else 's'}, and "
                         "I keep them in the order you named them",
                         ORIGIN_ON_FILE, since))
    return out


def _like_lines(workspace_root, sweep, since) -> List[dict]:
    """How you like it - the style card, absorbed, plus every tuned skill."""
    out: List[dict] = []
    try:
        from chat_persona import (DEFAULT_CHAT_PERSONA, get_chat_persona,
                                  is_persona_configured)
        from style_card import _PERSONA_EN
    except ImportError:
        DEFAULT_CHAT_PERSONA, _PERSONA_EN = {}, {}
        get_chat_persona = is_persona_configured = None
    if (get_chat_persona and is_persona_configured
            and is_persona_configured(workspace_root)):
        persona = get_chat_persona(workspace_root)
        for knob, value in persona.items():
            if value == DEFAULT_CHAT_PERSONA.get(knob):
                continue
            prov = sweep["style"].get(("chat_persona", knob))
            day = _date_only(prov.get("ts") if prov else "", workspace_root) or since
            if prov and prov.get("origin") in ("asked", "recalibrated",
                                               "inferred_confirmed"):
                origin = ORIGIN_ASKED
            elif prov:
                origin = ORIGIN_LEARNED
            else:
                origin = ORIGIN_ON_FILE
            if knob == "never_line":
                label = str(value)
            elif knob == "humor_ok":
                label = "Light humor welcome" if value else "No humor"
            elif knob == "language":
                label = f"I answer in '{value}'"
            else:
                label = _PERSONA_EN.get(knob, {}).get(str(value), str(value))
            if origin == ORIGIN_LEARNED:
                out.append(_line(SECTION_LIKE, f"like.persona.{knob}", label,
                                 origin, day, "my read of how you write"))
            else:
                out.append(_line(SECTION_LIKE, f"like.persona.{knob}", label,
                                 origin, day))
    for skill, entry in _configured_skills(workspace_root).items():
        if skill in ("chat_persona", PROFILE_SKILL_KEY):
            continue
        day = (_date_only(sweep["config"].get(skill), workspace_root)
               or _date_only(entry["configured_at"], workspace_root) or since)
        for axis, value in entry["config"].items():
            if skill in _SETTINGS_SKILLS and axis in CUSTOM2_ORGANIZATION_KEYS:
                continue  # already rendered under How you think
            if not _plain_value(value):
                continue  # a knob with nothing set is not a line
            template = _AXIS_EN.get(axis)
            label = (template.format(v=_plain_value(value)) if template
                     else f"{str(axis).replace('_', ' ')}: {_plain_value(value)}")
            pretty = skill.replace("-", " ").replace("_", " ")
            out.append(_line(SECTION_LIKE, f"like.{skill}.{axis}",
                             f"{pretty} - {label[0].lower() + label[1:]}",
                             ORIGIN_ASKED, day))
    return out


def _working_on_lines(workspace_root, sweep, since) -> List[dict]:
    """What you are working on. Renders NOTHING on an observed seat, which is
    every seat until somebody opens one of the two doors."""
    try:
        import coaching_doors
    except ImportError:
        return []
    return coaching_doors.profile_lines(workspace_root, sweep, since, _line)


def _brought_lines(workspace_root, sweep, since) -> List[dict]:
    """What you brought - the assessment door. Scores and a consent date.
    Never report text: `record_assessment` refuses it at the write, and there
    is nothing here that could render it if it got in."""
    a = load_profile_config(workspace_root).get(ASSESSMENT_KEY)
    if not isinstance(a, dict) or not a:
        return []
    day = (str(a.get("consent_date") or "")
           or _date_only(sweep.get("assessment"), workspace_root) or since)
    scores = a.get("scores") if isinstance(a.get("scores"), dict) else {}
    if not scores:
        return []
    label = ASSESSMENT_LABELS.get(str(a.get("instrument") or ""),
                                  "profile you brought")
    top = sorted(scores.items(), key=lambda kv: -float(kv[1]))[:2]
    pair = " and ".join(k.replace("_", " ") for k, _v in top)
    return [
        _line(SECTION_BROUGHT, "brought.assessment",
              f"You brought a {label}; it reads highest on {pair}",
              ORIGIN_UPLOADED, day),
        _line(SECTION_BROUGHT, "brought.assessment.use",
              "I use it for the order and the framing of what I write you, "
              "never for a conclusion about you", ORIGIN_UPLOADED, day),
    ]


def _learned_lines(workspace_root, sweep, since) -> List[dict]:
    """The learned layer. Every candidate passes through `filter_learned`, so
    a deleted line is gone here for good - not just hidden on this render."""
    candidates: List[dict] = []
    ws = workspace_root

    # LEARNFIX1 1.4 — ITERATE WHAT THE JOB WROTE, not what it has left to
    # read. This loop used to walk `sorted(unreviewed_counts(ws))`:
    # corrections NEWER than the last `voice_calibration_review` cutoff. The
    # learning job writes that marker for every skill it touched IN THE SAME
    # FIRE (`learning_pass.run_voice_leg`), so the moment a change lands the
    # count for that skill is 0, the loop never reached
    # `load_voice_block_override`, and the page said "nothing yet" four hours
    # after four changes had been applied (record B6.1). The job's own
    # artifact is the override FILE, so that is the population; the counts
    # stay as the EVIDENCE clause only, and drop to "your edits" at zero.
    # This is the same correction N-5 already applied to the prep family two
    # paragraphs below, which reads the weights it wrote for exactly this
    # reason.
    try:
        from voice_corrections import (load_voice_block_override,
                                       unreviewed_counts, written_overrides)
        counts = unreviewed_counts(ws) or {}
        learned_skills = written_overrides(ws) or []
    except Exception:
        load_voice_block_override, counts, learned_skills = None, {}, []
    if load_voice_block_override:
        for skill in learned_skills:
            try:
                ov = load_voice_block_override(ws, skill)
            except Exception:
                ov = None
            if not ov:
                continue
            # `load_voice_block_override` returns the writer's own header
            # stamp under `last_refreshed`; the two spellings below were
            # never keys it returns, so every voice line's date silently
            # fell back to the page's `since`.
            day = _date_only(str(ov.get("last_refreshed")
                                 or ov.get("updated_at")
                                 or ov.get("refreshed_at") or ""), ws) or since
            n = int(counts.get(skill) or 0)
            candidates.append({
                "key": f"learned.voice.{skill}",
                "text": f"I write your {skill.replace('-', ' ')} drafts the "
                        "way you rewrite them",
                "date": day,
                "evidence": f"{n} of your edits" if n else "your edits"})

    # NIGHT 11a fix round (REVIEW_NIGHT11A_MERGED_TREE N-5): the prep family
    # used to re-derive itself from the grade aggregate under keys the
    # aggregate never produced, so no prep line ever rendered and the
    # deletion door had no entrance. What the job LEARNED is what it WROTE:
    # a section weight of 0 in call-prep's own store. Read that, as the
    # voice family reads the override file it wrote.
    try:
        from skill_config_writer import load_skill_config
        stored = load_skill_config(ws, "call-prep") or {}
        cfg = stored.get("config") if isinstance(stored, dict) else None
        weights = (cfg or {}).get("section_weights") if isinstance(cfg, dict) else None
    except Exception:
        weights = None
    if isinstance(weights, dict):
        for meeting_type in sorted(weights):
            per_section = weights.get(meeting_type)
            if not isinstance(per_section, dict):
                continue
            for section in sorted(per_section):
                try:
                    weight = float(per_section.get(section))
                except (TypeError, ValueError):
                    continue
                if weight != 0:
                    continue
                candidates.append({
                    "key": f"learned.prep.{meeting_type}.{section}",
                    "text": f"I leave the {section} section out of "
                            f"{meeting_type} prep",
                    "date": since,
                    "evidence": "it kept coming up empty in your prep grades"})

    try:
        from commitment_noise import load_never_track_rules
        rules = load_never_track_rules(ws) or []
    except Exception:
        rules = []
    for pattern in rules:
        candidates.append({
            "key": f"learned.never_track.{pattern}",
            "text": f"I don't track low-stakes items from {pattern}",
            "date": since,
            "evidence": "you dropped them repeatedly"})

    # No `filter_learned` here: `build_lines` runs every layer's rows through
    # it, so a second call would be a call site with nothing to prove.
    return [_line(SECTION_LEARNED, c["key"], c["text"], ORIGIN_LEARNED,
                  c["date"], c["evidence"])
            for c in candidates]


def _boundaries_lines(workspace_root, sweep, since) -> List[dict]:
    cfg = load_profile_config(workspace_root)
    out: List[dict] = []
    # The boundaries carry their OWN set-on date, not "whenever this store was
    # last written". That is not cosmetic: `turn off coaching` restores this
    # config byte-for-byte, and a date sourced from the last write would move
    # every time anything else in the store did — so the page would not come
    # back byte-equal after a walk that was turned off again.
    day = (str(cfg.get(BOUNDARIES_SET_AT_KEY) or "")
           or _date_only(sweep.get("profile"), workspace_root) or since)
    bounds = cfg.get(BOUNDARIES_KEY)
    if isinstance(bounds, list):
        for i, b in enumerate(bounds):
            text = str(b).strip()
            if text:
                out.append(_line(SECTION_BOUNDARIES, f"boundaries.{i}", text,
                                 ORIGIN_ASKED, day))
    layer = cfg.get(COACHING_LAYER_KEY)
    if layer and layer != "observed":
        out.append(_line(SECTION_BOUNDARIES, "boundaries.coaching",
                         "Anything we work on together stays out of shared "
                         "documents, the brief, the wrap and every team view",
                         ORIGIN_ASKED, day))
    return out


_LAYERS = (
    (SECTION_WHO, _who_lines),
    (SECTION_THINK, _think_lines),
    (SECTION_LIKE, _like_lines),
    (SECTION_WORKING_ON, _working_on_lines),
    (SECTION_BROUGHT, _brought_lines),
    (SECTION_LEARNED, _learned_lines),
    (SECTION_BOUNDARIES, _boundaries_lines),
)


def build_lines(workspace_root, *, sweep: Optional[dict] = None) -> List[dict]:
    """Every profile line, in section order, each carrying its provenance.

    A DELETED LINE IS GONE FROM THE WHOLE PAGE, and it is gone here rather
    than inside whichever layer happened to remember. `delete_learned_line`
    hands back "Gone, and I won't learn it again", and the page footer says
    the same; before this gate existed that receipt was true for the learned
    layer and false for the persona-derived line under How you like it,
    which is exactly the kind of promise that must not depend on a layer
    author's diligence. Every layer's rows go through the ONE filter, so a
    layer added later inherits the deletion without knowing about it."""
    sweep = sweep if sweep is not None else _sweep(workspace_root)
    since = _workspace_since(workspace_root, sweep)
    out: List[dict] = []
    for _section, fn in _LAYERS:
        out.extend(fn(workspace_root, sweep, since) or [])
    return filter_learned(workspace_root, out)


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------

def render(workspace_root: Union[str, os.PathLike], *,
           write_view: bool = True) -> Dict[str, Any]:
    """The page. Returns {"chat_block", "view_path", "sections", "lines"}."""
    ws = Path(workspace_root)
    sweep = _sweep(ws)
    lines = build_lines(ws, sweep=sweep)
    try:
        from personification import get_brain_name
        brain = get_brain_name(ws)
    except Exception:
        brain = "Command Room"

    by_section: Dict[str, List[dict]] = {k: [] for k in SECTION_KEYS}
    for ln in lines:
        by_section.setdefault(ln["section"], []).append(ln)

    out = [f"## Your profile - what {brain} knows about you", ""]
    for key, title in SECTIONS:
        out.append(f"**{title}**")
        rows = by_section.get(key) or []
        if rows:
            out.extend(render_line(r) for r in rows)
        else:
            out.append(f"_{HONESTY_LINES[key]}_")
        out.append("")
    out.append("_Change any of it in a sentence. Delete any learned line by "
               "saying so and I won't learn it again. Say \"export my "
               "profile\" for the document, or reset a section to put it "
               "back._")
    chat_block = "\n".join(out)

    # THE FENCE. Removing this call is the removal-proof: a line without
    # provenance then ships.
    assert_every_line_has_provenance(chat_block)

    view_path = ws / "_hq" / "views" / "PROFILE.md"
    if write_view:
        view_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(view_path, chat_block + "\n")
    return {"chat_block": chat_block, "view_path": str(view_path),
            "sections": {k: [dict(r) for r in (by_section.get(k) or [])]
                         for k in SECTION_KEYS},
            "lines": lines}


# --------------------------------------------------------------------------
# reset <section>
# --------------------------------------------------------------------------

_SECTION_KEY_PREFIX = {
    SECTION_WHO: "who.",
    SECTION_THINK: "think.",
    SECTION_LIKE: "like.",
    SECTION_WORKING_ON: "working_on.",
    SECTION_BROUGHT: "brought.",
    SECTION_LEARNED: "learned.",
    SECTION_BOUNDARIES: "boundaries.",
}


#: LEARNFIX1 1.5 — for each section, the OWNERS of the lines this surface
#: only displays, in the customer's own words and with the phrase that
#: actually acts where there is one.
#:
#: WHY. `reset how you think` clears this page's own layer for the section.
#: On a real seat that layer is often empty: How-you-think's lines are brief
#: SETTINGS plus one live-computed count, so the announced verb cleared
#: nothing and still said "…is back to where it started" (record B6.1) — a
#: promised verb that cannot act, which is worse than one that says so. When
#: `cleared == 0` the receipt names who does own the lines, in ONE sentence.
#: No menu, no function name, no raw value, and no question: M's standing
#: rule is that a question is a defect, and a four-option fork over a thing
#: the page cannot do is the defect twice.
SECTION_LINE_OWNERS = {
    SECTION_WHO: ("what I know about you comes from your workspace records, "
                  "not from this page",),
    SECTION_THINK: ("your brief's grouping is a brief setting (say "
                    "`reset my brief`)",
                    "the workstream count is read from your workspace"),
    SECTION_LIKE: ("how I talk to you is the style card (say "
                   "`recalibrate my style`)",
                   "each surface's own settings are its own (say "
                   "`reset my brief`)"),
    SECTION_WORKING_ON: ("the coaching shape has its own door (say "
                         "`turn off coaching`)",),
    SECTION_BROUGHT: ("there is nothing on file from an assessment to put "
                      "back",),
    # Fix round 1 (REVIEW_LEARNFIX1 F-9). This read "what I learned is the
    # learning job's" — a job is machinery, and the customer never named it
    # that way. The product's own words for this pass, shipped since LEARN1,
    # are "learning from your edits", and its door is `stop learning from my
    # edits` (`commitment_policy_pass.FLOW_SWITCH_ACKS`, the switch's acks).
    # Every other owner on this page names a door; this one now does too.
    SECTION_LEARNED: ("what I learned comes from your own edits (say `stop "
                      "learning from my edits` to stop it), and a single "
                      "line goes with `forget that`",),
    SECTION_BOUNDARIES: ("there are no boundaries on file to put back",),
}


def _owners_sentence(section: str) -> str:
    """The ONE sentence a zero-clear reset returns. Never a menu, never a
    question — the suite pins both."""
    title = SECTION_TITLES.get(section, "this section")
    owners = SECTION_LINE_OWNERS.get(section) or ()
    if not owners:
        return (f"Nothing in {title} is mine to reset — every line in it "
                "comes from somewhere else.")
    if len(owners) == 1:
        body = owners[0]
    elif len(owners) == 2:
        body = f"{owners[0]}, and {owners[1]}"
    else:
        body = ", ".join(owners[:-1]) + ", and " + owners[-1]
    # The section is NAMED — a person who typed `reset how you think` is told
    # about the section they named, not about "this page".
    return f"Nothing in {title} is mine to reset: {body}."


def reset_section(workspace_root, section: str) -> dict:
    """Put one section back. Scope, stated plainly: this clears THIS
    surface's own layer for the section - the stated overrides it holds, the
    deletions that suppress learned lines, and (for the sections this surface
    owns outright) the assessment, the coaching shape and the boundaries. It
    does NOT reach into another owner's store: the persona has its own
    `recalibrate my style` and the per-skill settings have `reset my brief`.
    A second writer for a store that already has one is the bug this
    deliberately does not create."""
    section = str(section or "").strip().lower().replace(" ", "_")
    if section not in SECTION_KEYS:
        raise ValueError(f"unknown profile section {section!r} "
                         f"(known: {', '.join(SECTION_KEYS)})")
    cfg = dict(load_profile_config(workspace_root))
    previous = json.loads(json.dumps(cfg))
    cleared = 0
    overrides = dict(cfg.get(STATED_OVERRIDES_KEY) or {})
    prefix = _SECTION_KEY_PREFIX[section]
    for k in list(overrides):
        if k.startswith(prefix):
            overrides.pop(k)
            cleared += 1
    cfg[STATED_OVERRIDES_KEY] = overrides
    if section == SECTION_BROUGHT and cfg.get(ASSESSMENT_KEY):
        cfg.pop(ASSESSMENT_KEY)
        cleared += 1
    if section == SECTION_THINK and cfg.get(WORKSTREAMS_KEY):
        # fix-round review (PO item 4): the N-1 key is this section's own
        # store, so "back to where it started" has to clear it as it clears
        # boundaries and the assessment for theirs.
        cfg.pop(WORKSTREAMS_KEY)
        cleared += 1
    if section == SECTION_WORKING_ON and cfg.get(COACHING_LAYER_KEY):
        cfg[COACHING_LAYER_KEY] = "observed"
        cleared += 1
    if section == SECTION_BOUNDARIES and cfg.get(BOUNDARIES_KEY):
        cfg.pop(BOUNDARIES_KEY)
        cleared += 1
    if not cleared:
        # LEARNFIX1 1.5 — nothing on this page was this section's to clear,
        # so NOTHING IS WRITTEN. A config save over an unchanged store would
        # leave a batch behind promising an `undo` of a change that never
        # happened, which is the same lie from the other direction.
        return {"section": section, "cleared": 0,
                "receipt": _owners_sentence(section)}
    _save_profile_config(workspace_root, cfg, origin="tune",
                         previous=previous,
                         batch_id=_new_batch_id("profile-reset"))
    return {"section": section, "cleared": cleared,
            "receipt": f"{SECTION_TITLES[section]} is back to where it "
                       "started. Say undo to put it back."}


# --------------------------------------------------------------------------
# The assessment door
# --------------------------------------------------------------------------

ASSESSMENT_CVI = "cvi"
ASSESSMENT_CI = "culture_index"
ASSESSMENT_LABELS = {ASSESSMENT_CVI: "values profile",
                     ASSESSMENT_CI: "working-style profile"}

CVI_SCORES = ("builder", "merchant", "innovator", "banker")
CVI_TOTAL = 72
CI_SCORES = ("autonomy", "social_ability", "pace", "conformity", "energy",
             "logic", "ingenuity")


class AssessmentRefused(ValueError):
    """The write was refused. Either the shape is wrong, or - the one that
    matters - somebody tried to store report text."""


def assert_no_report_text(payload: dict) -> None:
    """The fence on the assessment door. Scores are numbers and a consent
    date is a date; the instrument's report text and methodology are the
    vendor's and may not be reproduced, so there is no field here that can
    hold a sentence. Any string outside the three short label keys, and any
    non-numeric score, is refused.

    Remove this call and a pasted report body lands in the store."""
    allowed_strings = {"instrument", "consent_date", "source"}
    for key, value in (payload or {}).items():
        if key in allowed_strings:
            if not isinstance(value, str) or len(value) > 40:
                raise AssessmentRefused(
                    f"{key} must be a short label, not report text")
            continue
        if key == "scores":
            for sk, sv in (value or {}).items():
                if isinstance(sv, bool) or not isinstance(sv, (int, float)):
                    raise AssessmentRefused(
                        f"score {sk} must be a number - this door stores "
                        "scores, never report text")
            continue
        raise AssessmentRefused(
            f"unexpected field {key!r} on the assessment door - scores and a "
            "consent date only, never report text")


def record_assessment(workspace_root, instrument: str, scores: dict,
                      consent_date: str, *, source: str = "entered") -> dict:
    """The assessment door. Entered by the operator at onboarding, or
    uploaded later. The product NEVER offers it - there is no code path here
    that asks, and `assessment_offer_scan` pins that no skill's prose does
    either."""
    instrument = str(instrument or "").strip().lower()
    if instrument not in (ASSESSMENT_CVI, ASSESSMENT_CI):
        raise AssessmentRefused(f"unknown instrument {instrument!r}")
    if not consent_date or len(str(consent_date)) != 10:
        raise AssessmentRefused("a consent date (YYYY-MM-DD) is required")
    scores = {str(k): v for k, v in (scores or {}).items()}
    if instrument == ASSESSMENT_CVI:
        if set(scores) != set(CVI_SCORES):
            raise AssessmentRefused(
                f"a values profile carries exactly {len(CVI_SCORES)} scores")
        for v in scores.values():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise AssessmentRefused("scores must be numbers")
        if sum(float(v) for v in scores.values()) != CVI_TOTAL:
            raise AssessmentRefused(
                f"the four scores must sum to {CVI_TOTAL}")
    else:
        if set(scores) != set(CI_SCORES):
            raise AssessmentRefused(
                f"a working-style profile carries exactly {len(CI_SCORES)} "
                "centiles")
        for k, v in scores.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise AssessmentRefused("centiles must be numbers")
            if not 0 <= float(v) <= 100:
                raise AssessmentRefused(f"{k} must be a centile 0-100")
    payload = {"instrument": instrument, "scores": scores,
               "consent_date": str(consent_date), "source": str(source)}
    assert_no_report_text(payload)
    cfg = dict(load_profile_config(workspace_root))
    previous = json.loads(json.dumps(cfg))
    cfg[ASSESSMENT_KEY] = payload
    _save_profile_config(workspace_root, cfg, origin="tune",
                         previous=previous,
                         batch_id=_new_batch_id("profile-assessment"))
    return payload


# Phrases that would amount to the product OFFERING the assessment. The
# memo's ruling is that onboarding may offer it once, by the operator, and
# the product never raises it - so none of these may appear in a skill's
# customer-facing prose.
ASSESSMENT_OFFER_PHRASES = (
    "take the cvi", "take a cvi", "take the culture index",
    "take an assessment", "take the assessment",
    "want to take the assessment", "shall i send you the assessment",
)


def assessment_offer_scan(skills_dir) -> List[str]:
    """Every skill file whose prose offers the assessment. Expected empty."""
    hits: List[str] = []
    root = Path(skills_dir)
    if not root.is_dir():
        return hits
    for md in sorted(root.glob("*/SKILL.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        for phrase in ASSESSMENT_OFFER_PHRASES:
            if phrase in text:
                hits.append(f"{md.parent.name}: {phrase}")
    return hits


# --------------------------------------------------------------------------
# The switch, and the workspace instructions
# --------------------------------------------------------------------------

def regenerate_instructions_enabled(
        workspace_root, *,
        default: bool = REGENERATE_INSTRUCTIONS_DEFAULT) -> bool:
    """Is `profile.regenerate_instructions` on for this workspace?

    Only a literal False turns it off. A missing key, a string, a malformed
    store and an unreadable file all read as ON - what the switch gates is a
    regenerated block with a receipt and an undo, and a typo in a config file
    is not a customer saying stop."""
    if workspace_root is None:
        return default
    try:
        val = load_profile_config(workspace_root).get(
            REGENERATE_INSTRUCTIONS_KEY)
        return False if val is False else default
    except Exception:
        return default


# The sections whose content is written by the coaching walk and by nothing
# else. They are the confidentiality tier in section form, and they may not
# appear in anything shared. `boundaries` is here because the ONLY writer of
# `profile.boundaries` is the walk's third question - so a boundary is
# coaching-conversation text, and the tier marker line the page renders
# beneath it is coaching text about coaching.
COACHING_OWNED_SECTIONS = (SECTION_WORKING_ON, SECTION_BOUNDARIES)

INSTRUCTION_SECTIONS = (SECTION_THINK, SECTION_LIKE)

# THE STRUCTURAL FENCE, in two halves, and the second one is the load-bearing
# half. The leak scanner cannot be the fence here: it is marker-based, and a
# boundary is an ordinary English sentence carrying no marker at all, so it
# passes the scan and lands in a shared file.
#
# The assertion below fires at IMPORT, before any caller runs, and stops a
# widening of INSTRUCTION_SECTIONS from ever loading — DURING DEVELOPMENT.
# Under `python -O` the interpreter strips assertions, and a widened tuple
# then imports cleanly. So in production the filter inside `instruction_lines`
# is not a second opinion — it is the whole fence, and it is why the filter is
# there rather than being "already covered" by this line.
assert not (set(INSTRUCTION_SECTIONS) & set(COACHING_OWNED_SECTIONS)), (
    "coaching-owned sections may never be written into the workspace "
    "instructions - the instructions are a shared file")


def instruction_lines(workspace_root) -> List[str]:
    """The profile lines that belong in the workspace instructions the model
    reads on every turn - so the profile shapes how the product talks even in
    a loose question. Generated, never hand-edited: the persona precedent
    (render_claude_md BLOCK_PERSONA) is the mechanism and this is its body.

    Nothing from the coaching or the assessment sections goes in - the
    instructions are a shared file and those two live in their own tier.
    That covers BOUNDARIES too, and the reason is worth stating: the only
    writer of `profile.boundaries` is the coaching walk's third question, so
    a boundary is a sentence said inside a coaching conversation. It renders
    on the seat's own page and it governs the coaching surfaces; it does not
    go in the file every shared session reads. If a boundary should be
    stated OUTSIDE coaching and honoured everywhere, that wants a stated
    boundary of its own with its own writer - a ruling, not a widening of
    this tuple.

    Only STATED lines go in. Two reasons, both deliberate. A learned line is
    a proposal the seat can delete, and the instructions the model reads on
    every turn are not the place to act on a proposal. And a line that is
    merely on the workspace record (`on file since`) is already in the
    generated register block above it - putting it here as well would say
    the same thing twice in the file that costs the most to say it in."""
    if not regenerate_instructions_enabled(workspace_root):
        return []
    return [f"- {ln['text']}" for ln in build_lines(workspace_root)
            if ln["section"] in INSTRUCTION_SECTIONS
            and ln["section"] not in COACHING_OWNED_SECTIONS
            and ln["origin"] in (ORIGIN_ASKED, ORIGIN_UPLOADED)]


def profile_block_active(workspace_root) -> bool:
    """The block participates only when the switch is on AND there is
    something to say. A never-configured workspace is byte-identical to
    pre-PROFILE1."""
    return bool(instruction_lines(workspace_root))


# --------------------------------------------------------------------------
# "how do you know that" — the trust map, derived
# --------------------------------------------------------------------------

def explain(workspace_root, key: str) -> str:
    """One line's provenance, in a sentence. The answer to "how do you know
    that" - and the reason a complaint becomes a correction instead of a loss
    of trust."""
    for ln in build_lines(workspace_root):
        if ln["key"] == str(key):
            return f"{ln['text']} - {ln['provenance']}."
    if str(key) in learned_suppressions(workspace_root):
        return ("You deleted that one, so I dropped it and I won't learn it "
                "again.")
    return "I don't have that on file."


def trust_map(workspace_root) -> List[dict]:
    """What I know / what I half-know / what I can't know yet, per section,
    DERIVED from what the stores actually hold - never hand-written, so it
    cannot go stale the way every written guide before it did.

    (Intake: operator-guidance-trust-map, folded in here as provenance.)"""
    lines = build_lines(workspace_root)
    out: List[dict] = []
    for key, title in SECTIONS:
        rows = [ln for ln in lines if ln["section"] == key]
        stated = sum(1 for r in rows
                     if r["origin"] in (ORIGIN_ASKED, ORIGIN_UPLOADED))
        read = sum(1 for r in rows if r["origin"] == ORIGIN_LEARNED)
        if stated:
            band, because = "reliable", "you told me"
        elif read:
            band, because = "my read", "I worked it out from what you did"
        else:
            band, because = "not yet", "nothing on file"
        out.append({"section": key, "title": title, "band": band,
                    "because": because, "stated": stated, "learned": read})
    return out


__all__ = [
    "SECTIONS", "SECTION_KEYS", "SECTION_TITLES", "HONESTY_LINES",
    "COACHING_OWNED_SECTIONS",
    "ORIGINS", "PROVENANCE_PREFIXES", "PROFILE_SKILL_KEY",
    "REGENERATE_INSTRUCTIONS_KEY", "STATED_OVERRIDES_KEY", "ASSESSMENT_KEY",
    "BOUNDARIES_KEY", "BOUNDARIES_SET_AT_KEY", "COACHING_LAYER_KEY",
    "ProfileLineError", "AssessmentRefused",
    "provenance_phrase", "render_line", "lines_missing_provenance",
    "assert_every_line_has_provenance", "build_lines", "render",
    "load_profile_config", "stated_overrides", "learned_suppressions",
    "filter_learned", "delete_learned_line", "state_override",
    "DELETION_EVENT", "deletion_join_keys",
    "reset_section", "SECTION_LINE_OWNERS",
    "record_assessment", "assert_no_report_text",
    "assessment_offer_scan", "ASSESSMENT_OFFER_PHRASES",
    "regenerate_instructions_enabled", "instruction_lines",
    "profile_block_active", "explain", "trust_map",
    "CVI_SCORES", "CI_SCORES", "CVI_TOTAL", "ASSESSMENT_CVI", "ASSESSMENT_CI",
]
