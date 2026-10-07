#!/usr/bin/env python3
"""ENV1 — which environment is this process actually running in?

Command Room now runs in three places that look alike from inside a skill and
behave nothing alike:

  * `legacy_cowork`      — the old desktop Cowork sandbox: the workspace is
                           mounted under `<session>/mnt/`, plugin files live at
                           `<session>/mnt/.remote-plugins/plugin_*/`, the host
                           clock is the customer's own clock.
  * `merged_cloud`       — the merged claude.ai + Cowork container: the plugin
                           is SYNCED to `/root/.claude/plugins/synced/<org>_<account>/cr/`,
                           the workspace is NOT on this filesystem at all (it is
                           FUSE-mounted in a sandbox VM on the customer's PC and
                           reached through the workspace access layer), and the
                           container clock is not the customer's clock.
  * `claude_code_local`  — a Claude Code session on the customer's own machine
                           (or the headless VM): `CLAUDE_PLUGIN_ROOT` is set
                           natively, the workspace is a normal local path.

Guessing wrong is the wrong-premise class a green battery cannot see: a writer
that "works" against a filesystem that is not the customer's, or a clock read as
user-local when it is UTC. So the environment is DETECTED from named signals,
each with a source, and the report says WHICH signal decided and whether any
other signal disagreed.

Design: gap analysis `build/MERGED_ENV_GAP_ANALYSIS_2026-09-19.md` §7.3
(signals S1-S8, precedence, API, the preamble line, the per-mode table) plus the
reconciliation note's S9 — `/root/.claude/plugins/synced/*/cr/` exists, which is
what a scheduled fire in the merged container actually has. Rulings: §0.34
(unknown env: readers proceed with a banner, writers and fires refuse), §0.18
(workspace clock for cloud seats), §0.23 (dual backend).

CONTRACT
--------
  * `detect()` NEVER raises, NEVER writes, and NEVER reads the substrate. It
    reads the process environment, an optional caller-supplied tool-name list,
    and a handful of directory existence checks under `root`.
  * `root` exists so fixtures can replace `/` (the `build_fixture(base)` idiom
    G24/G43 already use). The shipped preamble never passes it.
  * 3.10-safe (the sandbox VM is Python 3.10.12): no `tomllib`, no
    `datetime.UTC`, no `StrEnum`, no `except*`, no `typing.Self`.

CLI (the Rule 22 preamble calls the third form):
    python3 shared/scripts/env_detect.py [--tools a,b,c]
                                        [--json | --shell | --signals]
`--shell` prints exactly the five keys the preamble exports, shell-quoted, on
one line, for `eval`. `--signals` prints ONE json line of the signal booleans
S1-S10, the entrypoint and the NAMES of the harness variables in reach — no
path, no value — which is the evidence line the scheduled task's bootloader
puts in its run log (FIX3 R-FIX3-1).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

# The ONE legal home of the old Cowork tool strings (GUARD1, guard G67). This
# module recognises a legacy tool list by asking the shim which SEAM an id now
# belongs to, so no dead tool literal lives here. Same package, stdlib-only, no
# side effects. If the shim is somehow absent (a partial copy in the sandbox
# VM), the conservative answer is "no legacy tool seen" — S3/S4 stay False and
# a different signal decides; `_LEGACY_SHIM_LOADED` is pinned so the absence
# can never pass unnoticed.
try:
    import legacy_tools as _legacy_tools
except ImportError:  # pragma: no cover - pinned by run_env1_test
    _legacy_tools = None
_LEGACY_SHIM_LOADED = _legacy_tools is not None

# The four modes. `env_detect` names WHERE THIS PROCESS RUNS. It deliberately
# does NOT name where the DATA is — that is `workspace_access.host_mode`
# ("vm" / "local" / "cowork_legacy" / "container"), a different vocabulary with
# a pinned mapping (spec D-1). Neither module renames the other's enum.
MODES = ("legacy_cowork", "merged_cloud", "claude_code_local", "unknown")

#: The core-plugin marker every plugin-root candidate must contain. Discovery is
#: CONTENT-ADDRESSED, never positional — Rule 22 stage 2/3, guard G1.
PLUGIN_MARKER = "shared/scripts/chat_output_renderer.py"

#: Where the merged environment syncs the plugin bundle (breakdown §4.1).
SYNCED_REGISTRY = "root/.claude/plugins/synced"

#: Where the merged container stages the workspace's brain file (breakdown §3.1).
STAGED_BRAIN_DIR = "mnt/user-data/uploads/cowork-folders"

#: The merged environment's device-bash tool — a LIVE name, not a legacy id,
#: so it is spelled here rather than in the legacy shim.
_MERGED_DEVICE_BASH = "mcp__remote-devices__device_bash"

#: Fixture-only re-root for the CLI. The shipped preamble never sets it; the
#: suites that EXECUTE the shipped preamble text do, so the snippet can run
#: against a planted tree instead of the real `/`.
ROOT_ENV_VAR = "CR_ENV_ROOT"

_OVERRIDE_VAR = "CR_ENV"
_SHELL_KEYS = (
    "CR_ENV",
    "CR_PLUGIN_ROOT",
    "CR_BRAIN_FILE",
    "CR_LOCAL_FS",
    "CR_CLOCK_TRUST",
)


@dataclass(frozen=True)
class EnvReport:
    """One environment reading. Every field is safe to put in a receipt."""

    mode: str
    decided_by: str
    conflict: bool
    signals: Dict[str, Any]
    plugin_root: Optional[str]
    brain_file: Optional[str]
    local_workspace_fs: bool
    host_clock_trust: str
    python: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "decided_by": self.decided_by,
            "conflict": self.conflict,
            "signals": dict(self.signals),
            "plugin_root": self.plugin_root,
            "brain_file": self.brain_file,
            "local_workspace_fs": self.local_workspace_fs,
            "host_clock_trust": self.host_clock_trust,
            "python": self.python,
        }


# --------------------------------------------------------------------------
# helpers (every one of them fails soft — detect() must never raise)
# --------------------------------------------------------------------------

def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def _glob(base: Path, pattern: str) -> list:
    try:
        return sorted(base.glob(pattern))
    except (OSError, ValueError, NotImplementedError):
        return []


def _tool_names(tools) -> list:
    """Accept strings or objects carrying `tool_id` / `name` (ToolDescriptor)."""
    if not tools:
        return []
    names = []
    try:
        for entry in tools:
            if isinstance(entry, str):
                names.append(entry)
                continue
            if isinstance(entry, dict):
                value = entry.get("tool_id") or entry.get("name")
                if isinstance(value, str):
                    names.append(value)
                continue
            value = getattr(entry, "tool_id", None) or getattr(entry, "name", None)
            if isinstance(value, str):
                names.append(value)
    except TypeError:
        return []
    return names


def _has_seam(names: list, seam_const: str) -> bool:
    """True when any name in `names` is a legacy id whose behaviour now belongs
    to the named seam of `legacy_tools` (`SEAM_ACCESS`, `SEAM_SCHEDULE`).

    The seam constant is looked up by ATTRIBUTE NAME so this module carries no
    tool literal and no seam literal of its own — guard G67's rule."""
    if _legacy_tools is None:
        return False
    seam = getattr(_legacy_tools, seam_const, None)
    if seam is None:
        return False
    try:
        for name in names:
            hit = _legacy_tools.translate(name)
            if hit is not None and hit[0] == seam:
                return True
    except Exception:  # noqa: BLE001 - a detector that raises is worse than False
        return False
    return False


