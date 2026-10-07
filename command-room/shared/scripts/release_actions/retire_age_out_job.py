#!/usr/bin/env python3
"""retire_age_out_job — R1's one-shot: the 30-day cleanup goes off, everywhere.

WHY
---
On 2026-09-13 the `age-out` maintenance job fired unattended for the fourth
time and applied: 48 agreed commitments closed at thirty quiet days and 51 more
parked, in one batch, on a bar nobody had ruled — five days after night 10 had
ruled the silence door at 45 days rest and 60 days let go. M's ruling R1 is one
sentence: EXIT1's 45-rest / 60-let-go door is the ONLY silence door in the
product.

Deleting the job from `maintenance_dispatcher.MAINTENANCE_JOBS` (DOORS1 1.1)
is what stops it on every seat that takes the update. This action exists for
the two things deletion alone does NOT do:

  1. A seat can carry its OWN per-job override. The dispatcher reads
     `workspace.schedule_config.maintenance_jobs.<id>` and a seat that had
     turned the job ON explicitly would keep saying so in its config long
     after the job stopped existing — a stored `true` against a job that is
     gone, waiting for anyone who re-adds the row. This writes the explicit
     `{"enabled": false}` so the seat's own config agrees with the product.
  2. The CUSTOMER is owed a sentence. A job that has been closing their work
     unattended stops; that is a change they should read once, in their own
     words, on the next brief — not a silence.

WHAT IT DOES
------------
`schedule_config.set_maintenance_job_enabled(ws, "age-out", enabled=False, …)`
— the sanctioned per-job writer (atomic locked write to `entities.json`, the
`schedule_config_changed` audit row through the one writer that
`served_slot_markers` reads). Then ONE
`plugin_update_remediation` receipt carrying `receipt_role: "marker"`, exactly
as `park_silent_backlog` does, so `applied_remediation_ids` carries this item
and the bridge's Step 4.8c writes no second row.

THE MARKER IS WRITTEN EVEN WHEN THE CONFIG WRITE IS A NO-OP. This is the whole
point of the 1.5 rider that rides with it: the operator's own seat already
paused the job by hand on 2026-09-13 (`maintenance_jobs.age-out.enabled =
false`), so on that seat there is nothing to write — and a run that writes
nothing at all is indistinguishable from a run that never happened for anyone
reading the ledger afterwards. A no-op leaves the marker and says nothing to
the customer (`ran=False`, so the bridge surfaces no notice).

WHAT IT DOES NOT DO
-------------------
It does not re-register anything. The registered maintenance prompt DID change
with this build (the `age-out … --apply` stanza left
`schedule_config.SILENT_TASKS`), and a registered prompt is refreshed by the
bridge's own unconditional hash-refresh (update-bridge SKILL.md Phase 5.9 /
FS-01), which compares the registered prompt against
`compose_silent_task_prompt` and updates it in place. This action reports
`prompt_stanza_retired: True` in its context so that refresh has a reason
recorded beside it; it never calls a scheduled-task tool itself, because an
`auto_apply` action runs against a workspace and not against the platform.

It does not touch history. The `age-out` id keeps its rows in
`receipts.CANONICAL_TASK_IDS`, `receipts.RECEIPT_TYPES`,
`event_types.MACHINE_SOURCE_SKILLS` and `schedule_config.DISPLAY_NAMES` — the
`pulse` precedent. Receipts written under it are on M's book already and have
to keep parsing and keep reading as English.

It does not reverse the 48 closes. M reversed those himself on 2026-09-13
(99/99, canonical `undo_batch`). A release action that re-opened commitments
on every seat would be a second unattended mass write, which is the thing this
one exists to stop.

Signature per references/RELEASE_MANIFEST.md "Action contract":
    fn(events_jsonl_path, workspace_root, detector_context) -> dict
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import schedule_config as sc  # noqa: E402

#: The manifest item id. The coordinator writes `shared/releases/v5.31.0.json`
#: at the cut; this module carries the item BODY (`MANIFEST_ITEM` below) so the
#: id, the detector and the sentence the customer reads are versioned with the
#: code that produces them rather than retyped into a json file by hand.
ITEM_ID = "v5310_retire_age_out_job"
ACTION_MODULE = "release_actions.retire_age_out_job"
ACTION_FUNCTION = "retire_age_out_job"

#: The retired job's id. A literal, not an import: the constant that used to
#: hold it (`commitment_backlog_sweep.AGE_OUT_JOB_ID`) was deleted with the job,
#: and the string this action has to switch off is the one already written into
#: seats' config files — it is a stored key, not a live name.
JOB_ID = "age-out"

SOURCE_SKILL = "command-room-update-bridge"

RECEIPT_TYPE = "plugin_update_remediation"
#: Same role field, same reason, as `park_silent_backlog.RECEIPT_ROLE`: the
#: bridge writes its own audit row for every item that surfaces, so "which of
#: these is the one-shot's marker" has to be answerable by a field.
RECEIPT_ROLE = "marker"
RECEIPT_OUTCOME_APPLIED = "applied"
#: The outcome on a seat that had nothing to change. It is still a marker and
#: still lands in `applied_remediation_ids`; the word says the run found the
#: job already off rather than claiming an act that did not happen.
RECEIPT_OUTCOME_NOOP = "already_retired"

MANIFEST_ITEM = {
    "id": ITEM_ID,
    "detector_module": "release_detectors.always",
    "detector_function": "always_applies",
    "action": "auto_apply",
    "action_module": ACTION_MODULE,
    "action_function": ACTION_FUNCTION,
    "notice_template": (
        "Retired the 30-day cleanup — the 45/60-day silence door is the only "
        "one now."
    ),
    "fallback_prompt_template": (
        "The 30-day cleanup is being retired (the 45/60-day silence door "
        "replaces it); switching it off on this workspace is pending and will "
        "retry on the next update."
    ),
}


def _events_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "events.jsonl"


def already_ran(workspace_root) -> bool:
    """Has this seat already had this one-shot? The action's own marker is the
    answer, read through `events_io` like every other full-history read.

    Both outcomes count. A seat that was already retired got its marker on the
    first update and must not be re-marked on the second; the marker's job is
    to say the item was reached, not to say work was done.

    A read that fails answers False — a missed marker costs one more no-op
    pass, while a marker invented out of an error would silence the item
    forever."""
    try:
        from events_io import load_events_owner_scoped
        events, _skipped = load_events_owner_scoped(workspace_root)
    except Exception:  # noqa: BLE001
        return False
    for ev in events:
        if (ev.get("type") or ev.get("event")) != RECEIPT_TYPE:
            continue
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if d.get("item_id") != ITEM_ID:
            continue
        if not (d.get("receipt_role") == RECEIPT_ROLE
                or d.get("source") == ACTION_MODULE):
            continue
        if d.get("outcome") not in (RECEIPT_OUTCOME_APPLIED,
                                    RECEIPT_OUTCOME_NOOP):
            continue
        return True
    return False


def plan(workspace_root) -> dict:
    """Pure read. `{status, stored_enabled, job_id}` where status is one of
    `not_a_workspace` | `already_marked` | `already_disabled` | `would_disable`.

    `already_disabled` and `would_disable` are told apart by the STORED
    override, not by the dispatcher's effective answer: a core job with no
    stored key runs, so "absent" is a seat that never said anything and still
    owes the explicit off."""
    ws = Path(workspace_root)
    if not (ws / "_hq" / "data").is_dir():
        return {"status": "not_a_workspace", "stored_enabled": None,
                "job_id": JOB_ID}
    stored = sc.maintenance_job_enabled_override(ws, JOB_ID)
    if already_ran(ws):
        return {"status": "already_marked", "stored_enabled": stored,
                "job_id": JOB_ID}
    if stored is False:
        return {"status": "already_disabled", "stored_enabled": stored,
                "job_id": JOB_ID}
    return {"status": "would_disable", "stored_enabled": stored,
            "job_id": JOB_ID}


def _write_marker(workspace_root, context: dict, *, outcome: str,
                  manifest_version=None) -> bool:
    """ONE `plugin_update_remediation` — the receipt AND the one-shot marker.

    Through the gated appender, which stamps `seq` and `ts` inside the writer
    lock (CONTRACT Rule 31); never by hand."""
    from event_gate import append_event
    data = {
        "item_id": ITEM_ID,
        "action": "auto_apply",
        "outcome": outcome,
        "receipt_role": RECEIPT_ROLE,
        "source": ACTION_MODULE,
        "job_id": JOB_ID,
        "counts": {"n_disabled": 1 if outcome == RECEIPT_OUTCOME_APPLIED else 0},
        "previous_enabled": context.get("stored_enabled"),
        "config_written": bool(context.get("config_written")),
        "prompt_stanza_retired": True,
        "replaced_by": "the 45-day rest / 60-day let-go silence door",
    }
    if manifest_version:
        data["manifest_version"] = manifest_version
    try:
        append_event(_events_path(workspace_root),
                     [{"type": RECEIPT_TYPE, "source_skill": SOURCE_SKILL,
                       "data": data}], holder=SOURCE_SKILL)
        return True
    except Exception as exc:  # noqa: BLE001 — loud, never fatal
        sys.stderr.write(f"[retire_age_out_job] marker failed: {exc}\n")
        return False


def retire_age_out_job(events_jsonl_path, workspace_root,
                       detector_context) -> dict:
    """The `auto_apply` action contract (release_actions/__init__.py).

    `ran=True` ONLY when this run actually switched the job off — that is the
    one case the customer reads a line about. A seat that was already off
    still gets its marker and says nothing (`ran=False`)."""
    ctx: dict = {"job_id": JOB_ID, "prompt_stanza_retired": True,
                 "config_written": False, "receipt_written": False}
    try:
        dc = detector_context if isinstance(detector_context, dict) else {}
        p = plan(workspace_root)
        ctx["status"] = p["status"]
        ctx["stored_enabled"] = p["stored_enabled"]
        if p["status"] in ("not_a_workspace", "already_marked"):
            return {"success": True, "ran": False, "context": ctx,
                    "error": None, "fallback_prompt": None}

        outcome = RECEIPT_OUTCOME_NOOP
        if p["status"] == "would_disable":
            res = sc.set_maintenance_job_enabled(
                workspace_root, JOB_ID, enabled=False,
                source_skill=SOURCE_SKILL)
            if res.get("error"):
                return {"success": False, "ran": False, "context": ctx,
                        "error": res["error"],
                        "fallback_prompt":
                            MANIFEST_ITEM["fallback_prompt_template"]}
            ctx["config_written"] = bool(res.get("changed"))
            ctx["audit_event_written"] = bool(res.get("event_written"))
            if res.get("changed"):
                outcome = RECEIPT_OUTCOME_APPLIED
        ctx["outcome"] = outcome
        ctx["receipt_written"] = _write_marker(
            workspace_root, ctx, outcome=outcome,
            manifest_version=dc.get("manifest_version"))
        return {"success": True,
                "ran": outcome == RECEIPT_OUTCOME_APPLIED,
                "context": ctx, "error": None, "fallback_prompt": None}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "ran": False, "context": ctx,
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_prompt": MANIFEST_ITEM["fallback_prompt_template"]}


def main(argv=None) -> int:
    import argparse
    import json
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="switch the job off (default: plan only)")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if not args.apply:
        print(json.dumps(plan(ws), indent=2))
        return 0
    out = retire_age_out_job(_events_path(ws), ws, {})
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
