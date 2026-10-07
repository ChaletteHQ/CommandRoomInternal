#!/usr/bin/env python3
"""CUSTOM2 - the brief you asked for, kept as settings instead of a conversation.

WHY THIS EXISTS
---------------
"Customize my morning brief" used to be a conversation that had to happen
again every time, because nothing it decided was written down anywhere a fire
could read. The answers lived in free text under `_hq/custom/
morning-briefing.md`, which the model re-read and re-interpreted each morning;
two surfaces that should have moved together (the brief and the day-close)
moved apart; and the only way to change the shape of tomorrow's brief was to
have the conversation once more.

This module makes the same ten decisions ADDRESSABLE. Each one is an axis with
a name, a default, a plain-English question, a one-sentence path, and a reset.
They are stored in the two skill-config stores that already exist
(`morning-briefing` and `end-of-day`), which is why the settings survive a
restart, why a fire can read them without re-interpreting prose, and why one
act can move both surfaces plus the weekly wrap's grouping.

THE ORDERING RULE (a fence, proven by removal)
----------------------------------------------
Fires read SETTINGS. The free-text residue that survives migration is read
AFTER settings, and it can never override an axis the reader has actually
stated. `apply_residue` is the one place that ordering lives: a residue line
whose axis is already stated is recorded as overridden and dropped. Remove
that check and a five-line note file silently outranks the settings the reader
chose - the exact failure this build exists to end.

`stated` is per-AXIS, not per sub-key: once a reader has stated anything about
`thresholds`, a note about a different threshold is set aside too. That is
deliberate and it over-blocks in the safe direction - the reader's own choice
about a setting stands, and the note is reported, not obeyed.

THERE ARE EXACTLY FOUR PUBLIC ENTRANCES TO THIS RULE, and every one of them
is guarded. Two take free text from the caller and so can have the fence left
off by omission - both now REFUSE a `residue=` they were not also given a
`stated=`:

  1. `apply_residue(settings, residue, *, stated)` - the rule itself.
  2. `render_surface(view, settings, *, residue, stated)` - folds it too.

The other two read the store themselves, so the fence is on by construction
and there is no argument to forget:

  3. `settings_for_fire(workspace_root, surface)` - settings, then residue.
  4. `render_for_fire(workspace_root, view, surface)` - 3, then rendered.

A fire should use 4. A caller that has read the store itself may use 3. 1 and
2 exist for callers holding plain dicts, and they refuse rather than guess.
Anything added later that folds residue into settings is a fifth entrance and
owes the same refusal and the same removal proof.

THE NEGATION RULE (a fence, proven by removal)
----------------------------------------------
A prohibition is not a setting for the thing it prohibits. A negated sentence
is UNPARSEABLE and stays free text; it is never inverted into a value. Full
reasoning on `negation_in`.

THE FILTER FLOOR (a fence, proven by removal)
---------------------------------------------
An `only_groups` that matches no row is ignored and said out loud, and a plate
that filters away to nothing says so. A filter may empty the surface; it may
never do it silently under a number line that keeps counting. See
`_filter_rows`.

NOTHING IS DROPPED IN SILENCE (a fence, proven by removal)
----------------------------------------------------------
The migration takes standing notes past the five-line cap out of the notes
file. The receipt QUOTES them, because `undo` is only a mitigation for a
reader who has been told there is something to undo. See `dropped_clause`.

THE DEFAULT RULE (the second fence)
-----------------------------------
Every axis has a default and a reset. `resolve` composes from
`AXES[...]["default"]` and raises `MissingAxisDefault` when an axis has none,
because an axis with no default does not "fall back to sensible" - it falls
OPEN, and a surface that fails open renders whatever the last caller left in
the dict. A loud raise in code beats a quiet wrong brief.

WHAT IT DOES NOT DO
-------------------
- It does not decide WHEN the brief fires. That is `change-schedule`'s job and
  stays there; the `when` axis here is the WINDOW the brief covers.
- It does not rename the plate's number line. `vocabulary` never touches it,
  because the number leads (CUT-PLATE) and the fence that proves it matches
  that line's exact words.
- It does not write `events.jsonl` itself. Config writes go through
  `skill_config_writer.save_skill_config`; the residue receipt goes through
  `atomic_write.atomic_append_jsonl`, which gates strictly through
  `event_gate.gate_events(strict_enum=True)` - the same door
  `skill_custom_writer` uses for that event type.
- The `coaching_line` AXIS is a per-surface render switch (`off` / `on`).
  It is NOT the coaching stance - whether a coaching relationship exists at
  all is PROFILE1's `coaching_layer` in the `profile` store, and this axis
  never speaks for it.

Names in this module and its suite are the Sample/Stone placeholder roster.
"""

from __future__ import annotations

import copy
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Surfaces, stores, identifiers
# ---------------------------------------------------------------------------

#: The two config stores the ten axes live in. Both, always - the acceptance
#: is "one act moves the brief AND the day-close", so a write that lands in one
#: store only is a bug, not a smaller version of the feature.
SURFACE_BRIEF = "morning-briefing"
SURFACE_CLOSE = "end-of-day"
SURFACES = (SURFACE_BRIEF, SURFACE_CLOSE)

#: The undo class a settings act is stamped with. Registered in
#: `brain_undo.REVERSERS`; the reverser is the same typed config restore the
#: preset stamp uses (previous config back exactly, or the key cleared).
CHANGE_CLASS = "brief_settings"
#: The undo class for the free-text residue rewrite (its own reverser -
#: restores the file's previous text byte for byte).
RESIDUE_CHANGE_CLASS = "brief_residue"
BATCH_PREFIX = "cst_"

#: The migration leaves at most this many free-text lines behind.
RESIDUE_MAX_LINES = 5
#: The bot channel's whole brief, tail line included.
BOT_CHANNEL_MAX_LINES = 12

#: Where the free-text residue lives (the file the migration trims).
RESIDUE_SKILL = SURFACE_BRIEF


class BriefSettingsError(RuntimeError):
    """A settings read or write that cannot be honoured."""


class MissingAxisDefault(BriefSettingsError):
    """An axis with no default. Loud, in code: an axis without a default cannot
    be reset, cannot be explained on the profile page, and leaves the surface
    reading whatever the last caller left behind. See THE DEFAULT RULE."""


# ---------------------------------------------------------------------------
# The ten axes
# ---------------------------------------------------------------------------
#
# `default` is the value every workspace starts with, and the value a reset
# returns to. `question` is what the conversation asks, in the reader's words.
# `choices` is the closed set where there is one; a `map` axis takes a dict and
# merges key by key. `example` is the single sentence that sets the axis on its
# own, and it is also what the suite replays through `parse_sentence` - so the
# advertised sentence and the parsed sentence can never drift apart.