def _session_dir(tmpdir: str) -> str:
    """`$CLAUDE_CODE_TMPDIR` minus a trailing `/tmp` — Rule 22's line 1."""
    session = tmpdir.rstrip("/").rstrip("\\")
    if session.endswith("/tmp") or session.endswith("\\tmp"):
        session = session[:-4]
    return session


def _root_path(root, rel: str) -> Path:
    """`rel` (a container-absolute path, written without its leading slash)
    resolved under `root`, so fixtures can plant it anywhere."""
    try:
        return Path(root) / rel
    except (TypeError, ValueError):
        return Path(rel)


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------

def _read_signals(env: Dict[str, str], root, names: list) -> Dict[str, Any]:
    """Raw observations S1-S9. Every value is a bool, a string or None, so the
    whole dict can be written into a receipt as-is."""
    signals: Dict[str, Any] = {}

    # S1 — the merged harness env pair (breakdown §2.1, A.1 "Flags").
    signals["S1_remote_cowork_env"] = (
        env.get("CLAUDE_CODE_REMOTE") == "true"
        and env.get("CLAUDE_CODE_ENTRYPOINT") == "remote_cowork"
    )

    # S2/S3/S4 — the tool list, which is NOT in os.environ: the model passes the
    # names it sees (the `transcript-search` ToolDescriptor idiom). S3 and S4
    # are derived through the legacy shim, by SEAM, so the dead ids live in one
    # file (guard G67) and a new legacy spelling is recognised the moment the
    # shim learns it.
    has_device_bash = _MERGED_DEVICE_BASH in names
    has_workspace_seam = _has_seam(names, "SEAM_ACCESS")
    has_schedule_seam = _has_seam(names, "SEAM_SCHEDULE")
    signals["S2_device_bash_tool"] = has_device_bash
    signals["S3_workspace_bash_tool"] = has_workspace_seam
    signals["S4_desktop_scheduler_tool"] = bool(
        has_schedule_seam and not has_device_bash and not has_workspace_seam
    )
    signals["tools_seen"] = len(names)

    # S5 — the legacy Cowork sandbox shape.
    tmpdir = env.get("CLAUDE_CODE_TMPDIR") or ""
    session = _session_dir(tmpdir) if tmpdir else ""
    signals["S5_remote_plugins_dir"] = bool(
        session and _is_dir(Path(session) / "mnt" / ".remote-plugins")
    )
    signals["session_dir"] = session or None

    # S6 — the staged brain file the merged container hands the session.
    staged = _glob(_root_path(root, STAGED_BRAIN_DIR), "*/CLAUDE.md")
    signals["S6_staged_brain_file"] = bool(staged)
    signals["staged_brain_path"] = str(staged[0]) if staged else None

    # S7 — Claude Code sets the plugin root natively.
    plugin_env = env.get("CLAUDE_PLUGIN_ROOT") or ""
    signals["S7_claude_plugin_root"] = bool(plugin_env and _is_dir(Path(plugin_env)))

    # S8 — the explicit override (validated against MODES; a typo never becomes
    # a guess).
    override = env.get(_OVERRIDE_VAR)
    signals["S8_override"] = override if override else None

    # S9 — the synced plugin registry: what a scheduled fire in the merged
    # container actually has (reconciliation note to §7).
    synced = _glob(_root_path(root, SYNCED_REGISTRY), "*/cr")
    synced = [p for p in synced if _is_dir(p)]
    signals["S9_synced_registry"] = bool(synced)
    signals["synced_registry_path"] = str(synced[0]) if synced else None

    # S10 — the merged HARNESS, whatever shape the filesystem has (FIX3 F3-1,
    # ruling R-FIX3-1). The re-walk of 2026-09-21 proved that the scheduled
    # container of app 2.2553.1 is shell-indistinguishable from the legacy
    # Cowork sandbox by every signal above: same `<session>/mnt/.remote-plugins`
    # shape, same `mnt/<folder>` mount, same hostname, no device tools. S5
    # therefore decided, the seat classified `legacy_cowork`, the identity was
    # never forwarded, and the fire's receipt stamped a container hostname.
    # The only thing that can tell them apart is the harness's own environment,
    # which the legacy Cowork sandbox never exported. Three candidates, any one
    # of which is enough:
    #
    #   * `CLAUDE_CODE_REMOTE=true` with ANY entrypoint. S1 keeps the exact
    #     `remote_cowork` pair as the strongest form; this is the same flag with
    #     a different entrypoint value (a scheduled task is not a Cowork tab).
    #   * a non-empty `CLAUDE_CODE_REMOTE_SESSION_ID`.
    #   * the account uuid WITHOUT `CLAUDE_PLUGIN_ROOT` — the merged app's Code
    #     tab carries the uuid and sets the plugin root natively, and it must
    #     stay `claude_code_local` (ACCESS1 review H-3), so the uuid alone only
    #     argues for the cloud when Claude Code is demonstrably not hosting it.
    entrypoint = env.get("CLAUDE_CODE_ENTRYPOINT")
    remote_session = str(env.get("CLAUDE_CODE_REMOTE_SESSION_ID", "") or "").strip()
    account_uuid = str(env.get("CLAUDE_CODE_ACCOUNT_UUID", "") or "").strip()
    signals["S10_merged_harness_env"] = bool(
        env.get("CLAUDE_CODE_REMOTE") == "true"
        or remote_session
        or (account_uuid and not str(env.get("CLAUDE_PLUGIN_ROOT", "") or "").strip())
    )
    signals["entrypoint"] = entrypoint if isinstance(entrypoint, str) and entrypoint else None

    # The NAMES of the harness variables this process can see, sorted. Names
    # only, never a value: the next fire's run log carries this line, and the
    # round after this one needs the exact variable set the sandbox exports
    # without any of them becoming a token in a transcript.
    signals["harness_env_names"] = harness_env_names(env)

    return signals


