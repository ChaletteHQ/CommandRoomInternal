"""One answer to "is everything OK?" (cr1#1585, parent cr1#1579).

The owner could not tell whether Command Room was working without knowing
where to look: the task watchdog (by phrase only), the integrity check
(flags, read by the weekly cleanup), cleanup's note and the Hub's Maintenance
record each held part of the answer. `answer` reads them and gives ONE
answer in three parts, the same in chat (system-health's "is everything OK?")
and as one line on the Hub (`cockpit_helpers.maintenance_status`'s
`everything_ok`, under Run details):

  1. the tasks: did the scheduled work run? Maintenance's own record, always:
     its slot rule (`stale`, the rule the typed surfaces use), each failing
     job, a Hub Inbox run that built no page and each job its last run left
     unread, each by its plain name, and a brand-new seat whose first run is
     still to come. In chat, also the task watchdog's verdict over the
     scheduler's own list (`staff_meeting_helpers.health_report`, the read the
     health check makes), and the watchdog's own sentence when it cannot see
     the list. Without that list the answer says "Maintenance ran", never
     that every task did: the Hub has no list, and says what it read.
  2. the workspace: is it in shape? The integrity check's owner-facing
     findings (`OWNER_FINDING_CHECKS`), and the tidy piles.
  3. what needs a yes: the open To confirm questions (`feed_review`) and the
     items in the tidy piles (cr1#1584, `hub_tidy.tidy_block` through the
     projects feed), with the place to answer them.

Each part says what it read. A part that could not be read says so and is
never read as nothing to report. Asking writes nothing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: cr1#1585's known unknown, decided reversibly: which integrity findings
#: earn a line to the owner. Every finding of severity ERROR (a broken record
#: the weekly cleanup cannot repair by itself), plus these two warnings, each
#: something the owner can see or lose: a project folder on file that is
#: gone, and record lines that cannot be read. Every other warning stays
#: internal (cleanup's note and the integrity check keep reporting it); a
#: folder-less project or an orphan folder is a tidy pile, not a line here.
OWNER_FINDING_CHECKS = ("C9.thread_folder_missing", "C13.unparseable_events")
#: Never a line here (review round 1): C12 counts historic duplicate record
#: numbers too, which `seq_health` has already marked, so it would never clear.
#: New, unmarked duplicates are `seq_health`'s own finding and surface there.
SKIPPED_CHECKS = ("C12.duplicate_seq",)
#: The plain words for each counted finding: (one, many).
FINDING_WORDS: Dict[str, tuple] = {
    "C9.thread_folder_missing": ("project folder on file cannot be found",
                                 "project folders on file cannot be found"),
    "C13.unparseable_events": ("line in the record log cannot be read",
                               "lines in the record log cannot be read"),
}
ERROR_WORDS = ("problem in the records needs fixing by hand",
               "problems in the records need fixing by hand")

#: Where the answers wait: the tidy piles are in Work's Projects view, the To
#: confirm questions in Work's Commitments.
TIDY_GOTO, TIDY_LABEL = "projects", "Open Tidy"
CONFIRM_GOTO, CONFIRM_LABEL = "plate", "Open To confirm"

ALL_OK_LINE = "Everything is OK: the tasks ran, the workspace is in shape, nothing waits for a yes."
TASKS_UNREAD_LINE = "The scheduled tasks could not be checked here."
REVIEW_UNREAD_LINE = "The To confirm list could not be read here."
TIDY_UNREAD_LINE = "The tidy piles could not be counted here."
MAINT_FIRST_LINE = "Maintenance has not run yet; its first run is still to come."


def tidy_piles(workspace_root, projects: Optional[Dict[str, Any]] = None,
               now_iso: Optional[str] = None) -> Dict[str, Any]:
    """The tidy piles' counts (cr1#1584): `{n, by_pile, errors, left_out}`,
    `n` the items in every pile summed. From the projects feed the caller
    already built (the refresh), else read here through it. Any pile that
    could not be read is in `errors`, and the count is never reported as
    zero for it; a feed with no `tidy` block at all is one error."""
    if projects is None:
        import cockpit_helpers as ch

        projects = ch.feed_projects(workspace_root, now_iso=now_iso)
    tidy = (projects or {}).get("tidy") if isinstance(projects, dict) else None
    if not isinstance(tidy, dict):
        return {"n": 0, "by_pile": {}, "errors": ["tidy"], "left_out": {}}
    by_pile = {str(k): int(v or 0) for k, v in (tidy.get("n") or {}).items()}
    return {"n": sum(by_pile.values()), "by_pile": by_pile,
            "errors": [str(e) for e in tidy.get("errors") or []],
            "left_out": dict(tidy.get("left_out") or {})}


def _plural(n: int, words: tuple) -> str:
    return f"{n} {words[0] if n == 1 else words[1]}"


def _maintenance_findings(maintenance: Dict[str, Any]) -> tuple:
    """(lines, names, info) from Maintenance's own record."""
    from schedule_config import task_display_name

    lines: List[str] = []
    names: List[str] = []
    info: List[str] = []
    never = str(maintenance.get("state") or "") == "idle" and not maintenance.get("finished_at")
    if never and maintenance.get("first_run_pending"):
        info.append(MAINT_FIRST_LINE)
    elif never:
        lines.append("Maintenance has no run on record yet.")
        names.append("Maintenance")
    elif maintenance.get("stale") is True:
        # The slot rule (`maintenance_dispatcher.maintenance_staleness`): its
        # most recent slot went unserved. An older document without the field
        # gives no verdict here, never a guess from the hours.
        lines.append("Maintenance missed its last run.")
        names.append("Maintenance")
    for job in maintenance.get("failing") or []:
        name = str((job or {}).get("name") or (job or {}).get("job_id") or "a job")
        lines.append(f"{name} is failing.")
        names.append(name)
    if maintenance.get("inbox_line"):
        lines.append(str(maintenance["inbox_line"]))
        names.append("Hub Inbox")
    for job in maintenance.get("left_unread") or []:
        name = task_display_name(str(job))
        lines.append(f"{name} did not run last time.")
        names.append(name)
    return lines, names, info


