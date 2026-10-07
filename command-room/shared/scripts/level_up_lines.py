#!/usr/bin/env python3
"""The one sentence Command Room says about dashboards (Night M3, RETIRE1 R-1).

THE RULING (R-M3-4, D-5). The pinned sidebar dashboards — Workspace Map, Quick
Commands, My Open Commitments — are retired on EVERY seat. The merged Claude
app has no sidebar to pin them into, and keeping a second install path alive on
the older desktop seats would leave two products answering one phrase two ways.
So every phrase that used to install, refresh or rebuild a dashboard now gets
the same answer, on every seat: dashboards live in chat, and here is what to
say instead.

This module is where that answer lives, as constants, so the skill that owns
the phrases (`level-up-command-room`) and the update bridge's Phase 4 say it
from one home and the suite can pin both against it.

`plan(tools)` is the whole behaviour of the retired surface, as data: whatever
tool list the seat can see — the old sidebar verbs included — the plan says the
one sentence and calls NOTHING. `answer(installed_ids)` adds the one unpin
sentence for a workspace whose own history shows a dashboard was installed
once (read, never written).

`bridge_plan(...)` is the update bridge's Phases 1-4 as data: nothing to install
(`CURRENT_DEFAULTS` is empty), nothing called, the up-to-date exit reachable,
and nothing logged when an update is declined. It also carries the bridge's
intent (fix pass 2, review F-A): `bridge_intent(<the phrase that fired the
bridge>)` reads the bridge's own phrase table, and the artifact-only intent —
the old dashboard-install phrases — plans the one sentence and NO phase at all.

3.10-safe, stdlib only, no workspace access: it composes, it never reads.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

#: The ONE sentence, on every seat, for every dashboard phrase.
DASHBOARDS_IN_CHAT = (
    "Dashboards live in chat now. Say `list active projects` for your "
    "Workspace Map, `triage my commitments` for your open commitments, and "
    "the quick commands work just by saying them."
)

#: Said once more, only for a workspace whose history shows a dashboard was
#: installed at some point — the pinned copy stays where the customer pinned
#: it until they remove it, and it no longer refreshes. HYGIENE3 (ruling
#: R-REV-RETIRE1-1): the word "sidebar" is retired from customer sentences,
#: and the sentence invites no phrase — nothing in Command Room answers
#: "unpin", so it names no command to say.
UNPIN_NOTE = (
    "Dashboards pinned from an earlier version no longer refresh; you can "
    "remove them whenever you like."
)


def answer(installed_ids: Optional[Iterable[str]] = None) -> str:
    """The whole answer: the sentence, plus the unpin note when the workspace
    ever installed a dashboard (`installed_ids` = the `artifact` values of its
    `artifact_installed` history)."""
    ids = [i for i in (installed_ids or ()) if isinstance(i, str) and i.strip()]
    if ids:
        return DASHBOARDS_IN_CHAT + " " + UNPIN_NOTE
    return DASHBOARDS_IN_CHAT


def plan(tools: Optional[Iterable] = None,
         installed_ids: Optional[Iterable[str]] = None) -> Dict[str, object]:
    """What the retired surface does on a seat that can see `tools`.

    `{"say": <the answer>, "calls": []}` — for EVERY tool list, including one
    that still carries the old sidebar create/update/list verbs. There is no
    branch on the tool list: a sidebar that is still there is not a reason to
    install into it (R-M3-4).
    """
    del tools  # deliberately unread — see above
    calls: List[str] = []
    return {"say": answer(installed_ids), "calls": calls}


#: The Layer 1 default dashboards the update bridge installs: NONE (fix pass 1,
#: review H-2). Every `update command room` computed `missing_defaults =
#: CURRENT_DEFAULTS - <installed>`; with the two sidebar ids still listed and
#: nothing writing `artifact_installed` any more, that set was never empty, so
#: the bridge's up-to-date exit could never be reached and every run offered a
#: sidebar install. Empty, the up-to-date exit is reachable again.
CURRENT_DEFAULTS: frozenset = frozenset()

#: The bridge's up-to-date exit (Phase 2), said when nothing is pending.
UP_TO_DATE = (
    "You're all set. Your workspace is current, and there's nothing "
    "pending. Nothing to update."
)


#: The update bridge's intent table (fix pass 2, review F-A). The bridge's
#: dispatch paragraph above Phase 1 Step 0 and its Phase 4.7 intent table name
#: exactly these phrases; `run_retire1_test` pins the text to them.
INTENT_FULL_UPDATE = "full_update"
INTENT_ARTIFACT_ONLY = "artifact_only"
BRIDGE_INTENTS = (INTENT_FULL_UPDATE, INTENT_ARTIFACT_ONLY)

#: The full-update phrases (Phase 4.7's intent table, full-update row).
FULL_UPDATE_PHRASES = (
    "update my command room", "update command room", "what's new",
    "whats new", "whats new in command room", "check for updates",
    "install latest", "install the latest",
)

#: The old dashboard-install phrases: they still route to the bridge
#: (`tests/triggers.yaml`), and they hear the one sentence and nothing else.
ARTIFACT_ONLY_PHRASES = (
    "install my dashboards", "install dashboards",
    "install missing dashboards", "set up my dashboards",
    "add my dashboards", "i'm missing dashboards",
)

#: What the full-update intent runs, in order — every phase of the bridge,
#: as today. (`exit: "up_to_date"` is an early stop inside Phase 2, after the
#: Phase 4.7 state-gated blocks; it does not change which phases the intent
#: owns.)
FULL_UPDATE_PHASES = ("1", "2", "3", "4", "4.4", "4.4b", "4.5", "4.6",
                      "4.7", "4.8", "5", "6")


def _normalize_phrase(phrase: object) -> str:
    text = str(phrase or "").replace("’", "'").lower()
    text = " ".join(text.split())
    return text.strip(" .!?,;:")


def bridge_intent(phrase: object) -> str:
    """The intent of the phrase that fired the update bridge.

    A full-update phrase anywhere in it wins (someone who says "update
    command room and install my dashboards" asked for the update). Otherwise
    an old dashboard-install phrase makes it `artifact_only`. Anything else
    that reached the bridge is the full update, as it always was.
    """
    text = _normalize_phrase(phrase)
    if any(p in text for p in FULL_UPDATE_PHRASES):
        return INTENT_FULL_UPDATE
    if any(p in text for p in ARTIFACT_ONLY_PHRASES):
        return INTENT_ARTIFACT_ONLY
    return INTENT_FULL_UPDATE


def missing_defaults(installed_ids: Optional[Iterable[str]] = None) -> List[str]:
    """`CURRENT_DEFAULTS` minus what the workspace's history says it installed.
    Always empty now — the set is empty — and kept as a function so the
    bridge's rule has one home the suite can drive."""
    have = {i for i in (installed_ids or ()) if isinstance(i, str)}
    return sorted(CURRENT_DEFAULTS - have)


def bridge_plan(tools: Optional[Iterable] = None,
                installed_ids: Optional[Iterable[str]] = None, *,
                pending_migrations: Iterable = (),
                pending_remediations: Iterable = (),
                same_version: bool = True,
                state_blocks_surfaced: bool = False,
                intent: str = INTENT_FULL_UPDATE) -> Dict[str, object]:
    """The update bridge's Phases 1-4, as data (fix pass 1, review H-2).

    `tools` is deliberately unread, exactly as in `plan`: a sidebar verb the
    seat can still see is never a reason to call it.

    `intent` (fix pass 2, review F-A) is `bridge_intent(<the phrase>)`. The
    artifact-only intent is the whole turn: `exit: "dashboards_only"`,
    `say: [answer(installed_ids)]` — the bare sentence, since the bridge's
    dispatch reads nothing before it answers — and `run_phases: []`. No
    phase runs: no root guard, no version check, no migration, no heal, no
    Phase 4.7, no event. An intent that is not in `BRIDGE_INTENTS` raises.

    The full-update intent's answer (`run_phases` = `FULL_UPDATE_PHASES`):

    - `calls`: always `[]` — the bridge installs, lists and verifies nothing.
    - `missing_defaults`: always `[]` (see `CURRENT_DEFAULTS`).
    - `exit`: `"up_to_date"` when nothing is missing, no workspace migration
      or release remediation is pending, the version has not moved and the
      Phase 4.7 state-gated blocks surfaced nothing; else `"proceed"`.
    - `ask`: True only when a workspace migration is pending — the one
      confirmation is about migrations; there is no dashboard to confirm.
    - `say`: `[UP_TO_DATE]` on the up-to-date exit; otherwise Phase 4's answer
      (the dashboards sentence, plus the unpin note for a workspace whose
      history shows an install).
    - `log_on_decline`: None — a declined update logs nothing (the old
      `plugin_update_deferred` row carried `missing_defaults`, which is gone).
    """
    del tools  # deliberately unread — see above
    if intent not in BRIDGE_INTENTS:
        raise ValueError(f"unknown bridge intent: {intent!r}")
    missing = missing_defaults(installed_ids)
    calls: List[str] = []
    if intent == INTENT_ARTIFACT_ONLY:
        return {"exit": "dashboards_only", "ask": False, "calls": calls,
                "missing_defaults": missing, "say": [answer(installed_ids)],
                "log_on_decline": None, "run_phases": []}
    migrations = list(pending_migrations or ())
    remediations = list(pending_remediations or ())
    up_to_date = (not missing and not migrations and not remediations
                  and bool(same_version) and not state_blocks_surfaced)
    phases = list(FULL_UPDATE_PHASES)
    if up_to_date:
        return {"exit": "up_to_date", "ask": False, "calls": calls,
                "missing_defaults": missing, "say": [UP_TO_DATE],
                "log_on_decline": None, "run_phases": phases}
    return {"exit": "proceed", "ask": bool(migrations), "calls": calls,
            "missing_defaults": missing, "say": [answer(installed_ids)],
            "log_on_decline": None, "run_phases": phases}


__all__ = ["ARTIFACT_ONLY_PHRASES", "BRIDGE_INTENTS", "CURRENT_DEFAULTS",
           "DASHBOARDS_IN_CHAT", "FULL_UPDATE_PHASES", "FULL_UPDATE_PHRASES",
           "INTENT_ARTIFACT_ONLY", "INTENT_FULL_UPDATE", "UNPIN_NOTE",
           "UP_TO_DATE", "answer", "bridge_intent", "bridge_plan",
           "missing_defaults", "plan"]