#: The prefixes whose variable NAMES are worth reporting: the harness's own and
#: this product's. A value is never read here.
_HARNESS_ENV_PREFIXES = ("CLAUDE_", "CR_")


def harness_env_names(env: Dict[str, str]) -> list:
    """Sorted NAMES of every `CLAUDE_*` / `CR_*` variable, and nothing else.

    This is the evidence line R-FIX3-1 asks for. It exists so the next fire's
    run log says which harness variables the scheduled sandbox exports, which
    is the one fact no seat has ever recorded. It answers names, so a variable
    holding a token can be reported without the token being reported.
    """
    try:
        return sorted(
            name for name in env
            if isinstance(name, str) and name.startswith(_HARNESS_ENV_PREFIXES)
        )
    except (TypeError, AttributeError):
        return []


def _mode_for_signal(key: str, signals: Dict[str, Any]) -> Optional[str]:
    """The mode a single signal argues for, or None when it is silent."""
    if not signals.get(key):
        return None
    if key in ("S1_remote_cowork_env", "S2_device_bash_tool",
               "S10_merged_harness_env", "S6_staged_brain_file",
               "S9_synced_registry"):
        return "merged_cloud"
    if key in ("S3_workspace_bash_tool", "S5_remote_plugins_dir"):
        return "legacy_cowork"
    if key in ("S4_desktop_scheduler_tool", "S7_claude_plugin_root"):
        return "claude_code_local"
    return None


