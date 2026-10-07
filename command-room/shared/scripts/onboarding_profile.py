#!/usr/bin/env python3
"""ONBOARD2 — the day-one profile object (SPEC_SURFACES2 §9).

`new-client-onboarding` and `command-room-onboarding` write the profile
object on day one: who the client is, how they think (workstreams in their
own order and words), how they like it, and their boundaries — plus
whatever assessment scores the operator enters by hand, with a consent
date. NOTHING ON THIS PAGE WAS ASKED OF THE CLIENT BY THE PRODUCT. Say it
that precisely: onboarding's Phase 0 setup widget still asks the client
four questions (role, timezone, AI name, email draft posture), so the
unqualified "the product asks the client nothing" is true of the profile
object and of the interaction preset, and false of onboarding as a whole.
What is true here without qualification: every write in this module is
something the OPERATOR (M, or whoever is running the call) heard on the
call and typed in afterward — never a question the skill puts to the
customer, and Phase 6a2 contains no question mark at all. The
onboarding SKILL.md marks every call site "OPERATOR ONLY" and skips this
whole sub-step on a self-serve install (the no-operator rule already
governs every other OPERATOR line in that file).

WHAT THIS WRITES, AND WHERE IT LANDS ON THE PROFILE PAGE
----------------------------------------------------------
`record_how_they_think` writes the client's own ordering and naming of their
workstreams to the profile store's own registered key (`profile.workstreams`,
`skill_config.schema.json`) — never to a brief setting; `profile._think_lines`
renders it under How you think as a stated line and it reaches the
instruction block. `record_how_they_like_it` records ONE stated preference on
a registered axis: a brief axis `brief_settings` owns goes through
`brief_settings.apply_settings` (both surfaces, one batch, an `undo`) and is
validated by that door; the two legacy `depth` knobs are validated against
their documented sets; anything else lands as-is on the named skill.
`record_boundaries` writes the `profile` skill's `boundaries` /
`boundaries_set_at` keys (`profile.py`'s BOUNDARIES_KEY / BOUNDARIES_SET_AT_KEY).
(NIGHT 11a fix round, REVIEW_NIGHT11A_MERGED_TREE N-1: the first cut wrote the
workstream sentence into morning-briefing's `leads_with` enum.)

PROVENANCE, READ AGAINST profile.py's DOCUMENTED CONTRACT
-----------------------------------------------------------
`profile.py`'s `_think_lines` and `_like_lines` render every configured
skill-axis value as `asked <date>` UNCONDITIONALLY — the config store's own
`origin` metadata is not consulted for those lines (only `chat_persona`
gets origin-sensitive treatment, and this module never touches
`chat_persona`). So every line this module's writes produce on the profile
page carries `asked <date>` provenance, never `learned`. `record_boundaries`
lands under `_boundaries_lines`, which is likewise unconditional
`ORIGIN_ASKED`. This mapping is pinned by
`tests/run_onboard2_profile_test.py`'s `test_profile1_contract_pins` against
the exact PROFILE1 commit this lane was told to build against
(236e8d9145fd98537b796b80f015346c55fb156f) — see that test if the contract
ever drifts before the trial merge.

`record_assessment_from_session` is the one write that renders `uploaded
<date>`, never `asked` — that is PROFILE1's own fixed shape for the
assessment door (`_brought_lines` hard-codes `ORIGIN_UPLOADED`), and it is
also §9's own words: "assessment scores M enters by hand... with a consent
date recorded." BUILD_ONBOARD2's record states this plainly as the one
line on a fresh profile that is not `asked`.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_config_writer import load_skill_config, save_skill_config  # noqa: E402

# Where "how they think" lands. NIGHT 11a fix round (REVIEW_NIGHT11A_MERGED_TREE
# N-1): this used to be morning-briefing's `leads_with` — a five-value enum
# every brief reader keys on — so the client's own sentence rendered on the
# profile page and in CLAUDE.md as "It leads with Ops first, then…" while the
# brief silently kept its default order. The sentence is the profile's own
# fact now, under its own registered key (skill_config.schema.json `profile`),
# rendered under How you think by `profile._think_lines`.
THINK_SKILL = "profile"
THINK_AXIS = "workstreams"
LIKE_DEFAULT_SKILL = "morning-briefing"
LIKE_DEFAULT_AXIS = "depth"

# The one pre-CUSTOM2 morning-briefing knob with a closed value set that
# brief_settings does not own (skills/morning-briefing/SKILL.md: headline |
# full). A stated preference lands as the setting's own word; the client's
# phrasing belongs in the session note, never in the store.
_LEGACY_CHOICES = {("morning-briefing", "depth"): ("headline", "full"),
                   ("call-prep", "depth"): ("standard", "deep")}


def _refuse_unreadable(skill: str, axis: str, value) -> None:
    """A stated preference must land as a value its reader can read. A brief
    axis `brief_settings` owns is validated through its own door; the legacy
    `depth` knob through its documented set. Raises ValueError with the plain
    sentence the door itself would say."""
    try:
        from brief_settings import AXES, SURFACES, validate_value
    except Exception:  # pragma: no cover — no brief_settings, nothing to refuse
        AXES, SURFACES, validate_value = {}, (), None
    if skill in SURFACES and axis in AXES and validate_value is not None:
        ok, reason = validate_value(axis, value)
        if not ok:
            raise ValueError(f"{skill}.{axis}: {reason}")
        return
    choices = _LEGACY_CHOICES.get((skill, axis))
    if choices and value not in choices:
        raise ValueError(f"{skill}.{axis} takes one of {', '.join(choices)} "
                         f"— not {value!r}")

# The profile config's own store (PROFILE1's PROFILE_SKILL_KEY / BOUNDARIES_KEY /
# BOUNDARIES_SET_AT_KEY, copied here as literal strings per the pin discipline —
# "pin against the producing constant" — rather than importing profile.py, which
# is not present on this branch pre-merge).
PROFILE_SKILL_KEY = "profile"
BOUNDARIES_KEY = "boundaries"
BOUNDARIES_SET_AT_KEY = "boundaries_set_at"


def _today(today: Optional[str] = None) -> str:
    return today or _dt.date.today().isoformat()


def _new_batch_id(prefix: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{prefix}-{stamp}"


def _existing_config(workspace_root, skill_name: str) -> Dict[str, Any]:
    stored = load_skill_config(workspace_root, skill_name) or {}
    cfg = stored.get("config") if isinstance(stored, dict) else None
    return dict(cfg) if isinstance(cfg, dict) else {}


# ---------------------------------------------------------------------------
# How they think — workstreams, in their own order and words
# ---------------------------------------------------------------------------

def record_how_they_think(workspace_root, workstreams_text: str, *,
                          skill: str = THINK_SKILL,
                          axis: str = THINK_AXIS,
                          today: Optional[str] = None) -> dict:
    """Record the client's own ordering/naming of their workstreams, exactly
    as the operator heard it on the call — never the scan's inferred
    cluster labels. Lands on the profile's "How you think" section, dated
    to this call, `asked` provenance (see module docstring)."""
    text = str(workstreams_text or "").strip()
    if not text:
        raise ValueError("record_how_they_think needs the client's own words")
    _refuse_unreadable(skill, axis, text)
    cfg = _existing_config(workspace_root, skill)
    cfg[axis] = text
    save_skill_config(workspace_root, skill, cfg, origin="asked")
    return {"skill": skill, "axis": axis, "value": text, "date": _today(today)}


# ---------------------------------------------------------------------------
# How they like it — one stated preference at a time
# ---------------------------------------------------------------------------

def record_how_they_like_it(workspace_root, skill: str, axis: str,
                            value: Any, *, today: Optional[str] = None) -> dict:
    """Record one stated preference on an already-registered skill axis
    (e.g. morning-briefing's `depth`). Never called for anything the scan
    inferred — only for what the client said on the call."""
    skill = str(skill or "").strip()
    axis = str(axis or "").strip()
    if not skill or not axis:
        raise ValueError("record_how_they_like_it needs a skill and an axis")
    _refuse_unreadable(skill, axis, value)
    # fix-round review (PO item 1): an axis brief_settings owns is written the
    # way every other stated brief setting is - BOTH surfaces, one batch, an
    # `undo` - never one store on its own.
    try:
        from brief_settings import AXES, SURFACES, apply_settings
    except Exception:  # pragma: no cover
        AXES, SURFACES, apply_settings = {}, (), None
    if skill in SURFACES and axis in AXES and apply_settings is not None:
        got = apply_settings(workspace_root, {axis: value},
                             triggered_by="onboarding", origin="asked")
        return {"skill": skill, "axis": axis, "value": value,
                "date": _today(today), "surfaces": got.get("surfaces"),
                "batch_id": got.get("batch_id")}
    cfg = _existing_config(workspace_root, skill)
    cfg[axis] = value
    save_skill_config(workspace_root, skill, cfg, origin="asked")
    return {"skill": skill, "axis": axis, "value": value, "date": _today(today)}


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------

def record_boundaries(workspace_root, boundaries: Iterable[str], *,
                      today: Optional[str] = None) -> dict:
    """Record the boundaries the client named on the call. Writes the same
    `profile` skill-config keys `profile.py` (PROFILE1) reads for the
    Boundaries section — `boundaries` (a list) and `boundaries_set_at` (this
    call's date, held fixed so `turn off coaching` can restore the config
    byte-for-byte later without moving this date)."""
    items: List[str] = [str(b).strip() for b in (boundaries or ()) if str(b).strip()]
    if not items:
        raise ValueError("record_boundaries needs at least one boundary")
    day = _today(today)
    cfg = _existing_config(workspace_root, PROFILE_SKILL_KEY)
    cfg[BOUNDARIES_KEY] = items
    cfg.setdefault(BOUNDARIES_SET_AT_KEY, day)
    save_skill_config(workspace_root, PROFILE_SKILL_KEY, cfg, origin="asked")
    return {"boundaries": items, "date": cfg[BOUNDARIES_SET_AT_KEY]}


# ---------------------------------------------------------------------------
# The assessment door — entered by the operator, never offered by the product
# ---------------------------------------------------------------------------

def record_assessment_from_session(workspace_root, instrument: str,
                                   scores: dict, consent_date: str, *,
                                   source: str = "entered"):
    """Thin pass-through to `profile.record_assessment` (PROFILE1). Imported
    lazily, inside the call, so this module can be imported on this branch
    before the trial merge without failing — only CALLING this function
    needs `profile.py` on `sys.path` (present after the merge; a caller on
    this branch alone should not reach this function outside the merged
    tree — see BUILD_ONBOARD2's record)."""
    import profile as _profile  # PROFILE1's module — see docstring above
    return _profile.record_assessment(workspace_root, instrument, scores,
                                      consent_date, source=source)


# ---------------------------------------------------------------------------
# The no-question fence — proves the product never asks the client
# ---------------------------------------------------------------------------

# Phrases that would mean the PRODUCT is asking the client something this
# module's callers must only ever hear from the operator's own account of
# the call. Shares its intent with `profile.py`'s ASSESSMENT_OFFER_PHRASES
# (PROFILE1's own fence for the assessment door specifically) but is
# broader: it also covers this lane's "how you think" / "boundaries" doors,
# which PROFILE1's scan does not cover.
NO_QUESTION_BANNED_PHRASES = (
    "take the cvi", "take a cvi", "take the culture index",
    "take an assessment", "take the assessment",
    "want to take the assessment", "shall i send you the assessment",
    "what are your boundaries", "what workstreams do you run",
    "tell me your workstreams", "how do you like it",
    "what's off limits for you", "what is off limits for you",
)


def extract_section(text: str, heading: str) -> str:
    """The body of one markdown section (from `heading` up to the next
    heading of the same or shallower level, or end of file)."""
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading.strip():
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for i in range(start, len(lines)):
        stripped = lines[i]
        if stripped.startswith("#"):
            this_level = len(stripped) - len(stripped.lstrip("#"))
            if this_level <= level:
                end = i
                break
    return "\n".join(lines[start:end])


def scan_no_client_question(section_text: str) -> List[str]:
    """Lines in an OPERATOR-only section that would read as the PRODUCT
    asking the client something. A line is flagged when it contains a
    banned solicitation phrase and the word "operator" does not appear
    anywhere on that same line (this file's own established convention —
    an OPERATOR-marked line is a stage direction for the human running the
    call, never rendered to the customer; see the SKILL.md "No-operator
    rule"). Expected empty over the Phase 6a2 section. Remove the OPERATOR
    marker from a banned line and this goes non-empty — the fence's own
    removal proof."""
    hits: List[str] = []
    for raw in (section_text or "").splitlines():
        line = raw.strip()
        low = line.lower()
        if not any(p in low for p in NO_QUESTION_BANNED_PHRASES):
            continue
        if "operator" in low:
            continue
        hits.append(line)
    return hits


# ---------------------------------------------------------------------------
# ONBOARDGUARD1 2.1 (2026-09-17, M's ruling R-19) — the route guard, in code
# ---------------------------------------------------------------------------
# WHAT THIS REPLACES. `skills/command-room-onboarding/SKILL.md` Phase 0a was
# five prose routes that the model applied by reading. Route 1 ("already on
# latest") required orgs to carry a `scope` field and route 3 ("legacy JSON")
# required a legacy `type` field, so a real book whose orgs carry NEITHER
# satisfied neither rule, fell through to route 4 and was classed FRESH. A
# real book of tens of thousands of events and dozens of companies can be
# exactly that shape, and a seat that followed the rule would have seeded a
# new workspace over the top of it. The 2026-09-16 walk routed to "already
# set up" on the model's judgement rather than on the rule; M ruled that the
# judgement is not the guard.
#
# THE RULE, IN ORDER (the docstring on `workspace_route` restates it):
#   1. NEVER FRESH. A ledger with more than FRESH_MAX_EVENTS events, an
#      entities file with more than FRESH_MAX_ORGS companies, OR a ledger or
#      registry that EXISTS AND WILL NOT READ, is a book. Whatever any field
#      says. `scope` is NEVER load-bearing for freshness: a missing or
#      malformed `scope` is a data-quality fact (see `scope_hygiene`), never
#      evidence that a book does not exist.
#   2. The legacy probes run next, so a v1.x / v2.x install — which is
#      precisely a workspace WITH data in it — still gets its migration
#      instruction instead of being told it is already set up.
#   3. The checkpoint reads run after those, and only decide between
#      "already set up" and "resume".
#   4. `restart onboarding` is REFUSED on a never-fresh workspace, in code.
#      It archives the ledger and reseeds with no merge and no undo.
#
# FAIL CLOSED (fix round 1, review finding B1). The first cut swallowed every
# read failure and returned zero events / no companies, which is the ONE way
# into the fresh branch — so a ledger locked mid-sync, a cloud placeholder
# that has not downloaded, or a half-written registry read as "empty folder"
# and a restart on it was not refused. "Absent" and "present and will not
# read" are now different facts: `_ledger_probe` and `_registry_probe` report
# the second, it populates the workspace on its own, and the customer is told
# the honest thing ("I can't read what's here") rather than "you're all set
# up". A guard that cannot see the book assumes there is one.
#
# THE THRESHOLDS (coordinator ruling, fix round 1, inside M's "keys on a
# populated book" default; review finding M1). The planner's recommendation
# was 50 events / 5 companies. Measured on this tree, everything a fresh
# install writes up to and including Phase 0 is the `plugin_install`
# baseline, the `step_1_setup_v7` fire-marker, the Phase-0
# `onboarding_checkpoint` and the handful of first-run config rows the four
# widget answers produce — under ten events, and ZERO companies (the company
# seed is Phase 1's). The window between that and 50/5 was a class of real
# but small books that would auto-fire a seeding: 40 events and 3 companies
# routed `fresh`. So the thresholds now sit just above the measured seed:
# MORE THAN 20 events, or AT LEAST ONE company, and the workspace is never
# fresh. The comparison stays STRICT (`>`), so exactly 20 events with no
# companies is still fresh and 21 is not; with FRESH_MAX_ORGS at 0 that
# reads as "one company is a book".
#
# COST. One full pass over the ledger through `events_io` (the mandated
# reader) counts the events and finds the newest checkpoint in the same
# sweep. On a book of tens of thousands of events that is well under a
# second, and this runs once, at the top of an onboarding fire.

FRESH_MAX_EVENTS = 20
FRESH_MAX_ORGS = 0

# What makes a registry LEGACY rather than merely untidy (fix round 2, review
# finding N-1). A v2.0 / v2.1 registry types every company with the legacy
# field; a modern registry with a few stray `type` keys and a lot of junk
# company types is not that, and must not be sent through a migration it does
# not need. Both floors have to hold: essentially all companies carry the
# legacy field, and there are enough companies for that to mean something.
LEGACY_JSON_MIN_ORGS = 5
LEGACY_JSON_SHARE = 0.9

ROUTE_ALREADY_SET_UP = "already_set_up"
ROUTE_LEGACY_TRACKER = "legacy_tracker"
ROUTE_LEGACY_JSON = "legacy_json"
ROUTE_FRESH = "fresh"
ROUTE_IN_PROGRESS = "in_progress"
ROUTE_REFUSE_RESTART = "refuse_restart"

# The enum the seed writes (the onboarding SKILL.md's org-scope list).
# Membership is what `scope_hygiene` measures; it is NOT an input to the
# freshness test.
ORG_SCOPES = ("holding", "operating", "division", "brand", "fund", "other")

_LEGACY_INSTRUCTION = (
    "Looks like you've got an earlier version of Command Room here with data "
    "already in it. Setup won't move that over for you — say `update my "
    "command room` and I'll bring your existing stuff into the new format "
    "first. Once that's done, we can run setup on top of it."
)

# One sentence per route, composed here so the skill prints what the guard
# returns instead of improvising a sentence beside it. The two resume routes
# carry no sentence: `fresh` proceeds silently into the intro line, and
# `in_progress` branches on age and phase in the SKILL.md, which is prose this
# lane did not touch (that branch is unchanged and unruled).
ROUTE_SENTENCES = {
    ROUTE_ALREADY_SET_UP: (
        "Your Command Room is all set up already. Say **'new project [Name]'** "
        "to add a project, **'scan my files'** to take another look at your "
        "tools, or **'what version am I on'** if you want a quick health check."
    ),
    ROUTE_LEGACY_TRACKER: _LEGACY_INSTRUCTION,
    ROUTE_LEGACY_JSON: _LEGACY_INSTRUCTION,
    ROUTE_FRESH: "",
    ROUTE_IN_PROGRESS: "",
    ROUTE_REFUSE_RESTART: (
        "There's already a working Command Room in this folder, so I'm not "
        "going to restart setup — that puts everything on file into the "
        "archive and starts a new one over the top, with nothing carried "
        "across and no way to undo it. Say `update my command room` and I'll "
        "bring this one up to the latest build instead."
    ),
}

# B1 — what the customer is told when the guard could not read the workspace.
# Honest, and it still refuses to act: the route is `already_set_up` (nothing
# is seeded, nothing is archived) but the sentence does not claim setup is
# finished, because the guard does not know that.
UNREADABLE_SAY = (
    "I can't read what's in this folder right now — the files are there but "
    "they won't open, which usually means they're still syncing — so I'm not "
    "going to set anything up on top of them. Give it a minute and try again."
)
UNREADABLE_RESTART_SAY = (
    "I can't read what's already in this folder — the files are there but they "
    "won't open — so I'm certainly not going to archive it and start over. "
    "Give it a minute and try again."
)


def _scan_ledger(workspace_root):
    """ONE pass over the full history through `events_io` (the mandated
    reader): the event count and the newest `onboarding_checkpoint`.

    Best-effort in the sense that it never raises — but NOT in the sense that
    a failure is invisible. `events_io._iter_file` deliberately swallows an
    OSError on a whole file (FS-15) so a reader still gets a degraded view,
    which means a locked or undownloaded ledger yields zero events with no
    exception at all. Zero events is the ONLY way into the fresh branch, so
    this function does not get to shrug: `_ledger_probe` below reports
    "exists and will not read" separately, and `workspace_route` treats that
    as a populated workspace."""
    n_events = 0
    n_checkpoints = 0
    checkpoint = None
    try:
        from events_io import iter_events
    except Exception:  # pragma: no cover — events_io is always in tree
        return n_events, n_checkpoints, checkpoint
    try:
        for ev in iter_events(workspace_root):
            n_events += 1
            if ev.get("type") == "onboarding_checkpoint":
                n_checkpoints += 1
                checkpoint = ev
    except Exception:
        pass
    return n_events, n_checkpoints, checkpoint


def _ledger_probe(workspace_root, n_events: int) -> Dict[str, bool]:
    """Is there a ledger, and did it actually read? (B1.)

    `{"present", "unreadable"}`. Unreadable means one of: the shard resolver
    itself failed; a ledger file exists and cannot be opened (a lock, a
    directory in its place, a cloud placeholder); or a non-empty ledger file
    yielded zero usable events. Each of those is a book the guard cannot
    see — never an empty folder."""
    try:
        from events_io import active_path, shard_paths
    except Exception:  # pragma: no cover
        return {"present": False, "unreadable": True}
    paths = []
    try:
        paths = [Path(x) for x in shard_paths(workspace_root)]
    except Exception:
        return {"present": True, "unreadable": True}
    try:
        ap = Path(active_path(workspace_root))
        if ap.exists() and ap not in paths:
            paths.append(ap)
    except Exception:
        return {"present": True, "unreadable": True}
    if not paths:
        return {"present": False, "unreadable": False}
    unreadable = False
    any_bytes = False
    for f in paths:
        try:
            with open(f, "rb") as fh:
                chunk = fh.read(1)
            if chunk:
                any_bytes = True
        except OSError:
            unreadable = True
    if any_bytes and n_events == 0:
        # Bytes on disk that produced no event at all: a half-written or
        # mangled ledger, not an empty one.
        unreadable = True
    return {"present": True, "unreadable": unreadable}


def _registry_probe(workspace_root) -> Dict[str, bool]:
    """Is there a companies registry, and did it actually parse? (B1.)

    `connector_config.load_entities` returns `{}` on any failure, so "the
    file is absent" and "the file is there and will not parse" arrive at this
    guard looking identical. They are not the same fact and this tells them
    apart."""
    f = Path(workspace_root) / "_hq" / "data" / "entities.json"
    try:
        if not f.exists():
            return {"present": False, "unreadable": False}
    except OSError:
        return {"present": True, "unreadable": True}
    try:
        raw = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"present": True, "unreadable": True}
    if not raw.strip():
        return {"present": True, "unreadable": True}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"present": True, "unreadable": True}
    return {"present": True, "unreadable": not isinstance(data, dict)}