AXES: dict = {
    "organization": {
        "default": "importance",
        "kind": "choice",
        "choices": ("importance", "workstream", "people", "calendar", "money"),
        "question": "How should I group it - by what matters most, by workstream, by person, by your calendar, or by money?",
        "example": "group my brief by workstream",
        "plain": "how the brief is grouped",
    },
    "leads_with": {
        "default": "synthesis",
        "kind": "choice",
        "choices": ("synthesis", "calendar", "commitments", "money", "people"),
        "question": "After the number, what should come first?",
        "example": "lead with my calendar",
        "plain": "what comes first",
    },
    "section_depth": {
        "default": {"default": "full"},
        "kind": "map",
        "values": ("off", "one_line", "full"),
        "question": "How deep should each section go - everything, one line each, or off?",
        "example": "one line per section",
        "plain": "how deep each section goes",
    },
    "vocabulary": {
        "default": {},
        "kind": "map",
        "question": "Any words you would rather I used?",
        "example": "call them promises instead of commitments",
        "plain": "the words it uses",
    },
    "shape": {
        "default": "desktop",
        "kind": "choice",
        "choices": ("desktop", "bot_channel", "email"),
        "question": "Where do you read it - at your desk, on your phone, or in mail?",
        "example": "keep it short enough for my phone",
        "plain": "how long it runs",
    },
    "thresholds": {
        "default": {"group_min_items": 1, "overdue_after_days": 0},
        "kind": "map",
        "question": "How late does something have to be before I flag it?",
        "example": "do not flag anything until it is 3 days overdue",
        "plain": "when something counts as late",
    },
    "filters": {
        "default": {"only_groups": [], "exclude_groups": [], "hide_personal": False},
        "kind": "map",
        "question": "Anything you never want to see in it?",
        "example": "leave Sample Group out of my brief",
        "plain": "what it leaves out",
    },
    "when": {
        # The WINDOW the brief covers, never the hour it fires - the fire time
        # is `change-schedule`'s and stays there.
        "default": {"window": "since_last_brief", "days": 1},
        "kind": "map",
        "question": "How far back should it look - since the last one, or a fixed window?",
        "example": "cover the last 3 days",
        "plain": "how far back it looks",
    },
    "voice": {
        # A POINTER, not a copy: the persona is the one home for voice, so this
        # axis says which voice to follow rather than restating it.
        "default": "persona",
        "kind": "choice",
        "choices": ("persona",),
        "question": "Same voice as everywhere else?",
        "example": "use my usual voice",
        "plain": "whose voice it is in",
    },
    # A per-surface RENDER SWITCH: does the one coaching line print on this
    # surface. Deliberately NOT `coaching_layer` - that name belongs to
    # PROFILE1's three-value stance in the `profile` store, which decides
    # whether coaching exists at all. Renamed here so one name never carries
    # two meanings on one profile page.
    "coaching_line": {
        "default": "off",
        "kind": "choice",
        "choices": ("off", "on"),
        "question": "Want the one coaching line on it?",
        "example": "add the coaching line to my brief",
        "plain": "whether the coaching line shows",
    },
}

#: The order the conversation walks. Grouping first because it changes the most
#: on screen, so the re-render after answer one already looks different.
CONVERSATION_ORDER = (
    "organization", "leads_with", "section_depth", "shape",
    "filters", "thresholds", "vocabulary", "when", "voice", "coaching_line",
)

#: What a reader types to accept the walk.
KEEP_PHRASES = ("keep it", "keep that", "keep this", "yes keep it", "lock it in")


def axis_names() -> tuple:
    return tuple(AXES)


def axis_default(axis: str):
    """The default for one axis, deep-copied so a caller can never mutate the
    registry. Raises `MissingAxisDefault` when the axis has none."""
    if axis not in AXES:
        raise BriefSettingsError("no such setting: %r" % (axis,))
    spec = AXES[axis]
    if "default" not in spec:
        raise MissingAxisDefault(
            "the setting %r has no default. Every axis has a default and a "
            "reset; without one the surface reads whatever the last caller "
            "left behind instead of a known starting point." % (axis,))
    return copy.deepcopy(spec["default"])


