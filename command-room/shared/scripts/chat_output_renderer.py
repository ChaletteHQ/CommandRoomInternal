#!/usr/bin/env python3
"""
Deterministic chat-output renderer for Command Room scheduled tasks.

WHY THIS EXISTS:

v2.7-v2.10.5 had every orchestrator instruct the LLM to follow ~12 chat-output
rules simultaneously (no engineer-speak, inline source links, item numbering,
blank lines, per-item pills, drop redundant slugs, Quick Read meta-commentary,
plain-English errors, tail-block discipline, bullet summaries, no phase
labels, real subject fallback). The LLM kept dropping rules under load —
entity-ID leaks, phase labels, dot-format actions, missing N. prefixes, empty
subjects all slipped through despite increasingly forceful prose instructions.

The architectural fix: take format rendering AWAY from the LLM. The LLM gathers
data, drafts emails, writes Quick Read commentary. It hands a structured data
view to this renderer. The renderer produces the exact chat string with rules
enforced, every time.

USAGE:

    from chat_output_renderer import render_chat_output
    output = render_chat_output(data_view)

DATA SHAPE (ChatOutput):

    {
        "header": "Inbox · Apr 28 · 5 priority threads. Drafts ready to review.",
        "sub_header": "Noise filtered (49 total): listings (24), ...",  # optional
        "sections": [
            {
                "title": "OVERDUE",  # None → no section header
                "count": 1,           # used for "(1)" suffix; None → omit
                "items": [Item, ...]
            }
        ],
        "quick_read": "items 1+2 are technical replies...",  # optional
        "bulk_actions": ["send all", "to drafts all", "show more", "skip all"],  # optional
        "save_confirmation": None,  # optional one-line save confirmation
    }

Item shape:

    {
        "n": 1,                       # global numbering, REQUIRED, even at N=1
        "icon": "✉",                  # optional emoji icon (✉ 📅 📄 👤 📁 ⚙)
        "name": "Sam Sample",    # display name; bold-rendered
        "subject": "Q2 deck",         # optional; rendered in quotes
        "context_tag": "replies your thread, 2 days aging",  # optional, after em-dash
        "metadata": [                 # ordered key-value list rendered as labels
            ("Subject", "Re: Q2 deck"),
            ("To", "sam@example.com"),
        ],
        "body_lines": [               # each line wrapped in italic
            "Hey Sam —",
            "Got your renderer-pipeline diagnostic.",
        ],
        "sources": [                  # inline clickable source links
            {"label": "the diagnostic dump", "url": "https://mail.google.com/..."}
        ],
        "artifact_link": {            # clickable .docx link, optional
            "label": "Open full brief",
            "path": "./Mira/meetings/Call_Prep_2026-04-28.docx"
        },
        "actions": ["1 send", "1 to drafts", "1 edit", "1 skip"],  # pill labels
        "sub_items": [SubItem, ...],  # optional, for grouped commitments / pending reviews
        "annotations": [],            # optional inline annotations (e.g. "← recommended")
    }

SubItem shape (for grouped/pending review under a parent item):

    {
        "id": "1a",                   # sub-letter index
        "summary": "...",
        "actions": ["1a add to [org]", "1a manually", "1a skip"]
    }
"""

from __future__ import annotations

from typing import Any, Optional

# v4.5.2 S2 (F-59) — the verb taxonomy is the single source of truth for
# action ids, display labels, required-input flags, and mute durations.
# This module derives its registry + label maps from the table instead of
# owning literals that drift.
from verb_taxonomy import (
    CANONICAL_ACTION_IDS as _TAXONOMY_ACTION_IDS,
    DISPLAY_LABELS as _TAXONOMY_DISPLAY_LABELS,
    REQUIRED_INPUT_ACTION_IDS,
    required_input_thing,
)

# SPEC OUT2 §2a/2b — the stat-tile band markup is owned by the shared
# component library (one implementation, two backends: this widget surface +
# brief_writer's .docx band). The legacy `counters` path and the OUT2 `tiles`
# path both render through it; markup is byte-identical to the pre-OUT2
# inline loop, so nothing changes on screen.
from components import build_tile_band_html as _build_tile_band_html


# Visual constants
PILL = "▸"
DIVIDER = "---"
PILL_SEP = "  "  # two spaces between pills
BULLET = "-"


def _bold(text: str) -> str:
    """Wrap text in markdown bold."""
    return f"**{text}**"


def _italic(text: str) -> str:
    """Wrap text in markdown italic."""
    return f"*{text}*"


def _link(label: str, url: str) -> str:
    """Render a markdown link. Use for source-citation links (Gmail threads,
    Granola transcripts, Drive docs, calendar events in the `Sources:` section)
    per `_hq/CONVENTIONS_SOURCE_LINKS.md` (workspace-side canonical doc).

    For GENERATED-DELIVERABLE links (.docx/.pdf/.xlsx/.pptx that a skill just
    created — briefs, memos, one-pagers, board packs, recap docs, etc.), use
    `doc_headline_link()` instead. Source-citation vs generated-deliverable is
    the canonical split per CONTRACT.md Rule 3.
    """
    return f"[{label}]({url})"


def doc_headline_link(label: str, url: str) -> str:
    """Render the canonical H2 heading link for a generated deliverable
    (v3.13.0+ — per `shared/CONTRACT.md` Rule 3 + the 2026-05-20
    H2-heading-link handoff).

    Output shape: `## → **[{label}]({url})**` — an H2 heading containing the
    U+2192 right arrow, a space, then a bold markdown link.

    Why H2 + arrow + bold: M's testing surfaced that plain inline `computer://`
    links get buried in long chat responses and missed. The link underline +
    text color are controlled by Cowork's chat renderer (no skill can override
    them). The three knobs available to a skill — heading size, bold weight,
    leading arrow glyph — together make the link pop without leaking the
    underlying path (path stays in the href, never in the label per Rule 4).

    Scope: this format is for **generated deliverables a skill just produced**
    that the user is meant to open right now. NOT for source citations (those
    stay as plain inline `_link()` calls), NOT for in-widget artifact_link
    (the widget iframe doesn't render `computer://` reliably — that's why the
    widget side is data-only post-v2.12.0; per `orchestrator-upcoming-meetings`
    line 256).

    Usage:

        from chat_output_renderer import doc_headline_link
        link = doc_headline_link("1:1 Prep — Aria Sample", artifact_url)
        # link == '## → **[1:1 Prep — Aria Sample](computer://...)**'

    For multi-document lists (e.g. upcoming-meetings task surfacing several
    briefs at once), use H3 instead of H2 to avoid visual overload — call
    `doc_headline_link(label, url, level=3)`. The single-deliverable case
    (call-prep, decision memo, memo, one-pager) uses the default H2.
    """
    # Implementation: support an optional `level` kwarg via signature update.
    # Kept positional for back-compat with the common single-doc case.
    return _doc_headline_link_impl(label, url, level=2)


def _doc_headline_link_impl(label: str, url: str, level: int = 2) -> str:
    """Internal: emit the heading-link with caller-specified level."""
    if level not in (2, 3):
        level = 2  # safe fallback — never emit H1 or deeper than H3 for deliverables
    prefix = "#" * level
    return f"{prefix} → **[{label}]({url})**"


def doc_headline_link_h3(label: str, url: str) -> str:
    """H3 variant for multi-document lists (e.g. upcoming-meetings surfacing
    several briefs). The single-doc case uses `doc_headline_link()` (H2);
    multi-doc uses this so the headings don't visually dominate the surface.
    """
    return _doc_headline_link_impl(label, url, level=3)


def _pill_row(actions: list[str]) -> str:
    """Render a row of action pills."""
    if not actions:
        return ""
    return PILL_SEP.join(f"{PILL} {a}" for a in actions)


def _render_metadata(metadata: list[tuple[str, str]]) -> list[str]:
    """Render Subject/To/From-style metadata as plain key: value lines."""
    return [f"{k}: {v}" for k, v in metadata if v]


def _render_body(lines: list[str]) -> list[str]:
    """Wrap each draft body line in italics. Skips empty lines."""
    out = ["Body:"]
    for line in lines:
        if line.strip():
            out.append(_italic(line))
        else:
            out.append("")
    return out


def _render_sources_inline(sources: list[dict]) -> str:
    """Render a sources list as inline links separated by commas. Returns single line or empty."""
    if not sources:
        return ""
    return "Sources: " + ", ".join(_link(s["label"], s["url"]) for s in sources)


def _render_artifact_link(artifact: dict | None) -> str:
    """Render the click-through artifact link (e.g. brief .docx). Returns empty if absent."""
    if not artifact:
        return ""
    label = artifact.get("label", "Open")
    path = artifact.get("path", "")
    if not path:
        return ""
    return f"📄 {_link(label, path)}"


def _render_sub_item(sub: dict) -> list[str]:
    """Render a sub-item (grouped commitment / pending review) — indented summary + pill row."""
    out: list[str] = []
    sub_id = sub.get("id", "")
    summary = sub.get("summary", "")
    out.append(f"  **{sub_id}.** {summary}")
    actions = sub.get("actions", [])
    if actions:
        out.append(f"     {_pill_row(actions)}")
    return out


def _render_item(item: dict) -> list[str]:
    """Render a single item block: header line + metadata + body + sources + artifact + sub-items + pill row."""
    out: list[str] = []

    # Header line: N. icon **Name** · "subject" — context_tag
    n = item.get("n")
    if n is None:
        raise ValueError("Item missing required 'n' (item number) field — Rule 3 violation in source data")

    parts = [f"**{n}.**"]
    icon = item.get("icon", "")
    if icon:
        parts.append(icon)

    name = item.get("name", "")
    if name:
        parts.append(_bold(name))

    subject = item.get("subject", "")
    if subject:
        parts.append(f'· "{subject}"')

    context_tag = item.get("context_tag", "")
    if context_tag:
        parts.append(f"— {context_tag}")

    # Annotations (e.g. "← recommended") appended to header line
    for ann in item.get("annotations", []):
        parts.append(ann)

    out.append(" ".join(parts))

    # Metadata block
    metadata = item.get("metadata", [])
    if metadata:
        out.append("")
        out.extend(_render_metadata(metadata))

    # Body block (italicized)
    body_lines = item.get("body_lines", [])
    if body_lines:
        out.append("")
        out.extend(_render_body(body_lines))

    # Inline sources line
    sources = item.get("sources", [])
    if sources:
        out.append("")
        sources_line = _render_sources_inline(sources)
        if sources_line:
            out.append(sources_line)

    # Artifact link (click-through to brief .docx)
    artifact = item.get("artifact_link")
    if artifact:
        out.append("")
        artifact_line = _render_artifact_link(artifact)
        if artifact_line:
            out.append(artifact_line)

    # Action pill row
    actions = item.get("actions", [])
    if actions:
        out.append("")
        out.append(_pill_row(actions))

    # Sub-items (grouped commitments / pending reviews)
    sub_items = item.get("sub_items", [])
    if sub_items:
        out.append("")
        for sub in sub_items:
            out.extend(_render_sub_item(sub))
            out.append("")  # blank line between sub-items

    return out


def _render_section(section: dict) -> list[str]:
    """Render a section: optional title (with count) + items separated by dividers."""
    out: list[str] = []
    title = section.get("title")
    count = section.get("count")
    if title:
        title_text = title.upper()
        if count is not None:
            title_text = f"{title_text} ({count})"
        out.append(f"## {title_text}")
        out.append("")

    items = section.get("items", [])
    for i, item in enumerate(items):
        if i > 0:
            out.append("")
            out.append(DIVIDER)
            out.append("")
        out.extend(_render_item(item))

    return out


def render_chat_output(data: dict) -> str:
    """Render a full chat output from a structured data view.

    Returns a markdown-formatted string ready to post to chat. Cowork chat
    renders the markdown (headers, bold, italic, links, dividers) into proper
    visual hierarchy.

    Raises ValueError if the data view violates required structure (missing N,
    invalid section shape, etc.). The renderer is strict — better to fail loudly
    here than to ship a broken chat turn.
    """
    out: list[str] = []

    # Header (always present)
    header = data.get("header", "")
    if header:
        out.append(header)

    # Sub-header (optional)
    sub_header = data.get("sub_header", "")
    if sub_header:
        out.append("")
        out.append(sub_header)

    # Sections
    sections = data.get("sections", [])
    for i, section in enumerate(sections):
        out.append("")
        if i > 0:
            out.append(DIVIDER)
            out.append("")
        out.extend(_render_section(section))

    # Quick Read closing block (rendered as a blockquote for visual emphasis)
    quick_read = data.get("quick_read", "")
    if quick_read:
        out.append("")
        out.append(DIVIDER)
        out.append("")
        out.append(f"> **Quick read:** {quick_read}")

    # Save confirmation (optional one-line)
    save_confirmation = data.get("save_confirmation", "")
    if save_confirmation:
        out.append("")
        out.append(save_confirmation)

    # Bulk actions row
    bulk_actions = data.get("bulk_actions", [])
    if bulk_actions:
        out.append("")
        out.append(_pill_row(bulk_actions))

    # Final newline + collapse multi-blank-line runs to at most 2 (for cleanliness)
    raw = "\n".join(out)
    # Collapse 3+ consecutive newlines down to 2 (max one blank line)
    while "\n\n\n" in raw:
        raw = raw.replace("\n\n\n", "\n\n")
    return raw.strip() + "\n"


__all__ = [
    "render_chat_output",
    "render_chat_output_widget",
    "scan_for_id_leaks",
    "scan_for_generic_summary",
    "validate_chat_output",
    "validate_rendered_widget",
    "CANONICAL_ACTIONS",
    "is_canonical_action",
    "CanonicalActionError",
    "LeakDetectedError",
    "DataShapeError",
    "PulseRichnessError",
    "WrapperContractError",
    "WidgetFeedbackContractError",
    "REQUIRED_INPUT_ACTION_IDS",
    "EMAIL_REQUIRED_ACTIONS",
    "PULSE_PERSON_DORMANT_ACTIONS",
    "PULSE_REQUIRED_METADATA_KEYS",
]


# ============================================================================
# Canonical action set (v2.13.0+; v4.5.2 S2 — derived from the verb taxonomy)
#
# Per shared/CONTRACT.md Rule 5: every action label MUST be in this set.
# render_chat_output_widget raises CanonicalActionError if an orchestrator
# passes an unknown action verb. No silent acceptance.
#
# The set is DERIVED from shared/scripts/verb_taxonomy.py — one row per verb
# (wire id, display label, event, surfaces, mute TTL). Adding a new action:
# add a ROW there (+ shared/CHAT_ACTION_WIDGET.md's human-readable tables +
# an apply-choices handler). Never re-introduce a literal set here.
# ============================================================================

CANONICAL_ACTIONS = _TAXONOMY_ACTION_IDS


class CanonicalActionError(ValueError):
    """Raised when an orchestrator passes an action verb not in CANONICAL_ACTIONS.

    The fix is always at the orchestrator level — pick a canonical verb from the
    set above. Never paper-over by adding the misspelled verb to the set; the
    canonical set is the contract.
    """


class LeakDetectedError(ValueError):
    """Raised when the rendered output contains a forbidden pattern per
    shared/CONTRACT.md Rule 4. The fix is always at the data layer — strip the
    leak from the data view before re-rendering. Never paper-over by adding the
    leaking pattern to the allow-list.
    """


class DataShapeError(ValueError):
    """Raised when an item's data shape is internally inconsistent in ways that
    cause downstream UX failures (per M's Apr 30 v2.14.1 testing — Drew's
    "Edit then send didn't open" was traced to email items missing the action
    in their action set, OR missing populated metadata).

    Specific shape rules enforced (all blocking — no silent recovery):

    - **Email-shaped item rule (FB-17 form, 2026-07-19):** if `metadata`
      contains `To` AND `Subject` keys with non-empty values, the item is
      "email-shaped" and MUST include `send`, `draft`, and `snooze 3d` in its
      `actions` array — the Send / Draft / Snooze card. (FB-17 retired
      `edit then send`: the FB-10 inline contenteditable body replaces the
      To/Cc/Subject/Body popup editor, so the card no longer offers it. The
      wire id stays a DEPRECATED_ALIAS → `send` so an in-flight widget still
      dispatches, but no new card emits it. Pre-FB-17 the required set was
      `send / edit then send / draft`; pre-v2.14.4 it was
      `send / edit then send / to drafts / edit then draft / skip`. All legacy
      verbs are accepted as deprecated aliases, none is emitted anew.) A plain
      email card is EXACTLY Send / Draft / Snooze with no dropdown; Waiting On
      chase rows are also email-shaped but add domain verbs (mark received,
      follow-up call) in the tail (`add to my list` rode there too until MLK1
      retired it). Items that have email
      metadata but DON'T offer the required set leave users with widget buttons
      that don't match the visible draft — Drew's "this one doesn't have a
      resolve button" issue was the same class.

      **v3.13.4+ calendar carve-out:** items that ALSO carry calendar keys
      (`Time` / `Duration` / `Location` / `Date`) are calendar-shaped, not
      email-shaped, and the rule above does NOT apply. Calendar invites use
      `send / skip` (FB-17 retired `edit then send` there too — inline editing;
      no `draft`, Google Calendar's draft semantics don't map).

    - **Draft needs metadata rule:** if an item's `actions` includes `draft`
      (or the deprecated `edit then send` on an in-flight widget), the item
      MUST have at least one populated metadata field (`To`, `Cc`, `Subject`)
      AND non-empty `body_lines`. Otherwise the edit surface opens with all
      blank fields = looks broken to the user.

    - **Action-set consistency rule:** every item's actions must be either
      ALL canonical-with-no-input OR canonical-with-bracket-input — no mixing
      improvised verbs into a canonical set.

    The fix is always at the orchestrator level — fix the data view before
    rendering. Never disable this validator.
    """


class PulseRichnessError(DataShapeError):
    """FOSSIL VALIDATOR (LIFECYCLE1, 2026-08-02) — the Pulse chat is RETIRED and
    nothing builds this item shape any more. The validator STAYS: it is keyed on
    a shape fingerprint, not on a task id, so it costs nothing on surfaces that
    never emit that shape, and deleting a gate is how a shape quietly comes back
    thin. Do NOT build a new surface to this contract — `verb_taxonomy` and
    `CANONICAL_ACTIONS` are the live authority on any card you are rendering.

    Raised when a Pulse (cr-dont-forget) person-dormant item is too thin to
    render usefully. Per M's 2026-05-07 testing on Sam's bare card: *"the
    information was pretty bare and it was bringing up something from a couple
    of weeks ago. there was not a link to the email, the description was very
    sparse."*

    The v2.14.28+ contract requires 4 mandatory metadata rows on every
    person-dormant card: Last contact / Why they matter / Open context / What's
    at stake. The v2.14.38+ contract additionally requires `original_thread` to
    be populated when the source is an email or transcript thread.

    The recurring failure mode (per M's memory): the orchestrator agent
    improvises around the canonical contract and emits 1-2 sparse rows or a
    plain-text Open context with no markdown link. This validator blocks the
    render before show_widget so the agent can't ship a bare card.

    Specific rules enforced:

    1. **Mandatory metadata keys.** Every Pulse person-dormant item must have
       all 4 keys present in its metadata: `Last contact`, `Why they matter`,
       `Open context`, `What's at stake`. Empty/None values are OK ONLY if they
       use the explicit fallback strings spec'd in the contract (e.g.,
       `(no role tracked yet — ...)`); silently omitting a key is forbidden.

    2. **Last contact must include a topic.** The `Last contact` value must
       contain both a date pattern AND a topic separator (em-dash or hyphen
       with surrounding spaces). Catches the bare `"18 days ago"` failure mode
       — the date alone doesn't tell M what the last touch was about.

    3. **Source links must populate original_thread.** If `Open context` value
       contains a Gmail or Granola URL (or `Last contact` references an email
       subject pattern), the `original_thread` field MUST be populated with at
       minimum a non-empty `url`. Mirrors the inbox-triage / commitments
       pattern so Pulse cards get the same collapsible thread accordion the
       customer already knows.

    Fingerprint for "Pulse person-dormant item":
       - `icon == "👤"`, AND
       - actions array (after stripping `<n> ` prefix) intersects with
         `{"investigate", "draft re-engagement", "schedule catchup [when]"}`.

    The fix is always at the orchestrator level. Don't disable this validator
    or relax the rules — that's how the v2.14.x agent-improvises-around-canonical
    failure class keeps shipping.
    """


class WidgetFeedbackContractError(ValueError):
    """Raised by `validate_rendered_widget()` when an action widget's HTML is
    missing the visible-feedback layer (v4.5.2 S2 — F-58/F-17).

    F-58: a hand-built widget variant shipped buttons whose clicks registered
    with NO pressed-state — toggle semantics with invisible state, so the
    customer couldn't tell what was armed before Apply (and a repeat click
    silently deselected). The canonical renderer always emits the selected
    state CSS, the live "N of M selected" counter, and (when any action
    requires an input) the Apply-hold reason line — this validator makes
    those non-optional for ANY html that reaches show_widget, renderer-built
    or not.

    The fix when this raises: render through `render_chat_output_widget()`
    and ship its output byte-for-byte. Never hand-build an action widget.
    """


class WrapperContractError(ValueError):
    """Raised by `validate_rendered_widget()` when the rendered HTML's
    button-to-wrapper structural invariant is violated.

    Specifically: every action button with `data-input-type` other than
    `"none"` (i.e. an action that needs a textarea / picker / multi-field
    input) MUST have a matching `<div class="cr-action-input"
    data-input-for-n="..." data-input-for-action="...">` element in the
    rendered HTML.

    Why this exists (v2.14.34+ — root-cause diagnosis 2026-05-07):
    when the orchestrator's Phase 6 / Phase 9 step says "execute the
    renderer; post what it returns — byte-for-byte" and Claude (the agent
    firing the orchestrator) post-processes the HTML between
    `render_chat_output_widget()` and `mcp__visualize__show_widget`
    ("minify for size", "trim duplicates", "clean up whitespace"), the
    cleanup can drop `<div class="cr-item-inputs">` blocks silently —
    leaving buttons in the DOM that look fine but have no wrappers to
    open. Customer clicks Edit-then-send, button selects gold, no
    textarea appears.

    Pre-v2.14.34 the only signal was a console.warn buried in the iframe
    (added in v2.14.30 as a debugging aid). Customer-invisible. Two
    days of misdiagnosis chasing CSS / scroll / focus issues that were
    never the real cause.

    The validator runs over the FINAL HTML string (post any agent
    transformation). Raises with a list of every (n, action, input_type)
    that's missing its wrapper. Orchestrator instructions require this
    call to fire BEFORE `show_widget` — see
    `enable-command-room-schedules/references/orchestrator-*.md`
    "Zero-manipulation contract" sections.
    """


# Required-action sets per item-type signature (v2.14.1+).
# Key = a tuple of signature flags; value = set of action verbs that MUST be in
# the item's actions array (after _strip_action_n_prefix).
#
# These rules catch the "this item is missing the resolve button" / "Edit then
# send doesn't open because the action wasn't there" classes of bug.

EMAIL_REQUIRED_ACTIONS = frozenset({
    "send",
    "draft",   # v2.14.4+ — consolidates former `to drafts` + `edit then draft`
    "snooze 3d",  # FB-17 (M, 2026-07-19) — the email card's third primary
                  # button (Send / Draft / Snooze). `edit then send` RETIRED
                  # from the required set: the FB-10 inline body replaces the
                  # popup editor; the wire id stays a DEPRECATED_ALIAS so
                  # in-flight widgets still dispatch, but no new email card
                  # emits or renders it.
    # v2.14.31+ — `"skip"` was REMOVED from the required set (the v2.14.28
    # coupling bug: requiring a verb an orchestrator doesn't emit aborts the
    # widget). FB-17 keeps that lesson — `snooze 3d` is required because the
    # email card and Waiting On rows both carry it; skip / escalate to memo are
    # NOT required (a plain email card is exactly Send / Draft / Snooze).
})

# Items with `edit then send` or `edit then draft` need populated content
EDIT_REQUIRES_METADATA_KEYS = frozenset({"to", "cc", "subject"})


def _is_email_shaped(item: dict) -> bool:
    """An item is "email-shaped" if its metadata contains To AND Subject with
    non-empty values. Calendar invites, contracts, plain commitments without
    a draft do NOT count as email-shaped — they have different action sets.

    v3.13.4+ — calendar-shape carve-out. A calendar invite legitimately carries
    To + Subject (attendee list + event title) but ALSO carries Time / Duration
    / Location / Date keys that an email never would. When any calendar-only
    key is present, treat the item as calendar-shaped, not email-shaped, so
    the email-shaped action-set requirement doesn't fire. Calendar items use
    `send / edit then send / skip` (no `draft` — Google Calendar's draft
    semantics don't map onto the user's mental model of saving an email draft).
    """
    metadata = item.get("metadata") or []
    has_to = False
    has_subject = False
    has_calendar_key = False
    CALENDAR_KEYS = {"time", "duration", "location", "date", "when", "where"}
    for key, value in metadata:
        if not value:
            continue
        k = (key or "").lower()
        if k == "to":
            has_to = True
        elif k == "subject":
            has_subject = True
        elif k in CALENDAR_KEYS:
            has_calendar_key = True
    if has_calendar_key:
        return False
    return has_to and has_subject


def _has_populated_email_content(item: dict) -> bool:
    """For items with edit-then-send/draft actions: verify at least one of
    To/Cc/Subject is populated AND body_lines is non-empty. Otherwise the
    multi-field edit opens with all-blank fields = looks broken.
    """
    metadata = item.get("metadata") or []
    has_any_field = False
    for key, value in metadata:
        if not value:
            continue
        if (key or "").lower() in EDIT_REQUIRES_METADATA_KEYS:
            has_any_field = True
            break
    body_lines = item.get("body_lines") or []
    has_body = any(line.strip() for line in body_lines if isinstance(line, str))
    return has_any_field and has_body


def _validate_data_shape(data: dict) -> None:
    """Walk every item; raise DataShapeError on any inconsistency that would
    cause downstream UX failures. Called from render_chat_output_widget BEFORE
    rendering, AFTER canonical-action validation.
    """
    issues = []
    for section in data.get("sections", []):
        for item in section.get("items", []):
            n = item.get("n", "?")
            stripped_actions = set()
            for a in item.get("actions", []):
                s = _strip_n_prefix_for_validation(a, item.get("n", ""))
                stripped_actions.add(s)

            # Rule 1: email-shaped items must offer the required email set
            if _is_email_shaped(item):
                missing = EMAIL_REQUIRED_ACTIONS - stripped_actions
                if missing:
                    issues.append(
                        f"item {n!r} is email-shaped (has To+Subject metadata) but "
                        f"missing required actions: {sorted(missing)}. Email items "
                        f"MUST offer the full Send / Draft / Snooze set (FB-17) so "
                        f"the card's buttons line up with the visible draft."
                    )

            # Rule 2: edit-then-send/draft requires populated metadata + body
            has_edit_action = bool(
                stripped_actions & {"edit then send", "edit then draft"}
            )
            if has_edit_action and not _has_populated_email_content(item):
                issues.append(
                    f"item {n!r} has 'edit then send' or 'edit then draft' in actions "
                    f"but missing populated metadata (To/Cc/Subject) or body_lines. "
                    f"Multi-field edit would open with all blank fields = looks broken. "
                    f"Either populate metadata + body OR drop the edit-then-* actions."
                )

            # Rule 3: applies to sub_items too (recursive, simplified — sub-items
            # don't have their own metadata; they inherit parent context)
            for sub in item.get("sub_items", []):
                sub_id = sub.get("id", "?")
                sub_actions = set()
                for a in sub.get("actions", []):
                    s = _strip_n_prefix_for_validation(a, sub.get("id", ""))
                    sub_actions.add(s)
                # Sub-items don't have full email shape; just verify they have
                # at least one canonical action
                if not sub_actions:
                    issues.append(
                        f"sub-item {sub_id!r} on parent {n!r} has empty actions[]"
                    )

    if issues:
        raise DataShapeError(
            "Data shape validation failed — orchestrator built items that would "
            "render with broken UX. Fix at the orchestrator level (don't add "
            "exceptions here):\n  - " + "\n  - ".join(issues)
        )


