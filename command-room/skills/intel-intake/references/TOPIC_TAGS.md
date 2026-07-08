# Intel Topic Tags — Canonical Vocabulary

Created P1.8 2026-07-02 — the SKILL.md validated tags against this file for
four releases while it didn't exist, so every workspace grew its own ad-hoc
vocabulary. This is the starter set; the extension rule below governs growth.

## Tags

| Tag | Covers |
|---|---|
| `ai-models` | Model releases, capabilities, pricing, benchmarks |
| `agents` | Agent frameworks, orchestration patterns, multi-agent design |
| `mcp` | MCP servers, connectors, protocol changes |
| `cowork-skills` | Skill patterns, plugin mechanics, Cowork platform features |
| `automation` | Workflow automation, Zapier/n8n-class tooling, scheduled tasks |
| `connectors` | Third-party integrations (mail, calendar, CRM, storage) — use INSTEAD of tool-specific tags like `mcp-connectors` |
| `market` | Industry news, competitor moves, funding, M&A |
| `pricing` | Pricing models, packaging, monetization patterns |
| `security` | Auth, data handling, compliance, risk |
| `client-playbook` | Patterns directly reusable in client engagements |
| `productivity` | Personal/executive workflow techniques |

## Extension rule

Before creating a new tag: (1) check this table for an existing tag that
covers it — near-duplicates ("mcp-connectors" vs `connectors`) are the failure
mode this file exists to stop; (2) if genuinely new, add the row HERE in the
same session you first use it, with a one-line "covers" entry. A tag used in
an intel file but absent from this table is a bug.

Workspace-specific tags (client names, product lines) are allowed — add them
under a `## Workspace tags` section that this plugin file seeds empty and the
workspace copy owns.