def defaults() -> dict:
    """Every axis at its default - the shape `resolve` starts from and the
    shape a full reset writes back."""
    return dict((name, axis_default(name)) for name in AXES)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _merge(base, over):
    """A map axis merges key by key; every other kind is replaced."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(out.get(k), v) if isinstance(v, dict) else v
        return out
    return over


def stored_axes(workspace_root, surface: str) -> dict:
    """Only the axes this workspace has actually STATED, straight off the
    store. Nothing is filled in - that is `resolve`'s job. This is the set the
    residue is forbidden to override."""
    from skill_config_writer import load_skill_config
    saved = load_skill_config(workspace_root, surface) or {}
    cfg = saved.get("config") if isinstance(saved, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}
    return dict((k, copy.deepcopy(v)) for k, v in cfg.items() if k in AXES)


def stated_axes(workspace_root, surface: str = SURFACE_BRIEF) -> set:
    """The names of the axes this reader has actually CHOSEN. Stated outranks
    learned, always, and the free-text residue is the weakest layer of all.

    An axis stored at exactly its default value does not count as stated. That
    is not a technicality: a skill's first fire persists its whole DEFAULTS
    block so the store exists, which on a live workspace leaves keys sitting
    there that nobody ever chose. Counting those as choices would let a
    never-made decision outrank a note the reader actually wrote. A value that
    differs from the default is a decision; a value identical to it is the
    default, written down."""
    stored = stored_axes(workspace_root, surface)
    return set(k for k, v in stored.items() if v != axis_default(k))


def resolve(workspace_root, surface: str = SURFACE_BRIEF) -> dict:
    """The ten axes for one surface: defaults, with the stated values merged
    over them. Every axis is present in the answer - a reader never has to
    guess, and never has to carry its own fallback."""
    out = defaults()
    for k, v in stored_axes(workspace_root, surface).items():
        out[k] = _merge(out.get(k), v)
    return out


def describe(settings: dict) -> list:
    """The settings in plain English, one line each - what the profile page
    and the receipt both print. No key names, no store paths."""
    lines = []
    for name in CONVERSATION_ORDER:
        spec = AXES[name]
        lines.append("%s: %s" % (spec["plain"], value_phrase(name, settings.get(name))))
    return lines


def value_phrase(axis: str, value) -> str:
    """One value, said the way a person would say it."""
    if isinstance(value, dict):
        if not value:
            return "nothing set"
        return ", ".join("%s %s" % (k.replace("_", " "), v) for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "nothing set"
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value).replace("_", " ")


# ---------------------------------------------------------------------------
# The single-sentence path
# ---------------------------------------------------------------------------
#
# Any axis can be set by one sentence, on its own, with no conversation around
# it. Each rule is (pattern, builder). The builder returns (axis, value) or
# None when the captured word is not one this axis takes - a near-miss is a
# miss, never a guess.

_GROUP_WORDS = {
    "workstream": "workstream", "workstreams": "workstream",
    "project": "workstream", "projects": "workstream",
    "person": "people", "people": "people", "owner": "people",
    "client": "people", "clients": "people",
    "calendar": "calendar", "day": "calendar", "meeting": "calendar",
    "meetings": "calendar", "time": "calendar",
    "money": "money", "revenue": "money", "deal": "money", "deals": "money",
    "importance": "importance", "priority": "importance",
    "what matters": "importance",
}

_LEAD_WORDS = {
    "calendar": "calendar", "my calendar": "calendar", "meetings": "calendar",
    "commitments": "commitments", "what i owe": "commitments",
    "promises": "commitments", "my plate": "commitments",
    "money": "money", "deals": "money", "revenue": "money",
    "people": "people", "person": "people",
    "synthesis": "synthesis", "the theme": "synthesis", "the read": "synthesis",
}


def _rule_organization(m):
    word = (m.group("what") or "").strip().lower()
    val = _GROUP_WORDS.get(word)
    return ("organization", val) if val else None


def _rule_leads_with(m):
    word = (m.group("what") or "").strip().lower()
    val = _LEAD_WORDS.get(word)
    return ("leads_with", val) if val else None


def _rule_shape_short(m):
    return ("shape", "bot_channel")


def _rule_shape_desktop(m):
    return ("shape", "desktop")


def _rule_depth_one_line(m):
    return ("section_depth", {"default": "one_line"})


def _rule_depth_full(m):
    return ("section_depth", {"default": "full"})


def _rule_depth_section_off(m):
    return ("section_depth", {(m.group("what") or "").strip().lower(): "off"})


def _rule_vocabulary(m):
    new = (m.group("new") or "").strip().lower()
    old = (m.group("old") or "").strip().lower()
    if not new or not old:
        return None
    return ("vocabulary", {old: new})


def _rule_exclude(m):
    what = (m.group("what") or "").strip()
    return ("filters", {"exclude_groups": [what]}) if what else None


def _rule_only(m):
    what = (m.group("what") or "").strip()
    return ("filters", {"only_groups": [what]}) if what else None


def _rule_overdue(m):
    return ("thresholds", {"overdue_after_days": int(m.group("n"))})


def _rule_group_min(m):
    return ("thresholds", {"group_min_items": int(m.group("n"))})


def _rule_when_days(m):
    return ("when", {"window": "fixed_days", "days": int(m.group("n"))})


def _rule_when_since(m):
    return ("when", {"window": "since_last_brief", "days": 1})


def _rule_voice(m):
    return ("voice", "persona")


def _rule_coaching_on(m):
    return ("coaching_line", "on")


def _rule_coaching_off(m):
    return ("coaching_line", "off")


#: Ordered - the first rule that matches wins, and the more specific shapes
#: come first. Every `AXES[...]["example"]` is replayed through this table by
#: the suite, so an advertised sentence that stops parsing reds.
SENTENCE_RULES = (
    # NIGHT 11a fix round (REVIEW_NIGHT11A_MERGED_TREE N-3, R-1): the gap
    # between the verb and "by" may not swallow a prohibition — "group my
    # brief not by person but by workstream" used to match with the "not"
    # INSIDE the span, so the negation rule took it as consumed and the
    # sentence set the very thing it ruled out.
    (re.compile(r"\b(?:group|organi[sz]e|sort|arrange)\b(?:(?!\b(?:no|not|never|nor|stop|avoid|don'?t|do\s+not|hardly|rarely|seldom|except|other\s+than|but|unless|without|rather\s+than|instead\s+of)\b)[^.])*?\bby\s+(?P<what>what matters|workstreams?|projects?|people|person|owner|clients?|calendar|day|meetings?|time|money|revenue|deals?|importance|priority)\b", re.I), _rule_organization),
    (re.compile(r"\b(?:lead|open|start)\s+(?:it\s+)?with\s+(?:my\s+|the\s+)?(?P<what>calendar|meetings|commitments|what i owe|promises|my plate|money|deals|revenue|people|person|synthesis|the theme|the read)\b", re.I), _rule_leads_with),
    (re.compile(r"\b(?:keep it short|short enough for (?:my phone|slack|the bot)|make it fit (?:slack|my phone)|bot channel|phone[- ]sized)\b", re.I), _rule_shape_short),
    (re.compile(r"\b(?:full length again|back to the desktop brief|desk version)\b", re.I), _rule_shape_desktop),
    (re.compile(r"\b(?:one line per section|just the headlines|headlines only|one line each)\b", re.I), _rule_depth_one_line),
    (re.compile(r"\b(?:full detail everywhere|expand every section|show me everything)\b", re.I), _rule_depth_full),
    (re.compile(r"\b(?:drop|hide|turn off)\s+the\s+(?P<what>[a-z][a-z _-]{1,40}?)\s+section\b", re.I), _rule_depth_section_off),
    (re.compile(r"\bcall\s+(?:them|it)\s+(?P<new>[a-z][a-z ]{1,30}?)\s+instead of\s+(?P<old>[a-z][a-z ]{1,30})\b", re.I), _rule_vocabulary),
    (re.compile(r"\b(?:say|use)\s+[\"']?(?P<new>[a-z][a-z ]{1,30}?)[\"']?\s+(?:instead of|not)\s+[\"']?(?P<old>[a-z][a-z ]{1,30}?)[\"']?\s*$", re.I), _rule_vocabulary),
    (re.compile(r"\b(?:leave|keep)\s+(?P<what>.+?)\s+out of (?:my|the)\s+brief\b", re.I), _rule_exclude),
    (re.compile(r"\bonly show me\s+(?P<what>.+?)(?:\s+in (?:my|the) brief)?\s*$", re.I), _rule_only),
    (re.compile(r"\b(?:do ?n[o']?t|never)\s+flag\s+anything\s+until\s+it(?:\s+i|')s\s+(?P<n>\d{1,2})\s+days?\s+overdue\b", re.I), _rule_overdue),
    (re.compile(r"\bfold\s+groups?\s+(?:smaller|with fewer)\s+than\s+(?P<n>\d{1,2})\b", re.I), _rule_group_min),
    (re.compile(r"\bcover the last\s+(?P<n>\d{1,2})\s+days?\b", re.I), _rule_when_days),
    (re.compile(r"\b(?:just|only)\s+what(?:'s| is| has)?\s+(?:changed|new)\s+since (?:the )?last (?:one|brief)\b", re.I), _rule_when_since),
    (re.compile(r"\buse my usual voice\b", re.I), _rule_voice),
    (re.compile(r"\b(?:add|show|put)\s+the\s+coaching line\b", re.I), _rule_coaching_on),
    (re.compile(r"\b(?:no|drop the|without the)\s+coaching line\b", re.I), _rule_coaching_off),
)


#: Words that turn a sentence into a prohibition. See THE NEGATION RULE.
_NEGATION_RX = re.compile(
    r"\b(?:no|not|never|none|nothing|neither|nor|stop|avoid|"
    r"do\s*n[o']?t|does\s*n[o']?t|did\s*n[o']?t|"
    r"ca\s*n[o']?t|can\s*not|wo\s*n[o']?t|"
    r"is\s*n[o']?t|are\s*n[o']?t|"
    r"should\s*n[o']?t|would\s*n[o']?t|"
    r"dont|doesnt|didnt|cant|wont|isnt|arent|shouldnt|wouldnt|"
    # NIGHT 11a fix round (N-3): the soft prohibitions the corpus found
    r"hardly|rarely|seldom|rather\s+than|"
    # fix-round review (LC 1a): the exception shapes the exclude / only /
    # shape rules still inverted ("leave everything but X out", "only show me
    # things other than X", "anything but the bot channel version").
    r"except|other\s+than|anything\s+but|everything\s+but|everyone\s+but)\b", re.I)

#: Words that make a sentence CONDITIONAL. NIGHT 11a fix round (N-3, R-2):
#: "On Mondays lead with my calendar" used to set `leads_with` for every day
#: and the migration deleted the note, so the condition was lost twice. A
#: conditional sentence is unparseable, like a negated one: it stays free
#: text, where the model still reads the condition.
_CONDITION_RX = re.compile(
    r"\b(?:when(?:ever)?|if|unless|while|only on|except on|"
    # fix-round review (LC 1b): twelve natural phrasings walked through the
    # first word list unattended ("every Monday lead with my calendar",
    # "Mondays: ...", "during the week", "this week only", "on travel days").
    # A day, a period, an "every", a "during/before/after", a place - any of
    # these makes the sentence a condition. `until` stays out ONLY for the
    # overdue rule's own sentence ("until it is 3 days overdue").
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)days?|weekends?|weekdays?|"
    r"(?:every|each)\s+(?:day|morning|week|other)|during|before|after|"
    r"until(?!\s+(?:it(?:'s|\s+is)|they(?:'re|\s+are))\s+\d)|"
    r"this week|next week|in the mornings?|on days|travel days|on the road|"
    r"travell?ing)\b",
    re.I)


def condition_in(text: str):
    """The first conditional word in `text`, or None."""
    m = _CONDITION_RX.search(text or "")
    return m.group(0) if m else None

#: Typographic apostrophes normalised before the scan, so "don’t" is the
#: same prohibition as "don't".
_APOSTROPHES = "‘’ʼ´"


def negation_in(text: str, span=None):
    """The first prohibition word in `text` that the matched span does not
    cover, or None.

    THE NEGATION RULE (a fence, proven by removal)
    ----------------------------------------------
    A prohibition is not a setting for the thing it prohibits. "Never group my
    brief by person" names `organization` and `people` in the same breath, and
    a parser that reads only the words it recognises turns the sentence into
    its exact opposite - grouping by the one thing the reader ruled out, on
    both surfaces, and saying it moved the note in successfully.

    The ruling: a negated sentence is UNPARSEABLE, not inverted. It stays free
    text, where the model still reads it as prose and the reader can still see
    it in their own notes. Leaving a sentence as text is recoverable; silently
    setting the opposite of what it says is not.

    A rule may own its own negation - "do not flag anything until it is 3 days
    overdue" and "no coaching line" both mean what they say, and in both the
    prohibition word sits INSIDE the matched span. That is the test: a
    negation the matched rule did not consume is a negation the parser did not
    understand."""
    # One-for-one, so every offset below still indexes the caller's text.
    t = (text or "").translate({ord(c): "'" for c in _APOSTROPHES})
    for m in _NEGATION_RX.finditer(t):
        if span is not None and m.start() >= span[0] and m.end() <= span[1]:
            continue  # the rule consumed it, so the rule understood it
        return m.group(0)
    return None


def _value_words(value) -> str:
    """The words a parsed value carries (a group name, a vocabulary word), so
    a prohibition hiding inside them is seen."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_value_words(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_value_words(v) for v in value.values())
    return ""