# v3.13.8+ — Gate 6: send-class items must carry a valid To: email address.
# Closes Bug #44 (dead-chrome in degraded n=1 case). The pre-v3.13.8 renderer
# would happily ship a widget with action buttons that could never succeed
# because the To: was a placeholder like "Bo (no email)".
import re as _re_mod_send  # ensure available at module-load time

_EMAIL_REGEX = _re_mod_send.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# Send-class actions — buttons whose entire UX promise depends on a valid email.
# `nudge` is DELIBERATELY absent (train-merge review F-4, ruled 2026-07-22):
# the WG1-B D-B4 moves adapter emits To-less `nudge` rows on scheduled
# staff-meeting fires (compose-on-click resolves the address at dispatch), so
# adding it here would DataShapeError that whole surface. The waiting-on
# driver's own degrade (`nudge` -> `add email then send` when no owner email
# resolves) is the enforcement for delegated commitment rows.
_SEND_CLASS_ACTIONS = frozenset({"send", "draft", "edit then send"})


def _validate_send_class_email_addresses(data: dict) -> None:
    """Gate 6 (v3.13.8+ — Bug #44).

    Every item that exposes a send-class action (`send`, `draft`,
    `edit then send`) must carry a valid email in its To: metadata. The
    fallback for degraded items (recipient identified but email unknown) is
    the canonical `add email then send` verb, which collects the address
    via a single-field input.

    The pre-v3.13.8 renderer shipped widgets where the send button rendered
    but could not actually fire (Cowork's B5-* surfaces). Per the v3.13.8
    architectural review: do not render chrome that cannot succeed.
    """
    issues = []
    for section in data.get("sections", []):
        for item in section.get("items", []):
            n = item.get("n", "?")
            stripped_actions = set()
            for a in item.get("actions", []) or []:
                s = _strip_n_prefix_for_validation(a, item.get("n", ""))
                stripped_actions.add(s)
            if not (stripped_actions & _SEND_CLASS_ACTIONS):
                continue

            # Pull the To: value from metadata
            to_value = ""
            for key, value in item.get("metadata", []) or []:
                if (key or "").lower() == "to" and value:
                    to_value = str(value).strip()
                    break

            # Strip a single "Name <email@x>" wrapper to extract the address
            inner = to_value
            if "<" in inner and ">" in inner:
                inner = inner.split("<", 1)[1].rsplit(">", 1)[0].strip()

            if not _EMAIL_REGEX.match(inner):
                issues.append(
                    f"item {n!r} has send-class actions {sorted(stripped_actions & _SEND_CLASS_ACTIONS)!r} "
                    f"but To: value {to_value!r} is not a valid email. "
                    f"Use the canonical `add email then send` verb instead — it opens a "
                    f"single-field email input and transitions to enabled `send` on submit."
                )

    if issues:
        raise DataShapeError(
            "Send-class chrome would render but could not succeed (Bug #44). "
            "Fix at the orchestrator level — degrade to the `add email then send` "
            "recovery verb when an item's recipient has no actionable email:\n  - "
            + "\n  - ".join(issues)
        )


# v2.14.38+ — Pulse person-dormant fingerprint + validator.
# Catches the agent-improvises-around-canonical-paths failure mode where the
# orchestrator ships bare cards with 1-2 metadata rows and no source link.
# Per M's 2026-05-07 testing on Sam's bare card.

PULSE_PERSON_DORMANT_ACTIONS = frozenset({
    "investigate",
    "draft re-engagement",
    "schedule catchup [when]",
})

PULSE_REQUIRED_METADATA_KEYS = ("Last contact", "Why they matter", "Open context", "What's at stake")

# Source URL hosts that indicate an email/transcript thread is anchoring the item.
# When present in Open context (or other metadata), original_thread must be populated.
_PULSE_SOURCE_URL_HOSTS = ("mail.google.com", "notes.granola.ai", "fireflies.ai", "outlook.office.com", "outlook.office365.com")


def _is_pulse_person_dormant_item(item: dict) -> bool:
    """True if the item shape matches a Pulse person-dormant / pattern-break
    card. Fingerprint: 👤 icon + actions intersect with the Pulse-specific verbs.

    Person-shape cards on OTHER orchestrators (e.g., people-crm `tell me about`
    output) have different action sets and won't match.
    """
    if item.get("icon") != "👤":
        return False
    n_str = str(item.get("n", ""))
    stripped_actions = set()
    for a in item.get("actions", []) or []:
        s = _strip_n_prefix_for_validation(a, n_str)
        stripped_actions.add(s)
    return bool(stripped_actions & PULSE_PERSON_DORMANT_ACTIONS)


def _has_pulse_source_url(value: str) -> bool:
    """True if the metadata value contains a Gmail/Granola/transcript URL host.
    Used to detect that an item references an email or transcript thread, in
    which case original_thread must be populated.
    """
    if not isinstance(value, str) or not value:
        return False
    v = value.lower()
    return any(host in v for host in _PULSE_SOURCE_URL_HOSTS)


def _validate_pulse_richness(data: dict) -> None:
    """Walk every item; raise PulseRichnessError on any Pulse person-dormant
    card that's too thin to render usefully.

    Three rules (all blocking):
      1. All 4 mandatory metadata keys must be present (empty fallback strings OK).
      2. `Last contact` must contain both a date AND a topic separator.
      3. If `Open context` (or any metadata value) contains a Gmail/Granola URL,
         `original_thread` must be populated with a non-empty url.
    """
    issues: list[str] = []
    for section in data.get("sections", []) or []:
        for item in section.get("items", []) or []:
            if not _is_pulse_person_dormant_item(item):
                continue
            n = item.get("n", "?")
            metadata = item.get("metadata") or []
            keys_present = {(k or "") for k, _ in metadata}

            # Rule 1: all 4 mandatory keys present
            missing_keys = [k for k in PULSE_REQUIRED_METADATA_KEYS if k not in keys_present]
            if missing_keys:
                issues.append(
                    f"item {n!r} is a Pulse person-dormant card but missing required "
                    f"metadata keys: {missing_keys}. The v2.14.28+ contract requires "
                    f"all 4 keys ({list(PULSE_REQUIRED_METADATA_KEYS)}); use explicit "
                    f"fallback strings like '(no role tracked yet)' if data is genuinely "
                    f"unavailable, but never silent-omit."
                )

            # Rule 2: Last contact must include a topic, not just a date
            last_contact_value = ""
            open_context_value = ""
            for k, v in metadata:
                if k == "Last contact":
                    last_contact_value = v or ""
                elif k == "Open context":
                    open_context_value = v or ""
            if last_contact_value:
                # Reject "18 days ago" alone or "Apr 18" alone — needs date + topic.
                # Heuristic: an em-dash or " — " indicates date + topic separator.
                # Allow explicit fallback strings starting with "(" (e.g., "(no contact tracked)").
                is_fallback = last_contact_value.strip().startswith("(")
                has_separator = " — " in last_contact_value or " – " in last_contact_value
                if not is_fallback and not has_separator:
                    issues.append(
                        f"item {n!r} 'Last contact' value is too bare: {last_contact_value!r}. "
                        f"Must include both a date AND the topic/subject of the last touch, "
                        f"separated by ' — '. Pattern: '18 days ago — Apr 18, Slack DM about Q3 OKR'. "
                        f"Pre-v2.14.28 cards just said 'X days ago' with no story — this rule "
                        f"blocks that regression. If genuinely no topic is known, use the explicit "
                        f"fallback '(last touch context unavailable — open thread to read)'."
                    )

            # Rule 3: source URL → original_thread required
            references_thread = (
                _has_pulse_source_url(open_context_value)
                or any(_has_pulse_source_url(v) for _, v in metadata)
            )
            if references_thread:
                orig = item.get("original_thread")
                if not isinstance(orig, dict):
                    issues.append(
                        f"item {n!r} references a Gmail/Granola/transcript thread "
                        f"in its metadata but has no `original_thread` field. Per the "
                        f"v2.14.38+ contract: when Pulse pulls from a source, the source "
                        f"must be linked AND (for email/transcript) the thread must "
                        f"render in the same collapsible block inbox-triage uses. "
                        f"Populate `original_thread` with author/date/subject/body/url. "
                        f"Per M's 2026-05-07 ask after testing Sam's bare card."
                    )
                elif not (orig.get("url") or "").strip():
                    issues.append(
                        f"item {n!r} has `original_thread` but no `url`. The url field "
                        f"is required so the renderer can show the ↗ Open in Gmail / "
                        f"↗ Open in Granola link. If the source was deleted or "
                        f"permission-denied, leave the url populated and put "
                        f"'(thread body unavailable — open in Gmail to read)' in body."
                    )

    if issues:
        raise PulseRichnessError(
            "Pulse richness validation failed — orchestrator built person-dormant "
            "cards that are too thin or missing source links. Fix at the "
            "orchestrator level (re-pull thread/transcript data, use the explicit "
            "fallback strings spec'd in orchestrator-dont-forget.md):\n  - "
            + "\n  - ".join(issues)
        )


def is_canonical_action(action_id):
    """True if action_id is in CANONICAL_ACTIONS or matches a specific-name
    variant pattern.

    Two specific-name variants are accepted:

    - `add as person to <Specific Org Name>` — when adding a person and the
      target org is known (mirror of generic `add as person to [org]`).
    - `add as new org <Specific Org Name>` — when proposing a new org and the
      candidate name is inferable (mirror of generic `add as new org`). Added
      v2.14.5 per M's preview-cycle Acme Co feedback: "Add as new org" alone
      doesn't tell the user WHICH org is being created — embed the name.

    Neither variant accepts arbitrary input; the string after the verb prefix
    must be non-empty and must not be the placeholder bracket form.
    """
    if action_id in CANONICAL_ACTIONS:
        return True
    if action_id.startswith("add as person to ") and not action_id.endswith("[org]"):
        # Specific-org variant. Reject if the part after `to ` is empty or only
        # whitespace — that's malformed.
        rest = action_id[len("add as person to "):].strip()
        return bool(rest)
    if action_id.startswith("add as new org ") and not action_id.endswith("[org]"):
        # Specific-org variant for the new-org case (v2.14.5+).
        rest = action_id[len("add as new org "):].strip()
        return bool(rest)
    return False


def _strip_n_prefix_for_validation(action, item_n):
    """Strip the `<n> ` prefix that orchestrators include in actions[] entries.

    Same logic as _strip_action_n_prefix but defensive against the n-string
    not matching (e.g., when the orchestrator passes `1 send` for item with
    n=1).
    """
    n_str = str(item_n)
    if n_str and action.startswith(n_str + " "):
        return action[len(n_str) + 1:]
    return action


def _validate_canonical_actions(data):
    """Walk every item + sub_item action; raise CanonicalActionError on any
    non-canonical verb. Called from render_chat_output_widget BEFORE rendering.
    """
    bad = []
    for section in data.get("sections", []):
        for item in section.get("items", []):
            for a in item.get("actions", []):
                stripped = _strip_n_prefix_for_validation(a, item.get("n", ""))
                if not is_canonical_action(stripped):
                    bad.append(f"item {item.get('n')!r} has non-canonical action: {stripped!r}")
            for sub in item.get("sub_items", []):
                for a in sub.get("actions", []):
                    stripped = _strip_n_prefix_for_validation(a, sub.get("id", ""))
                    if not is_canonical_action(stripped):
                        bad.append(
                            f"sub-item {sub.get('id')!r} has non-canonical action: {stripped!r}"
                        )
    for bulk in data.get("bulk_actions", []):
        if not is_canonical_action(bulk):
            bad.append(f"bulk action non-canonical: {bulk!r}")
    if bad:
        raise CanonicalActionError(
            "Orchestrator passed action(s) not in CANONICAL_ACTIONS — fix the "
            "data view at the orchestrator level. Findings:\n  - "
            + "\n  - ".join(bad)
        )


def validate_chat_output(text_or_html, *, paths_text=None, workspace=None,
                         plugin_root=None, surface=None):
    """Blocking leak-scanner gate per shared/CONTRACT.md Rule 4 + Rule 25.

    Runs two scanners over the input (three when `surface` declares an org
    audience):
      1. `scan_for_id_leaks` — catches forbidden patterns from `_LEAK_PATTERNS`
         (internal IDs, event types, schema fields, phase labels, etc.).
      2. `_scan_for_path_leaks` — catches absolute filesystem paths that do
         not fall under the user's runtime-resolved workspace, the plugin
         root, or the Cowork session mount. Closes the v3.5.3 class of bug
         (author's local Drive path leaking into chat output for users whose
         workspace lives somewhere else).
      3. SPEC PGUARD1 D2 — when `surface` is an org/board/client/external tag
         (personal_leak.is_org_surface), `scan_for_personal_leak` runs and
         its findings BLOCK. On `surface=None` or any owner surface the
         personal scan does not run — personal content is legitimate on
         m_facing output, and an undeclared surface must never be treated as
         org (PGUARD1 risk rule).

    Raises `LeakDetectedError` with a plain-English summary listing every leak
    found, plus the matched substring so the orchestrator (or apply-choices
    skill) can identify the source.

    Keyword args:
      - paths_text — optional alternative text to scan for path leaks
        (typically the HTML with href values preserved, while the primary
        `text_or_html` has hrefs stripped so legitimate `_hq/meetings/` paths
        in href URLs don't false-positive on the internal-path leak scanner).
        If None, paths are scanned over `text_or_html`.
      - workspace — explicit absolute path of the user's workspace folder.
        If None, resolved via `_resolve_workspace_prefix()` from
        `CLAUDE_CODE_TMPDIR` per CONTRACT.md Rule 22.
      - plugin_root — explicit absolute path of the installed plugin copy.
        If None, resolved via `_resolve_plugin_root()`.
        When BOTH workspace and plugin_root resolve to None (e.g. local
        pytest with no Cowork session), the path scan no-ops — we have no
        trusted prefix to compare against. ID-leak scan still runs.

    Used by:
      - render_chat_output_widget (after rendering, before returning HTML)
      - apply-choices Step 4 (over the consolidated chat ack before posting)
      - orchestrator Phase 8/9 post-widget chat-links section (before posting)

    NEVER silently passes. The fix is always at the data layer — strip the
    leak before re-rendering. Never add the leaking pattern to the allow-list.
    """
    leaks = scan_for_id_leaks(text_or_html)
    path_leaks = _scan_for_path_leaks(
        paths_text if paths_text is not None else text_or_html,
        workspace=workspace,
        plugin_root=plugin_root,
    )
    # PGUARD1 D2 — personal-content scan, surface-gated BLOCKING. Only fires
    # when the caller DECLARES an org/board/client/external surface; never on
    # m_facing / undeclared. Import tolerance mirrors turn_backstop (a partial
    # plugin update must not brick every chat render), but the skip is loud.
    personal_leaks = []
    if surface is not None:
        try:
            from personal_leak import is_org_surface, scan_for_personal_leak
            if is_org_surface(surface):
                personal_leaks = [
                    (f"personal-content leak ({f['name']})", f["match"])
                    for f in scan_for_personal_leak(text_or_html)
                ]
        except ImportError:
            import sys as _sys
            _sys.stderr.write(
                "[chat_output_renderer] WARN: personal_leak module missing — "
                "the org-surface personal-content scan did NOT run.\n"
            )
    all_leaks = leaks + path_leaks + personal_leaks
    if all_leaks:
        unique = {}
        for kind, sample in all_leaks:
            unique.setdefault(kind, set()).add(sample)
        msg_parts = ["Forbidden patterns detected — refusing to post:"]
        for kind, samples in unique.items():
            sample_list = ", ".join(repr(s) for s in sorted(samples)[:3])
            more = f" (+{len(samples) - 3} more)" if len(samples) > 3 else ""
            msg_parts.append(f"  - {kind}: {sample_list}{more}")
        msg_parts.append(
            "Fix: strip these strings from the data view (chat_output_renderer "
            "input) or the post-widget commentary. Never add to the allow-list."
        )
        if personal_leaks:
            msg_parts.append(
                "Personal fix (PGUARD1): a personal-lane row reached an "
                "org/board/client surface. Remove it from the data view — "
                "personal reminders and personal-tie items render ONLY on "
                "owner-facing surfaces. Never re-tag the surface to bypass."
            )
        if path_leaks:
            msg_parts.append(
                "Path fix (Rule 25): emit absolute paths using the runtime-resolved "
                "$WORKSPACE (per CONTRACT.md Rule 22 discovery — "
                "`find $SESSION_DIR/mnt -name _hq`). Never copy a path from "
                "docstrings, references, or CHANGELOG examples — those land on "
                "the author's machine, not the user's, and click 404s on user surfaces."
            )
        raise LeakDetectedError("\n".join(msg_parts))


# ============================================================================
# ID-leak guard (v2.12.0+) — runs against rendered chat output / widget HTML to
# catch internal mechanics that should never reach the user.
#
# Per CHAT_ACTION_WIDGET.md "Posting contract" forbidden patterns:
# - person_NNN, project_NNN, org_NNN entity IDs
# - "Domain match: x@y.com → ..."
# - paths under _hq/staging/
# - "Phase N", "Step N" labels
# - event seq numbers ("seq 192")
# - confidence scores ("0.87 confidence")
# ============================================================================

import re as _leak_re

_LEAK_PATTERNS = [
    # Internal entity / event IDs
    (_leak_re.compile(r"\b(person|project|org|event|matter|engagement)_\d{3,}\b", _leak_re.IGNORECASE),
     "internal entity ID"),
    # T3.1 (FB-13) — commitment / brain-proposal id shapes. These live in
    # data-* wire attributes (blanked before the widget scan) and action
    # tuples only; one in visible text or prose is the same leak class as
    # an entity ID. `cmt_` ULIDs, `commitment_seq_N`, `bp_` proposal ids.
    # IGNORECASE (T3.1 review F-2): display paths re-case text — section
    # titles are .upper()'d, labels get first-letter capitalization — and
    # `CMT_…` on screen is the same leak as `cmt_…`.
    (_leak_re.compile(r"\b(?:cmt_[0-9A-Za-z]{10,}|commitment_seq_\d+|bp_[0-9a-f]{6,})\b",
                      _leak_re.IGNORECASE),
     "internal commitment/proposal ID"),
    # Routing / synthesis metadata
    (_leak_re.compile(r"\bDomain match\s*:", _leak_re.IGNORECASE), "routing-metadata leak"),
    (_leak_re.compile(r"\bRouting\s*:", _leak_re.IGNORECASE), "routing label"),
    (_leak_re.compile(r"\bconfidence\s*[:=]\s*\d", _leak_re.IGNORECASE), "confidence-score leak"),
    (_leak_re.compile(r"\b(?:low|high|medium)\s+confidence\b", _leak_re.IGNORECASE), "confidence-score leak"),
    # Internal file paths (v2.12.4+; v2.12.6 — `meetings` is user-facing, allowed)
    # Forbids _hq/{staging|data|views|deliverables|tmp}/. Allows _hq/meetings/ since
    # that's where briefs save (v2.12.6+) and the user opens them via the artifact_link
    # in the widget. Path appears in href URL but not as visible label text — fine.
    (_leak_re.compile(r"_hq/(?:staging|data|views|deliverables|tmp|scheduled_outputs|insights)/?", _leak_re.IGNORECASE),
     "internal _hq path"),
    # v2.14.14+ — agent-freelancing patterns observed in real fires.
    # Catches "Saved the full standalone HTML at...", "Files saved to _hq/...",
    # "saved to disk", and similar narrations of disk writes the orchestrator
    # should not be doing.
    (_leak_re.compile(r"\b(?:saved|wrote|written|persisted|stored)\s+(?:the\s+)?(?:full\s+)?(?:standalone\s+)?(?:HTML|widget|outputs?|file|results?)\s+(?:at|to|into|in)\b",
                      _leak_re.IGNORECASE),
     "freelance file-write leak"),
    # v2.14.14+ — narrating widget contents ("Regenerated with N items",
    # "What's in the widget above", "Total scan results: X persons flagged",
    # "5 actionable items"). The widget self-describes; explaining it duplicates the surface.
    (_leak_re.compile(r"\b(?:Regenerated|Generated|Refreshed|Rebuilt)\s+with\s+\d+\s+(?:real\s+)?(?:items?|emails?|threads?|persons?|meetings?|drafts?)\b",
                      _leak_re.IGNORECASE),
     "widget-narration leak"),
    (_leak_re.compile(r"\bWhat'?s\s+in\s+the\s+widget\s+above\b", _leak_re.IGNORECASE),
     "widget-narration leak"),
    (_leak_re.compile(r"\bTotal\s+(?:scan\s+results?|fired|surfaced|generated):\s*\d+\b",
                      _leak_re.IGNORECASE),
     "widget-narration leak"),
    (_leak_re.compile(r"\b\d+\s+(?:actionable|surfaced|flagged|pending|stuck)\s+items?\b\s+(?:in\s+)?(?:the\s+widget|above)\b",
                      _leak_re.IGNORECASE),
     "widget-narration leak"),
    (_leak_re.compile(r"\b(events|entities|aliases|staging_emissions|known-newsletters)\.(?:jsonl?|txt)\b", _leak_re.IGNORECASE),
     "internal data file"),
    (_leak_re.compile(r"\bSESSION_NOTES(?:_[A-Z_]+)?\.md\b"), "internal session-notes file"),
    (_leak_re.compile(r"\bevents\.schema\.json\b", _leak_re.IGNORECASE), "internal schema file"),
    # Internal event-type names (v2.12.4+ — surfaced in apply-time outputs;
    # v2.14.6+ — added commitment_updated, commitment_review_proposed for CRU layer;
    # v2.14.7+ — added commitment_review_dismissed for review-skip path)
    (_leak_re.compile(r"\b(chat_dismissal|pack_run|connector_read|outreach_sent|draft_created|"
                      r"commitment_resolved|commitment_updated|commitment_review_proposed|"
                      r"commitment_review_dismissed|"
                      r"commitment_to_discuss|thread_resolved|choices_applied|"
                      r"chat_suppressed|dont_forget_feedback|interaction|meeting_processed|"
                      r"pattern_break_detected|decision)\s+(?:event(?:s)?|written|logged|appended)",
                      _leak_re.IGNORECASE),
     "internal event-type leak"),
    # Phase / Step labels (development-time scaffold)
    (_leak_re.compile(r"\b(Phase|Step)\s+\d+(?:\.\d+)?[a-z]?\b"), "phase/step label"),
    # Schema field names
    (_leak_re.compile(r"primary_thread_id|classification_confidence|source_event_seq|last_interaction(?:_date)?(?:\s+proposed)?",
                      _leak_re.IGNORECASE),
     "schema-field leak"),
    # Event seq numbers
    (_leak_re.compile(r"\bseq\s+\d+\b", _leak_re.IGNORECASE), "event seq leak"),
    # Plugin protocol / version internals (e.g. "post-widget chat-links section per v2.12.0+ protocol")
    (_leak_re.compile(r"\bv\d+\.\d+(?:\.\d+)?\+?\s+(?:protocol|spec|format)\b", _leak_re.IGNORECASE),
     "plugin-version protocol leak"),
    (_leak_re.compile(r"\bper\s+v\d+\.\d+(?:\.\d+)?\+?\s+", _leak_re.IGNORECASE),
     "plugin-version reference leak"),
    # Apply-payload string (the JSON wire format must never reach chat as visible text)
    (_leak_re.compile(r"^apply\s+choices\s*:", _leak_re.IGNORECASE | _leak_re.MULTILINE),
     "apply-payload string leak"),
    # Internal narration patterns (Cowork-side LLM thinking out loud)
    (_leak_re.compile(r"\b(?:Now\s+(?:appending|fetching|reading|writing|loading)|"
                      r"Pack\s+run\s+complete|"
                      r"writing\s+to\s+events\.jsonl|"
                      r"appending\s+(?:dispatch\s+)?events?\b)",
                      _leak_re.IGNORECASE),
     "internal narration leak"),
    # v2.14.37+ — agent-improvises-around-canonical-paths: narrating that the
    # widget "couldn't transmit" / hit a "session payload limit" / exceeded
    # the "live widget surface" instead of calling `show_widget` after a clean
    # `validate_rendered_widget` pass. None of these phrases exist anywhere
    # in the codebase — they are pure agent improvisation. ZERO-MANIPULATION
    # CONTRACT extension forbids them; this scanner catches any that slip past.
    # See `feedback_canonical_path_improv_pattern.md` (memory) for the full
    # pattern lineage (v2.14.18 / v2.14.20 / v2.14.34 / 2026-05-07 evening).
    (_leak_re.compile(r"\bcouldn'?t\s+transmit\b", _leak_re.IGNORECASE),
     "widget-improvisation leak (couldn't transmit)"),
    (_leak_re.compile(r"\bsession\s+payload\b", _leak_re.IGNORECASE),
     "widget-improvisation leak (session payload)"),
    (_leak_re.compile(r"\blive\s+widget\s+surface\b", _leak_re.IGNORECASE),
     "widget-improvisation leak (live widget surface)"),
    (_leak_re.compile(r"\bpayload\s+(?:limit|size)\b", _leak_re.IGNORECASE),
     "widget-improvisation leak (payload limit/size)"),
    (_leak_re.compile(r"\b(?:widget|render(?:ed)?)\s+(?:was\s+)?too\s+(?:large|big)\b", _leak_re.IGNORECASE),
     "widget-improvisation leak (too large)"),
    (_leak_re.compile(r"\brender\s+validated\s+(?:cleanly|but)\b", _leak_re.IGNORECASE),
     "widget-improvisation leak (render validated but...)"),
    # EW2+T (F-09) — a Cowork platform style block (`::view-transition-group`
    # animation CSS) echoed into chat text immediately before a show_widget
    # render. The token exists nowhere in plugin-produced HTML, so any
    # occurrence in scanned output is a widget-bytes echo. The widget_code
    # transport (widget_transport.render_and_persist, relayed as widget_code)
    # keeps bytes out of chat; this pattern catches any relay that slips past it.
    (_leak_re.compile(r"::view-transition", _leak_re.IGNORECASE),
     "platform CSS echo leak"),
]


