#!/usr/bin/env python3
"""SPEC_BOARD1 — the artifact triage board: a SERIALIZATION of the
commitment-triage surface, and nothing else.

WHY THIS EXISTS
---------------

`apply-choices` is stateless by construction: every tuple carries the
commitment's `data.id` verbatim plus `src`, and dispatch keys on id + verb,
never on which surface emitted the click. The in-chat widget is therefore only
ONE possible emitter. A published artifact page can be another — with
local-only selection state and a button that composes the exact
`apply choices: [...]` wire string for the user to PASTE into chat.

What that buys: no per-page byte-relay transport (Bug #67 class), so the full
open set renders as ONE scrollable page with working controls; and the board
lives at a stable URL, readable from a phone between sessions.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------

- **It does not write.** No `window.claude.mcp` capability, no write path, no
  queue (M ruling 2026-08-06 — both one-tap variants were rejected: a direct
  ledger write is a corruption risk with no append primitive, no undo and no
  idempotency; a queue write is silent-rot plus verb collapse). Apply stays
  synchronous and in-chat, so every interactive confirm (the OpenSubitemsError
  cascade, Later-date validation, ambiguous reassign) keeps working unchanged,
  and `undo` keeps working because the batch cache is in-session.
- **It does not derive.** It is handed the data view that
  `surface_drivers.build_commitment_triage_view` already built — the same
  load → project → build pipeline the widget path runs — and turns it into
  HTML. There is no second view builder here, ever (the freelance-path bug
  class #11 / #45 / #52). Even the tab a row lands on is READ off the row's
  `bucket` stamp, which the driver set through `commitment_state.bucket_of` —
  THE same predicate `count_commitments` counts with.
- **It does not fork a validator.** The pre-render gate family
  (`chat_output_renderer.validate_data_view`) and the leak scan
  (`chat_output_renderer.scan_rendered_html`) are CALLED. The board-shaped
  structural contract that the widget's `validate_rendered_widget` cannot
  express (it asserts the widget DOM) lives here as `validate_board_html`.

LAYOUT (M ruling 2026-08-06)
----------------------------

Two PINNED strips above three tabs:

    Unconfirmed   the 7d+ escalation pins — escalation never gets buried
                  behind a tab
    Unowned       the `unowned` bucket

Both are their own lines by standing doctrine (F-47 P2b / F-56: unowned and
unconfirmed are never folded into you-owe / owed-to-you), so they are never
absorbed into a tab.

    My Tasks      bucket you_owe, effective kind `task`
    I Owe         bucket you_owe, every other kind
    Owed to Me    bucket owed_to_you

Each tab keeps the driver's age bands (`30+ days old` / `The rest`) internally,
oldest first. Sub-item families render WHOLE inside their parent's tab.
Selection state spans ALL tabs — switching tabs never drops a selection, and
the one Copy button composes a single wire string carrying everything picked
anywhere on the board.

stdlib only.
"""
from __future__ import annotations

import html as _html
import json
import re
from pathlib import Path

# The wire contract. `src` is deliberately the EXISTING commitment-triage
# surface id: the board is an alternate emitter of that surface, not a new
# surface, so apply-choices' shipped `commitment-triage` handler section
# applies with ZERO changes.
BOARD_SRC = "commitment-triage"

# The tabs, in render order: (lane id, display label).
BOARD_TABS = (
    ("my_tasks", "My Tasks"),
    ("i_owe", "I Owe"),
    ("owed_to_me", "Owed to Me"),
)
# The pinned strips, in render order (above the tabs, visible from every tab).
BOARD_STRIPS = (
    ("unconfirmed", "Unconfirmed"),
    ("unowned", "Unowned"),
)
BOARD_LANES = tuple(l for l, _ in BOARD_STRIPS) + tuple(l for l, _ in BOARD_TABS)

# The four headline buckets a row's `bucket` stamp may carry — the same set as
# `commitment_state.HEADLINE_BUCKETS`, spelled HERE rather than imported.
# Importing `commitment_state` would put the ownership derivation inside this
# module's import surface, and "this module cannot re-derive ownership" is the
# structural property the board's one-derivation claim rests on. The board
# suite pins this tuple against `HEADLINE_BUCKETS` itself, so the two can
# never drift apart silently.
BOARD_BUCKETS = ("you_owe", "owed_to_you", "unowned", "unconfirmed")

# Where a published board's stable URL is remembered, so a redeploy keeps the
# SAME URL (the board is a bookmark; a new URL every Friday is not a board).
BOARD_URL_REL = Path("_hq") / "config" / "artifact_board.json"

# The encoding declaration this fragment opens with. The artifact viewer serves
# the fragment raw — there is no head to carry it — so the board declares its
# own encoding as its first bytes or every non-ASCII character it renders
# (em-dashes, middots) garbles on screen. Spelled once, asserted by
# `validate_board_html`, emitted by `render_board_html`.
CHARSET_DECL = '<meta charset="utf-8">'


