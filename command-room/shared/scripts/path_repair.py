#!/usr/bin/env python3
"""
shared/scripts/path_repair.py — SPEC PATHREPAIR1 v2 (2026-08-27, rebuild
after REVIEW_PATHREPAIR1_2026-08-27's FAIL verdict).

A folder move or rename silently breaks every scheduled task. Task
registration stores an absolute workspace path (`_hq/workspace_config.json`
`workspace_root`, written by `enable-command-room-schedules` Step 0.C and
baked into each Cowork bootloader as `<WORKSPACE_BASENAME>` — see
`scheduled-task-bootloader.md`); rename or move the workspace folder and that
stored path goes dead with no error and no signal (BACKLOG row PATHREPAIR1 —
observed in six separate workspaces, every machine).

WHY V2 (the F1 finding, read literally)
-----------------------------------------
v1 compared the stored path against THIS PROCESS's OWN filesystem view — a
reachability check ("does os.path.isfile() answer yes at this string"). That
is vantage-BLIND: a Cowork scheduled fire runs inside a per-session sandbox
mount (`/sessions/<slug>/mnt/<basename>/...`, a NEW slug every fire —
`workspace_paths.py`, field-verified 2026-08-08) where the stored MACHINE
path can never resolve at all, and a real multi-machine workspace legitimately
stores a DIFFERENT machine's path at the top level (the `machines` block,
live example: `workspace_config.json` on the flagship workspace carries an
explicit 2026-08-26 operator note "these are two different PCs, not drift").
Under the v1 check both of those read as DEAD every single fire — and the
repair leg would happily "fix" a workspace that was never broken, persisting
an ephemeral mount path or ping-ponging the shared config between two
machines. That is the exact defect class WALKFIX1 exists to purge, this time
written by a repair feature.

M's ruling 2026-08-27 (spec v2 §0, binding, kept verbatim below) replaces
"does this path exist right now" with an IDENTITY question with THREE
answers, not two:

RULINGS (binding)
------------------
1. Dead-ness is identity-based, never reachability-based. A stored root has
   three states: ALIVE (resolves through this vantage AND carries this
   workspace's marker+fingerprint), DEAD (resolves, is PROBEABLE, and
   affirmatively no longer carries it), UNKNOWN (cannot be probed from this
   vantage at all — a session mount, another machine's registration, a
   disconnected drive). "Nobody answered the knock" is UNKNOWN, not DEAD.
   Only DEAD ever repairs. UNKNOWN never writes, never alarms, never
   triggers a candidate search.
2. Ephemeral roots are unwritable, ever. Any path the session-mount shape
   recognizes is deny-listed both as a repair TARGET and as a persistable
   root — pinned by test, mutation-verified. A repair whose only candidate
   is ephemeral refuses.
3. Machines-aware. The config's `machines` block is part of the model: a
   root belonging to a DIFFERENT machine is ELSEWHERE, never dead, never
   repaired from this machine. Cross-machine repair is out of scope
   entirely.
4. v1 fences kept verbatim: unique-or-nothing marker+fingerprint match (the
   decoy-refusal behavior review-proven at 49824b80); zero/multi candidates
   -> no write, loud report; TASKALARM1 `dead_root` class distinct from
   `never_authorized`/`late`/`receipt_gap`.
5. Durable undo (review F4). The `path_repair` event carries
   `pre_repair_config`; `undo()` restores from the EVENT ALONE — no
   in-memory receipt required. A repair that cannot be undone from the
   record is a migration, and migrations do not run silently.
6. Bridge call site wired (review F2). The once-at-update leg exists and is
   tested, not just described (`command-room-update-bridge/SKILL.md`).
7. Cowork verification is load-bearing: a fixture reproducing the
   session-mount vantage must prove a HEALTHY workspace probed from it
   classifies UNKNOWN with zero writes across repeated fires. Fleet-ward
   shipping (a manifest line) waits on ONE supervised live Cowork fire —
   out of scope for this branch; see the BUILD record.

THE FINGERPRINT (unchanged from v1)
-------------------------------------
events.jsonl is additive-only history (never rewritten — DEVELOPMENT.md's
architecture invariants). Its EARLIEST line is therefore immutable for the
life of a workspace: a rename/move carries the identical bytes; a genuinely
different Command Room workspace (even one whose `_hq/` merely looks right)
almost never has the same first line. SHA-256 of that line is the
fingerprint `find_workspace` matches candidates against.

THE VANTAGE
-----------
`current_vantage(current_root, machine=None, home=None)` builds the small
dict `classify_root` needs: `current_root` is NEVER a stored value — it is
the directory THIS caller is demonstrably already running against (Cowork's
bash discovery / CONTRACT.md Rule 22, resolved and verified before this
module is ever imported). `machine` is looked up LAZILY, and only when a
registration's `machines` block actually exists (`machine_identity.py`'s
per-machine marker file, `~/.command-room/machine_id`) — the overwhelming
majority of workspaces (and every pre-v2 fixture in this battery) carry no
`machines` block at all, so this never touches a real machine's identity
marker unless the registration under test actually needs one.

TWO CALL SHAPES, ONE ENGINE (unchanged from v1)
-------------------------------------------------
`fire_time_guard(workspace_root)` covers both real invocation points from
ruling 3 (the maintenance dispatch preamble, the update-bridge leg, and the
scheduled-task bootloader's fire-time guard): the caller already runs
against a LIVE, verified root, so it is passed as `trusted_candidate` —
`repair()` accepts it without a fingerprint match on a workspace's first-ever
repair and backfills one for every future repair to check against. A cold,
general-purpose repair (nothing trusted yet) supplies `search_roots` and must
clear the full marker + fingerprint bar at exactly one candidate.

`plan_repair()` is the pure (read-only) core `repair()`, `fire_time_guard()`
and `task_alarm._classify_dead_root` all share — so no two callers can ever
disagree about what "dead" / "unknown" / "unrepairable" means.

Sample/Stone-roster convention: nothing in this module's data shape is a
person or org name (workspace paths + hashes only) — the PRIVACY_POLICY.md
roster doesn't apply here, same posture task_alarm.py's own header takes.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import workspace_paths as _wp  # noqa: E402 — the canonical vantage/mount machinery

MARKER_RELPARTS = ("_hq", "data", "events.jsonl")
WORKSPACE_CONFIG_RELPARTS = ("_hq", "workspace_config.json")
EVENT_TYPE = "path_repair"
DEFAULT_MAX_SCAN_DEPTH = 4

# The workspace_config.json fields a repair ever touches — kept as a tuple so
# `repair()` / `undo()` snapshot and restore exactly the same set (REPAIRED_
# FIELDS drift between the two would make an undo silently partial).
REPAIRED_FIELDS = ("workspace_root", "workspace_basename", "root_fingerprint")

# The three-state identity model (ruling 1).
STATE_ALIVE = "ALIVE"
STATE_DEAD = "DEAD"
STATE_UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# The marker + fingerprint
# ---------------------------------------------------------------------------

def _marker_path(root) -> Path:
    return Path(root).joinpath(*MARKER_RELPARTS)


def has_marker(root) -> bool:
    """The workspace marker: `_hq/data/events.jsonl` exists as a readable
    file. Defensive — any OS-level surprise (permissions, a dangling
    junction) reads as 'not there' rather than raising into a fire."""
    if not root:
        return False
    try:
        return _marker_path(root).is_file()
    except OSError:
        return False


def compute_fingerprint(root) -> Optional[str]:
    """SHA-256 of events.jsonl's EARLIEST line, or None when the marker is
    missing, unreadable, or the file is empty (nothing to fingerprint yet —
    a workspace with zero events has no identity to match against)."""
    p = _marker_path(root)
    try:
        with p.open("r", encoding="utf-8") as f:
            first_line = f.readline()
    except OSError:
        return None
    first_line = first_line.strip()
    if not first_line:
        return None
    return hashlib.sha256(first_line.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# workspace_config.json — the registration record
# ---------------------------------------------------------------------------

def _config_path(root) -> Path:
    return Path(root).joinpath(*WORKSPACE_CONFIG_RELPARTS)


def read_workspace_config(root) -> dict:
    try:
        data = json.loads(_config_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def registration(root) -> dict:
    """Build a `reg` dict from a LIVE workspace's own on-disk record — the
    normal caller shape:
    `classify_root(registration(workspace_root), current_vantage(workspace_root))`.

    `reg["workspace_root"]` is the STORED absolute path
    (`workspace_config.json`'s own `workspace_root` field — the value task
    bindings were registered against) — falling back to `root` itself when
    the field is absent (a config written before v2.14.26 added the field,
    or no config at all yet: a fresh install's dispatcher preamble is
    trivially correct on day one, because `root` IS where it's running).

    `reg["machines"]` (ruling 3) is the config's per-machine block verbatim
    (`{}` when absent) — the raw dict, never interpreted here; interpreting
    it is `classify_root`'s job so there is exactly one place that decision
    is made.

    `reg["config_path"]` deliberately always points at THIS caller's `root`
    — where a repair result gets written — never derived from the (possibly
    stale) stored value: the file doing the recording always lives inside
    the folder that is ACTUALLY executing this code.
    """
    cfg = read_workspace_config(root)
    stored_root = cfg.get("workspace_root")
    machines = cfg.get("machines")
    return {
        "workspace_root": (stored_root if isinstance(stored_root, str) and stored_root.strip()
                           else str(root)),
        "config_path": str(_config_path(root)),
        "root_fingerprint": cfg.get("root_fingerprint"),
        "task_ids": list(cfg.get("registered_taskIds") or []),
        "search_roots": cfg.get("path_repair_search_roots"),
        "machines": machines if isinstance(machines, dict) else {},
    }


# ---------------------------------------------------------------------------
# The vantage (ruling 1-3)
# ---------------------------------------------------------------------------

def is_session_mount_root(value) -> bool:
    """True when `value` is a workspace ROOT sitting inside a Cowork
    per-session sandbox mount — thin wrapper over `workspace_paths`' own
    session-mount shape (the canonical vantage/mount machinery this module
    integrates with rather than reimplementing). Coerces to `str` first —
    unlike `workspace_paths`' own callers (always JSON-sourced strings),
    this module passes `Path` objects around routinely (`Path(root)`,
    discovered candidates, …), and `workspace_paths.is_session_mount_root`
    deliberately stays strict (`isinstance(value, str)`) to match its
    sibling pointer-classification functions."""
    if value is None:
        return False
    return _wp.is_session_mount_root(str(value))


def current_vantage(current_root, *, machine=None, home=None) -> dict:
    """The vantage descriptor `classify_root` needs.

    `current_root` is NEVER a stored value — it is the directory this call
    is demonstrably already running against (the caller's own live,
    already-verified root: `$WORKSPACE` at the bootloader / dispatcher /
    bridge). `machine` and `home` are passed straight through, UNRESOLVED —
    `classify_root` only ever needs real machine identity when a
    registration's `machines` block exists at all, so resolving it here
    unconditionally would touch `machine_identity`'s real per-machine marker
    file (`~/.command-room/machine_id`) on every single-machine workspace,
    including every fixture in this battery that never mentions `machines`.
    Lazy resolution keeps that blast radius to the workspaces that actually
    have one."""
    return {
        "current_root": str(current_root) if current_root else None,
        "machine": machine,
        "home": home,
    }


def _same_path(a, b) -> bool:
    if not a or not b:
        return False
    def _norm(x):
        return str(x).strip().replace("\\", "/").rstrip("/").lower()
    return _norm(a) == _norm(b)


def _reachable(path) -> bool:
    """Ruling 1's 'resolves... is probeable': can THIS vantage even ask the
    filesystem about `path`'s neighborhood? False only when the PARENT
    itself cannot be found — the 'disconnected drive' shape ruling 1 names
    (nobody answered the knock). A parent that answers 'no' to the exact
    subfolder is the ordinary moved/renamed-folder case, which stays
    reachable and reads DEAD from the marker check below, never UNKNOWN."""
    try:
        p = Path(path)
        parent = p.parent
        if parent == p:  # a bare drive/anchor names its own parent
            return p.exists()
        return parent.exists()
    except OSError:
        return False


def _machine_id_for(vantage: dict) -> Optional[str]:
    machine = vantage.get("machine")
    if machine is not None:
        return machine
    try:
        from machine_identity import machine_id

        return machine_id(home=vantage.get("home"))
    except Exception:  # noqa: BLE001 — identity is never load-bearing enough
        # to crash a fire; an unresolvable machine reads as "unknown machine"
        # below, which is the SAFE direction (falls back to single-machine
        # posture rather than guessing a cross-machine write is fine).
        return None


def _scoped_root(reg: dict, vantage: dict) -> tuple:
    """(root, owner) — ruling 3. `owner` is "self" (evaluate `root`
    normally), "other" (the STORED top-level root is recorded as belonging
    to a DIFFERENT machine's own registration — ELSEWHERE, out of scope in
    both directions, per ruling 3's 'a machine repairs only its own
    bindings'), or "unclaimed" (a non-empty `machines` block exists but
    THIS machine has no entry in it, or its identity can't be resolved —
    it may confirm the shared binding ALIVE but has no standing to declare
    it DEAD, so dead-shaped verdicts downgrade to UNKNOWN in
    `classify_root`; REVIEW_PATHREPAIR1v2 F1).

    Machine identity is resolved LAZILY (`_machine_id_for`) and ONLY when
    `reg["machines"]` is non-empty — every pre-machines-block workspace (the
    overwhelming majority, and every fixture that doesn't explicitly build a
    `machines` block) short-circuits to `("self", top_root)` without ever
    touching `machine_identity`.
    """
    machines = reg.get("machines") if isinstance(reg.get("machines"), dict) else {}
    top_root = reg.get("workspace_root")
    if not machines:
        return top_root, "self"

    machine = _machine_id_for(vantage)
    if not machine:
        # No way to tell whose registration this is from here. When a
        # `machines` block EXISTS, an unresolvable identity must not be
        # promoted to ownership of the shared binding — ALIVE may still be
        # confirmed (read-only), but a dead-shaped verdict has no standing
        # to repair (REVIEW_PATHREPAIR1v2 F1: ruling 3's "a machine repairs
        # only its own bindings", applied to the write path).
        return top_root, "unclaimed"

    my_entry = machines.get(machine)
    if my_entry is None:
        # This machine has NO entry in a non-empty `machines` block: it has
        # no recorded binding here at all, so it has no standing to declare
        # the shared top-level binding DEAD (and thereby repair it) — that
        # is exactly how an unlisted machine (a regenerated machine_id, a
        # not-yet-registered box syncing the same workspace) would clobber
        # the recorded owner's registration (REVIEW_PATHREPAIR1v2 F1).
        # Reading ALIVE stays allowed; DEAD downgrades to UNKNOWN.
        return top_root, "unclaimed"
    my_override = my_entry.get("workspace_root") if isinstance(my_entry, dict) else None
    if my_override and not _same_path(my_override, top_root):
        # This machine has its OWN recorded root, distinct from the shared
        # top-level value — the top-level value was never this machine's.
        # Cross-machine repair is out of scope in BOTH directions (ruling
        # 3): do not silently redirect to the override either, just say so.
        return top_root, "other"
    return top_root, "self"


def classify_root(reg: dict, vantage: dict) -> dict:
    """The three-state identity classification (ruling 1), replacing the v1
    boolean `dead_root`. Returns `{"state": ..., "reason": str|None,
    "root": str|None}` — `root` is the specific stored value that was
    (or, for UNKNOWN, would have been) evaluated.

    Order matters and is itself part of the contract:
      1. ephemeral VANTAGE (ruling 2) — checked first, absolute. A fire
         whose own live root is a session mount can reach no durable
         conclusion about anything at all.
      2. machine scope (ruling 3) — a stored root recorded as belonging to
         a different machine is never evaluated for aliveness from here.
      3. an ephemeral STORED root (should not normally arise — a prior
         repair would have refused it as a target — but defends the same
         way if it ever does).
      4. reachability (ruling 1) — "resolves... is probeable".
      5. marker + fingerprint identity — the actual ALIVE/DEAD verdict.
    """
    if not isinstance(reg, dict):
        reg = {}
    if not isinstance(vantage, dict):
        vantage = {}
    current_root = vantage.get("current_root")

    if is_session_mount_root(current_root):
        return {"state": STATE_UNKNOWN, "reason": "ephemeral_vantage", "root": None}

    stored_root, owner = _scoped_root(reg, vantage)
    if owner == "other":
        return {"state": STATE_UNKNOWN, "reason": "elsewhere", "root": stored_root}
    if not stored_root:
        return {"state": STATE_UNKNOWN, "reason": "no_registration", "root": None}

    if is_session_mount_root(stored_root):
        return {"state": STATE_UNKNOWN, "reason": "ephemeral_registration", "root": stored_root}

    if not _reachable(stored_root):
        return {"state": STATE_UNKNOWN, "reason": "unreachable", "root": stored_root}

    if has_marker(stored_root):
        stored_fp = reg.get("root_fingerprint")
        if stored_fp:
            live_fp = compute_fingerprint(stored_root)
            if live_fp is not None and live_fp != stored_fp:
                if owner == "unclaimed":
                    return {"state": STATE_UNKNOWN, "reason": "unclaimed_binding",
                            "root": stored_root}
                return {"state": STATE_DEAD, "reason": "fingerprint_mismatch", "root": stored_root}
        return {"state": STATE_ALIVE, "reason": None, "root": stored_root}
    if owner == "unclaimed":
        # REVIEW_PATHREPAIR1v2 F1 (ruling 3): a machine with no entry in a
        # non-empty `machines` block (or whose identity can't be resolved)
        # may CONFIRM the shared binding alive, but has no standing to
        # declare it dead — "dead" is what authorizes a repair write, and a
        # repair by an unlisted machine rewrites the recorded owner's
        # registration (the ping-pong/clobber class F1 named). UNKNOWN,
        # never a write, never an alarm.
        return {"state": STATE_UNKNOWN, "reason": "unclaimed_binding", "root": stored_root}
    return {"state": STATE_DEAD, "reason": "marker_absent", "root": stored_root}


# ---------------------------------------------------------------------------
# Candidate discovery (unchanged from v1, plus ruling 2's ephemeral deny-list)
# ---------------------------------------------------------------------------

def discover_candidates(search_roots: Iterable, max_depth: int = DEFAULT_MAX_SCAN_DEPTH) -> list:
    """Every directory under `search_roots` (bounded depth) that carries the
    workspace marker. Cross-platform, pure `pathlib` — no shell-out, so this
    runs identically wherever the caller runs (dispatch preamble, bridge
    update, a diagnostic CLI). Directories that don't exist are skipped
    silently (a parent that no longer exists either is not a defect in
    THIS function — the caller has bigger problems)."""
    found: list[Path] = []
    for sr in search_roots or ():
        base = Path(sr)
        if not base.is_dir():
            continue
        _walk(base, depth=0, max_depth=max_depth, found=found)
    # Stable, deduplicated order — same input always yields the same
    # candidate list (ambiguity reporting must be reproducible, not a
    # filesystem-iteration-order coin flip).
    seen = set()
    ordered = []
    for p in found:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)
    ordered.sort(key=lambda p: str(p))
    return ordered


def _walk(base: Path, depth: int, max_depth: int, found: list) -> None:
    if has_marker(base):
        found.append(base)
        # Don't recurse INTO a confirmed workspace — a workspace nested
        # inside another workspace's folder is not a second candidate, it's
        # a sub-folder of the one just found.
        return
    if depth >= max_depth:
        return
    try:
        children = [c for c in base.iterdir() if c.is_dir()]
    except OSError:
        return
    for c in children:
        # Skip conspicuously internal folders — never descend into a
        # workspace's own _hq (already checked at this level by has_marker)
        # or dot-directories, which is how deep, slow scans are avoided.
        if c.name.startswith("."):
            continue
        _walk(c, depth + 1, max_depth, found)


def _partition_ephemeral(candidates: Iterable) -> tuple:
    """(usable, excluded) — ruling 2's deny-list applied to a discovered
    candidate set. A session-mount-shaped directory is never a valid repair
    TARGET, no matter how confidently its marker+fingerprint would
    otherwise match: it is gone the moment the session that produced it
    ends, so pointing a durable registration at it just relocates the F1
    bug one level down."""
    usable, excluded = [], []
    for c in candidates or ():
        (excluded if is_session_mount_root(c) else usable).append(c)
    return usable, excluded


def matching_candidates(candidates: Iterable, fingerprint: Optional[str] = None) -> list:
    """Every candidate carrying the marker AND (when `fingerprint` is given)
    a matching fingerprint. The shared filter `find_workspace` and
    `plan_repair`'s ambiguity reporting both build on."""
    out = []
    for c in candidates or ():
        c = Path(c)
        if not has_marker(c):
            continue
        if fingerprint is not None and compute_fingerprint(c) != fingerprint:
            continue
        out.append(c)
    return out


def find_workspace(candidates: Iterable, fingerprint: Optional[str] = None) -> Optional[Path]:
    """Unique-or-nothing (ruling 4, kept verbatim from v1): the ONE
    candidate whose marker exists and whose fingerprint matches
    `fingerprint` (when given) — None on zero OR two-plus matches.
    `repair()` / `plan_repair()` use `matching_candidates` directly when
    they need to REPORT the ambiguous set; this is the narrow public
    primitive named in the spec's item 1. Ephemeral candidates are NOT
    filtered here (this primitive is deliberately dumb) — `plan_repair` is
    where ruling 2's deny-list is enforced, on the actual repair path."""
    matches = matching_candidates(candidates, fingerprint)
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# The plan — pure, no I/O beyond reads. Shared by repair() and
# task_alarm._classify_dead_root so the two can never disagree.
# ---------------------------------------------------------------------------

def plan_repair(
    reg: dict,
    *,
    trusted_candidate=None,
    search_roots: Optional[Iterable] = None,
    max_depth: int = DEFAULT_MAX_SCAN_DEPTH,
    vantage: Optional[dict] = None,
) -> dict:
    """What a repair of `reg` WOULD do, without doing it.

    `vantage` is the identity-based gate (ruling 1-3) — when omitted, a
    trivial self-referential vantage is built from `trusted_candidate` (or
    `reg`'s own stored root) with no machine override, which reproduces the
    single-machine, non-ephemeral posture every direct/cold caller has
    always had; the two REAL "runs where the fleet already runs" call sites
    (`fire_time_guard`) always build and pass a real one.

    Returns one of:
      {"class": "alive", ...}
      {"class": "unknown", "reason": ...}                  — ruling 1/2/3:
        never dead, never repaired, never a candidate-search trigger.
      {"class": "repaired", "new_root": Path, "trusted": bool}
      {"class": "ambiguous", "candidates": [Path, ...]}      — 2+ matches
      {"class": "no_candidate", "candidates_considered": int}
      {"class": "no_fingerprint"}  — dead, no stored fingerprint, and no
        `trusted_candidate` was offered either: a wide scan cannot clear
        ruling 2's confidence bar with nothing to compare against.
      {"class": "ephemeral_candidate", "candidates": [...]}  — ruling 2: the
        only candidate(s) found (trusted or discovered) are session-mount
        shaped and therefore deny-listed as a repair TARGET.
    """
    if vantage is None:
        seed = trusted_candidate if trusted_candidate is not None else reg.get("workspace_root")
        vantage = current_vantage(seed)

    verdict = classify_root(reg, vantage)
    if verdict["state"] == STATE_ALIVE:
        return {"class": "alive"}
    if verdict["state"] == STATE_UNKNOWN:
        return {"class": "unknown", "reason": verdict.get("reason")}

    fingerprint = reg.get("root_fingerprint")

    # The trusted-candidate path (ruling 3's real call sites): the caller is
    # not guessing, it is naming the directory it is DEMONSTRABLY executing
    # against. Accepted without a fingerprint match (there may be none on
    # record yet); a stored fingerprint that DISAGREES with the trusted
    # candidate is a louder problem than a missing one and is refused
    # rather than silently overridden.
    if trusted_candidate is not None:
        tc = Path(trusted_candidate)
        if is_session_mount_root(tc):
            # Defense in depth (ruling 2) — unreachable via the two real
            # call sites today (their vantage IS trusted_candidate, so
            # classify_root's ephemeral-vantage check already caught this
            # above), but a repair TARGET is deny-listed categorically,
            # independent of how the caller arrived at it.
            return {"class": "ephemeral_candidate", "candidates": [str(tc)]}
        if not has_marker(tc):
            return {"class": "no_candidate", "candidates_considered": 0}
        if fingerprint is not None:
            tc_fp = compute_fingerprint(tc)
            if tc_fp is not None and tc_fp != fingerprint:
                return {
                    "class": "ambiguous",
                    "candidates": [tc],
                    "reason": ("the trusted candidate's own substrate "
                              "fingerprint does not match what's on record"),
                }
        return {"class": "repaired", "new_root": tc, "trusted": True}

    if fingerprint is None:
        return {"class": "no_fingerprint"}

    roots = list(search_roots) if search_roots is not None else (
        reg.get("search_roots") or [str(Path(reg["workspace_root"]).parent)]
    )
    candidates = discover_candidates(roots, max_depth=max_depth)
    usable, excluded = _partition_ephemeral(candidates)
    matches = matching_candidates(usable, fingerprint)
    if len(matches) == 1:
        return {"class": "repaired", "new_root": matches[0], "trusted": False}
    if len(matches) == 0:
        if not usable and excluded:
            return {"class": "ephemeral_candidate", "candidates": [str(c) for c in excluded]}
        return {"class": "no_candidate", "candidates_considered": len(candidates)}
    return {"class": "ambiguous", "candidates": matches}


UNREPAIRABLE_CLASSES = frozenset({"ambiguous", "no_candidate", "no_fingerprint", "ephemeral_candidate"})
# Ruling 1/2/3: UNKNOWN is not a failed repair attempt — it is "not
# evaluated from here at all". Kept as a distinct set from
# UNREPAIRABLE_CLASSES so callers (task_alarm in particular) can tell
# "tried and failed" (alarm-worthy) from "nothing to say from this vantage"
# (never alarm-worthy) apart — collapsing the two back together is exactly
# the mutation this module's battery pins against.
NO_WRITE_CLASSES = UNREPAIRABLE_CLASSES | {"unknown"}


# ---------------------------------------------------------------------------
# repair() — commits the plan. NO WRITE on any unrepairable OR unknown class
# (ruling 1/2).
# ---------------------------------------------------------------------------

def repair(
    reg: dict,
    *,
    trusted_candidate=None,
    search_roots: Optional[Iterable] = None,
    max_depth: int = DEFAULT_MAX_SCAN_DEPTH,
    vantage: Optional[dict] = None,
    now: Optional[_dt.datetime] = None,
) -> dict:
    """Repoint `reg`'s stale registration to its live root, evidence-based,
    never a guess (ruling 2). Returns a receipt dict:

      ok=True  (class "alive"):
        {"ok": True, "class": "alive", "old_root": None, "new_root": None,
         "task_ids": [...], "event": None}
      ok=True  (class "unknown" — ruling 1/2/3, never a write):
        {"ok": True, "class": "unknown", "old_root": None, "new_root": None,
         "task_ids": [...], "event": None, "reason": str}
      ok=True  (class "repaired"):
        {"ok": True, "class": "repaired", "old_root": str|None,
         "new_root": str, "task_ids": [...], "config_path": str,
         "pre_repair_config": {...}, "event": <the appended event>|None}
      ok=False (unrepairable — ambiguous/no_candidate/no_fingerprint/
        ephemeral_candidate): the `plan_repair` dict verbatim, plus
        "ok": False — NO write happens; this is the 'candidate roots -> NO
        write, loud report naming both' ruling, in code.
    """
    plan = plan_repair(reg, trusted_candidate=trusted_candidate,
                       search_roots=search_roots, max_depth=max_depth,
                       vantage=vantage)
    if plan["class"] == "alive":
        return {"ok": True, "class": "alive", "old_root": None, "new_root": None,
                "task_ids": reg.get("task_ids") or [], "event": None}
    if plan["class"] == "unknown":
        return {"ok": True, "class": "unknown", "old_root": None, "new_root": None,
                "task_ids": reg.get("task_ids") or [], "event": None,
                "reason": plan.get("reason")}
    if plan["class"] not in ("repaired",):
        plan = dict(plan)
        plan["ok"] = False
        return plan

    new_root = Path(plan["new_root"])
    if is_session_mount_root(new_root):
        # Ruling 2, belt-and-suspenders at the actual write boundary: never
        # write to an ephemeral root even if some future caller constructs
        # a plan dict by hand instead of going through `plan_repair`.
        return {"ok": False, "class": "ephemeral_candidate", "candidates": [str(new_root)]}

    old_root = reg.get("workspace_root")
    new_cfg_path = _config_path(new_root)
    live_cfg = read_workspace_config(new_root)
    pre_repair_config = {k: live_cfg.get(k) for k in REPAIRED_FIELDS}

    new_fp = reg.get("root_fingerprint") or compute_fingerprint(new_root)
    updated_cfg = dict(live_cfg)
    updated_cfg["workspace_root"] = str(new_root)
    updated_cfg["workspace_basename"] = new_root.name
    if new_fp is not None:
        updated_cfg["root_fingerprint"] = new_fp

    from atomic_write import atomic_write_json

    atomic_write_json(new_cfg_path, updated_cfg)

    task_ids = reg.get("task_ids") or list(live_cfg.get("registered_taskIds") or [])
    event = _write_repair_event(
        new_root, status="repaired", old_root=old_root, new_root=str(new_root),
        task_ids=task_ids, trusted=bool(plan.get("trusted")), now=now,
        # Ruling 5 — the durable-undo fix (review F4): the event itself
        # carries what undo() needs, so an undo remains possible after the
        # session (and any in-memory receipt) that performed the repair has
        # evaporated. `data.old_root`/`data.new_root` already escape the
        # WALKFIX1 pointer sweep (`root` is not a `_PATH_WORDS` token, a
        # pre-existing heuristic-scope gap the review named, not new here);
        # `pre_repair_config` rides the same posture.
        extra={"pre_repair_config": pre_repair_config},
    )

    return {
        "ok": True,
        "class": "repaired",
        "old_root": old_root,
        "new_root": str(new_root),
        "task_ids": task_ids,
        "config_path": str(new_cfg_path),
        "pre_repair_config": pre_repair_config,
        "event": event,
    }


def _write_repair_event(root, *, status, old_root, new_root, task_ids,
                        trusted, now=None, extra=None) -> Optional[dict]:
    """The ONE receipt/audit writer for this module — every repair (and
    every undo) leaves exactly one `path_repair` event, appended through the
    canonical gate (DEVELOPMENT.md: 'one append path'). Best-effort: a write
    that fails costs the audit trail for this repair, never the repair
    itself (the config was already updated by the time this is called)."""
    data = {
        "status": status,
        "old_root": old_root,
        "new_root": new_root,
        "task_ids": list(task_ids or []),
        "trusted": bool(trusted),
    }
    if extra:
        data.update(extra)
    event = {"type": EVENT_TYPE, "source_skill": "path_repair", "data": data}
    try:
        from event_gate import append_event

        events_path = Path(root).joinpath(*MARKER_RELPARTS)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        appended = append_event(events_path, event, holder="path_repair")
        return appended[0] if appended else None
    except Exception:  # noqa: BLE001 — never let the audit trail block a
        # repair whose actual filesystem work already succeeded.
        return None


# ---------------------------------------------------------------------------
# undo() — the DURABLE round trip ruling 5 requires (review F4)
# ---------------------------------------------------------------------------

def _latest_repair_event(root) -> Optional[dict]:
    """The LAST `path_repair` event in `root`'s own events.jsonl, whatever
    its status — additive-only history, so the last matching line is the
    most recent fact on record. None when the file is missing/unreadable or
    carries no `path_repair` event at all."""
    events_path = Path(root).joinpath(*MARKER_RELPARTS)
    try:
        text = events_path.read_text(encoding="utf-8")
    except OSError:
        return None
    latest = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == EVENT_TYPE:
            latest = ev
    return latest


def undo(root, *, event: Optional[dict] = None, now: Optional[_dt.datetime] = None) -> dict:
    """Revert the most recent SUCCESSFUL repair recorded for `root` —
    restores `workspace_config.json`'s `REPAIRED_FIELDS` to the event's own
    `pre_repair_config` and appends a `status: "undone"` event.

    Ruling 5 (review F4's fix): restores from the EVENT ALONE. `event` may
    be supplied directly (a `path_repair` event dict already in hand, e.g.
    the return of `repair()["event"]`) — otherwise the LATEST `path_repair`
    event for `root` is read straight off `events.jsonl`, so an undo remains
    possible long after the process (and any in-memory receipt) that
    performed the repair is gone — exactly the shape both real call sites
    need, since both run inside a silent scheduled fire whose session
    evaporates the moment it ends.

    Returns `{"ok": bool, "reason": str|None}`. Refuses (never guesses) when:
      - no `path_repair` event exists for `root` at all;
      - the latest one is not a successful repair (`status != "repaired"` —
        this also makes a double-undo refuse cleanly: the SECOND call's
        latest event is the first call's own `"undone"` record);
      - it carries no `pre_repair_config` (an old-shape or otherwise
        incomplete record — ruling 5's own sentence: 'a repair that cannot
        be undone from the record is a migration, and migrations do not run
        silently').
    """
    if event is None:
        event = _latest_repair_event(root)
    if not isinstance(event, dict):
        return {"ok": False, "reason": "no path_repair event found for this root"}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if data.get("status") != "repaired":
        return {"ok": False, "reason": f"latest event is not a successful repair "
                                        f"(status={data.get('status')!r})"}
    pre = data.get("pre_repair_config")
    if not isinstance(pre, dict):
        return {"ok": False, "reason": "event carries no pre_repair_config -- "
                                        "not an undoable repair record"}

    cfg_path = _config_path(root)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, json.JSONDecodeError):
        cfg = {}
    for field in REPAIRED_FIELDS:
        prior = pre.get(field)
        if prior is None:
            cfg.pop(field, None)
        else:
            cfg[field] = prior
    try:
        from atomic_write import atomic_write_json

        atomic_write_json(cfg_path, cfg)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"write failed: {exc}"}
    _write_repair_event(
        root, status="undone",
        old_root=data.get("new_root"), new_root=data.get("old_root"),
        task_ids=data.get("task_ids") or [], trusted=False, now=now,
    )
    return {"ok": True, "reason": None}