#: Precedence, top first (§7.3.2). The first rule that fires decides; every
#: lower rule that names a DIFFERENT mode only sets `conflict`.
_PRECEDENCE = (
    "S1_remote_cowork_env",
    "S2_device_bash_tool",
    # S10 sits here on purpose (FIX3 F3-1): above the filesystem shapes, so the
    # merged harness's own environment beats a container that merely LOOKS like
    # the legacy sandbox, and below the two signals that are already certain.
    "S10_merged_harness_env",
    "S3_workspace_bash_tool",
    "S4_desktop_scheduler_tool",
    "S5_remote_plugins_dir",
    "S6_staged_brain_file",
    "S9_synced_registry",
    "S7_claude_plugin_root",
)


def _resolve_plugin_root(env: Dict[str, str], root, signals: Dict[str, Any]) -> Optional[str]:
    """The plugin root, in ONE order shared by every resolver in the product:

      1. `$CLAUDE_PLUGIN_ROOT` when Claude Code sets it natively;
      2. the SYNCED registry `/root/.claude/plugins/synced/<org>_<account>/cr/`
         — the merged environment;
      3. the legacy Cowork `<session>/mnt/.remote-plugins/plugin_*/` shape.

    That is the order Rule 22's prose states and the order
    `chat_output_renderer._resolve_plugin_root` walks (ENV1 fix round 1, review
    finding F-2 — stages 2 and 3 used to be swapped here, so on a box carrying
    BOTH shapes the shell and this module named different roots). Every
    candidate is CONTENT-ADDRESSED on `shared/scripts/chat_output_renderer.py`,
    the same rule guard G1 enforces: a bucket that is not the core plugin can
    never win a first-match coin flip."""
    plugin_env = env.get("CLAUDE_PLUGIN_ROOT") or ""
    if plugin_env and _is_dir(Path(plugin_env)):
        return plugin_env
    candidates = list(_glob(_root_path(root, SYNCED_REGISTRY), "*/*/" + PLUGIN_MARKER))
    session = signals.get("session_dir") or ""
    if session:
        candidates.extend(
            _glob(Path(session) / "mnt" / ".remote-plugins", "plugin_*/" + PLUGIN_MARKER)
        )
    for marker in candidates:
        try:
            return str(marker.parent.parent.parent)
        except (OSError, ValueError, IndexError):
            continue
    return None