def _tasks_part(workspace_root, maintenance: Dict[str, Any], task_records, backend,
                records_known: Optional[bool], now_iso: Optional[str]) -> Dict[str, Any]:
    lines, names, info = _maintenance_findings(maintenance)
    read = ["Maintenance's record of its runs"]
    listed = False
    # A task still waiting on its first run has not "run": a brand-new seat is
    # OK, and is never told a task ran when it should (#1626 fix check A).
    first_wait = MAINT_FIRST_LINE in info
    if records_known is not None:
        # In chat. With no list, the watchdog still answers, and its sentence
        # that it cannot see the scheduler is said and counts.
        import staff_meeting_helpers as smh
        import task_watchdog as tw

        rep = smh.health_report(str(workspace_root), task_records=task_records if records_known else None,
                                backend=backend, now_iso=now_iso, records_known=bool(records_known))
        if not isinstance(rep, dict) or rep.get("error") or not isinstance(rep.get("reports"), list):
            return {"ok": False, "short": "the tasks could not be checked", "names": ["the scheduled tasks"],
                    "lines": [TASKS_UNREAD_LINE], "read": read + ["the scheduler's own list"], "listed": False}
        read.append("the scheduler's own list")
        listed = bool(records_known) and not rep.get("vantage")
        for line in rep.get("lines") or []:
            lines.append(str(line))
        for r in rep.get("reports") or []:
            later_add = (r.get("status") == "not_registered" and not r.get("first_install")
                         and not r.get("hub"))
            if not later_add and tw.report_is_problem(r):
                names.append(str(r.get("display_name") or r.get("task") or "a task"))
        if (rep.get("vantage") or not records_known) and rep.get("summary_line"):
            lines.append(str(rep["summary_line"]))
            names.append("the scheduled tasks")
        info.extend(str(x) for x in rep.get("info_lines") or [])
        first_wait = first_wait or bool(rep.get("first_run_pending"))
    ok = not lines
    if ok and first_wait:
        if listed:
            said, short = "No scheduled task has missed a run.", "no task has missed a run"
        else:
            said, short = MAINT_FIRST_LINE, "Maintenance is waiting on its first run"
            info = [x for x in info if x != MAINT_FIRST_LINE]
    elif ok:
        said = "Every scheduled task ran when it should." if listed else "Maintenance ran when it should."
        short = "the tasks ran" if listed else "Maintenance ran"
    else:
        said = " ".join(lines)
        short = (", ".join(dict.fromkeys(names)) or "a scheduled task") + " needs a look"
    return {"ok": ok, "short": short, "names": list(dict.fromkeys(names)), "listed": listed,
            "first_wait": first_wait, "lines": [said] + info, "read": read}