class BoardContractError(ValueError):
    """The rendered board violated its own structural contract.

    Raised BEFORE the HTML is persisted or published, exactly as
    `validate_rendered_widget` raises before a widget ships. The fix is always
    at the render layer — never hand-patch the HTML.
    """


# ---------------------------------------------------------------------------
# Lane assignment — READ, never re-derived
# ---------------------------------------------------------------------------

def lane_of(row: dict) -> str:
    """Which lane (pinned strip or tab) a rendered row belongs to.

    Reads the `bucket` + `kind` the DRIVER stamped through
    `commitment_state.bucket_of` / `commitment_kind`. Ownership is not
    re-derived here, so a tab's membership can never disagree with the tile
    above it (the F-56 defect class, prevented structurally).

    The two strips are disjoint by bucket, which matters because
    `confirm_flow.select_unconfirmed_escalation` pins EVERY amber class at 7d+
    — an unowned row can be an escalation pin too. Bucket decides: a
    pending_review row is Unconfirmed, an ownerless row is Unowned, and
    neither can appear twice.

    RAISES on a bucket outside `BOARD_BUCKETS` (review F-4). This used to end
    `return "my_tasks" if kind == "task" else "i_owe"`, so a row with an
    unrecognized or MISSING stamp silently landed in I Owe — `lane_of({})`
    returned `i_owe`. Latent while `bucket_of` only ever returns a headline
    bucket, and precisely the F-56 drift this module claims to prevent
    structurally the moment a fifth bucket exists: rows would file themselves
    into the wrong tab while `my_tasks + i_owe == you_owe` quietly stopped
    meaning anything. A lane the board cannot derive is a decision somebody
    has to make, not a default it may pick.
    """
    bucket = (row.get("bucket") or "").strip()
    if bucket not in BOARD_BUCKETS:
        row_id = row.get("n") or row.get("id") or "?"
        raise BoardContractError(
            f"row {row_id!r} carries bucket {bucket!r}, which is not one of "
            f"{', '.join(BOARD_BUCKETS)} — a row whose bucket this board does "
            "not recognise has no lane. Give the new bucket a lane "
            "deliberately; never let it drift into a tab by falling through.")
    if bucket == "unconfirmed":
        return "unconfirmed"
    if bucket == "unowned":
        return "unowned"
    if bucket == "owed_to_you":
        return "owed_to_me"
    # bucket == "you_owe" — split by effective kind.
    return "my_tasks" if (row.get("kind") or "") == "task" else "i_owe"


def partition_board(view: dict) -> dict:
    """The view's sections, re-grouped into lanes.

    Returns `{lane: [{"band": <the driver's section title>, "row": <row>}, …]}`
    preserving the driver's own order (oldest first inside each band, bands in
    the driver's order). Pure regrouping — no row is created, dropped,
    duplicated, or re-ordered.
    """
    out: dict = {lane: [] for lane in BOARD_LANES}
    for section in view.get("sections") or []:
        band = section.get("title") or ""
        for row in section.get("items") or []:
            out[lane_of(row)].append({"band": band, "row": row})
    return out


def lane_counts(view: dict) -> dict:
    """Rows per lane — what the tab labels and strip headings carry."""
    part = partition_board(view)
    return {lane: len(part[lane]) for lane in BOARD_LANES}


# ---------------------------------------------------------------------------
# The wire string — the ONE thing the Copy button produces
# ---------------------------------------------------------------------------

def compose_wire_string(selections) -> str:
    """The exact string the page's Copy button puts on the clipboard.

    `selections` is an ordered list of `{"n", "action", "input"?}`. The result
    is the literal prefix `apply choices: ` followed by
    `JSON.stringify(choices)` — separators and key order matched to the page
    JS below, so the Python mirror and the browser produce byte-identical
    output for the same picks.

    Every tuple carries `src` = BOARD_SRC, so apply-choices dispatches through
    its shipped commitment-triage handlers with nothing new to learn.
    """
    choices = []
    for sel in selections:
        choice = {"n": sel["n"], "action": sel["action"]}
        value = (sel.get("input") or "")
        if isinstance(value, str) and value.strip():
            choice["input"] = value.strip()
        choice["src"] = BOARD_SRC
        choices.append(choice)
    return "apply choices: " + json.dumps(choices, separators=(",", ":"),
                                          ensure_ascii=False)


# ---------------------------------------------------------------------------
# Reading a rendered board back (tests + the structural contract)
# ---------------------------------------------------------------------------

_ROW_RE = re.compile(
    r'<div class="crb-row(?P<cls>[^"]*)"(?P<attrs>[^>]*)>', re.IGNORECASE)
_ATTR_RE = re.compile(r'\bdata-([a-z-]+)="([^"]*)"')
_SELECT_RE = re.compile(
    r'<select class="crb-action" data-n="(?P<n>[^"]*)"[^>]*>(?P<body>.*?)</select>',
    re.IGNORECASE | re.DOTALL)
_OPTION_RE = re.compile(r'<option value="(?P<value>[^"]*)"[^>]*>', re.IGNORECASE)
_INPUT_RE = re.compile(
    r'<input class="crb-input"[^>]*data-input-for-n="(?P<n>[^"]*)"[^>]*'
    r'data-input-for-action="(?P<action>[^"]*)"[^>]*>', re.IGNORECASE)


