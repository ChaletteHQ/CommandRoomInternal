# Slack button → `apply choices:` bridge — listener contract (SPEC_SLACK1 C-3)

> **Audience: the Slack listener (bot.py) implementer.** This is the wire
> contract between the plugin's Block Kit emissions
> (`widget_transport.render_and_persist(target="slack")`) and the listener's
> interactivity handlers. Nothing in this document is plugin-side work — the
> plugin's half already ships in `shared/scripts/slack_render.py` and
> `shared/CHAT_ACTION_WIDGET.md` § Transport. **The receiving skill
> (`apply-choices`) is UNCHANGED and must stay unchanged** — its statelessness
> ("every action carries its source id") is the entire reason this port needs
> zero skill edits.

## 1. What the plugin hands you

`render_and_persist(target="slack")` returns:

```json
{
  "blocks":     [ ...Block Kit... ],
  "text":       "<mrkdwn fallback digest — use as chat.postMessage `text`>",
  "pagination": { "page": 1, "total_pages": 3, "...": "..." },
  "path":       "<audit file — already persisted, do not re-write>"
}
```

Post `blocks` + `text` verbatim via `chat.postMessage` (or
`chat.startStream`/`appendStream` for the agent-experience surfaces). Never
edit, trim, or re-order blocks — the zero-manipulation contract is
surface-independent. Optionally run Slack's `blocks.validate` before posting;
the plugin already enforced the same limits, so a failure there is a bug
report, not something to route around.

## 2. Element vocabulary — what a click carries

Every interactive element the emitter produces carries a JSON **wire tuple**
in its `value` (buttons) or selected option `value` (static_selects):

```json
{"n": 1, "action": "send", "src": "commitments"}
{"n": "7a", "action": "mark received", "src": "commitments"}
{"n": 2, "action": "push to [date]", "src": "inbox"}
```

This is byte-for-byte the tuple shape the Cowork widget's Apply batch carries
(`shared/CHAT_ACTION_WIDGET.md` § Submission format). `n` may be an int, a
sub-lettered string (`"7a"`), or a prefixed id (`digest:...`) — treat it as
opaque and pass it through VERBATIM (the F2 identity contract: never
re-derive, abbreviate, or substitute an id).

`action_id` prefixes (routing only — the payload of record is the `value`):

| action_id prefix | Element | Meaning |
|---|---|---|
| `cr_verb_*` | button | a row's primary verb (Send / Draft / Done / …) |
| `cr_more_*` | static_select | a row's tail-verb dropdown ("— more —") |
| `cr_sub_*` | static_select | a sub-item's verb dropdown |
| `cr_apply_all` | button (footer) | compose + inject the batch (see §4) |
| `cr_snooze_rest` | button (footer) | select `skip` for every unanswered row, then apply |

## 3. Selection accumulation (listener state)

Slack has no client-side widget state, so the all-batch model moves to the
listener: keep a per-message selection map keyed by
`(channel, message_ts)` → `{n: wire_tuple}`. A `cr_verb_*` click or a
`cr_more_*`/`cr_sub_*` selection UPSERTS that row's tuple (one armed verb per
row — a new pick replaces the old, exactly like the dropdown on desktop).
Acknowledge the interaction (ack + optional ephemeral "armed: Send on 1") —
do NOT dispatch anything yet.

**Input-bearing verbs.** An `action` whose string carries a bracket
placeholder (`push to [date]`, `resolved [reason]`, `context [text]`,
`theirs to [name]`, …) requires input before it can dispatch. On selection,
open a **modal** with one free-text field (natural-language dates are the
norm: "monday at 2", "5" — never a strict date picker, CONTRACT Rule 7;
2026 modals also fit the confirm-with-edit upgrade later). On submit,
substitute the input into the action string exactly where the bracket was —
`push to [date]` + "monday at 2" → `"action": "push to monday at 2"` — which
is precisely what the desktop widget does. An empty submit on a REQUIRED
input holds that row un-armed and says why (the F-17 no-silent-swallow rule).

## 4. Apply — composing the identical wire string

On `cr_apply_all`, compose ONE message from the accumulated tuples, ordered
by `n`:

```
apply choices: [{"n":1,"action":"send","src":"commitments"},{"n":"7a","action":"mark received","src":"commitments"}]
```

- Prefix is the literal `apply choices: ` — the `apply-choices` skill fires
  ONLY on this exact prefix followed by a JSON array.
- Tuples ride VERBATIM as accumulated (no re-derivation, no enrichment).
- `cr_snooze_rest`: first fill every row that has no selection with
  `{"n": <n>, "action": "skip", "src": <src>}`, then compose the same way.
- Inject the composed string into the Claude session as a user prompt.
  **Session resume is by `thread_ts`**: the click may land hours after the
  post (the evening click on the 7:15 AM brief is the NORMAL case, not the
  edge) — resume the session bound to that thread; `src`-first dispatch
  inside apply-choices makes the late click safe by design.
- Clear the selection map for that message after injection; the apply-time
  response (a NEW blocks payload from the same transport) is the next surface.

## 5. Deliverable files (C-4)

Skill output on Slack never carries `computer://` URLs. When a fire produces
a document, the chat text names it as a plain bold headline
(`→ *Call Prep — Acme Co*`) and the LISTENER uploads the file into the thread
via `files.upload` (`thread_ts` of the surface message). Heavy-docs posture:
Slack triggers the build, the file lands in the thread, reading happens at a
desk.

## 6. Runtime declaration (B-3)

The listener's session bootstrap MUST:

1. Append `SURFACE=slack` to the system prompt it hands Claude, and
2. Export `SURFACE=slack` in the service environment (scripts read it via
   `shared/scripts/surface_context.py`; unset ≡ cowork, which would render
   desktop HTML into your channel).

Also export `CR_WORKSPACE` (clock-trust G23) and keep `CLAUDE_PLUGIN_ROOT`
untouched — the Rule 22 preamble resolves through it natively on the VM.

## 7. What the listener must NOT do

- Never compose or edit Block Kit for skill surfaces by hand — every payload
  comes from the transport (the FS-08 rule, surface-independent).
- Never synthesize an `apply choices:` tuple a click didn't produce, and
  never batch across DIFFERENT source messages (one message = one page-set =
  one batch).
- Never dispatch a closing verb without its id verbatim (the orphan-tombstone
  class: 74 closures that matched nothing came from re-derived ids).
- Never auto-send anything: `send` reaching apply-choices is the user's
  click, and the plugin-side draft posture (drafts, confirm gates) stays the
  authority. The out-of-model confirm-button gate (Workstream E-1) layers on
  top of this bridge later; leave room for a `cr_confirm_*` action_id family.