def _orgs(workspace_root) -> List[Dict[str, Any]]:
    """The orgs collection, through `entities_io` so reader and writer agree
    on the nested/flat shape. [] on any failure — and a failure here is NOT
    silent, because `_registry_probe` reports it to the route."""
    try:
        from connector_config import load_entities
        from entities_io import entities_collection
    except Exception:  # pragma: no cover
        return []
    data = load_entities(workspace_root)
    if not isinstance(data, dict) or not data:
        return []
    try:
        coll = entities_collection(data, "orgs")
    except Exception:
        return []
    return [o for o in coll if isinstance(o, dict)]


def _route_result(route: str, why: str, signals: Dict[str, Any],
                  say: Optional[str] = None) -> Dict[str, Any]:
    return {
        "route": route,
        "why": why,
        "signals": signals,
        # `say` is overridden only where the route's standing sentence would
        # claim something the guard does not know — the unreadable case must
        # not tell a customer "you're all set up" when it could not look.
        "say": say if say is not None else ROUTE_SENTENCES.get(route, ""),
        # Phase 0b: "fresh" is the ONE auto-fire signal. Every other route
        # stops or resumes on the customer's word, so this is False on all of
        # them and the suite pins it route by route.
        "auto_fire": route == ROUTE_FRESH,
    }


