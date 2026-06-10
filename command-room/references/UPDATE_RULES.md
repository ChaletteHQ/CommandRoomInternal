# Update Rules — Command Room Coordination

**Version:** 3.12.0 (refreshed from v1.0/v1.8 — see "Substrate note" below)
**Companion to:** `shared/WORKSPACE_API.md` (canonical writer contract), `references/DATA_CONTRACT.md` (schemas), `references/SOURCE_OF_TRUTH.md` (read/write convergence), `references/VIEW_GENERATION.md` (how views regenerate).

This file is the coordination layer. When multiple skills might want to update the same piece of context, these rules decide who writes, when, and how conflicts resolve.

### Substrate note (v3.12.0)

This file was originally written against the v1.8 markdown-substrate model (MASTER_TRACKER.md / PEOPLE.md / DECISION_LOG.md / ALIASES.md as primary stores). v2.2 moved the canonical substrate to JSON: `_hq/data/entities.json` + `_hq/data/events.jsonl` + `_hq/data/aliases.json`. The original markdown files are now **regenerated views** under `_hq/views/` (with v1.8-path mirrors at `_hq/MASTER_TRACKER.md` etc. for backward compatibility). The coordination principles below still hold — one owner per file, snapshot rules, conflict ladder — but where this document says "PEOPLE.md" / "MASTER_TRACKER.md" as a write target, the actual write goes to entities.json (canonical) and the view regenerates automatically.

---

## Principle: One Owner, Many Readers

Every context file has exactly one primary owner skill (see WORKSPACE_API.md § File Ownership Map). The owner is responsible for:

1. Creating the file if it doesn't exist.
2. Validating its shape against DATA_CONTRACT.md.
3. Performing snapshot updates (replace whole file or whole sections).
4. Bumping the version header on every write.

Other skills can:

- **Read** the file freely.
- **Suggest** changes by surfacing them to the user. The owner skill picks them up next turn.
- **Append** only to files explicitly marked append-only (`SESSION_NOTES_[NAME].md`, `DECISION_LOG.md`, `CONFLICTS.md`, `intel/KNOWLEDGE_BASE.md`, `_hq/.write-log.md`).

Non-owner skills never perform snapshot writes. If `intel-intake` wants to flag a new person for `PEOPLE.md`, it doesn't write to `PEOPLE.md` — it surfaces "I saw a new person named X, want to add them?" The user's "yes" triggers `people-crm` on the next turn, which is the owner.

---

## Appender Rules (for append-only files)

Files that accept appends from multiple skills follow these rules:

1. **Prepare the entry in canonical form.** Use the Append Format in WORKSPACE_API.md (timestamp + source skill + content).
2. **Always append at the bottom of the current section, above any `---` separator.** Never reorder existing entries.
3. **Include a source tag.** Every entry says which skill wrote it, in the header line: `## [YYYY-MM-DD HH:MM] — [source-skill]`.
4. **No surgical edits to past entries.** To correct a past entry, append a new one that references the old by timestamp.
5. **Bump the version header.** Even append-only files carry a version; increment on every append.

---

## Snapshot Rules (for full-file updates)

Files owned by a single writer and updated as a whole (e.g., `MASTER_TRACKER.md`, `PEOPLE.md`, `CLAUDE.md`):

1. **Read the full current file.**
2. **Apply the change in memory.** Always apply to the full current content, never to a stale cached copy.
3. **Re-read the version marker immediately before writing.** If it changed since step 1, abort, re-read, retry.
4. **Write the full file with the bumped version.**
5. **Log the write to `_hq/.write-log.md`.**

---

## Surgical Edit Rules (for targeted changes)

Some files (BUSINESS_CONTEXT.md, PROJECT_CONTEXT.md, PROJECT_BRAIN.md) are stable but accept small targeted edits — e.g., "goal 2 changed to X." Use the Edit tool with an exact old_string → new_string, and:

1. Read the current file.
2. Verify the version hasn't changed.
3. Make the surgical edit. Bump the version comment.
4. If old_string is ambiguous (appears multiple times), widen the context until unique. Never use `replace_all` on these files.

---

## Conflict Resolution

A conflict happens when two skills try to write the same file in the same turn, or one skill writes while another skill's cached read becomes stale.

### Detection

At step 4 of the Write Protocol (WORKSPACE_API.md), the writer re-reads the version marker. A version mismatch = conflict.

### Resolution ladder

1. **Retry.** The most common case is a stale read from earlier in the same turn. Re-read fresh, re-apply the change, write. This usually succeeds.