def scan_for_id_leaks(text):
    """Return a list of (pattern_label, matched_substring) for every forbidden
    pattern found in the input text. Empty list = clean.

    Use against widget HTML and against the chat-links section markdown to catch
    internal-mechanics leaks before posting. Per Sam's Apr 30 feedback ("Why
    is it showing IDs?") — these patterns must never reach the user surface.
    """
    if not text:
        return []
    findings = []
    for pat, label in _LEAK_PATTERNS:
        for m in pat.finditer(text):
            findings.append((label, m.group(0)))
    return findings


# ============================================================================
# Generic-summary banned patterns (SPEC EXEC1 element 1 — the anti-washing
# floor). These are washing shapes that defeat the 30-second contract: a header
# line that says "Several important updates" instead of a named entity / number
# / date. Per the EXEC1 spec these apply to EXEC-HEADER LINES ONLY (verdict /
# CHANGED / DECIDE / NEEDED) — NOT to arbitrary body prose, which may legitimately
# quote such phrases. So they are a SEPARATE, opt-in scan (run against header
# lines), deliberately NOT folded into the blocking `_LEAK_PATTERNS` that scans
# every chat post. Semantic verdict-quality (does the line carry a real
# conclusion) lives in the per-skill checklists — this catches only the four
# canonical washing shapes the spec enumerates, and nothing more.
# ============================================================================

_GENERIC_SUMMARY_PATTERNS = [
    (_leak_re.compile(r"\bkey\s+developments?\b", _leak_re.IGNORECASE),
     "generic-summary header (key developments)"),
    (_leak_re.compile(r"\bseveral\s+(?:important\s+)?updates?\b", _leak_re.IGNORECASE),
     "generic-summary header (several updates)"),
    (_leak_re.compile(r"\bbusy\s+week\s+across\b", _leak_re.IGNORECASE),
     "generic-summary header (busy week across)"),
    (_leak_re.compile(r"\blots\s+of\s+movement\b", _leak_re.IGNORECASE),
     "generic-summary header (lots of movement)"),
]


def scan_for_generic_summary(text):
    """Return a list of (label, matched_substring) for every generic-summary
    washing shape found. Run this against EXEC-HEADER LINES (verdict / CHANGED /
    DECIDE / NEEDED) — not body prose. Empty list = clean.

    SPEC EXEC1 element 1: an exec-header line must carry a named entity, number,
    or date, OR use the explicit nothing-form. A line like "Several important
    updates this week" is the washing shape this catches."""
    if not text:
        return []
    findings = []
    for pat, label in _GENERIC_SUMMARY_PATTERNS:
        for m in pat.finditer(text):
            findings.append((label, m.group(0)))
    return findings


# ============================================================================
# Path-leak runtime guard (v3.6.0+) — runs against chat output / widget HTML to
# catch absolute filesystem paths that do not fall under the user's
# runtime-resolved workspace. Closes the v3.5.3 class of bug: literal absolute
# paths in doc examples (the author's local Drive path) leaking back into chat
# output for users whose workspace lives somewhere completely different.
#
# v3.5.3 closed the SOURCE of bad paths in docs (placeholders + Rule 25 +
# tests/run_no_hardcoded_drive_test.py grep guard). v3.6.0 adds the RUNTIME
# check so that if a future skill author writes a new doc example, or the
# agent improvises a path from somewhere not covered by the static grep, the
# same bug class can't reach the user.
#
# Per CONTRACT.md Rule 22, the workspace and plugin root are derived from
# CLAUDE_CODE_TMPDIR — the only Cowork-set env var we rely on. If discovery
# fails (no env var, no matching folder), the scan no-ops rather than
# false-alarming — better to skip than to lie. The static grep test plus
# the source-doc placeholders still cover that case in CI.
# ============================================================================

import os as _os_mod
import urllib.parse as _urlparse_mod
from pathlib import Path as _Path_mod


# Cross-platform absolute-path detector.
#
# Matches paths that start with:
#   - Windows drive: C:\ C:/ D:\ etc.
#   - Unix absolute (selective prefixes — full /-anchored scan would false-positive
#     on Markdown lists, URL paths, etc.):
#       /Users/, /home/, /var/, /tmp/, /root/, /opt/, /sessions/, /Volumes/, /mnt/
#   - Cygwin/MSYS-style Windows: /c/Users/, /c/users/
#   - Home-relative: ~/
#
# Lookbehind `(?<![A-Za-z0-9.])` prevents false positives on HTTP/HTTPS URLs:
# `https://example.com/Users/foo` — char before `/Users` is `m` (alnum), no match.
# `computer:///Users/foo` — char before `/Users` is `/` (not alnum), MATCHES
# (which is what we want — Rule 3 `computer:///` hrefs must also resolve under
# the user's workspace).
#
# Path body excludes whitespace + HTML/URL-terminating characters so that a
# path embedded in an `href="..."` attribute or a parenthetical stops cleanly.

_PATH_LEAK_PATTERN = _leak_re.compile(
    r"""
    (?<![A-Za-z0-9.])                                              # not in mid-URL/mid-word
    (?:
        [A-Za-z]:[\\/]                                             # Windows: C:\ or C:/
      | /(?:Users|home|var|tmp|root|opt|sessions|c|mnt|Volumes)/   # Unix absolute (selective)
      | ~/                                                          # Home-relative
    )
    [^\s<>"'`)\]]+                                                  # initial non-whitespace run
    (?:                                                             # zero-or-more continuations
        [\ \t][^\s<>"'`)\]/]+/[^\s<>"'`)\]]*                        # `\s<word>/<more>` — folder with embedded space
    )*
    """,
    _leak_re.IGNORECASE | _leak_re.VERBOSE,
)


def _normalize_path_for_prefix_match(p):
    """Canonicalize a path string for prefix comparison.

    - URL-decode (`%20` → space) so encoded paths inside `computer:///` hrefs
      compare equal to the resolved workspace.
    - Collapse `\\` to `/`.
    - Lowercase the Windows drive letter (`C:` → `c:`) so case differences in
      drive letters don't false-negative.
    - Strip trailing sentence punctuation that's almost certainly not part of
      the path (`.`, `,`, `;`, `:`, `!`, `?`, `)`, `"`, `'`, backtick).

    The path body itself is NOT lowercased — folder names can be case-sensitive
    on Unix. Callers that want case-insensitive comparison lowercase both
    sides explicitly.
    """
    if not p:
        return ""
    while p and p[-1] in '.,;:!?)"\'`':
        p = p[:-1]
    try:
        p = _urlparse_mod.unquote(p)
    except Exception:
        pass
    p = p.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0].lower() + p[1:]
    return p


def _resolve_workspace_prefix():
    """Resolve the user's workspace folder absolute path per CONTRACT.md Rule 22.

    Discovery: $CLAUDE_CODE_TMPDIR → strip trailing `/tmp` to get the session
    dir → look for any subdirectory under `<session>/mnt/` that contains an
    `_hq/` folder. That subdirectory's absolute path IS the workspace.

    Returns None when:
      - CLAUDE_CODE_TMPDIR is not set (local pytest, non-Cowork environment)
      - The mnt directory doesn't exist
      - No mounted folder contains an `_hq/` child

    Callers that get None should NOT validate paths — they have no trusted
    prefix to compare against. The static grep test
    (`tests/run_no_hardcoded_drive_test.py`) covers source-doc leaks in CI,
    so a runtime no-op here is acceptable.
    """
    tmpdir = _os_mod.environ.get("CLAUDE_CODE_TMPDIR")
    if not tmpdir:
        return None
    session_dir = tmpdir.rstrip("/").rstrip("\\")
    if session_dir.endswith("/tmp") or session_dir.endswith("\\tmp"):
        session_dir = session_dir[:-4]
    mnt = _Path_mod(session_dir) / "mnt"
    try:
        if not mnt.is_dir():
            return None
        for child in mnt.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if (child / "_hq").is_dir():
                return str(child)
            # One level deeper (covers some Cowork connect layouts)
            for grandchild in child.iterdir():
                if grandchild.is_dir() and (grandchild / "_hq").is_dir():
                    return str(grandchild)
    except (OSError, PermissionError):
        return None
    return None


def _resolve_plugin_root():
    """Resolve the installed plugin's absolute path per CONTRACT.md Rule 22.

    Discovery: $CLAUDE_CODE_TMPDIR → strip trailing `/tmp` → look at
    `<session>/mnt/.remote-plugins/plugin_*/` and return the first match.

    Returns None when CLAUDE_CODE_TMPDIR is not set or the .remote-plugins
    directory doesn't exist.
    """
    tmpdir = _os_mod.environ.get("CLAUDE_CODE_TMPDIR")
    if not tmpdir:
        return None
    session_dir = tmpdir.rstrip("/").rstrip("\\")
    if session_dir.endswith("/tmp") or session_dir.endswith("\\tmp"):
        session_dir = session_dir[:-4]
    remote = _Path_mod(session_dir) / "mnt" / ".remote-plugins"
    try:
        if not remote.is_dir():
            return None
        for child in remote.iterdir():
            if child.is_dir() and child.name.startswith("plugin_"):
                return str(child)
    except (OSError, PermissionError):
        return None
    return None


def _resolve_session_mnt_prefix():
    """The Cowork session's `mnt/` directory absolute path. Any path under
    this prefix is by definition under the user's session (workspace folder,
    plugin folder, or another mounted connector folder). Used as a permissive
    safety net so that a path under `mnt/` that's not under the more specific
    workspace prefix (e.g., a path into another connected folder the user
    mounted) doesn't flag as a leak. Returns None outside Cowork.
    """
    tmpdir = _os_mod.environ.get("CLAUDE_CODE_TMPDIR")
    if not tmpdir:
        return None
    session_dir = tmpdir.rstrip("/").rstrip("\\")
    if session_dir.endswith("/tmp") or session_dir.endswith("\\tmp"):
        session_dir = session_dir[:-4]
    mnt = _Path_mod(session_dir) / "mnt"
    try:
        if mnt.is_dir():
            return str(mnt)
    except (OSError, PermissionError):
        return None
    return None


def _scan_for_path_leaks(text, *, workspace=None, plugin_root=None):
    """Return a list of (label, matched_substring) for every absolute filesystem
    path in `text` that does NOT fall under a trusted prefix.

    Trusted prefixes:
      1. `workspace` (the user's runtime-resolved workspace folder) — passed
         in explicitly or resolved via `_resolve_workspace_prefix()`.
      2. `plugin_root` (the installed plugin copy under `mnt/.remote-plugins/`)
         — passed in explicitly or resolved via `_resolve_plugin_root()`.
      3. The Cowork session's `mnt/` directory — auto-resolved via
         `_resolve_session_mnt_prefix()`. Catches paths under other mounted
         folders the user connected (e.g., a Drive folder mounted as a
         separate connector). These are still ON the user's machine, so they
         resolve cleanly when clicked — no 404 class of bug.

    If all three prefixes resolve to None (local pytest, non-Cowork
    environment, or workspace/plugin discovery failure), the scan no-ops
    and returns []. Callers passing explicit `workspace=` and `plugin_root=`
    can override the discovery for test isolation.
    """
    if not text:
        return []
    resolved_ws = workspace if workspace is not None else _resolve_workspace_prefix()
    resolved_pr = plugin_root if plugin_root is not None else _resolve_plugin_root()
    resolved_mnt = _resolve_session_mnt_prefix() if (workspace is None and plugin_root is None) else None
    if not resolved_ws and not resolved_pr and not resolved_mnt:
        return []
    safe_prefixes = []
    for raw_prefix in (resolved_ws, resolved_pr, resolved_mnt):
        if not raw_prefix:
            continue
        norm = _normalize_path_for_prefix_match(raw_prefix).rstrip("/")
        if norm:
            safe_prefixes.append(norm.lower())
    findings = []
    seen = set()
    for m in _PATH_LEAK_PATTERN.finditer(text):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        norm = _normalize_path_for_prefix_match(raw).lower()
        if any(norm.startswith(prefix) for prefix in safe_prefixes):
            continue
        findings.append(("path-leak (not under workspace; click would 404)", raw))
    return findings


# ============================================================================
# All-batch button widget mode (v2.11.0+) — public function defined here so it's
# visible alongside render_chat_output in the first ~350 lines of the file.
# Helpers (_md_to_html, _render_widget_*, _WIDGET_CSS, _WIDGET_JS_TEMPLATE,
# _count_total_selectable_items) are defined further down. Python resolves
# names at call time, so the forward reference is fine.
# ============================================================================


def _render_all_clear_summary(data: dict, wrapper: str = "document") -> str:
    """v2.14.19+ — render the canonical 'nothing pressing' empty-state widget.

    Used when an orchestrator's bucket filters produced zero qualifying items
    AND the orchestrator wants to surface the workspace state ("here's what's
    on the books — none of it needs your attention right now") rather than a
    bare empty header.

    Required fields:
      - header (str): top-line summary, e.g. "Commitments — nothing needs your
        attention this morning."

    Optional fields:
      - sub_header (str): one-line context, e.g. "Monday, May 4 · 8:30 AM check"
      - counters (list of {label, value}): 2-4 metric cards, rendered as a grid.
      - summary_line (str): callout box content, e.g. "Nothing overdue, nothing
        due in the next 3 days, and nothing has been sitting open long enough to
        be aging."
      - tracked_items (list of {direction, title, due}): read-only line list of
        what IS on the books — visible context but no action buttons.
        `direction` ∈ {"You owe", "Owed to you", "Self"}; `due` is a plain-English
        string ("today", "due May 25", "undated", etc.) — orchestrator MUST
        compute against stored due_date per the date-prose rule (see
        orchestrator-commitments.md).
      - footer (str, optional): single one-line footer note. NO buttons.
        Improvising buttons is what got us here in the first place — empty-state
        widgets do not have actions.

    No actions are rendered, no apply-all footer, no validators run (no item
    actions to validate). Leak scanner still runs on the rendered HTML.
    """
    parts: list[str] = []
    parts.append('<div class="cr-card cr-card-all-clear">')
    parts.append('<div class="cr-brand-strip">' + _BRAND_LOGO_SVG + '</div>')

    header = data.get("header", "")
    if header:
        parts.append(f'<div class="cr-header">{_md_to_html(header)}</div>')

    sub_header = data.get("sub_header", "")
    if sub_header:
        parts.append(f'<div class="cr-sub-header">{_md_to_html(sub_header)}</div>')

    counters = data.get("counters") or []
    if counters:
        # SPEC OUT2 §2b — shared tile fragment (markup identical to the old
        # inline loop). validate=False: counters are R4-verbatim headline
        # numbers (0 is data; >5 buckets is legitimate).
        parts.append(_build_tile_band_html(counters, validate=False))

    summary_line = data.get("summary_line", "")
    if summary_line:
        parts.append(f'<div class="cr-summary-callout">{_md_to_html(summary_line)}</div>')

    tracked_items = data.get("tracked_items") or []
    if tracked_items:
        parts.append('<div class="cr-tracked-items-header">WHAT&apos;S ON THE BOOKS</div>')
        parts.append('<div class="cr-tracked-items">')
        for ti in tracked_items:
            direction = _html_mod.escape(str(ti.get("direction", "")))
            title = _md_to_html(ti.get("title", ""))
            due = _html_mod.escape(str(ti.get("due", "")))
            parts.append(
                f'<div class="cr-tracked-row">'
                f'<span class="cr-tracked-direction">{direction}</span>'
                f'<span class="cr-tracked-title">{title}</span>'
                f'<span class="cr-tracked-due">{due}</span>'
                f'</div>'
            )
        parts.append('</div>')

    footer = data.get("footer", "")
    if footer:
        parts.append(f'<div class="cr-all-clear-footer">{_md_to_html(footer)}</div>')

    parts.append('</div>')
    content = "".join(parts)
    css = _compose_widget_css(content) + _ALL_CLEAR_CSS_MIN
    assembled: list[str] = []
    if wrapper == "document":
        assembled.append("<!DOCTYPE html>")
        assembled.append('<html lang="en"><head><meta charset="utf-8">')
    assembled.append(f"<style>{css}</style>")
    if wrapper == "document":
        assembled.append("</head><body>")
    assembled.append(content)
    if wrapper == "document":
        assembled.append("</body></html>")
    html = "".join(assembled)

    # Run the leak scanner on the rendered HTML — same Rule 4 enforcement as
    # the standard widget path. The CanonicalActionError + DataShapeError gates
    # are skipped because there are no actions to validate.
    validate_chat_output(html)

    return html


_ALL_CLEAR_CSS = """
.cr-card-all-clear { padding: 16px 20px; }
.cr-summary-callout { margin: 12px 0; padding: 10px 14px; background: #14110F; border-left: 3px solid #4A6B8C; border-radius: 0 6px 6px 0; color: #B5A998; font-size: 13px; line-height: 1.5; }
.cr-tracked-items-header { margin-top: 16px; font-size: 11px; color: #8C7A65; text-transform: uppercase; letter-spacing: 0.06em; padding-bottom: 4px; border-bottom: 1px solid #2A2520; }
.cr-tracked-items { display: flex; flex-direction: column; }
.cr-tracked-row { display: grid; grid-template-columns: 110px 1fr auto; gap: 12px; padding: 8px 0; border-bottom: 1px solid #1A1714; align-items: baseline; }
.cr-tracked-row:last-child { border-bottom: none; }
.cr-tracked-direction { font-size: 12px; color: #8C7A65; }
.cr-tracked-title { font-size: 13px; color: #E8E0D6; }
.cr-tracked-due { font-size: 12px; color: #B5A998; white-space: nowrap; }
.cr-all-clear-footer { margin-top: 14px; font-size: 12px; color: #8C7A65; font-style: italic; }
"""


def _render_onboarding_setup(data: dict, wrapper: str = "document") -> str:
    """v3.4.1+ — render the onboarding Step 1 setup widget.

    Used by `command-room-onboarding/SKILL.md` Step 1 to surface the 3-4
    setup questions (role / day-to-day note / email exclusions / timezone)
    as a single chat-action widget with selectable buttons + textarea
    inputs, instead of a numbered markdown list.

    Replaces the v3.4.0 markdown-list selection-prompt pattern after M's
    2026-05-17 testing: *"before it would show you a widget within the
    chat where you can select answers to the different questions and then
    write notes at the bottom."*

    Bypasses CANONICAL_ACTIONS validation — onboarding options are
    selection labels (e.g. `run-single-company`, `pacific`), not action
    verbs from the orchestrator vocabulary. Leak scanner still runs on
    the rendered HTML.

    Reuses the standard widget's CSS / JS / brand strip so look-and-feel
    matches orchestrator widgets. Same Apply-all submission shape — the
    user clicks Apply and the existing crApplyAll JS fires a single
    `apply choices: [...]` sendPrompt that apply-choices dispatches back
    to onboarding's Reply handling section.

    Required fields:
      - header (str): top-line, e.g. "Quick setup — three questions"
      - items (list): one entry per question, each with:
          - n (int|str): item id (matches the Apply payload's `n`)
          - icon (str, optional): single emoji for the head row
          - question (str): the question text
          - context_tag (str, optional): grey context line under the question
          - options (list): one entry per button, each with:
              - action (str): canonical lowercase label (e.g. "pacific")
              - label (str): display text on the button (e.g. "Pacific (LA/SF/Seattle)")
              - input_type (str, optional): "none" (default) or "textarea-text"
                  for "Other" buttons that open a textarea on click
              - placeholder (str, optional): textarea placeholder text

    Optional fields:
      - sub_header (str): subheader line under the header
      - footer_note (str): small italic note above the Apply button
    """
    items = data.get("items", [])
    total = len(items)

    parts: list[str] = []
    parts.append('<div class="cr-card cr-card-onboarding-setup">')
    parts.append('<div class="cr-brand-strip">' + _BRAND_LOGO_SVG + '</div>')

    header = data.get("header", "")
    if header:
        parts.append(f'<div class="cr-header">{_md_to_html(header)}</div>')

    sub_header = data.get("sub_header", "")
    if sub_header:
        parts.append(f'<div class="cr-sub-header">{_md_to_html(sub_header)}</div>')

    parts.append('<div class="cr-body">')
    for item in items:
        parts.append(_render_onboarding_setup_item(item))
    parts.append("</div>")

    footer_note = data.get("footer_note", "")
    if footer_note:
        parts.append(f'<div class="cr-onboarding-footer-note">{_md_to_html(footer_note)}</div>')

    parts.append('<div class="cr-footer">')
    parts.append(
        f'<div class="cr-counter">Answered: <strong id="cr-count">0</strong> of {total}</div>'
    )
    parts.append('<div class="cr-footer-actions">')
    parts.append('<button class="cr-btn-apply" id="cr-apply" type="button" disabled>Apply all</button>')
    parts.append('<button class="cr-btn-secondary" id="cr-clear" type="button">Reset</button>')
    parts.append("</div></div>")
    parts.append("</div>")

    content = "".join(parts)
    js = _compose_widget_js(content).replace("__TOTAL_ITEMS__", str(total))
    # W4 (Phase 3) — bake the emitting surface's id into the widget so every
    # Apply-all tuple carries {src}. Orchestrator data views pass source_skill;
    # legacy/absent -> empty string -> apply-choices uses the fire-marker fallback.
    js = js.replace("__CR_SRC__", _json_mod.dumps(str(data.get("source_skill") or "")))
    css = _compose_widget_css(content) + _ONBOARDING_SETUP_CSS_MIN
    assembled: list[str] = []
    if wrapper == "document":
        assembled.append("<!DOCTYPE html>")
        assembled.append('<html lang="en"><head><meta charset="utf-8">')
    assembled.append(f"<style>{css}</style>")
    if wrapper == "document":
        assembled.append("</head><body>")
    assembled.append(content)
    assembled.append(f"<script>{js}</script>")
    if wrapper == "document":
        assembled.append("</body></html>")
    html = "".join(assembled)

    # Leak scanner runs on the rendered HTML — same Rule 4 enforcement as
    # the standard widget path. Action-verb validators skipped (onboarding
    # options are not in CANONICAL_ACTIONS by design).
    scannable = _re_mod.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re_mod.DOTALL)
    scannable = _re_mod.sub(r"<script[^>]*>.*?</script>", "", scannable, flags=_re_mod.DOTALL)
    validate_chat_output(scannable)

    # Structural wrapper check: every option with input_type "textarea-text"
    # must have its matching <div class="cr-action-input"> wrapper or the
    # textarea won't open on click.
    validate_rendered_widget(html)

    return html


def _render_onboarding_setup_item(item: dict) -> str:
    """Render one onboarding setup question as a widget item.

    HTML shape matches the standard widget's `.cr-item` so the existing
    crToggle / crApplyAll JS (selection toggling, textarea open, Apply
    dispatch) handles clicks without modification.
    """
    n = item.get("n")
    if n is None:
        raise ValueError("Onboarding setup item missing required 'n' field")

    safe_n = _html_mod.escape(str(n), quote=True)
    parts = [f'<div class="cr-item" data-item-n="{safe_n}">']

    # Head row — number + icon + question text
    head_parts = [f'<span class="cr-item-num"><strong>{_html_mod.escape(str(n))}.</strong></span>']
    icon = item.get("icon", "")
    if icon:
        head_parts.append(f'<span class="cr-item-icon">{_html_mod.escape(icon)}</span>')
    question = item.get("question", "")
    if question:
        head_parts.append(
            f'<span class="cr-item-name"><strong>{_md_to_html(question)}</strong></span>'
        )
    parts.append(f'<div class="cr-item-head">{" ".join(head_parts)}</div>')

    context_tag = item.get("context_tag", "")
    if context_tag:
        parts.append(f'<div class="cr-onboarding-context">{_md_to_html(context_tag)}</div>')

    # Buttons + input wrappers
    options = item.get("options", [])
    buttons_html = []
    inputs_html = []
    for opt in options:
        action = opt.get("action", "")
        label = opt.get("label", action)
        input_type = opt.get("input_type", "none")
        placeholder = opt.get("placeholder", "")

        safe_action = _html_mod.escape(action, quote=True)
        safe_label = _html_mod.escape(label)
        safe_input_type = _html_mod.escape(input_type, quote=True)

        buttons_html.append(
            f'<button class="cr-action" type="button" '
            f'data-n="{safe_n}" '
            f'data-action="{safe_action}" '
            f'data-input-type="{safe_input_type}" '
            f'onclick="crToggle(this)">{safe_label}</button>'
        )

        if input_type == "textarea-text":
            safe_placeholder = _html_mod.escape(placeholder, quote=True)
            inputs_html.append(
                f'<div class="cr-action-input" '
                f'data-input-for-n="{safe_n}" '
                f'data-input-for-action="{safe_action}" '
                f'data-input-type="{safe_input_type}" '
                f'style="display:none;">'
                f'<textarea class="cr-input-field" '
                f'placeholder="{safe_placeholder}" '
                f'rows="2"></textarea>'
                f'</div>'
            )

    if buttons_html:
        parts.append(f'<div class="cr-item-actions">{"".join(buttons_html)}</div>')
    if inputs_html:
        parts.append(f'<div class="cr-item-inputs">{"".join(inputs_html)}</div>')

    parts.append("</div>")  # close cr-item
    return "".join(parts)


_ONBOARDING_SETUP_CSS = """
.cr-card-onboarding-setup { padding: 16px 20px; }
.cr-card-onboarding-setup .cr-item { margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid #1A1714; }
.cr-card-onboarding-setup .cr-item:last-child { border-bottom: none; margin-bottom: 0; }
.cr-onboarding-context { font-size: 12px; color: #8C7A65; margin: 4px 0 8px 28px; line-height: 1.4; font-style: italic; }
.cr-onboarding-footer-note { font-size: 12px; color: #8C7A65; font-style: italic; margin: 8px 0 12px; padding: 0 4px; }
"""


