---
name: level-up-command-room
surfaces: cowork
slack_fallback: "Dashboards live in chat now. Say `list active projects` for your Workspace Map, `triage my commitments` for your open commitments, and the quick commands work just by saying them."
description: "Answers every sidebar-dashboard phrase with one sentence: dashboards live in chat now (the pinned Workspace Map, Quick Commands and My Open Commitments are retired on every seat). Fires on: 'level up command room', 'level up my command room', 'show me dashboards', 'what dashboards can I install', 'show me what I can enable', 'add more dashboards', 'level me up'. Workspace Map: 'install workspace map', 'enable workspace map', 're-install workspace map', 'rebuild workspace map', legacy 'install orgs map' / 'enable orgs map' / 'rebuild orgs map'. Quick Commands: 'install quick commands', 'enable quick commands', 'rebuild quick commands'. My Open Commitments: 'install my commitments', 'enable my commitments', 'rebuild my commitments dashboard', 'my commitments dashboard'. Does NOT fire on 'install command room' (command-room-onboarding), 'update command room' (command-room-update-bridge), or 'triage my commitments' (commitment-triage)."
---

# Level Up Command Room — dashboards live in chat now

The pinned sidebar dashboards are retired on every seat (Night M3, RETIRE1 —
ruling R-M3-4). The merged Claude app has no sidebar to pin them into, and the
older desktop app's sidebar is not kept as a second install path: one phrase,
one answer, everywhere. Nothing here installs, refreshes, rebuilds, verifies or
logs anything, and nothing here reads or writes the workspace.

## The whole answer

Every phrase this skill owns — the menu, and every Workspace Map, Quick
Commands and My Open Commitments phrase — gets this sentence, verbatim, once
(`level_up_lines.DASHBOARDS_IN_CHAT`):

> *"Dashboards live in chat now. Say `list active projects` for your Workspace Map, `triage my commitments` for your open commitments, and the quick commands work just by saying them."*

That is the entire turn. It is the same on a seat whose tool list still shows
the old sidebar verbs: a sidebar that is still there is not a reason to
install into it (`level_up_lines.plan` — whatever the seat can see, the plan
says the sentence and calls nothing).

If the customer asks about a dashboard that is still pinned from an earlier
version, add ONE sentence (`level_up_lines.UNPIN_NOTE`), verbatim:

> *"Dashboards pinned from an earlier version no longer refresh; you can remove them whenever you like."*

When another skill reaches this one silently (an older onboarding or update
flow that still names a dashboard install), do nothing and say nothing —
there is nothing to install.

**Output guard:** no internal tokens, paths, event names or version numbers.
The customer-facing names are **Workspace Map**, **Quick Commands** and **My
Open Commitments**; never an artifact id.

## What this skill never does

- Never installs, refreshes, rebuilds or verifies a dashboard, on any seat.
- Never writes a dashboard event of any kind; the ones already in the
  workspace's history stay there, unchanged, as history.
- Never offers a different dashboard, a workaround or a way to "turn the
  sidebar back on".

## Routing (full trigger corpus)

The complete trigger family and fences for this skill. The description is
budget-capped (G11); everything below remains binding at fire time and is
enforced mechanically by tests/triggers.yaml.

> The menu: 'level up command room', 'level up my command room', 'show me dashboards', 'what dashboards can I install', 'show me what I can enable', 'add more dashboards', 'level me up', 'my dashboards', 'sidebar dashboards'. Workspace Map (formerly enable-workspace-map): 'install workspace map', 'enable workspace map', 're-install workspace map', 'rebuild workspace map', 'refresh workspace map', 'install orgs map', 'enable orgs map', 'rebuild orgs map'. Quick Commands (formerly enable-quick-commands): 'install quick commands', 'enable quick commands', 'rebuild quick commands', 'refresh quick commands'. My Open Commitments (artifact id my-commitments): 'install my commitments', 'enable my commitments', 'rebuild my commitments dashboard', 'refresh my commitments dashboard', 'my commitments dashboard', 'my open commitments dashboard', 'install the commitments dashboard'. DOES NOT fire on 'install command room' / 'set up command room' (command-room-onboarding), 'update command room' / 'install my dashboards' / 'install dashboards' / 'install missing dashboards' / 'set up my dashboards' / 'add my dashboards' (command-room-update-bridge — it says the same sentence, and it is the ONE owner of every artifact-only phrase), 'triage my commitments' / 'show me my commitments' (commitment-triage — the actionable list), or 'set up command room schedules' (enable-command-room-schedules).