2. **Merge if possible.** If two skills were both adding entries (append-only files), just append both in order. No actual conflict.

3. **User-mediated.** If two snapshot writers genuinely disagree (e.g., both want to set project status differently), log both to CONFLICTS.md and surface to the user: "Both meeting-notes and workspace-manager want to mark Project X — which is right?"

4. **Skip and log.** Last resort: skip the write, log to CONFLICTS.md, continue the turn. The user sees the conflict in the next cleanup.

### Anti-patterns (do not do)

- Silent overwrite — never write without a version check.
- Delete-and-rewrite — never drop a file to resolve a conflict. Always merge or skip.
- "Last writer wins" — never rely on timestamp alone. Always version-check.

---

## Cadence Rules

Some updates happen on a schedule, not on demand. These are the canonical cadences:

| Update | Trigger | Owner |
|---|---|---|
| `CLAUDE.md` surgical update (new person, new term, status change) | "end session" | `workspace-manager` |
| `CLAUDE.md` full regen | "regen my claude md" command OR version mismatch between CLAUDE.md and PEOPLE.md >7 days | `workspace-manager` |
| `MASTER_TRACKER.md` status updates | "end session" OR "update [project]" | `workspace-manager` |
| `PROJECT_BRAIN.md` auto-update | "end session" | `workspace-manager` |
| `PEOPLE.md` new entry | "add [person]" OR detected during ingest | `people-crm` |
| `DECISION_LOG.md` append | When a decision is captured (meeting-notes, workspace-manager, direct user) | `decision-log` (if standalone) OR the capturing skill |
| `ALIASES.md` append | Whenever a new raw→canonical mapping is resolved | `meeting-notes` or `people-crm` |
| `cleanup` report | Weekly (manual trigger or scheduled) | `cleanup` |

---

## Ingest → Persist Pipeline

This is the standard flow for any MCP-sourced data:

```
1. MCP delivers raw data
   (Gmail message, Granola transcript, Slack thread, Calendar event)

2. Ingest skill parses
   - meeting-notes for transcripts
   - intel-intake for articles/videos
   - (future) inbox-digest for email batches

3. Ingest skill canonicalizes
   - Every person → canonical via ALIASES.md → PEOPLE.md
   - Every project → canonical via MASTER_TRACKER.md
   - Every decision → decision record in DATA_CONTRACT shape

4. Ingest skill calls owner skills (implicit handoff)
   - "Decided to ship Q2" → DECISION_LOG append
   - "John mentioned a new vendor" → PEOPLE.md suggestion
   - "Project X status changed" → MASTER_TRACKER surgical edit

5. Each owner skill applies the Write Protocol
   - Read, canonicalize, version-check, write, bump, log

6. CONFLICTS.md captures any failures
   - cleanup surfaces them in the next report
```

Rule: an ingest skill never writes to `MASTER_TRACKER.md`, `PEOPLE.md`, or `CLAUDE.md` directly. It always routes through owner skills.

---

## Read-Before-Write Discipline

Every skill that modifies state follows this rhythm:

```
1. READ relevant current state (target file + any referenced files)
2. THINK through the change (what's actually new, what's already there)
3. CANONICALIZE (resolve entities)
4. VERIFY version hasn't moved
5. WRITE
6. LOG
```

Skipping any step breaks the contract. The most common violation: skipping step 3 (canonicalize) and writing a raw "John" to DECISION_LOG. That breaks cross-skill lookups days later.

---

## User-Triggered Overrides

The user can always override these rules by saying so explicitly:

- "Just add it" → owner skill proceeds without asking, assumes the user accepts canonicalization risk.
- "Don't canonicalize" → write as-is, flag in CONFLICTS.md for later resolution.
- "Force write" → skip the version check. **Logged prominently** in CONFLICTS.md with "user-override" tag.

These overrides exist because the user is the ultimate authority. The contract bends for them; it does not bend for skills.

---

## v2.0 Preview — Server-Side Coordination

In v2.0 (when storage moves to JSON + events.jsonl):

- Version checks use the JSON `version` field directly.
- Events are appended to `events.jsonl` with a monotonically increasing sequence number.
- Views are regenerated atomically (write to temp, rename) so readers never see a half-written view.
- The Writer Helper (still a markdown contract, no server) describes the JSON-specific syntax.

**Coordination rules in this file do not change.** The owner map, the retry ladder, the ingest pipeline — all identical. Only the storage substrate changes.

---

**End of update rules.**
