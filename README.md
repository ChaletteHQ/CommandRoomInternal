# Command Room — canonical marketplace

Private GitHub marketplace for the Command Room plugin. Single home for all releases going forward — every new version ships to this repo, clients install once and use Cowork's Update button to pull new versions.

## Install

For paying Chalette clients with collaborator access:

1. In Cowork, link GitHub: Customize -> Connectors -> GitHub -> connect.
2. Customize -> Personal plugins -> "+" -> Add marketplace.
3. URL: `https://github.com/chaletteholdings/commandroom` -> Sync.
4. Install the Command Room plugin from the marketplace it just added.

## Updates

When a new version ships:

1. Customize -> Personal plugins -> Command room.
2. Click **Check for updates**, then click **Update** when the button activates.

That's it — no need to re-add the marketplace.

## Migrating from per-version repos

If you previously installed Command Room from a per-version repo (`commandroom2177`, etc.):

1. Remove the old marketplace from Customize -> Personal plugins.
2. Add this canonical marketplace following the Install steps above.
3. Your workspace data (entities.json, events.jsonl, _hq/, etc.) is unaffected — it lives in your workspace folder, not in the plugin.

The per-version repos remain frozen as historical pins; existing installs continue to work but will not receive new versions.

## Current version

See `command-room/.claude-plugin/plugin.json` (`version` field) for the current release. Full version history in `command-room/CHANGELOG.md`. Tagged releases are published on this repo's GitHub releases page.

## Access

Private repo. Read access granted to paying Chalette clients via GitHub collaborator invite. For access, contact Chalette Holdings directly.

For use by paying Chalette clients only. No redistribution.
