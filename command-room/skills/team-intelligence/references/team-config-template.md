# Team Configuration — _team-config.md Template

> Controls how the team-intelligence skill behaves. Lives at `_people/_team-config.md`.
> Edit this file to customize prep format, staleness rules, and tracked people.

## Roster

<!-- All tracked team members. Filename must match the PERSON.md file in _people/. -->

| Name | Filename | Role | Added |
|------|----------|------|-------|
| | | | |

## Prep Format

<!-- What the CEO wants in 1:1 prep briefs. Uncomment/reorder to customize. -->

Default prep sections (in order):
1. Since last time (what's happened since last logged interaction)
2. Open commitments (with overdue flags)
3. Across projects (their status in each project they touch)
4. Fresh intel (new email/Slack/calendar activity not yet logged)
5. Flags (anything the CEO flagged)
6. Suggested talking points (generated from data)

<!-- To remove a section, delete or comment it out. To reorder, renumber. -->

## Staleness Rules

| Rule | Threshold | Action |
|------|-----------|--------|
| No interaction | 14 days | Flag in team overview |
| Commitment overdue | 3 days | Flag in team overview + 1:1 prep |
| Profile untouched | 30 days | Suggest review with CEO |
| Flag age | 14 days (3+ sessions) | Prompt CEO to clear or convert to Working Style note |

## Notification Preferences

<!-- Where team-related flags show up -->

- **Team overview:** Always (on "my team" / "team status")
- **Daily briefing ("what's going on"):** Include overdue commitments + stale interactions
- **1:1 prep:** Full detail for that person
- **cleanup:** Include team health section