def workspace_route(workspace_root, *, restart_requested: bool = False) -> Dict[str, Any]:
    """Which onboarding route this mounted folder takes, decided in code.

    Returns `{"route", "why", "signals", "say", "auto_fire"}`. `route` is one
    of the six ROUTE_* constants; `say` is the one sentence the skill prints
    (empty for `fresh` and `in_progress`); `auto_fire` is True only on
    `fresh`.

    Pass `restart_requested=True` when the customer typed the explicit
    `restart onboarding` phrase: on a populated workspace that returns
    `refuse_restart` and the skill stops. On a workspace with nothing in it
    the ordinary route comes back and the restart path is harmless.

    The order is the ruled one — never-fresh first, checkpoints second,
    restart refusal in code rather than in judgement. See the module comment
    above for why the thresholds are what they are.
    """
    root = Path(workspace_root)
    hq = root / "_hq"
    entities_path = hq / "data" / "entities.json"
    tracker_path = hq / "MASTER_TRACKER.md"

    n_events, n_checkpoints, checkpoint = _scan_ledger(root)
    ledger = _ledger_probe(root, n_events)
    registry = _registry_probe(root)
    orgs = _orgs(root)
    n_orgs = len(orgs)
    n_valid_scope = sum(1 for o in orgs if o.get("scope") in ORG_SCOPES)
    n_legacy_type = sum(1 for o in orgs
                        if o.get("type") and o.get("scope") not in ORG_SCOPES)

    cp = checkpoint if isinstance(checkpoint, dict) else {}
    status = cp.get("status")
    # B1 — a workspace the guard cannot READ is populated. Fail closed.
    unreadable = bool(ledger["unreadable"] or registry["unreadable"])
    populated = (n_events > FRESH_MAX_EVENTS
                 or n_orgs > FRESH_MAX_ORGS
                 or unreadable)

    signals: Dict[str, Any] = {
        "hq_exists": hq.is_dir(),
        "entities_exists": entities_path.is_file(),
        "master_tracker_exists": tracker_path.is_file(),
        "n_events": n_events,
        "n_orgs": n_orgs,
        "n_orgs_valid_scope": n_valid_scope,
        "n_orgs_legacy_type": n_legacy_type,
        "n_checkpoints": n_checkpoints,
        "checkpoint_status": status,
        "checkpoint_phase": cp.get("phase"),
        "checkpoint_ts": cp.get("ts") or cp.get("timestamp"),
        "populated": populated,
        "ledger_present": ledger["present"],
        "ledger_unreadable": ledger["unreadable"],
        "registry_present": registry["present"],
        "registry_unreadable": registry["unreadable"],
        "unreadable": unreadable,
        "fresh_max_events": FRESH_MAX_EVENTS,
        "fresh_max_orgs": FRESH_MAX_ORGS,
        "restart_requested": bool(restart_requested),
    }

    if unreadable:
        which = " and ".join(
            [w for w, bad in (("activity log", ledger["unreadable"]),
                              ("companies registry", registry["unreadable"])) if bad])
        unreadable_why = (f"the {which} is on disk and will not read — a "
                          f"workspace this guard cannot see is assumed to have a "
                          f"book in it, never assumed empty")
    else:
        unreadable_why = ""

    # The two legacy shapes, computed once and used in both branches (H2).
    # An unreadable registry yields no legacy evidence, so neither can fire
    # on it and the workspace falls through to the fail-closed answer.
    #
    # N-1 (fix round 2). `is_legacy_json` used to fire on ANY company carrying
    # a `type` key so long as no company carried a valid scope — which, once
    # the legacy probes moved ahead of "already set up" (H2), meant a MODERN
    # book with junk company types and a few stray `type` keys was told to run
    # the migration it does not need. The original prose meant a v2.0 / v2.1
    # registry, where the legacy field is how EVERY company is typed, so the
    # predicate now says that: essentially all companies carry it, and there
    # are enough companies for "essentially all" to mean anything. A registry
    # below the floor is not called legacy on this evidence; it stops as a
    # book instead, which is safe and loses nothing.
    is_legacy_tracker = tracker_path.is_file() and not entities_path.is_file()
    is_legacy_json = bool(entities_path.is_file()
                          and not n_valid_scope
                          and n_orgs >= LEGACY_JSON_MIN_ORGS
                          and n_legacy_type >= LEGACY_JSON_SHARE * n_orgs)

    # 1 — NEVER FRESH, and the restart refusal, which outranks everything
    #     else because it is the only irreversible act on this path.
    if populated and restart_requested:
        return _route_result(
            ROUTE_REFUSE_RESTART,
            unreadable_why or
            (f"restart refused: {n_events} events and {n_orgs} companies on "
             f"file — a restart archives all of it with no merge and no undo"),
            signals,
            say=UNREADABLE_RESTART_SAY if unreadable else None)

    # 2 — the legacy shapes, BEFORE "already set up" (H2). A v1.x / v2.x
    #     install is precisely a workspace with data in it; telling it "you're
    #     all set up" is how a customer never migrates. Neither route ever
    #     says fresh, so running them here cannot re-open the door rule 1
    #     just closed.
    if is_legacy_tracker:
        return _route_result(ROUTE_LEGACY_TRACKER,
                             "an older Command Room's tracker with no registry",
                             signals)
    if is_legacy_json:
        return _route_result(ROUTE_LEGACY_JSON,
                             f"{n_legacy_type} companies carry the legacy kind "
                             f"field and none carry a type",
                             signals)

    # 3 — UNREADABLE OUTRANKS THE CHECKPOINT (fix round 2, review finding
    #     N-2). A half-finished setup on record used to win here, so a
    #     workspace the guard had just admitted it could not read would be
    #     resumed — and at phases 0-2 resuming means seeding substrate into
    #     it. Nothing was archived and the restart was still refused, so this
    #     was never the B1 harm; it was a WRITE the same fail-closed rule
    #     should have stopped. A guard that cannot see the book does not
    #     resume a seed into it: it stops, with the honest sentence, and the
    #     customer can try again once the folder settles. This sits AFTER the
    #     legacy probes deliberately — those read evidence that is still
    #     legible (a tracker file, a typed registry) and their instruction is
    #     to stop and migrate, which is the safe answer either way.
    if unreadable:
        return _route_result(ROUTE_ALREADY_SET_UP, unreadable_why, signals,
                             say=UNREADABLE_SAY)

    # 4 — the checkpoint only chooses between resume and set-up.
    if status == "in_progress":
        return _route_result(
            ROUTE_IN_PROGRESS,
            f"a setup checkpoint that never finished, over {n_events} events "
            f"— resume, never restart",
            signals)

    if populated:
        return _route_result(
            ROUTE_ALREADY_SET_UP,
            unreadable_why or
            (f"{n_events} events and {n_orgs} companies on file — this "
             f"workspace has a book in it, whatever any field says "
             f"({n_valid_scope} of {n_orgs} companies carry a type)"),
            signals,
            say=UNREADABLE_SAY if unreadable else None)

    if status == "complete":
        return _route_result(ROUTE_ALREADY_SET_UP,
                             "setup finished and nothing has been added since",
                             signals)
    return _route_result(
        ROUTE_FRESH,
        f"nothing on file: {n_events} events, {n_orgs} companies, no setup "
        f"checkpoint",
        signals)


