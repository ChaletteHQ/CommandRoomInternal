"""
Trivial detector that always applies. Used for manifest items that announce a
new skill / feature / behavior to any user coming from a prior version —
where there's no workspace-state check to run, the version-was-missed is the
trigger.
"""
from __future__ import annotations


def always_applies(events_jsonl_path) -> dict:  # noqa: ARG001
    return {"applies": True, "context": {}}