def embedded_rows(html: str) -> list:
    """Every row the board rendered, read back out of the HTML itself.

    Returns `[{"n", "lane", "kind", "parent", "is_sub", "actions": [...]}, …]`
    in DOM order — the same order the Copy button walks. Tests use this so the
    round trip starts from the RENDERED page rather than from the data view
    (a board that renders a row it cannot dispatch is exactly the failure this
    is meant to catch).
    """
    selects = {m.group("n"): [o.group("value")
                              for o in _OPTION_RE.finditer(m.group("body"))
                              if o.group("value")]
               for m in _SELECT_RE.finditer(html)}
    out = []
    for m in _ROW_RE.finditer(html):
        attrs = dict(_ATTR_RE.findall(m.group("attrs")))
        n = attrs.get("n")
        if not n:
            continue
        out.append({
            "n": _html.unescape(n),
            "lane": attrs.get("lane", ""),
            "kind": attrs.get("kind", ""),
            "parent": _html.unescape(attrs.get("parent", "")) or None,
            "is_sub": "crb-sub" in (m.group("cls") or ""),
            "actions": [_html.unescape(a) for a in selects.get(n, [])],
        })
    return out


def embedded_tuples(html: str) -> list:
    """The dispatchable tuple SHELL for every rendered row: `{n, action, src}`
    using the row's FIRST offered verb. The round-trip test fills in real
    picks; this is the shape assertion."""
    return [{"n": r["n"], "action": (r["actions"] or [""])[0], "src": BOARD_SRC}
            for r in embedded_rows(html)]


# ---------------------------------------------------------------------------
# The structural contract
# ---------------------------------------------------------------------------

# Anything that would make the page reach off-host. The artifact CSP blocks
# every external host, so a board that needs one is a board that renders
# broken for the user and discovers it in production.
_EXTERNAL_MARKERS = (
    "http://", "https://", "//cdn", "<link", "<img", "<iframe", "@import",
    "url(", "fetch(", "XMLHttpRequest", "WebSocket", "importScripts",
    "navigator.sendBeacon",
)
# The write paths this build is FORBIDDEN to grow (§3 ruling). Named here so
# adding one later trips a test rather than a review.
_WRITE_PATH_MARKERS = ("window.claude", "sendPrompt", "crSendPrompt",
                       "localStorage", "sessionStorage", "indexedDB")


def validate_board_html(html: str, *, expect_rows: int | None = None) -> None:
    """Board-shaped structural assertion — the `validate_rendered_widget`
    analog for this surface.

    `validate_rendered_widget` asserts the WIDGET DOM (cr-action buttons,
    cr-action-input wrappers, the F-58 feedback chrome). The board is a
    different DOM with a different affordance — a clipboard, not an Apply — so
    forcing that contract on it would assert nothing true. These are the
    invariants that ARE true of a board:

      1. every row carries a non-empty `data-n`, and no id renders twice
         (identity contract, Stage B: the id is what dispatch keys on);
      2. every offered verb is a registered canonical action (the taxonomy is
         the one source of verbs; a board verb with no row would paste a
         tuple apply-choices refuses);
      3. every REQUIRED-input verb has its matching input control on the row
         (the F-17 dead-button class, ported to this surface);
      4. the copy affordance exists in BOTH forms — the button and the
         always-there textarea fallback (the textarea IS the mechanism when
         the sandbox has no clipboard API);
      5. the page composes the literal `apply choices: ` prefix, exactly once,
         with `src` bound to the commitment-triage surface;
      6. nothing reaches an external host (artifact CSP) and no write path
         exists (§3: copy-paste apply, never a write);
      7. the generated-at stamp is present — a board is stale by design and
         must say when it was made;
      8. the fragment OPENS with its charset declaration. The artifact viewer
         serves these bytes raw, so an undeclared encoding is decoded by
         guess and every em-dash and middot on the page garbles. Asserted
         here — before persist, before publish — because the failure is
         invisible to every source-side scanner: the bytes on disk are
         correct UTF-8 and the corruption happens at display-time decode.

    `expect_rows` (passed by `run_board`, which knows) asserts CONSERVATION:
    the page carries exactly as many top-level rows as the view handed over.
    That is the check that catches a serializer silently dropping a row — an
    empty board is a legitimate answer (nothing is open), a board one row
    short is not, and a bare "did any row render?" cannot tell those apart.

    Raises BoardContractError listing every violation. Never warns.
    """
    from chat_output_renderer import is_canonical_action
    from verb_taxonomy import REQUIRED_INPUT_ACTION_IDS

    problems: list[str] = []
    rows = embedded_rows(html)
    if expect_rows is not None:
        n_top = len([r for r in rows if not r["is_sub"]])
        if n_top != expect_rows:
            problems.append(
                f"the view handed over {expect_rows} top-level row(s) and the "
                f"page rendered {n_top} — serialization must not lose rows")

    seen: set = set()
    for r in rows:
        if not r["n"]:
            problems.append("a row rendered without data-n")
        elif r["n"] in seen:
            problems.append(f"row id {r['n']!r} rendered more than once")
        seen.add(r["n"])
        for action in r["actions"]:
            if not is_canonical_action(action):
                problems.append(
                    f"row {r['n']!r} offers {action!r}, which has no "
                    "verb_taxonomy row")

    inputs = {(m.group("n"), _html.unescape(m.group("action")))
              for m in _INPUT_RE.finditer(html)}
    for r in rows:
        for action in r["actions"]:
            if action in REQUIRED_INPUT_ACTION_IDS and (r["n"], action) not in inputs:
                problems.append(
                    f"row {r['n']!r} offers {action!r} (input required) with "
                    "no input control — the F-17 dead-button class")

    if 'id="crb-copy"' not in html:
        problems.append("no Copy button")
    if "<textarea" not in html:
        problems.append("no textarea fallback — the clipboard API is not "
                        "guaranteed inside the artifact sandbox")
    n_prefix = html.count("apply choices: ")
    if n_prefix != 1:
        problems.append(
            f"the literal 'apply choices: ' prefix appears {n_prefix} times "
            "— it must be composed exactly once")
    if f'"{BOARD_SRC}"' not in html and f"'{BOARD_SRC}'" not in html:
        problems.append(f"the wire src {BOARD_SRC!r} is not bound in the page")
    if "data-generated-at=" not in html:
        problems.append("no generated-at stamp — a board that hides its age "
                        "lies about how stale it is")
    if not html.startswith(CHARSET_DECL):
        problems.append(
            f"the fragment does not open with {CHARSET_DECL!r} — the artifact "
            "viewer serves these bytes with no head to declare an encoding, so "
            "it guesses, and every em-dash and middot renders as mojibake")

    low = html
    for marker in _EXTERNAL_MARKERS:
        if marker in low:
            problems.append(f"external reference {marker!r} — the artifact "
                            "CSP blocks every external host")
    for marker in _WRITE_PATH_MARKERS:
        if marker in low:
            problems.append(f"write path {marker!r} — this board is "
                            "copy-paste apply only (§3 ruling)")

    if problems:
        raise BoardContractError(
            "the rendered board violates its structural contract:\n  - "
            + "\n  - ".join(problems))


