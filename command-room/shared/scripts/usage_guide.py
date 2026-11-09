#!/usr/bin/env python3
"""The usage guide (cr1#187): one habit per way of working, phrases that route
well, and how a promise gets on and off the list.

One text, two doors: a section of the Hub's Settings tab (`feed_settings`) and
the answer to a typed "how do I use this" (`command-room-coach`). The surfaces are
today's: the Hub, Morning Brief, Maintenance and typed asks. What it knows
about the owner is not written here: it is `profile.trust_map`, read fresh, so
it cannot go stale. `profile.explain` stays the answer to "how do you know
that".

Each phrase names the skill it reaches; `run_usage_guide_test.py` holds each one
to that skill's own trigger list, so a phrase here cannot drift from the route.
Written for the owner, in plain words. stdlib only at import.
"""
from __future__ import annotations

from typing import Any, Dict, List

WAYS: List[Dict[str, Any]] = [
    {"way": "The Hub",
     "habit": "Open it once in the morning and press Start. Go through the tabs from left to right, "
              "then press Apply so your choices reach your records.",
     "phrases": [("where is my hub", "cockpit-setup"), ("show my settings", "cockpit-setup")]},
    {"way": "Morning Brief",
     "habit": "Read it before your first meeting. The Hub's Today tab holds the same morning, with up to three questions you can answer there.",
     "phrases": [("brief me on today", "morning-briefing"), ("morning briefing", "morning-briefing")]},
    {"way": "Maintenance",
     "habit": "Leave it on. It runs through the day to keep your records tidy, and the Hub tells you if it needs you.",
     "phrases": [("is everything running", "system-health"), ("what did you change", "system-health")]},
    {"way": "Typed asks",
     "habit": "Say what you want the way you would say it to a colleague, and name the person or the meeting.",
     "phrases": [("prep me for my 2pm", "call-prep"), ("draft an email to Sam about the proposal", "email-writer"),
                 ("who is Sam", "people-crm")]},
    {"way": "Project work",
     "habit": "Say go and the project's name first, so what you say next lands in the right place.",
     "phrases": [("go Acme", "workspace-manager"), ("what's on my plate", "commitment-triage")]},
    {"way": "Deep work",
     "habit": "Ask for the finished piece in one go, and say who will read it.",
     "phrases": [("memo on the pricing change", "memo-writer"), ("research Acme", "research")]},
]

LOOP_TITLE = "How a promise gets on and off your list"
LOOP = ("It picks promises out of your meetings and mail and puts them on Commitments. If one is wrong, say so "
        "on the row and it learns. A promise closes when you press Done and Apply, when you say it is done, when "
        "it sees the work happen in your mail or calendar, or after a long quiet spell. Settings shows "
        "whether the quiet spell and your own word are on. What it closed on its own in the last week is on the Week tab, where you can put it back.")
ASK_WHY = ("To see where a promise came from, open its row. To see what it knows about you, say \"my profile\", "
           "then ask \"how do you know that\" about any line.")
KNOWN_TITLE = "How sure it is about you"
_BAND_WORDS = {"reliable": "you told it", "my read": "it worked this out from what you did", "not yet": "nothing told or learned yet"}


def known(workspace_root) -> List[Dict[str, str]]:
    """What it knows about the owner, section by section, from
    `profile.trust_map`. Empty when the profile cannot be read: the guide
    stands without it."""
    from profile import trust_map

    try:
        return [{"title": str(r.get("title") or ""), "line": _BAND_WORDS.get(str(r.get("band") or ""), "")}
                for r in trust_map(workspace_root)]
    except Exception:  # noqa: BLE001 - the habits do not depend on it
        return []


def as_text(doc: Dict[str, Any]) -> str:
    """The same guide as chat text."""
    lines: List[str] = []
    for w in doc["ways"]:
        lines.append(f"{w['way']}: {w['habit']}")
        lines.append("Try: " + "; ".join(f'"{p}"' for p in w["phrases"]))
        lines.append("")
    lines += [doc["loop_title"] + ".", doc["loop"], "", doc["ask_why"]]
    if doc["known"]:
        lines += ["", doc["known_title"] + ":"]
        lines += [f"{k['title']}: {k['line']}" for k in doc["known"]]
    return "\n".join(lines)


def guide(workspace_root=None) -> Dict[str, Any]:
    """READ. `{ways: [{way, habit, phrases}], loop_title, loop, ask_why,
    known_title, known, text}`: the Settings section's fields, and `text`, the
    words a typed "how do I use this" says. One source for both."""
    doc: Dict[str, Any] = {
        "ways": [{"way": w["way"], "habit": w["habit"], "phrases": [p for p, _skill in w["phrases"]]} for w in WAYS],
        "loop_title": LOOP_TITLE, "loop": LOOP, "ask_why": ASK_WHY, "known_title": KNOWN_TITLE,
        "known": known(workspace_root) if workspace_root else [],
    }
    doc["text"] = as_text(doc)
    return doc


__all__ = ["WAYS", "LOOP", "LOOP_TITLE", "ASK_WHY", "KNOWN_TITLE", "known", "as_text", "guide"]