def detect(*, env=None, root=Path("/"), tools=None) -> EnvReport:
    """Read the environment and say which one it is. Never raises."""
    try:
        environ = dict(os.environ) if env is None else dict(env)
    except (TypeError, ValueError):
        environ = {}
    names = _tool_names(tools)
    try:
        signals = _read_signals(environ, root, names)
    except Exception:  # noqa: BLE001 — a detector that raises is worse than "unknown"
        signals = {"error": "signal read failed"}

    override = signals.get("S8_override")
    if override:
        if override in MODES:
            mode, decided_by = override, "override"
        else:
            mode, decided_by = "unknown", "override_invalid"
        decided_rank = -1
    else:
        mode, decided_by, decided_rank = "unknown", "none", len(_PRECEDENCE)
        for rank, key in enumerate(_PRECEDENCE):
            argued = _mode_for_signal(key, signals)
            if argued is not None:
                mode, decided_by, decided_rank = argued, key.split("_", 1)[0], rank
                break

    conflict = False
    for rank, key in enumerate(_PRECEDENCE):
        if rank <= decided_rank:
            continue
        argued = _mode_for_signal(key, signals)
        if argued is not None and argued != mode:
            conflict = True

    plugin_root = None
    try:
        plugin_root = _resolve_plugin_root(environ, root, signals)
    except Exception:  # noqa: BLE001
        plugin_root = None

    brain_file = signals.get("staged_brain_path") if mode == "merged_cloud" else None
    local_fs = mode in ("legacy_cowork", "claude_code_local")
    clock_trust = "user_local" if local_fs else "untrusted"

    return EnvReport(
        mode=mode,
        decided_by=decided_by,
        conflict=conflict,
        signals=signals,
        plugin_root=plugin_root,
        brain_file=brain_file,
        local_workspace_fs=local_fs,
        host_clock_trust=clock_trust,
        python=platform.python_version(),
    )


# --------------------------------------------------------------------------
# per-surface policy
# --------------------------------------------------------------------------

#: What a surface may do in each mode when `shared/config/env_support.json` has
#: nothing to say about it (§0.34: unknown env → readers banner, writers refuse;
#: merged_cloud degrades until a skill is explicitly cleared).
_DEFAULT_POLICY = {
    "legacy_cowork": "full",
    "claude_code_local": "full",
    "merged_cloud": "degraded",
    "unknown": "refuse",
}
_POLICIES = ("full", "degraded", "refuse")


def _support_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "env_support.json"