def _workspace_part(workspace_root, piles: Dict[str, Any]) -> Dict[str, Any]:
    import integrity_check as ic

    findings = [f.as_dict() if hasattr(f, "as_dict") else dict(f) for f in ic.run_checks(workspace_root)]
    counts: Dict[str, int] = {}
    errors = 0
    for f in findings:
        check = str(f.get("check") or "")
        if check in SKIPPED_CHECKS:
            continue
        if check in OWNER_FINDING_CHECKS:
            counts[check] = counts.get(check, 0) + 1
        elif str(f.get("severity") or "") == ic.ERROR:
            errors += 1
    lines = [_plural(counts[c], FINDING_WORDS[c]).capitalize() + "." for c in OWNER_FINDING_CHECKS if counts.get(c)]
    if errors:
        lines.append(_plural(errors, ERROR_WORDS).capitalize() + ".")
    n = sum(counts.values()) + errors
    if piles.get("errors"):
        lines.append(TIDY_UNREAD_LINE)
    ok = not lines
    pile_line = ("" if piles.get("errors") else
                 "No item waits in the tidy piles." if not piles.get("n") else
                 f"{piles['n']} {'item waits' if piles['n'] == 1 else 'items wait'} in the tidy piles.")
    short = ("the workspace is in shape" if ok else
             "the tidy piles could not be counted" if not n else f"the workspace ({n} to fix)")
    return {"ok": ok, "short": short, "names": [] if ok else ["the workspace"],
            "lines": (lines or ["The workspace is in shape."]) + ([pile_line] if pile_line else []),
            "read": ["the workspace check", "the tidy piles"], "findings": n}


def _yes_part(workspace_root, review: Optional[Dict[str, Any]], piles: Dict[str, Any],
              now_iso: Optional[str]) -> Dict[str, Any]:
    if review is None:
        import cockpit_helpers as ch

        review = ch.feed_review(workspace_root, now_iso=now_iso)
    read = ["the To confirm list", "the tidy piles"]
    unread = not isinstance(review, dict) or "review" in (review.get("errors") or [])
    if unread or piles.get("errors"):
        said = [REVIEW_UNREAD_LINE] if unread else []
        if piles.get("errors"):
            said.append(TIDY_UNREAD_LINE)
        return {"ok": False, "short": "what waits for a yes could not be read", "names": ["what waits for a yes"],
                "lines": said, "read": read, "n": None, "goto": "", "goto_label": ""}
    n = int(review.get("n_open") or 0) + len(review.get("owner_questions") or [])
    n_piles = int(piles.get("n") or 0)
    total = n + n_piles
    if not total:
        line, short, goto, label = "Nothing waits for a yes.", "nothing waits for a yes", "", ""
    else:
        bits = []
        if n:
            bits.append(f"{n} under To confirm")
        if n_piles:
            bits.append(f"{n_piles} in the tidy piles")
        line = f"{total} {'waits' if total == 1 else 'wait'} for a yes: {', '.join(bits)}."
        short = f"{total} {'waits' if total == 1 else 'wait'} for a yes"
        goto, label = (TIDY_GOTO, TIDY_LABEL) if n_piles else (CONFIRM_GOTO, CONFIRM_LABEL)
    return {"ok": True, "short": short, "names": [], "lines": [line], "read": read, "n": total,
            "goto": goto, "goto_label": label}