# ---------------------------------------------------------------------------
# fire_time_guard() — the SECOND leg ruling 1 requires: fire-time validation
# that fails LOUD instead of silently producing nothing, and now ALSO never
# alarms on a vantage it cannot trust (ruling 1/2/3). ONE function, called
# from every registered-task prompt path (the maintenance dispatcher preamble
# via `maintenance_dispatcher._check_and_repair_root`, the update-bridge leg,
# and the scheduled-task bootloader — see scheduled-task-bootloader.md's
# Step 1.4) so there is exactly one place this decision is made, never a copy
# per call site to drift.
# ---------------------------------------------------------------------------

def fire_time_guard(workspace_root, *, now: Optional[_dt.datetime] = None,
                    machine: Optional[str] = None, home: Optional[str] = None) -> dict:
    """Validate-or-repair-or-alarm-or-stay-quiet for ONE fire, against the
    workspace this code is demonstrably already running against
    (`workspace_root` — already resolved and verified by the caller's own
    discovery, Cowork's bash Rule 22 or the maintenance dispatcher's
    bootloader Step 1).

    `machine` / `home` are optional overrides for `current_vantage` — real
    call sites never pass them (production wants the real per-machine
    identity, resolved lazily and only when the registration's `machines`
    block exists at all); tests pass an explicit `machine=` so a
    multi-machine fixture never touches this box's own identity marker.

    Returns {"blocked": bool, "ok": bool, "checked": True, "state": ...}:
      - `state="ALIVE"`  — blocked=False, ok=True, repaired=False. A
        fingerprint backfill may have run.
      - `state="UNKNOWN"` (ruling 1/2/3) — blocked=False, ok=True,
        repaired=False, carries `reason`. NEVER a write, NEVER an alarm —
        the caller proceeds exactly as on a healthy root; this is what lets
        a session-mount fire or a cross-machine registration stay silent
        forever instead of repairing (or alarming) on every single fire.
      - `state="DEAD"`, repaired — blocked=False, ok=True, repaired=True,
        `"receipt": {...}`.
      - `state="DEAD"`, unrepairable — blocked=True, ok=False,
        repaired=False, `"detail": {...}`. The caller MUST treat this as a
        loud failure (an abort message in a chat surface; `due: []` plus
        this dict in the maintenance dispatcher) — never a quiet no-op.
    """
    reg = registration(workspace_root)
    vantage = current_vantage(workspace_root, machine=machine, home=home)
    plan = plan_repair(reg, trusted_candidate=workspace_root, vantage=vantage)

    if plan["class"] == "alive":
        try:
            ensure_fingerprint(workspace_root)
        except Exception:  # noqa: BLE001 — best-effort backfill only
            pass
        return {"blocked": False, "ok": True, "checked": True, "state": STATE_ALIVE,
                "repaired": False}

    if plan["class"] == "unknown":
        return {"blocked": False, "ok": True, "checked": True, "state": STATE_UNKNOWN,
                "repaired": False, "reason": plan.get("reason")}

    if plan["class"] == "repaired":
        result = repair(reg, trusted_candidate=workspace_root, vantage=vantage, now=now)
        if result.get("ok"):
            return {"blocked": False, "ok": True, "checked": True, "state": STATE_DEAD,
                    "repaired": True, "receipt": result}
        return {"blocked": True, "ok": False, "checked": True, "state": STATE_DEAD,
                "repaired": False, "detail": result}

    detail = dict(plan)
    detail["ok"] = False
    return {"blocked": True, "ok": False, "checked": True, "state": STATE_DEAD,
            "repaired": False, "detail": detail}