# ---------------------------------------------------------------------------
# ONBOARDGUARD1 2.3 — the scope vocabulary, as a note and never a write
# ---------------------------------------------------------------------------
# On a real book most companies can carry a `scope` that is missing, null,
# a description, or a relationship type. That is worth reporting and it is
# NOT worth writing: this lane writes nothing to
# entities.json on any path. Where the note goes is settled by M's 2026-09-07
# ruling and its extension — the condition of the plumbing is reported by the
# MAINTENANCE RUN and never on the brief, End of Day or the wrap. The
# `examples` carry company names, so the maintenance composer gates them;
# `scope_hygiene_line` deliberately carries NO name, so the line itself is
# safe on the one surface that is allowed to have it at all.

SCOPE_HYGIENE_SURFACE = "maintenance"


def scope_hygiene(workspace_root, *, max_examples: int = 5) -> Dict[str, Any]:
    """A data-hygiene READ over the orgs registry. Writes nothing, ever.

    Returns `{"n_orgs", "n_valid_scope", "n_invalid", "examples"}`, where each
    example is `{"name", "scope"}` for one org whose `scope` is not a member
    of ORG_SCOPES. The caller gates the names."""
    orgs = _orgs(workspace_root)
    invalid = [o for o in orgs if o.get("scope") not in ORG_SCOPES]
    examples = [{"name": o.get("name") or o.get("id") or "", "scope": o.get("scope")}
                for o in invalid[:max(0, int(max_examples))]]
    return {
        "n_orgs": len(orgs),
        "n_valid_scope": len(orgs) - len(invalid),
        "n_invalid": len(invalid),
        "examples": examples,
    }