def parse_sentence(text: str):
    """One sentence -> {"axis", "value", "matched"} or None.

    This is the single-sentence path AND the migration's reader: a directive
    that parses becomes a setting, one that does not becomes residue. The two
    can never disagree about what a sentence means, because there is one
    parser. It is also why THE NEGATION RULE lives here and not in a caller:
    the unattended migration reads through this same door."""
    t = (text or "").strip()
    if not t:
        return None
    if condition_in(t) is not None:
        # A setting with a condition on it is not a setting. Free text keeps
        # the condition; a store cannot.
        return None
    for rx, build in SENTENCE_RULES:
        m = rx.search(t)
        if not m:
            continue
        got = build(m)
        if got is None:
            continue
        if negation_in(_value_words(got[1])) is not None:
            # fix-round review (LC 1a): "leave everything but X out of my
            # brief" / "only show me things other than X" put the exception
            # INSIDE the captured value, where the span rule could not see
            # it - and stored a group named "everything but X".
            continue
        if negation_in(t, m.span()) is not None:
            # A prohibition the rule did not consume. Refuse the whole
            # sentence rather than set the thing it rules out.
            continue
        axis, value = got
        return {"axis": axis, "value": value, "matched": m.group(0).strip()}
    return None


def is_keep(text: str) -> bool:
    """`keep it` and its neighbours - the one word that applies the walk.

    Two things it is NOT. A prohibition is not an acceptance: "do not keep it"
    contains "keep it" and means the opposite, and applying a walk on a refusal
    writes ten settings the reader was in the middle of declining. And a
    sentence that names a setting is an ANSWER before it is an acceptance -
    "keep it short enough for my phone" is this lane's own advertised sentence
    for `shape`, so a reader answering the walk's shape question with the words
    the walk just showed them must set the axis, not end the walk."""
    t = (text or "").strip().lower().rstrip(".!")
    if not t:
        return False
    if negation_in(t) is not None:
        return False
    if parse_sentence(t) is not None:
        return False
    if condition_in(t) is not None:
        return False  # "keep it unless..." - fix-round review (LC 1d)
    return any(t == p or t.startswith(p + " ") or t.endswith(" " + p)
               for p in KEEP_PHRASES)


# ---------------------------------------------------------------------------
# The five templates
# ---------------------------------------------------------------------------
#
# TEMPLATE 1 (`workstream`) is the genericized shape of the free-text
# directives a workstream-organized reader had been maintaining by hand: group
# everything under the workstream it belongs to, open with what is owed rather
# than with a synthesis paragraph, never fold a workstream away for being
# small, and keep the quiet section to a single line. No client name, no client
# content, no client vocabulary survives into it - the settings are the whole
# of what carried over, and the words in a rendered brief come from the
# reader's own workspace.

TEMPLATES: dict = {
    "workstream": {
        "label": "By workstream",
        "one_liner": "Everything under the workstream it belongs to, what you owe first.",
        "settings": {
            "organization": "workstream",
            "leads_with": "commitments",
            "thresholds": {"group_min_items": 1},
            "section_depth": {"default": "full", "going quiet": "one_line"},
        },
    },
    "people": {
        "label": "By person",
        "one_liner": "Grouped by who it involves, so you can read it before a call.",
        "settings": {
            "organization": "people",
            "leads_with": "people",
            "thresholds": {"group_min_items": 1},
        },
    },
    "calendar-first": {
        "label": "Calendar first",
        "one_liner": "The day as it will actually run, then everything else.",
        "settings": {
            "organization": "calendar",
            "leads_with": "calendar",
        },
    },
    "money": {
        "label": "Money first",
        "one_liner": "Anything with a number attached, before anything without one.",
        "settings": {
            "organization": "money",
            "leads_with": "money",
        },
    },
    "minimal": {
        "label": "Minimal",
        "one_liner": "One line a section, short enough to read on a phone.",
        "settings": {
            "shape": "bot_channel",
            "section_depth": {"default": "one_line"},
            "leads_with": "commitments",
        },
    },
}


def template_names() -> tuple:
    return tuple(TEMPLATES)


def template_settings(name: str) -> dict:
    """One template's axis values, merged over the defaults so the answer is a
    complete settings dict every axis can be read from."""
    if name not in TEMPLATES:
        raise BriefSettingsError("no such template: %r" % (name,))
    out = defaults()
    for k, v in TEMPLATES[name]["settings"].items():
        if k not in AXES:
            raise BriefSettingsError(
                "template %r sets %r, which is not one of the settings" % (name, k))
        out[k] = _merge(out.get(k), v)
    return out


# ---------------------------------------------------------------------------
# Settings first, the free-text residue second - THE ORDERING FENCE
# ---------------------------------------------------------------------------


#: A caller that never mentioned `stated` is different from one that passed an
#: empty set. The first does not know the ordering rule exists; the second has
#: read the store and found nothing stated. BOTH functions that fold free text
#: - `apply_residue` and `render_surface` - refuse the first whenever they are
#: also handed free text. See THE ORDERING RULE.
_STATED_UNSET: Any = object()

_NO_STATED_MSG = (
    "render_surface was handed free-text notes but was never told which "
    "settings the reader has actually stated, so it cannot tell which notes "
    "are allowed to speak - and a note that moves a stated setting is the "
    "failure this build exists to end. Call render_for_fire(workspace_root, "
    "view, surface=...), which reads both in the one legal order, or pass "
    "stated=stated_axes(workspace_root, surface) yourself.")

_NO_STATED_MSG_RESIDUE = (
    "apply_residue was handed free-text notes but was never told which "
    "settings the reader has actually stated, so it cannot tell which notes "
    "are allowed to speak - and a note that moves a stated setting is the "
    "failure this build exists to end. Call settings_for_fire(workspace_root, "
    "surface), which reads both in the one legal order, or pass "
    "stated=stated_axes(workspace_root, surface) yourself.")