# ---------------------------------------------------------------------------
# The stable-URL memory (so a redeploy keeps the same link)
# ---------------------------------------------------------------------------

def board_url_path(workspace_root) -> Path:
    return Path(workspace_root) / BOARD_URL_REL


def load_board_url(workspace_root):
    """The artifact URL this workspace's board already lives at, or None.

    The publishing skill passes it back on every redeploy so the URL never
    changes. Defensive: a missing or corrupt file reads as "no URL yet" —
    losing the memory costs one new URL, never a failed publish."""
    p = board_url_path(workspace_root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    url = (data or {}).get("url")
    return url if isinstance(url, str) and url.strip() else None


def save_board_url(workspace_root, url: str, *, published_at: str) -> Path:
    """Remember the board's stable URL. Atomic, so a crash mid-write can never
    leave a half-written pointer that reads as "no URL" and mints a second
    board."""
    from atomic_write import atomic_write_text

    p = board_url_path(workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(
        {"url": url, "published_at": published_at, "surface": BOARD_SRC},
        indent=2, ensure_ascii=False))
    return p


# ---------------------------------------------------------------------------
# The serializer
# ---------------------------------------------------------------------------

def _esc(text) -> str:
    return _html.escape(str(text if text is not None else ""), quote=True)


def generated_at_line(now_iso: str, workspace_root) -> str:
    """The board header's prominent generated-at line, localized via
    `tz.to_local()` (workspace timezone is the workspace's, never the
    renderer's). A workspace with no timezone set degrades HONESTLY — it says
    the stamp is UTC rather than printing a local-looking time that is not
    local."""
    try:
        from tz import to_local
        local = to_local(now_iso, workspace_path=str(workspace_root))
        if local is not None:
            return local.strftime("Generated %a %b %d, %Y at %I:%M %p %Z").replace(
                " 0", " ")
    except Exception:
        pass
    return (f"Generated {now_iso} UTC — set your timezone "
            "to see this in local time")


def _render_verbs(row: dict, actions: list) -> str:
    """The row's verb picker + its required-input controls.

    Display labels come from `chat_output_renderer._action_display_label`,
    which reads the verb taxonomy — never restated by hand (F-59: one label
    per wire id, everywhere). The offered SET is the widget's own display set
    (`_merge_later_verbs`), so board and widget show the same options for the
    same row.
    """
    from chat_output_renderer import _action_display_label
    from verb_taxonomy import REQUIRED_INPUT_ACTION_IDS, required_input_thing

    n = _esc(row["n"])
    opts = ['<option value="">— pick an action —</option>']
    inputs = []
    for action in actions:
        needs = action in REQUIRED_INPUT_ACTION_IDS
        opts.append(
            f'<option value="{_esc(action)}" data-input-required='
            f'"{"1" if needs else "0"}">{_esc(_action_display_label(action))}'
            f"</option>")
        if needs:
            thing = required_input_thing(action)
            inputs.append(
                f'<input class="crb-input" type="text" hidden '
                f'data-input-for-n="{n}" '
                f'data-input-for-action="{_esc(action)}" '
                f'placeholder="{_esc(thing)}" '
                f'aria-label="{_esc(thing)}" />')
    return (f'<div class="crb-verbs">'
            f'<select class="crb-action" data-n="{n}" '
            f'aria-label="action">{"".join(opts)}</select>'
            f'{"".join(inputs)}</div>')


def _render_row(row: dict, lane: str, *, is_sub: bool = False,
                parent_id: str | None = None) -> str:
    from chat_output_renderer import _merge_later_verbs, mark_field

    actions = _merge_later_verbs(list(row.get("actions") or []))
    cls = " crb-sub" if is_sub else ""
    parent_attr = (f' data-parent="{_esc(parent_id)}"' if parent_id else "")
    # The TITLE is the user's own sentence — the one string on this row nobody
    # here composed — so it carries the provenance marker Gate 2 reads. The
    # context tag, the annotations and the reduced-verbs reason below are all
    # DRIVER-composed and deliberately unmarked: a leak in those is the leak
    # the gate exists to catch.
    # WALKFIX1 Item E — the blank-card guard. A row with no title renders the
    # shared repair placeholder instead of an empty card (the live case: an
    # unowned, undated scheduling row whose card came out with nothing in it).
    # The placeholder is RENDERER prose about a damaged record, so it is never
    # marked as the user's words — a fixed string the renderer wrote must keep
    # facing the whole vocabulary scan.
    from commitment_state import UNTITLED_PLACEHOLDER

    _name = row.get("name")
    _title_html = (mark_field(row, "name", _esc(_name), surface="board")
                   if isinstance(_name, str) and _name.strip()
                   else _esc(UNTITLED_PLACEHOLDER))
    bits = [
        f'<div class="crb-row{cls}" data-n="{_esc(row["n"])}" '
        f'data-lane="{_esc(lane)}" data-kind="{_esc(row.get("kind") or "")}"'
        f"{parent_attr}>",
        f'<div class="crb-title">{_title_html}</div>',
    ]
    if row.get("context_tag"):
        bits.append(f'<div class="crb-tag">{_esc(row["context_tag"])}</div>')
    for note in row.get("annotations") or []:
        bits.append(f'<div class="crb-note">{_esc(note)}</div>')
    if row.get("reduced_verbs_reason"):
        bits.append(f'<div class="crb-note">{_esc(row["reduced_verbs_reason"])}</div>')
    bits.append(_render_verbs(row, actions))
    # SUB1 — the family renders WHOLE, inside the parent's lane. Children carry
    # their own data.id verbatim and their own per-kind verb set.
    kids = row.get("sub_items") or []
    if kids:
        bits.append('<div class="crb-kids">')
        from chat_output_renderer import COMPOSED_FIELDS_KEY
        for kid in kids:
            child = {"n": kid.get("id"), "name": kid.get("summary"),
                     "actions": kid.get("actions") or [],
                     # The CHILD's own effective kind, stamped by the driver — a
                     # child of a promise can itself be a task, and labelling it
                     # with its parent's kind would be a second derivation that
                     # happens to be wrong.
                     "kind": kid.get("kind") or ""}
            # ...and the child's own PROVENANCE declaration. This dict is a
            # fresh projection of the sub-item, so anything not copied across
            # is silently lost — and losing a "I composed this myself" flag
            # would mark a driver-written summary as the user's words.
            if kid.get(COMPOSED_FIELDS_KEY):
                child[COMPOSED_FIELDS_KEY] = kid[COMPOSED_FIELDS_KEY]
            bits.append(_render_row(child, lane, is_sub=True,
                                    parent_id=row["n"]))
        bits.append("</div>")
    bits.append("</div>")
    return "".join(bits)


# The board wears the PRODUCT's widget chrome — the same warm-charcoal
# surfaces and brass accent `_WIDGET_CSS` uses, and deliberately NOT a new
# palette (SPEC OUT2 §2c: colors for CLIENT DELIVERABLE surfaces resolve
# through `brand.get_brand()`; product UI is the documented carve-out that the
# widget chrome already sits in, and this page is the same product UI in a
# different frame). Every literal below already appears in that chrome:
# #14110F ink · #1A1714 / #221E1A surfaces · #2A2520 / #3A3530 borders ·
# #E8E0D6 text · #B5A998 muted · #B88B4A brass · #C9A570 brass-lift.
# The page sets its own background because it is a standalone document, not a
# fragment dropped into a themed chat card.
_BOARD_CSS = """
.crb{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 color:#E8E0D6;background:#14110F;max-width:900px;margin:0 auto;padding:12px}
.crb h1{font-size:20px;margin:0 0 4px;color:#E8E0D6}
.crb-stamp{font-size:12px;color:#B5A998;margin-bottom:12px}
.crb-tiles{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.crb-tile{border:1px solid #2A2520;border-radius:6px;padding:6px 10px;
 min-width:78px;background:#1A1714}
.crb-tile b{display:block;font-size:18px;color:#E8E0D6}
.crb-tile span{font-size:11px;color:#B5A998;text-transform:uppercase;
 letter-spacing:.04em}
.crb-pointer{font-size:13px;color:#B5A998;margin-bottom:12px}
.crb-strip{border:1px solid #B88B4A;border-radius:8px;padding:8px 10px;
 margin-bottom:10px;background:#1A1714}
.crb-strip h2{font-size:13px;margin:0 0 6px;text-transform:uppercase;
 letter-spacing:.05em;color:#C9A570}
.crb-tabs{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 10px;
 border-bottom:1px solid #2A2520}
.crb-tab{font:inherit;background:none;border:1px solid transparent;
 border-bottom:none;border-radius:6px 6px 0 0;padding:7px 12px;cursor:pointer;
 color:#B5A998}
.crb-tab[aria-selected="true"]{border-color:#2A2520;background:#221E1A;
 color:#E8E0D6;font-weight:600}
.crb-panel[hidden]{display:none}
.crb-band{font-size:12px;text-transform:uppercase;letter-spacing:.05em;
 color:#B5A998;margin:12px 0 6px}
.crb-row{border:1px solid #2A2520;border-radius:8px;padding:8px 10px;
 margin-bottom:8px;background:#1A1714}
.crb-row.crb-armed{border-color:#B88B4A;background:#221E1A}
.crb-title{font-weight:600;color:#E8E0D6}
.crb-tag{font-size:12px;color:#B5A998;margin-top:2px}
.crb-note{font-size:12px;color:#C9A570;margin-top:2px}
.crb-verbs{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.crb-action,.crb-input{font:inherit;padding:4px 6px;border:1px solid #3A3530;
 border-radius:5px;background:#221E1A;color:#E8E0D6}
.crb-kids{margin:8px 0 0 16px;border-left:2px solid #2A2520;padding-left:10px}
.crb-sub{border-style:dashed}
.crb-empty{font-size:13px;color:#B5A998;padding:6px 0}
.crb-apply{position:sticky;bottom:0;background:#14110F;
 border-top:1px solid #2A2520;padding:10px 0;margin-top:16px}
.crb-count{font-size:13px;margin-bottom:6px;color:#E8E0D6}
#crb-copy{font:inherit;font-weight:600;padding:8px 14px;border-radius:6px;
 border:1px solid #B88B4A;background:#B88B4A;color:#14110F;cursor:pointer}
#crb-copy[disabled]{opacity:.45;cursor:not-allowed}
.crb-status{font-size:12px;color:#B5A998;margin-top:6px;min-height:1em}
.crb-out{width:100%;box-sizing:border-box;margin-top:6px;font-family:
 ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
 background:#221E1A;color:#E8E0D6;border:1px solid #3A3530;border-radius:5px}
.crb-help{font-size:12px;color:#B5A998;margin-top:4px}
"""

# The page script. Deliberately small, deliberately inert: it selects, it
# composes, it copies. There is no transport in it — no fetch, no XHR, no
# storage, no host callback — which is what makes "the board never writes" a
# property of the artifact rather than a promise in a doc.
_BOARD_JS = r"""
(function(){
  var rows = Array.prototype.slice.call(document.querySelectorAll('.crb-row'));
  var out = document.getElementById('crb-out');
  var btn = document.getElementById('crb-copy');
  var count = document.getElementById('crb-count');
  var status = document.getElementById('crb-status');

  function inputFor(row, action){
    var boxes = row.querySelectorAll('.crb-input');
    for (var i = 0; i < boxes.length; i++){
      if (boxes[i].getAttribute('data-input-for-action') === action
          && boxes[i].getAttribute('data-input-for-n') === row.getAttribute('data-n')){
        return boxes[i];
      }
    }
    return null;
  }

  function picks(){
    var chosen = [], missing = [];
    rows.forEach(function(row){
      var sel = row.querySelector('.crb-action');
      if (!sel || !sel.value) { row.classList.remove('crb-armed'); return; }
      row.classList.add('crb-armed');
      var action = sel.value;
      var opt = sel.options[sel.selectedIndex];
      var needs = opt && opt.getAttribute('data-input-required') === '1';
      var box = inputFor(row, action);
      var value = box && box.value ? box.value.trim() : '';
      if (needs && !value) { missing.push(row); return; }
      chosen.push({ n: row.getAttribute('data-n'), action: action, input: value });
    });
    return { chosen: chosen, missing: missing };
  }

  function compose(chosen){
    var choices = chosen.map(function(c){
      var o = { n: c.n, action: c.action };
      if (c.input) { o.input = c.input; }
      o.src = SRC;
      return o;
    });
    return PREFIX + JSON.stringify(choices);
  }

  function refresh(){
    var p = picks();
    out.value = p.chosen.length ? compose(p.chosen) : '';
    btn.disabled = p.chosen.length === 0;
    var n = p.chosen.length;
    var line = n === 0 ? 'Nothing picked yet.'
      : (n === 1 ? '1 action ready to copy.' : n + ' actions ready to copy.');
    if (p.missing.length) {
      line += ' ' + p.missing.length + (p.missing.length === 1
        ? ' row still needs its value typed in.'
        : ' rows still need their values typed in.');
    }
    count.textContent = line;
    status.textContent = '';
  }

  function showInputs(sel){
    var row = sel.closest ? sel.closest('.crb-row') : null;
    if (!row) { return; }
    var boxes = row.querySelectorAll('.crb-input');
    for (var i = 0; i < boxes.length; i++){
      var mine = boxes[i].getAttribute('data-input-for-n') === row.getAttribute('data-n');
      boxes[i].hidden = !(mine
        && boxes[i].getAttribute('data-input-for-action') === sel.value);
    }
  }

  document.querySelectorAll('.crb-action').forEach(function(sel){
    sel.addEventListener('change', function(){ showInputs(sel); refresh(); });
  });
  document.querySelectorAll('.crb-input').forEach(function(box){
    box.addEventListener('input', refresh);
  });

  document.querySelectorAll('.crb-tab').forEach(function(tab){
    tab.addEventListener('click', function(){
      var want = tab.getAttribute('data-tab');
      document.querySelectorAll('.crb-tab').forEach(function(t){
        t.setAttribute('aria-selected', String(t === tab));
      });
      document.querySelectorAll('.crb-panel').forEach(function(p){
        p.hidden = p.getAttribute('data-tab') !== want;
      });
    });
  });

  btn.addEventListener('click', function(){
    var p = picks();
    if (!p.chosen.length) { return; }
    var wire = compose(p.chosen);
    out.value = wire;
    out.focus();
    out.select();
    var done = function(){ status.textContent =
      'Copied. Paste it into any Command Room chat.'; };
    var manual = function(){ status.textContent =
      'Copy the line in the box below, then paste it into any Command Room chat.'; };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(wire).then(done, manual);
        return;
      }
    } catch (e) { /* fall through to the textarea, which is always there */ }
    manual();
  });

  refresh();
})();
"""


# The tile whose value is the WHOLE pending-review queue. The Unconfirmed
# strip pins only the escalated subset, so this is the number the strip has to
# reconcile against. Looked up by label because `counters` is the view's own
# published shape; a missing or unreadable tile degrades to the plain heading
# rather than to an invented number.
UNCONFIRMED_TILE_LABEL = "Unconfirmed"


def _tile_value(view: dict, label: str):
    """The view's own value for one headline tile, as an int, or None.

    None is a real answer — it means the view did not publish that tile, and a
    heading that cannot read the total says nothing about it."""
    for tile in view.get("counters") or []:
        if str(tile.get("label") or "").strip().lower() != label.lower():
            continue
        value = tile.get("value")
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
    return None


def _strip_heading(view: dict, lane: str, label: str, shown: int) -> str:
    """One pinned strip's heading.

    The Unconfirmed strip is the one heading that carries a RECONCILIATION.
    The strip pins the 7d+ escalations only (`select_unconfirmed_escalation`),
    while the headline tile counts the whole pending-review queue — so a phone
    reader saw the strip's number and never learned the larger one existed.
    Every other bucket on this page reconciles exactly; this one now says its
    own arithmetic out loud and names the verb that reaches the remainder.

    Both numbers are DERIVED: `shown` is the count of rows this strip actually
    renders (so it stays honest about the ownerless escalation pin that files
    into the Unowned strip by design), and the total is read off the view's own
    tile. No literal, no second derivation. When the tile is missing, or
    smaller than what the strip renders, or equal to it (nothing in the
    remainder), the heading degrades to the plain form — a reconciliation with
    nothing to reconcile is noise.
    """
    plain = f"{_esc(label)} ({shown})"
    if lane != "unconfirmed":
        return plain
    total = _tile_value(view, UNCONFIRMED_TILE_LABEL)
    if total is None or total <= shown:
        return plain
    # WALKFIX1 Item E — the sentence itself comes from THE shared derivation,
    # so the board strip, the widget section header and the widget quick-read
    # cannot drift into three different vocabularies for one queue. `shown` is
    # what this strip renders and the ownerless escalation files into the
    # Unowned strip by design, so on the board shown == escalated.
    from commitment_state import unconfirmed_slices

    slices = unconfirmed_slices(queue_total=total, shown=shown,
                                escalated=shown)
    if slices["degraded"]:
        return plain
    return f"{_esc(label)} — {slices['board_heading_tail']}"


def render_board_html(view: dict, *, generated_at: str, stamp_line: str) -> str:
    """The board, as one self-contained HTML document.

    Inline CSS + inline JS, zero external requests (the artifact CSP blocks
    every external host, so anything remote is a page that renders broken in
    production). `view` is the commitment-triage data view, already built and
    already gated — this function only serializes it.
    """
    part = partition_board(view)
    counts = {lane: len(part[lane]) for lane in BOARD_LANES}

    head = [
        f'<div class="crb" data-generated-at="{_esc(generated_at)}">',
        f'<h1>{_esc(view.get("header") or "Commitment board")}</h1>',
        f'<div class="crb-stamp">{_esc(stamp_line)} · this page does not '
        "refresh itself — re-publish it to see today&#39;s list</div>",
    ]
    tiles = view.get("counters") or []
    if tiles:
        head.append('<div class="crb-tiles">')
        for tile in tiles:
            head.append(
                f'<div class="crb-tile"><b>{_esc(tile.get("value"))}</b>'
                f'<span>{_esc(tile.get("label"))}</span></div>')
        head.append("</div>")
    pointer = view.get("pointer") or view.get("quick_read")
    if pointer:
        head.append(f'<div class="crb-pointer">{_esc(pointer)}</div>')

    def _lane_body(lane: str, *, bands: bool = True) -> str:
        """One lane's rows, in the driver's order.

        `bands` is False for the pinned strips: a strip is ONE labelled block
        (its heading is the label), so re-printing the driver's section title
        inside it would put the word "Unconfirmed" above an Unowned row —
        `select_unconfirmed_escalation` pins every amber class, so an unowned
        row can arrive from that section. The tabs keep their age bands, which
        is where a band means something.
        """
        bits = []
        band = None
        for entry in part[lane]:
            if bands and entry["band"] != band:
                band = entry["band"]
                bits.append(f'<div class="crb-band">{_esc(band)}</div>')
            bits.append(_render_row(entry["row"], lane))
        return "".join(bits)

    # The pinned strips — above the tabs, visible from every tab, never folded
    # into one (F-47 P2b / F-56). Drop-empty: a board with nothing unconfirmed
    # says nothing about unconfirmed rather than printing a reassuring zero.
    strips = []
    for lane, label in BOARD_STRIPS:
        if not counts[lane]:
            continue
        strips.append(
            f'<section class="crb-strip" data-lane="{lane}">'
            f"<h2>{_strip_heading(view, lane, label, counts[lane])}</h2>"
            f"{_lane_body(lane, bands=False)}</section>")

    tabs = ['<nav class="crb-tabs" role="tablist">']
    panels = []
    for i, (lane, label) in enumerate(BOARD_TABS):
        first = i == 0
        tabs.append(
            f'<button class="crb-tab" type="button" role="tab" '
            f'data-tab="{lane}" aria-selected="{"true" if first else "false"}">'
            f"{_esc(label)} ({counts[lane]})</button>")
        body = _lane_body(lane) or (
            '<div class="crb-empty">Nothing here right now.</div>')
        panels.append(
            f'<div class="crb-panel" data-tab="{lane}" role="tabpanel"'
            f'{"" if first else " hidden"}>{body}</div>')
    tabs.append("</nav>")

    footer = [
        '<div class="crb-apply">',
        '<div class="crb-count" id="crb-count">Nothing picked yet.</div>',
        '<button id="crb-copy" type="button" disabled>Copy triage command'
        "</button>",
        '<div class="crb-status" id="crb-status"></div>',
        '<label class="crb-help" for="crb-out">Paste this line into any '
        "Command Room chat — nothing is applied until you do.</label>",
        '<textarea class="crb-out" id="crb-out" rows="3" readonly></textarea>',
        "</div>",
        "</div>",
    ]

    # The two literals the page composes with, bound once, in the script.
    js = (f'var PREFIX = "apply choices: ";\nvar SRC = "{BOARD_SRC}";\n'
          + _BOARD_JS)
    # CHARSET FIRST — before any byte a decoder could guess from.
    #
    # This fragment is served RAW by the artifact viewer: no doctype, no head,
    # nothing upstream declaring an encoding. The bytes are clean UTF-8 (the
    # em-dashes in the verb picker and the middots in every row's context tag
    # are real), and a viewer that defaults to Latin-1/cp1252 renders every one
    # of them as mojibake. The widget surface never showed this because
    # show_widget's host owns the charset; the artifact path has no host.
    #
    # A source-side leak/mojibake scanner is STRUCTURALLY blind to this class —
    # the source bytes are correct and the corruption happens at display-time
    # decode — so the declaration is the only fix, and `validate_board_html`
    # asserts it before any board is persisted or published.
    return (CHARSET_DECL
            + f"<style>{_BOARD_CSS}</style>"
            + "".join(head) + "".join(strips) + "".join(tabs)
            + "".join(panels) + "".join(footer)
            + f"<script>{js}</script>")


__all__ = [
    "BOARD_SRC",
    "CHARSET_DECL",
    "BOARD_TABS",
    "BOARD_STRIPS",
    "BOARD_LANES",
    "BOARD_BUCKETS",
    "BOARD_URL_REL",
    "BoardContractError",
    "lane_of",
    "partition_board",
    "lane_counts",
    "compose_wire_string",
    "embedded_rows",
    "embedded_tuples",
    "validate_board_html",
    "board_url_path",
    "load_board_url",
    "save_board_url",
    "generated_at_line",
    "render_board_html",
]
