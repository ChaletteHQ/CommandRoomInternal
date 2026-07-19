# Account Scope Contract — Layer B/C (connector-agnostic-v1)

**Status:** Phase-1 paper deliverable of the connector-agnostic build
(`HANDOFF_connector-agnostic-build-v2_2026-07-11.md`). Amends the two doctrines
it touches ONCE, in writing, before any skill Writer Contract prose is rewritten
(review requirement R1 — otherwise triage/brief prose gets rewritten twice).

This is the canonical home for: the two-dial account model, the named
business-substrate file list (R7), the surface-dial scope rule (R9), the
provenance-required event families the writer wall fails closed on (R2/R3), the
account classification state machine (R10), the in-place tombstone/scope-mask
rule (R5), the promote-queue (R8), and the honest statement of where each wall
is actually enforceable (R17).

Read alongside `WORKSPACE_API.md` (ownership + append protocol),
`PASSIVE_CAPTURE.md` (v3 — amended per R1 to consult this contract), and
`shared/scripts/capture_gate.py` (doctrine amended per R1).

> **Sequencing note.** The mechanism (`connector_config.py` account-map readers,
> the writer-side scope check) lands in Phases 1/3. This document is the spec
> those phases build to. Until Layer B ships and a workspace populates its
> account map, **an empty map = today's behavior** (R4): every account is
> treated as in-scope exactly as the plugin behaves today. Nothing here
> regresses a live client mid-upgrade.

---

## 1. The two-dial model (M, locked 2026-07-11)

Scope is **two independent dials per account** (and, optionally, per sender/
thread within an account), NOT one wall:

- **`surface`** — show this account's items in single-user ephemeral surfaces
  (morning brief, reminders, the daily action chats, triage list).
- **`write_to_business`** — file this account's items into the CRM + workspace
  substrate (the named file list in §2).

They are independent. Important personal mail (spouse, doctor, school) can be
`surface: on, write_to_business: off` — it shows in the brief but NEVER enters
business records.

**Default posture — "show liberally, file conservatively":**

| Dial | Default for a *classified* account | Why |
|---|---|---|
| `surface` | **liberal** — pull important personal items AND work-relevant mail in; user dials back | surfacing is freely reversible, so an inclusive default is safe |
| `write_to_business` | **conservative** — genuine work only (business-by-association: ties to a known contact / client domain / tracked project) | un-writing is messy under archive-never-delete; retroactive contamination is expensive (§6) |

**Fail-closed, non-negotiable in two places:** (a) a brand-new / unclassified
account is silent on **both** dials until the user assigns a role; (b) the
`write_to_business` dial is always fail-closed — when unsure whether something is
business, it stays out of the substrate.

### Account roles (spectrum)

`business-primary` (default outbound; both dials on) · `business-secondary`
(second business email; both on) · `mixed` (personal address carrying some
work — `write_to_business` on by association only, `surface` per user) ·
`personal` (both dials OFF by default; user opts specific senders into
`surface`) · plus functional roles `shared-support` · `billing` ·
`cold-outreach`.

### Compound account record (B1 — account × bindings)

Role and scope are properties of the **mailbox** (keyed on address); routing is
a property of the **connector instance** (keyed on server-id). One mailbox can
carry two bindings (M's `matthew@chaletteholdings.com` fronted by both Superhuman
and the old Gmail connector). Shape (lives in `entities.json` →
`workspace.accounts[]`, read by `connector_config.py`):

```jsonc
{
  "address": "matthew@chaletteholdings.com",   // the account key
  "account_id": "acct_<stable>",               // address-keyed stable id (R3)
  "role": "business-primary",
  "what_lives_here": "Chalette + Command Room business mail",
  "scope": { "surface": "on", "write_to_business": "on" },
  "routing": { "draftable_from": [...], "sendable_from": [...], "default_outbound": true },
  "send_as": [ { "address": "...", "signature": "..." } ],
  "overrides": { "senders": {}, "threads": {} },   // optional per-sender/thread scope
  "voice_register": null,                          // optional (N7 — deferred)
  "bindings": [
    { "server_id": "ec5e0bd5-...", "provider": "superhuman",
      "capabilities_ref": "superhuman", "binding_verified": "user_asserted" }
  ]
}
```

