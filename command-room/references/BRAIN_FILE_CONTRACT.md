# Brain File Contract (v3.16-pending)

Canonical rules for what lives in a `PROJECT_BRAIN.md` / `PROJECT_CONTEXT.md`,
what is **generated** from substrate vs **durable** hand-owned narrative, and
who regenerates when. Born out of the brain-substrate-drift audit (2026-05-30):
brains were copying volatile facts (people, status, open items, pricing) out of
the substrate and then freezing, while the substrate moved on — and they're
loaded at session start, so the staleness poisoned outputs.

Every skill that reads or writes a brain file MUST obey this contract.

---

## 1. Two kinds of content

**GENERATED** — volatile facts the substrate owns. Rendered from `events.jsonl`
/ `entities.json`, never hand-edited. Lives inside `LIVE-STATE` markers.

**DURABLE** — judgment, narrative, gotchas, framework notes, decisions-prose,
custom workflows. Hand-owned. The renderer NEVER touches a byte outside the
markers.

A brain file is: durable narrative + exactly one generated **Live State** block
(People + Status) + pointers for the cheap classes (commitments, pricing).

## 2. The marker convention

```
<!-- LIVE-STATE:people generated_at=<ISO> source_seq=<int> -->
...generated roster + status...
<!-- /LIVE-STATE:people -->
```

- Markers are HTML comments — invisible in rendered markdown.
- `source_seq` = the newest thread-tagged event seq the block was built from.
  It powers the dirty-check.
- Only `shared/scripts/render_brain_block.py::render_block` may write between
  the markers. It is atomic (fsync + rename), idempotent, and byte-preserves
  everything outside. If the markers are absent it does NOT guess an insertion
  point (returns `no_anchor`) — only the one-time migration creates them.

## 3. What is generated vs pointed-at

| Class | Treatment | Source of truth |
|---|---|---|
| People / roster | **GENERATED** (Live State block) | `thread_roster.derive_roster()` over `events.jsonl` |
| Thread status | **GENERATED** (Live State block) | `entities.json` thread `status` |
| Commitments / open items | **POINTER** — link to the live view, do not inline-copy | `cru_match.load_open_commitments` / `_hq/views/COMMITMENT_AGING.md` |
| Pricing / numbers | **POINTER** — link to the model file | the financial model (no substrate pricing record exists) |
| Everything else | **DURABLE** | the human |

Rationale: rendering an inline copy still leaves two representations to drift.
For the cheap classes, one representation (a pointer) beats two.

## 4. The roster (membership) model

- Membership is **recomputed every time** via `derive_roster`, never stored as
  a frozen `members[]` field (that re-creates the maintenance trap).
- It is **lineage-aware**: a thread spawned from / parented by an archived
  umbrella inherits the umbrella's people as `inherited` candidates (lower
  confidence, surfaced for the confirm-gate) so members tagged only to the
  pre-split umbrella are never silently dropped.
- Only **human overrides** persist, on the thread record:
  `roster_overrides: {pin: [...], suppress: [...]}`. Pins force-include a
  durable contact who has no events (e.g. a framework author referenced for
  context); suppresses kill cross-thread bleed the CEO rejected.
- `confidence` ∈ {high (≥2 direct events), low (1 direct), inherited (umbrella
  only), pinned}. Inherited/low candidates go through the confirm-gate
  (insight-generator weekly propose/confirm); high candidates render directly.

## 5. Who renders, and when

- **Primary trigger — the load path.** On `go [project]`, `workspace-manager`
  runs the **dirty-check** (`render_brain_block.needs_render`) and re-renders
  the Live State block when a thread-tagged event newer than `source_seq`
  exists. This runs on EVERY `go` (including the cached fast-path), because the
  check is cheap (one seq compare) — cadence-only demonstrably rots (the global
  views sat stale from 2026-05-10 because nothing re-fired them).
- **Backstop — the weekly sweep.** `cleanup` regenerates all view files and
  every live thread's Live State block. NOTE: this backstop is only real once
  `cleanup` is registered as a scheduled task (it is not, as of the audit).
- **Never** LLM-append volatile facts. `workspace-manager`'s "end session"
  stops appending People/Status prose; durable sections keep append behavior.

## 6. Migration (one-time, per workspace)

A `release_actions/` converter, run once inside `cleanup` on upgrade:
- wraps/replaces the hand People table + status prose with the Live State block;
- converts frozen `- [ ]` checklists + price prose to pointers;
- **preserves durable content byte-untouched**, including durable People rows
  that have NO events (e.g. a framework author who isn't a contact) — these are
  moved into the durable section or pinned, never deleted;
- must run AFTER any `workspace-ingest` parse (ingest reads commitments out of
  brains), and idempotently / dry-run-loggable.

## 7. Consumers that must use the live source (not the inline copy)

Before the inline commitment checklist is removed from brains, these readers
must be repointed to the live commitment view:
- `team-intelligence` (reads brain "open items" per person);
- `workspace-ingest` parsers (one-time ingest only — sequence converter after).

## 8. Integrity (fleet visibility)

`integrity_check.py` gains read-only checks: a Live State block older than the
newest thread-tagged event, and a brain status line that disagrees with
`thread.status`. These fail loudly so drift is visible across the fleet.
