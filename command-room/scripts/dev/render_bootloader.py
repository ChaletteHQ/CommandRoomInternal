#!/usr/bin/env python3
"""Render ONE scheduled chat's v3 bootloader prompt, exactly as registration does.

Why this exists (M's request, 2026-09-19 18:05 PT): before anything is
registered on a real seat, somebody has to be able to LOOK at the prompt a chat
would actually get, and at the exact field values the registration call would
carry. Reading the template and doing the substitutions by hand is how a
placeholder ships unresolved. This script does what the registration skill's
substitution rules say, and nothing else.

    python scripts/dev/render_bootloader.py \\
        --task-id inbox \\
        --orchestrator orchestrator-inbox.md \\
        --basename "Sample Brain" \\
        --abs-path "C:/Users/sample/Desktop/Sample Brain" \\
        --display-name Inbox \\
        --discover-block-file /path/to/discover_block.txt

Output is the prompt body on stdout — no leading frontmatter, because the
harness prepends its own and a second one is a doubling bug — followed by a
separator line and ONE JSON line holding the field values the registration call
would carry.

It registers nothing, calls nothing and writes nothing.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import inspect
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO_ROOT / "shared" / "scripts"
REFS = REPO_ROOT / "skills" / "enable-command-room-schedules" / "references"
TEMPLATE = REFS / "scheduled-task-bootloader.md"
BODY_MARKER = (
    "## The bootloader template (everything below this heading is the "
    "registered prompt body)"
)
SEPARATOR = "---CREATE_TRIGGER---"

#: What the registration skill substitutes into the template, in one place so
#: this script and the size gate cannot disagree about the placeholder set.
PLACEHOLDERS = (
    "<TASK_ID>",
    "<ORCHESTRATOR_FILENAME>",
    "<WORKSPACE_BASENAME>",
    "<PLUGIN_VERSION>",
    "<WORKSPACE_ABSOLUTE_PATH>",
    "<CHAT_DISPLAY_NAME>",
    "<DISCOVER_BLOCK>",
)

DISCOVER_PLACEHOLDER_LINE = (
    "<DISCOVER_BLOCK — run `python3 shared/scripts/workspace_access.py discover` "
    "and paste its block here>"
)

#: The workspace step's one proof-of-life call, as it appears in the body:
#: `run_helper --json '{...}'`. The access layer binds a helper's arguments by
#: NAME (`fn(**args)`), so an argument key the helper does not take is a
#: TypeError inside the fire, an `ok: false` envelope, and a chat that stops and
#: tells the customer its runtime is missing. That is a defect nobody can see
#: from the template, so the renderer binds it here instead — review F-1.
SMOKE_CALL_RE = re.compile(r"run_helper --json '(\{.*?\})'", re.S)


def check_smoke_call(body: str) -> None:
    """Refuse a body whose proof-of-life call cannot bind to its own helper."""
    match = SMOKE_CALL_RE.search(body)
    if match is None:
        raise SystemExit(
            "the rendered body has no run_helper proof-of-life call — the fire "
            "would have no measurement that the workspace layer works"
        )
    try:
        payload = json.loads(match.group(1))
    except ValueError as exc:
        raise SystemExit(f"the proof-of-life call is not valid JSON: {exc}")
    name = str(payload.get("name") or "")
    if name.count(":") != 1:
        raise SystemExit(
            f"the proof-of-life helper name {name!r} is not 'module:function'"
        )
    module_name, function_name = name.split(":", 1)
    sys.path.insert(0, str(SCRIPTS))
    try:
        module = __import__(module_name)
    except ImportError:
        raise SystemExit(
            f"the proof-of-life call names {module_name!r}, which is not a "
            "helper this plugin ships"
        )
    function = getattr(module, function_name, None)
    if function is None:
        raise SystemExit(
            f"the proof-of-life call names {name!r}, and {module_name!r} has no "
            f"{function_name!r}"
        )
    args = dict(payload.get("args") or {})
    try:
        inspect.signature(function).bind(**args)
    except TypeError as exc:
        raise SystemExit(
            f"the proof-of-life call passes arguments {sorted(args)} that "
            f"{name} does not accept ({exc}) — every fire would stop here"
        )


def template_body() -> str:
    """The registered prompt body: everything under the template heading."""
    text = TEMPLATE.read_text(encoding="utf-8")
    if BODY_MARKER not in text:
        raise SystemExit(
            f"the bootloader template has no body marker — looked for {BODY_MARKER!r}"
        )
    return text.split(BODY_MARKER, 1)[1].lstrip("\n").lstrip()


def plugin_version() -> str:
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    return str(data.get("version") or "").strip() or "unknown"


def render(
    *,
    task_id: str,
    orchestrator: str,
    basename: str,
    abs_path: str,
    display_name: str,
    version: str,
    discover_block: str,
    writer_id=None,
    writer_derivation=None,
) -> str:
    """The body registration composes, by CALLING the product composer
    (SCHEDREG1 MUST 3, D-1): `schedule_refresh.compose_bootloader_body` with
    the seven inputs and the pair. This script no longer carries its own
    substitution loop, so what it shows is what registration registers."""
    sys.path.insert(0, str(SCRIPTS))
    import schedule_refresh as _sr  # noqa: E402 - path set above

    return _sr.compose_bootloader_body(
        task_id,
        workspace_basename=basename,
        plugin_version=version,
        plugin_root=REPO_ROOT,
        orchestrator_filename_override=orchestrator,
        abs_path=abs_path,
        display_name=display_name,
        discover_block=discover_block,
        writer_id=writer_id,
        writer_derivation=writer_derivation,
    )


def trigger_fields(
    *,
    task_id: str,
    basename: str,
    abs_path: str,
    cron_local: str,
    tz_name: str,
    on_date: _dt.date,
    notify: bool,
) -> dict:
    """The field values the registration call would carry, from the seam itself.

    The cron is derived by the same function registration uses, so a mistake
    here is a mistake there, and reviewing this output is reviewing that.
    """
    sys.path.insert(0, str(SCRIPTS))
    import schedule_backend as sb  # noqa: E402 - path set above

    fields = {
        "name": sb.trigger_name(task_id, basename),
        "requires_local_device": True,
        "folders": [abs_path],
        "permission_mode": "auto",
        "notifications": {"push": bool(notify), "email": False},
        "cron_local": cron_local,
        "cron_expression": None,
        "timezone": tz_name,
        "converted_on": on_date.isoformat(),
    }
    converted = sb.local_cron_to_utc(cron_local, tz_name, on_date=on_date, task_id=task_id)
    if isinstance(converted, sb.Refusal):
        fields["cron_expression"] = None
        fields["refusal"] = {"line": converted.line, "reason_code": converted.reason_code}
    else:
        fields["cron_expression"] = converted[0]
    return fields


def default_cron(task_id: str) -> str:
    sys.path.insert(0, str(SCRIPTS))
    import schedule_config as sc  # noqa: E402 - path set above

    row = sc.DEFAULT_SCHEDULES.get(task_id) or {}
    return str(row.get("cron") or "")


def default_display_name(task_id: str) -> str:
    sys.path.insert(0, str(SCRIPTS))
    import schedule_config as sc  # noqa: E402 - path set above

    return sc.task_display_name(task_id)


def default_orchestrator(task_id: str) -> str:
    omap = json.loads((REFS / "orchestrator-map.json").read_text(encoding="utf-8"))
    return str(omap.get(task_id) or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--orchestrator", default=None,
                    help="orchestrator filename; defaults to the map's entry")
    ap.add_argument("--basename", required=True,
                    help="the workspace folder's name, e.g. \"Sample Brain\"")
    ap.add_argument("--abs-path", required=True,
                    help="the workspace folder's absolute path on the computer")
    ap.add_argument("--display-name", default=None,
                    help="the chat's display name; defaults to the registry's")
    ap.add_argument("--plugin-version", default=None,
                    help="defaults to the version in .claude-plugin/plugin.json")
    ap.add_argument("--discover-block-file", default=None,
                    help="file holding the access layer's discover block")
    ap.add_argument("--cron", default=None,
                    help="local cron; defaults to this chat's shipped default")
    ap.add_argument("--tz", default="America/Los_Angeles")
    ap.add_argument("--on-date", default=None,
                    help="YYYY-MM-DD; which side of a clock change to convert on")
    ap.add_argument("--no-push", action="store_true",
                    help="notifications off (the posture for background work)")
    ap.add_argument("--writer-id", default=None,
                    help="the registering seat's writer id (acct- + 12 hex); "
                         "with --writer-derivation, or not at all")
    ap.add_argument("--writer-derivation", default=None,
                    help="the 64-hex derivation digest beside --writer-id")
    ap.add_argument("--writer-from-seat", action="store_true",
                    help="bake THIS process's own derived pair "
                         "(writer_identity.bakeable_pair of --abs-path), "
                         "or none when this seat has no account")
    ap.add_argument("--live", action="store_true",
                    help="REFUSED: a live registration composes through the "
                         "product (set up command room schedules), never here")
    args = ap.parse_args(argv)
    if args.live:
        # SCHEDREG1 MAY 11: this dev script never renders for a live seat and
        # never reads the synced identity file; the product composer, driven
        # by the registering seat, is the only live path.
        sys.stderr.write("This script renders for review only. A live seat's "
                         "chats are composed by `set up command room "
                         "schedules` on that seat.\n")
        return 2
    if (args.writer_id is None) != (args.writer_derivation is None):
        sys.stderr.write("The writer id and its derivation must be given "
                         "together.\n")
        return 2

    orchestrator = args.orchestrator or default_orchestrator(args.task_id)
    if not orchestrator:
        raise SystemExit(
            f"no orchestrator file known for {args.task_id!r} — pass --orchestrator"
        )
    display_name = args.display_name or default_display_name(args.task_id)
    version = args.plugin_version or plugin_version()
    cron_local = args.cron or default_cron(args.task_id)
    if not cron_local:
        raise SystemExit(
            f"no shipped cron for {args.task_id!r} — pass --cron"
        )
    on_date = (
        _dt.date.fromisoformat(args.on_date) if args.on_date else _dt.date.today()
    )

    if args.discover_block_file:
        discover_block = Path(args.discover_block_file).read_text(encoding="utf-8").rstrip("\n")
    else:
        discover_block = DISCOVER_PLACEHOLDER_LINE
        print(
            "warning: no --discover-block-file given, so the workspace step still "
            "carries a placeholder. This prompt is NOT ready to register.",
            file=sys.stderr,
        )

    # IDENT1 I-9: the registering seat's writer pair, baked or removed - by
    # the ONE composer the registration skill uses (SCHEDREG1 MUST 3).
    sys.path.insert(0, str(SCRIPTS))
    import writer_identity as _wi  # noqa: E402 - path set above
    if args.writer_from_seat and args.writer_id is None:
        own = _wi.bakeable_pair(args.abs_path)
        if own:
            args.writer_id, args.writer_derivation = own
    try:
        body = render(
            task_id=args.task_id,
            orchestrator=orchestrator,
            basename=args.basename,
            abs_path=args.abs_path,
            display_name=display_name,
            version=version,
            discover_block=discover_block,
            writer_id=args.writer_id,
            writer_derivation=args.writer_derivation,
        )
    except ValueError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2

    left = [p for p in PLACEHOLDERS if p in body]
    if left:
        print(f"warning: unsubstituted placeholders left: {left}", file=sys.stderr)
    if body.lstrip().startswith("---"):
        raise SystemExit(
            "the rendered body starts with '---'; the harness adds its own "
            "frontmatter and a second one is a doubling bug"
        )
    check_smoke_call(body)

    fields = trigger_fields(
        task_id=args.task_id,
        basename=args.basename,
        abs_path=args.abs_path,
        cron_local=cron_local,
        tz_name=args.tz,
        on_date=on_date,
        notify=not args.no_push,
    )

    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write(SEPARATOR + "\n")
    sys.stdout.write(json.dumps(fields, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
