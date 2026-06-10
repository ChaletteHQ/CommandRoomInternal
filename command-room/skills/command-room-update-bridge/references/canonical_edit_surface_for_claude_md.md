# Canonical replacement — workspace CLAUDE.md "Plugin source-of-truth rule" section

> **Purpose:** the exact text the `canonical_edit_surface_claude_md` migration (Phase 4.5) surfaces for the user to copy-paste into their workspace `CLAUDE.md`.

When the migration fires, the bridge tells the user to find the section in their `CLAUDE.md` starting with `## Plugin source-of-truth rule` (or the older `## Plugin source-of-truth rule (non-negotiable)`) and replace it through the next `## ` heading or `---` divider with the block below.

---

## Plugin source-of-truth rule (Option B, established 2026-05-12)

**The staging marketplace clone IS the canonical edit surface for the Command Room plugin.** Path: `~/.claude/plugins/marketplaces/commandroom1/command-room/` on every machine where Cowork is installed. It's a git clone of `chaletteholdings/commandroom1`, M's private staging repo — edits land directly and push to GitHub. There is no separate Drive-edit folder. (Pre-2026-05-12 the model was "edit in Drive, mirror to staging clone." Drive sync was async + eventually-consistent → drift between PC and laptop. The staging clone is git, which is exact.)

**The production clone** at `~/.claude/plugins/marketplaces/commandroom/command-room/` is off-limits for direct edits. It's the deployment target for paying clients. `ship-cr-plugin`'s `promote` mode mirrors staging → production after operator confirmation.

**Distribution model:** two private GitHub repos under `chaletteholdings` — `commandroom1` (M's staging, agents push directly) and `commandroom` (paying-client production, promoted from staging). Per-version repos like `commandroom2177` are retired and frozen. `marketplace.json` `version` field is deliberately OMITTED — including it breaks Cowork's Update button (the field acts as Cowork's update-detection cache key; field absent → commit-SHA detection works on every push).

Full enforcement + ship ritual live in the plugin itself at `references/HOW_COMMAND_ROOM_WORKS.md` Section 6 and the staging repo's `DEVELOPMENT.md`. Workspace-side details in `_hq/INFRASTRUCTURE.md`.