Read-scope keys on the **account**; tool routing keys on the **binding**. Some
connectors expose no whoami/profile tool (Gmail in-env has only search/get/draft/
label — H-A), so the address↔server binding may be `user_asserted` (unverified);
a fail-closed send from an unverified binding **degrades to paste-text** rather
than risk sending from the wrong account (§5).

---

## 2. "Business substrate" — the named file list (R7)

The `write_to_business` wall applies to **all** of the following. Anything an
account is out-of-scope for never enters these files:

| Substrate file | Where the wall ACTUALLY lives (be precise — a wrong attribution here is a bug) |
|---|---|
| `_hq/data/events.jsonl` — `interaction`, connector-derived `commitment`, `meeting`, person-enrichment events | **`account_scope_gate.enforce_scope`, called inside `atomic_append_jsonl`'s events.jsonl branch (atomic_write.py)** — the single append chokepoint every writer helper (`event_gate` / `capture_gate` / `sent_capture` / `slack_capture` / `meeting_capture`) funnels through |
| `_hq/data/entities.json` — people, orgs created from interactions | **`account_scope_gate.enforce_record_scope`, called by `people_writer.create_person` / `update_person` and `org_writer.create_org`** — fires only when the payload carries connector provenance (`provenance` / `source_ref` / `account_address` kwargs); manual adds pass. Callers deriving records from connector reads MUST pass the read's provenance |
| `_hq/data/aliases.json` — sender→canonical mappings inferred from mail | `people_writer.py` (`add_person_alias` targets an existing person record — the record wall gated its creation; alias adds themselves are manual/confirmed → pass) |
| `_people/[name].md` — person records + Interaction Log | transitive: composed from walled `entities.json` records + walled `interaction` events |
| `_hq/data/staging_emissions.jsonl` — dismissed draft text (EMAIL_DRAFT_PROTOCOL §4) | prose-mandated (capture path) — documented residue, not structurally walled |
| `_hq/data/sender-priority-rules.json` + `triage_feedback` events | events walled at the append chokepoint; the rules file is prose-mandated residue |
| `_hq/voice/corrections-*.jsonl` — voice-correction logs mined from sent mail | prose-mandated residue (sent-mail mining prose consults the account map; no structural hook) |
| `SESSION_NOTES_*.md` appends derived from connector reads | prose-mandated residue (`workspace-manager`, `meeting-notes`) |
| Regenerated views (`MASTER_TRACKER`, `PEOPLE`, `DECISION_LOG`, …) | transitive — derived from walled sources, inherit scope |

**Documented residue (walled at the read tier, not the write tier — see §5):**

- `pack_run.needs_attention_ids`, prep receipts, brief artifacts (the .docx and
  its state) — these are *composed from* already-scoped substrate, so they
  inherit scope transitively. A `surface:on, write_to_business:off` item can
  reach an ephemeral brief but MUST NOT reach an exportable artifact (§3).
- Scheduled-chat transcripts — a Cowork *session* setting the plugin cannot
  control (H-B). The writer-side ingest guard is the backstop; the honest
  answer for regulated clients is session hygiene (§5).

---

## 3. Surface-dial scope = single-user ephemeral surfaces only (R9)

The `surface` dial governs **single-user, ephemeral, reversible** surfaces
ONLY: the morning brief chat, reminders, the daily action chats, the triage
list. These are seen by the workspace owner alone and regenerate every fire.

**Exportable / multi-recipient artifacts draw ONLY from `write_to_business`-scoped
substrate — never from a `surface:on, write_to_business:off` item:**

- `weekly-recap`, `operator-report`, `board-pack-assembler`
- the brief **.docx** via `brief_writer` (the forwardable artifact — distinct
  from the ephemeral brief chat)
- any deliverable that leaves the owner's own screen

Rationale: a personal item surfaced into the owner's private brief is
low-stakes and reversible; the same item baked into a board pack or a forwardable
.docx is a privacy breach. Each composer skill states this in its Writer
Contract (Phase 2/3).

---