def render_chat_output_widget(data: dict, *, wrapper: str = "document") -> str:
    """Render a full chat output as self-contained HTML for `mcp__visualize__show_widget`.

    The orchestrator passes the same data view shape used for `render_chat_output`
    (markdown mode). This function returns an HTML string with embedded CSS + JS
    that renders the same content as an interactive button widget.

    **Wrapper modes (v3.13.0+):**

      - `wrapper="document"` (DEFAULT — back-compat): emits a complete HTML
        document with `<!DOCTYPE>`, `<html>`, `<head>`, `<body>`. Suitable for
        environments that want a standalone HTML file (the legacy behavior all
        callers got pre-v3.13.0). Cowork's chat-markdown renderer can accept
        this form too (it strips the chrome internally).

      - `wrapper="fragment"`: emits the inner widget content only — `<style>`
        + content `<div>` + trailing `<script>`. **No `<!DOCTYPE>`, `<html>`,
        `<head>`, `<body>`.** Required by `mcp__visualize__show_widget`, whose
        documented contract is "No DOCTYPE, `<html>`, `<head>`, or `<body>` —
        just content fragments." Scheduled-task orchestrators must call with
        `wrapper="fragment"` when relaying the rendered HTML through the
        visualize tool. See the 2026-05-20 fragment-render handoff for
        background — the ZERO-MANIPULATION CONTRACT requires the renderer (not
        the agent) to own the output shape, so we expose this as a first-class
        renderer mode rather than telling agents to regex-strip the chrome.

    The functional wrappers + validators (cr-action-input blocks, Apply-all
    handler, data-input-for-n / data-input-for-action attributes, the
    leak-scanner pass) are IDENTICAL between the two modes — only the
    document chrome differs. validate_rendered_widget accepts both shapes
    (v3.13.0+).

    Click behavior: per-item buttons toggle local selection state. "Apply all"
    fires one consolidated `apply choices: [...]` sendPrompt. See
    `shared/CHAT_ACTION_WIDGET.md` for the full spec.

    Returns: HTML string. The orchestrator passes this to
    `mcp__visualize__show_widget`.

    Validation chain (v2.13.0+ — per shared/CONTRACT.md):
      1. Canonical-action validator runs BEFORE rendering. Raises
         CanonicalActionError if any item / sub_item / bulk action is not in
         CANONICAL_ACTIONS (or the specific-org variant pattern).
      2. After rendering, the leak scanner runs over the produced HTML.
         Raises LeakDetectedError if any forbidden pattern matches.

    Both errors are recoverable at the orchestrator level — fix the data view,
    don't add the offending verb / pattern to the allow-list. The contract is
    the contract.
    """
    # v2.14.19+ — empty-state "all clear" branch. When the orchestrator finds
    # zero items qualifying for any bucket, it should pass widget_mode:
    # "all_clear_summary" instead of improvising raw HTML. This mode is the
    # FIX for the v2.14.18 Commitments-empty-state bug where the agent
    # bypassed the renderer entirely and hand-typed the widget HTML, including
    # a hardcoded "Needing action: 0" counter and four model-invented bottom
    # buttons. Bypassing the renderer also bypassed the validators (canonical
    # actions, data shape, leak scanner) — three contracts broken at once.
    # Giving the canonical path a good empty-state answer removes the
    # incentive to improvise.
    widget_mode = data.get("widget_mode", "all_batch_widget")
    if widget_mode == "all_clear_summary":
        return _render_all_clear_summary(data, wrapper=wrapper)
    if widget_mode == "onboarding_setup":
        return _render_onboarding_setup(data, wrapper=wrapper)

    # Gate 1: canonical-action validator (raises CanonicalActionError on miss)
    _validate_canonical_actions(data)

    # Gate 1b (v2.14.1+): data-shape validator (raises DataShapeError on miss).
    # Catches email items missing required send/edit/draft actions AND items
    # with edit-then-* actions but blank metadata/body. Per M's Apr 30 v2.14.1
    # testing: Drew's "Edit then send didn't open" was traced to data-shape
    # inconsistency, not action-label misspelling.
    _validate_data_shape(data)

    # Gate 1c (v2.14.38+): Pulse richness validator (raises PulseRichnessError).
    # Catches bare person-dormant cards: missing mandatory metadata rows, bare
    # "Last contact" values without a topic, OR source-thread URLs without an
    # `original_thread` block to render the inbox-style accordion. Per M's
    # 2026-05-07 testing on Sam's bare card.
    _validate_pulse_richness(data)

    # Gate 1d (v3.13.8+): send-class chrome must carry a valid email.
    # Closes Bug #44 (canonical renderer dead-chrome for degraded items).
    # Items with send/draft/edit-then-send actions must have a parseable
    # email address in metadata To:. Items that can't be sent should use
    # the `add email then send` recovery verb instead.
    _validate_send_class_email_addresses(data)

    # Gate 1e (SPEC GATE1, v3.20.x): turn-level voice-tell backstop. Scans every
    # email-shaped item's body for banned voice tells even when no composer ran
    # its Step-2 gate — this is the deterministic backstop for the email/chat
    # surface that never reaches brief_writer.make_brief. NON-BLOCKING by design
    # (stderr-warn only; the renderer has no per-client allow_phrases context, so
    # blocking here would punish a calibrated voice). Best-effort + never raises.
    try:
        from turn_backstop import scan_data_view_for_tells
        # workspace_root self-resolves inside the backstop (SPEC GATE2 D4) so the
        # emit branch actually fires from the renderer call-site — the GATE1 wiring
        # gap that left the backstop scanning but writing nothing detectable.
        scan_data_view_for_tells(data, source_skill=data.get("source_skill"))
    except Exception:
        # The backstop must never break a render. The hard voice gate still lives
        # in make_brief for the .docx surface.
        pass

    sections = data.get("sections", [])
    total = _count_total_selectable_items(sections)
    # WG1-A D-A5 — a single-item widget drops the batch footer (counter / Apply
    # all / Reset / Snooze rest); a button click dispatches directly. The
    # `cr-card-single` marker is the single source of truth the JS reads for
    # direct-dispatch mode and validate_rendered_widget reads to exempt the
    # suppressed footer from the F-58 feedback contract.
    single_item = (total == 1)

    # T2.2 — content renders FIRST; the <style>/<script> scaffold is composed
    # from what the content actually contains (conditional emission).
    parts: list[str] = []
    parts.append('<div class="cr-card cr-card-single">' if single_item
                 else '<div class="cr-card">')
    # Brand strip — inline Chalette Command Room stacked logo (v2.12.2+).
    # SVG colors adapted for dark widget background:
    #   "C" stays brand gold #B88B4A (works on either bg)
    #   "halette" flipped from #1A1714 (logo's dark) to #E8E0D6 (warm white)
    #   divider line flipped from #1A1714 to #5E4F3F
    #   "COMMAND ROOM" caps flipped from #1A1714 to #B88B4A (brand gold)
    parts.append('<div class="cr-brand-strip">' + _BRAND_LOGO_SVG + '</div>')

    header = data.get("header", "")
    if header:
        parts.append(f'<div class="cr-header">{_md_to_html(header)}</div>')

    sub_header = data.get("sub_header", "")
    if sub_header:
        parts.append(f'<div class="cr-sub-header">{_md_to_html(sub_header)}</div>')

    # v4.5.2 S2 (F-18 convergence) — optional header stat tiles. The
    # full-list triage layout M picked opens with headline-bucket tiles
    # (Open / You owe / Owed to you / Unowned / Unconfirmed) rendered
    # VERBATIM from the canonical loader's bucket export (R4). Same markup
    # the all-clear summary already used.
    #
    # SPEC OUT2 §2b — both header-band keys render through the shared
    # component fragment (components.build_tile_band_html), so the chat band
    # and brief_writer's .docx band are one implementation (F-60):
    #   - `counters` (legacy key): rendered as-passed, validate=False —
    #     R4-verbatim headline numbers where 0 is data and more than 5
    #     buckets is legitimate. Labels/values unchanged.
    #   - `tiles` (OUT2 key): the docx-parity band — same {label, value}
    #     shape make_brief consumes, validated by the component contract
    #     (drop-empty refusal, 5-tile band cap). Pass the SAME list to both
    #     surfaces and they provably render the same values/labels/order.
    counters = data.get("counters") or []
    if counters:
        parts.append(_build_tile_band_html(counters, validate=False))
    tiles = data.get("tiles") or []
    if tiles:
        parts.append(_build_tile_band_html(tiles, validate=True))

    parts.append('<div class="cr-body">')
    for section in sections:
        parts.append(_render_widget_section(section))
    parts.append("</div>")

    # Paginate-by-design position line (T2, F2 rework). When the data view was
    # sliced to one page (unbounded surfaces: commitments full set, Staff
    # Meeting queue), show WHERE the reader is and teach the `show more` verb
    # that re-fires the next page. Inert on single-page (bounded) fires.
    pagination = data.get("pagination") or {}
    if pagination.get("total_pages", 1) > 1:
        _pg = int(pagination.get("page", 1))
        _tp = int(pagination.get("total_pages", 1))
        _more = (' · say <code>show more</code> for the next page'
                 if pagination.get("has_more") else ' · end of the queue')
        parts.append(
            f'<div class="cr-pagination">Page {_pg} of {_tp}{_more}</div>'
        )

    quick_read = data.get("quick_read", "")
    if quick_read:
        parts.append(f'<div class="cr-quick-read"><strong>Quick read:</strong> {_md_to_html(quick_read)}</div>')

    save_confirmation = data.get("save_confirmation", "")
    if save_confirmation:
        parts.append(f'<div class="cr-sub-header">{_md_to_html(save_confirmation)}</div>')

    # v4.5.2 S2 (F-58/F-17) — live "N of M selected" counter + a visible
    # Apply-hold reason line. The reason line is the anti-silent-block
    # surface: whenever Apply is disabled because a selected action is
    # missing its required input, the reason renders HERE, next to the
    # button the user is staring at.
    # WG1-A D-A5 — batch chrome only on multi-item widgets. A single-item card
    # dispatches directly on click (crSingleDispatch); the counter/Apply/Reset/
    # Snooze-rest footer would be dead chrome, so it is suppressed.
    if not single_item:
        parts.append('<div class="cr-footer">')
        parts.append(
            f'<div class="cr-counter"><strong id="cr-count">0</strong> of {total} selected</div>'
        )
        parts.append('<div class="cr-footer-actions">')
        parts.append('<button class="cr-btn-apply" id="cr-apply" type="button" disabled>Apply all</button>')
        parts.append('<button class="cr-btn-secondary" id="cr-clear" type="button">Reset</button>')
        parts.append('<button class="cr-btn-secondary" id="cr-skip-all" type="button">Snooze rest (1 day)</button>')
        parts.append("</div>")
        parts.append('<div class="cr-apply-reason" id="cr-apply-reason" style="display:none;"></div>')
        parts.append("</div>")

    parts.append("</div>")

    content = "".join(parts)
    js = _compose_widget_js(content).replace("__TOTAL_ITEMS__", str(total))
    # W4 (Phase 3) — bake the emitting surface's id into the widget so every
    # Apply-all tuple carries {src}. Orchestrator data views pass source_skill;
    # legacy/absent -> empty string -> apply-choices uses the fire-marker fallback.
    js = js.replace("__CR_SRC__", _json_mod.dumps(str(data.get("source_skill") or "")))
    css = _compose_widget_css(content)
    assembled: list[str] = []
    if wrapper == "document":
        assembled.append("<!DOCTYPE html>")
        assembled.append('<html lang="en"><head><meta charset="utf-8">')
    assembled.append(f"<style>{css}</style>")
    if wrapper == "document":
        assembled.append("</head><body>")
    assembled.append(content)
    assembled.append(f"<script>{js}</script>")
    if wrapper == "document":
        assembled.append("</body></html>")
    html = "".join(assembled)

    # Gate 2: leak-scanner blocking gate (raises LeakDetectedError on miss).
    # We scan the human-content portion of the HTML — header, sub_header, item
    # bodies, sub-item summaries — NOT the embedded CSS/JS (which legitimately
    # references internal class names). Strip <style> and <script> blocks
    # before scanning so we don't false-positive on `_WIDGET_CSS` etc.
    scannable = _re_mod.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re_mod.DOTALL)
    scannable = _re_mod.sub(r"<script[^>]*>.*?</script>", "", scannable, flags=_re_mod.DOTALL)
    # v3.6.0+ — the path-leak scanner needs href values preserved so that bad
    # absolute paths inside `computer:///...` hrefs (the clickable artifact
    # URLs per Rule 3) get caught. Keep a copy with hrefs intact, then strip
    # hrefs from the ID-leak scannable (legitimate `_hq/meetings/<file>.docx`
    # in an href URL would otherwise trip the internal-path leak rule).
    paths_scannable = scannable
    scannable = _re_mod.sub(r'href="[^"]*"', 'href=""', scannable)
    # T3.1 (FB-13) — data-* attributes are the F2-sanctioned wire home for
    # ids (data-item-n / data-n / data-sub-id / data-note-for-n); a legacy
    # commitment id shaped `event_NNNN` in `data-item-n` tripped the
    # entity-ID pattern and refused a card whose VISIBLE text was clean
    # (live morning-brief brain card, 2026-07-16). Blank data-* values the
    # same way hrefs are blanked — every visible string stays fully scanned,
    # and validate_rendered_widget's visible-span check still refuses any
    # wire id that reaches row text.
    scannable = _re_mod.sub(r'(\bdata-[a-z-]+=")[^"]*(")', r"\1\2", scannable)
    validate_chat_output(scannable, paths_text=paths_scannable)

    # Gate 3 (v2.14.35+): self-validate the structural button-to-wrapper invariant
    # before returning. Per Cowork's 2026-05-07 follow-up after the v2.14.34 fix:
    # "every action whose data-input-type !== 'none' must have a matching
    # .cr-action-input wrapper". v2.14.34 made this a separate function
    # `validate_rendered_widget()` that the orchestrator must call between
    # render() and show_widget. v2.14.35 BAKES the same check into the renderer
    # itself — defense in depth. Even if the agent forgets the orchestrator-side
    # call, the renderer's own output can never have an invariant violation
    # without the renderer raising. (If the agent then post-mangles the HTML,
    # the orchestrator-side `validate_rendered_widget()` call still catches
    # that downstream — both gates remain useful.)
    validate_rendered_widget(html)

    return html


# ============================================================================
# Widget helpers (markdown→HTML, button rendering, item rendering, CSS/JS templates)
# ============================================================================

import html as _html_mod
import json as _json_mod
import re as _re_mod


# T2.2 display hygiene (RV-5), shared shape: what a WIRE id looks like when it
# leaks whole into visible row text. Used by validate_rendered_widget's
# visible-span check and by the sub-item renderer's label suppression (T3.1
# FB-13 — a commitment id as a sub-item's visible label is the same class).
_WIRE_ID_RE = _re_mod.compile(
    r"^(?:bp_[0-9a-f]{6,}"
    r"|(?:person|cru|org|project|dont_forget|schedule):\S+"
    r"|(?:commitment_seq|seq|event)_\d+"
    r"|cmt_\S+)$"
)


# ============================================================================
# Post-render structural validator (v2.14.34+)
# ============================================================================

def validate_rendered_widget(html: str, *, surface=None) -> None:
    """Post-render structural assertion: every action button that needs an
    input wrapper has its matching wrapper element in the rendered HTML.
    When `surface` declares an org/board/client/external audience, the
    PGUARD1 personal-content scan also runs BLOCKING (see the tail of this
    function); m_facing / undeclared surfaces never get that scan.

    Catches the failure mode where the orchestrator-firing agent
    post-processes the renderer's output between
    `render_chat_output_widget()` and `mcp__visualize__show_widget` and
    drops `<div class="cr-item-inputs">` blocks while "minifying" or
    "trimming" — leaving buttons with no wrappers to open.

    Per `shared/CONTRACT.md` Rule 22 (added v2.14.34): every widget
    orchestrator MUST call this function on the HTML it intends to ship,
    BEFORE invoking show_widget. Failure raises `WrapperContractError`
    listing every (n, action, input_type) tuple missing its wrapper.

    The fix when this raises is structural — re-render via the canonical
    path and pass the renderer's exact output to show_widget without
    modification. Do NOT try to add wrappers manually; do NOT add
    matching wrappers to suppress the error. The renderer is the single
    source of truth.

    Args:
      html: the rendered widget HTML string (return value of
        `render_chat_output_widget()`, or the post-processed string the
        agent intends to ship).

    Raises:
      WrapperContractError: when at least one input-needing button
        lacks its matching wrapper.

    Returns: None on success.
    """
    if not html or not isinstance(html, str):
        # Empty or non-string input — caller error, not a contract violation.
        # Let it pass; downstream show_widget will surface the real problem.
        return

    # Every action button (button-mode widgets — onboarding setup — plus any
    # legacy hand-relayed HTML): <button class="cr-action" ... data-n="X"
    # data-action="Y" data-input-type="Z" ...>
    # Order of attributes is renderer-controlled but we can't assume it. Use lookahead-style
    # extraction: find the button tag, then pull each attribute independently.
    btn_re = _re_mod.compile(r"<button\b[^>]*\bclass=\"cr-action\b[^\"]*\"[^>]*>", _re_mod.IGNORECASE)
    attr_n = _re_mod.compile(r"\bdata-n=\"([^\"]*)\"")
    attr_action = _re_mod.compile(r"\bdata-action=\"([^\"]*)\"")
    attr_input_type = _re_mod.compile(r"\bdata-input-type=\"([^\"]*)\"")

    buttons_needing_wrapper = []  # list of (n, action, input_type)
    for m in btn_re.finditer(html):
        tag = m.group(0)
        n_match = attr_n.search(tag)
        action_match = attr_action.search(tag)
        type_match = attr_input_type.search(tag)
        if not (n_match and action_match and type_match):
            # Buttons without all three attributes are pre-v2.14.x or malformed —
            # not our concern here.
            continue
        input_type = type_match.group(1)
        if not input_type or input_type == "none":
            continue
        # Decode HTML entities so the comparison matches wrapper attributes
        # (both are escaped at render time the same way, but be defensive).
        n_value = _html_mod.unescape(n_match.group(1))
        action_value = _html_mod.unescape(action_match.group(1))
        buttons_needing_wrapper.append((n_value, action_value, input_type))

    # T2.2 row diet — verb DROPDOWNS. This is the button contract PORTED to
    # selects, not a weakening: every <option> inside a `.cr-action-select`
    # whose data-input-type isn't "none" needs the same (n, action) wrapper a
    # button did — the select's data-n is the row id, the option's `value` is
    # the action.
    sel_re = _re_mod.compile(
        r"(<select\b[^>]*\bclass=\"[^\"]*\bcr-action-select\b[^\"]*\"[^>]*>)(.*?)</select>",
        _re_mod.IGNORECASE | _re_mod.DOTALL,
    )
    opt_re = _re_mod.compile(r"<option\b[^>]*>", _re_mod.IGNORECASE)
    attr_value = _re_mod.compile(r"\bvalue=\"([^\"]*)\"")
    selects_found = 0
    for m in sel_re.finditer(html):
        selects_found += 1
        open_tag, body = m.group(1), m.group(2)
        n_match = attr_n.search(open_tag)
        n_value = _html_mod.unescape(n_match.group(1)) if n_match else ""
        for om in opt_re.finditer(body):
            tag = om.group(0)
            v_match = attr_value.search(tag)
            type_match = attr_input_type.search(tag)
            if not (v_match and type_match):
                continue
            action_value = _html_mod.unescape(v_match.group(1))
            input_type = type_match.group(1)
            if not action_value or not input_type or input_type == "none":
                continue
            buttons_needing_wrapper.append((n_value, action_value, input_type))

    # Every wrapper: <div class="cr-action-input ..." ... data-input-for-n="X" data-input-for-action="Y" ...>
    wrap_re = _re_mod.compile(r"<div\b[^>]*\bclass=\"[^\"]*\bcr-action-input\b[^\"]*\"[^>]*>", _re_mod.IGNORECASE)
    attr_for_n = _re_mod.compile(r"\bdata-input-for-n=\"([^\"]*)\"")
    attr_for_action = _re_mod.compile(r"\bdata-input-for-action=\"([^\"]*)\"")

    wrappers_present = set()
    for m in wrap_re.finditer(html):
        tag = m.group(0)
        n_match = attr_for_n.search(tag)
        action_match = attr_for_action.search(tag)
        if not (n_match and action_match):
            continue
        n_value = _html_mod.unescape(n_match.group(1))
        action_value = _html_mod.unescape(action_match.group(1))
        wrappers_present.add((n_value, action_value))

    # v4.5.2 S2 (F-58/F-17) — visible-feedback contract. Any HTML that carries
    # action buttons must also carry: the pressed-state CSS (armed rows
    # visibly distinct), the live selection counter, and — when any action
    # requires an input — the inline reason element + the Apply-hold reason
    # line. This is what makes a hand-built widget (the F-58 variant whose
    # selection CSS was broken) fail loudly BEFORE show_widget instead of
    # shipping invisible toggles.
    has_action_buttons = bool(btn_re.search(html))
    has_action_selects = selects_found > 0
    if has_action_buttons or has_action_selects:
        feedback_missing = []
        # The pressed/armed-state rule must live in actual CSS — the JS also
        # names the classes (classList toggles), so scan only <style> blocks.
        style_blocks = "".join(
            _re_mod.findall(r"<style[^>]*>(.*?)</style>", html, flags=_re_mod.DOTALL)
        )
        if has_action_buttons and ".cr-selected" not in style_blocks:
            feedback_missing.append(
                "pressed-state CSS (.cr-selected rules inside a <style> block)"
            )
        # T2.2 — the select port of the same F-58 contract: an armed dropdown
        # must be visibly distinct before Apply.
        if has_action_selects and ".cr-select-armed" not in style_blocks:
            feedback_missing.append(
                "armed-state CSS (.cr-select-armed rules inside a <style> block)"
            )
        # WG1-A D-A5 — a single-item widget legitimately has no batch footer
        # (counter / Apply / Apply-hold reason); it dispatches directly on
        # click. The pressed-state CSS requirement below still applies. Match
        # the full card-open ELEMENT, `<` included — the JS carries a
        # `.cr-card-single` selector string that a bare substring test would
        # false-hit, and visible text can carry the bare ATTRIBUTE string too
        # (quotes are not escaped in text nodes, so a row name containing
        # `class="cr-card cr-card-single"` renders it verbatim). `<` IS
        # escaped in every text node, so only the renderer can produce the
        # element form — either false-hit would silently disable the
        # F-58/F-17 enforcement for the page.
        single_item = '<div class="cr-card cr-card-single">' in html
        if 'id="cr-count"' not in html and not single_item:
            feedback_missing.append('live selection counter (id="cr-count")')
        if 'id="cr-apply"' not in html and not single_item:
            feedback_missing.append('Apply button (id="cr-apply")')
        if 'data-input-required="1"' in html:
            if 'id="cr-apply-reason"' not in html and not single_item:
                feedback_missing.append(
                    'Apply-hold reason line (id="cr-apply-reason") — required '
                    "because at least one action needs an input"
                )
            if "cr-input-reason" not in html:
                feedback_missing.append(
                    "inline input reason element (.cr-input-reason) — required "
                    "because at least one action needs an input"
                )
        if feedback_missing:
            raise WidgetFeedbackContractError(
                "Rendered widget HTML has action buttons but is missing the "
                "visible-feedback layer:\n  - "
                + "\n  - ".join(feedback_missing)
                + "\n\nClicks with invisible state are the F-58 bug class. "
                "Render through render_chat_output_widget() and ship its "
                "output byte-for-byte — never hand-build an action widget."
            )

    missing = [
        (n, action, input_type)
        for (n, action, input_type) in buttons_needing_wrapper
        if (n, action) not in wrappers_present
    ]

    if missing:
        sample_lines = "\n".join(
            f"    - n={n!r} action={action!r} input_type={input_type!r}"
            for (n, action, input_type) in missing[:10]
        )
        more_note = "" if len(missing) <= 10 else f"\n    ... and {len(missing) - 10} more"
        raise WrapperContractError(
            f"Rendered widget HTML is missing input wrappers for "
            f"{len(missing)} action button(s) that need them.\n\n"
            f"This usually means the HTML was post-processed AFTER "
            f"`render_chat_output_widget()` returned and structural elements "
            f"were dropped. Common cause: agent 'minified' or 'trimmed' the "
            f"renderer's output before passing to show_widget. The fix is "
            f"to ship the renderer's HTML byte-for-byte WITHOUT modification.\n\n"
            f"Missing wrappers (button exists, wrapper does not):\n"
            f"{sample_lines}{more_note}"
        )

    # T2.2 display hygiene (RV-5 M feedback): wire ids must never render as
    # VISIBLE row text. Ids live in data-* attributes and action tuples only —
    # "bp_d27b6b5244bb." or "person:135." as a row number/title is plumbing on
    # screen. The canonical fix is `display_n` on the item (build_card_view
    # assigns it); this check catches any path that leaks the wire id anyway.
    # T3.1 (FB-13): cr-sub-id spans covered too — a sub-item whose sub_id is a
    # wire id would otherwise render it as a visible label unchecked.
    visible_spans = _re_mod.findall(
        r'<span class="cr-(?:item-(?:num|name)|sub-id)">(?:<strong>)?([^<]*)', html
    )
    id_leaks = []
    for text in visible_spans:
        visible = _html_mod.unescape(text).strip().rstrip(".")
        if visible and _WIRE_ID_RE.match(visible):
            id_leaks.append(visible)
    if id_leaks:
        raise LeakDetectedError(
            "Wire ids rendered as visible row text — refusing to post:\n  - "
            + "\n  - ".join(sorted(set(id_leaks))[:10])
            + "\nFix at the data-view layer: keep the wire id in `n` (it IS "
            "the dispatch id) and pass a sequential `display_n` for the "
            "visible row number; row titles come from the record's display "
            "name, never its id."
        )

    # SPEC PGUARD1 D2 — personal-content scan, surface-gated BLOCKING. Only
    # when the caller declares an org surface; owner widgets (the brief,
    # commitments, staff meeting) legitimately carry personal rows and never
    # pass an org tag. Import tolerance mirrors validate_chat_output.
    if surface is not None:
        try:
            from personal_leak import is_org_surface, scan_for_personal_leak
        except ImportError:
            import sys as _sys
            _sys.stderr.write(
                "[chat_output_renderer] WARN: personal_leak module missing — "
                "the org-surface personal-content widget scan did NOT run.\n"
            )
            return
        if is_org_surface(surface):
            findings = scan_for_personal_leak(html)
            if findings:
                lines = "\n  - ".join(
                    f"[{f['name']}] {f['match']!r}" for f in findings[:10]
                )
                raise LeakDetectedError(
                    f"Personal-lane content in a widget declared for the "
                    f"{surface!r} surface — refusing to post:\n  - {lines}\n"
                    "Fix at the data-view layer: personal reminders and "
                    "personal-tie rows render only on owner-facing surfaces. "
                    "Never re-tag the surface to bypass."
                )


# ============================================================================
# Widget helpers (markdown→HTML, button rendering, item rendering, CSS/JS templates)
# (Note: validate_rendered_widget is defined ABOVE this divider so the public
# API surface is grouped at the top of the helpers section.)
# ============================================================================


