# Connectors

## How connectors work

The Command Room plugin gets smarter the more tools you connect. Each connector gives Claude access to a different type of context — email threads, calendar events, Slack conversations, documents, meeting transcripts, and more.

Connect tools during onboarding (Settings → Connectors) or add them anytime later. Skills automatically use whatever's connected and skip what's not — no errors, no reconfiguration needed.

## Available Connectors

| Category | Tools | What It Unlocks |
|----------|-------|-----------------|
| Email | Gmail, Outlook, Superhuman | Read threads, draft responses, search history. Powers call prep, briefings, and "what did they say about X." |
| Calendar | Google Calendar, Outlook Calendar, Superhuman | See upcoming meetings, who's on the call, recurring workstreams. Powers meeting prep and time awareness. |
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