## 4. The writer wall — provenance-required event families (R2/R3)

The wall's job: **a personal-tagged (out-of-scope) account's mail can never
enter the business substrate.** The mechanism sits in the writer helpers (§2),
which every write funnels through.

### 4a. The fail-open inversion this fixes (R2)

The naive rule "reject an event whose provenance resolves out-of-scope" fails
**open**: most event families legitimately carry no provenance, so an LLM that
simply drops `source_ref` bypasses the wall silently. The fix is a two-list
enumeration:

**What is enforced, exactly** (this is the code's behavior in
`account_scope_gate._classify`, not an aspiration):

- `interaction` — **STRICT**: rejected when provenance is absent, AND rejected
  when provenance resolves to an out-of-scope account. Interactions are by
  definition connector reads; there is no manual variant.
- `commitment` — discriminated by **`data.origin`** (the reminders-lane
  precedent, `event_gate.REMINDER_ORIGIN`). Producers stamp at write time:
  connector capture paths (inbox-triage extraction, `sent_capture`,
  `slack_capture`, `meeting_capture`, the `capture_gate` promote path) stamp
  `origin: "connector"`; a chat-stated commitment ("I'll do X" typed to the
  agent) stamps `origin: "user_stated"`.
    - `origin == "connector"` → **STRICT** (reject on absent provenance +
      reject on out-of-scope — closes the R2 fail-open inversion for stamped
      producers).
    - `origin == "user_stated"` → exempt (never gated on provenance).
    - `origin` ABSENT → **legacy staging**: today's behavior (scope check only
      when the provenance carries a connector provider prefix), with a stderr
      warning. NOT hard-rejected yet — live producers lag the stamp; flip
      absent-origin to strict in a later release once the fleet stamps.
- `meeting` — **STRICT only when connector-sourced**: `origin == "connector"`
  → strict; provenance present without origin → scope check (out-of-scope
  rejects); provenance-less manual meeting logs (workspace-manager end-session
  review, "log the meeting") → exempt and MUST pass on classified workspaces.
- person-enrichment events (`person_created`, `person_updated`,
  `person_enriched`, `contact_email_captured`, `person_proposal`,
  `person_update_proposal`) — scope check **only when they carry provenance**
  (created FROM a connector read); a provenance-less manual add passes.

**Provenance-OPTIONAL families** (pass exactly as today — never gated on
provenance):

- `decision`, `note`, user-stated `commitment` (above), `reminder` (already
  user_explicit-only), workspace/lifecycle events, audit/receipt events, all
  schedule/onboarding types.

`capture_gate` already enforces the "observed items require provenance" half
(its `observed_id` path) — R2 generalizes that posture to the required families
above. `data.origin` is a data FIELD on existing event types, not a new event
type — nothing new to register (R14 unaffected).

### 4b. Provenance shape carries a stable `account_id` (R3)

Provenance becomes `{connector: <server_id>, provider, native_id, account_id}`.
`server_id` rotates on reconnect (CONTRACT Rule 22), so historical rows would
dangle for scope checks and reply-routing if keyed on it. `account_id` is
**address-keyed and stable** — the normalizer resolves it at read time from the
account map. Legacy rows (`gmail:<id>`, `gcal:<id>`, `slack:<permalink>`, bare
ids) carry no `account_id`; readers treat a missing `account_id` as
**in-scope** (back-compat — they predate the wall and must stay readable
forever; §6). The scope check applies to NEW writes.

### 4c. Where the map is empty (R4)

If the account map is empty (live client mid-upgrade, or a workspace that hasn't
onboarded Layer B), the wall is a **no-op**: every family writes exactly as
today. The wall only rejects once an account is classified out-of-scope.

---

## 5. Honest statement of enforcement strength (R17)

The privacy wall is **strong at the write/substrate tier and best-effort at the
read/brief tier.** State it plainly; do not describe it as absolute.

