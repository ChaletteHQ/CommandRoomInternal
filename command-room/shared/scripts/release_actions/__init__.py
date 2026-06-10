"""Release actions package (v3.14.4+).

Companion to release_detectors/. Each module here exports an action function
that update-bridge Phase 4.8 invokes for manifest items with action="auto_apply".

Action contract:

    def fn(events_jsonl_path, workspace_root, detector_context) -> dict:
        '''
        Args:
            events_jsonl_path: Path to _hq/data/events.jsonl.
            workspace_root: Path to the workspace root (parent of _hq/).
            detector_context: dict returned by the detector for this item.

        Returns:
            {
              "success": bool,            # action completed without raising
              "ran": bool,                # True if it actually did something; False if no-op
              "context": dict,            # merged into detector_context for notice_template
              "error": Optional[str],     # set when success=False
              "fallback_prompt": Optional[str]  # if success=False AND we want to fall back
                                              # to surfacing an instruct_user-style prompt
            }
        '''

Idempotency: actions must short-circuit when their effect is already applied
(usually by checking for a prior event in events.jsonl). Return success=True
ran=False in that case so the bridge skips surfacing a notice.

Safety constraint (v3.14.4+ design rule): auto_apply actions MUST be additive,
reversible, and no-data-loss. Substrate-rewriting actions (corruption recovery,
backfills) are allowed because they quarantine sidecar-style — original data is
preserved at events_quarantine_*.jsonl.

Anything destructive (delete, overwrite without backup) MUST stay instruct_user
so the customer explicitly consents to the operation.
"""
