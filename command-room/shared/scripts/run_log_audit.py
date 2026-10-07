#!/usr/bin/env python3
"""The run-log audit - what a fire actually did, read off its own run log
(IDENT1 I-8; ruling R-RW2-2 NARROW, 2026-09-22).

WHY THIS EXISTS. Guard G69 polices the INSTRUCTION LAYER: the fenced python
blocks the skills carry, against the census allowance. It never sees a run
log. The re-walk's rule "zero `python3 -c` in the run log" was a scorekeeper's
rule with no tool behind it, and the 2026-09-22 fire's log carried four such
bodies - every one a JSON parse over the run's own `/tmp` files - beside two
heredocs that opened `entities.json` and `events.jsonl` directly. M ruled the
rule NARROW: an improvised body that opens, reads or writes a WORKSPACE file
fails the fire; a body over the run's own scratch is a note, never a fail.
The structural fence is the write door (R-RW2-3), not a grep - this audit is
the scorekeeper's instrument, and it says which of the two a body is.

WHAT IT READS. The expanded run log, as text: the commands the chat ran and
the lines it posted. It answers ONE dict:

  bodies        every improvised python body - `python3 -c`, a `python3 -`
                heredoc, or a `python3 <file>` outside the plugin's own
                scripts - with its line, its kind, its class (`workspace` when
                it names the customer's workspace, `scratch` otherwise) and
                the workspace tokens it names;
  narration     every line the fire-narration family reds (I-4);
  asks          every line the unattended-ask family reds (I-16), when the
                family is present;
  docs          every line the unattended-doc family reds (DOCS1 D-3): a
                fire naming or offering a Claude Doc, a page or a deck it
                made outside the folder - the republished page line is
                exempt by identity;
  widget_line   the line of the first widget call (`show_widget`), or None;
  after_widget  every prose line posted after it;
  surface_line  on a fire with NO widget: the line of the LAST saved-at
                sentence (`Saved to `...``, "saved at `...`", or the "under
                ... in your Command Room folder" form), or None;
  after_surface every prose line after it - the saved-at line is the last
                thing a no-widget fire says (R-RW3-4). On a SILENT fire
                (`silent=True`, CLI `--silent`) the contract is silence:
                EVERY prose line is listed except the maintenance close
                constant (`maintenance_dispatcher.SILENT_CLOSE_LINE`, read by
                import; "" when the tree does not carry it);
  reached       T2 FIRE1 MUST 2 (FIX-8, F-OFF-6): did the run reach its
                workspace? `{"reached", "reason", "why", "request_line",
                "ok_line", "door_lines"}` - NOT reached when the run made
                a folder request and no envelope answered `"ok": true` with
                `"folder_attached": true` after it, or carries no door line
                at all, or was refused for want of the folder and never
                answered ok after (`reached_workspace`). A silent run that
                did not reach it is a FAIL named
                `no_workspace_reached`: on the second computer two
                maintenance fires found no folder, asked for none, closed
                `Done.`, and this audit passed them;
  verdict       {"g69": FAIL|NOTE|PASS, "narration": FAIL|PASS,
                 "after_widget": FAIL|PASS, "after_surface": FAIL|PASS,
                 "asks": FAIL|PASS, "docs": FAIL|PASS,
                 "reached": FAIL|PASS (silent) or NOTE|PASS}.

THE PRODUCT'S OWN CLOSING LINES are never a finding (T2 FIRE1 MUST 2): the
inbox driver's draft line (`inbox_helpers.DRAFT_HINT_LINE`) is exempt from
the ask family by identity, and on a silent run the maintenance close
(`SILENT_CLOSE_LINE`) and a saved-at line are allowed ONLY after the run
reached its workspace; the one stop sentence a fire that reached nothing
says (`maintenance_dispatcher.no_workspace_line()`) is allowed as prose, and
the run still FAILs `reached`.

The door's own calls (`workspace_access.py <verb>`) and the plugin's own
scripts are the product, not an improvisation, and are never listed. The
product's own discover heredoc (`workspace_access.DISCOVER_BLOCK`, pasted into
the device shell exactly as the bootloader says) is listed with class `door`
and never counts toward G69 (R-RW3-5 (i); the 2026-09-23 fire's one G69 hit
was that block). A heredoc whose end tag never appears stops at the next
prompt or tool line instead of eating the rest of the log. A `python3 -c`
whose quoted body runs over several lines is read to its closing quote and
classed by the WHOLE body (R-RW3-5 (ii); the same fire's four scratch bodies
opened on the line after the opener and were missed).

3.10-safe, stdlib only - it runs wherever the log is.

CLI: python3 shared/scripts/run_log_audit.py [--silent] <file>
     (prints one JSON line)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

#: The tokens that say a body reaches into the customer's workspace - the
#: census's own list (`scripts/dev/census_python_blocks.SUBSTRATE_TOKENS`),
#: restated because the census script is a developer tool that never ships;
#: the lane suite pins the two lists equal on every entry that can appear in
#: a run log.
WORKSPACE_TOKENS = ("_hq/", "$WORKSPACE", "${WORKSPACE", "$CR_WORKSPACE",
                    "${CR_WORKSPACE", "<WORKSPACE>", "<WS>", "workspace_root",
                    "events.jsonl", "entities.json", "aliases.json",
                    "deliverables/")

#: The workspace named through a VARIABLE, any case - the census's own
#: `SUBSTRATE_PATTERNS`, restated for the same reason and pinned equal by the
#: lane suite (fix pass 1, the coordinator's census addition).
WORKSPACE_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9_])workspace_root(?![A-Za-z0-9_])"),
    re.compile(r"\$\{?(?:CR_)?WORKSPACE(?:_ROOT)?(?![A-Za-z0-9_])\}?"),
    re.compile(r"(?<![A-Za-z0-9_$])(?:CR_)?WORKSPACE(?:_ROOT)?(?![A-Za-z0-9_])"),
)

KIND_INLINE = "python3 -c"
KIND_HEREDOC = "heredoc"
KIND_FILE = "python3 file"
CLASS_WORKSPACE = "workspace"
CLASS_SCRATCH = "scratch"
#: The product's own discover heredoc - listed, never counted (R-RW3-5 (i)).
CLASS_DOOR = "door"

_INLINE_RE = re.compile(r"\bpython3?\s+-c\b")
#: The quote that opens an inline body (`python3 -c "` or `python3 -c '`).
_INLINE_QUOTE_RE = re.compile(r"\bpython3?\s+-c\s*([\"'])")
_HEREDOC_RE = re.compile(r"\bpython3?\s+-\s*<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
_FILE_RE = re.compile(r"\bpython3?\s+(?:-[A-Za-z]\s+)*(\"[^\"]+\.py\"|'[^']+\.py'|\S+\.py)\b")
_WIDGET_RE = re.compile(r"show_widget\b")
#: A line that is a command, a tool call or machine output rather than prose.
_NOT_PROSE_RE = re.compile(
    r"^\s*(?:[$>#]|\{|\[|\}|\]|python3?\b|cd\b|export\b|echo\b|cat\b|ls\b|"
    r"mcp__|Tool\b|Called\b|Result\b|⏺|●|└|```)")
#: Where an unterminated heredoc or quoted body stops: the next shell prompt
#: or tool-call line (never the end of the file).
_PROMPT_OR_TOOL_RE = re.compile(r"^\s*(?:\$\s|mcp__|⏺|●|Tool\b|Called\b|Result\b)")
#: The saved-at sentence a no-widget fire closes on - the two forms
#: `deliverables` owns (`Saved to `...``, "Saved under ... in your Command Room
#: folder.") and the inbox text surface's closing ("... saved at `...`").
_SAVED_AT_RE = re.compile(
    r"(?i)\bsaved (?:to|at) `[^`]+`|\bsaved under \S.* in your Command Room folder")
#: A folder request the run made (the bootloaders' Step 0 / the maintenance
#: body's F0).
_FOLDER_REQUEST_RE = re.compile(r"device_request_folder_access\b")
#: An access-layer line (the door's own command).
_DOOR_LINE_RE = re.compile(r"workspace_access\.py\b")
#: An access-layer envelope that answered ok ON an attached folder - the one
#: fact `Done.` may stand on. Key order and spacing are the printer's, so
#: both halves are matched on the same line independently.
_OK_RE = re.compile(r'"ok"\s*:\s*true\b')
_ATTACHED_RE = re.compile(r'"folder_attached"\s*:\s*true\b')
#: An envelope refused for want of the folder - the access layer's two
#: reasons (`workspace_access.R_NOT_ATTACHED` / `R_NO_WORKSPACE`).
_NO_FOLDER_RE = re.compile(
    r'"reason"\s*:\s*"(?:folder_not_attached|no_workspace_on_this_host)"')
#: The reason a silent run that never reached its workspace FAILs.
NO_WORKSPACE_REACHED = "no_workspace_reached"


def _normalise_block(text: str) -> List[str]:
    return [ln.strip() for ln in str(text or "").replace("\r\n", "\n").split("\n")
            if ln.strip()]


def _discover_lines() -> List[str]:
    """The product's discover block, normalised - read from the door module
    itself, so the two can never drift. Empty when the door is absent (the
    audit is then narrower, never wrong in the other direction)."""
    try:
        import workspace_access as wa  # type: ignore
        return _normalise_block(getattr(wa, "DISCOVER_BLOCK", ""))
    except Exception:  # noqa: BLE001
        return []


def silent_close_line() -> str:
    """The ONE line a silent maintenance fire may say - the maintenance
    lane's constant, read by import; "" when this tree does not carry it."""
    try:
        import maintenance_dispatcher as md  # type: ignore
        return str(getattr(md, "SILENT_CLOSE_LINE", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def no_workspace_line() -> str:
    """The ONE sentence a maintenance fire that reached no workspace stops
    with (T2 FIRE1 MUST 1), read by import; "" when the tree lacks it."""
    try:
        import maintenance_dispatcher as md  # type: ignore
        return str(md.no_workspace_line() or "")
    except Exception:  # noqa: BLE001
        return ""


def reenabled_line_re():
    """The maintenance fire's one "switched back on" line (T2 FIRE1 MUST 4),
    as a pattern over its form with any chat name in the slot; None when the
    tree does not carry the form."""
    try:
        import maintenance_dispatcher as md  # type: ignore
        form = str(getattr(md, "REENABLED_LINE_FORM", "") or "")
    except Exception:  # noqa: BLE001
        return None
    if "{chat}" not in form:
        return None
    head, tail = form.split("{chat}", 1)
    single = re.escape(head) + r"[A-Z][A-Za-z ]{0,40}" + re.escape(tail)
    # CHATSON1: the same line for several chats, their names joined with
    # commas and one "and" in the slot (`REENABLED_LINES_FORM`).
    many_form = str(getattr(md, "REENABLED_LINES_FORM", "") or "")
    if "{chats}" in many_form:
        mhead, mtail = many_form.split("{chats}", 1)
        many = (re.escape(mhead) + r"[A-Z][A-Za-z ,]{0,200}"
                + re.escape(mtail))
        return re.compile("^(?:" + single + "|" + many + ")$")
    return re.compile("^" + single + "$")


def product_closing_lines() -> List[str]:
    """The product's own composed closing lines that no family may red, by
    identity (never by wording): the inbox driver's draft line. Read by
    import; a tree without one is narrower, never wrong the other way."""
    out = []
    try:
        import inbox_helpers as ih  # type: ignore
        line = str(getattr(ih, "DRAFT_HINT_LINE", "") or "").strip()
        if line:
            out.append(line)
    except Exception:  # noqa: BLE001
        pass
    return out


def reached_workspace(lines: List[str], door_body_lines=()) -> Dict[str, Any]:
    """Did the run reach its workspace (T2 FIRE1 MUST 2)?

    NOT reached - the `no_workspace_reached` FAIL on a silent run - when ANY
    of three things the log shows is true:
      * it made a folder request and no access-layer envelope answered ok on
        an attached folder AFTER it (the last request counts);
      * it carries no door line at all (no `workspace_access.py` line and no
        pasted discover block): the walk's no-folder fire, which asked
        get_device_info, found nothing and closed `Done.`;
      * an envelope refused for want of the folder (`folder_not_attached` /
        `no_workspace_on_this_host`) and no ok on an attached folder came
        after it;
      * (T3 FIRE3) it carries door lines but no envelope anywhere answered
        ok on an attached folder (`no_ok_envelope`).
    `door_body_lines` are the 0-based lines of the pasted discover block
    (class `door`), which count as door lines."""
    requests = [n for n, ln in enumerate(lines) if _FOLDER_REQUEST_RE.search(ln)]

    def _top_level_ok(ln: str) -> bool:
        # LOWS2 row 13 (FIRE3 N-5): a candidate line is read as JSON and its
        # TOP-LEVEL `ok` and `folder_attached` decide, so a refused envelope
        # carrying a nested `"ok": true` is not a reached workspace. A line
        # that does not parse keeps the pattern's answer, as before.
        text = ln.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return True
        try:
            obj = json.loads(text[start:end + 1])
        except ValueError:
            return True
        return (isinstance(obj, dict) and obj.get("ok") is True
                and obj.get("folder_attached") is True)

    oks = [n for n, ln in enumerate(lines)
           if _OK_RE.search(ln) and _ATTACHED_RE.search(ln) and _top_level_ok(ln)]
    refusals = [n for n, ln in enumerate(lines) if _NO_FOLDER_RE.search(ln)]
    door = sorted(set(n for n, ln in enumerate(lines) if _DOOR_LINE_RE.search(ln))
                  | set(door_body_lines or ()))
    why = ""
    if requests and not any(n > requests[-1] for n in oks):
        why = "request_without_ok"
    elif not door:
        why = "no_door_line"
    elif refusals and not any(n > refusals[-1] for n in oks):
        why = "refused_no_folder"
    elif not oks:
        # T3 FIRE3 MUST 2 (FIRE1 N-2): door lines but no ok-and-attached
        # envelope ANYWHERE - a bare door line, or `ok: true` with the folder
        # not attached - is not a reached workspace, the same fact
        # `maintenance_dispatcher.workspace_reached` answers.
        why = "no_ok_envelope"
    reached = not why
    after = requests[-1] if requests else -1
    ok_line = next((n + 1 for n in oks if n > after), None)
    return {"reached": reached,
            "reason": "" if reached else NO_WORKSPACE_REACHED,
            "why": why,
            "request_line": (requests[-1] + 1) if requests else None,
            "ok_line": ok_line,
            "door_lines": len(door)}


def _is_discover_heredoc(opener: str, start: int, body: List[str]) -> bool:
    want = _discover_lines()
    if not want:
        return False
    got = _normalise_block("\n".join([opener[start:]] + list(body)))
    return got == want


def _stop_at_prompt(lines: List[str], i: int) -> int:
    """The last line of an unterminated chunk opened at `i`: the line before
    the next prompt/tool line, or the last line of the log."""
    j = i + 1
    while j < len(lines) and not _PROMPT_OR_TOOL_RE.match(lines[j]):
        j += 1
    return j - 1


def _inline_end(lines: List[str], i: int) -> int:
    """The line an inline `-c` body closes on. A body whose closing quote is
    on the opener line ends there; one that runs on is read to its closing
    quote (a double-quoted body honours backslash escapes; a single-quoted
    one honours the shell's close-escape-reopen idiom), and one that never
    closes stops at the next prompt/tool line."""
    opener = _INLINE_QUOTE_RE.search(lines[i])
    if not opener:
        return i
    quote = opener.group(1)
    row, col = i, opener.end()
    while row < len(lines):
        text = lines[row]
        if row > i and _PROMPT_OR_TOOL_RE.match(text):
            return row - 1
        k = col
        while k < len(text):
            ch = text[k]
            if quote == '"' and ch == "\\":
                k += 2
                continue
            if ch == quote:
                if quote == "'" and text[k + 1:k + 4] == "\\''":
                    k += 4
                    continue
                return row
            k += 1
        row, col = row + 1, 0
    return len(lines) - 1


def _opens(text: str) -> List[str]:
    """The workspace tokens a body names (backslash paths normalised)."""
    norm = text.replace("\\", "/")
    found = {tok for tok in WORKSPACE_TOKENS if tok in norm}
    for pattern in WORKSPACE_PATTERNS:
        found.update(m.group(0) for m in pattern.finditer(norm))
    return sorted(found)


def _is_door_or_plugin(cmd: str) -> bool:
    norm = cmd.replace("\\", "/")
    return "workspace_access.py" in norm or "/shared/scripts/" in norm \
        or norm.lstrip("\"' ").startswith("shared/scripts/")


def _family(name: str):
    try:
        import surface_leak_patterns as slp  # type: ignore
    except Exception:  # noqa: BLE001 - an audit without the family is narrower
        return []
    try:
        return list(getattr(slp, name)())
    except Exception:  # noqa: BLE001
        return []


def _hits(line: str, family) -> bool:
    return any(rx.search(line) for rx, _label in family)


def audit(text: str, *, silent: bool = False) -> Dict[str, Any]:
    """The one answer described in the module docstring."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    bodies: List[Dict[str, Any]] = []
    body_lines = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        heredoc = _HEREDOC_RE.search(line)
        if heredoc:
            end_tag = heredoc.group(1)
            j = i + 1
            while j < len(lines) and lines[j].strip() != end_tag:
                j += 1
            if j >= len(lines):
                j = _stop_at_prompt(lines, i)
            chunk = "\n".join(lines[i:j + 1])
            opened = _opens(chunk)
            if _is_discover_heredoc(line, heredoc.start(), lines[i + 1:j + 1]):
                klass = CLASS_DOOR
            else:
                klass = CLASS_WORKSPACE if opened else CLASS_SCRATCH
            bodies.append({"line": i + 1, "kind": KIND_HEREDOC,
                           "class": klass, "opens": opened})
            body_lines.update(range(i, j + 1))
            i = j + 1
            continue
        if _INLINE_RE.search(line):
            j = _inline_end(lines, i)
            opened = _opens("\n".join(lines[i:j + 1]))
            bodies.append({"line": i + 1, "kind": KIND_INLINE,
                           "class": CLASS_WORKSPACE if opened else CLASS_SCRATCH,
                           "opens": opened})
            body_lines.update(range(i, j + 1))
            i = j + 1
            continue
        found = _FILE_RE.search(line)
        if found and not _is_door_or_plugin(found.group(1)):
            opened = _opens(line)
            bodies.append({"line": i + 1, "kind": KIND_FILE,
                           "class": CLASS_WORKSPACE if opened else CLASS_SCRATCH,
                           "opens": opened})
            body_lines.add(i)
        elif found:
            body_lines.add(i)
        i += 1

    narration_family = _family("fire_narration_leak_patterns")
    ask_family = _family("unattended_ask_leak_patterns")
    doc_family = _family("unattended_doc_leak_patterns")
    try:
        import surface_leak_patterns as _slp  # noqa: WPS433

        _doc_exempt = _slp.artifact_line_exempt
    except Exception:  # noqa: BLE001
        def _doc_exempt(_line):  # noqa: ANN001
            return False
    product_lines = set(product_closing_lines())
    narration: List[Dict[str, Any]] = []
    asks: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []
    widget_line: Optional[int] = None
    after: List[Dict[str, Any]] = []
    prose_lines: List[int] = []
    for index, line in enumerate(lines):
        if index in body_lines or not line.strip():
            continue
        if widget_line is None and _WIDGET_RE.search(line):
            widget_line = index + 1
            continue
        prose = not _NOT_PROSE_RE.match(line)
        if prose:
            prose_lines.append(index)
        if prose and narration_family and _hits(line, narration_family):
            narration.append({"line": index + 1, "text": line.strip()[:200]})
        if (prose and ask_family and line.strip() not in product_lines
                and _hits(line, ask_family)):
            asks.append({"line": index + 1, "text": line.strip()[:200]})
        if prose and doc_family and not _doc_exempt(line) and _hits(line, doc_family):
            docs.append({"line": index + 1, "text": line.strip()[:200]})
        if widget_line is not None and prose:
            after.append({"line": index + 1, "text": line.strip()[:200]})

    surface_line: Optional[int] = None
    after_surface: List[Dict[str, Any]] = []
    door_body_lines = set()
    for body in bodies:
        if body["class"] == CLASS_DOOR:
            n = body["line"] - 1
            door_body_lines.add(n)
    reached = reached_workspace(lines, door_body_lines)
    if silent:
        # T2 FIRE1 MUST 2: the close is allowed only after a reached
        # workspace; a run that reached nothing may say only the one stop
        # sentence (and still FAILs `reached`).
        if reached["reached"]:
            allowed = {silent_close_line().strip()}
            saved_ok = True
        else:
            allowed = {no_workspace_line().strip()}
            saved_ok = False
        allowed.discard("")
        # T2 FIRE1 MUST 4: the one "switched back on" line is allowed ONCE,
        # after a reached workspace; a second one is a FAIL line.
        reenabled = reenabled_line_re() if reached["reached"] else None
        said_reenabled = False
        for n in prose_lines:
            text = lines[n].strip()
            if text in allowed or (saved_ok and _SAVED_AT_RE.search(lines[n])):
                continue
            if reenabled is not None and reenabled.match(text) \
                    and not said_reenabled:
                said_reenabled = True
                continue
            after_surface.append({"line": n + 1, "text": text[:200]})
    elif widget_line is None:
        saved = [n for n in prose_lines if _SAVED_AT_RE.search(lines[n])]
        if saved:
            surface_line = saved[-1] + 1
            after_surface = [{"line": n + 1, "text": lines[n].strip()[:200]}
                             for n in prose_lines if n > saved[-1]]

    counted = [b for b in bodies if b["class"] != CLASS_DOOR]
    if any(b["class"] == CLASS_WORKSPACE and b["kind"] in (KIND_INLINE, KIND_HEREDOC, KIND_FILE)
           for b in counted):
        g69 = "FAIL"
    elif counted:
        g69 = "NOTE"
    else:
        g69 = "PASS"
    return {
        "bodies": bodies,
        "narration": narration,
        "asks": asks,
        "docs": docs,
        "widget_line": widget_line,
        "after_widget": after,
        "surface_line": surface_line,
        "after_surface": after_surface,
        "silent": bool(silent),
        "reached": reached,
        "verdict": {"g69": g69,
                    "narration": "FAIL" if narration else "PASS",
                    "after_widget": "FAIL" if after else "PASS",
                    "after_surface": "FAIL" if after_surface else "PASS",
                    "asks": "FAIL" if asks else "PASS",
                    "docs": "FAIL" if docs else "PASS",
                    "reached": ("PASS" if reached["reached"]
                                else ("FAIL" if silent else "NOTE"))},
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    silent = "--silent" in argv
    argv = [a for a in argv if a != "--silent"]
    if not argv:
        sys.stderr.write("usage: run_log_audit.py [--silent] <run log file>\n")
        return 2
    text = Path(argv[0]).read_text(encoding="utf-8", errors="replace")
    sys.stdout.write(json.dumps(audit(text, silent=silent)) + "\n")
    return 0


__all__ = ["CLASS_DOOR", "NO_WORKSPACE_REACHED", "WORKSPACE_TOKENS", "audit",
           "main", "no_workspace_line", "product_closing_lines",
           "reached_workspace", "reenabled_line_re", "silent_close_line"]


if __name__ == "__main__":
    raise SystemExit(main())