- **Substrate / CRM wall = structural, MODULO helper compliance.** Every write
  funnels through deterministic helpers, and the gate
  (`account_scope_gate.enforce_scope`) sits inside `atomic_append_jsonl`
  (**atomic_write.py** — the same chokepoint as the event gate and dedup hook)
  so all helper-mediated appends are gated. But: (i) the helper path is **prose-mandated**, not physically forced —
  the repo's own history (workspace-manager SKILL.md L154 citing v2.7–v2.10.4
  hand-rolled-write incidents) shows an LLM can improvise a raw write around the
  helper; (ii) `CR_EVENT_GATE=0` is a real escape hatch (emergency replay only).
  So the wall is **"structural modulo helper compliance."** It is a genuine
  upgrade over the prior boundary (`email_exclusion_rules` as prose in CLAUDE.md)
  because enforcement moved to the writer layer — but it is not a firewall.
- **Brief / chat-surface wall = prompt-tier (soft).** A validator can't know a
  paragraph in a brief originated from personal mail. Best-effort only.
- **The only TRUE brief wall is session hygiene** — the personal connector is
  simply not enabled on Command Room chats. This is the honest answer for
  compliance/healthcare clients (a flag on a read the agent can still make is
  weaker than the connector not being in the session at all). Reserved as the
  opt-in bulletproof mode for regulated clients.

Outbound send from an **unverified** binding (H-A) degrades to paste-text rather
than sending from a possibly-wrong account — fail closed, never fail wrong.

---

## 6. Retroactive contamination — in-place tombstone / scope-mask (R5)

When an account is classified **personal after the fact**, its historical
substrate rows are already in the CRM. The fix is an **in-place scope mask**
honored by readers — **NEVER a physical move of rows out of `events.jsonl`.**

Why not quarantine-move (this overrides the v1 E-2 recommendation): `events.jsonl`
is reference-dense — `seq` / `source_event_seq` chains, commitment ids referenced
by later closures, `(source_ref, title)` idempotency that re-arms if a row is
removed, and the self-healing `.source_refs.idx` sidecar all break if a valid row
is physically relocated.

Mechanism:

- A business→personal reclassification appends an `account_scope_masked` event
  `{address, masked_account_id, from_seq?, reason}`. Readers filter out rows
  whose resolved `account_id` matches a live mask (a scope-mask honor pass in
  people-view, the CRU projector, dormancy, relationship-moves).
- A personal→business restore appends `account_scope_restored` (un-mask) and
  offers a rescan of the previously-masked window.
- Masks are append-only and reversible, consistent with archive-never-delete.

**As implemented (2026-07-12 fix pass):** the shared helper is
`account_scope_gate.live_masks` / `filter_masked_events` (masks computed in
append order — latest mask/restore wins; a row matches on
`provenance.account_id`, else the id derived from `data.account_address` /
`data.from`). Wired into `cru_match.load_open_commitments` (the commitment/CRU
projector — every downstream chase surface inherits), `dormancy.
load_dormancy_signals`, and `render_people_view` (people-view); relationship-
moves consumes only those two filtered sources, so it inherits. Never-brick:
any mask-resolution failure returns the events UNFILTERED (a broken mask must
never blank a surface). **Honest limit:** a historical row is maskable only if
it carries account identity — account-stamped provenance or an account
address. Rows written before account stamping have no attribution and remain
visible; prospectively every connector write carries `account_id` via
`normalize_provenance`, so mask coverage is complete going forward.

---

## 7. Account classification state machine (R10)

All transitions are explicit. A missing transition is a bug.

```
                    (new server-id / address detected — connector_detected)
                                     │
                                     ▼
                              ┌─────────────┐
                              │ unclassified │  ← both dials OFF (fail-closed).
                              └─────────────┘     EXCLUDED from sent-mail scans
                                     │             (reconcile-sent, scan-for-
                (user assigns role;  │              commitments Sent pass) —
                 account_classified) │              §7a.
                                     ▼
                              ┌─────────────┐
                              │ classified   │  ← role + two dials set.
                              │  (role)      │     On classify: OFFER a scoped
                              └─────────────┘     backfill of the silent window
                                  │    ▲            (E-9 — user confirms; never
        business→personal         │    │ personal→business  silent).
    (account_role_changed +       │    │ (account_role_changed +
     account_scope_masked over    │    │  account_scope_restored +
     the silent+historical window)│    │  rescan offer)
                                  ▼    │
                              ┌─────────────┐
                              │ reclassified │
                              └─────────────┘
```