def apply_residue(settings: dict, residue, *, stated=_STATED_UNSET):
    """Fold the free-text residue into `settings` WITHOUT letting it override
    anything the reader has stated.

    Returns `(settings, notes)`. `notes` carries three lists in the reader's
    own words: what the residue still contributes, what it tried to say about
    a setting that is already stated (dropped), and what nobody could parse
    (kept as prose for the model, never as a setting).

    THIS FUNCTION IS THE ORDERING RULE. Delete the `stated` check and a note
    file outranks the settings page - which is the failure that made
    "customize my brief" a conversation you had to keep having.

    Which is exactly why `stated` cannot be left off. A default of `()` made
    the rule's own function the widest way around it: one natural line -
    `apply_residue(resolve(ws), residue_lines(ws))` - read as correct and let
    every note outrank every setting. Free text with no stated list now
    RAISES, the same refusal `render_surface` carries, so neither public
    entrance to this rule can have the fence left off by omission.
    `settings_for_fire` is the door to use."""
    if residue and stated is _STATED_UNSET:
        raise BriefSettingsError(_NO_STATED_MSG_RESIDUE)
    out = dict(settings or {})
    stated = set(() if stated is _STATED_UNSET else (stated or ()))
    notes = {"applied": [], "overridden": [], "unparsed": []}
    for line in (residue or ()):
        parsed = parse_sentence(line)
        if parsed is None:
            notes["unparsed"].append(line)
            continue
        axis = parsed["axis"]
        if axis in stated:
            # Settings were read first and they win. The residue line is not
            # deleted - it is simply not allowed to move a stated axis.
            notes["overridden"].append(line)
            continue
        out[axis] = _merge(out.get(axis), parsed["value"])
        notes["applied"].append(line)
    return out, notes


def residue_lines(workspace_root) -> list:
    """The free-text lines that survived migration, capped. Never more than
    `RESIDUE_MAX_LINES`, because a note file longer than that is a settings
    page nobody wrote down."""
    from skill_custom_writer import load_directives
    got = [d.get("text", "") for d in load_directives(workspace_root, RESIDUE_SKILL)]
    return [t for t in got if t][:RESIDUE_MAX_LINES]


def settings_for_fire(workspace_root, surface: str = SURFACE_BRIEF):
    """WHAT A FIRE READS, in the one legal order: settings, then the residue
    beneath them. Returns `(settings, notes)`. Every fire that renders a brief
    or a day-close calls exactly this - which is why no fire has to know the
    ordering rule, and why no fire can get it wrong."""
    stated = stated_axes(workspace_root, surface)
    settings = resolve(workspace_root, surface)
    return apply_residue(settings, residue_lines(workspace_root), stated=stated)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

GROUP_FALLBACK = "Everything else"
NO_PERSON = "No one named"
NO_DATE = "No date"
MONEY_GROUP = "Money on the table"

#: What each `leads_with` value names on the composed screen. `commitments`
#: names the plate block itself; everything else names a section key.
LEAD_TO_KEY = {
    "commitments": "plate",
    "calendar": "calendar",
    "money": "money",
    "people": "people",
    "synthesis": "synthesis",
}

#: The wrap's grouping heading per organization. `importance` keeps the wrap's
#: shipped default heading, so a workspace that never touches these settings
#: sees the wrap it saw yesterday.
WRAP_HEADINGS = {
    "importance": "By project",
    "workstream": "By workstream",
    "people": "By person",
    "calendar": "By day",
    "money": "By money",
}


def shape_line_cap(shape: str):
    """Lines the whole surface may run to, tail line included. None = no cap."""
    return BOT_CHANNEL_MAX_LINES if shape == "bot_channel" else None


def _complete(settings: dict) -> dict:
    """Fill any axis the caller left out from its default. Raises
    `MissingAxisDefault` through `axis_default` when an axis has none - the
    surface loses that axis loudly instead of rendering whatever was in the
    dict."""
    out = dict(settings or {})
    for name in AXES:
        if name not in out:
            out[name] = axis_default(name)
    return out


def _row_group(row: dict, organization: str) -> str:
    if organization == "workstream":
        return (row.get("group") or "").strip() or GROUP_FALLBACK
    if organization == "people":
        return (row.get("person") or "").strip() or NO_PERSON
    if organization == "calendar":
        return (row.get("when") or "").strip() or NO_DATE
    if organization == "money":
        return MONEY_GROUP if row.get("amount") else GROUP_FALLBACK
    return ""


def _matches_any(row, organization: str, wanted: set) -> bool:
    name = _row_group(row, organization or "workstream").strip().lower()
    raw = (row.get("group") or "").strip().lower()
    return bool(set(n for n in (name, raw) if n) & wanted)


def _filter_rows(rows, filters: dict, organization: str, notes=None) -> list:
    """Rows the filters leave standing, plus - through `notes` - anything the
    reader should be told out loud.

    THE FILTER FLOOR (a fence, proven by removal)
    ---------------------------------------------
    A filter that matches NOTHING is a typo, not an instruction to hide the
    whole plate. "Only show me what changed" names a group that does not
    exist, and an `only_groups` taken literally empties the surface while the
    number line keeps counting - the reader reads "4 on your plate" above a
    blank space and has no way to know a filter did it. So an `only_groups`
    that matches no row is IGNORED and said out loud, and a plate that filters
    away to nothing says that out loud too. Silence is the one thing a filter
    may never do."""
    rows = list(rows or ())
    said = notes if notes is not None else []
    only = [str(g).strip().lower() for g in (filters.get("only_groups") or [])]
    exclude = [str(g).strip().lower() for g in (filters.get("exclude_groups") or [])]
    hide_personal = bool(filters.get("hide_personal"))

    if only and not any(_matches_any(r, organization, set(only)) for r in rows):
        said.append(
            "Nothing on your plate is %s, so I left the whole plate in."
            % " or ".join(str(g).strip()
                          for g in (filters.get("only_groups") or [])))
        only = []

    out = []
    for row in rows:
        if hide_personal and row.get("personal"):
            continue
        names_hit_only = _matches_any(row, organization, set(only)) if only else True
        if only and not names_hit_only:
            continue
        if exclude and _matches_any(row, organization, set(exclude)):
            continue
        out.append(row)

    if rows and not out:
        said.append("Your filters left nothing on the plate this morning.")
    return out


def _threshold_rows(rows, thresholds: dict) -> list:
    after = int(thresholds.get("overdue_after_days") or 0)
    if after <= 0:
        return list(rows)
    out = []
    for row in rows:
        over = row.get("overdue_days")
        if over is not None and int(over) > 0 and int(over) < after:
            continue
        out.append(row)
    return out


def group_rows(rows, settings: dict, *, notes=None) -> list:
    """Rows as `[(group_name, [row, ...]), ...]` in render order.

    `importance` returns one unnamed group - the shipped order, untouched. A
    group smaller than `thresholds.group_min_items` folds into one named
    remainder rather than disappearing: a threshold is a render bound, never a
    silence.

    `notes`, when a caller passes a list, collects anything the filters want
    said out loud - see THE FILTER FLOOR."""
    settings = _complete(settings)
    organization = settings["organization"]
    rows = _filter_rows(rows, settings["filters"], organization, notes=notes)
    rows = _threshold_rows(rows, settings["thresholds"])
    if organization == "importance":
        return [("", list(rows))]
    buckets: list = []
    index: dict = {}
    for row in rows:
        name = _row_group(row, organization)
        if name not in index:
            index[name] = len(buckets)
            buckets.append((name, []))
        buckets[index[name]][1].append(row)
    floor = int((settings["thresholds"] or {}).get("group_min_items") or 1)
    if floor <= 1:
        return buckets
    kept, folded = [], []
    for name, items in buckets:
        (kept if len(items) >= floor else folded).append((name, items))
    if folded:
        rest = []
        for _, items in folded:
            rest.extend(items)
        kept.append((GROUP_FALLBACK, rest))
    return kept


