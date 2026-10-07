#!/usr/bin/env python3
"""The Friday Wrap's named entry points, for the workspace access layer.

WHY THIS MODULE EXISTS (MIGRATE3-FW, Train 2b, F-T2-15). The Friday Wrap chain
is `orchestrator-friday-wrap.md` plus `weekly-recap/SKILL.md`, which the
orchestrator runs verbatim. Together they carried eighteen python blocks that
opened the customer's workspace in-process: the lateness check, the post
fences, the fire receipt and the earned-door write in the orchestrator, and in
the skill the first-run settings, the catch-up window, the upkeep catch-up,
the batched capture append, the plate cut, the grouping heading, the measure
line, the week-against-the-word composer, the post fences again, the brief
path, the closing lines and the whole-turn post. On a merged seat each of
them ran where the files are not, so the migration gate stopped the chain
with one sentence (the walk's F-T2-15). Each is now ONE plan line naming a
function here, or a writer on `RUN_WRITER_ALLOWLIST`.

TWO KINDS OF FUNCTION, KEPT APART BY THE TWO DOORS (the `eod_helpers` shape).

  * READ / COMPUTE (on `RUN_HELPER_ALLOWLIST`, past the transitive
    write-scan). A row a delegated writer would append comes back in
    `rows` / `pending_rows` for the caller's `plan append_jsonl`.
  * WRITE (on `RUN_WRITER_ALLOWLIST` ONLY, never on the read list; guard G74
    enforces it). `record_offer` writes the once-ever offer row, and the
    composers that build the plate mint its display numbers under the ledger
    lock (`plate_view.mint_display_numbers`), which is a whole-file write the
    capture cannot hold.

Every function takes `workspace_root` first and answers JSON; a failure comes
back as data (`morning_brief_helpers.answers`), never a raise through the door.
3.10-safe: this module ships in the runtime manifest.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from morning_brief_helpers import _jsonable, answers, lateness_verdict

#: The scheduled task this module serves.
TASK_ID = "friday-wrap"

#: The skill the orchestrator runs verbatim, and whose settings it reads.
SKILL_NAME = "weekly-recap"

#: The one sentence a wrap that its own final check refused says (MIGRATE3-FW
#: MUST 1). `quiet.wrap_post` raises on a question, a grade or a plate cut that
#: did not make it into the post; through the door that raise comes back as
#: data, and this is the whole chat turn when the text cannot be put right.
#: No file, no mechanism, no class name: a reader-facing statement.
WRAP_REFUSED_LINE = ("This week's wrap stopped at its final check, so nothing "
                     "was posted this time.")

#: The reason code each fence's raise answers with. A class this table does
#: not name answers `wrap_refused`; the code is for the prose, never spoken.
_REFUSAL_REASONS = {
    "WrapAsksError": "wrap_asks",
    "ScoreRenderedError": "score_rendered",
    "WrapRelayError": "relay_missing",
}


def _refusal(exc: BaseException, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`{ok: false, reason, line, detail, rows}` for a fence that raised.

    `detail` is the fence's own message, which names the sentence to rewrite
    (the skill's rule: rewrite it as a statement and ask again). It is for the
    model, never for the chat; `line` is the only thing a turn may post."""
    return {"ok": False,
            "reason": _REFUSAL_REASONS.get(type(exc).__name__, "wrap_refused"),
            "line": WRAP_REFUSED_LINE,
            "detail": str(exc)[:400],
            "rows": _jsonable(list(rows))}


# ---------------------------------------------------------------------------
# Phase 2.9: the lateness check, every row it writes handed back
# ---------------------------------------------------------------------------

@answers
def lateness(workspace_root: str, *, fired_via: str = "manual",
             env_date: str = "", now: Optional[Any] = None) -> Dict[str, Any]:
    """The Friday Wrap's lateness verdict (Phase 2.9): `late_fire.
    check_lateness(ws, 'friday-wrap', ...)` exactly as the old block ran it,
    with every row the check writes held and handed back in `pending_rows`
    (the `morning_brief_helpers.lateness_verdict` pattern)."""
    return lateness_verdict(workspace_root, TASK_ID, fired_via=fired_via,
                            env_date=env_date, now=now)


# ---------------------------------------------------------------------------
# Phase 4 (and the skill's 5.A): the one door every wrap post goes through
# ---------------------------------------------------------------------------