def _md_to_html(text: str) -> str:
    """Minimal markdown→HTML for the subset emitted by orchestrators.

    Handles: bold `**foo**`, italic `*foo*`, links `[label](url)`. HTML-escapes
    everything else first. Not a full markdown parser — just what we ship.
    """
    if not text:
        return ""
    s = _html_mod.escape(text, quote=False)
    # Links: [label](url) — do this before italic so `*` inside URLs is preserved
    s = _re_mod.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{_html_mod.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        s,
    )
    # Bold: **foo**
    s = _re_mod.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", s)
    # Italic: *foo* (single asterisk not preceded/followed by another asterisk)
    s = _re_mod.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def _render_original_thread_html(orig: dict) -> str:
    """Render the original email/source thread as a collapsible <details> block.

    Shown above the draft blockquote in v2.12.1+ for inbox + commitments items
    that have an `original_thread` field in the data shape. Lets the user expand
    to see what they're responding to without leaving the widget.

    Per Sam's Apr 30 Granola call: he wanted to see the original thread he was
    replying to, inline. v2.12.4 adds an "Open in Gmail / source" link at the top
    of the expanded body when `url` is present, per M's Apr 30 ask: "I dont see
    the link to see the original thread in gmail." The link uses target="_blank"
    so it opens in a new Cowork tab / external browser.
    """
    if not orig:
        return ""
    author = orig.get("author", "")
    date = orig.get("date", "")
    subject = orig.get("subject", "")
    body = orig.get("body", "")
    url = orig.get("url", "")

    # No displayable content at all → render no accordion. An empty <details>
    # shell that opens to nothing is worse than none (BUG: content-less
    # original_thread dicts rendered a doubled label + empty expandable body).
    if not url and not subject and not body:
        return ""

    summary_parts = []
    if author:
        summary_parts.append(_html_mod.escape(author))
    if date:
        summary_parts.append(_html_mod.escape(date))
    # When there's no author/date, the summary is just the "📨 Original thread"
    # label itself — no " — Original thread" tail (that produced the doubled
    # "Original thread — Original thread").
    summary_text = " · ".join(summary_parts) if summary_parts else ""

    body_html = ""
    if url:
        # Open-in-source link at top of expanded block. Label adapts to source type
        # so the user knows where it'll open.
        link_label = "Open in Gmail" if "mail.google.com" in url else (
            "Open in Granola" if "notes.granola.ai" in url else (
                "Open original" if url else ""
            )
        )
        if link_label:
            body_html += (
                f'<div class="cr-orig-openlink">'
                f'<a href="{_html_mod.escape(url, quote=True)}" target="_blank" rel="noopener">'
                f'↗ {link_label}</a></div>'
            )
    if subject:
        body_html += f'<div class="cr-orig-subject"><strong>Subject:</strong> {_html_mod.escape(subject)}</div>'
    if body:
        # Preserve line breaks in body; treat as plain text (not markdown)
        body_lines = body.splitlines()
        for line in body_lines:
            if line.strip():
                body_html += f'<div class="cr-orig-line">{_html_mod.escape(line)}</div>'
            else:
                body_html += '<div class="cr-orig-empty"></div>'

    summary_label = f"📨 Original thread — {summary_text}" if summary_text else "📨 Original thread"
    return (
        f'<details class="cr-orig-thread">'
        f'<summary class="cr-orig-summary">{summary_label}</summary>'
        f'<div class="cr-orig-body">{body_html}</div>'
        f"</details>"
    )


def _render_email_blockquote_html(metadata: list[tuple[str, str]], body_lines: list[str]) -> str:
    """Render an email draft preview as an HTML blockquote per CONVENTIONS_EMAIL_PREVIEW.md.

    metadata is the ordered key/value list (To, Cc, Subject, etc.). body_lines is
    the email body (each entry one line). Returns empty string if there's no
    metadata AND no body — caller decides whether to render a wrapper at all.
    """
    if not metadata and not body_lines:
        return ""
    # t3 FB-12: orchestrators sometimes paste the CONVENTIONS_EMAIL_PREVIEW
    # blockquote convention (`> ` line prefixes) straight into body_lines.
    # Those markers are markdown plumbing — the widget already draws the
    # quote bar with CSS, so a literal "> " renders as visible junk in the
    # displayed body. Storage is clean (M verified queued drafts land clean
    # in Superhuman) — strip at render only.
    body_lines = [_strip_blockquote_marker(l) for l in body_lines]
    parts = ['<blockquote class="cr-email-draft">']
    # Header lines (To, Cc, Subject) — bolded labels, mailto links handled via _md_to_html
    # v2.14.36+ — `Originally` is no longer rendered inline. The collapsible
    # `original_thread` accordion (rendered ABOVE the blockquote in
    # `_render_widget_item`) carries source-thread context now. If an
    # orchestrator still emits `("Originally", ...)` in metadata for backward
    # compat, defensively skip it so we don't double-render the source pointer
    # alongside the rich accordion. Mirrors inbox-triage one-for-one.
    for key, value in metadata:
        if not value:
            continue
        key_lower = key.lower() if key else ""
        if key_lower == "originally":
            continue  # v2.14.36+ — superseded by `original_thread` accordion
        if key_lower in ("to", "cc", "bcc", "subject"):
            parts.append(f'<div class="cr-eh"><strong>{_html_mod.escape(key)}:</strong> {_md_to_html(value)}</div>')
        else:
            # Other metadata renders as a normal labeled line
            parts.append(f'<div class="cr-em"><em>{_html_mod.escape(key)}:</em> {_md_to_html(value)}</div>')
    # Spacer between header and body (per CONVENTIONS_EMAIL_PREVIEW — blank `>` line)
    if metadata and body_lines:
        parts.append('<div class="cr-eb-spacer"></div>')
    # Body lines — t3 FB-10 (M ruling): the body is DIRECTLY editable. Click
    # into it and type; no Edit button. The wrapper carries the original
    # plain text in data-original so Apply can serialize the CURRENT
    # on-screen text and the orchestrator can diff rendered-vs-queued for the
    # voice-corrections log. (`.cr-eb` stays the line class — the
    # textarea-prepop JS and the mfe pre-population read it unchanged.)
    if body_lines:
        # data-original mirrors what innerText will read back (markdown
        # stripped, not raw `**bold**` source) so an untouched body never
        # false-positives as edited.
        original_plain = "\n".join(
            _visible_text(_md_to_html(l)) if l.strip() else "" for l in body_lines
        )
        parts.append(
            f'<div class="cr-eb-body" contenteditable="true" '
            f'spellcheck="false" '
            f'data-original="{_html_mod.escape(original_plain, quote=True)}">'
        )
        for line in body_lines:
            if line.strip():
                parts.append(f'<div class="cr-eb">{_md_to_html(line)}</div>')
            else:
                parts.append('<div class="cr-eb-empty"><br></div>')
        parts.append("</div>")
    parts.append("</blockquote>")
    return "".join(parts)


def _visible_text(html: str) -> str:
    """Tag-strip + entity-unescape a rendered line back to the plain text a
    browser's innerText would report. Exact for the minimal markdown subset
    `_md_to_html` emits (bold/italic/links — no nested block structure)."""
    return _html_mod.unescape(_re_mod.sub(r"<[^>]+>", "", html or ""))


def _strip_blockquote_marker(line: str) -> str:
    """t3 FB-12: drop a leading markdown blockquote marker (`> ` / lone `>`)
    from an email body line. Only the leading convention marker goes — a
    `>` mid-line (quoted reply text, comparisons) is content and stays."""
    if not isinstance(line, str):
        return line
    stripped = line.lstrip()
    if stripped.startswith("> "):
        return stripped[2:]
    if stripped == ">":
        return ""
    return line


def _detect_input_type(action: str) -> Optional[str]:
    """Detect what input affordance an action label needs (v2.12.4+).

    Returns one of:
        'multi-field-email' — 4 separate inputs (To, Cc, Subject, Body) all
                              pre-populated from item metadata + body_lines.
                              Triggered by `edit then send` / `edit then draft`
                              (combined edit + disposition for email items).
        'textarea-prepop' — single textarea pre-populated with body. Triggered
                            by bare `edit` (used in non-email contexts).
        'when-text' — single-line text input. Originally natural-language
                      datetime ("monday at 2", "tomorrow afternoon",
                      "2026-05-12") — replaces strict `date` pickers per M's
                      Apr 30 v2.12.4 ask; triggered by `[date]` / `[when]` /
                      `[time]` brackets. Also the short-value input for
                      `[org]` (v2.14.29+) and `[name]` / `[existing]` /
                      `[stage]` / `[status]` (v5.9.1+) — names and enum picks
                      are one short line, not paragraph text.
        'datetime' — strict datetime-local picker (legacy, unused by canonical
                     action set; kept for orchestrators that explicitly opt in
                     via `[date+time]` / `[datetime]` bracket).
        'textarea' — multi-line free-form input. Triggered by `[change]` /
                     `[text]` / `[reason]` / `[free-form change]` / `[context]`.
        None — no input needed (simple click-to-toggle).
    """
    if not action:
        return None
    a = action.strip().lower()
    # Multi-field edit (v2.12.3+) — combined edit+disposition for email items
    # v2.14.4+ — `draft` also opens multi-field edit (consolidates `to drafts`
    # + `edit then draft`; if you're saving to Drafts, you probably want to
    # review/edit first).
    if a.startswith("edit then ") or a == "draft":
        return "multi-field-email"
    # Bare `edit` for non-email contexts (don't-forget pending review etc.)
    if a == "edit":
        return "textarea-prepop"
    # v4.5.2 S2 — `add email then send` promised a single-field address input
    # since v3.13.8 but fell through to None here (no bracket placeholder), so
    # the button was a dead toggle: the documented input never opened. Same
    # silent-block family as F-17. Single-line email input, REQUIRED.
    if a == "add email then send":
        return "email-text"
    # Strict datetime picker (legacy, opt-in only)
    if "[date+time]" in a or "[datetime]" in a:
        return "datetime"
    # v2.12.4+ — natural-language datetime as free text. Captures `[date]`,
    # `[when]`, `[time]` (single time, not paired with date+time bracket above).
    if "[date]" in a or "[when]" in a or "[time]" in a:
        return "when-text"
    # v2.14.29+ — `[org]` placeholder for past-meetings new-person sub_items when
    # the org genuinely isn't determinable. Single-line text input (org names are
    # short — not paragraph text, so don't waste vertical space with a multi-line
    # textarea). Pre-v2.14.29 the `[org]` placeholder fell through to None and
    # the button was a dead toggle with no input affordance — Cowork diagnostic
    # 2026-05-06 D3 caught it.
    if "[org]" in a:
        return "when-text"  # reuse the single-line text-input wrapper
    # v5.9.1 — `[name]` (reassign to / theirs to / mark received from),
    # `[existing]` (same as), `[stage]` (move to), `[status]` (report) all
    # fell through to None, so the option was stamped required (F-17) but no
    # input box ever rendered: Apply held with "Reassign needs a name" and
    # there was nowhere to type one — the row dead-ended (customer bug
    # report 2026-08-04, my-plate Reassign; same silent-block family as the
    # D3 `[org]` and v4.5.2 `add email then send` fixes). Single-line input —
    # names, stages, and statuses are one short line.
    if "[name]" in a or "[existing]" in a or "[stage]" in a or "[status]" in a:
        return "when-text"
    # v5.9.1 — `[items]` (split into / add subitems) takes a LIST (newlines /
    # semicolons / ' / ' per commitment_state's parsers) — multi-line.
    if "[items]" in a:
        return "textarea"
    # Multi-line free-form (reasons, contexts, edits, decisions, types)
    # v2.14.5+ — `[type]` added for Pulse entity-proposal `confirm [type]`
    # / `edit [type]` so the user can override the inferred relationship_type
    # before confirming the new org/project record.
    if (
        "[change]" in a
        or "[text]" in a
        or "[reason]" in a
        or "[free-form change]" in a
        or "[context]" in a
        or "[decision]" in a
        or "[type]" in a
    ):
        return "textarea"
    return None


# v4.5.2 S2 (F-59) — display labels come from the verb taxonomy, one label
# per wire id, everywhere. This is what killed Resolved-vs-Done and
# Push-to-vs-Defer: `resolved` displays "Done", `push to [date]` displays
# "Defer", `skip` displays "Snooze (1 day)" — and every mute states its
# duration on the button. Never add a local label here; edit the table row.
# ONE ruled exception (UXR1 D2, M 2026-07-21): CLASS_DISPLAY_LABELS below
# relabels a verb per GRAMMAR CLASS (hygiene rows only today) — a table,
# never a call-site string, so the exception stays as auditable as the rule.
_DISPLAY_LABEL_OVERRIDES = dict(_TAXONOMY_DISPLAY_LABELS)


def _action_display_label(action: str) -> str:
    """Strip bracket placeholders + capitalize first letter for the visible button text (v2.12.2+).

    `push meeting [date]` → `Push meeting`
    `resolved [reason]` → `Resolved`
    `edit [change]` → `Edit`
    `send` → `Send`
    `draft` → `Draft` (v2.14.4+ consolidated form — replaced `to drafts` + `edit then draft`)
    `edit then send` → `Edit then send`
    `context [text]` → `Context` (v2.14.37+ unified context verb)
    `snooze 3d` → `Snooze (3 days)` (v2.14.38+ override)
    `snooze 14d` → `Snooze (14 days)` (v3.13.2+ intro-followup-check)
    `not relevant` → `Not relevant` (v2.14.38+; duration NEVER shown in UI)
    `landed` → `Landed`, `didnt land` → `Didn't land` (v3.13.2+ intro-followup-check)

    Per M's Apr 30 ask: "I would capitalize all first letters of tags."
    Underlying action_id (data-action attribute) stays lowercase for parsing
    consistency in apply-choices and reply handlers; only the display label is
    capitalized.
    """
    if not action:
        return ""
    # v2.14.38+ — check explicit overrides first
    if action.lower() in _DISPLAY_LABEL_OVERRIDES:
        return _DISPLAY_LABEL_OVERRIDES[action.lower()]
    # v2.14.29+ — for `[org]`-style placeholders that name a thing the user types
    # (org name, person name), strip the brackets but KEEP the inner word so the
    # button reads "Add as person to org" instead of "Add as person to" (which
    # was meaningless without context — Cowork diagnostic 2026-05-06 D3). This
    # only applies to placeholders that are NOUN labels (org/name/team) — content
    # placeholders like [text]/[change]/[reason] still strip fully because they
    # don't carry semantic meaning ("Edit text" is no clearer than "Edit").
    NOUN_PLACEHOLDERS = {"[org]", "[name]", "[team]"}
    a_lower = action.lower()
    for nph in NOUN_PLACEHOLDERS:
        if nph in a_lower:
            # Keep the inner word, drop the brackets
            stripped = action.replace(nph, nph[1:-1]).strip()
            if stripped:
                return stripped[0].upper() + stripped[1:]
            return stripped
    # Default: strip the entire bracket placeholder including its inner word
    stripped = _re_mod.sub(r"\s*\[[^\]]+\]\s*", "", action).strip() or action
    if stripped:
        return stripped[0].upper() + stripped[1:]
    return stripped


def _extract_email_fields(item: Optional[dict]) -> dict:
    """Pull To, Cc, Bcc, Subject, Body from an item's metadata + body_lines.

    Used by multi-field-email input rendering to pre-populate the 4 inputs.
    Strips markdown/mailto wrappers from To/Cc values so the textarea shows
    plain "sam@example.com" instead of "[sam@example.com](mailto:...)".
    """
    if not item:
        return {"to": "", "cc": "", "bcc": "", "subject": "", "body": ""}
    out = {"to": "", "cc": "", "bcc": "", "subject": "", "body": ""}
    for key, value in item.get("metadata", []):
        k = key.lower() if key else ""
        if k in out:
            # Strip markdown link syntax: [foo@example.com](mailto:foo@example.com) → foo@example.com
            stripped = _re_mod.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value or "")
            out[k] = stripped.strip()
    body_lines = item.get("body_lines", [])
    if body_lines:
        out["body"] = "\n".join(body_lines)
    return out


def _render_action_option(action: str, item_n, item: Optional[dict] = None,
                          label: Optional[str] = None) -> tuple[str, str]:
    """Render a single action as an <option> in the row's verb DROPDOWN
    (T2.2 row diet — M approved dropdowns over the six-button row; RV/FS-10
    density ask). Returns (option_html, input_html).

    item_n is the row's data-n (number, sub-letter id like '1a', or a wire id
    like a proposal id). item (optional) is the parent item dict; used to
    pre-populate multi-field edit inputs from the item's metadata + body_lines.
    label (UXR1 D2) is an explicit per-class display label from
    CLASS_DISPLAY_LABELS; None → the taxonomy label. The option `value`
    always carries the frozen wire id.

    The option's `value` carries the FULL action string (including brackets) —
    the SAME contract the per-row buttons carried — so apply-choices dispatch
    and the `apply choices:` wire format are byte-compatible. The F-17
    required-input payload (data-input-required / data-input-thing) rides on
    the option exactly as it rode on the button.

    input_html is empty string for actions without input affordances. Caller
    renders the select first, then all input wrappers stacked below.
    """
    safe_action = _html_mod.escape(action, quote=True)
    label = label if label is not None else _action_display_label(action)
    input_type = _detect_input_type(action)

    # v4.5.2 S2 (F-17) — required-input contract, unchanged: the requirement
    # + the missing-thing word ride on the OPTION so the widget JS needs no
    # separate lookup table.
    is_required = action.lower() in REQUIRED_INPUT_ACTION_IDS
    thing = required_input_thing(action) if is_required else ""

    # v5.9.1 backstop — a required-input action must NEVER render without an
    # input affordance: F-17 holds Apply on the missing value and the widget
    # JS has no field to read, so the row dead-ends with no way to comply
    # (the `[name]` bug class). A placeholder this detector doesn't know yet
    # gets the single-line default instead of a dead toggle.
    if input_type is None and is_required:
        input_type = "when-text"

    # Row diet: default attributes are OMITTED (absent dataset keys read as
    # falsy in the JS, and the validator skips type-less options exactly as
    # it skipped attribute-less buttons) — only input-bearing options carry
    # data-input-type, only required ones carry the F-17 payload.
    attrs = [f'value="{safe_action}"']
    if input_type:
        attrs.append(f'data-input-type="{input_type}"')
    if is_required:
        attrs.append('data-input-required="1"')
        attrs.append(f'data-input-thing="{_html_mod.escape(thing, quote=True)}"')
    opt_html = f'<option {" ".join(attrs)}>{_html_mod.escape(label)}</option>'
    input_html = _render_action_input_wrapper(
        action, item_n, item,
        input_type=input_type, label=label,
        is_required=is_required, thing=thing,
    )
    return opt_html, input_html


def _render_action_input_wrapper(
    action: str,
    item_n,
    item: Optional[dict],
    *,
    input_type: Optional[str],
    label: str,
    is_required: bool,
    thing: str,
) -> str:
    """The per-action input wrapper (`.cr-action-input`) — unchanged contract
    from the button era (v2.12.4+): wrappers stack below the verb control and
    open when their (n, action) is armed. Returns "" when the action needs no
    input."""
    safe_action = _html_mod.escape(action, quote=True)
    safe_n = _html_mod.escape(str(item_n), quote=True)

    if input_type is None:
        return ""

    # Inline reason element, baked into every required action's wrapper so
    # the F-17 feedback needs no DOM construction at click time. Hidden until
    # the validator flags the selection.
    reason_html = ""
    if is_required:
        reason_html = (
            f'<div class="cr-input-reason" style="display:none;">'
            f'{_html_mod.escape(label)} needs a {_html_mod.escape(thing)} '
            f"before Apply can run — type it above.</div>"
        )

    if input_type == "multi-field-email":
        # 4 separate fields pre-populated from item's metadata + body_lines.
        # User edits any of them. JS gathers all 4 into a structured input on Apply.
        # v2.12.5+ layout: To/Cc/Subject render with INLINE labels (label + input on
        # one line) so they take ~28px each; Body renders full-width below with the
        # label above and a tall textarea — body claims most of the vertical space,
        # which is what the user actually edits. Per M's v2.12.4 feedback: "the to:
        # subject: and cc: lines are way to big and take up too much of the screen,
        # you can barely see the body of the email."
        fields = _extract_email_fields(item)
        rows = []
        for fkey, flabel in [("to", "To"), ("cc", "Cc"), ("subject", "Subject")]:
            value = _html_mod.escape(fields.get(fkey, ""), quote=True)
            rows.append(
                f'<div class="cr-mfe-row cr-mfe-inline">'
                f'<label class="cr-mfe-label cr-mfe-label-inline">{flabel}</label>'
                f'<input class="cr-input-field cr-mfe-field cr-mfe-field-compact" type="text" data-field="{fkey}" value="{value}" />'
                f"</div>"
            )
        body_value = _html_mod.escape(fields.get("body", ""), quote=True)
        rows.append(
            f'<div class="cr-mfe-row cr-mfe-body">'
            f'<label class="cr-mfe-label">Body</label>'
            f'<textarea class="cr-input-field cr-mfe-field" data-field="body" rows="12">{body_value}</textarea>'
            f"</div>"
        )
        input_html = (
            f'<div class="cr-action-input cr-multi-field" data-input-for-n="{safe_n}" '
            f'data-input-for-action="{safe_action}" '
            f'data-input-type="multi-field-email" style="display:none;">'
            f'{"".join(rows)}'
            f"</div>"
        )
    elif input_type == "textarea-prepop":
        # Single textarea pre-populated with body (legacy non-email edit contexts).
        input_html = (
            f'<div class="cr-action-input" data-input-for-n="{safe_n}" '
            f'data-input-for-action="{safe_action}" '
            f'data-input-type="textarea-prepop" style="display:none;">'
            f'<textarea class="cr-input-field" rows="6" '
            f'placeholder="Edit the draft body directly. Apply submits the edited text."></textarea>'
            f"</div>"
        )
    elif input_type == "textarea":
        input_html = (
            f'<div class="cr-action-input" data-input-for-n="{safe_n}" '
            f'data-input-for-action="{safe_action}" '
            f'data-input-type="textarea" style="display:none;">'
            f'<textarea class="cr-input-field" rows="3" '
            f'placeholder="Type the change/instruction. Apply submits."></textarea>'
            f"</div>"
        )
    elif input_type == "when-text":
        # v2.12.4+ — free-text natural-language datetime input.
        # Replaces the strict date picker for `[date]` actions per M's Apr 30 ask:
        # "push meeting should be an open field and they can write monday at 2 or
        # tomorrow at 3 etc." Reply handler parses natural language at apply time.
        # v5.9.1 — when-text also serves non-datetime short values ([name],
        # [org], [stage], …); the date-flavored placeholder on a name box read
        # as the wrong field, so non-date/time things name themselves instead.
        if thing and thing not in ("date", "time"):
            ph = f"Type the {thing}"
        else:
            ph = "e.g. Friday, 5 (days), or a date"
        input_html = (
            f'<div class="cr-action-input" data-input-for-n="{safe_n}" '
            f'data-input-for-action="{safe_action}" '
            f'data-input-type="when-text" style="display:none;">'
            f'<input class="cr-input-field" type="text" '
            f'placeholder="{_html_mod.escape(ph, quote=True)}" />'
            f"{reason_html}"
            f"</div>"
        )
    elif input_type == "email-text":
        # v4.5.2 S2 — single-field address input for `add email then send`
        # (the input the verb documented since v3.13.8 but never rendered).
        input_html = (
            f'<div class="cr-action-input" data-input-for-n="{safe_n}" '
            f'data-input-for-action="{safe_action}" '
            f'data-input-type="email-text" style="display:none;">'
            f'<input class="cr-input-field" type="text" '
            f'placeholder="name@example.com" />'
            f"{reason_html}"
            f"</div>"
        )
    else:
        # date / time / datetime — hard pickers. Used only by actions that explicitly
        # need a strict format (kept for backward compat; no current canonical action
        # uses these — `[date]` now resolves to `when-text` per v2.12.4).
        type_attr = {"date": "date", "time": "time", "datetime": "datetime-local"}[input_type]
        input_html = (
            f'<div class="cr-action-input" data-input-for-n="{safe_n}" '
            f'data-input-for-action="{safe_action}" '
            f'data-input-type="{input_type}" style="display:none;">'
            f'<input class="cr-input-field" type="{type_attr}" />'
            f"{reason_html}"
            f"</div>"
        )
    return input_html


def _merge_later_verbs(actions: list) -> list:
    """t3 FB-3 (M ruling): a row offering BOTH `push to [date]` and a
    skip/snooze verb reads as two time-kicking options — the merged 'Later…'
    option (`push to [date]`, whose dispatch auto-routes defer-vs-snooze by
    ownership) covers both, so the separate skip/snooze DROPDOWN option is
    suppressed. Display-layer only: the skip wire stays frozen and
    dispatchable (chat phrase, in-flight widgets, the Snooze-rest footer),
    and rows without `push to [date]` keep their snooze option unchanged."""
    if not any(str(a).lower().startswith("push to") for a in actions):
        return list(actions)
    out = []
    for a in actions:
        low = str(a).lower()
        if low == "skip" or _re_mod.fullmatch(r"snooze \d+d", low):
            continue
        out.append(a)
    return out


# t3 FB-4 (M ruling): per-surface primary verbs render as visible one-tap
# buttons; the tail stays in the dropdown. Row-shape driven (not
# source_skill driven) so mixed surfaces route correctly: an email-shaped
# row inside the commitments chat still gets its primaries.
#
# FB-17 (M, 2026-07-19): the email card's primaries are Send / Draft / Snooze.
# A PLAIN email-draft card carries exactly those three, so its tail is empty —
# "no dropdown", per the ruling. A Waiting On chase row is also email-shaped
# but carries domain verbs (mark received, follow-up call) that cannot
# collapse into three buttons; those stay in the tail — the ruling is about
# the plain draft card, not the chase surface.
_EMAIL_SHAPE_MARKERS = ("send", "draft")   # detects an email-shaped row
_PRIMARY_EMAIL_VERBS = ("send", "draft", "snooze 3d")
_PRIMARY_COMMITMENT_VERBS = ("resolved", "mark done")

# WG1-A D-A1/D-A2 (M ruling 2026-07-20, big-test Findings Ledger rows 13/13b):
# ONE declarative grammar table replaces the two hardcoded email/commitment
# special-cases. Each row's most-likely action(s) promote to visible primary
# buttons; class detection is marker-based on the row's OWN verb set (rows
# self-describe by their verbs — no new data-view field, so legacy persisted
# views and every builder keep working unchanged). First matching class wins,
# so ORDER MATTERS: email is checked before delegated so a Waiting On chase row
# (email-shaped AND carrying `mark received`) still promotes Send/Draft/Snooze,
# not Nudge — FB-17's card behaviour is preserved. Wire ids stay frozen; this is
# display layout only. Buttons carry the CANONICAL taxonomy label + action id —
# never a locally-relabelled verb (the one-label-per-verb F-59 contract holds).
#
# Row = (class, marker verb-ids (any present ⇒ this class), primary verb-ids in
# display order). The ≤4 rule in _render_widget_item may additionally surface
# the tail as secondary buttons; primaries here decide what's promoted + styled.
PRIMARY_VERB_GRAMMAR = (
    ("email",      _EMAIL_SHAPE_MARKERS,             _PRIMARY_EMAIL_VERBS),
    ("delegated",  ("nudge", "mark received"),       ("nudge",)),
    # UXR1 D2 — the hygiene/commitment_review row ("Did you already handle
    # this? — close it?"). Marker: `hold` (only _CRU_ACTIONS emits it), OR the
    # confirm+not-relevant PAIR (the same family's commitments-chat Phase 3.6
    # cluster, which carries `add to my plate` instead of `hold`) — a tuple
    # marker means ALL its ids must be present. Checked BEFORE the bare
    # `confirm` class so a hygiene row never falls through to it.
    ("hygiene",    ("hold", ("confirm", "not relevant")), ("confirm",)),
    ("commitment", _PRIMARY_COMMITMENT_VERBS,        _PRIMARY_COMMITMENT_VERBS),
    ("identity",   ("mine",),                        ("mine",)),
    ("confirm",    ("confirm", "confirm proposal"),  ("confirm", "confirm proposal")),
)