def _apply_vocabulary(line: str, vocabulary: dict) -> str:
    for old, new in sorted((vocabulary or {}).items()):
        if not old:
            continue
        line = re.sub(r"\b%s\b" % re.escape(str(old)), str(new), line, flags=re.I)
    return line


def _depth_for(settings: dict, *names) -> str:
    depth = settings.get("section_depth") or {}
    for n in names:
        if not n:
            continue
        key = str(n).strip().lower()
        if key in depth:
            return depth[key]
    return depth.get("default", "full")


def render_surface(view: dict, settings: dict, *, surface: str = SURFACE_BRIEF,
                   residue=(), stated=_STATED_UNSET) -> dict:
    """The composed surface, as the settings say it should look.

    THE ORDERING RULE REACHES THIS DOOR TOO. `render_surface` folds the
    free-text residue in, so it is a second public entrance to the rule that
    settings outrank notes. Passing `residue=` without `stated=` used to
    default the fence OFF and let a note regroup a brief over the reader's own
    stated choice; it now RAISES. Use `render_for_fire` unless you have read
    the store yourself.

    `view` is the surface's own content, already gathered:
      number_line     the plate's one lead line - it ALWAYS leads, whatever
                      `leads_with` says, because CUT-PLATE's rule outranks a
                      preference and `assert_number_leads` is run here.
      rows            [{"text", "group", "person", "when", "amount",
                        "overdue_days", "personal"}]
      sections        [{"key", "name", "lines"}]
      coaching_line   one line of the view's own content, rendered only when
                      the `coaching_line` SETTING is on. (Same word, two
                      places: the axis is the switch, the view key is the
                      line.) NO FIRE SUPPLIES THIS KEY, and that is still
                      deliberate: the switch says whether a coaching line
                      would print, never whether this reader has earned and
                      chosen coaching, and this module reads no such door.
                      BRIEF2 (2026-09-14) built the line where the doors ARE
                      — `surface_drivers.brief_coaching_line` reads the
                      coaching shape, THIS setting and the coaching object's
                      behaviour, and places the sentence inside the morning
                      pack's own lead, where the brief's leak gate and its
                      ask fence see it. So the morning fire READS this switch
                      and passes no `coaching_line` key: the branch below
                      would then print the same line a second time. The key
                      stays for a surface with no pack of its own to place a
                      line in.

    Returns `{"lines", "text", "groups", "blocks", "capped"}`."""
    view = view or {}
    if residue and stated is _STATED_UNSET:
        raise BriefSettingsError(_NO_STATED_MSG)
    stated = () if stated is _STATED_UNSET else stated
    settings, _notes = apply_residue(_complete(settings), residue, stated=stated)
    settings = _complete(settings)
    number_line = str(view.get("number_line") or "").strip()

    said: list = []
    grouped = group_rows(view.get("rows") or [], settings, notes=said)
    plate_lines: list = []
    group_names: list = []
    for name, items in grouped:
        if not items:
            continue
        if name:
            group_names.append(name)
            plate_lines.append(name)
        for row in items:
            plate_lines.append("- %s" % str(row.get("text") or "").strip())
    plate_depth = _depth_for(settings, "plate", "your plate")
    if plate_depth == "off":
        plate_lines = []
    elif plate_depth == "one_line" and plate_lines:
        plate_lines = plate_lines[:1]

    blocks = [{"key": "plate", "name": "Your plate", "lines": plate_lines}]
    for sec in (view.get("sections") or []):
        name = sec.get("name") or sec.get("key") or ""
        depth = _depth_for(settings, sec.get("key"), name)
        lines = list(sec.get("lines") or [])
        if depth == "off" or not lines:
            continue
        if depth == "one_line":
            lines = lines[:1]
        blocks.append({"key": sec.get("key") or name, "name": name, "lines": lines})

    lead_key = LEAD_TO_KEY.get(settings["leads_with"], settings["leads_with"])
    ordered = ([b for b in blocks if b["key"] == lead_key]
               + [b for b in blocks if b["key"] != lead_key])

    lines: list = [number_line] if number_line else []
    # What a filter did to the plate is said out loud IMMEDIATELY under the
    # number, where the shape cap cannot shorten it away: a filter may empty
    # the surface, but it may never do it silently under a number line that
    # keeps counting. See THE FILTER FLOOR.
    lines.extend(said)
    for block in ordered:
        if not block["lines"]:
            continue
        if block["key"] != "plate" and block.get("name"):
            lines.append(block["name"])
        lines.extend(str(l) for l in block["lines"])

    if settings["coaching_line"] == "on" and view.get("coaching_line"):
        lines.append(str(view["coaching_line"]))

    # The vocabulary never touches the number line. The plate's line is the one
    # the order fence matches on, word for word; renaming it would silence the
    # fence rather than change the wording.
    vocabulary = settings["vocabulary"]
    if vocabulary:
        lines = ([lines[0]] + [_apply_vocabulary(l, vocabulary) for l in lines[1:]]
                 if lines else lines)

    cap = shape_line_cap(settings["shape"])
    capped = False
    if cap and len(lines) > cap:
        hidden = len(lines) - (cap - 1)
        lines = lines[:cap - 1]
        lines.append("...and %d more - say `morning briefing` for the rest." % hidden)
        capped = True

    text = "\n".join(lines)
    from surface_drivers import assert_number_leads
    assert_number_leads(text, where=surface, number_line=number_line)
    return {"lines": lines, "text": text, "groups": group_names,
            "blocks": [b["key"] for b in ordered if b["lines"]],
            "capped": capped, "said": said}


def render_for_fire(workspace_root, view: dict, *,
                    surface: str = SURFACE_BRIEF) -> dict:
    """THE DOOR A FIRE SHOULD USE: read the store in the one legal order, then
    render with it.

    `settings_for_fire` + `render_surface` in a single call, with `stated`
    supplied from the store, so the ordering rule cannot be left off by
    forgetting an argument. Returns the render dict with `notes` added - what
    the leftover free text still contributes, and which of it was set aside
    for naming something the reader has already stated."""
    settings, notes = settings_for_fire(workspace_root, surface)
    out = render_surface(view, settings, surface=surface)
    out["notes"] = notes
    return out


def wrap_group_section(workspace_root) -> dict:
    """The weekly wrap's grouping section, per the SAME `organization` axis the
    brief and the day-close read. This is the third surface the one act moves:
    the wrap's own heading and grouping key come from here, so a reader who
    says "group my brief by workstream" does not then find the wrap still
    grouped by project."""
    settings = resolve(workspace_root, SURFACE_BRIEF)
    organization = settings["organization"]
    return {"heading": WRAP_HEADINGS.get(organization, WRAP_HEADINGS["importance"]),
            "group_by": organization}


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------


def conversation_step(view: dict, settings: dict, *, answers=None,
                      surface: str = SURFACE_BRIEF) -> dict:
    """One turn of `customize my morning brief`: today's surface as it would
    look with the answers so far, then ONE question.

    The re-render is not a courtesy the prose asks for - it is what this
    function returns. A caller that walks the axes by calling this repeatedly
    cannot skip the re-render, because the render IS the return value."""
    answers = dict(answers or {})
    working = _complete(settings)
    for axis, value in answers.items():
        if axis not in AXES:
            raise BriefSettingsError("no such setting: %r" % (axis,))
        working[axis] = _merge(working.get(axis), value)
    rendered = render_surface(view, working, surface=surface)
    remaining = [a for a in CONVERSATION_ORDER if a not in answers]
    question = None
    if remaining:
        axis = remaining[0]
        question = {"axis": axis,
                    "question": AXES[axis]["question"],
                    "example": AXES[axis]["example"],
                    "choices": tuple(AXES[axis].get("choices", ()))}
    return {"render": rendered["text"], "lines": rendered["lines"],
            "question": question, "answered": len(answers),
            "remaining": remaining, "done": not remaining, "settings": working}


