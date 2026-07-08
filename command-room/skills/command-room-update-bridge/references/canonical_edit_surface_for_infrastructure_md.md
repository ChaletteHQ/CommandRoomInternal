# Canonical replacement — workspace `_hq/INFRASTRUCTURE.md` "Plugin source-of-truth rule" section

> **Purpose:** the exact text the `canonical_edit_surface_infrastructure_md` migration (Phase 4.5) surfaces for the user to copy-paste into their workspace `_hq/INFRASTRUCTURE.md`.

When the migration fires, the bridge tells the user to find the section in `_hq/INFRASTRUCTURE.md` starting with `## Plugin source-of-truth rule` (or an older variant such as `## Plugin source-of-truth rule (Option B, established 2026-05-12)` or `## Plugin source-of-truth rule (effective 2026-04-26)`) and replace it through the next `## ` heading or `---` divider with the block below.

The replacement also includes a brief update to the preceding "Architectural debt" paragraph if it referenced a retired pattern — surface that as a secondary copy-paste if the user wants the doc fully consistent.

---

## Plugin source-of-truth rule (cr1 model, current as of 2026-06-22)

**The canonical edit surface for the Command Room plugin is `~/repos/cr1-canonical/command-room/`** — a dedicated git clone of **`ChaletteHQ/cr1`**, the private staging repo. Edits land there directly and push to `cr1`. ALWAYS verify against this clone before building — it is the only authoritative state.

**Why the change:** the marketplace clones under `~/.claude/plugins/marketplaces/` are Cowork's locally-installed copies — install caches coupled to the Cowork install location, not working clones. Editing one is a dead end: those changes never reach the push flow, and the copy goes stale the moment staging moves ahead (the legacy staging marketplace clone sat at 4.0.0 while `cr1` moved on). Its remote was renamed to `oldtest` and retired on 2026-06-22 — push only to `ChaletteHQ/cr1`. A dedicated `~/repos/` working clone is exact via git, multi-machine consistent, and independent of where Cowork installs.

**Do not edit any marketplace clone.** Treat everything under `~/.claude/plugins/marketplaces/` as read-only.

**Distribution model (per-client, established 2026-06-10):**
- Staging: `ChaletteHQ/cr1` (private). The canonical edit surface clones this repo.
- Production: per-client repos under `ChaletteHQ` — `CommandRoomInternal` plus one `CommandRoom<Client>` repo per client. `ship-cr-plugin`'s `promote` mode fans the core out from staging to every per-client repo via `scripts/promote_core_to_clients.py`, honoring each client's `_chalette/overrides.json` so custom skills are never clobbered.
- `marketplace.json` `version` field deliberately OMITTED — including it breaks Cowork's Update button (field acts as Cowork's update-detection cache key; field absent → Cowork falls back to commit-SHA detection which works on every push).
- Distribution is private + GitHub-collaborator-gated. Clients pay → added as collaborator → install → revoke = remove collaborator.

**Ship ritual (full detail in chalette plugin's `ship-cr-plugin` SKILL.md + the staging repo's `DEVELOPMENT.md`):**
- Pull staging fresh → status check (no unexpected dirty state) → release-readiness inspect → version bump + release notes from the operator → write CHANGELOG.md + plugin.json → write release manifest at `shared/releases/v<X.Y.Z>.json` (mandatory since v0.4.1 of ship-cr-plugin) → commit + push to staging → tell the operator what to do in Cowork.
- Production promote is a separate command (`promote v3.X.Y`). Fans out staging → per-client repos, commits + tags + pushes.

**Canonical references:**
- In the plugin: `references/HOW_COMMAND_ROOM_WORKS.md` Section 6 (orientation overview).
- In the staging repo root: `DEVELOPMENT.md` (the operational guide for agents about to edit).
- In the operator's workspace: this file (`_hq/INFRASTRUCTURE.md`) + the workspace-level CLAUDE.md.

**If the rule gets broken** (someone edits a marketplace clone directly, or recreates a separate Drive-edit folder): a `ship-cr-plugin status` check surfaces the drift. Reconciliation: copy any stray edits back into `~/repos/cr1-canonical`, push to `cr1`, re-promote; delete any rogue edit folder.

---

## Optional secondary: update the preceding "Architectural debt" paragraph

If the user's INFRASTRUCTURE.md has an "Architectural debt" paragraph just above the source-of-truth rule that still describes the Drive-edit or marketplace-clone-canonical eras, that text is also outdated. Replace it with:

```markdown
**Architectural debt RETIRED 2026-06-22 (cr1 move):** two prior patterns are retired — the Drive-edit folder mirrored to a staging clone (drift-prone; retired 2026-05-12) and the "Option B" model that named the staging marketplace clone canonical (coupled the edit surface to Cowork's install cache; its remote was renamed `oldtest` and retired 2026-06-22). The current model is a dedicated working clone at `~/repos/cr1-canonical/` of `ChaletteHQ/cr1` (see rule below) — exact via git, independent of the Cowork install location.
```
