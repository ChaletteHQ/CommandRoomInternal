#!/usr/bin/env python3
"""ROUTEMISS1 — the routing-correction writer, and the two helpers its verb
handler leans on.

WHY
---
workspace-manager promised for five months that a routing correction ("no, I
meant X") would be appended to `_hq/ROUTER_MISSES.md` and "reviewed weekly".
No trigger, no writer, no reviewer, no file on any workspace: a prose contract
with no code chokepoint. This module is the chokepoint. The verb handler in
`skills/workspace-manager/SKILL.md` ("Routing corrections") calls
`log_router_miss` as a SIDE EFFECT of re-dispatching the CEO's real request —
the miss is never the answer, the answer is.

WHAT IS WRITTEN — and what is deliberately not
---------------------------------------------
One `router_miss` event per correction, through `event_gate.append_event`
(the one append path), shaped by `build_router_miss_event`:

    {said, meant, resolved_to, routed_to, source: "user", session_ref}

  said         the CEO's full correction turn, verbatim
  meant        the remainder that was re-dispatched ("prep me for my 2pm")
  resolved_to  the skill folder the remainder routed to
  routed_to    the skill that took the PREVIOUS turn — known ONLY when that
               turn was a scheduled fire (the newest event on the log is a
               `pack_run`; its task_id is the answer). Otherwise null. There
               is no session-local state in a Cowork chat, and the previous
               turn's skill is not derivable from the correction phrase, so
               this field is never guessed (DD-2). `previous_turn_skill`
               is the ONLY legal source.
  source       always "user" — a correction is the CEO's word
  session_ref  the chat's session/receipt pointer when one exists, else null

`said` / `meant` are the CEO's OWN WORDS — they may name people, companies,
anything. Two consequences, both enforced elsewhere and restated here so a
reader of this file cannot miss them:
  * the only consumers are OWNER-facing (the `_hq/views/ROUTER_MISSES.md`
    view via `render_router_misses` and cleanup's Monday-note line). Nothing
    org-, board-, client- or external-facing reads this type.
  * a payload NEVER becomes a `tests/triggers.yaml` row without a human
    rewrite into placeholder vocabulary. `tests/gen_negative_rows.py` reads
    FENCES from skill descriptions and never reads events.

`split_correction` recognises the correction shapes the verb handler owns and
returns the remainder to re-dispatch. Recognising a shape here is NOT firing:
the handler's fences (fire only when the remainder routes to a DIFFERENT skill
than the one that just ran; never after a draft or a disambiguation question)
are prose, decided by the model with the conversation in view, and this module
does not second-guess them.

stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

EVENT_TYPE = "router_miss"
SOURCE = "user"

# The correction openers the verb handler owns. `no, I meant` is recognised
# HERE (so the handler can strip it) but is deliberately NOT a trigger stem
# anywhere in a skill description or the trigger corpus: as a positive stem it
# would collide with every "no I meant <real request>" phrase, whose owner is
# the skill the remainder routes to. The bare stems that DO route to
# workspace-manager ('wrong skill', 'you routed that wrong', 'that should have
# been') live in its body Routing corpus.
_OPENERS = [
    r"no[,!.\s]*\s*i\s+meant\b",
    r"no[,!.\s]*\s*i\s+mean\b",
    r"that\s+should\s+have\s+been\b",
    r"that\s+should\s+(?:have\s+)?gone\s+to\b",
    r"you\s+routed\s+that\s+wrong\b",
    r"wrong\s+skill\b",
]
_OPENER_RE = re.compile(
    r"^\s*(?P<opener>" + "|".join(_OPENERS) + r")\s*[:,\-—]?\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
# A leading connective on the remainder ("wrong skill — I wanted the brief",
# "that should have been a prep brief") is noise, not intent.
_REST_LEAD_RE = re.compile(
    r"^(?:[:,\-—\s]+|(?:i\s+(?:wanted|want|asked\s+for|was\s+asking\s+for)\s+)"
    r"|(?:the|a|an)\s+)+",
    re.IGNORECASE,
)


def split_correction(text: str) -> Optional[dict]:
    """Recognise a correction turn. Returns {opener, remainder} or None.

    `remainder` is what the handler re-dispatches through normal trigger
    matching AS THE REQUEST. An empty remainder ("wrong skill.") is still a
    recognised correction — the handler then has nothing to re-dispatch and
    must ASK what was meant rather than log a miss with an empty `meant`.
    """
    if not isinstance(text, str):
        return None
    m = _OPENER_RE.match(text)
    if not m:
        return None
    rest = m.group("rest").strip()
    rest = _REST_LEAD_RE.sub("", rest, count=1).strip()
    rest = rest.strip(" .!?\"'")
    return {"opener": m.group("opener").strip(), "remainder": rest}


def build_router_miss_event(said: str, meant: str, resolved_to: str, *,
                            routed_to: Optional[str] = None,
                            session_ref: Optional[str] = None,
                            source_skill: str = "workspace-manager") -> dict:
    """The event envelope (no seq/ts — the writer lock stamps them).

    Refuses an empty `meant` or `resolved_to`: a miss that cannot say what was
    meant or where it went is not a miss, it is a question the handler should
    have asked. `routed_to` is stored exactly as given (null stays null) — the
    caller gets it from `previous_turn_skill` and nowhere else.
    """
    if not isinstance(said, str) or not said.strip():
        raise ValueError("router_miss needs `said` — the correction turn itself")
    if not isinstance(meant, str) or not meant.strip():
        raise ValueError("router_miss needs a non-empty `meant` — ask, don't log")
    if not isinstance(resolved_to, str) or not resolved_to.strip():
        raise ValueError("router_miss needs `resolved_to` — the skill the "
                         "remainder actually routed to")
    if routed_to is not None and (not isinstance(routed_to, str)
                                  or not routed_to.strip()):
        raise ValueError("routed_to is a skill/task id or None — never ''")
    data: dict[str, Any] = {
        "said": said.strip(),
        "meant": meant.strip(),
        "resolved_to": resolved_to.strip(),
        "routed_to": routed_to.strip() if routed_to else None,
        "source": SOURCE,
        "session_ref": (str(session_ref).strip() or None) if session_ref else None,
    }
    return {"type": EVENT_TYPE, "source_skill": source_skill, "data": data}


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def previous_turn_skill(workspace_root) -> Optional[str]:
    """The ONLY legal source for `routed_to`.

    Returns the task id of the newest event on the log IF AND ONLY IF that
    event is a `pack_run` — i.e. the last thing that happened in this
    workspace was a scheduled fire, whose output is what the CEO is
    correcting. Anything else (a chat turn, an older receipt, an empty log,
    an unreadable log) returns None: `routed_to` is unknown, and the view
    says so. Never inferred from the phrase.
    """
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:
        return None
    if not events:
        return None
    newest = events[-1]
    if not isinstance(newest, dict) or newest.get("type") != "pack_run":
        return None
    data = newest.get("data") or {}
    task = data.get("task_id") if isinstance(data, dict) else None
    if isinstance(task, str) and task.strip():
        return task.strip()
    src = newest.get("source_skill")
    return src.strip() if isinstance(src, str) and src.strip() else None


def log_router_miss(workspace_root, said: str, meant: str, resolved_to: str, *,
                    routed_to: Optional[str] = None,
                    session_ref: Optional[str] = None,
                    source_skill: str = "workspace-manager") -> dict:
    """THE writer. Appends one `router_miss` through event_gate.append_event.

    Pass `routed_to=previous_turn_skill(ws)` — or leave it None. Returns the
    written event (seq/ts stamped). The caller acknowledges in one clause
    inside the real answer and never narrates the event name.
    """
    from event_gate import append_event

    ev = build_router_miss_event(said, meant, resolved_to, routed_to=routed_to,
                                 session_ref=session_ref,
                                 source_skill=source_skill)
    written = append_event(_events_path(workspace_root), [ev],
                           holder=f"router_miss:{source_skill}")
    return written[0]


__all__ = [
    "EVENT_TYPE", "SOURCE", "split_correction", "build_router_miss_event",
    "previous_turn_skill", "log_router_miss",
]