# ---------------------------------------------------------------------------
# Write: one act, one receipt, one undo
# ---------------------------------------------------------------------------


def _now_iso(now_iso=None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        return now_iso
    return datetime.now(timezone.utc).isoformat()


def mint_batch_id(now_iso=None) -> str:
    stamp = _now_iso(now_iso)[:19].replace("-", "").replace(":", "")
    return "%s%s-%s" % (BATCH_PREFIX, stamp, secrets.token_hex(4))


def validate_value(axis: str, value):
    """(ok, reason). The reason is plain English, ready to say back."""
    if axis not in AXES:
        return False, "I do not have a setting for that yet."
    spec = AXES[axis]
    if spec.get("kind") == "choice":
        if value not in spec.get("choices", ()):
            return False, ("For %s I can do %s."
                           % (spec["plain"],
                              " or ".join(str(c).replace("_", " ")
                                          for c in spec.get("choices", ()))))
        return True, None
    if spec.get("kind") == "map" and not isinstance(value, dict):
        return False, "That one takes a name and a value together."
    return True, None


def receipt_line(changed: dict) -> str:
    """One sentence naming what moved and where it now applies."""
    if not changed:
        return "Nothing changed - that is already how it reads."
    parts = ["%s is now %s" % (AXES[a]["plain"], value_phrase(a, c["to"]))
             for a, c in changed.items() if a in AXES]
    return ("Your morning brief and your day-close: %s. Say `undo` to put it back."
            % "; ".join(parts))


def apply_settings(workspace_root, changes: dict, *, triggered_by: str,
                   now_iso=None, surfaces=SURFACES, batch_id=None,
                   origin: str = "tune") -> dict:
    """Write one or more axes to BOTH surfaces as ONE act.

    Both stores move together on purpose: the acceptance for this build is that
    a single sentence changes the morning brief and the day-close (and, through
    `wrap_group_section`, the wrap's grouping) - not that it changes one of
    them and leaves the reader to find the others.

    Every write is stamped with the same batch, so a bare `undo` lists one act
    and the registered reverser puts both stores back exactly."""
    from skill_config_writer import load_skill_config, save_skill_config
    changes = dict(changes or {})
    for axis, value in changes.items():
        ok, reason = validate_value(axis, value)
        if not ok:
            raise BriefSettingsError(reason)
    batch_id = batch_id or mint_batch_id(now_iso)
    ts = _now_iso(now_iso)
    changed: dict = {}
    touched: list = []
    for surface in surfaces:
        saved = load_skill_config(workspace_root, surface) or {}
        prev = saved.get("config") if isinstance(saved, dict) else None
        prev = dict(prev) if isinstance(prev, dict) else None
        new = dict(prev or {})
        moved = False
        for axis, value in changes.items():
            before = new.get(axis, axis_default(axis))
            after = _merge(new.get(axis, axis_default(axis)), value)
            new[axis] = after
            if before != after:
                moved = True
                if axis not in changed:
                    changed[axis] = {"from": before, "to": after}
        if not moved:
            continue
        save_skill_config(
            workspace_root, surface, new,
            is_reconfigure=bool(prev), origin=origin,
            event_extra={"brain_batch_id": batch_id,
                         "brain_change_class": CHANGE_CLASS,
                         "skill_name": surface,
                         "prev_config_present": prev is not None,
                         "prev_config": prev,
                         "triggered_by": triggered_by},
            event_ts=ts)
        touched.append(surface)
    return {"ran": bool(touched), "batch_id": batch_id if touched else None,
            "changed": changed, "surfaces": touched,
            "receipt": receipt_line(changed),
            "undo": "Say `undo` to put it back."}


def apply_sentence(workspace_root, text: str, *, now_iso=None,
                   triggered_by=None) -> dict:
    """The single-sentence path: "group my brief by workstream" on its own,
    with no conversation around it. Returns the same receipt shape, or
    `{"ok": False, "reason": ...}` when nothing in the sentence names a
    setting."""
    parsed = parse_sentence(text)
    if parsed is None:
        # A prohibition gets its own answer, because "I did not catch that"
        # reads as a parser miss when the reader was in fact perfectly clear.
        # THE NEGATION RULE: I will not set the thing you ruled out.
        if negation_in(text) is not None:
            return {"ok": False, "ran": False, "negated": True,
                    "reason": "That says what you do not want, and I can only "
                              "set a part of the brief TO something. Tell me "
                              "what you would like instead and I will set it."}
        return {"ok": False, "ran": False,
                "reason": "I did not catch which part of the brief that changes."}
    out = apply_settings(workspace_root, {parsed["axis"]: parsed["value"]},
                         triggered_by=triggered_by or (text or "").strip(),
                         now_iso=now_iso)
    out["ok"] = True
    out["axis"] = parsed["axis"]
    if _COMPOUND_RX.search(text or "") and out.get("receipt"):
        # fix-round review (LC 1c): one sentence, two instructions - the
        # receipt names the part it took and asks for the rest on its own,
        # instead of leaving the second clause unsaid and unwritten.
        out["receipt"] += (" I took only the part about %s; say the rest on "
                           "its own." % AXES[parsed["axis"]]["plain"])
        out["partial"] = True
    return out


def apply_template(workspace_root, name: str, *, now_iso=None,
                   triggered_by=None) -> dict:
    """One of the five starting shapes, applied as one act."""
    if name not in TEMPLATES:
        raise BriefSettingsError("no such template: %r" % (name,))
    out = apply_settings(workspace_root, dict(TEMPLATES[name]["settings"]),
                         triggered_by=triggered_by or ("template: %s" % name),
                         now_iso=now_iso)
    out["template"] = name
    out["receipt"] = ("Your morning brief and your day-close now read %s - %s "
                      "Say `undo` to put it back."
                      % (TEMPLATES[name]["label"].lower(),
                         TEMPLATES[name]["one_liner"]))
    return out


def reset_settings(workspace_root, *, axes=None, now_iso=None,
                   triggered_by: str = "reset my brief",
                   surfaces=SURFACES, batch_id=None) -> dict:
    """`reset my brief` - the named axes (or all ten) back to their shipped
    defaults. The stored keys are REMOVED rather than overwritten, so the axis
    stops being stated: a reader who resets grouping gets the shipped grouping
    back, and anything they had said about it in free text is allowed to speak
    again."""
    from skill_config_writer import load_skill_config, save_skill_config
    names = list(axes) if axes else list(AXES)
    for a in names:
        axis_default(a)  # loud if an axis has no default to reset TO
    batch_id = batch_id or mint_batch_id(now_iso)
    ts = _now_iso(now_iso)
    touched: list = []
    changed: dict = {}
    for surface in surfaces:
        saved = load_skill_config(workspace_root, surface) or {}
        prev = saved.get("config") if isinstance(saved, dict) else None
        prev = dict(prev) if isinstance(prev, dict) else None
        if not prev:
            continue
        new = dict(prev)
        moved = False
        for a in names:
            if a in new:
                if a not in changed:
                    changed[a] = {"from": new[a], "to": axis_default(a)}
                del new[a]
                moved = True
        if not moved:
            continue
        save_skill_config(
            workspace_root, surface, new, is_reconfigure=True, origin="reset",
            event_extra={"brain_batch_id": batch_id,
                         "brain_change_class": CHANGE_CLASS,
                         "skill_name": surface,
                         "prev_config_present": True,
                         "prev_config": prev,
                         "triggered_by": triggered_by},
            event_ts=ts)
        touched.append(surface)
    receipt = ("Your morning brief and your day-close are back to how they "
               "shipped." if touched else
               "They were already the way they shipped - nothing to put back.")
    return {"ran": bool(touched), "batch_id": batch_id if touched else None,
            "changed": changed, "surfaces": touched, "receipt": receipt,
            "undo": "Say `undo` to bring your settings back." if touched else ""}


# ---------------------------------------------------------------------------
# Migration: free text in, settings out, at most five lines left over
# ---------------------------------------------------------------------------


def _custom_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "custom" / ("%s.md" % RESIDUE_SKILL)


#: A note that joins two instructions. Free text keeps both; a setting keeps
#: one. NIGHT 11a fix round (N-3).
_COMPOUND_RX = re.compile(r"\b(?:and|but|then|also|plus|except)\b|;", re.I)


def _merge_filters(base, over):
    """The filters axis ACCUMULATES across notes: two "leave X out" notes are
    two exclusions, not the last one. (NIGHT 11a fix round, N-3 R-4 - the
    typed path still replaces, because a typed sentence is the reader's
    current word; only the unattended migration folds.)"""
    out = dict(base) if isinstance(base, dict) else {}
    for k, v in (over or {}).items():
        if isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = v
    return out


def plan_migration(workspace_root) -> dict:
    """Read-only: what the migration WOULD do. `mapped` becomes settings;
    `residue` stays as free text; `overflow` is what does not fit under the
    five-line cap and has to be folded by a word before it is dropped."""
    from skill_custom_writer import load_directives
    mapped: list = []
    residue: list = []
    # NIGHT 11a fix round (N-3, R-7): a setting the reader has STATED - typed
    # in the walk or in one sentence - outranks a note they wrote before it.
    # The migration used to flip the typed value back to the note's, with a
    # receipt that never said so.
    stated = stated_axes(workspace_root)
    for d in load_directives(workspace_root, RESIDUE_SKILL):
        text = (d.get("text") or "").strip()
        if not text:
            continue
        parsed = parse_sentence(text)
        # (N-3, R-3/R-5): a note that says two things is not one setting.
        # The first clause used to become the setting and the rest of the
        # sentence left the file for good under "Moved N".
        compound = _COMPOUND_RX.search(text) is not None
        if parsed is None or compound or parsed["axis"] in stated:
            residue.append({"id": d.get("id"), "text": text})
        else:
            mapped.append({"id": d.get("id"), "text": text,
                           "axis": parsed["axis"], "value": parsed["value"]})
    overflow = residue[RESIDUE_MAX_LINES:]
    residue = residue[:RESIDUE_MAX_LINES]
    return {"mapped": mapped, "residue": residue, "overflow": overflow,
            "n_residue": len(residue)}


def dropped_clause(overflow) -> str:
    """The receipt's sentence about the notes the cap took out, QUOTED.

    NOTHING IS DROPPED IN SILENCE (a fence, proven by removal)
    ----------------------------------------------------------
    The migration keeps at most `RESIDUE_MAX_LINES` free-text lines and takes
    the rest out of the notes file. `undo` puts them back byte for byte - but
    a reader who is not told anything was lost will never say `undo`, and a
    receipt that reports only how many notes MOVED reads like a clean
    migration. So the receipt names the dropped lines in the reader's own
    words. If this clause is ever removed, stop dropping instead."""
    rows = list(overflow or ())
    if not rows:
        return ""
    quoted = "; ".join('"%s"' % (r["text"] if isinstance(r, dict) else r)
                       for r in rows)
    return (" %d %s did not fit and %s no longer in your notes: %s."
            % (len(rows), "line" if len(rows) == 1 else "lines",
               "it is" if len(rows) == 1 else "they are", quoted))


def migrate_directives(workspace_root, *, now_iso=None,
                       triggered_by: str = "customize my morning brief",
                       apply: bool = True) -> dict:
    """Turn the free-text brief directives into settings, leaving at most five
    lines of free text behind.

    One batch covers both halves - the settings write and the trim of the notes
    file - so a bare `undo` puts the whole migration back: the previous config
    exactly, and the notes file byte for byte."""
    from atomic_write import atomic_append_jsonl
    from skill_custom_writer import remove_directive
    plan = plan_migration(workspace_root)
    if not apply or (not plan["mapped"] and not plan["overflow"]):
        plan.update({"ran": False, "batch_id": None,
                     "receipt": "Nothing in your notes needed moving.",
                     "changed": {}})
        return plan
    batch_id = mint_batch_id(now_iso)
    ts = _now_iso(now_iso)
    path = _custom_path(workspace_root)
    prev_text = path.read_text(encoding="utf-8") if path.exists() else ""

    changes: dict = {}
    for row in plan["mapped"]:
        if row["axis"] == "filters":
            changes["filters"] = _merge_filters(changes.get("filters"), row["value"])
        else:
            changes[row["axis"]] = _merge(changes.get(row["axis"]), row["value"])
    applied = apply_settings(workspace_root, changes, triggered_by=triggered_by,
                             now_iso=ts, batch_id=batch_id,
                             origin="migrated_from_notes") if changes else {
        "changed": {}, "surfaces": []}

    for row in plan["mapped"] + plan["overflow"]:
        if row.get("id"):
            remove_directive(workspace_root, RESIDUE_SKILL, row["id"],
                             source_skill="morning-briefing")

    new_text = path.read_text(encoding="utf-8") if path.exists() else ""
    if new_text != prev_text:
        atomic_append_jsonl(
            Path(workspace_root) / "_hq" / "data" / "events.jsonl",
            {"ts": ts, "type": "skill_customization_updated",
             "source_skill": "morning-briefing",
             "data": {"skill_name": RESIDUE_SKILL,
                      "brain_batch_id": batch_id,
                      "brain_change_class": RESIDUE_CHANGE_CLASS,
                      "custom_skill": RESIDUE_SKILL,
                      "prev_custom_text": prev_text,
                      "directive_count": len(plan["residue"]),
                      "triggered_by": triggered_by}})

    n = len(plan["mapped"])
    kept = len(plan["residue"])
    receipt = ("Moved %d of your standing notes into settings; %d %s still free "
               "text."
               % (n, kept, "line is" if kept == 1 else "lines are"))
    receipt += dropped_clause(plan["overflow"])
    receipt += " Say `undo` to put your notes back exactly as they were."
    plan.update({"ran": True, "batch_id": batch_id, "receipt": receipt,
                 "changed": applied.get("changed", {}),
                 "surfaces": applied.get("surfaces", []),
                 "dropped": [r["text"] for r in plan["overflow"]],
                 "n_dropped": len(plan["overflow"]),
                 "undo": "Say `undo` to put your notes back exactly as they were."})
    return plan


__all__ = [
    "AXES", "CONVERSATION_ORDER", "KEEP_PHRASES", "SURFACES", "SURFACE_BRIEF",
    "SURFACE_CLOSE", "TEMPLATES", "CHANGE_CLASS", "RESIDUE_CHANGE_CLASS",
    "RESIDUE_MAX_LINES", "BOT_CHANNEL_MAX_LINES", "WRAP_HEADINGS",
    "BriefSettingsError", "MissingAxisDefault",
    "axis_names", "axis_default", "defaults", "describe", "value_phrase",
    "resolve", "stored_axes", "stated_axes", "settings_for_fire",
    "condition_in",
    "apply_residue", "residue_lines",
    "parse_sentence", "negation_in", "is_keep", "validate_value",
    "template_names", "template_settings",
    "group_rows", "render_surface", "render_for_fire", "shape_line_cap",
    "wrap_group_section",
    "conversation_step",
    "mint_batch_id", "receipt_line", "apply_settings", "apply_sentence",
    "apply_template", "reset_settings",
    "plan_migration", "migrate_directives", "dropped_clause",
]
