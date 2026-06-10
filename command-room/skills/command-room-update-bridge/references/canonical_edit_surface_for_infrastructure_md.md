# Canonical replacement — workspace `_hq/INFRASTRUCTURE.md` "Plugin source-of-truth rule" section

> **Purpose:** the exact text the `canonical_edit_surface_infrastructure_md` migration (Phase 4.5) surfaces for the user to copy-paste into their workspace `_hq/INFRASTRUCTURE.md`.

When the migration fires, the bridge tells the user to find the section in `_hq/INFRASTRUCTURE.md` starting with `## Plugin source-of-truth rule` (or the older `## Plugin source-of-truth rule (effective 2026-04-26)`) and replace it through the next `## ` heading or `---` divider with the block below.

The replacement also includes a brief update to the preceding "Architectural debt" paragraph that referenced the retired pattern — surface that as a secondary copy-paste if the user wants the doc fully consistent.

---

## Plugin source-of-truth rule (Option B, established 2026-05-12)

**The staging marketplace clone IS the canonical edit surface for the Command Room plugin.** Path: `~/.claude/plugins/marketplaces/commandroom1/command-room/` on every machine where Cowork is installed (Cowork installs the marketplace clone as part of personal-plugin install). It's a git clone of `chaletteholdings/commandroom1`, M's private staging repo — edits land directly and push to GitHub. There is no separate Drive-edit folder; the retired earlier-folder pattern was deprecated 2026-05-12.

**Why the change:** the pre-2026-05-12 model was "edit on Drive, mirror to the staging clone before pushing." Drive sync was async + eventually-consistent → drift between PC and laptop, drift between Drive working copy and staging clone, drift between staging clone and GitHub. Each drift incident took manual reconciliation to recover (v2.7.4 ↔ v2.7.6, v2.7.7 surgical merge, etc.). The staging clone IS git — exact, atomic, multi-machine consistent. Editing it directly collapses three layers into one.

**The production clone** at `~/.claude/plugins/marketplaces/commandroom/command-room/` remains off-limits for direct edits. It's the deployment target for paying clients. `ship-cr-plugin`'s `promote` mode mirrors staging → production after operator confirmation. Editing the production clone directly is the only way to ship inconsistent code to clients.

**Distribution model:**
- Two private repos under `chaletteholdings`: `commandroom1` (M's staging) + `commandroom` (paying-client production).
- Per-version repos (`commandroom2177` etc.) retired 2026-05-07 and frozen. New ships do NOT create new repos.
- `marketplace.json` `version` field deliberately OMITTED — including it breaks Cowork's Update button (field acts as Cowork's update-detection cache key; field absent → Cowork falls back to commit-SHA detection which works on every push).
- Distribution is private + GitHub-collaborator-gated. Clients pay → added as collaborator → install → revoke = remove collaborator.

**Ship ritual (full detail in chalette plugin's `ship-cr-plugin` SKILL.md + the staging repo's `DEVELOPMENT.md`):**
- Pull staging clone fresh → status check (no unexpected dirty state) → release-readiness inspect → version bump + release notes from M → write CHANGELOG.md + plugin.json → write release manifest at `shared/releases/v<X.Y.Z>.json` (mandatory since v0.4.1 of ship-cr-plugin) → commit + push to staging → tell M what to do in Cowork.
- Production promote is a separate command (`promote v3.X.Y`). Mirrors staging → production repo, commits + tags + pushes.

**Canonical references:**
- In the plugin: `references/HOW_COMMAND_ROOM_WORKS.md` Section 6 (orientation overview).
- In the staging repo root: `DEVELOPMENT.md` (the operational guide for agents about to edit).
- In M's workspace: this file (`_hq/INFRASTRUCTURE.md`) + the workspace-level CLAUDE.md.

**If the rule gets broken** (someone edits the production marketplace clone directly, or recreates a separate Drive-edit folder): a `ship-cr-plugin status` check surfaces the drift. Reconciliation: copy any direct-production edits back into staging, re-promote; delete any rogue Drive-edit folder.

---

## Optional secondary: update the preceding "Architectural debt" paragraph

If the user's INFRASTRUCTURE.md has an "Architectural debt RESOLVED 2026-04-26" paragraph just above the source-of-truth rule, that text is also outdated. Replace it with:

```markdown
**Architectural debt RETIRED 2026-05-12 (Option B move):** the prior pattern — a separate Drive-edit folder mirrored to the staging clone — drifted between v2.7.4 → v2.7.6, required a surgical merge at v2.7.7, and continued to cause periodic drift events through April. The 2026-05-12 Option B move retired the Drive-edit folder entirely; the staging marketplace clone IS now the canonical edit surface (see rule below). That collapses three layers (Drive working copy → staging clone → GitHub) into one, which eliminates the entire drift class rather than just preventing it.
```
