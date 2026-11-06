# Connectors

## How connectors work

The Command Room plugin gets smarter the more tools you connect. Each connector gives Claude access to a different type of context — email threads, calendar events, Slack conversations, documents, meeting transcripts, and more.

Connect tools during onboarding (the Claude app's Settings → Connectors) or add them anytime later. Skills automatically use whatever's connected and skip what's not — no errors, no reconfiguration needed.

## Available Connectors

| Category | Tools | What It Unlocks |
|----------|-------|-----------------|
| Email | Gmail, Outlook, Superhuman | Read threads, draft responses, search history. Powers call prep, briefings, and "what did they say about X." |
| Calendar | Google Calendar, Outlook Calendar, Superhuman (the same connection that reads your mail also reads your calendar — nothing extra to connect) | See upcoming meetings, who's on the call, recurring workstreams. Powers meeting prep and time awareness. |
| Chat | Slack, Microsoft Teams | Read channels, search history, send messages. Powers cross-tool search and team communication. |
| Cloud Storage | Google Drive, OneDrive, Box, Dropbox | Search and read docs. Powers "find that document about X." |
| Meeting Transcripts | Granola | Auto-pull meeting transcripts. No more pasting — meetings get processed automatically. |
| E-Signature | DocuSign | Route documents for signature, track envelopes, check agreement status. |
| Design | Canva | Generate designs, manage brand assets, create presentations. |

## Which connectors matter most

**Start here (high impact):**
- Email + Calendar — enables meeting prep, email search, briefings, and brand voice capture
- Chat — enables cross-platform search and team communication

**Add when ready:**
- Cloud Storage — enables document search and project file awareness
- Meeting Transcripts — automates meeting note processing (highest compounding value)
- E-Signature — enables contract and agreement workflows
- Design — enables visual content creation

## How Command Room picks the right tool (connector-agnostic-v1)

Skills never name a provider's tool. They express intent ("draft a reply",
"find availability Tue–Thu") and a resolver maps it to whatever you've
connected, matched by the connector's **server-id**, not by sniffing a name
like "gmail" (which breaks for Superhuman and any modern connector). The
authoritative registry of what each provider can do is the capability manifest
(`shared/data-schemas/connector_capabilities.json`) — both this catalog and the
resolver read from it, so they can't drift apart. A connector that only reads
(no send/draft) degrades gracefully to "here's the text to paste," never an
error. Which mailbox each connected account is *for* — and whether it files into
your business records — is governed by the account map (see the account-scope
model); a personal account can show in your brief without ever entering the CRM.

## Kinds, dials and lists (cr1#667, rulings of 2026-11-03)

Every declared connector carries a **kind**, and Maintenance keys every read on
the kind, the connector's role and its dials, never on a provider name or a
free-text description (a prose setting drifts; a dial holds). The vocabulary
and the defaults live in the capability manifest
(`shared/data-schemas/connector_capabilities.json`, key `kinds`); the table
below is a rendering of it and the battery checks the two agree.

Beside its kind, each connector carries five **dials** and two **lists**:

- `open_commitments`: may this connector open commitments
- `close_commitments`: may it close them
- `close_mode`: a matched close lands as a `close` or as a `propose` (the owner confirms)
- `update_people`, `update_projects`: is it read for people and for projects
- `include` and `exclude`: names at the kind's grain (channels for chat, boards
  and projects for tasks, labels or folders for mail and documents, calendars
  for calendar). An excluded name is never read; with an include list, only
  the listed names are read. A name matches an item by its display name or
  its id, case and a leading `#` ignored, so a channel the owner names in
  words matches the connector's name for it.

A connector with `open_commitments` off is read for people and projects only.
Every Maintenance job reads the dials and the lists before it reads the source
(`shared/scripts/connector_reads.py`, `for_job`), and records a dial that
turned it away as a skip on its own receipt.

Defaults when the owner says nothing (grain in the second column):

| Kind | Grain | Open | Close | Close mode | People | Projects |
|---|---|---|---|---|---|---|
| `mail` | labels or folders | on | on | close | on | on |
| `calendar` | calendars | on | on | close | on | on |
| `chat` | channels | on | on | close | on | on |
| `meetings` | meeting sources | on | on | propose | on | on |
| `tasks` | boards and projects | on | on | propose | on | on |
| `documents` | labels or folders | off | on | propose | on | on |
| `deals` | pipelines | off | on | propose | on | on |
| `contracts` | folders | off | on | propose | on | on |

The `email` key in `workspace.connectors` is the `mail` kind; the key itself
is unchanged. A record that carries none of these fields reads as its kind's
defaults, so an existing workspace keeps today's behaviour exactly and nothing
migrates.

**One writer.** Set-up turns the owner's words into these values, shows them
back as one table for confirmation, and writes them through
`connector_config.set_connector_settings`. A later change is one sentence in
chat ("ignore the marketing board", "a task closed there closes the
commitment", "do not open commitments from chat") through the same writer,
never an edit by hand; "also exclude the marketing board" extends the list
(the writer's merge form) rather than replacing it. Every Maintenance job
that reads a connector source is named in the reader's map
(`connector_reads.JOB_READS`) with the kinds it reads, and guard G84 refuses
a job that reads a source without calling the reader first. The writer refuses an unknown kind, an unknown dial,
a non-boolean dial, a `close_mode` outside `close` / `propose`, or a scope
outside `account` / `desktop_only`.

**Reachability.** A scheduled fire sees the connectors of the account that
registered the schedules. A connector attached only in the desktop app is
declared with `scope: desktop_only` and is not reachable from a fire. Each
Maintenance run reads every declared connector it can reach and its receipt
names the ones it could not (`connectors_unreachable`); on a cloud fire the
connectors are the container's and the receipt says so
(`connector_scope: container`, otherwise `computer`).
