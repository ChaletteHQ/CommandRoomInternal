#!/usr/bin/env python3
"""The two coaching doors, and the one that closes them again.

SPEC_SURFACES2 PROFILE1; design memo "The coaching surfaces" section 2.

NOTHING IS REQUIRED TO START
----------------------------
Every seat begins OBSERVED: the surfaces report the day, note a pattern and
state the bigger picture, and they ask nothing. That is a complete way to use
the product and it is where every seat stays until somebody opens a door.

    The EARNED door.  The record shows the same pattern three times, the
    Friday wrap says so and offers ONCE. Yes makes it the thing being worked
    on. No — or silence — and that pattern is never raised again. The
    "never again" is written down, because an offer that comes back is a
    nag, and a nag is the thing this design is trying not to be.

    The CHOSEN door.  The seat says "turn on coaching". The product walks a
    SHORT CONVERSATION — at most three questions — and, before it applies
    anything, SHOWS what changes on the morning brief, at End of Day and on
    the Friday wrap. Then it applies, with a receipt.

    "turn off coaching" puts everything back. Not "disables the feature":
    the profile page renders byte-for-byte what it rendered before the walk.

WHERE THE STATE LIVES, AND WHY IT LIVES THERE
---------------------------------------------
The coaching relationship object sits in `_hq/coaching/`, its own
confidentiality tier (`coaching_confidential`): out of every shared artifact,
out of the brief, the wrap and every team surface. The SHAPE alone
(observed / named / coached) sits in the profile settings store, because the
profile page has to be able to say what shape the seat is in without opening
a single note.

Pure stdlib plus the shared writers. Reads nothing from the event lane
directly.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atomic_write import atomic_write_json  # noqa: E402
from coaching_confidential import COACHING_DIR_PARTS  # noqa: E402
from delete_grant import SUFFIX_ARCHIVED, remove_or_move_aside  # noqa: E402

SHAPE_OBSERVED = "observed"
SHAPE_NAMED = "named"
SHAPE_COACHED = "coached"
SHAPES = (SHAPE_OBSERVED, SHAPE_NAMED, SHAPE_COACHED)

# The stance dial (design memo section 5). The default is SITUATIONAL, per
# the ruling M owes and the recommendation in force.
STANCE_ASK_FIRST = "ask_first"
STANCE_TELL_FIRST = "tell_first"
STANCE_SITUATIONAL = "situational"
STANCES = (STANCE_ASK_FIRST, STANCE_TELL_FIRST, STANCE_SITUATIONAL)
STANCE_DEFAULT = STANCE_SITUATIONAL

# The earned door's floor: the same pattern, three times, before the wrap is
# allowed to raise it once.
EARNED_DOOR_FLOOR = 3

RELATIONSHIP_FILE = "relationship.json"
OFFERS_FILE = "offers.json"
UNDO_FILE = "walk_undo.json"

# The walk. THREE questions, and the count is a cap the code enforces, not a
# style note. Every one of them is answerable in a sentence; none of them is
# a form field.
WALK_QUESTIONS: tuple = (
    {"key": "behaviour",
     "ask": "What is the one thing you want to be better at this quarter? "
            "Say it in your own words."},
    {"key": "stance",
     "ask": "When we get into it, do you want questions first, a straight "
            "read first, or whichever fits the moment?"},
    {"key": "boundaries",
     "ask": "Anything off limits, or anyone who should never see this?"},
)
assert len(WALK_QUESTIONS) <= 3, "the walk is capped at three questions"

_STANCE_WORDS = {
    "question": STANCE_ASK_FIRST, "questions": STANCE_ASK_FIRST,
    "ask": STANCE_ASK_FIRST, "ask first": STANCE_ASK_FIRST,
    "read": STANCE_TELL_FIRST, "straight": STANCE_TELL_FIRST,
    "tell": STANCE_TELL_FIRST, "tell first": STANCE_TELL_FIRST,
    "opinion": STANCE_TELL_FIRST,
    "whichever": STANCE_SITUATIONAL, "either": STANCE_SITUATIONAL,
    "both": STANCE_SITUATIONAL, "situational": STANCE_SITUATIONAL,
}


def _dir(workspace_root) -> Path:
    return Path(workspace_root).joinpath(*COACHING_DIR_PARTS)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _today() -> str:
    return _dt.date.today().isoformat()


# --------------------------------------------------------------------------
# The shape
# --------------------------------------------------------------------------

def coaching_shape(workspace_root) -> str:
    """observed / named / coached. Default and fail-to-default: observed.

    A malformed store never promotes a seat into coaching it did not ask
    for — this is the one switch in the product whose safe direction is OFF,
    because the alternative is coaching somebody who never opened a door."""
    try:
        import profile as _profile
        value = _profile.load_profile_config(workspace_root).get(
            _profile.COACHING_LAYER_KEY)
    except Exception:
        return SHAPE_OBSERVED
    return value if value in SHAPES else SHAPE_OBSERVED


def coaching_enabled(workspace_root) -> bool:
    return coaching_shape(workspace_root) != SHAPE_OBSERVED


def relationship(workspace_root) -> dict:
    """The coaching object. Confidential tier — every caller that renders
    from this must declare a coaching surface to the leak scan."""
    return _read_json(_dir(workspace_root) / RELATIONSHIP_FILE)


# --------------------------------------------------------------------------
# The earned door
# --------------------------------------------------------------------------

def _offers(workspace_root) -> dict:
    return _read_json(_dir(workspace_root) / OFFERS_FILE)


def _write_offers(workspace_root, data: dict) -> None:
    d = _dir(workspace_root)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / OFFERS_FILE, data)


def earned_offer_due(workspace_root, pattern_key: str, seen_count: int) -> bool:
    """May the wrap raise this pattern? Once, ever, and only at the floor.

    A pattern already offered — accepted, declined, or met with silence —
    returns False forever. That is the ruling: silence means never again."""
    if int(seen_count or 0) < EARNED_DOOR_FLOOR:
        return False
    return str(pattern_key) not in (_offers(workspace_root).get("offered") or {})


def record_earned_offer(workspace_root, pattern_key: str, *,
                        answer: str = "silence",
                        today: Optional[str] = None) -> dict:
    """Write down that the wrap raised this pattern, and what came back.
    Called whether the answer is yes, no, or nothing at all — the whole
    point is that the row exists either way."""
    data = dict(_offers(workspace_root))
    offered = dict(data.get("offered") or {})
    offered[str(pattern_key)] = {"answer": str(answer),
                                 "date": today or _today()}
    data["offered"] = offered
    _write_offers(workspace_root, data)
    return offered[str(pattern_key)]


def accept_earned_offer(workspace_root, pattern_key: str, behaviour: str, *,
                        today: Optional[str] = None) -> dict:
    """Yes on the wrap's one offer. This moves the seat to NAMED — one thing
    watched, one coaching line, one accountability report. It does not open
    the full relationship; that is the other door."""
    record_earned_offer(workspace_root, pattern_key, answer="yes", today=today)
    return _apply(workspace_root, shape=SHAPE_NAMED,
                  answers={"behaviour": behaviour},
                  door="earned", today=today)


# --------------------------------------------------------------------------
# The chosen door — the walk, the preview, then the apply
# --------------------------------------------------------------------------

def walk_questions() -> List[dict]:
    """At most three, always these, in this order."""
    return [dict(q) for q in WALK_QUESTIONS]


def normalize_stance(answer: Any) -> str:
    text = str(answer or "").strip().lower()
    for word, stance in _STANCE_WORDS.items():
        if word in text:
            return stance
    return STANCE_DEFAULT


def surface_deltas(answers: Optional[dict] = None,
                   *, shape: str = SHAPE_COACHED) -> Dict[str, List[str]]:
    """What changes on each of the three surfaces, in the seat's own terms,
    BEFORE anything is applied. The walk shows this and then asks to keep
    it; nothing is written until it does."""
    answers = answers or {}
    behaviour = str(answers.get("behaviour") or "the thing you named").strip()
    stance = normalize_stance(answers.get("stance"))
    stance_line = {
        STANCE_ASK_FIRST: "I ask before I offer a read",
        STANCE_TELL_FIRST: "I give you my read, then the pushback",
        STANCE_SITUATIONAL: "I ask when you are exploring and tell you when "
                            "the record disagrees with you",
    }[stance]
    brief = [f"One line, stated not asked: if {behaviour} comes up today, "
             "you said you would do it rather than defer it."]
    eod = ["The pattern line you already have, plus one sentence tying the "
           "day to what you are working on."]
    wrap = [f"An accountability report against what you committed to last "
            f"Friday, and where {behaviour} stands in evidence from the week.",
            stance_line]
    if shape == SHAPE_NAMED:
        eod.append("At most one self-scored question a day, out of the five "
                   "the Staff Meeting already budgets. Never an extra one.")
    else:
        eod.append("Up to two self-scored questions a day, out of the five "
                   "the Staff Meeting already budgets. Never an extra one.")
    return {"morning brief": brief, "end of day": eod, "weekly wrap": wrap}


def preview(workspace_root, answers: Optional[dict] = None, *,
            shape: str = SHAPE_COACHED) -> str:
    """The deltas as the block the walk shows before it applies."""
    deltas = surface_deltas(answers, shape=shape)
    out = ["Here is what changes, before I change it:", ""]
    for surface, lines in deltas.items():
        out.append(f"**{surface}**")
        out.extend(f"- {ln}" for ln in lines)
        out.append("")
    out.append("Say keep it and I will. Say no and nothing moves.")
    return "\n".join(out)


def _apply(workspace_root, *, shape: str, answers: dict, door: str,
           today: Optional[str] = None) -> dict:
    """Write the shape and the relationship object, after snapshotting
    enough to put it all back."""
    import profile as _profile
    day = today or _today()
    before = json.loads(json.dumps(
        _profile.load_profile_config(workspace_root)))

    d = _dir(workspace_root)
    d.mkdir(parents=True, exist_ok=True)
    # The snapshot lives in the coaching tier, NOT in the profile settings —
    # so restoring the settings restores them byte-for-byte, with no residue
    # of the walk left inside them.
    #
    # And it is taken ONCE per coaching cycle. What "turn off coaching" has
    # to restore is the page as it read before coaching was EVER applied,
    # not the page as it read before the most recent call. A seat that takes
    # the earned door and then the chosen one has applied twice — that is
    # the designed progression, observed -> named -> coached — and a
    # snapshot overwritten on the second apply would put the seat back to
    # the MIDDLE of it while the receipt said coaching was off.
    shape_before = coaching_shape(workspace_root)
    existing = _read_json(d / UNDO_FILE)
    have_snapshot = isinstance(existing.get("profile_config_before"), dict)
    if shape_before == SHAPE_OBSERVED:
        snapshot: Optional[dict] = before
    elif not have_snapshot:
        # Already in a coaching shape with nothing to go back to (a
        # hand-edited store, or a seat from before this file existed). The
        # honest restore target is this config with everything the walk
        # owns taken back out — never this config as it stands, which would
        # restore the seat straight back into coaching.
        snapshot = {k: v for k, v in before.items()
                    if k not in (_profile.COACHING_LAYER_KEY,
                                 _profile.BOUNDARIES_KEY,
                                 _profile.BOUNDARIES_SET_AT_KEY)}
    else:
        snapshot = None  # keep the pre-coaching snapshot already on disk
    if snapshot is not None:
        # COACH2 5.2 item 5 — the brief's render switch is a SETTING, not a
        # profile line, so it is snapshotted here beside the profile rather
        # than inside it. Restoring the profile byte-for-byte must not also
        # smuggle the switch back in, and turning coaching off must not
        # clobber a switch the seat had turned on by hand before any of this.
        atomic_write_json(d / UNDO_FILE, {"profile_config_before": snapshot,
                                          "shape_before": shape_before,
                                          "coaching_line_before":
                                              _coaching_line_stored(workspace_root),
                                          "taken_at": day})

    obj = {
        "schema_version": 1,
        "shape": shape,
        "behaviour": str(answers.get("behaviour") or "").strip(),
        "stance": normalize_stance(answers.get("stance")),
        "push": str(answers.get("push") or "steady"),
        "boundaries": _as_list(answers.get("boundaries")),
        "stakeholders": _as_list(answers.get("stakeholders")),
        "started_at": day,
        "quarter": _quarter(day),
        "door": door,
    }
    atomic_write_json(d / RELATIONSHIP_FILE, obj)

    cfg = dict(_profile.load_profile_config(workspace_root))
    cfg[_profile.COACHING_LAYER_KEY] = shape
    bounds = obj["boundaries"]
    if bounds:
        # NIGHT 11a fix round (N-10): a boundary the seat stated at
        # onboarding is not replaced by the walk's third answer - both stand.
        prior = cfg.get(_profile.BOUNDARIES_KEY)
        prior = [str(b) for b in prior] if isinstance(prior, list) else []
        cfg[_profile.BOUNDARIES_KEY] = list(dict.fromkeys(prior + list(bounds)))
        cfg.setdefault("boundaries_set_at", day)
    _profile._save_profile_config(
        workspace_root, cfg, origin="tune", previous=before,
        batch_id=_profile._new_batch_id("coaching-on"))
    # N-13's promise, kept: `surface_deltas` says one stated line appears on
    # the morning brief. That line is BRIEF2's render branch, gated on the
    # `coaching_line` axis, and until something flips the axis the preview
    # promises a delta the apply never produces. The switch is flipped HERE,
    # from the door, because the door is the only place that knows a seat
    # just opened one — never from inside the brief's own settings module.
    _set_coaching_line(workspace_root, "on", triggered_by=f"coaching-{door}")
    return {"shape": shape, "door": door, "started_at": day,
            "receipt": "Coaching is on. Say turn off coaching and everything "
                       "goes back exactly as it was."}


#: BRIEF2's per-surface render switch (`brief_settings.AXES["coaching_line"]`).
#: Spelled once, here, so the door and the brief can never drift by a
#: character. This lane never edits `brief_settings.py` — it only turns the
#: axis the brief already owns.
COACHING_LINE_AXIS = "coaching_line"


def _coaching_line_stored(workspace_root) -> dict:
    """What each surface has STORED for the switch, per surface, with `None`
    where the key is absent.

    Absent is not the same as `off`, and the difference is the whole reason
    this is snapshotted per surface rather than as one word. The profile page
    renders a line for every axis the store HOLDS, so writing `off` onto a
    seat that never held the key adds a line to a page whose whole promise is
    that turning coaching off puts it back byte for byte."""
    out: dict = {}
    try:
        import brief_settings as _bs
        from skill_config_writer import load_skill_config
        for surface in _bs.SURFACES:
            saved = load_skill_config(workspace_root, surface) or {}
            cfg = saved.get("config") if isinstance(saved, dict) else None
            cfg = cfg if isinstance(cfg, dict) else {}
            out[surface] = cfg.get(COACHING_LINE_AXIS)
    except Exception:  # noqa: BLE001 — no store is "nothing stored"
        return {}
    return out


def _set_coaching_line(workspace_root, value: str, *, triggered_by: str) -> dict:
    """Turn the brief's coaching line on. Never raises at a customer: a
    settings store that cannot be written is a brief without the line, not a
    failed door."""
    try:
        import brief_settings as _bs
        return _bs.apply_settings(workspace_root, {COACHING_LINE_AXIS: value},
                                  triggered_by=triggered_by)
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "reason": str(exc)}


def _restore_coaching_line(workspace_root, stored: dict) -> dict:
    """Put the switch back to what each surface held BEFORE the first door —
    including holding nothing at all, which means the key comes back OUT.

    Written straight through `skill_config_writer` rather than through
    `apply_settings`, because the thing being restored is sometimes the
    ABSENCE of the key and a settings writer has no word for that."""
    if not isinstance(stored, dict):
        stored = {}
    touched: list = []
    try:
        import brief_settings as _bs
        from skill_config_writer import load_skill_config, save_skill_config
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "reason": str(exc)}
    for surface in _bs.SURFACES:
        try:
            saved = load_skill_config(workspace_root, surface) or {}
            prev = saved.get("config") if isinstance(saved, dict) else None
            prev = dict(prev) if isinstance(prev, dict) else None
            if prev is None:
                continue
            new = dict(prev)
            want = stored.get(surface)
            if want is None:
                if COACHING_LINE_AXIS not in new:
                    continue
                new.pop(COACHING_LINE_AXIS, None)
            else:
                if new.get(COACHING_LINE_AXIS) == want:
                    continue
                new[COACHING_LINE_AXIS] = want
            save_skill_config(workspace_root, surface, new,
                              is_reconfigure=True, origin="tune",
                              event_extra={"skill_name": surface,
                                           "triggered_by": "coaching-off",
                                           "prev_config_present": True,
                                           "prev_config": prev})
            touched.append(surface)
        except Exception:  # noqa: BLE001 — one surface never blocks the other
            continue
    return {"ran": bool(touched), "surfaces": touched}


def _as_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in ("no", "none", "nothing", "nope"):
            return []
        return [text]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _quarter(day: str) -> str:
    try:
        d = _dt.date.fromisoformat(day)
    except ValueError:
        d = _dt.date.today()
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def turn_on_coaching(workspace_root, answers: Optional[dict] = None, *,
                     today: Optional[str] = None) -> dict:
    """The chosen door, applied. Call `preview` first — the walk shows the
    deltas before this runs, and this function is the "keep it"."""
    return _apply(workspace_root, shape=SHAPE_COACHED,
                  answers=answers or {}, door="chosen", today=today)


def turn_off_coaching(workspace_root) -> dict:
    """Put everything back.

    The profile settings are restored from the snapshot taken before the
    walk, so the profile page renders byte-for-byte what it rendered before.
    The relationship object and the notes are ARCHIVED, never deleted — they
    are the seat's own, and a seat that turns coaching back on should not
    have lost them."""
    import profile as _profile
    d = _dir(workspace_root)
    snap = _read_json(d / UNDO_FILE)
    before = snap.get("profile_config_before")
    # COACH2 5.2 item 5 — the switch goes back to what it was BEFORE the
    # first door, not to `off`: a seat that had asked for the coaching line
    # by hand keeps it. No snapshot to read means the axis default.
    line_before = snap.get("coaching_line_before")
    line_before = line_before if isinstance(line_before, dict) else {}
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived = []
    for name in (RELATIONSHIP_FILE,):
        p = d / name
        if p.exists():
            dest = d / "_archive" / f"{stamp}-{name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            p.replace(dest)
            archived.append(str(dest))
    forced = False
    if isinstance(before, dict):
        _profile._save_profile_config(
            workspace_root, before, origin="tune",
            previous=_profile.load_profile_config(workspace_root),
            batch_id=_profile._new_batch_id("coaching-off"))
    elif coaching_shape(workspace_root) != SHAPE_OBSERVED:
        # No snapshot to restore from but the seat IS coached (an `undo` of a
        # turn-off lands here): put the shape down by hand, with the store as
        # it stands as the way back - and say so. The walk's lines may still
        # be on the page, so the "exactly as it did before" receipt is not
        # true here (fix-round review, PO item 6).
        before_cfg = dict(_profile.load_profile_config(workspace_root))
        cfg = dict(before_cfg)
        cfg[_profile.COACHING_LAYER_KEY] = SHAPE_OBSERVED
        _profile._save_profile_config(
            workspace_root, cfg, origin="tune", previous=before_cfg,
            batch_id=_profile._new_batch_id("coaching-off"))
        forced = True
    # NIGHT 11a fix round (N-9): a seat that never turned coaching on and has
    # nothing to put back gets the honest line and NO write - the receipt
    # says the page reads as it did before, so nothing may move.
    # The receipt says coaching is off, so coaching has to be off. Read the
    # shape back out of the store rather than trusting the restore, and put
    # it down by hand if it did not land there. A receipt that says a thing
    # happened when it did not is worse than no receipt at all.
    if coaching_shape(workspace_root) != SHAPE_OBSERVED:
        cfg = dict(_profile.load_profile_config(workspace_root))
        cfg[_profile.COACHING_LAYER_KEY] = SHAPE_OBSERVED
        _profile._save_profile_config(
            workspace_root, cfg, origin="tune",
            previous=_profile.load_profile_config(workspace_root),
            batch_id=_profile._new_batch_id("coaching-off"))
        forced = True
    _restore_coaching_line(workspace_root, line_before)
    if (d / UNDO_FILE).exists():
        # DEL1: the profile is already restored by here and the receipt below
        # is about to say so, so a refused delete may not raise — it would
        # take the receipt down with it and leave the customer told nothing
        # after a change they asked for landed. On a mount that refuses
        # deletes the undo file is RENAMED aside (`.archived.<epoch>`): its
        # content has been applied, and `_read_json(d / UNDO_FILE)` no longer
        # finds a snapshot to re-apply.
        remove_or_move_aside(d / UNDO_FILE, "coaching-off undo file",
                             suffix=SUFFIX_ARCHIVED)
    receipt = ("Coaching is off and your profile reads exactly as it did "
               "before. Nothing you said was thrown away.")
    if forced:
        receipt = ("Coaching is off. I could not find the page as it read "
                   "before coaching started, so a line or two may have "
                   "stayed. Nothing you said was thrown away.")
    return {"shape": SHAPE_OBSERVED, "archived": archived,
            "restored_from_snapshot": not forced, "receipt": receipt}


# --------------------------------------------------------------------------
# What the profile page shows for this
# --------------------------------------------------------------------------

def profile_lines(workspace_root, sweep: dict, since: str,
                  line: Callable) -> List[dict]:
    """The "What you are working on" section. Empty on an observed seat,
    which is the default and the whole starting posture.

    Only the SHAPE, the behaviour in the seat's own words and the stance
    render here. No note, no session text, nothing from the coaching store
    beyond those three — the page is the seat's own, but it is also the page
    they screen-share."""
    shape = coaching_shape(workspace_root)
    if shape == SHAPE_OBSERVED:
        return []
    obj = relationship(workspace_root)
    day = str(obj.get("started_at") or "") or since
    out = []
    behaviour = str(obj.get("behaviour") or "").strip()
    if behaviour:
        out.append(line("working_on", "working_on.behaviour",
                        f"This quarter you are working on {behaviour}",
                        "asked", day))
    stance = obj.get("stance")
    if stance in STANCES:
        out.append(line("working_on", "working_on.stance",
                        {"ask_first": "You want questions before a read",
                         "tell_first": "You want a straight read, then the "
                                       "pushback",
                         "situational": "You want me to read the moment - "
                                        "questions when you are exploring, a "
                                        "straight read when the record "
                                        "disagrees with you"}[stance],
                        "asked", day))
    stakeholders = obj.get("stakeholders")
    if isinstance(stakeholders, list) and stakeholders:
        out.append(line("working_on", "working_on.stakeholders",
                        f"{len(stakeholders)} people are named as the ones "
                        "who would notice a change", "asked", day))
    return out


__all__ = [
    "SHAPES", "SHAPE_OBSERVED", "SHAPE_NAMED", "SHAPE_COACHED",
    "COACHING_LINE_AXIS", "RELATIONSHIP_FILE",
    "STANCES", "STANCE_DEFAULT", "EARNED_DOOR_FLOOR", "WALK_QUESTIONS",
    "coaching_shape", "coaching_enabled", "relationship",
    "earned_offer_due", "record_earned_offer", "accept_earned_offer",
    "walk_questions", "normalize_stance", "surface_deltas", "preview",
    "turn_on_coaching", "turn_off_coaching", "profile_lines",
]