def scope_hygiene_line(workspace_root, *, note: Optional[Dict[str, Any]] = None) -> str:
    """ONE plain-English line for the maintenance run's "worth a glance"
    tier, or "" when there is nothing to report. Carries no company name, no
    path and no field name — it is a sentence, not a diagnosis."""
    n = note if isinstance(note, dict) else scope_hygiene(workspace_root)
    bad = int(n.get("n_invalid") or 0)
    total = int(n.get("n_orgs") or 0)
    if bad <= 0 or total <= 0:
        return ""
    return (f"{bad} of the {total} companies on file don't have a company type "
            f"set (holding, operating, division, brand, fund). Nothing is "
            f"broken — anything that groups companies by type just skips them.")


# ---------------------------------------------------------------------------
# ONBOARDGUARD1 2.2 (2026-09-17) — the setup widget names the seat's own mail
# ---------------------------------------------------------------------------
# The Phase-0 widget and its spoken line said "Gmail" and "Auto-queue to Gmail
# Drafts" on every seat, including one whose declared mail backend is
# something else entirely. The reader already existed —
# `connector_config.declared_backend("email", ws)` returns the declared row's
# `{server_id, provider, label}` — and nothing consulted it here.
#
# THE RENDER PATH, CONFIRMED BEFORE BUILDING. The widget is NOT served: the
# skill reads `references/step1_widget_v2.html` and hands the markup to
# `show_widget` (the documented renderer bypass, pinned by the widget-transport
# guard). "Read the file verbatim" is therefore the substitution point, and it
# is the one place a literal can hide from every reader in the tree. So the
# reference file now carries three tokens and this module is what fills them;
# the SKILL.md instructs a call instead of a raw read, which is what makes the
# copy a behaviour rather than a string.
#
# UNDECLARED IS NOT A GUESS. With no declared backend the copy says "your
# mail" and names no provider at all — it never falls back to the last
# provider anyone happened to mention.