# UXR1 D2 (M ruling 2026-07-21, UX review finding 9) — per-CLASS display-label
# overrides, the ONE ruled exception to F-59's one-label-per-verb contract.
# The hygiene row's question is "close it?", so its affirmative must answer
# the question ("Close it", not the opaque "Confirm") and its negative must
# say what actually happens ("No — still open": the suggestion mutes 60d and
# the commitment STAYS OPEN — the old "Not relevant (60 days)" read as
# "make this go away" while leaving it open). Wire ids untouched — this is
# display text only; dispatch and every OTHER surface's labels (the global
# "Not relevant (60 days)") are unchanged. Add entries here ONLY with an
# M ruling — a per-class relabel anywhere else recreates the F-59 disease.
CLASS_DISPLAY_LABELS = {
    "hygiene": {"confirm": "Close it", "not relevant": "No — still open"},
}


def _class_markers_match(low: set, markers) -> bool:
    """A marker entry that is itself a tuple requires ALL its ids present
    (the UXR1 D2 pair marker); a string entry matches on presence."""
    for m in markers:
        if isinstance(m, tuple):
            if set(m) <= low:
                return True
        elif m in low:
            return True
    return False


def _grammar_class_of(actions: list) -> Optional[str]:
    """The PRIMARY_VERB_GRAMMAR class that owns this row (first match wins,
    same order as _split_primary_verbs), or None."""
    low = {str(a).lower() for a in actions}
    for cls, markers, _primaries in PRIMARY_VERB_GRAMMAR:
        if _class_markers_match(low, markers):
            return cls
    return None


def _class_label_overrides(actions: list) -> dict:
    """The row's CLASS_DISPLAY_LABELS map ({} for every non-overridden
    class) — computed once per row in _render_widget_item and threaded to
    the button/option renderers."""
    return CLASS_DISPLAY_LABELS.get(_grammar_class_of(actions) or "", {})


def _split_primary_verbs(actions: list) -> tuple[list, list]:
    """Split a row's action list into (primary, tail) per PRIMARY_VERB_GRAMMAR.
    The first class whose marker set matches the row's verbs owns the row;
    its primary verbs — those actually present, emitted in the table's declared
    order so the most-likely action leads — become the promoted set, and the
    rest is the tail. Rows matching no class → ([], all_actions). Wire ids
    untouched; this is display layout only (the ≤4 rule in _render_widget_item
    decides whether the tail renders as secondary buttons or a dropdown)."""
    low = {str(a).lower() for a in actions}
    primary_ids: tuple = ()
    for _cls, markers, primaries in PRIMARY_VERB_GRAMMAR:
        if _class_markers_match(low, markers):
            primary_ids = primaries
            break
    if not primary_ids:
        return [], list(actions)
    pset = set(primary_ids)
    # Table order, not row order, so Send leads Draft leads Snooze regardless of
    # how the builder ordered the verb set.
    primary = [a for pid in primary_ids for a in actions if str(a).lower() == pid]
    tail = [a for a in actions if str(a).lower() not in pset]
    return primary, tail


def _render_verb_button(action: str, item_n, *, primary: bool = True,
                        label: Optional[str] = None) -> str:
    """One-tap verb button (WG1-A D-A3, generalizing t3 FB-4's primary button).
    `primary=True` → the gold-accented cr-action-primary style (the row's
    most-likely move); `primary=False` → a secondary cr-action-secondary button
    (the ≤4 rule renders a small row's WHOLE verb set as buttons — no dropdown).
    Same selection model as the dropdown (tap arms the row, Apply batches) and
    the same wire attributes the legacy cr-action contract carries.

    `label` (UXR1 D2) — an explicit per-class display label from
    CLASS_DISPLAY_LABELS; None → the taxonomy label. The wire attributes
    (data-action) always carry the frozen action id, whatever the label.

    Always input-type "none": Send/Done/Snooze/Nudge/Mine need nothing typed,
    and Draft's edit surface is the FB-10 inline-editable body. An input-bearing
    verb rendered as a button (D-A3 — e.g. `theirs to [name]` on a ≤4 confirm
    row) dispatches its BARE action id and apply-choices asks the follow-up for
    the missing input; it never carries an inline wrapper here."""
    safe_action = _html_mod.escape(action, quote=True)
    safe_n = _html_mod.escape(str(item_n), quote=True)
    label = _html_mod.escape(label if label is not None
                             else _action_display_label(action))
    cls = "cr-action cr-action-primary" if primary else "cr-action cr-action-secondary"
    return (
        f'<button class="{cls}" type="button" '
        f'data-n="{safe_n}" data-action="{safe_action}" '
        f'data-input-type="none">{label}</button>'
    )


def _render_primary_button(action: str, item_n) -> str:
    """Back-compat alias — the row's promoted primary button (WG1-A: use
    _render_verb_button directly for secondary buttons)."""
    return _render_verb_button(action, item_n, primary=True)


def _strip_action_n_prefix(action: str, item_n) -> str:
    """Strip a leading item-id prefix from an action label so the button shows just the verb.

    Strips:
      1. Exact `<item_n> ` prefix (e.g. `1 send` for item 1, `7a confirm` for sub-item 7a)
      2. Defensive: any single ASCII-letter + space prefix (e.g. `A confirm`, `b edit`) —
         catches the case where an orchestrator passes per-sub-item action arrays with
         orphan letter prefixes that don't match the parent's `n`. Per M's Apr 30 v2.12.4
         feedback: REVIEW section buttons rendered as `A confirm` / `B confirm` because
         the data shape used letter sub-IDs but the parent item_n was a digit.

    Defensive strip is case-insensitive but only fires when the leading token is exactly
    ONE letter followed by whitespace. That's safe vs the canonical action set (no
    canonical action label starts with a single letter followed by a space).
    """
    if not action:
        return action
    n_str = str(item_n)
    if n_str and action.startswith(n_str + " "):
        return action[len(n_str) + 1:]
    # Defensive: orphan single-letter sub-id prefix
    m = _re_mod.match(r"^[A-Za-z]\s+", action)
    if m:
        return action[m.end():]
    return action


def _render_widget_item(item: dict) -> str:
    """Render one item as an HTML block with a per-row verb dropdown (T2.2)."""
    n = item.get("n")
    if n is None:
        raise ValueError("Item missing required 'n' field")

    parts = [f'<div class="cr-item" data-item-n="{_html_mod.escape(str(n), quote=True)}">']

    # T2.2 display hygiene (RV-5): `n` is the WIRE id (data-n, the apply
    # payload); `display_n` is the visible row number. When a surface keys
    # rows on wire ids (cr-brain proposal ids, commitment ids), it passes a
    # sequential `display_n` so plumbing never renders as row-title text.
    disp = item.get("display_n", n)

    # Header line
    head_parts = [f'<span class="cr-item-num"><strong>{_html_mod.escape(str(disp))}.</strong></span>']
    icon = item.get("icon", "")
    if icon:
        head_parts.append(f'<span class="cr-item-icon">{_html_mod.escape(icon)}</span>')
    name = item.get("name", "")
    if name:
        head_parts.append(f'<span class="cr-item-name"><strong>{_md_to_html(name)}</strong></span>')
    subject = item.get("subject", "")
    if subject:
        # The "· " separator sits BETWEEN a name and the subject. On a
        # name=None row (counterparty-unresolved orphan promises, my-plate
        # CTS1 §8.2) there is no name to separate from — leading with a bare
        # "· " is the render-side twin of the "?"-lead bug. Only emit it when
        # a name actually precedes the subject.
        _sep = '· ' if name else ''
        head_parts.append(f'<span class="cr-item-subject">{_sep}"{_md_to_html(subject)}"</span>')
    context_tag = item.get("context_tag", "")
    if context_tag:
        head_parts.append(f'<span class="cr-item-context">— {_md_to_html(context_tag)}</span>')
    for ann in item.get("annotations", []):
        head_parts.append(f'<span class="cr-item-annotation">{_md_to_html(ann)}</span>')
    parts.append(f'<div class="cr-item-head">{" ".join(head_parts)}</div>')

    # Original-thread collapsible block (v2.12.1+) — shown ABOVE the email draft
    # so user can expand the source thread for context before reviewing the reply.
    # Per Sam's Apr 30 ask: "this needs to be a response to an email."
    # Data shape: item["original_thread"] = {"author": str, "date": str, "subject": str, "body": str}
    # All fields optional. If "original_thread" key absent, no <details> renders.
    orig = item.get("original_thread")
    if orig and isinstance(orig, dict):
        parts.append(_render_original_thread_html(orig))

    # Email draft block (metadata + body) — render as blockquote
    metadata = item.get("metadata", [])
    body_lines = item.get("body_lines", [])
    if metadata or body_lines:
        parts.append(_render_email_blockquote_html(metadata, body_lines))

    # Sources line
    sources = item.get("sources", [])
    if sources:
        sources_html = ", ".join(
            f'<a href="{_html_mod.escape(s.get("url", ""), quote=True)}">{_md_to_html(s.get("label", ""))}</a>'
            for s in sources
        )
        parts.append(f'<div class="cr-item-sources">Sources: {sources_html}</div>')

    # v2.14.14+ — inline artifact links REMOVED from widget body. Per M's testing:
    # documents linked from inside the widget iframe don't open correctly across
    # tasks (the iframe sandbox blocks `computer://` hrefs in some Cowork builds,
    # and even when they work the user-facing experience was inconsistent). The
    # post-widget Briefs / Sources markdown sections are now the ONLY correct
    # place for clickable artifact links — those render in regular chat where
    # links work reliably.
    #
    # The `artifact_link` field stays in the data shape so orchestrators can
    # still collect paths for the post-widget Briefs section + the
    # `mcp__cowork__present_files` fallback. The renderer just doesn't paint
    # them inside the widget body anymore.

    # Per-item verb DROPDOWN (T2.2 row diet — replaces the per-row button
    # group; wire format unchanged). Default option is "— leave —" (no
    # selection); the select is the row's single verb control, input wrappers
    # stack below exactly as in the button era.
    safe_n = _html_mod.escape(str(n), quote=True)
    safe_disp = _html_mod.escape(str(disp), quote=True)
    # t3 FB-3: merge the Defer/Snooze pair into the one 'Later…' option.
    actions = _merge_later_verbs(item.get("actions", []))
    if actions:
        # WG1-A D-A3 — primary verbs promote to visible buttons; the ≤4 rule
        # then decides whether the rest are secondary buttons or a dropdown.
        stripped_actions = [_strip_action_n_prefix(a, n) for a in actions]
        primary, tail = _split_primary_verbs(stripped_actions)
        # UXR1 D2 — the row's per-class label overrides ({} everywhere but
        # the ruled classes). Wire ids on buttons/options stay frozen.
        overrides = _class_label_overrides(stripped_actions)
        inputs_html = []
        if len(stripped_actions) <= 4:
            # The ≤4 rule (M ruling row 13): a small row renders EVERY verb as
            # a visible button and emits NO <select>. Primaries lead (gold);
            # the rest are secondary buttons. Input-bearing verbs dispatch
            # their bare id and apply-choices asks the follow-up (D-A3) — so
            # no inline input wrapper is emitted on this path.
            controls_html = [_render_verb_button(
                a, n, primary=True, label=overrides.get(str(a).lower()))
                for a in primary]
            controls_html += [_render_verb_button(
                a, n, primary=False, label=overrides.get(str(a).lower()))
                for a in stripped_actions if a not in primary]
        else:
            # ≥5 options: primaries promote to buttons, the tail stays in the
            # dropdown (which keeps inline inputs for input-bearing tail verbs).
            controls_html = [_render_verb_button(
                a, n, primary=True, label=overrides.get(str(a).lower()))
                for a in primary]
            options_html = ['<option value="">— more —</option>' if primary
                            else '<option value="">— leave —</option>']
            for a in tail:
                opt, inp = _render_action_option(
                    a, n, item, label=overrides.get(str(a).lower()))
                options_html.append(opt)
                if inp:
                    inputs_html.append(inp)
            controls_html.append(
                f'<select class="cr-action-select" data-n="{safe_n}" '
                f'data-disp="{safe_disp}">{"".join(options_html)}</select>'
            )
        parts.append(
            f'<div class="cr-item-actions">{"".join(controls_html)}</div>'
        )
        # v4.5.2 S2 (F-59) — when a row offers fewer verbs than its siblings
        # (needs-confirm items), it says WHY in one line. The orchestrator
        # passes the reason; unexplained reduced verb sets read as broken
        # buttons.
        reduced_reason = item.get("reduced_verbs_reason", "")
        if reduced_reason:
            parts.append(f'<div class="cr-verbset-note">{_md_to_html(reduced_reason)}</div>')
        if inputs_html:
            parts.append(f'<div class="cr-item-inputs">{"".join(inputs_html)}</div>')

    # v2.14.36+ — collapsible "+ Add context" toggle replaces the v2.14.28-v2.14.35
    # always-visible textarea. M's 2026-05-07 reversal: "Lets keep a context button
    # for all and delete the static open box. the static open box is bad for UI,
    # makes it look too cluttered." The semantic stays the same — per-item context,
    # captured as `context` in the apply-choices payload alongside `n` and `action`,
    # empty notes drop from payload — but the affordance is now click-to-reveal so
    # widgets with many items (commitments at 16+ open) don't render 16 textareas
    # that the user has to scroll past. Click "+ Add context" → textarea expands
    # below the button → user types → fires with whatever action is selected on
    # Apply. crApplyAll's existing `.cr-note-field` lookup still grabs the value.
    safe_n = _html_mod.escape(str(n), quote=True)
    parts.append(
        f'<div class="cr-item-note">'
        f'<button class="cr-note-toggle" type="button" '
        f'data-note-for-n="{safe_n}">+ Add context</button>'
        f'<input class="cr-note-field" type="text" '
        f'placeholder="Add context (optional)" '
        f'data-note-for-n="{safe_n}" '
        f'style="display:none;" />'
        f"</div>"
    )

    # Sub-items (grouped commitments / pending reviews)
    sub_items = item.get("sub_items", [])
    if sub_items:
        parts.append('<div class="cr-sub-items">')
        for sub in sub_items:
            sub_id = sub.get("id", "")
            sub_summary = sub.get("summary", "")  # rendered as visible label v2.12.4+ (per M's Apr 30 6a-e visual-connection ask)
            # t3 FB-3 + FB-4 + WG1-A D-A3 — same merge + primary split + ≤4
            # all-buttons rule as parent rows.
            sub_actions = _merge_later_verbs(sub.get("actions", []))
            sub_stripped = [_strip_action_n_prefix(a, sub_id) for a in sub_actions]
            sub_primary, sub_tail = _split_primary_verbs(sub_stripped)
            # UXR1 D2 — per-class label overrides, same as parent rows.
            sub_overrides = _class_label_overrides(sub_stripped)
            sub_all_buttons = len(sub_stripped) <= 4  # WG1-A D-A3 ≤4 rule
            sub_options_html = ['<option value="">— more —</option>' if sub_primary
                                else '<option value="">— leave —</option>']
            sub_inputs_html = []
            if not sub_all_buttons:
                for a in sub_tail:
                    opt, inp = _render_action_option(
                        a, sub_id, item,
                        label=sub_overrides.get(str(a).lower()))
                    sub_options_html.append(opt)
                    if inp:
                        sub_inputs_html.append(inp)
            sub_summary_html = (
                f'<div class="cr-sub-summary">{_md_to_html(sub_summary)}</div>'
                if sub_summary
                else ""
            )
            sub_inputs_block = (
                f'<div class="cr-sub-inputs">{"".join(sub_inputs_html)}</div>'
                if sub_inputs_html
                else ""
            )
            # Show the visible sub-id label only when it carries useful context
            # for the user — i.e., the parent-letter format like `7a`, `7b`
            # (groups items under parent #7). Suppress single-letter+digit
            # namespaces like `d1`, `e1`, `r3` — those are routing prefixes
            # for the orchestrator (dormant / entity / review) and read as
            # cryptic codes to non-technical users. Sam Apr 29: "I don't
            # know what name these mean." The summary text below the row
            # carries the meaning the user actually needs; the data-sub-id
            # attribute on the wrapper still routes the action correctly.
            # T3.1 (FB-13): a wire-id-shaped sub_id (commitment/proposal ids —
            # `cmt_*`, `event_NNN`, `cru:*`...) is plumbing, never a label;
            # data-sub-id still routes the action.
            sid_str = str(sub_id)
            show_sid_label = not (
                _re_mod.match(r"^[a-z]\d+$", sid_str)
                or _WIRE_ID_RE.match(sid_str.strip().rstrip("."))
            )
            sub_id_html = (
                f'<span class="cr-sub-id"><strong>{_html_mod.escape(sid_str)}</strong></span>'
                if show_sid_label
                else ""
            )
            # v2.14.36+ — collapsible "+ Add context" toggle for sub-items, mirroring
            # the parent-item pattern. Pre-v2.14.36 sub-items had an always-visible
            # textarea that visually doubled the footprint of every grouped item
            # (commitments grouped chase emails with 5 sub-items rendered 5 stacked
            # textareas the user never used). Click-to-reveal keeps the affordance
            # available without the visual clutter.
            sub_safe_n = _html_mod.escape(sid_str, quote=True)
            # T3.1 review F-1: data-disp feeds the JS hold message ("Apply is
            # waiting on item N — …") — visible text composed client-side,
            # which the render-time scan can never see (data-* values are
            # blanked from the scannable). A suppressed sub_id (wire-id or
            # routing-prefix shaped) must not be the display handle; fall
            # back to the parent row's display number. data-n stays the
            # wire id — dispatch untouched.
            sub_disp = sub_safe_n if show_sid_label else safe_disp
            sub_note_html = (
                f'<div class="cr-item-note cr-sub-item-note">'
                f'<button class="cr-note-toggle" type="button" '
                f'data-note-for-n="{sub_safe_n}">+ Add context</button>'
                f'<input class="cr-note-field" type="text" '
                f'placeholder="Add context (optional)" '
                f'data-note-for-n="{sub_safe_n}" '
                f'style="display:none;" />'
                f"</div>"
            )
            # v4.5.2 S2 (F-59) — sub-items with a reduced verb set say why,
            # same contract as parent items.
            sub_reduced = sub.get("reduced_verbs_reason", "")
            sub_reduced_html = (
                f'<div class="cr-verbset-note">{_md_to_html(sub_reduced)}</div>'
                if sub_reduced
                else ""
            )
            parts.append(
                f'<div class="cr-sub-item" data-sub-id="{_html_mod.escape(sid_str, quote=True)}">'
                f'<div class="cr-sub-row">'
                f'{sub_id_html}'
                f'{sub_summary_html}'
                f'<div class="cr-sub-actions">'
                + (
                    # WG1-A D-A3 ≤4 rule — every verb a button, no <select>.
                    "".join(_render_verb_button(
                        a, sub_id, primary=True,
                        label=sub_overrides.get(str(a).lower()))
                            for a in sub_primary)
                    + "".join(_render_verb_button(
                        a, sub_id, primary=False,
                        label=sub_overrides.get(str(a).lower()))
                              for a in sub_stripped if a not in sub_primary)
                    if sub_all_buttons else
                    # ≥5 options — primaries as buttons, tail in the dropdown.
                    "".join(_render_verb_button(
                        a, sub_id, primary=True,
                        label=sub_overrides.get(str(a).lower()))
                            for a in sub_primary)
                    + (
                        f'<select class="cr-action-select" data-n="{sub_safe_n}" '
                        f'data-disp="{sub_disp}">{"".join(sub_options_html)}</select>'
                        if sub_tail else ""
                    )
                )
                + f"</div>"
                f"</div>"
                f"{sub_reduced_html}"
                f"{sub_inputs_block}"
                f"{sub_note_html}"
                f"</div>"
            )
        parts.append("</div>")

    parts.append("</div>")  # close cr-item
    return "".join(parts)


# ============================================================================
# WG1-A D-A6 — row-quarantine-not-page-block (M ruling 2026-07-20, big-test
# Findings Ledger row 10b). A defective row degrades to an honest placeholder
# and the page still renders every healthy row; the page-level scans in
# render_chat_output_widget REMAIN as the final backstop over the assembled
# chrome. Never-silent doctrine preserved: the placeholder IS the user-visible
# receipt, and quarantine is logged to stderr. No new keys enter the
# render_and_persist return dict (transport contract frozen).
# ============================================================================

# Fixed defect-class copy. The placeholder's context line is drawn ONLY from
# these strings (never the offending content) so it trivially passes the same
# scan the original row failed (anti-self-leak, spec §7). Scope: the always-on
# render-path scans (id-leak + non-workspace path). Personal-content leaks stay
# the transport-layer backstop (surface-gated) exactly as today — the render
# path never ran that scan, so quarantine does not change its block semantics.
_QUARANTINE_DEFECT_CLASSES = {
    "id_leak": "withheld — failed the leak scan (an internal id or token)",
    "path_leak": "withheld — failed the path scan (a non-workspace path)",
    "render_error": "withheld — could not be rendered",
}


def _scan_row_fragment(fragment: str):
    """Per-row content scan (WG1-A D-A6). Returns a defect-class key from
    _QUARANTINE_DEFECT_CLASSES when the row's rendered fragment carries a leak,
    else None. Uses the SAME scannable prep as the page-level gate — blank
    data-* attributes + href values so wire ids parked there don't
    false-positive; only visible text is scanned — and the SAME always-on
    scanners (id-leak + non-workspace path)."""
    if not fragment:
        return None
    scannable = _re_mod.sub(r'href="[^"]*"', 'href=""', fragment)
    scannable = _re_mod.sub(r'(\bdata-[a-z-]+=")[^"]*(")', r"\1\2", scannable)
    if scan_for_id_leaks(scannable):
        return "id_leak"
    try:
        if _scan_for_path_leaks(scannable):
            return "path_leak"
    except Exception:
        # The path scan needs a resolvable workspace/plugin prefix; when absent
        # (local pytest) it no-ops. Never let it break a render.
        pass
    return None


def _quarantine_placeholder_item(item: dict, defect_class: str, ordinal) -> dict:
    """Build the honest placeholder that replaces a defective row (D-A6). Real
    `n` (so `show why` dispatches against the same row id), the fixed
    "1 row withheld" name, a context line naming the defect CLASS + the row's
    ordinal (never the offending content), and the single `show why` action."""
    reason = _QUARANTINE_DEFECT_CLASSES.get(
        defect_class, _QUARANTINE_DEFECT_CLASSES["render_error"])
    n = item.get("n")
    return {
        "n": n if n is not None else f"withheld-{ordinal}",
        "display_n": ordinal,
        "icon": "⚠",  # ⚠
        "name": "1 row withheld",
        "context_tag": f"row {ordinal} {reason}",
        "actions": ["show why"],
    }


def _render_widget_item_quarantined(item: dict, *, ordinal) -> str:
    """Render one row, scanning its fragment; a defective row is REPLACED by an
    honest placeholder (WG1-A D-A6). The page renders with every healthy row —
    one bad row degrades to a visible placeholder naming the defect class
    instead of blocking the whole page. Logged to stderr; the placeholder is
    the user-visible receipt."""
    import sys as _sys
    try:
        fragment = _render_widget_item(item)
    except Exception as exc:
        _sys.stderr.write(
            f"[chat_output_renderer] WG1-A quarantine: row {ordinal} raised "
            f"during render ({type(exc).__name__}) — placeholder emitted.\n")
        return _render_widget_item(
            _quarantine_placeholder_item(item, "render_error", ordinal))
    defect = _scan_row_fragment(fragment)
    if defect is None:
        return fragment
    _sys.stderr.write(
        f"[chat_output_renderer] WG1-A quarantine: row {ordinal} withheld "
        f"({defect}) — placeholder emitted.\n")
    placeholder = _render_widget_item(
        _quarantine_placeholder_item(item, defect, ordinal))
    # Anti-self-leak backstop: the placeholder is built from fixed strings, so
    # it must scan clean. If it somehow doesn't, fall back to a minimal row that
    # cannot carry the defect.
    if _scan_row_fragment(placeholder) is not None:
        placeholder = _render_widget_item({
            "n": item.get("n") if item.get("n") is not None else f"withheld-{ordinal}",
            "display_n": ordinal, "name": "1 row withheld",
            "context_tag": "withheld — failed a content scan",
            "actions": ["show why"]})
    return placeholder


def _render_widget_section(section: dict) -> str:
    """Render one section: optional title + items separated by dividers. Each
    row passes through the WG1-A D-A6 quarantine gate — a defective row becomes
    an honest placeholder rather than taking the whole page down."""
    parts = []
    title = section.get("title")
    count = section.get("count")
    if title:
        title_text = title.upper()
        if count is not None:
            title_text = f"{title_text} ({count})"
        parts.append(f'<h3 class="cr-section-title">{_html_mod.escape(title_text)}</h3>')

    items = section.get("items", [])
    for i, item in enumerate(items):
        if i > 0:
            parts.append('<hr class="cr-divider">')
        ordinal = item.get("display_n", i + 1)
        parts.append(_render_widget_item_quarantined(item, ordinal=ordinal))
    return "".join(parts)


# Chalette Command Room stacked logo, dark-mode adapted (v2.12.2+).
# Mirrors Command Room/ref/brand/Chalette_CommandRoom_Logo_Stacked_2026-04-21.svg
# with the dark elements flipped to light/gold for visibility on the widget's
# warm-charcoal background. Scaled to fit the brand strip via CSS height: 44px.
_BRAND_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 130" '
    'class="cr-brand-logo" preserveAspectRatio="xMidYMid meet">'
    '<g transform="translate(49 58)">'
    '<text x="0" y="0" font-family="\'Cormorant Garamond\', Georgia, serif" '
    'font-weight="500" font-size="60" fill="#B88B4A" letter-spacing="-0.5">C</text>'
    '<text x="40" y="0" font-family="\'Cormorant Garamond\', Georgia, serif" '
    'font-weight="500" font-size="54" fill="#E8E0D6" letter-spacing="-0.5">halette</text>'
    '</g>'
    '<line x1="78" y1="80" x2="202" y2="80" stroke="#5E4F3F" stroke-width="1" opacity="0.7"></line>'
    '<text x="140" y="104" font-family="\'JetBrains Mono\', ui-monospace, monospace" '
    'font-weight="500" font-size="11" text-anchor="middle" fill="#B88B4A" letter-spacing="4">COMMAND ROOM</text>'
    '</svg>'
)


# ============================================================================
# Widget CSS (T2.2 scaffold diet round 2 — conditional emission).
#
# The sheet is split into a CORE block (always emitted) plus FEATURE blocks,
# each keyed by a trigger substring: a block ships ONLY when the rendered
# content actually contains its trigger (an email blockquote, a sub-item
# group, the multi-field editor, ...). `_compose_widget_css(content_html)`
# assembles the sheet per render. Classes the JS ADDS at runtime
# (.cr-select-armed, .cr-selected, .cr-item-invalid, .cr-input-missing,
# .cr-action-input-just-opened, .cr-wrapper-missing) ride with the block
# whose STATIC trigger guarantees their host elements exist — they are never
# their own triggers (they aren't in the initial HTML).
#
# Brand colors derived from Chalette_CommandRoom_Logo_Stacked SVG:
#   #B88B4A  gold/bronze (the "C" in Chalette, accent)
#   #1A1714  warm charcoal (deep brown-black, primary dark)
# ============================================================================