def _load_support_config() -> Dict[str, Any]:
    """The gating table is DATA (G24 doctrine). The file belongs to another
    lane; a missing or malformed one must never take a surface down — it falls
    back to the conservative defaults above."""
    try:
        raw = _support_config_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def surface_policy(skill: str, mode: str) -> str:
    """"full" | "degraded" | "refuse" for one skill dir in one mode."""
    if mode not in MODES:
        return "refuse"
    data = _load_support_config()
    rows: Any = None
    for key in ("per_skill", "skills"):
        candidate = data.get(key)
        if isinstance(candidate, dict):
            rows = candidate
            break
    if rows is None:
        rows = data
    row = rows.get(skill) if isinstance(rows, dict) else None
    if isinstance(row, dict):
        value = row.get(mode)
        if value in _POLICIES:
            return value
    elif row in _POLICIES:
        return row
    defaults = data.get("defaults")
    if isinstance(defaults, dict) and defaults.get(mode) in _POLICIES:
        return defaults[mode]
    return _DEFAULT_POLICY[mode]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def shell_values(report: EnvReport) -> Dict[str, str]:
    """The exported name -> value map, before shell quoting.

    Separate from `shell_line` so a pin can compare this key set against
    `_SHELL_KEYS` and against the preamble's own `export` list. Renaming a key
    in one place and not the others then reds a NAMED check instead of raising
    a `KeyError` half way through the suite (ENV1 fix round 1, finding F-5)."""
    return {
        "CR_ENV": report.mode,
        "CR_PLUGIN_ROOT": report.plugin_root or "",
        "CR_BRAIN_FILE": report.brain_file or "",
        "CR_LOCAL_FS": "1" if report.local_workspace_fs else "0",
        "CR_CLOCK_TRUST": report.host_clock_trust,
    }


def shell_line(report: EnvReport) -> str:
    """The five keys the Rule 22 preamble exports, shell-quoted, on one line."""
    values = shell_values(report)
    return " ".join("{}={}".format(k, shlex.quote(values.get(k, "")))
                    for k in _SHELL_KEYS)


#: The keys `--signals` prints. Booleans, one entrypoint string, one list of
#: NAMES. No path is in this set and none may be added: the whole point of the
#: form is that a run log can carry it.
_SIGNALS_KEYS = (
    "S1_remote_cowork_env",
    "S2_device_bash_tool",
    "S3_workspace_bash_tool",
    "S4_desktop_scheduler_tool",
    "S5_remote_plugins_dir",
    "S6_staged_brain_file",
    "S7_claude_plugin_root",
    "S8_override",
    "S9_synced_registry",
    "S10_merged_harness_env",
    "entrypoint",
    "harness_env_names",
)


def signals_line(report: EnvReport) -> str:
    """ONE JSON line: the ten booleans, the entrypoint, the variable NAMES.

    This is what the bootloader prints beside `CR_ENV=` so a fire's run log
    says which harness variables the scheduled sandbox exports (R-FIX3-1). It
    is NAMES-ONLY by construction — `harness_env_names` never reads a value,
    and `_SIGNALS_KEYS` carries no path key, so a session directory, a staged
    brain path and a synced registry path cannot reach a transcript through it.
    """
    signals = dict(getattr(report, "signals", {}) or {})
    out = {"mode": report.mode, "decided_by": report.decided_by}
    for key in _SIGNALS_KEYS:
        value = signals.get(key)
        if key == "harness_env_names":
            out[key] = list(value or [])
        elif key in ("entrypoint", "S8_override"):
            out[key] = value if isinstance(value, str) and value else None
        else:
            out[key] = bool(value)
    return json.dumps(out, sort_keys=True, separators=(",", ":"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report which environment this process is running in.")
    parser.add_argument("--tools", default="",
                        help="comma-separated tool names this session can see")
    parser.add_argument("--root", default=None,
                        help="re-root the filesystem probes (fixtures only)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--shell", action="store_true",
                        help="print CR_ENV/CR_PLUGIN_ROOT/CR_BRAIN_FILE/CR_LOCAL_FS/"
                             "CR_CLOCK_TRUST for eval")
    parser.add_argument("--signals", action="store_true",
                        help="print ONE json line of the signal booleans, the "
                             "entrypoint and the harness variable NAMES "
                             "(no path, no value)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = args.root or os.environ.get(ROOT_ENV_VAR) or "/"
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    report = detect(root=Path(root), tools=tools)

    if args.shell:
        print(shell_line(report))
    elif args.signals:
        print(signals_line(report))
    elif args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print("environment: {} (decided by {}{})".format(
            report.mode, report.decided_by, ", signals disagree" if report.conflict else ""))
        print("plugin root: {}".format(report.plugin_root or "not resolved"))
        print("workspace on this filesystem: {}".format(
            "yes" if report.local_workspace_fs else "no"))
        print("host clock: {}".format(report.host_clock_trust))
    return 0


if __name__ == "__main__":
    sys.exit(main())