MAIL_LABEL_FALLBACK = "your mail"
MAIL_LABEL_TOKEN = "{{MAIL_LABEL}}"
MAIL_DRAFTS_TOKEN = "{{MAIL_DRAFTS}}"
MAIL_QUEUE_CHIP_TOKEN = "{{MAIL_QUEUE_CHIP}}"
MAIL_TOKENS = (MAIL_LABEL_TOKEN, MAIL_DRAFTS_TOKEN, MAIL_QUEUE_CHIP_TOKEN)

SETUP_WIDGET_REL = ("skills", "command-room-onboarding", "references",
                    "step1_widget_v2.html")


def _plugin_root(plugin_root=None) -> Path:
    """This file lives at <plugin>/shared/scripts/, so the plugin root is two
    parents up. An explicit `plugin_root` wins (the suites pass a copy)."""
    if plugin_root:
        return Path(plugin_root)
    return Path(__file__).resolve().parent.parent.parent


def mail_copy(workspace_root=None, entities: Optional[dict] = None) -> Dict[str, Any]:
    """The mail-name copy for the setup widget and its spoken line.

    `{"label", "drafts", "queue_chip", "declared"}` — read from the declared
    email backend's own `label`. When nothing is declared, or the declared row
    carries no label: "your mail", a draft phrase that names nobody, and
    `declared: False`.

    L1 (fix round 1): the first cut fell back to the row's `provider` when
    there was no `label`, which is an internal slug — a legitimate row with a
    `server_id` and `provider: "gmail"` and no label put lower-case "gmail" in
    the first sentence a customer ever reads. A slug is not a display name, so
    a label-less row is treated as undeclared."""
    label = None
    try:
        from connector_config import declared_backend
        row = declared_backend("email", workspace_root, entities)
        if isinstance(row, dict):
            val = row.get("label")
            if isinstance(val, str) and val.strip():
                label = val.strip()
    except Exception:
        label = None
    if label:
        return {"label": label,
                "drafts": f"your {label} drafts",
                "queue_chip": f"Auto-queue to {label} Drafts",
                "declared": True}
    return {"label": MAIL_LABEL_FALLBACK,
            "drafts": "your drafts",
            "queue_chip": "Auto-queue to my drafts",
            "declared": False}