_CSS_CORE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { background: transparent; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 12px; color: #E8E0D6 !important; font-size: 13px; line-height: 1.5; }
.cr-card { max-width: 820px; border: 1px solid #3A3530 !important; border-radius: 8px; background: #1A1714 !important; overflow: hidden; color: #E8E0D6; box-shadow: 0 1px 3px rgba(0,0,0,0.4); }
.cr-brand-strip { padding: 14px 16px; background: #14110F !important; border-bottom: 1px solid #2A2520 !important; text-align: center; line-height: 0; }
.cr-brand-logo { height: 64px; width: auto; max-width: 280px; display: inline-block; vertical-align: middle; }
.cr-header { padding: 12px 16px; background: #221E1A !important; border-bottom: 1px solid #2A2520; font-weight: 600; color: #F5EFE6 !important; font-size: 14px; }
.cr-sub-header { padding: 6px 16px; background: #221E1A !important; border-bottom: 1px solid #2A2520; color: #B5A998 !important; font-size: 12px; }
.cr-body { padding: 16px; background: #1A1714; }
.cr-section-title { font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, monospace !important; font-size: 10px; font-weight: 700; letter-spacing: 2px; color: #B88B4A !important; margin: 16px 0 8px; padding-top: 8px; border-top: 1px solid #2A2520; }
.cr-section-title:first-child { margin-top: 0; padding-top: 0; border-top: none; }
.cr-item { padding: 12px 0; color: #E8E0D6; }
.cr-item-head { margin-bottom: 8px; color: #F5EFE6; }
.cr-item-num { color: #B88B4A; font-weight: 600; }
.cr-item-icon { margin: 0 4px; }
.cr-item-name { color: #F5EFE6; }
.cr-item-subject { color: #D6CDC0; }
.cr-item-context { color: #B5A998; }
.cr-item-annotation { color: #B88B4A; font-style: italic; margin-left: 6px; }
.cr-item-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; align-items: center; clear: both; }
.cr-wrapper-missing { display: inline-block; margin-left: 8px; padding: 2px 8px; font-size: 11px; color: #C44A3D; background: rgba(196, 74, 61, 0.12); border: 1px solid rgba(196, 74, 61, 0.4); border-radius: 4px; font-style: italic; }
.cr-verbset-note { margin-top: 4px; font-size: 12px; color: #8C7A65; font-style: italic; }
""".strip()

# Collapsible source-thread accordion (v2.12.1+).
_CSS_ORIG = """
.cr-orig-thread { margin: 6px 0 8px; padding: 0; background: #14110F !important; border: 1px solid #2A2520 !important; border-radius: 4px; color: #B5A998; }
.cr-orig-summary { padding: 6px 12px; cursor: pointer; font-size: 12px; color: #B5A998 !important; user-select: none; outline: none; }
.cr-orig-summary:hover { color: #E8E0D6 !important; background: #1A1714 !important; }
.cr-orig-body { padding: 8px 14px 12px; border-top: 1px solid #2A2520; font-size: 12px; color: #B5A998 !important; }
.cr-orig-openlink { margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px dashed #2A2520; }
.cr-orig-openlink a { color: #C9A570 !important; text-decoration: none; font-weight: 500; font-size: 12px; }
.cr-orig-openlink a:hover { color: #B88B4A !important; text-decoration: underline; }
.cr-orig-subject { margin-bottom: 6px; color: #D6CDC0; }
.cr-orig-line { margin: 2px 0; line-height: 1.45; }
.cr-orig-empty { height: 4px; }
""".strip()

# Email draft blockquote (CONVENTIONS_EMAIL_PREVIEW).
_CSS_EMAIL = """
.cr-email-draft { margin: 8px 0; padding: 10px 14px; background: #221E1A !important; border-left: 3px solid #B88B4A !important; color: #E8E0D6 !important; border-radius: 4px; }
.cr-eh { margin-bottom: 2px; color: #F5EFE6; }
.cr-eh strong { color: #F5EFE6; }
.cr-em { margin-bottom: 2px; color: #B5A998; font-size: 12px; }
.cr-eb-spacer { height: 8px; }
.cr-eb { margin: 2px 0; color: #E8E0D6; }
.cr-eb-empty { height: 6px; }
.cr-email-draft a { color: #C9A570 !important; text-decoration: none; }
.cr-email-draft a:hover { text-decoration: underline; }
/* t3 FB-10 — the body is directly editable: quiet at rest, an unmistakable
 * edit surface on hover/focus. */
.cr-eb-body[contenteditable] { cursor: text; border-radius: 4px; padding: 2px 4px; margin: 0 -4px; }
.cr-eb-body[contenteditable]:hover { background: rgba(184, 139, 74, 0.07); }
.cr-eb-body[contenteditable]:focus { outline: 1px solid #B88B4A; background: #221D17; box-shadow: 0 0 0 2px rgba(184, 139, 74, 0.18); }
.cr-eb-empty { min-height: 6px; height: auto; }
""".strip()

# Inline source links per item.
_CSS_SOURCES = """
.cr-item-sources { margin: 6px 0; font-size: 12px; color: #B5A998; }
.cr-item-sources a { color: #C9A570 !important; text-decoration: none; }
.cr-item-sources a:hover { text-decoration: underline; }
""".strip()

# Input wrappers + fields (v2.14.14+ explicit positioning + clear:both to
# prevent the overlay bugs from M's v2.14.13 testing; v2.14.30 flash-open
# animation cue; the JS adds .cr-action-input-just-opened at open time).
_CSS_INPUTS = """
.cr-item-inputs { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; clear: both; position: static; width: 100%; }
.cr-action-input { width: 100%; padding: 10px 12px; background: #221E1A; border: 1px solid #3A3530; border-radius: 6px; box-sizing: border-box; position: relative !important; clear: both !important; display: block; margin-top: 4px; z-index: 5; }
.cr-action-input[style*="display: block"], .cr-action-input[style*="display:block"] { display: block !important; }
/* v2.14.30+ — flash animation on open. Cues the customer that something appeared
 * even if the wrapper is partly clipped or scrolled out of view. Lasts 1.2s,
 * removed by the JS handler at 1500ms. Brand-gold border pulse matching the
 * selected-button state for visual continuity. */
.cr-action-input-just-opened { animation: cr-flash-open 1.2s ease-out; }
@keyframes cr-flash-open {
  0%   { border-color: #B88B4A; box-shadow: 0 0 0 4px rgba(184, 139, 74, 0.5), 0 4px 12px rgba(0, 0, 0, 0.6); transform: translateY(-2px); }
  30%  { border-color: #C9A570; box-shadow: 0 0 0 6px rgba(184, 139, 74, 0.3), 0 6px 16px rgba(0, 0, 0, 0.5); transform: translateY(0); }
  100% { border-color: #3A3530; box-shadow: none; transform: translateY(0); }
}
/* v2.14.30+ — defensive: parents must not clip an opened wrapper.
 * .cr-item-inputs is the immediate flex container; .cr-item is the per-item card.
 * Either could in theory have overflow:hidden inherited from a future style;
 * pin overflow:visible explicitly so a tall multi-field-email wrapper can
 * always render past its parent's content box if needed. */
.cr-item, .cr-item-inputs { overflow: visible !important; }
.cr-input-field { width: 100%; padding: 6px 8px; font-size: 13px; font-family: inherit; line-height: 1.5; background: #14110F !important; color: #E8E0D6 !important; border: 1px solid #3A3530 !important; border-radius: 4px !important; resize: vertical; box-sizing: border-box; }
.cr-input-field:focus { outline: none; border-color: #B88B4A !important; box-shadow: 0 0 0 2px rgba(184, 139, 74, 0.2); }
textarea.cr-input-field { min-height: 60px; }
.cr-item-invalid { border-left: 3px solid #C4703D !important; padding-left: 10px !important; background: rgba(196, 112, 61, 0.07); border-radius: 4px; }
.cr-input-reason { margin-top: 4px; font-size: 12px; color: #E09A5F; }
.cr-input-field.cr-input-missing { border-color: #C4703D !important; box-shadow: 0 0 0 1px rgba(196, 112, 61, 0.5) !important; }
""".strip()

# Multi-field email editor (v2.12.5+ inline To/Cc/Subject rows; body claims
# the vertical space per M's v2.12.4 feedback).
_CSS_MFE = """
.cr-multi-field { padding: 8px 10px; }
.cr-mfe-row { margin-bottom: 4px; }
.cr-mfe-row:last-child { margin-bottom: 0; }
.cr-mfe-label { display: block; font-size: 9px; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase; color: #B88B4A !important; margin-bottom: 3px; font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, monospace; }
/* v2.12.6+ — even tighter inline layout for To/Cc/Subject; body claims dominant vertical space */
.cr-mfe-inline { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
.cr-mfe-label-inline { display: inline-block; min-width: 44px; margin-bottom: 0 !important; flex-shrink: 0; font-size: 9px; }
.cr-mfe-field-compact { padding: 3px 6px !important; font-size: 12px !important; line-height: 1.3 !important; min-height: 22px; }
.cr-mfe-body { margin-top: 10px; }
.cr-mfe-body .cr-mfe-label { font-size: 10px; margin-bottom: 4px; }
.cr-mfe-body textarea.cr-input-field { min-height: 220px; font-size: 13px; line-height: 1.5; }
""".strip()

# Per-row verb DROPDOWN (T2.2 row diet — the six-button row's replacement).
# .cr-select-armed is the F-58 visible-armed-state: the JS adds it on any
# non-empty selection so an armed row is unmistakable before Apply.
_CSS_SELECTS = """
select.cr-action-select { padding: 5px 10px !important; font-size: 12px !important; font-weight: 500 !important; font-family: inherit !important; border: 1px solid #3A3530 !important; border-radius: 6px !important; background: #2A2520 !important; color: #E8E0D6 !important; cursor: pointer !important; max-width: 100%; }
select.cr-action-select:hover { border-color: #5E4F3F !important; }
select.cr-action-select:focus { outline: none; border-color: #B88B4A !important; box-shadow: 0 0 0 2px rgba(184, 139, 74, 0.2); }
select.cr-action-select.cr-select-armed { border-color: #B88B4A !important; background: rgba(184, 139, 74, 0.16) !important; color: #B88B4A !important; font-weight: 700 !important; box-shadow: 0 0 0 2px rgba(184, 139, 74, 0.35) !important; }
select.cr-action-select option { background: #1A1714; color: #E8E0D6; font-weight: 400; }
""".strip()

# Legacy per-item action BUTTONS — still used by the onboarding setup widget
# (selection labels, not verbs) and validated for hand-relayed legacy HTML.
_CSS_BUTTONS = """
button.cr-action { padding: 5px 12px !important; font-size: 12px !important; font-weight: 500 !important; font-family: inherit !important; border: 1px solid #3A3530 !important; border-radius: 6px !important; background: #2A2520 !important; color: #E8E0D6 !important; cursor: pointer !important; transition: all 0.1s; position: relative; }
button.cr-action:hover { background: #3A3530 !important; border-color: #5E4F3F !important; }
/* v2.14.1+ — much more visible selected state per Drew's Apr 30 testing
 * ("Skip button doesn't work" — visual feedback was too subtle). Now combines
 * brand-gold background + dark text + bold weight + a checkmark prefix that
 * appears via the cr-selected ::before pseudo-element. Even if the iframe
 * sandbox somehow ignores the background change, the checkmark survives. */
button.cr-action.cr-selected { background: #B88B4A !important; color: #14110F !important; border-color: #B88B4A !important; font-weight: 700 !important; box-shadow: 0 0 0 2px rgba(184, 139, 74, 0.35) !important; padding-left: 22px !important; }
/* v2.14.14+ — hex-escape the checkmark glyph (\2713) to survive iframe font fallback
 * issues; some Cowork builds rendered the literal "✓" as the Unicode-name string
 * "checkmark" overlapping the button label. Hex escape is the canonical CSS form
 * and renders consistently across font stacks. v2.14.35 fix: this string is
 * emitted from a Python triple-quoted block, where `\2713` was being parsed as
 * an OCTAL escape (`\271` = 0xB9 = `¹`, then literal `3`) — producing
 * `content: "¹3"` in the CSS, rendered as visible junk inside selected
 * buttons. Double-escape `\\2713` so Python emits the literal `\2713`. */
button.cr-action.cr-selected::before { content: "\\2713"; position: absolute; left: 8px; top: 50%; transform: translateY(-50%); font-weight: 700; color: #14110F; line-height: 1; pointer-events: none; }
button.cr-action.cr-selected:hover { background: #C9A570 !important; border-color: #C9A570 !important; }
/* t3 FB-4 — per-surface primary verbs as visible one-tap buttons (Done /
 * Send / Draft). Gold-accented at rest so the row's main move is obvious;
 * the selected state is the same .cr-selected treatment as every button. */
button.cr-action.cr-action-primary { border-color: #B88B4A !important; color: #B88B4A !important; font-weight: 600 !important; }
button.cr-action.cr-action-primary:hover { background: rgba(184, 139, 74, 0.16) !important; }
/* FB-CTS1: a SELECTED primary verb carries cr-action + cr-action-primary + cr-selected.
 * The gold `color:#B88B4A` on `.cr-action-primary` ties on specificity with the dark
 * `color:#14110F` on `.cr-selected` and, being later in source, WON — painting the
 * selected label gold-on-gold (its own gold `.cr-selected` background), i.e. invisible.
 * The 3-class rule below outranks both and restores the intended dark-on-gold selected
 * label (Done / Send / Draft / Snooze all read correctly once armed). */
button.cr-action.cr-action-primary.cr-selected { color: #14110F !important; }
/* WG1-A D-A3 — secondary verb buttons (the tail of a ≤4 row, rendered as
 * buttons instead of a dropdown) deliberately inherit the plain .cr-action
 * base style: the gold .cr-action-primary is the row's most-likely move and
 * reads first, the rest sit quieter at the base weight. No new palette. */
""".strip()

# Collapsible "+ Add context" note toggle + field (v2.14.36+ — M's 2026-05-07
# reversal: click-to-reveal, never a static open box).
_CSS_NOTES = """
.cr-item-note { margin-top: 8px; }
.cr-sub-item-note { margin-top: 6px; }
.cr-note-toggle { padding: 4px 10px !important; font-size: 11px !important; font-weight: 500 !important; font-family: inherit !important; border: 1px dashed rgba(184, 139, 74, 0.35) !important; border-radius: 4px !important; background: transparent !important; color: #8A7A60 !important; cursor: pointer !important; transition: all 0.1s; letter-spacing: 0.04em; }
.cr-note-toggle:hover { border-color: rgba(184, 139, 74, 0.6) !important; color: #B5A998 !important; }
.cr-note-toggle.cr-note-toggle-open { border-style: solid !important; border-color: #B88B4A !important; background: rgba(184, 139, 74, 0.12) !important; color: #B88B4A !important; }
.cr-note-field { width: 100%; margin-top: 6px; padding: 7px 10px !important; font-size: 12px !important; line-height: 1.4 !important; background: #1C1815 !important; color: #E8E0D6 !important; border: 1px solid #4A3F30 !important; border-radius: 4px !important; box-sizing: border-box; font-family: inherit; }
.cr-note-field::placeholder { color: #8A7A60; font-style: italic; }
.cr-note-field:hover { border-color: #6B5A40 !important; }
.cr-note-field:focus { outline: none; border-color: #B88B4A !important; color: #FAF6EE !important; box-shadow: 0 0 0 2px rgba(184, 139, 74, 0.18); background: #221D17 !important; }
""".strip()

# Grouped sub-items (commitments 7a/7b, pending reviews).
_CSS_SUBS = """
.cr-sub-items { margin-top: 8px; padding-left: 16px; border-left: 2px solid #2A2520; }
.cr-sub-item { margin: 8px 0; }
.cr-sub-row { display: flex; align-items: flex-start; gap: 10px; flex-wrap: wrap; }
.cr-sub-id { color: #B88B4A; font-size: 12px; min-width: 28px; font-weight: 600; padding-top: 4px; }
.cr-sub-summary { color: #D6CDC0; font-size: 12px; flex: 1 1 200px; padding-top: 4px; line-height: 1.5; }
.cr-sub-actions { display: flex; gap: 4px; flex-wrap: wrap; }
.cr-sub-inputs { margin-top: 6px; margin-left: 38px; display: flex; flex-direction: column; gap: 6px; }
""".strip()

_CSS_DIVIDER = """
.cr-divider { border: 0; border-top: 1px solid #2A2520; margin: 12px 0; }
""".strip()

_CSS_PAGINATION = """
.cr-pagination { padding: 8px 16px; background: #14110F !important; border-top: 1px solid #2A2520; color: #B5A998 !important; font-size: 12px; text-align: center; }
.cr-pagination code { color: #B88B4A !important; font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, monospace; font-size: 11px; }
""".strip()

_CSS_QUICKREAD = """
.cr-quick-read { padding: 12px 16px; background: #221E1A !important; border-top: 1px solid #2A2520; color: #E8E0D6 !important; font-size: 12px; border-left: 3px solid #B88B4A; }
.cr-quick-read strong { color: #B88B4A !important; font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, monospace !important; font-size: 10px; letter-spacing: 1.5px; text-transform: uppercase; }
""".strip()

# Footer (counter + Apply/Reset/Snooze-rest + the F-17 apply-hold reason).
_CSS_FOOTER = """
.cr-footer { padding: 12px 16px; background: #14110F !important; border-top: 1px solid #2A2520; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.cr-counter { color: #B5A998; font-size: 12px; }
.cr-counter strong { color: #F5EFE6; font-size: 13px; }
.cr-footer-actions { display: flex; gap: 6px; }
button.cr-btn-apply { padding: 6px 18px !important; font-size: 13px !important; font-weight: 600 !important; font-family: inherit !important; border: 1px solid #B88B4A !important; border-radius: 6px !important; background: #B88B4A !important; color: #14110F !important; cursor: pointer !important; }
button.cr-btn-apply:hover { background: #C9A570 !important; border-color: #C9A570 !important; }
button.cr-btn-apply:disabled { background: #3A3530 !important; border-color: #3A3530 !important; color: #5E4F3F !important; cursor: not-allowed !important; }
button.cr-btn-secondary { padding: 6px 12px !important; font-size: 12px !important; font-family: inherit !important; border: 1px solid #3A3530 !important; border-radius: 6px !important; background: #2A2520 !important; color: #E8E0D6 !important; cursor: pointer !important; }
button.cr-btn-secondary:hover { background: #3A3530 !important; border-color: #5E4F3F !important; }
.cr-apply-reason { flex-basis: 100%; font-size: 12px; color: #E09A5F; }
""".strip()

# Header stat tiles (v4.5.2 S2 — F-18 full-list layout; shared with the
# all-clear summary).
_CSS_TILES = """
.cr-counter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin: 12px 0; }
.cr-counter-card { background: #14110F; border: 1px solid #2A2520; border-radius: 6px; padding: 10px 12px; }
.cr-counter-label { font-size: 11px; color: #8C7A65; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
.cr-counter-value { font-size: 22px; color: #E8E0D6; font-weight: 500; }
""".strip()

# Feature blocks with their trigger substrings. A block is emitted iff its
# trigger appears in the rendered CONTENT (the html between <style> and
# <script>). Order preserves the original monolith's cascade.
_CSS_FEATURE_BLOCKS: list[tuple[str, str]] = [
    ("cr-orig-thread", _CSS_ORIG),
    ("cr-email-draft", _CSS_EMAIL),
    ("cr-item-sources", _CSS_SOURCES),
    ("cr-action-input", _CSS_INPUTS),
    ("cr-mfe-", _CSS_MFE),
    ("cr-action-select", _CSS_SELECTS),
    # Two triggers, one block (composition dedupes): the exact-token form for
    # legacy/onboarding buttons, the t3 FB-4 primary-button class whose
    # multi-class attribute the exact-token trigger can't see.
    ('class="cr-action"', _CSS_BUTTONS),
    ("cr-action-primary", _CSS_BUTTONS),
    ("cr-action-secondary", _CSS_BUTTONS),  # WG1-A D-A3 — a ≤4 row can be all
                                            # secondary buttons (no primary);
                                            # the pressed-state CSS must ship.
    ("cr-note-toggle", _CSS_NOTES),
    ("cr-sub-items", _CSS_SUBS),
    ("cr-divider", _CSS_DIVIDER),
    ("cr-pagination", _CSS_PAGINATION),
    ("cr-quick-read", _CSS_QUICKREAD),
    ('class="cr-footer"', _CSS_FOOTER),
    ("cr-counter-grid", _CSS_TILES),
]


# ============================================================================
# Widget JS (T2.2 scaffold diet round 2 — modular, conditionally emitted).
#
# The script is assembled per render from blocks keyed by content triggers
# (`_compose_widget_js`): the select handler ships only when verb dropdowns
# render, the button handler only for button widgets (onboarding), the note
# toggle only when "+ Add context" renders, Snooze-rest only when its footer
# button exists. Selection state lives in the DOM (armed selects / .cr-selected
# buttons) — no bookkeeping object. The `apply choices:` wire format is
# byte-compatible with the button era ({n, action, src?, input?, context?}).
#
# Notes carried over from the button-era template:
#   - dataset-iteration lookups, never CSS attribute selectors (action strings
#     contain spaces/brackets; some Cowork iframe builds fail the escaped
#     selector silently — Sam's Apr 29 wrapper-never-opened bug).
#   - bind immediately, never DOMContentLoaded (Cowork injects into an
#     already-loaded iframe — the v2.11.1→v2.11.2 bug).
#   - visible wrapper-missing caption (v2.14.34 — renderer-bypass class).
# ============================================================================

_JS_SELECT = """
function crSel(sel) {
  const n = sel.dataset.n;
  crHideWraps(n);
  // t3 FB-4 — a row has ONE armed verb: picking from the dropdown clears
  // the row's primary buttons.
  crQ('.cr-action').forEach(b => { if (b.dataset.n === n) b.classList.remove('cr-selected'); });
  if (sel.value) {
    sel.classList.add('cr-select-armed');
    const o = sel.options[sel.selectedIndex];
    crOpenWrap(n, sel.value, o ? o.dataset.inputType : '', sel);
  } else {
    sel.classList.remove('cr-select-armed');
  }
  crUpdateCounter();
  // WG1-A D-A5 — single-item ≥5 dropdown: picking an option dispatches.
  if (crSingleItem && sel.value) crSingleDispatch(sel);
}
""".strip()

_JS_BUTTONS = """
function crToggle(btn) {
  const n = btn.dataset.n;
  crHideWraps(n);
  const was = btn.classList.contains('cr-selected');
  crQ('.cr-action').forEach(b => { if (b.dataset.n === n) b.classList.remove('cr-selected'); });
  // t3 FB-4 — tapping a primary button clears the row's dropdown pick.
  crQ('.cr-action-select').forEach(s => {
    if (s.dataset.n === n) { s.value = ''; s.classList.remove('cr-select-armed'); }
  });
  if (!was) {
    btn.classList.add('cr-selected');
    crOpenWrap(n, btn.dataset.action, btn.dataset.inputType, btn);
  }
  crUpdateCounter();
  // WG1-A D-A5 — no batch footer on a single-item widget: arming IS the
  // dispatch (only when the button was just selected, never on toggle-off).
  if (crSingleItem && !was) crSingleDispatch(btn);
}
""".strip()

_JS_NOTES = """
function crToggleNote(btn) {
  const n = btn.dataset.noteForN;
  // FB-CTS1 (Bug C): resolve the field WITHIN the clicked row first, not by a
  // global first-match on data-note-for-n. If the same n ever surfaces on two
  // rows (confirmed real for id-less sub-items, which all emit n=""), a global
  // scan opens the wrong row's field and the clicked toggle reads as dead.
  // closest() pins us to this row; global stays as the fallback.
  const row = btn.closest('.cr-sub-item') || btn.closest('.cr-item');
  const fields = (row || document).querySelectorAll('.cr-note-field');
  for (let i = 0; i < fields.length; i++) {
    if (fields[i].dataset.noteForN === n) {
      const f = fields[i];
      if (f.style.display !== 'none') {
        f.style.display = 'none';
        btn.classList.remove('cr-note-toggle-open');
      } else {
        f.style.display = 'block';
        btn.classList.add('cr-note-toggle-open');
        try { f.focus(); } catch (e) {}
      }
      return;
    }
  }
}
""".strip()

_JS_SKIP = """
function crSkipAll() {
  const armed = new Set();
  crSelected().forEach(s => armed.add(String(s.n)));
  const optioned = new Set();
  crQ('.cr-action-select').forEach(s => {
    if (armed.has(String(s.dataset.n))) return;
    for (let i = 0; i < s.options.length; i++) {
      if (s.options[i].value === 'skip') {
        s.value = 'skip';
        s.classList.add('cr-select-armed');
        optioned.add(String(s.dataset.n));
        break;
      }
    }
  });
  crQ('.cr-item, .cr-sub-item').forEach(el => {
    const n = el.dataset.itemN || el.dataset.subId;
    if (!n || armed.has(String(n))) return;
    const b = el.querySelector('.cr-action[data-action="skip"]');
    if (b) { b.classList.add('cr-selected'); optioned.add(String(n)); }
  });
  // t3 FB-3 — merged rows carry no dropdown skip option ('Later...' covers
  // the visible surface); Snooze-rest still mutes them via direct payload
  // entries (the skip WIRE stays live).
  window.crExtraSkips = [];
  crQ('.cr-item, .cr-sub-item').forEach(el => {
    const n = el.dataset.itemN || el.dataset.subId;
    if (!n || armed.has(String(n)) || optioned.has(String(n))) return;
    const own = el.querySelector(':scope > .cr-item-actions, :scope > .cr-sub-row');
    if (own && (own.querySelector('.cr-action-select') || own.querySelector('.cr-action'))) {
      window.crExtraSkips.push(String(n));
    }
  });
  crUpdateCounter();
  crApplyAll();
}
""".strip()

_JS_CORE = """
const crTotalItems = __TOTAL_ITEMS__;
const crSrc = __CR_SRC__;
const crQ = s => document.querySelectorAll(s);
const crG = id => document.getElementById(id);
// WG1-A D-A5 — single-item widgets have no batch footer; a click dispatches
// directly. The marker is baked on the card by the renderer.
const crSingleItem = !!document.querySelector('.cr-card-single');

function crWrap(n, action) {
  // dataset iteration, never CSS attribute selectors (action strings carry
  // spaces/brackets; some Cowork iframe builds fail the escaped selector
  // silently -- the Apr 29 wrapper-never-opened bug).
  const ws = document.querySelectorAll('.cr-action-input');
  const s = String(n);
  for (let i = 0; i < ws.length; i++) {
    if (ws[i].dataset.inputForN === s && ws[i].dataset.inputForAction === action) return ws[i];
  }
  return null;
}

function crHideWraps(n) {
  const s = String(n);
  crQ('.cr-action-input').forEach(w => { if (w.dataset.inputForN === s) w.style.display = 'none'; });
}

function crOpenWrap(n, action, type, ctl) {
  if (!type || type === 'none') return;
  const w = crWrap(n, action);
  if (!w) {
    console.warn('cr-widget: no input wrapper for', { n: n, action: action });
    if (ctl && ctl.parentElement && !ctl.parentElement.querySelector('.cr-wrapper-missing')) {
      const warn = document.createElement('span');
      warn.className = 'cr-wrapper-missing';
      warn.textContent = '\u26a0 input field missing \u2014 re-fire task to fix';
      ctl.parentElement.appendChild(warn);
    }
    return;
  }
  w.style.display = 'block';
  w.classList.add('cr-action-input-just-opened');
  setTimeout(() => { try { w.classList.remove('cr-action-input-just-opened'); } catch (e) {} }, 1500);
  const f = w.querySelector('textarea, input');
  if (f) {
    if (w.dataset.inputType === 'textarea-prepop' && !f.value.trim() && ctl) {
      const item = ctl.closest('.cr-item');
      if (item) {
        const out = [];
        item.querySelectorAll('.cr-email-draft .cr-eb').forEach(l => out.push(l.textContent));
        f.value = out.join('\\n');
      }
    }
    setTimeout(() => {
      try {
        w.scrollIntoView({ behavior: 'smooth', block: 'center' });
        f.focus();
      } catch (e) { console.warn('cr-widget: scroll/focus failed', e); }
    }, 50);
  }
}

function crSelected() {
  const out = [];
  crQ('.cr-action-select').forEach(s => {
    if (!s.value) return;
    const o = s.options[s.selectedIndex];
    out.push({ n: s.dataset.n, action: s.value, el: s,
      disp: s.dataset.disp || s.dataset.n,
      req: !!(o && o.dataset.inputRequired === '1'),
      thing: (o && o.dataset.inputThing) || 'value',
      label: ((o && o.text) || s.value).trim() });
  });
  crQ('.cr-action.cr-selected').forEach(b => {
    out.push({ n: b.dataset.n, action: b.dataset.action, el: b,
      disp: b.dataset.n,
      req: b.dataset.inputRequired === '1',
      thing: b.dataset.inputThing || 'value',
      label: (b.textContent || b.dataset.action).trim() });
  });
  return out;
}

function crValidate() {
  crQ('.cr-item-invalid').forEach(el => el.classList.remove('cr-item-invalid'));
  crQ('.cr-input-reason').forEach(el => { el.style.display = 'none'; });
  crQ('.cr-input-field.cr-input-missing').forEach(el => el.classList.remove('cr-input-missing'));
  const invalid = [];
  crSelected().forEach(sel => {
    if (!sel.req) return;
    const w = crWrap(sel.n, sel.action);
    const f = w ? w.querySelector('textarea, input') : null;
    if (f && f.value && f.value.trim()) return;
    invalid.push(sel);
    const rowEl = sel.el.closest('.cr-sub-item') || sel.el.closest('.cr-item');
    if (rowEl) rowEl.classList.add('cr-item-invalid');
    if (w) {
      w.style.display = 'block';
      const reason = w.querySelector('.cr-input-reason');
      if (reason) reason.style.display = 'block';
      if (f) f.classList.add('cr-input-missing');
    }
  });
  return invalid;
}

function crUpdateCounter() {
  const picks = crSelected();
  const el = crG('cr-count');
  if (el) el.textContent = picks.length;
  const applyBtn = crG('cr-apply');
  if (!applyBtn) return;
  let orphan = false;
  crQ('.cr-note-field').forEach(f => { if (f.value && f.value.trim()) orphan = true; });
  const invalid = crValidate();
  const reasonEl = crG('cr-apply-reason');
  if (invalid.length > 0) {
    applyBtn.disabled = true;
    if (reasonEl) {
      const first = invalid[0];
      reasonEl.textContent = (invalid.length === 1)
        ? 'Apply is waiting on item ' + first.disp + ' \u2014 ' + first.label + ' needs a ' + first.thing + '.'
        : 'Apply is waiting on ' + invalid.length + ' items \u2014 fill the highlighted fields.';
      reasonEl.style.display = 'block';
    }
    return;
  }
  if (reasonEl) { reasonEl.textContent = ''; reasonEl.style.display = 'none'; }
  applyBtn.disabled = (picks.length === 0) && !orphan;
}

function crSendPrompt(text) {
  if (window.sendPrompt) window.sendPrompt(text);
  else if (window.parent && window.parent.sendPrompt) window.parent.sendPrompt(text);
  else console.warn('sendPrompt unavailable');
}

function crNoteFor(n) {
  const fields = crQ('.cr-note-field');
  const s = String(n);
  for (let i = 0; i < fields.length; i++) {
    if (fields[i].dataset.noteForN === s) return fields[i];
  }
  return null;
}

function crInlineBody(n) {
  // t3 FB-10 — the row's directly-editable email body, or null.
  const items = crQ('.cr-item, .cr-sub-item');
  const s = String(n);
  for (let i = 0; i < items.length; i++) {
    const rn = items[i].dataset.itemN || items[i].dataset.subId;
    if (String(rn) !== s) continue;
    return items[i].querySelector('.cr-eb-body[contenteditable]');
  }
  return null;
}

function crBodyText(el) {
  // Normalized on-screen text: trim line-trailing space, collapse blank-line
  // runs (an empty .cr-eb-empty line div reads back as TWO breaks via
  // innerText), drop trailing blanks — the shape data-original carries.
  const raw = (el.innerText || '').replace(/\\n{3,}/g, '\\n\\n');
  const lines = raw.split('\\n').map(l => l.replace(/\\s+$/, ''));
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  return lines.join('\\n');
}

function crRowArmed(row){
  // Any verb armed on this row — a dropdown value or a pressed primary button?
  const s=row.querySelectorAll('.cr-action-select');
  for(let i=0;i<s.length;i++)if(s[i].value)return true;
  return !!row.querySelector('.cr-action.cr-selected');
}

function crBodyEdited(el){
  // FB-CTS1 (Bug A): editing the email body must let the user Apply. The Apply
  // gate only counts armed verbs + note text, so a body edit alone left Apply
  // greyed and read as "editing the email breaks Apply." Auto-arm the row's
  // DRAFT verb (never Send — a mere edit must not queue a send) so the counter
  // ticks, the edited body serializes, and (post-CSS-fix) "Draft" shows armed.
  // NEVER on a single-item page: there arming IS the dispatch (D-A5), so the
  // auto-arm would fire `draft` on the first keystroke with zero clicks —
  // violating the draft-posture ruling. The auto-arm is also unnecessary
  // there: crApplyAll's FB-10 block serializes the current on-screen body at
  // explicit click time, so edit-then-click-Draft/Send still ships the edit.
  const row=el.closest('.cr-sub-item')||el.closest('.cr-item');
  if(row&&!crRowArmed(row)&&!crSingleItem){
    const d=row.querySelector('.cr-action[data-action="draft"]');
    if(d&&typeof crToggle==='function')crToggle(d);
  }
  crUpdateCounter();
}

function crSingleDispatch(ctl) {
  // WG1-A D-A5 — a single-item widget has no Apply footer, so a click
  // dispatches immediately. Build the SAME wire crApplyAll would (byte-equal
  // to the multi-select form). If the armed action needs an input that is
  // missing, open its wrapper + show the inline reason and dispatch when the
  // user supplies it (Enter / blur) — never fire an empty required action.
  const picks = crSelected();
  if (!picks.length) return;
  const sel = picks[0];
  if (sel.req) {
    const w = crWrap(sel.n, sel.action);
    const f = w ? w.querySelector('textarea, input') : null;
    if (!(f && f.value && f.value.trim())) {
      crValidate();  // opens the wrapper + surfaces the inline missing reason
      if (f && !f.dataset.crSingleBound) {
        f.dataset.crSingleBound = '1';
        const go = function () { if (f.value && f.value.trim()) crSingleDispatch(ctl); };
        f.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); }
        });
        f.addEventListener('blur', go);
      }
      return;
    }
  }
  crApplyAll();
}

function crApplyAll() {
  if (crValidate().length > 0) {
    crUpdateCounter();
    return;
  }
  const choices = [];
  const seen = new Set();
  crSelected().forEach(sel => {
    const choice = { n: sel.n, action: sel.action };
    if (crSrc) choice.src = crSrc;
    const w = crWrap(sel.n, sel.action);
    if (w) {
      if (w.dataset.inputType === 'multi-field-email') {
        const obj = {};
        w.querySelectorAll('.cr-mfe-field').forEach(f => {
          if (f.dataset.field) obj[f.dataset.field] = f.value || '';
        });
        if (Object.keys(obj).length) choice.input = obj;
      } else {
        const f = w.querySelector('textarea, input');
        if (f && f.value && f.value.trim()) choice.input = f.value;
      }
    }
    // t3 FB-10 — serialize the CURRENT on-screen body at Apply time. An
    // inline edit rides the choice as {body}; an open mfe editor wins
    // (it already carries body + To/Cc/Subject).
    if (!choice.input) {
      const eb = crInlineBody(sel.n);
      if (eb) {
        const cur = crBodyText(eb);
        const orig = (eb.dataset.original || '').trim();
        if (cur.trim() !== orig) choice.input = { body: cur };
      }
    }
    const note = crNoteFor(sel.n);
    if (note && note.value && note.value.trim()) choice.context = note.value.trim();
    choices.push(choice);
    seen.add(String(sel.n));
  });
  // t3 FB-3 — Snooze-rest entries for rows whose dropdown no longer offers
  // skip (the 'Later...' merge); staged by crSkipAll.
  if (window.crExtraSkips && window.crExtraSkips.length) {
    window.crExtraSkips.forEach(n => {
      if (seen.has(String(n))) return;
      const c = { n: n, action: 'skip' };
      if (crSrc) c.src = crSrc;
      choices.push(c);
      seen.add(String(n));
    });
    window.crExtraSkips = [];
  }
  // Orphan-note capture (v3.13.0+): typed context with no action selected
  // still applies. The carrier deliberately keeps the legacy wire id
  // `add to my list` (MLK1: retired as a BUTTON, kept as this carrier so
  // old and new widgets emit one shape) — apply-choices re-routes a
  // context-bearing tuple to a `note` on the resolved person/thread, or
  // declines honestly when nothing resolves. Never a list write.
  crQ('.cr-note-field').forEach(f => {
    const n = f.dataset.noteForN;
    if (!n || seen.has(String(n))) return;
    if (!f.value || !f.value.trim()) return;
    const c = { n: n, action: 'add to my list', context: f.value.trim() };
    if (crSrc) c.src = crSrc;
    choices.push(c);
    seen.add(String(n));
  });
  if (choices.length === 0) return;
  crSendPrompt('apply choices: ' + JSON.stringify(choices));
}

function crClear() {
  crQ('.cr-action-select').forEach(s => { s.value = ''; s.classList.remove('cr-select-armed'); });
  crQ('.cr-action.cr-selected').forEach(b => b.classList.remove('cr-selected'));
  crQ('.cr-action-input').forEach(w => { w.style.display = 'none'; });
  crQ('.cr-note-field').forEach(f => { f.value = ''; });
  crQ('.cr-note-toggle-open').forEach(t => t.classList.remove('cr-note-toggle-open'));
  // t3 FB-10 — Reset restores an inline-edited body to the queued text.
  crQ('.cr-eb-body[contenteditable]').forEach(eb => {
    const orig = eb.dataset.original;
    if (orig !== undefined && crBodyText(eb).trim() !== orig.trim()) eb.innerText = orig;
  });
  window.crExtraSkips = [];
  crUpdateCounter();
}

(function bindCrWidget() {
  try {
    const a = crG('cr-apply');
    if (a) a.addEventListener('click', crApplyAll);
    const c = crG('cr-clear');
    if (c) c.addEventListener('click', crClear);
    const sk = crG('cr-skip-all');
    if (sk && typeof crSkipAll === 'function') sk.addEventListener('click', crSkipAll);
    if (typeof crSel === 'function') crQ('.cr-action-select').forEach(s => {
      s.addEventListener('change', function () { crSel(s); });
    });
    // t3 FB-4 — primary verb buttons bind here; the onclick guard keeps the
    // onboarding widget's inline-onclick buttons from double-firing.
    if (typeof crToggle === 'function') crQ('.cr-action').forEach(b => {
      if (!b.getAttribute('onclick')) b.addEventListener('click', function () { crToggle(b); });
    });
    if (typeof crToggleNote === 'function') crQ('.cr-note-toggle').forEach(b => {
      b.addEventListener('click', function () { crToggleNote(b); });
    });
    crQ('.cr-note-field').forEach(f => f.addEventListener('input', crUpdateCounter));
    crQ('.cr-action-input').forEach(w => {
      w.querySelectorAll('textarea, input').forEach(f => f.addEventListener('input', crUpdateCounter));
    });
    // FB-CTS1 (Bug A) — editing the inline email body auto-arms Draft (crBodyEdited).
    crQ('.cr-eb-body[contenteditable]').forEach(el=>{el.addEventListener('input',()=>crBodyEdited(el));});
    crUpdateCounter();
  } catch (e) {
    console.error('cr-widget bind failed:', e);
  }
})();
""".strip()

# JS feature blocks with triggers, emitted in this order (function
# declarations hoist, and the bind IIFE lives at the end of _JS_CORE).
_JS_FEATURE_BLOCKS: list[tuple[str, str]] = [
    ("cr-action-select", _JS_SELECT),
    ('class="cr-action"', _JS_BUTTONS),
    ("cr-action-primary", _JS_BUTTONS),  # t3 FB-4 — see the CSS twin note
    ("cr-action-secondary", _JS_BUTTONS),  # WG1-A D-A3 — a ≤4 row can be all
                                           # secondary buttons (no primary); the
                                           # button handler must still ship.
    ("cr-note-toggle", _JS_NOTES),
    ('id="cr-skip-all"', _JS_SKIP),
]





def _count_total_selectable_items(sections: list[dict]) -> int:
    """Count items + sub-items that have at least one action button (selectable)."""
    total = 0
    for section in sections:
        for item in section.get("items", []):
            if item.get("actions"):
                total += 1
            for sub in item.get("sub_items", []):
                if sub.get("actions"):
                    total += 1
    return total


# ============================================================================
# Scaffold diet (T2, F2 rework) — render-time minification of the CSS + JS
# scaffold, computed ONCE at import.
#
# WHY: the delivery contract changed from "hand a file:// URI to show_widget"
# (impossible — show_widget has no file_uri param, Bug #67) to "paginate by
# design and relay each validated PAGE's bytes as show_widget's `widget_code`."
# For that relay to fit a single Cowork Read page (25K-token cap) the fixed
# scaffold had to shrink: it was 35KB (CSS 16KB + JS 19KB + brand SVG), which
# left almost no room per page. Minifying the two big constants at emit time
# (source stays fully commented for maintainers) cuts the scaffold ~43% with
# ZERO behavior change — every class, id, selector, and statement is preserved
# byte-for-byte; only comments and inter-token whitespace go.
#
# SAFETY: CSS minification is QUOTE-AWARE — it never touches text inside a
# quoted string, so attribute selectors that depend on a literal space
# (`.cr-action-input[style*="display: block"]`) are preserved exactly. JS
# minification is line-level (drop whole-line `//` comments, strip leading
# indentation, drop blank lines) PLUS conservative intra-line tightening
# (_tighten_js_line): spaces adjacent to punctuation are dropped, but any
# line containing '/' is exempt entirely (regex/comment/division safety),
# quoted strings are untouched (quote-aware, escape-aware), a space between
# two word characters always survives, and `+ +` never collapses to `++`.
# It never joins lines (ASI stays intact), so string/regex literals and the
# `__TOTAL_ITEMS__` / `__CR_SRC__` placeholders survive untouched.
# ============================================================================

def _minify_css(css: str) -> str:
    """Quote-aware CSS minifier. Strips /* */ comments and collapses
    inter-token whitespace WITHOUT ever editing the inside of a quoted string
    (so `[style*="display: block"]` selectors keep their literal space)."""
    # 1. Strip block comments (CSS has no line comments; strings can't contain
    #    an unescaped `*/`, and none in our sheet contain `/*`).
    out: list[str] = []
    i, n = 0, len(css)
    in_str = ""  # current quote char, or "" when outside a string
    while i < n:
        c = css[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:  # keep escaped char verbatim
                out.append(css[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = ""
            i += 1
            continue
        if c in "\"'":
            in_str = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and css[i + 1] == "*":
            j = css.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    stripped = "".join(out)
    # 2. Collapse whitespace runs to a single space OUTSIDE strings, then trim
    #    spaces around structural punctuation ({ } ; : ,). Quote-awareness is
    #    what makes `:` and `,` safe to trim (T2.2 diet round 2): the `:` in
    #    `[style*="display: block"]` lives INSIDE a string and is never
    #    touched; every unquoted `:`/`,` is declaration/selector punctuation
    #    where surrounding spaces are cosmetic. Descendant-combinator spaces
    #    (`.a .b`) survive because neither neighbour is punctuation. A `;`
    #    whose next token is `}` drops entirely (the final-declaration
    #    semicolon is redundant).
    result: list[str] = []
    i, n = 0, len(stripped)
    in_str = ""
    _PUNCT = "{};:,"
    while i < n:
        c = stripped[i]
        if in_str:
            result.append(c)
            if c == "\\" and i + 1 < n:
                result.append(stripped[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = ""
            i += 1
            continue
        if c in "\"'":
            in_str = c
            result.append(c)
            i += 1
            continue
        if c == ";":
            # peek past whitespace: a `;` immediately before `}` is redundant
            j = i + 1
            while j < n and stripped[j].isspace():
                j += 1
            if j < n and stripped[j] == "}":
                i += 1
                continue
            result.append(c)
            i += 1
            continue
        if c.isspace():
            j = i
            while j < n and stripped[j].isspace() and not (stripped[j] in "\"'"):
                j += 1
            # peek non-space neighbours to decide whether to drop the run
            prev = result[-1] if result else ""
            nxt = stripped[j] if j < n else ""
            if prev in _PUNCT or nxt in _PUNCT or prev == "" or nxt == "":
                pass  # drop the whitespace run entirely
            else:
                result.append(" ")
            i = j
            continue
        result.append(c)
        i += 1
    return "".join(result).strip()


def _tighten_js_line(line: str) -> str:
    """FB-CTS1 scaffold diet: conservative intra-line tightening. Rules:
    - any line containing '/' is returned UNCHANGED (regex/comment/division safety);
    - text inside quoted strings is untouched (quote-aware, handles \\ escapes);
    - a space (run) is dropped only when adjacent to punctuation; a space between
      two word characters always survives (`return true` never becomes `returntrue`);
    - a single space between '+' and '+' survives (`a+ +b` must not become `a++b`).
    Idempotent by construction."""
    if "/" in line:
        return line
    punct = set("{}()[];,=<>+!&|?:")
    out = []
    i, n, quote = 0, len(line), None
    while i < n:
        c = line[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(line[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1; continue
        if c in ("'", '"'):
            quote = c; out.append(c); i += 1; continue
        if c == " ":
            j = i
            while j < n and line[j] == " ":
                j += 1
            prev = out[-1] if out else ""
            nxt = line[j] if j < n else ""
            if prev == "+" and nxt == "+":
                out.append(" ")           # never mint '++'
            elif prev in punct or nxt in punct or not prev or not nxt:
                pass                       # drop the run
            else:
                out.append(" ")            # word-word: keep one space
            i = j; continue
        out.append(c); i += 1
    return "".join(out)


def _minify_js(js: str) -> str:
    """Line-level JS minifier — safe because it never joins lines or rewrites
    tokens. Drops whole-line `//` comments and blank lines, strips leading
    indentation, then applies _tighten_js_line's conservative intra-line
    tightening (slash-lines exempt, quote-aware). Trailing `//` comments and
    `/* */` blocks are left alone (their lines contain '/', so tightening
    skips them too)."""
    kept: list[str] = []
    for raw in js.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):
            continue
        kept.append(_tighten_js_line(line))
    return "\n".join(kept)


# Computed once at import — source constants stay fully commented above.
#
# Compat monoliths: the every-block concatenations. External callers/tests
# that reference _WIDGET_CSS / _WIDGET_JS_TEMPLATE keep working; per-render
# scaffolds are composed conditionally via _compose_widget_css/_js (T2.2).
def _unique_blocks(blocks: list[tuple[str, str]]) -> list[str]:
    """Every block once, first-trigger order (multi-trigger blocks — t3
    FB-4's button block — must not double up in the compat monoliths)."""
    seen: set[str] = set()
    out: list[str] = []
    for _, b in blocks:
        if b not in seen:
            out.append(b)
            seen.add(b)
    return out


_WIDGET_CSS = "\n".join([_CSS_CORE] + _unique_blocks(_CSS_FEATURE_BLOCKS))
_WIDGET_JS_TEMPLATE = "\n".join(
    _unique_blocks(_JS_FEATURE_BLOCKS) + [_JS_CORE])

_CSS_CORE_MIN = _minify_css(_CSS_CORE)
_CSS_FEATURE_BLOCKS_MIN = [(t, _minify_css(b)) for t, b in _CSS_FEATURE_BLOCKS]
_JS_CORE_MIN = _minify_js(_JS_CORE)
_JS_FEATURE_BLOCKS_MIN = [(t, _minify_js(b)) for t, b in _JS_FEATURE_BLOCKS]

_WIDGET_CSS_MIN = _minify_css(_WIDGET_CSS)
_ALL_CLEAR_CSS_MIN = _minify_css(_ALL_CLEAR_CSS)
_ONBOARDING_SETUP_CSS_MIN = _minify_css(_ONBOARDING_SETUP_CSS)
_WIDGET_JS_TEMPLATE_MIN = _minify_js(_WIDGET_JS_TEMPLATE)


def _compose_widget_css(content_html: str) -> str:
    """T2.2 conditional CSS emission: core + only the feature blocks whose
    trigger substring appears in the rendered content. Every block is
    pre-minified at import; composition is a membership test + join.
    A block listed under several triggers (t3 FB-4: the button block fires
    on legacy buttons AND on primary buttons) emits once."""
    parts = [_CSS_CORE_MIN]
    seen: set[str] = set()
    for trigger, block in _CSS_FEATURE_BLOCKS_MIN:
        if trigger in content_html and block not in seen:
            parts.append(block)
            seen.add(block)
    return "".join(parts)


def _compose_widget_js(content_html: str) -> str:
    """T2.2 conditional JS emission: only the handler blocks the content
    actually wires (dropdowns / buttons / note toggles / Snooze-rest), then
    the core (state, validation, Apply wire, bind). Multi-trigger blocks
    emit once (see the CSS twin)."""
    parts = []
    seen: set[str] = set()
    for trigger, block in _JS_FEATURE_BLOCKS_MIN:
        if trigger in content_html and block not in seen:
            parts.append(block)
            seen.add(block)
    parts.append(_JS_CORE_MIN)
    return "\n".join(parts)


# ============================================================================
# Paginate-by-design (T2, F2 rework)
#
# Unbounded data views (the full commitment set, the Staff Meeting queue) MUST
# be delivered one page at a time — a page is the unit the runtime relays as
# `widget_code`. `paginate_data_view` slices a data view to a single page of
# ~`page_size` top-level items, preserving section order and grouping (money >
# identity > hygiene on the Staff Meeting), keeping every sub_item with its
# parent, and stamping `pagination: {page, total_pages, page_size, has_more}`
# so the renderer can draw the position line + `show more` affordance.
#
# Bounded surfaces (the daily ≤5 card, small fires) call with page=None and
# render everything — pagination is inert (total_pages == 1, no position line).
# ============================================================================

# PAGESNAP: ONE page-size default for the whole stack. This was 10 here and 15
# in `surface_drivers.run_surface`, so a caller that omitted the argument got a
# different page geometry than one that passed it. 15 wins because it is what
# every live surface already requests, and because the value is a CEILING, not
# a promise — `widget_transport._fit_page_size` lowers it to whatever fits the
# relay byte budget. Import this constant; never re-type the number.
DEFAULT_PAGE_SIZE = 15


def _iter_page_items(sections: list[dict]) -> list[tuple[int, dict]]:
    """Flatten to a list of (section_index, item) in render order, counting
    only top-level items that carry at least one action OR any actionable
    sub_item (the paginate unit is the top-level row)."""
    flat: list[tuple[int, dict]] = []
    for si, section in enumerate(sections):
        for item in section.get("items", []):
            flat.append((si, item))
    return flat


def paginate_data_view(data: dict, *, page: int,
                       page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    """Return a shallow copy of `data` sliced to a single page.

    Rebuilds `sections` to contain only the top-level items that fall in the
    requested page window, dropping any section left empty and preserving
    section order/titles. Adds a `pagination` block the renderer consumes.

    Args:
      page: 1-indexed page number.
      page_size: max top-level items per page (ceiling — see DEFAULT_PAGE_SIZE).

    NOTE (PAGESNAP): this function is a PURE function of the view it is
    handed. It was never the bug — the bug was that `run_surface` handed it a
    freshly re-read view on page 2. Callers paginating an unbounded surface
    must slice a SNAPSHOT (see `page_snapshot`), not a live query.
    """
    sections = data.get("sections", []) or []
    flat = _iter_page_items(sections)
    total_items = len(flat)
    page_size = max(1, int(page_size))
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    requested_page = int(page)
    page = max(1, min(requested_page, total_pages))
    start = (page - 1) * page_size
    end = start + page_size
    keep_by_section: dict[int, list[dict]] = {}
    for idx in range(start, min(end, total_items)):
        si, item = flat[idx]
        keep_by_section.setdefault(si, []).append(item)

    new_sections: list[dict] = []
    for si, section in enumerate(sections):
        if si not in keep_by_section:
            continue
        sec_copy = dict(section)
        sec_copy["items"] = keep_by_section[si]
        new_sections.append(sec_copy)

    sliced = dict(data)
    sliced["sections"] = new_sections
    sliced["pagination"] = {
        "page": page,
        "total_pages": total_pages,
        "page_size": page_size,
        "has_more": page < total_pages,
        "total_items": total_items,
    }
    # PAGESNAP: a request past the end is CLAMPED to the last page — never
    # raised (a benign extra `show more` click must not crash a surface) and
    # never rendered empty (an empty frame is never data, the same rule the
    # tile builder enforces). But a silent clamp is the same class of fault as
    # a silent re-read: the system served something other than what was asked
    # and said nothing, and the user reads re-served rows as NEW rows. So the
    # clamp goes ON THE RECORD and the caller can say "that's the end of it".
    if requested_page != page:
        sliced["pagination"]["clamped"] = True
        sliced["pagination"]["requested_page"] = requested_page
    return sliced


# CLI mode for shell-based callers
def main() -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Render a Command Room chat output from a structured data view."
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Path to JSON data view file. If omitted, reads from stdin.",
    )
    args = parser.parse_args()

    if args.input:
        raw = open(args.input, encoding="utf-8").read()
    else:
        raw = sys.stdin.read()

    data = json.loads(raw)
    print(render_chat_output(data), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