def ensure_fingerprint(root) -> Optional[str]:
    """Best-effort, idempotent backfill: if this LIVE workspace's config has
    no `root_fingerprint` yet, compute and persist one now, so a FUTURE
    rename has evidence to repair against. Returns the fingerprint (existing
    or newly written), or None on any failure (never blocks a fire — the
    cheap-check path this rides inside must stay cheap and safe)."""
    cfg = read_workspace_config(root)
    existing = cfg.get("root_fingerprint")
    if isinstance(existing, str) and existing.strip():
        return existing
    fp = compute_fingerprint(root)
    if fp is None:
        return None
    cfg["root_fingerprint"] = fp
    cfg.setdefault("workspace_root", str(root))
    try:
        from atomic_write import atomic_write_json

        atomic_write_json(_config_path(root), cfg)
    except Exception:  # noqa: BLE001 — a failed backfill costs one future
        # lower-confidence repair, never this fire.
        return fp
    return fp


__all__ = [
    "MARKER_RELPARTS",
    "WORKSPACE_CONFIG_RELPARTS",
    "EVENT_TYPE",
    "REPAIRED_FIELDS",
    "STATE_ALIVE",
    "STATE_DEAD",
    "STATE_UNKNOWN",
    "UNREPAIRABLE_CLASSES",
    "NO_WRITE_CLASSES",
    "has_marker",
    "compute_fingerprint",
    "read_workspace_config",
    "registration",
    "is_session_mount_root",
    "current_vantage",
    "classify_root",
    "ensure_fingerprint",
    "discover_candidates",
    "matching_candidates",
    "find_workspace",
    "plan_repair",
    "repair",
    "undo",
    "fire_time_guard",
]