def answer(workspace_root, *, task_records: Optional[List[Dict[str, Any]]] = None,
           backend: Optional[str] = None, records_known: Optional[bool] = None,
           now_iso: Optional[str] = None, maintenance: Optional[Dict[str, Any]] = None,
           review: Optional[Dict[str, Any]] = None,
           projects: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """READ. The one answer: `{ok, line, short, lines, parts: {tasks,
    workspace, yes}, goto, goto_label}`. `line` is the full sentence and
    `short` its few words (the Hub draws `short` with `line` behind its
    info mark); `lines` are the chat's, one per part, each ending in what it
    read.

    `records_known` says the caller is the chat: true with the scheduler's
    listing in `task_records`, false when the chat found no scheduler (the
    watchdog is still asked, and its sentence counts). Left out (None), as
    the Hub does, only Maintenance's record is read for the tasks.
    `maintenance`, `review` and `projects`, when the caller already built them
    (the Hub's refresh), are used as they are; otherwise they are read here.
    A part that could not be read says so and needs a look; asking never
    raises and writes nothing."""
    try:
        piles = tidy_piles(workspace_root, projects, now_iso)
    except Exception:  # noqa: BLE001 - never zero for a count not made
        piles = {"n": 0, "by_pile": {}, "errors": ["tidy"], "left_out": {}}
    parts: Dict[str, Dict[str, Any]] = {}
    try:
        if maintenance is None:
            import cockpit_helpers as ch

            maintenance = ch.maintenance_status(workspace_root, now_iso=now_iso, with_answer=False)
        parts["tasks"] = _tasks_part(workspace_root, maintenance or {}, task_records, backend,
                                     records_known, now_iso)
    except Exception:  # noqa: BLE001 - an unreadable part is said, never raised
        parts["tasks"] = {"ok": False, "short": "the tasks could not be checked", "names": ["the scheduled tasks"],
                          "lines": [TASKS_UNREAD_LINE], "read": [], "listed": False}
    try:
        parts["workspace"] = _workspace_part(workspace_root, piles)
    except Exception:  # noqa: BLE001
        parts["workspace"] = {"ok": False, "short": "the workspace could not be checked", "names": ["the workspace"],
                              "lines": ["The workspace check could not run here."], "read": []}
    try:
        parts["yes"] = _yes_part(workspace_root, review, piles, now_iso)
    except Exception:  # noqa: BLE001
        parts["yes"] = {"ok": False, "short": "what waits for a yes could not be read",
                        "names": ["what waits for a yes"], "lines": [REVIEW_UNREAD_LINE], "read": [],
                        "n": None, "goto": "", "goto_label": ""}
    ok = parts["tasks"]["ok"] and parts["workspace"]["ok"] and parts["yes"]["ok"]
    waiting = parts["yes"].get("n") or 0
    if ok and not waiting and parts["tasks"].get("listed") and not parts["tasks"].get("first_wait"):
        line = ALL_OK_LINE
    else:
        head = "Everything is OK" if ok else "Needs a look"
        line = f"{head}: {parts['tasks']['short']}, {parts['workspace']['short']}, {parts['yes']['short']}."
    if not ok:
        names = [n for k in ("tasks", "workspace", "yes") for n in parts[k].get("names") or []]
        short = "Needs a look: " + ", ".join(dict.fromkeys(names))
    elif waiting:
        short = f"{waiting} {'waits' if waiting == 1 else 'wait'} for a yes"
    else:
        short = "All OK"
    titles = {"tasks": "Tasks", "workspace": "Workspace", "yes": "Needs a yes"}
    lines = []
    for key in ("tasks", "workspace", "yes"):
        p = parts[key]
        said = " ".join(p["lines"])
        read = f" (Read: {', '.join(p['read'])}.)" if p.get("read") else ""
        lines.append(f"{titles[key]}: {said}{read}")
    return {"ok": ok, "line": line, "short": short, "lines": lines, "parts": parts,
            "goto": parts["yes"].get("goto") or "", "goto_label": parts["yes"].get("goto_label") or ""}


__all__ = ["answer", "tidy_piles", "OWNER_FINDING_CHECKS", "SKIPPED_CHECKS", "FINDING_WORDS", "ERROR_WORDS",
           "TIDY_GOTO", "TIDY_LABEL", "CONFIRM_GOTO", "CONFIRM_LABEL", "ALL_OK_LINE", "TASKS_UNREAD_LINE",
           "REVIEW_UNREAD_LINE", "TIDY_UNREAD_LINE"]