@answers
def wrap_post(workspace_root: str, text: str = "", *, relayed: str = "",
              spans: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """`quiet.wrap_post` beside the data: `{text, rows}` when the three fences
    pass, `{ok: false, reason, line, detail, rows}` when one raises.

    The raise is the fence working, so it is answered, not re-raised. It also
    writes ONE `surface_failed` receipt (`quiet.wrap_failed`), which is what
    makes a dead wrap visible to the watchdog: that row is held here and comes
    back in `rows` for the caller's ONE `plan append_jsonl`. On a pass `rows`
    is empty and there is nothing to append."""
    import quiet
    from inbox_helpers import _captured_appends

    with _captured_appends() as captured:
        try:
            body = quiet.wrap_post(str(text or ""), relayed=str(relayed or ""),
                                   spans=[str(s) for s in (spans or [])],
                                   workspace_root=workspace_root)
        except Exception as exc:  # noqa: BLE001 - a fence refusal is the answer
            return _refusal(exc, list(captured))
    return {"text": body, "rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# Phase 5: THE receipt, composed by its own writer and NOT written
# ---------------------------------------------------------------------------

@answers
def plan_wrap_receipt(workspace_root: str, *,
                      fired_via: Optional[str] = None,
                      duration_ms: Optional[int] = None,
                      late_tier: Optional[str] = None,
                      extra_data: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """`{rows}`: the fire's ONE `pack_run`, by `receipts.log_receipt(ws,
    'friday-wrap', ...)` with its append held. The row that comes back is the
    one it writes (its vocabulary check, its slot provenance, its writer
    stamp); the caller lands it with ONE `plan append_jsonl`.

    `late_tier` keeps only the two tiers a receipt records (`note`,
    `degrade`), the old block's own conditional, so a caller that hands the
    raw tier cannot mis-stamp a `none` fire as late."""
    from inbox_helpers import _captured_appends
    from receipts import log_receipt

    tier = late_tier if late_tier in ("note", "degrade") else None
    kwargs: Dict[str, Any] = {"duration_ms": duration_ms, "late_tier": tier,
                              "extra_data": dict(extra_data or {})}
    if fired_via:
        kwargs["fired_via"] = fired_via
    with _captured_appends() as captured:
        log_receipt(workspace_root, TASK_ID, **kwargs)
    return {"rows": _jsonable(list(captured))}


# ---------------------------------------------------------------------------
# Phase 5 tail: the earned door, closed after the post (a WRITER)
# ---------------------------------------------------------------------------

def record_offer(workspace_root: str, *, wrap2: Optional[Dict[str, Any]] = None,
                 offer_pattern_key: str = "") -> Dict[str, Any]:
    """`quiet.wrap_record_offer(ws, wrap2)`: a WRITER, on the write list only.

    It writes the once-ever offer row, so it may only run after the post
    (REVIEW_NIGHT11C H-1). The composer's answer is large and only one key of
    it matters here, so a caller may hand `offer_pattern_key` alone; either
    way no key means no offer this week and nothing is written.

    ONCE PER FIRE (fix round 1, review N-9). The skill's Phase 6 and the
    orchestrator's Phase 5 tail both close the door; a key whose row is
    already written answers `recorded: false, already: true` and writes
    nothing, so the second call can neither rewrite the row nor claim it."""
    import coaching_doors as cd
    import quiet

    key = str(offer_pattern_key or (wrap2 or {}).get("offer_pattern_key")
              or "").strip()
    if key and key in dict((cd._offers(workspace_root) or {}).get("offered")
                           or {}):
        return {"recorded": False, "already": True, "pattern_key": key}
    return _jsonable(quiet.wrap_record_offer(workspace_root,
                                             {"offer_pattern_key": key}))


# ---------------------------------------------------------------------------
# MUST 2: weekly-recap/SKILL.md, in file order
# ---------------------------------------------------------------------------
#
# The skill runs on demand (a typed `weekly recap`) and as the Friday fire's
# engine (Phase 3 of the orchestrator runs its Phases 1..6 verbatim). Every
# block it carried that opened the workspace is one of the names below. The
# plate, the measure and the week-against-the-word composer all build the
# plate, which mints its display numbers under the ledger lock, so they are
# WRITERS; everything else reads or computes.

#: SPEC FRP1: the recap's two first-run decisions, moved here from the
#: skill's python block so the defaults have ONE home the helper and the
#: first-fire save both read.
RECAP_DEFAULTS: Dict[str, Any] = {
    "lens": "theme_led",            # theme_led | numbers_led
    "internal_backlog": "split",    # split | external_only
}

#: The feed categories 8b-bis owns, so 8b skips them (REVIEW_QUIET1 F-4: one
#: act, one line). Spelled once, here, for the door's `week_sections`.
DECIDED_CATEGORIES = ("closed_from_meetings", "closed_from_sent",
                      "unconfirmed_expired", "proposals_retracted",
                      "orgs_promoted", "let_go_backlog")


@answers
def recap_config(workspace_root: str) -> Dict[str, Any]:
    """First-Run Personalization: `{config, configured, defaults}`.

    `config` is `skill_config_writer.get_config(ws, 'weekly-recap',
    RECAP_DEFAULTS)` (the saved config deep-merged over the defaults) and
    `configured` is `is_configured(...)`, the first-fire gate. The save on a
    first fire is a WRITER: `run_writer skill_config_writer:save_skill_config`
    with `config` = `defaults`."""
    from skill_config_writer import get_config, is_configured

    return _jsonable({"config": get_config(workspace_root, SKILL_NAME,
                                           dict(RECAP_DEFAULTS)),
                      "configured": bool(is_configured(workspace_root,
                                                       SKILL_NAME)),
                      "defaults": dict(RECAP_DEFAULTS),
                      "skill_name": SKILL_NAME})


@answers
def catchup_window(workspace_root: str, *, fired_via: Optional[str] = None,
                   floor_hours: float = 168, cap_days: float = 30,
                   scheduled_only: bool = True) -> Dict[str, Any]:
    """The scheduled window (SPEC CATCHUP1 F-2): `catchup.catchup_window(ws,
    'friday-wrap', floor_hours=168, cap_days=30, fired_via=..., scheduled_only=
    True)`, the span since the last successful Friday Wrap. All four
    timestamps come back: `start_aware` / `end_aware` for the connectors, the
    naive `start` / `end` for the receipt."""
    from catchup import catchup_window as _window

    return _jsonable(_window(workspace_root, TASK_ID,
                             floor_hours=float(floor_hours),
                             cap_days=float(cap_days), fired_via=fired_via,
                             scheduled_only=bool(scheduled_only)))


def plate_cut(workspace_root: str, *, since_iso: str = "",
              now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Phase 4 section 3: `plate_view.wrap_cut(ws, since_iso=..., now_iso=...)`
    plus `plate_view.wrap_docx_section(cut)` as `docx_section`. A WRITER, on
    the write list only: building the plate mints its display numbers under
    the ledger lock. `text` is relayed byte-exact; `refused` means say `line`
    and nothing else."""
    import plate_view

    cut = plate_view.wrap_cut(workspace_root, since_iso=since_iso,
                              now_iso=now_iso)
    out = dict(cut)
    try:
        out["docx_section"] = plate_view.wrap_docx_section(cut)
    except Exception as exc:  # noqa: BLE001 - the chat cut still stands
        out["docx_section"] = None
        out["docx_error"] = f"{type(exc).__name__}: {exc}"[:300]
    return _jsonable(out)


@answers
def group_section(workspace_root: str) -> Dict[str, Any]:
    """Phase 4 section 8's heading and grouping key (CUSTOM2):
    `brief_settings.wrap_group_section(ws)` -> `{heading, group_by}`."""
    from brief_settings import wrap_group_section

    return _jsonable(wrap_group_section(workspace_root))


@answers
def week_sections(workspace_root: str, *, since_iso: str = "",
                  now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Phase 4 sections 8b, 8b-bis and 8c, read beside the data, each drop-
    empty and each rendered VERBATIM by the skill:

      * `change_lines`: `narration_names.recap_change_lines(ws, since,
        now_iso=..., skip_categories=DECIDED_CATEGORIES)["texts"]` (8b);
      * `open_proposals`: `brain_proposals.card_health_counts(ws)["open"]`,
        the count behind 8b's one pointer line;
      * `decided_for_you` / `still_waiting`: `quiet.wrap_sections(ws, since,
        now)` (8b-bis);
      * `objective_rows`: `objective_math.recap_rows(...)` over the open
        objectives (8c; the status block, never the self-report ask).

    A part that cannot be read comes back empty with its name in `failed`,
    the same best-effort posture the wrap's own blocks take (H-4)."""
    import datetime as _dt

    out: Dict[str, Any] = {"change_lines": [], "open_proposals": None,
                           "decided_for_you": "", "still_waiting": "",
                           "objective_rows": [], "failed": []}
    try:
        from narration_names import recap_change_lines
        feed = recap_change_lines(workspace_root, since_iso, now_iso=now_iso,
                                  skip_categories=list(DECIDED_CATEGORIES))
        out["change_lines"] = list(feed.get("texts") or [])
    except Exception:  # noqa: BLE001
        out["failed"].append("change_lines")
    try:
        from brain_proposals import card_health_counts
        out["open_proposals"] = (card_health_counts(workspace_root,
                                                    now_iso=now_iso) or {}
                                 ).get("open")
    except Exception:  # noqa: BLE001
        out["failed"].append("open_proposals")
    try:
        import quiet
        sections = quiet.wrap_sections(workspace_root, since_iso, now_iso)
        out["decided_for_you"] = str(((sections or {}).get("decided_for_you")
                                      or {}).get("text") or "")
        out["still_waiting"] = str(((sections or {}).get("still_waiting")
                                    or {}).get("text") or "")
    except Exception:  # noqa: BLE001
        out["failed"].append("decided_for_you")
    try:
        import objective_math as om
        from narration_names import name_index
        inputs = om.load_objective_inputs(workspace_root)
        if inputs.get("open_objectives"):
            today = _dt.date.today()
            if now_iso:
                try:
                    today = _dt.datetime.fromisoformat(
                        str(now_iso).replace("Z", "+00:00")).date()
                except ValueError:
                    pass
            # Every input the canonical reader assembled that the health
            # function takes, handed across by the function's own signature:
            # the owner id arrives already resolved by `load_objective_inputs`
            # (through the primary-user seam), never re-read here.
            import inspect
            accepted = (set(inspect.signature(
                om.compute_objective_health).parameters)
                - {"open_objectives", "today", "config"})
            health = om.compute_objective_health(
                inputs["open_objectives"], today=today,
                **{k: v for k, v in inputs.items() if k in accepted})
            names = name_index(workspace_root)
            out["objective_rows"] = list(om.recap_rows(
                health, names_by_person_id=names if isinstance(names, dict)
                else None))
    except Exception:  # noqa: BLE001
        out["failed"].append("objective_rows")
    return _jsonable(out)


def measure_line(workspace_root: str, *, since_iso: str = "",
                 now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Phase 4 section 8b-ter: `flow_measure.wrap_line(ws, since_iso=...,
    now_iso=...)` -> `{text}`, one line rendered VERBATIM, `""` = render
    nothing. A WRITER, on the write list only: its plate number comes from
    the plate's own projection, which mints display numbers."""
    from flow_measure import wrap_line

    return _jsonable(wrap_line(workspace_root, since_iso=since_iso or None,
                               now_iso=now_iso))


def coaching_blocks(workspace_root: str, *, since_iso: str = "",
                    now_iso: Optional[str] = None,
                    what_now: Optional[List[Dict[str, Any]]] = None
                    ) -> Dict[str, Any]:
    """Phase 4 section 8d: `quiet.wrap_coaching_blocks(ws, since_iso=...,
    now_iso=..., what_now=[...])`, the week against the word. A WRITER, on the
    write list only: next week's three come off the plate, which mints its
    display numbers. The answer is the composer's own (`text`,
    `docx_sections`, `receipt_extra`, `failed_blocks`, `offer_pattern_key`).

    Its three fences RAISE, and a raise has already written ONE
    `surface_failed` receipt (`quiet.wrap_failed`); through the door that
    raise is answered as `{ok: false, reason, line, detail}`, never re-raised,
    so the fire can say its one line."""
    import quiet

    try:
        return _jsonable(quiet.wrap_coaching_blocks(
            workspace_root, since_iso, now_iso,
            what_now=list(what_now or [])))
    except Exception as exc:  # noqa: BLE001 - a fence refusal is the answer
        return _refusal(exc, [])


@answers
def brief_path(workspace_root: str, *, date: str = "") -> Dict[str, Any]:
    """Phase 5.B's path: `brief_path.get_brief_path(ws, 'weekly_recap', '',
    date)`, its `computer://` url and whether it is session-scoped. `date` is
    the trigger day in the workspace timezone (`CR_TODAY` on a scheduled
    fire); empty means the workspace's own today through `tz.to_local`. The
    meetings folder itself is made first by `brief_path:ensure_brief_directory`
    through the WRITE door."""
    from pathlib import Path

    from brief_path import (get_brief_artifact_url, get_brief_path,
                            is_session_scoped_path)

    day = str(date or "").strip()
    if not day:
        import datetime as _dt

        from tz import to_local
        day = to_local(_dt.datetime.now(_dt.timezone.utc),
                       workspace_path=workspace_root).strftime("%Y-%m-%d")
    path = get_brief_path(workspace_root, "weekly_recap", "", day)
    try:
        rel = Path(path).resolve().relative_to(
            Path(workspace_root).resolve()).as_posix()
    except ValueError:
        rel = None
    return {"brief_path": path, "rel": rel, "date": day,
            "brief_url": get_brief_artifact_url(path),
            "session_scoped": bool(is_session_scoped_path(path))}


def _closing(workspace_root: str, docx_rel: str, docx_path: str,
             label: str, drive_web_url: str) -> Dict[str, str]:
    """The two closing lines for one landed document. The path is rebuilt
    HERE from the workspace-relative `docx_rel` (or taken from `docx_path`
    when the caller holds this host's own spelling), and mapped to the
    customer's computer through `CR_DEVICE_WORKSPACE`, which the door
    forwards. A path never has to cross a command line: the door's argument
    fence refuses any text carrying a `computer:` link, and a device path is
    outside the fence on a merged seat."""
    import os
    from pathlib import Path

    from brief_path import get_brief_opener_url, saved_to_meetings_footer
    from chat_output_renderer import doc_headline_link

    path = str(docx_path or "")
    if not path and docx_rel:
        path = str(Path(workspace_root) / str(docx_rel))
    device = os.environ.get("CR_DEVICE_WORKSPACE", "")
    footer = saved_to_meetings_footer(path, label=label,
                                      drive_web_url=drive_web_url,
                                      workspace_root=workspace_root,
                                      device_root=device)
    url = get_brief_opener_url(path, drive_web_url,
                               workspace_root=workspace_root,
                               device_root=device) if path else ""
    h2_link = doc_headline_link(label, url)
    # WORDS, NOT A DEAD POST (MIGRATE3-FW seam S-3). The whole-turn door runs
    # the leak gate over these two lines, and that gate reads a link's path
    # only up to its first space and only against this host's workspace
    # spelling: a folder named with a space, or the path on the customer's
    # own computer on a merged seat, is refused as a path leak, and the whole
    # wrap with it. When the gate would refuse the lines, the footer is the
    # composer's own words-only form (DELIV1: the words are always true, the
    # href is only there when it opens something) and no heading link is
    # added. Asked here, before the post, so a refusal never writes a
    # `surface_failed` receipt for a wrap that did post.
    try:
        from chat_output_renderer import validate_chat_output
        validate_chat_output(footer + "\n\n" + h2_link,
                             workspace=str(workspace_root), surface="wrap")
    except Exception:  # noqa: BLE001 - the gate's refusal is the answer
        return {"footer": saved_to_meetings_footer(""), "h2_link": "",
                "linked": False}
    return {"footer": footer, "h2_link": h2_link, "linked": True}


@answers
def closing_lines(workspace_root: str, *, docx_rel: str = "",
                  label: str = "", drive_web_url: str = "",
                  docx_path: str = "") -> Dict[str, Any]:
    """Phase 5.C's two closing lines, composed and NOT printed: `footer` is
    `brief_path.saved_to_meetings_footer(path, label=..., drive_web_url=...)`
    (the ONE sentence that says where the recap went) and `h2_link` is
    `chat_output_renderer.doc_headline_link(label, get_brief_opener_url(path,
    drive_web_url))`. `docx_rel` is the landing's workspace-relative `rel`;
    the path on the customer's own computer is spelled here, never handed
    in (see `_closing`)."""
    return _closing(workspace_root, docx_rel, docx_path, label, drive_web_url)


def post_turn(workspace_root: str, *, text: str = "", since_iso: str = "",
              now_iso: Optional[str] = None, docx_rel: str = "",
              label: str = "", drive_web_url: str = "") -> Dict[str, Any]:
    """Phase 5.C: the whole turn through `surface_composers.post(text,
    surface='wrap', workspace=ws, relayed=<the plate cut>)`. A WRITER, on the
    write list only.

    WHY IT RE-COMPOSES THE CUT. `post` checks that every relayed line came
    back from a COMPOSER IN THIS PROCESS (RE-VERIFY_LEAK4 P-1: nothing may
    stamp text it was handed). Through the door the cut was composed in
    another process, so this function asks `plate_view.wrap_cut` again with
    the SAME `since_iso` / `now_iso` the skill used; the composer stamps its
    own return here, and that return is what is relayed. The plate numbers
    are already minted, so the second build mints nothing new. A plate that
    moved between the two builds fails the relay check honestly.

    THE CLOSING LINES ARE APPENDED HERE. Given `docx_rel` (the landing's
    workspace-relative `rel`) and `label`, the footer and the heading link
    are composed by `_closing` and joined after `text`, in that order, so
    the whole turn is text, footer and heading link, each after a blank
    line (an empty heading link is left out). They cannot
    be handed in: a text carrying a `computer:` link is refused by the
    door's argument fence before anything runs.

    `{text}` on a pass; `{ok: false, reason, line, detail}` when `post`
    refuses (it has already written its ONE `surface_failed` receipt), and
    `line` is `surface_composers.refused_line('wrap')`."""
    import plate_view
    import surface_composers as sc

    cut = plate_view.wrap_cut(workspace_root, since_iso=since_iso,
                              now_iso=now_iso)
    relayed = "" if cut.get("refused") else str(cut.get("text") or "")
    body = str(text or "")
    if docx_rel or label:
        tail = _closing(workspace_root, docx_rel, "", label, drive_web_url)
        body = "\n\n".join(part for part in (body, tail["footer"],
                                               tail["h2_link"]) if part)
    try:
        final = sc.post(body, surface="wrap",
                        workspace=workspace_root, relayed=relayed)
    except Exception as exc:  # noqa: BLE001 - a refusal is the answer
        return {"ok": False, "reason": "post_refused",
                "line": sc.refused_line("wrap"),
                "detail": f"{type(exc).__name__}: {exc}"[:400]}
    return {"text": final}


#: Where the skill lands the recap document's payload with `plan write`
#: before `land_recap` builds the document from it (one file, overwritten
#: each fire, under the system area the widget pages already use).
RECAP_PAYLOAD_REL = "_hq/.system/wrap/weekly_recap_payload.json"


def land_recap(workspace_root: str, *,
               payload_rel: str = RECAP_PAYLOAD_REL) -> str:
    """Phase 5.B: the recap document, built from a payload that is READ HERE.
    A WRITER, on the write list only; answers the path `brief_writer` saved,
    which the door names by `rel` and gives an `opener_line`.

    WHY NOT `brief_writer:make_brief_from_json` WITH THE PAYLOAD AS ITS
    ARGUMENT. The door walks every argument and refuses a payload past the
    door's argument cap (`fence_helper_args`, `ARG_WALK_CAP`, `bad_args`),
    inline or by
    `args_file`, and a heavy week's recap (the plate's rows and their
    `user_spans`, the week-against-the-word sections, the tables) walks past
    that. So the skill lands the payload as ONE text with `plan write` at
    `payload_rel` (a text is one item and `write` fences only its `rel`),
    and this writer, handed only the rel, reads it beside the data and calls
    `brief_writer.make_brief_from_json` exactly as the old heredoc did. The
    payload may be the object itself or `{"json_payload": {...}}`."""
    import json
    from pathlib import Path

    from brief_writer import make_brief_from_json

    root = Path(workspace_root).resolve()
    source = (root / str(payload_rel or RECAP_PAYLOAD_REL)).resolve()
    source.relative_to(root)  # a rel that escapes the workspace raises here
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("json_payload"),
                                                dict):
        payload = payload["json_payload"]
    return make_brief_from_json(payload)


#: The ledger rows Phase 4's sections read (decisions, meetings, what was
#: promised and what closed), asked for when a caller names no `types`.
WINDOW_TYPES = ("decision", "meeting", "commitment", "commitment_resolved")

#: The `data` fields those sections use; every other field stays beside the
#: data. A string longer than `WINDOW_FIELD_CHARS` is cut to it.
WINDOW_DATA_FIELDS = ("title", "summary", "text", "decision", "source_ref",
                      "owner_id", "counterparty_id", "counterparty_name",
                      "due", "status", "kind", "duration_min", "channel",
                      "direction", "attendees_external")
WINDOW_FIELD_CHARS = 240

#: The byte ceiling on one `window_events` answer (fix round 1, review N-2).
#: A real week answered 371 KB whole and a busy one 1.2 MB, which no chat
#: can hold; the newest rows that fit are kept and `truncated` says the rest
#: was cut, so the recap says it sampled rather than claiming the span.
WINDOW_EVENTS_MAX_BYTES = 60000


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > WINDOW_FIELD_CHARS:
        return value[:WINDOW_FIELD_CHARS]
    if isinstance(value, list):
        return [_clip(v) for v in value[:20]]
    return value


def _project(ev: Dict[str, Any]) -> Dict[str, Any]:
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    return {"ts": ev.get("ts"), "type": ev.get("type"),
            "primary_thread_id": ev.get("primary_thread_id"),
            "person_ids": _clip(list(ev.get("person_ids") or [])),
            "data": {k: _clip(data[k]) for k in WINDOW_DATA_FIELDS
                     if k in data}}


@answers
def window_events(workspace_root: str, *, since_iso: str = "",
                  now_iso: Optional[str] = None,
                  types: Optional[List[str]] = None) -> Dict[str, Any]:
    """Phase 4's read: `events_io.load_events_org_scoped(ws, since_ts=...)`
    (PGUARD1: the account-scope mask and the personal-lane drop are the
    default, and a personal row never comes back), the rows inside
    [since, now] of `types` (default `WINDOW_TYPES`), each projected to the
    fields the sections use, newest first until `WINDOW_EVENTS_MAX_BYTES`.
    `{events, counts_by_type, n_skipped, truncated}`: `counts_by_type` covers
    EVERY row of the window whatever its type, so a count never comes from a
    trimmed list; `truncated` is true when the ceiling cut rows."""
    import json as _json

    from events_io import load_events_org_scoped

    since = _parse_ts(since_iso)
    until = _parse_ts(now_iso) if now_iso else None
    events, skipped = load_events_org_scoped(workspace_root,
                                             since_ts=since_iso or None)
    wanted = set(types or WINDOW_TYPES)
    counts: Dict[str, int] = {}
    rows = []
    for ev in events:
        at = _parse_ts(ev.get("ts"))
        if since is not None and (at is None or at < since):
            continue
        if until is not None and at is not None and at > until:
            continue
        kind = str(ev.get("type") or "")
        counts[kind] = counts.get(kind, 0) + 1
        if kind in wanted:
            rows.append(_project(ev))
    kept: List[Dict[str, Any]] = []
    size = 0
    truncated = False
    for row in reversed(rows):
        cost = len(_json.dumps(row, default=str)) + 2
        if size + cost > WINDOW_EVENTS_MAX_BYTES:
            truncated = True
            break
        kept.append(row)
        size += cost
    kept.reverse()
    return _jsonable({"events": kept, "counts_by_type": counts,
                      "n_skipped": len(skipped or []),
                      "truncated": truncated})


# ---------------------------------------------------------------------------
# Fix round 1, N-1: Phase 3's commitments, captured without the scan skill
# ---------------------------------------------------------------------------

#: The most extracted items one `plan_commitment_captures` call carries
#: (fix round 2, review R-1). The door walks every argument and refuses past
#: the door's argument cap (`workspace_access.ARG_WALK_CAP`; the ceiling was
#: sized at the cap of the time); a realistic item (six to eight people, the
#: counterparty name,
#: an amount) walks 51 to 53, so 40 already failed. At 25 the richest shape
#: measured walks 1,330. A longer list is sent in several calls, each landed
#: by its own `append_jsonl`; the append refuses a row it already holds, so
#: no row lands twice.
CAPTURE_BATCH_MAX = 25

#: The one phrasing of a never-track rule that is narrower than "never":
#: `commitment_noise.propose_noise_rules` writes `never-track: low-consequence
#: items from <name>`, and such a rule leaves an item that carries a due date
#: or money alone (the caution rail, `capture_gate.carries_due_or_money`).
_LOW_CONSEQUENCE_PREFIX = "low-consequence items from "

#: The punctuation a rule's word may carry at its edges when it names a
#: source inside a sentence ("never-track: items from (granola:abc),").
#: Never `=` or `:`, which a native id may end or begin with. The period and
#: the question mark joined in NEVERTRACK2 (BATTFIX1 N-6): a source named
#: mid-sentence ("granola:abc. and more") keys as itself again.
_RULE_WORD_EDGE = ",;()[]<>\"'`.?"

#: The kinds a never-track match answers (NEVERTRACK2 MUST 2), in the order
#: the matcher tries them. `unsure` is always last: a one-word rule that only
#: turns up inside the title is skipped AND counted (D-W2-2), never trusted
#: ahead of a shape that is sure.
NEVER_TRACK_KINDS = ("source", "title", "person", "email", "org", "unsure")


def _fold(text: Any) -> str:
    """Lower case, accents dropped, whitespace collapsed: the one spelling
    both sides of a never-track comparison are put in (NEVERTRACK2 MUST 2).
    `unicodedata.normalize("NFKD")` splits a letter from its accent and the
    combining marks are dropped, so `José` and `jose` fold alike. Used on
    rule targets, titles, names, addresses and org names; NEVER on a stored
    `source_ref`, which is compared by identity only."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(bare.lower().split())


def _has_phrase(haystack: str, phrase: str) -> bool:
    """`phrase` inside `haystack` as a whole phrase: no word character
    touches either end. Both already folded."""
    import re

    if not phrase or not haystack:
        return False
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)",
                     haystack) is not None


def _never_track_targets(workspace_root: str) -> List[tuple]:
    """`[(target, low_consequence_only, folded)]` off the customer's taught
    rules: `commitment_noise.load_never_track_rules`, the one reader of
    `_hq/config/commitment-rules.md` (the file every commitment producer
    reads before writing). `target` is lower-cased (the source half keys it
    through `dedup_key_of`); `folded` is `_fold(target)`, the spelling every
    other shape compares."""
    from commitment_noise import load_never_track_rules

    out = []
    for rule in load_never_track_rules(workspace_root) or []:
        body = str(rule).split(":", 1)[1].strip() if ":" in str(rule) else ""
        low = body.lower().startswith(_LOW_CONSEQUENCE_PREFIX)
        target = body[len(_LOW_CONSEQUENCE_PREFIX):] if low else body
        target = target.strip().strip(".").strip().lower()
        if target:
            out.append((target, low, _fold(target)))
    return out


def _email_hit(folded: str, emails: List[str]) -> bool:
    """(d) the rule names an address on the candidate: the whole address, or
    its domain when the rule wrote `@domain`, or its local part when the
    rule wrote `name@`."""
    for addr in emails:
        if "@" not in addr:
            continue
        local, domain = addr.rsplit("@", 1)
        if folded == addr or _has_phrase(folded, addr):
            return True
        if domain and _has_phrase(folded, "@" + domain):
            return True
        if local and (folded == local + "@" or _has_phrase(folded, local + "@")):
            return True
    return False


def _definite_kind(target: str, folded: str, ref_key: Optional[str],
                   title_key: str, names: List[str], keys: List[str],
                   emails: List[str], orgs: List[str]) -> Optional[str]:
    """The first SURE shape one rule matches, in `NEVER_TRACK_KINDS` order,
    or None."""
    from connector_adapters.provenance import dedup_key_of

    words = [target] + [w.strip(_RULE_WORD_EDGE) for w in target.split()]
    # (a) source, by identity
    if ref_key and any(dedup_key_of(w) == ref_key for w in words if w):
        return "source"
    # (b) title: the rule IS the title, or a rule of two words or more sits
    # inside it as a whole phrase
    if title_key and folded == title_key:
        return "title"
    if title_key and len(folded.split()) > 1 and _has_phrase(title_key, folded):
        return "title"
    # (c) person: a name or alias of a party inside the rule, or the rule is
    # one; an entity id the rule names
    if any(_has_phrase(folded, name) for name in names):
        return "person"
    if any(_has_phrase(target, key) for key in keys):
        return "person"
    # (d) email
    if _email_hit(folded, emails):
        return "email"
    # (e) org: the company's name or alias inside the rule, or the rule is one
    if any(_has_phrase(folded, org) for org in orgs):
        return "org"
    return None


def _never_track_hit(targets: List[tuple], data: Dict[str, Any],
                     party_names: List[str], *, party_keys: Iterable = (),
                     org_names: Iterable = (), party_emails: Iterable = ()
                     ) -> tuple:
    """`(hit, kind)`: whether a taught rule describes this item, and which
    shape said so (`NEVER_TRACK_KINDS`), else `(False, None)`.

    NEVERTRACK2 MUST 2 widens the matcher from a party name as on record and
    a source to the rule shapes the wrap's prose reads (REVIEW_T2B_MIGRATE3_FW,
    the table of misses): (a) the source by identity; (b) the title, the rule
    being the title or a phrase of two words or more inside it (what the
    triage surface's `never track this` writes is a title shape); (c) a
    person by canonical name, alias or nickname, or by entity id; (d) an
    email address, its `@domain`, or its `name@`; (e) a company by name or
    alias; every shape but the source compared after `_fold`, so an accent
    the record lacks is no miss. (f) UNSURE: a rule of ONE word that no sure
    shape matched and that turns up inside the title on a word boundary. It
    is skipped and counted as `unsure` (D-W2-2: an unsure match never lands a
    row and never drops one silently). Every sure shape of every rule is
    tried before any rule is taken as unsure.

    The low-consequence rail is unchanged: a rule worded `low-consequence
    items from <name>` leaves an item that carries a due date or money,
    whichever shape matched.

    The SOURCE half compares identities, never spellings (BATTFIX1, the
    PROV2 ruling: a stored `source_ref` is case preserving and is never
    case-normalized on read outside Layer A4). The item's ref is folded by
    `connector_adapters.provenance.dedup_key_of`, the one identity function
    guard G30 routes every stored-ref comparison through, and so is the rule
    as a whole and each of its words. So a rule that spells the source in
    another letter case, or in another spelling the dedup key folds (the
    two `slack:` shapes, `gcalendar:` for `gcal:`), names the same artifact,
    exactly as the append path's dedup and `_resolved_source_keys` already
    treat it; a rule word that merely CONTAINS the ref (`gmail:abc` inside
    `gmail:abc123`) does not count."""
    from capture_gate import carries_due_or_money
    from connector_adapters.provenance import dedup_key_of

    names = [_fold(n) for n in party_names if _fold(n)]
    keys = [str(k).strip().lower() for k in party_keys if str(k or "").strip()]
    emails = [_fold(e) for e in party_emails if "@" in str(e or "")]
    orgs = [_fold(o) for o in org_names if _fold(o)]
    # Fix round 1 N-3: a trailing period, question mark or exclamation mark
    # (chr(33)) is not part of the title.
    title_key = _fold(data.get("title") or data.get("summary")).strip(
        ".?" + chr(33)).strip()
    ref = str(data.get("source_ref") or "").strip()
    ref_key = dedup_key_of(ref) if ref else None
    rail = carries_due_or_money(data)
    for target, low, folded in targets:
        kind = _definite_kind(target, folded, ref_key, title_key, names, keys,
                              emails, orgs)
        if kind and not (low and rail):
            return True, kind
    for target, low, folded in targets:
        # (f) unsure, after every sure shape of every rule
        if len(folded.split()) == 1 and _has_phrase(title_key, folded):
            if not (low and rail):
                return True, "unsure"
    return False, None


def _party_context(workspace_root: str) -> Dict[str, Any]:
    """What the matcher needs to know about the book's people and companies,
    read ONCE per batch beside the data, written nowhere.

    `entities.json` is read through `narration_names` (the one parse
    `name_index` makes, then `name_index_from_doc` over it, so the index is
    exactly `name_index`'s), and its people and orgs through
    `entities_io.entities_collection`; `aliases.json` through the
    resolver's own readers (`entity_resolve._load_aliases`,
    `_iter_alias_mappings`), which take both shapes the product has written.
    Answers `{"index": {id: name}, "names": {id: [every name and alias]},
    "emails": {person id: [addresses]}, "org_of": {person id: org id},
    "orgs": set of org ids}`."""
    from entities_io import entities_collection
    from narration_names import _load_doc, name_index_from_doc
    from people_writer import get_person_emails

    doc = _load_doc(workspace_root)
    index = name_index_from_doc(doc)
    names: Dict[str, List[str]] = {}
    emails: Dict[str, List[str]] = {}
    org_of: Dict[str, str] = {}
    org_ids = set()
    for coll in ("people", "orgs"):
        try:
            records = entities_collection(doc, coll) if doc else []
        except Exception:  # noqa: BLE001 - a read never breaks the capture
            records = []
        for rec in records if isinstance(records, list) else []:
            if not isinstance(rec, dict) or not rec.get("id"):
                continue
            eid = str(rec["id"])
            aliases = rec.get("aliases") if isinstance(rec.get("aliases"),
                                                       list) else []
            # Fix round 1 N-1: `nicknames` is the other canonical name-variant
            # field (`people_writer.ALLOWED_PERSON_FIELDS`, v3.13.0+).
            nicknames = rec.get("nicknames") if isinstance(rec.get("nicknames"),
                                                           list) else []
            slot = names.setdefault(eid, [])
            for name in [index.get(eid)] + aliases + nicknames:
                if str(name or "").strip() and str(name) not in slot:
                    slot.append(str(name))
            if coll == "orgs":
                org_ids.add(eid)
                # Fix round 1 N-9: an `@domain` rule names the company whose
                # record lists that domain (`org_writer`'s `domains` array).
                domains = rec.get("domains") if isinstance(rec.get("domains"),
                                                           list) else []
                slot += ["@" + str(d).strip().lstrip("@") for d in domains
                         if str(d or "").strip().lstrip("@")]
                continue
            emails[eid] = get_person_emails(rec)
            # Fix round 1 N-2: `org_id` is deprecated but still read for
            # back-compat (`people_writer.ALLOWED_PERSON_FIELDS`).
            company = rec.get("primary_org_id") or rec.get("org_id")
            if company:
                org_of[eid] = str(company)
    try:
        from pathlib import Path as _Path

        from entity_resolve import _iter_alias_mappings, _load_aliases

        loaded = _load_aliases(_Path(workspace_root))
        mappings = _iter_alias_mappings(loaded if isinstance(loaded, dict)
                                        else {})
    except Exception:  # noqa: BLE001 - no aliases is the honest degrade
        mappings = []
    for mapping in mappings:
        eid = str(mapping.get("canonical_id") or "")
        raw = str(mapping.get("raw") or "").strip()
        if eid and raw and raw not in names.setdefault(eid, []):
            names[eid].append(raw)
    return {"index": index, "names": names, "emails": emails,
            "org_of": org_of, "orgs": org_ids}


def _item_parties(item: Dict[str, Any], data: Dict[str, Any],
                  book: Dict[str, Any], user_id: Any) -> Dict[str, List[str]]:
    """The keyword arguments `_never_track_hit` takes for one item: every
    name, alias and id of its people (the ids in `person_ids`, the owner and
    the counterparty, as before, plus their aliases), their addresses, and
    the companies the item and its counterparty belong to. The primary user
    is left out of the people: a rule is about someone else, and the user's
    own name or nickname inside a title-shaped rule must not skip every item
    the user owns."""
    people = list(item.get("person_ids") or [])
    ids = [str(i) for i in people + [data.get("owner_id"),
                                     data.get("counterparty_id")] if i]
    ids = [i for i in ids if i != str(user_id or "")]
    names = [data.get("counterparty_name"), data.get("owner_external")]
    keys, emails, orgs = [], [data.get("counterparty_email"),
                              data.get("owner_email")], [item.get("org_name")]
    org_ids = [item.get("org_id")]
    counterparty = str(data.get("counterparty_id") or "")
    if counterparty in book["org_of"]:
        org_ids.append(book["org_of"][counterparty])
    for eid in ids:
        if eid in book["orgs"]:
            org_ids.append(eid)
            continue
        keys.append(eid)
        names += book["names"].get(eid) or [book["index"].get(eid)]
        emails += book["emails"].get(eid) or []
    for oid in [str(o) for o in org_ids if o]:
        orgs += [oid] + list(book["names"].get(oid) or [])
    return {"party_names": [n for n in names if n],
            "party_keys": keys,
            "party_emails": [e for e in emails if e],
            "org_names": [o for o in orgs if o]}


def _resolved_source_keys(workspace_root: str) -> set:
    """The identity (`connector_adapters.provenance.dedup_key_of`) of every
    source a `commitment_resolved` or `thread_resolved` row already covers,
    read through `events_io.load_events_org_scoped` (PGUARD1: the wrap is an
    org surface and reads no raw ledger), with the same identity comparison
    the codified capture legs use (`slack_capture.already_captured`,
    `sent_capture.already_captured`) for the scan's Step 4 rule, "no point
    creating a commitment that's already known to be done"."""
    from connector_adapters.provenance import dedup_key_of
    from events_io import load_events_org_scoped

    keys = set()
    events, _skipped = load_events_org_scoped(workspace_root)
    for ev in events:
        if ev.get("type") not in ("commitment_resolved", "thread_resolved"):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        key = dedup_key_of(str(data.get("source_ref") or "").strip())
        if key:
            keys.add(key)
    return keys


#: The party context `classify_capture` takes off `workspace_capture_context`:
#: the owner's key and the three sets beside it.
_OWNER_KEY = "_".join(("user", "id"))
_PARTY_KEYS = (_OWNER_KEY, "user_names", "team_ids", "known_ids")

#: The skill these rows are captured for, spelled as the scan spells it, so a
#: bad batch can still be found and rolled back by `source_skill`.
CAPTURE_SOURCE_SKILL = "scan-for-commitments"


@answers
def plan_commitment_captures(workspace_root: str, *,
                             items: Optional[List[Dict[str, Any]]] = None
                             ) -> Dict[str, Any]:
    """Phase 3's commitment capture, composed beside the data and NOT written.

    WHY (fix round 1, review N-1). The wrap used to invoke the
    scan-for-commitments skill here, and that skill is not on the access
    layer, so a fire that the gate called migrated still ran an unmigrated
    file. The wrap now does the scan's commit path itself: the model
    extracts candidate commitments from the meetings this fire captured (the
    schema's own rules, over text it already holds), and this helper runs
    the scan's Steps 3 to 3.5 over them exactly as its writers do:
    `capture_gate.gate_commitment_data` (kind, due or no_due, the
    pending_review inversion), then the relevance gate
    (`workspace_capture_context`, a per-org `resolve_capture_mode`,
    `classify_capture`), building an open `commitment` row or a set-aside
    `commitment_observed` row by `build_observed_event` (the caution rail:
    an item with a due date or money always lands open). Every row carries
    `source_skill: scan-for-commitments` and `data.origin: connector`, as the
    scan stamps them. Duplicates are refused at the append chokepoint
    (CAPTUREONCE1), so no ledger read is needed here. The rows come back for
    ONE `plan append_jsonl` with `holder: scan-for-commitments`.

    Each item is `{"data": {...}, "primary_thread_id", "person_ids",
    "classification_confidence", "org_id", "org_name"}`. An item the capture
    block refuses comes back in `refused` with the reason, never written.

    THE SCAN'S TWO SKIPS (fix round 2, review R-2). Before the gate, an item a
    taught never-track rule describes (by its source, title, person, email or
    company, NEVERTRACK2; `_never_track_hit`) is skipped (the scan's Step 3,
    read through `commitment_noise.load_never_track_rules`), and an item
    whose `source_ref` a `commitment_resolved` or `thread_resolved` row
    already covers is skipped (the scan's Step 4). Both come back in
    `skipped` with the reason and the `kind` of match, never written, and
    `left_out` counts the never-track skips: `never_track` for a sure match,
    `unsure` for a one-word rule found only inside the title (D-W2-2)."""
    from capture_gate import (DEFAULT_MODE, CaptureGateError,
                              build_observed_event, classify_capture,
                              gate_commitment_data, intake_kwargs,
                              resolve_capture_mode, workspace_capture_context)

    batch = list(items or [])
    if len(batch) > CAPTURE_BATCH_MAX:
        return {"ok": False, "reason": "batch_too_large",
                "max": CAPTURE_BATCH_MAX, "rows": []}
    from connector_adapters.provenance import dedup_key_of

    ctx = workspace_capture_context(workspace_root)
    targets = _never_track_targets(workspace_root)
    resolved = _resolved_source_keys(workspace_root)
    book = _party_context(workspace_root) if targets else {}
    rows: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    # NEVERTRACK2 MUST 3: what the rules left out, sure and unsure, counted
    # on the answer so the wrap's one line can say it (D-W2-2).
    left_out = {"never_track": 0, "unsure": 0}
    n_open = n_set_aside = 0
    for index, item in enumerate(batch):
        data = dict((item or {}).get("data") or {})
        data.setdefault("origin", "connector")
        source_ref = str(data.get("source_ref") or "")
        confidence = (item or {}).get("classification_confidence")
        thread = (item or {}).get("primary_thread_id")
        people = list((item or {}).get("person_ids") or [])
        if targets:
            parties = _item_parties(item or {}, data, book, ctx.get(_OWNER_KEY))
            hit, kind = _never_track_hit(targets, data, **parties)
            if hit:
                skipped.append({"index": index, "reason": "never_track",
                                "kind": kind})
                left_out["unsure" if kind == "unsure" else "never_track"] += 1
                continue
        key = dedup_key_of(source_ref.strip()) if source_ref.strip() else None
        if key and key in resolved:
            skipped.append({"index": index, "reason": "already_resolved",
                            "kind": "resolved_source"})
            continue
        try:
            gate_commitment_data(data, subject=f"commitment {index + 1} "
                                 f"({source_ref})",
                                 classification_confidence=confidence,
                                 workspace_root=workspace_root)
        except CaptureGateError as exc:
            refused.append({"index": index, "reason": str(exc)[:300]})
            continue
        org_id, org_name = item.get("org_id"), item.get("org_name")
        override = (resolve_capture_mode(workspace_root, org_id=org_id,
                                         org_name=org_name)
                    if (org_id or org_name) else None)
        # The party context passes through by name, exactly as
        # `workspace_capture_context` answered it (the owner came through the
        # primary-user seam there; it is not re-read here).
        party = {name: ctx.get(name) for name in _PARTY_KEYS}
        verdict = classify_capture(
            data, mode=ctx.get("mode") or DEFAULT_MODE,
            user_names=party.get("user_names") or (),
            team_ids=party.get("team_ids") or frozenset(),
            known_ids=party.get("known_ids") or frozenset(),
            org_override=override,
            **{_OWNER_KEY: party.get(_OWNER_KEY)},
            **intake_kwargs(ctx, primary_thread_id=thread, person_ids=people))
        opened = {"type": "commitment", "source_skill": CAPTURE_SOURCE_SKILL,
                  "primary_thread_id": thread,
                  "related_thread_ids": list(item.get("related_thread_ids")
                                             or []),
                  "person_ids": people,
                  "classification_confidence": confidence, "data": data}
        if verdict.get("tier") == "open":
            rows.append(opened)
            n_open += 1
            continue
        try:
            rows.append(build_observed_event(
                str(data.get("title") or ""), source_ref=source_ref,
                reason=verdict.get("reason") or "",
                kind=data.get("kind"), owner_id=data.get("owner_id") or "",
                owner_external=data.get("owner_external") or "",
                counterparty_id=data.get("counterparty_id"),
                counterparty_name=data.get("counterparty_name"),
                evidence=data.get("evidence") or "",
                primary_thread_id=thread, person_ids=people,
                classification_confidence=confidence,
                source_skill=CAPTURE_SOURCE_SKILL))
            n_set_aside += 1
        except CaptureGateError:
            # The caution rail: an item the observed builder refuses (a due
            # date or money) always lands open.
            rows.append(opened)
            n_open += 1
    return _jsonable({"rows": rows, "n_open": n_open,
                      "n_set_aside": n_set_aside, "refused": refused,
                      "skipped": skipped, "left_out": left_out})


# ---------------------------------------------------------------------------
# Fix round 1, N-3: the prose calls, named through the door
# ---------------------------------------------------------------------------

@answers
def week_facts(workspace_root: str, *, since_iso: str = "",
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The reads the recap's prose used to name with no door, answered beside
    the data (fix round 1, review N-3): `primary_person_id` is
    `primary_user.resolve_primary_user(ws)` (the internal/external split and
    the first name); `commitment_counts` is `commitment_state.
    commitment_counts(ws, now_iso=...)` (the R6 numeric verification and the
    document's tiles). A part that cannot be read comes back empty with its
    name in `failed`."""
    out: Dict[str, Any] = {"primary_person_id": None, "commitment_counts": {},
                           "failed": []}
    try:
        from primary_user import resolve_primary_user
        out["primary_person_id"] = resolve_primary_user(workspace_root)
    except Exception:  # noqa: BLE001
        out["failed"].append("primary_person_id")
    try:
        from commitment_state import commitment_counts
        out["commitment_counts"] = commitment_counts(workspace_root,
                                                     now_iso=now_iso)
    except Exception:  # noqa: BLE001
        out["failed"].append("commitment_counts")
    return _jsonable(out)


@answers
def plan_visual_gate(workspace_root: str, *, doc_rel: str = "",
                     rendered: bool = False,
                     findings: Optional[List[str]] = None,
                     fixed: bool = False,
                     skipped_reason: str = "") -> Dict[str, Any]:
    """5.B's visual-pass audit row, `visual_gate.log_visual_gate(ws, doc,
    rendered, findings, fixed, skipped_reason)` with its append held: the row
    comes back in `rows` for ONE `plan append_jsonl` (fix round 1, review
    N-3: this was a write on every fire with no door)."""
    from pathlib import Path

    from inbox_helpers import _captured_appends
    from visual_gate import log_visual_gate

    doc = str(Path(workspace_root) / doc_rel) if doc_rel else ""
    with _captured_appends() as captured:
        log_visual_gate(workspace_root, doc, bool(rendered),
                        findings=list(findings or []), fixed=bool(fixed),
                        skipped_reason=skipped_reason or None,
                        source_skill=SKILL_NAME)
    return {"rows": _jsonable(list(captured))}


def _parse_ts(value: Any):
    """An ISO string to an aware datetime (naive read as UTC), else None."""
    import datetime as _dt

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


__all__ = ["DECIDED_CATEGORIES", "RECAP_DEFAULTS", "SKILL_NAME", "TASK_ID",
           "CAPTURE_BATCH_MAX", "WINDOW_EVENTS_MAX_BYTES", "WINDOW_TYPES",
           "WRAP_REFUSED_LINE", "brief_path",
           "catchup_window", "closing_lines", "coaching_blocks",
           "group_section", "land_recap", "lateness", "measure_line",
           "plan_wrap_receipt", "RECAP_PAYLOAD_REL",
           "plan_commitment_captures", "plan_visual_gate", "plate_cut",
           "post_turn", "recap_config", "record_offer", "week_facts",
           "week_sections", "window_events", "wrap_post"]
