# Canonical replacement — workspace CLAUDE.md "Plugin source-of-truth rule" section

> **Purpose:** the exact text the `canonical_edit_surface_claude_md` migration (Phase 4.5) surfaces for the user to copy-paste into their workspace `CLAUDE.md`.

When the migration fires, the bridge tells the user to find the section in their `CLAUDE.md` starting with `## Plugin source-of-truth rule` (or an older variant such as `## Plugin source-of-truth rule (Option B, established 2026-05-12)` or `## Plugin source-of-truth rule (non-negotiable)`) and replace it through the next `## ` heading or `---` divider with the block below.

---

## Plugin source-of-truth rule (cr1 model, current as of 2026-06-22)

**The canonical edit surface for the Command Room plugin is `~/repos/cr1-canonical/command-room/`** — a dedicated git clone of **`ChaletteHQ/cr1`**, the private staging repo. Edits land there directly and push to `cr1`. ALWAYS verify against this clone before building — it is the only authoritative state.

> **DO NOT edit any Command Room clone under `~/.claude/plugins/marketplaces/`.** Those are Cowork's locally-installed copies — read-only install caches. The legacy staging marketplace clone's remote was renamed to `oldtest` and retired on 2026-06-22; push only to `ChaletteHQ/cr1`. Editing a marketplace clone is a dead end — those changes never reach the push flow.

**Distribution / production:** `ship-cr-plugin`'s `promote` mode fans the core out from staging (`ChaletteHQ/cr1`) to every per-client repo under `ChaletteHQ` (`CommandRoomInternal` plus one `CommandRoom<Client>` repo per client) via `scripts/promote_core_to_clients.py`, honoring each client's `_chalette/overrides.json` so custom skills are never clobbered. `marketplace.json` `version` field is deliberately OMITTED — including it breaks Cowork's Update button (the field acts as Cowork's update-detection cache key; field absent → commit-SHA detection works on every push).

Full enforcement + ship ritual live in the plugin itself at `references/HOW_COMMAND_ROOM_WORKS.md` Section 6 and the staging repo's `DEVELOPMENT.md`. Workspace-side details in `_hq/INFRASTRUCTURE.md`.
