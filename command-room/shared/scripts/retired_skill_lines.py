#!/usr/bin/env python3
"""The one sentence Command Room says for a retired skill's old phrase (cr1#552).

THE RULING (the owner, 2026-10-28, settled in the triage review of
2026-11-06). Three skills are retired: `show-my-list` (the discuss-later list,
drain-only since MLK1), `balance` (the personal white-space surface) and
`board-pack-assembler`. Their folders are gone. A phrase a customer learned
still gets an answer, one sentence, from the skill that owns the phrase now:
`show-my-reminders` for the list and the mute ledger (the other personal
lane), `workspace-manager` for the other two (the catch-all). This module is
where the sentences live, so the skills, the update bridge and the suite read
one copy.

Shape borrowed from `level_up_lines` (the retired dashboards): constants, no
workspace access, nothing in the text that names a file, a script or an event.
3.10-safe, stdlib only.
"""

from __future__ import annotations

from typing import Dict

#: The three retired skills and the skill that answers their phrases now.
RETIRED_SKILLS: Dict[str, str] = {
    "show-my-list": "show-my-reminders",
    "balance": "workspace-manager",
    "board-pack-assembler": "workspace-manager",
}

#: `show my list` and its family. The items that were still open were moved
#: by the update bridge (`release_actions.discuss_list_retired_v1`), so the
#: sentence can say where they went.
DISCUSS_LIST_RETIRED = (
    "Your discuss-later list is retired. Anything still on it became a note "
    "on its person or project, or a task on My Plate, the last time Command "
    "Room updated. Say `what's on my plate` for your tasks, or `show my "
    "reminders` for your reminders."
)

#: `balance check` and its family.
BALANCE_RETIRED = (
    "Balance is retired. Say `who should I reach out to` for the people worth "
    "a note this week, or `remind me about [thing] on [day]` to keep a "
    "personal evening in view."
)

#: `build the board pack` and its family, the tune and customize phrases
#: included.
BOARD_PACK_RETIRED = (
    "The board pack is retired. Say `board update` for a board memo in your "
    "voice, or `one-pager on [topic]` for a one-page brief."
)

_LINES: Dict[str, str] = {
    "show-my-list": DISCUSS_LIST_RETIRED,
    "balance": BALANCE_RETIRED,
    "board-pack-assembler": BOARD_PACK_RETIRED,
}


def line_for(skill: str) -> str:
    """The sentence for a retired skill's phrase; "" for a skill that is not
    retired, so a caller surfaces nothing rather than inventing a line."""
    return _LINES.get(str(skill or "").strip(), "")


def owner_of(skill: str) -> str:
    """The skill that answers a retired skill's phrases; "" when not retired."""
    return RETIRED_SKILLS.get(str(skill or "").strip(), "")


__all__ = [
    "BALANCE_RETIRED", "BOARD_PACK_RETIRED", "DISCUSS_LIST_RETIRED",
    "RETIRED_SKILLS", "line_for", "owner_of",
]