def setup_widget_html(workspace_root=None, *, plugin_root=None,
                      entities: Optional[dict] = None,
                      html: Optional[str] = None) -> str:
    """The Phase-0 setup widget's markup with the mail-name tokens filled.

    Read the reference file (or take `html` directly, which is how the suite
    feeds a mutant), substitute, and refuse to return markup that still
    carries a token — an unsubstituted token on a customer's screen is worse
    than the literal it replaced."""
    if html is None:
        html = _plugin_root(plugin_root).joinpath(*SETUP_WIDGET_REL).read_text(
            encoding="utf-8")
    copy = mail_copy(workspace_root, entities)
    out = (html.replace(MAIL_LABEL_TOKEN, copy["label"])
               .replace(MAIL_DRAFTS_TOKEN, copy["drafts"])
               .replace(MAIL_QUEUE_CHIP_TOKEN, copy["queue_chip"]))
    left = [t for t in MAIL_TOKENS if t in out]
    if left:
        raise ValueError("setup widget still carries unsubstituted copy tokens: "
                         + ", ".join(left))
    return out


def draft_posture_line(workspace_root=None, entities: Optional[dict] = None) -> str:
    """The Q4 spoken line, composed from the same copy the widget renders, so
    the two can never disagree about which mailbox the customer has."""
    copy = mail_copy(workspace_root, entities)
    return ("How should I handle email drafts? When you ask me to write an "
            f"email, I can show you the draft first and only touch {copy['label']} "
            f"when you click — or drop every draft straight into {copy['drafts']}. "
            "You can change this anytime by saying 'tune email-writer.'")


# ---------------------------------------------------------------------------
# The setup card's typed sentence, routed (MF-5321-2, REVIEW_WIDGETSEND1
# RE-VERIFY 2 R1)
# ---------------------------------------------------------------------------
# When the Phase-0 card cannot reach the chat, it hands the customer a line to
# paste. That line used to be the wire tuple (`1 run-company single-op-co, 2
# eastern, ...`) — unreadable, and nobody would type it. It is now the four
# questions with the customer's OWN answers in the words the card printed
# back:
#
#   Your role: Run a company → Single operating company; Timezone: Pacific;
#   AI name: Keep Penelope; Email drafts: Show me first
#
# Readable — and, until this function existed, UNROUTED: the skill's reply
# handler dispatches on the wire enums (`run-company` / `pacific`), and the
# labels above appear nowhere in SKILL.md. Pasting the sentence left the
# mapping to the model's inference on a first-run seat, which is the one seat
# the dead-click bug was reported from.
#
# So the label → enum table lives HERE, in the module the onboarding skill
# already imports to compose that card (`setup_widget_html`), and SKILL.md
# relays it. One function turns the sentence into the SAME tuples the widget's
# wire carries, and the skill dispatches those through its existing Items.
SETUP_QUESTION_LABELS = {1: "Your role", 2: "Timezone", 3: "AI name",
                         4: "Email drafts"}

# Q4's auto-queue chip is named after the seat's own mailbox ("Auto-queue to
# my drafts", "Auto-queue to Stone Mail Drafts" — `mail_copy`), so it is the
# one answer matched on a prefix rather than a fixed string.
SETUP_QUEUE_CHIP_PREFIX = "Auto-queue"

SETUP_CHIP_LABELS: Dict[int, Dict[str, str]] = {
    1: {"Run a company": "run-company",
        "Senior leader (not owner)": "senior-leader",
        "Investor / board / advisor": "investor",
        "Client work / service": "client-work",
        "Nonprofit": "nonprofit",
        "Other": "other"},
    2: {"Pacific": "pacific",
        "Mountain": "mountain",
        "Central": "central",
        "Eastern": "eastern",
        "Other": "other"},
    3: {"Keep Penelope": "default",
        "Pick my own": "custom"},
    4: {"Show me first": "show_first"},
}

SETUP_SUB_CHIP_LABELS: Dict[str, Dict[str, str]] = {
    "run-company": {"Single operating company": "single-op-co",
                    "Multiple companies (holdco)": "holdco",
                    "Family business": "family-business",
                    "Other": "other"},
    "senior-leader": {"C-suite": "c-suite",
                      "VP / SVP": "vp",
                      "Director or below": "director-below",
                      "Other": "other"},
    "investor": {"GP / fund partner": "gp-fund",
                 "Board director": "board-director",
                 "Independent advisor": "independent-advisor",
                 "Family office principal": "family-office",
                 "Other": "other"},
    "client-work": {"Consulting": "consulting",
                    "Agency": "agency",
                    "Professional services": "professional-services",
                    "Other": "other"},
    "nonprofit": {"Executive director": "exec-director",
                  "Senior staff": "senior-staff",
                  "Board member": "board-member",
                  "Other": "other"},
}

