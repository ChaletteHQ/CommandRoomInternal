# Command Room hooks

## Stop hook — GATE2 D3 (best-effort same-turn enforcement)

`hooks.json` registers a `Stop` hook that runs `shared/scripts/gate2_turn_sweep.py`.
It sweeps the deliverables the turn just produced for voice/privacy violations and
surfaces them BEFORE the user forwards the artifact.

- Plugin `Stop` hooks are CONFIRMED in the Claude Code CLI; whether the Cowork
  sandboxed runtime executes plugin hooks is UNCONFIRMED (see the
  `gate2_turn_sweep.py` header).
- Shipped as a safe probe + best-effort layer: the runner NEVER blocks and ALWAYS
  exits 0. If the runtime ignores this hook there is zero harm, and the cleanup
  weekly sweep (`cleanup/SKILL.md` Phase 3f) remains the load-bearing backstop.
- If the hook DOES fire, a `gate_ran` event with `surface=turn_hook` lands in
  `events.jsonl` that turn — that is how a live re-run confirms whether Cowork
  honors it.

> Note: this rationale used to live in a `_comment` key inside `hooks.json`.
> That was removed in v4.6.1 — the Cowork marketplace validator rejects any
> unknown field in a hooks config (`_comment` included), which blocked install/
> update fleet-wide. Keep `hooks.json` limited to schema-valid hook fields only;
> document rationale here instead.