### 7a. Unclassified accounts are excluded from sent-mail scans

An `unclassified` account is invisible to `reconcile-sent` and the
`scan-for-commitments` Sent pass. Sent-mail scanning attributes commitments to
the owner; running it against an account of unknown role would file personal
sent mail as business commitments before the user ever classified it. Fail
closed: no scan until a role is assigned.

### 7b. On-classify backfill (E-9)

Between account-detection and classification there is a **silent window** —
reads happened but nothing was filed (or, pre-Layer-B, everything was filed).
When the user classifies an account business, OFFER a scoped scan of that window
("want me to backfill the last N days from this account?"). Never backfill
silently. When classifying personal, the silent-window rows are covered by the
`account_scope_masked` mechanism (§6).

---

## 8. The promote-queue (R8) — business-by-association bootstrap

**The deadlock:** person records are created FROM `interaction` events
(people-crm). But the `write_to_business` wall blocks `interaction` events for
**unknown senders** on mixed accounts (they don't yet tie to a known entity). So
a genuinely-new business contact on a mixed account never enters `entities.json`
→ is permanently treated as personal. Business-by-association starves itself.

**The fix:** mixed-account mail from a sender not in the entity graph goes to a
**propose-then-confirm review surface** ("looks like business — file it?"),
modeled on people-crm's existing org-domain inference (people-crm SKILL.md L286).
The user promoting a proposal creates the person record, which then makes future
mail from that sender in-scope by association. This is built **with Layer B**
(Phase 3), not deferred — without it the write dial is a one-way ratchet toward
"everything personal."

**As implemented (2026-07-12 fix pass):**

- **By-association mechanics** live in `account_scope_gate._out_of_scope`: on a
  `mixed` account whose write dial is off, an event referencing a resolved
  entity (`person_ids` / `data.counterparty_id`) passes; an unknown-sender
  event is walled.
- **Per-sender overrides** (`overrides.senders[addr].{surface,
  write_to_business}` on the account record) are honored on ANY role and
  written ONLY via `connector_config.set_sender_scope_override` (delegated
  setter). The promote-queue's demote path and the email_exclusion_rules
  migration both write these.
- **Proposals are `person_proposal` events with `data.promote_queue: true`**
  (an existing registered type — nothing new registered). The flag exempts the
  proposal from the write wall: the metadata-only review surface must be
  writable for exactly the accounts it reviews; the wall bites at PROMOTION.
- **Promotion is a user-confirmed manual add** — people-crm creates the person
  with NO provenance kwargs (the user is the authority; the record wall guards
  unconfirmed connector derivations).
- **Honest retention limit (closeout 2026-07-12):** a DECLINED proposal's
  sender name/email remains in `events.jsonl` under archive-never-delete —
  the demote override stops the proposal from ever re-firing or re-surfacing,
  but it does NOT scrub the original `person_proposal` row. The exposure is
  bounded (one metadata-only row per sender, never a body, never a person
  record), but it exists; say so if a privacy-sensitive client asks.
- Producer/confirm wiring: inbox-triage Writer Contract (proposal emission +
  surface line), people-crm Writer Contract (confirm/demote handlers).

**Correction loop (H-G):** the promote-queue is also the demote surface ("this
is actually personal"). Feedback teaches the classifier. The write dial stays
fail-closed throughout — a classification error hides business mail (safe) rather
than polluting records (unsafe).

**Product trade-off (E-5, flag in CHANGELOG + coach material):** because the
write dial is conservative on mixed accounts, last-touch / dormancy signal
weakens for out-of-scope senders on those accounts. This is the accepted cost of
"file conservatively."

---

## 9. What this contract does NOT change (YAGNI)

Per the handoff §8: no per-sender/per-thread scope override *matrix* at launch
(the per-account dials + the promote/demote queue suffice; the `overrides` field
in §1 exists but ships empty); no per-account voice registers (N7 — deferred);
no content-aware routing *logic* (the "what lives here" string ships as data
only). These are absorbable later without re-opening this contract.

---

**End of account scope contract.**