SETUP_SUB_ARROW = "→"   # the card's own ` → ` between chip and sub-chip
SETUP_FREETEXT_DASH = "—"  # the card's own ` — ` before typed detail

_SETUP_QUESTION_BY_LABEL = {v: k for k, v in SETUP_QUESTION_LABELS.items()}
_SETUP_SPLIT = re.compile(
    "(" + "|".join(re.escape(v) for v in SETUP_QUESTION_LABELS.values())
    + r")\s*:\s*")


def parse_setup_sentence(text: str) -> List[Dict[str, Any]]:
    """Turn the setup card's pasted sentence into the widget's own tuples.

    In: the line the Finish click hands over when no stage takes it —
    `Your role: Run a company → Single operating company; Timezone: Pacific;
    AI name: Keep Penelope; Email drafts: Show me first`.

    Out: `[{"n": 1, "action": "run-company", "sub": "single-op-co"},
           {"n": 2, "action": "pacific"}, {"n": 3, "action": "default"},
           {"n": 4, "action": "show_first"}]` — the SAME four tuples
    `apply choices:` would have delivered, in the same shape, so the skill's
    Items 1-5 dispatch them without knowing which door they came through.

    Where the customer refined an answer, the typed detail rides as `input`,
    exactly as the wire carries it (`Timezone: Other — America/Los_Angeles`
    -> `{"n": 2, "action": "other", "input": "America/Los_Angeles"}`). The
    card truncates a long refinement to 38 characters and an ellipsis in its
    own summary, so a pasted `input` can be the SHORTENED text — it is the
    customer's words either way, and the ellipsis is visible evidence it was
    cut.

    The questions are found by their own labels rather than by splitting on
    "; ", so a refinement that itself contains a semicolon cannot invent a
    fifth answer.

    Refuses rather than guesses. A label this card never printed, a missing
    question, or a repeated one raises `ValueError` naming exactly what could
    not be read — NOTHING is applied on a refusal, and the skill asks for the
    answer again instead of inferring it. That is the whole point of the
    table: `Pacific -> America/Los_Angeles` is a route here and was a model
    inference before.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("no setup sentence to read")
    parts = _SETUP_SPLIT.split(text)
    answers: Dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        n = _SETUP_QUESTION_BY_LABEL[parts[i]]
        if n in answers:
            raise ValueError(
                f"the setup sentence names {SETUP_QUESTION_LABELS[n]!r} twice; "
                "nothing was applied — ask which answer is the current one")
        answers[n] = parts[i + 1].strip().rstrip(";").strip()
    missing = [SETUP_QUESTION_LABELS[n] for n in sorted(SETUP_QUESTION_LABELS)
               if n not in answers or not answers[n]]
    if missing:
        raise ValueError(
            "the setup sentence is missing an answer for "
            + ", ".join(repr(m) for m in missing)
            + " — nothing was applied; ask for the missing answer rather "
              "than assuming a default")

    tuples: List[Dict[str, Any]] = []
    for n in sorted(SETUP_QUESTION_LABELS):
        chips, _, free = answers[n].partition(" " + SETUP_FREETEXT_DASH + " ")
        top_label, _, sub_label = chips.strip().partition(
            " " + SETUP_SUB_ARROW + " ")
        top_label, sub_label = top_label.strip(), sub_label.strip()
        action = SETUP_CHIP_LABELS[n].get(top_label)
        if action is None and n == 4 and top_label.startswith(
                SETUP_QUEUE_CHIP_PREFIX):
            action = "auto_queue"
        if action is None:
            raise ValueError(
                f"the setup sentence's {SETUP_QUESTION_LABELS[n]!r} answer "
                f"reads {top_label!r}, which is not a chip this card prints; "
                "nothing was applied — ask the question again rather than "
                "guessing which answer was meant")
        row: Dict[str, Any] = {"n": n, "action": action}
        if sub_label:
            sub = SETUP_SUB_CHIP_LABELS.get(action, {}).get(sub_label)
            if sub is None:
                raise ValueError(
                    f"the setup sentence refines {SETUP_QUESTION_LABELS[n]!r} "
                    f"with {sub_label!r}, which is not a sub-chip this card "
                    f"prints under {top_label!r}; nothing was applied")
            row["sub"] = sub
        if free.strip():
            row["input"] = free.strip()
        tuples.append(row)
    return tuples


__all__ = [
    "THINK_SKILL", "THINK_AXIS", "LIKE_DEFAULT_SKILL", "LIKE_DEFAULT_AXIS",
    "PROFILE_SKILL_KEY", "BOUNDARIES_KEY", "BOUNDARIES_SET_AT_KEY",
    "record_how_they_think", "record_how_they_like_it", "record_boundaries",
    "record_assessment_from_session",
    "NO_QUESTION_BANNED_PHRASES", "extract_section", "scan_no_client_question",
    "FRESH_MAX_EVENTS", "FRESH_MAX_ORGS", "ORG_SCOPES", "ROUTE_SENTENCES",
    "ROUTE_ALREADY_SET_UP", "ROUTE_LEGACY_TRACKER", "ROUTE_LEGACY_JSON",
    "ROUTE_FRESH", "ROUTE_IN_PROGRESS", "ROUTE_REFUSE_RESTART",
    "workspace_route", "SCOPE_HYGIENE_SURFACE", "scope_hygiene",
    "scope_hygiene_line",
    "MAIL_LABEL_FALLBACK", "MAIL_TOKENS", "SETUP_WIDGET_REL", "mail_copy",
    "setup_widget_html", "draft_posture_line",
    "SETUP_QUESTION_LABELS", "SETUP_CHIP_LABELS", "SETUP_SUB_CHIP_LABELS",
    "SETUP_QUEUE_CHIP_PREFIX", "parse_setup_sentence",
]
