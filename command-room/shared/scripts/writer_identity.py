#!/usr/bin/env python3
"""One writer identity, derived from the account and the workspace (ACCESS1).

THE PROBLEM, IN THE TREE. Command Room has TWO machine ids and they disagree.
`machine_identity.machine_id()` derives a token from the host's node name plus
its hardware address and persists it at `~/.command-room/machine_id` (receipts
carry that one). `writer_lock.machine_id()` mints a uuid4 and persists it at
`%LOCALAPPDATA%\\CommandRoom\\machine_id` / `~/.commandroom/machine_id` (the
writer-lock diagnostics carry that one). Two answers to one question is a bug
that only shows up in an incident, which is the worst time to find it.

THE PROBLEM, IN THE MERGED ENVIRONMENT. Both markers live in a home directory,
and in the sandbox VM the home is `/sessions/rcw-<id>` — a NEW directory every
session, wiped when the session ends (probe P4, `build/P1_RESULT_2026-09-19.md`:
`~` came back as two different `rcw-…` paths on two runs, and
`~/.command-room` did not exist). So every fire would mint a fresh identity,
and the one field whose whole job is telling two writers apart would name a
session instead of a writer.

THE ANSWER (gap analysis §0.14/§0.15, ruled). Derive the identity from what
actually stays the same — the ACCOUNT the fire runs under and the workspace
BASENAME it is writing — and persist it inside the workspace at
`_hq/.system/writer_identity.json`, which is the one storage that survives both
a wiped VM home and a move between machines.

    acct-<sha1(account[:org]:basename)[:12]>

WHAT IS NEVER WRITTEN DOWN. The account uuid itself never lands in the
workspace. The stored file carries the derived id, a digest of the derivation
inputs (so a changed account or a renamed folder is DETECTED without the
inputs being recoverable from it) and a timestamp. Nothing else.

BYTE-IDENTICAL ON LEGACY SEATS, BY CONSTRUCTION. `writer_id()` returns None
whenever there is no account uuid in the environment, whenever the environment
is a legacy Cowork sandbox, and whenever no workspace can be resolved — and
both existing `machine_id` functions fall through to exactly the code they run
today when it returns None. The v5.29.0 fleet therefore keeps its current
identities to the byte; the merged seats get the stable one.

3.10-safe, stdlib only: this runs in the sandbox VM.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

# Where the answer is kept. `_hq/.system/` is already plugin collateral inside
# the workspace (writer_lock.py:59-64, workspace_paths.py:79-82), so this adds
# a file to an established home rather than a new convention.
IDENTITY_REL = "_hq/.system/writer_identity.json"

ACCOUNT_ENV = "CLAUDE_CODE_ACCOUNT_UUID"
ORG_ENV = "CLAUDE_CODE_ORGANIZATION_UUID"
WORKSPACE_ENV = "CR_WORKSPACE"

#: THE IDENTITY CROSSES THE DOOR AS A PAIR (fix round 2, ruling R-FIX-5).
#: The account uuid lives in the CONTAINER. The helper child that writes the
#: receipt runs on the device host, with the device host's environment, and the
#: gate walk of 2026-09-20 proved the cost: `writer_id()` fell through to None
#: on every write, so `data.machine` carried a per-session marker token on five
#: receipts and the container hostname on the fire, and the identity file was
#: never created. The container therefore DERIVES the id and forwards the
#: derived id plus the derivation digest on the rendered command
#: (`workspace_access.plan`), and the uuid itself never appears in a rendered
#: command, a transcript or the workspace.
FORWARDED_ID_ENV = "CR_WRITER_ID"
FORWARDED_DERIVATION_ENV = "CR_WRITER_DERIVATION"

PREFIX = "acct-"
_ID_HEX = 12

#: What a forwarded pair must look like to be believed. Anything else is
#: IGNORED — the caller falls through to today's None and the seat keeps its
#: current identity, which is what stops a mangled paste from minting an id.
_FORWARDED_ID_RE = re.compile(r"^" + PREFIX + r"[0-9a-f]{%d}$" % _ID_HEX)
_FORWARDED_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _env(env: Optional[Dict[str, str]]) -> Dict[str, str]:
    return dict(os.environ) if env is None else dict(env)


def _account(env: Dict[str, str]) -> Optional[str]:
    value = str(env.get(ACCOUNT_ENV, "")).strip()
    return value or None


#: The `env_detect` modes whose seats keep today's identities BYTE FOR BYTE
#: (SPEC_NIGHTM1 §2 item 6). The gate is the MODE, not the presence of an
#: account uuid: the merged app's Code tab is a `claude_code_local` seat that
#: exports one, and gating on the variable flipped both machine ids there
#: mid-stream (review finding H-3).
#:
#: NARROWED 2026-09-21 (FIX3 F3-1). `legacy_cowork` left the list. The true
#: legacy Cowork sandbox never exported `CLAUDE_CODE_ACCOUNT_UUID`, so on a
#: real legacy seat `_account()` is None and every caller falls through to
#: exactly the code it runs today — byte-identical, which is the fence, and
#: pinned by the legacy byte-identity control. What the removal changes is the
#: seat the re-walk found: a merged-app container that LOOKS like the legacy
#: sandbox on every filesystem signal, carries the account uuid, and was
#: therefore refused an identity it had every input for. `env_detect`'s S10
#: reclassifies that seat, and this list stops being the second place the same
#: seat is turned away.
LEGACY_ID_MODES = ("claude_code_local",)


def detected_mode(env: Dict[str, str]) -> Optional[str]:
    """ENV1's mode name, or None when it cannot be read.

    `env_detect` is ENV1's module and may not be on an older runtime, so the
    import is guarded. It answers with an `EnvReport` DATACLASS; the dict and
    str branches are the fallbacks for a runtime whose `env_detect` predates it
    (review finding B-1 — reading only those two shapes made this helper answer
    None for every mode, so the one fence that holds the un-merged fleet
    byte-identical was inert).
    """
    try:
        import env_detect  # type: ignore
    except Exception:
        return None
    try:
        report = env_detect.detect(env=env)  # type: ignore[call-arg]
    except TypeError:
        try:
            report = env_detect.detect()  # type: ignore[misc]
        except Exception:
            return None
    except Exception:
        return None
    mode = getattr(report, "mode", None)
    if isinstance(mode, str) and mode:
        return mode
    if isinstance(report, dict):
        mode = report.get("mode")
    elif isinstance(report, str):
        mode = report
    return mode if isinstance(mode, str) and mode else None


def _keeps_legacy_identity(env: Dict[str, str]) -> bool:
    """True on a seat whose existing machine ids must not change.

    `unknown` and "no `env_detect` at all" are NOT in the list: the sandbox VM
    runs the helpers with none of ENV1's signals in reach and reads `unknown`,
    and that VM is exactly the seat the account-derived id exists for.
    """
    return detected_mode(env) in LEGACY_ID_MODES


def _resolve_root(workspace_root, env: Dict[str, str]) -> Optional[Path]:
    """The workspace this process is writing, or None.

    Explicit argument wins, then `CR_WORKSPACE`, then a walk up from the cwd.
    Never a downward search: an identity resolver must be cheap enough to sit
    on the receipt path, and it is only reached at all when an account uuid is
    present.
    """
    candidates = []
    if workspace_root:
        candidates.append(Path(workspace_root))
    env_ws = str(env.get(WORKSPACE_ENV, "")).strip()
    if env_ws:
        candidates.append(Path(env_ws).expanduser())
    for candidate in candidates:
        try:
            if (candidate / "_hq" / "data" / "entities.json").is_file():
                return candidate.resolve()
        except OSError:
            continue
    try:
        here = Path.cwd().resolve()
        for d in (here, *here.parents):
            if (d / "_hq" / "data" / "entities.json").is_file():
                return d
    except OSError:
        pass
    return None


def derive(account: str, basename: str, org: Optional[str] = None) -> str:
    """`acct-<sha1(...)[:12]>` — the derivation, in one place.

    The org uuid joins the input when the environment carries one, so two
    workspaces with the same basename under different organizations cannot
    collapse onto one id.
    """
    parts = [account]
    if org:
        parts.append(org)
    parts.append(basename)
    raw = ":".join(parts)
    return PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:_ID_HEX]


def _derivation_digest(account: str, basename: str, org: Optional[str]) -> str:
    """A full digest of the SAME inputs, stored so a change is detectable.

    Not the id (which is truncated) and not the inputs (which are private): the
    file must be able to say "these are no longer the inputs I was derived
    from" without ever being able to say what they were.
    """
    parts = [account]
    if org:
        parts.append(org)
    parts.append(basename)
    return hashlib.sha256(("derivation:" + ":".join(parts)).encode("utf-8")).hexdigest()


def identity_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / ".system" / "writer_identity.json"


def _read_stored(path: Path) -> Optional[Dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _persist(path: Path, payload: Dict[str, object]) -> None:
    """Best effort, through the canonical JSON writer. An identity that cannot
    be persisted is still the right answer — the derivation is what makes it
    stable, and the file is the fast path, not the source of truth.

    NOT UNDER THE HELPER DOOR (MF-M2-21, 2026-09-20). On a merged seat with
    an account uuid in the environment and no cached file, an allow-listed
    READ — `inbox_helpers:plan_fire_receipt`, four frames up through
    `receipts.machine_fields` — landed `_hq/.system/writer_identity.json`
    inside the customer's workspace. The id is derived, as the sentence above
    already says, so skipping the cache under `CR_HELPER_DOOR` changes no
    answer at all; it only stops a read from minting a file. The test sits
    INSIDE the `try` (MF-M2-25) so the best-effort contract holds on the
    door's side too."""
    try:
        if os.environ.get("CR_HELPER_DOOR"):
            return
        import datetime as _dt
        payload = dict(payload)
        payload["updated"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from atomic_write import atomic_write_json  # type: ignore
        except ImportError:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from atomic_write import atomic_write_json  # type: ignore
        atomic_write_json(path, payload)
    except Exception:
        pass


#: The two placeholders the scheduled-task bootloader carries for the writer
#: pair the REGISTERING seat bakes in (IDENT1 I-9; coordinator decision D-1).
WRITER_ID_PLACEHOLDER = "<WRITER_ID>"
WRITER_DERIVATION_PLACEHOLDER = "<WRITER_DERIVATION>"

#: The one sentence a malformed pair is refused with - never a blank export.
BAD_PAIR_LINE = ("The writer id and its derivation must be given together, as "
                 "an acct- id with twelve hex characters and a sixty-four "
                 "character digest.")


def bakeable_pair(workspace, env: Optional[Dict[str, str]] = None):
    """`(writer_id, derivation)` THIS seat may bake into a bootloader, or None.

    RETIRE1's reviewer, H-1 (2026-09-23). The first cut of I-9 read the pair
    from `_hq/.system/writer_identity.json` - a file that is Drive-synced to
    every computer sharing the workspace. Once the un-merged PC updated, ITS
    registrations would have baked the merged seat's id, passed the scheduled-
    writer question and written under that identity: SAFETY0 switched off, and
    the exact confusion coordinator decision D-1 warns against.

    So the pair is DERIVED here, from THIS process's own account uuid and the
    workspace's folder name, exactly as `writer_id()` derives it - and when the
    workspace's stored pair is readable it must EQUAL the derived one (id and
    digest), or nothing is baked. No account in this environment (every legacy
    and local seat), a seat whose mode keeps today's identities, or a stored
    pair that belongs to another account -> None, and the bootloader's two
    export lines are removed. `workspace` is the folder (its name is the
    derivation input); it need not be readable from this process."""
    env = _env(env)
    account = _account(env)
    if account is None or _keeps_legacy_identity(env):
        return None
    try:
        text = str(workspace or "").strip().rstrip("/\\")
    except Exception:  # noqa: BLE001
        return None
    if not text:
        return None
    basename = text.replace("\\", "/").rsplit("/", 1)[-1]
    org = str(env.get(ORG_ENV, "")).strip() or None
    ident = derive(account, basename, org)
    digest = _derivation_digest(account, basename, org)
    try:
        stored_path = identity_path(Path(text))
        stored = _read_stored(stored_path) if stored_path.is_file() else None
    except OSError:
        stored = None
    if stored is not None and (stored.get("writer_id") != ident
                               or stored.get("derivation") != digest):
        return None
    return ident, digest


def bake_pair(body: str, writer_id: Optional[str] = None,
              derivation: Optional[str] = None) -> str:
    """The bootloader body with this seat's writer pair baked in, or with the
    pair's lines REMOVED when the seat has none.

    Identity by HANDING (D-1): a scheduled fire in the legacy sandbox carries
    no account, so the only way its receipts can name the customer is the pair
    the registering seat bakes into its prompt. Both or neither: a half pair,
    or a value that is not the shape `forwarded_pair` accepts, is a refusal
    (`ValueError(BAD_PAIR_LINE)`), never a blank export - and a `<WRITER_...>`
    placeholder never ships, so with no pair every line carrying one goes."""
    if writer_id is None and derivation is None:
        keep = [ln for ln in str(body).split("\n")
                if WRITER_ID_PLACEHOLDER not in ln
                and WRITER_DERIVATION_PLACEHOLDER not in ln]
        return "\n".join(keep)
    ident = str(writer_id or "").strip()
    digest = str(derivation or "").strip()
    if not _FORWARDED_ID_RE.match(ident) or not _FORWARDED_DIGEST_RE.match(digest):
        raise ValueError(BAD_PAIR_LINE)
    return (str(body).replace(WRITER_ID_PLACEHOLDER, ident)
            .replace(WRITER_DERIVATION_PLACEHOLDER, digest))


def forwarded_pair(env: Optional[Dict[str, str]] = None):
    """`(writer_id, derivation digest)` from the environment, or None.

    Shape-checked, never trusted on looks alone: the id is `acct-` plus twelve
    hex, the digest is sixty-four hex. Anything else — a truncated paste, a
    stray quote, a value the shell ate — is not a pair, and the caller keeps
    today's behaviour instead of writing a made-up identity into append-only
    history.
    """
    env = _env(env)
    ident = str(env.get(FORWARDED_ID_ENV, "")).strip()
    digest = str(env.get(FORWARDED_DERIVATION_ENV, "")).strip()
    if not ident or not digest:
        return None
    if not _FORWARDED_ID_RE.match(ident) or not _FORWARDED_DIGEST_RE.match(digest):
        return None
    return ident, digest


def _forwarded_identity(workspace_root, env: Dict[str, str]) -> Optional[str]:
    """The forwarded identity, cached into the workspace the same way a derived
    one is — except under the helper door, where `_persist` already holds its
    record (R-M2-6: a read helper returns evidence and writes nothing)."""
    pair = forwarded_pair(env)
    if pair is None:
        return None
    if _keeps_legacy_identity(env):
        return None
    ident, digest = pair
    root = _resolve_root(workspace_root, env)
    if root is not None:
        path = identity_path(root)
        stored = _read_stored(path)
        if not (stored and stored.get("writer_id") == ident
                and stored.get("derivation") == digest):
            _persist(path, {"writer_id": ident, "derivation": digest})
    return ident


def writer_id(workspace_root=None, *, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """The stable writer id for this account + workspace, or None.

    None means "this seat's existing identity applies" — no account uuid, a
    seat whose MODE keeps today's ids (`legacy_cowork` or `claude_code_local`,
    §2 item 6), or no resolvable workspace. Callers fall through to their
    current behaviour on None, which is what keeps the un-merged fleet
    byte-identical.
    """
    env = _env(env)
    account = _account(env)
    if account is None:
        # No uuid here — but the CONTAINER had one, and may have forwarded what
        # it derived. A forwarded pair IS the identity, stored and compared
        # exactly as a derived one; a malformed pair is ignored, so this seat
        # falls through to today's None rather than minting anything.
        return _forwarded_identity(workspace_root, env)
    if _keeps_legacy_identity(env):
        return None
    root = _resolve_root(workspace_root, env)
    if root is None:
        return None
    org = str(env.get(ORG_ENV, "")).strip() or None
    basename = root.name
    expected = derive(account, basename, org)
    digest = _derivation_digest(account, basename, org)

    path = identity_path(root)
    stored = _read_stored(path)
    if stored and stored.get("writer_id") == expected and stored.get("derivation") == digest:
        return expected
    # Either nothing is stored yet, or the account / folder changed under it.
    # Re-derive and re-write: a wiped VM home never mints a new id, and a moved
    # workspace does not keep claiming the identity it had somewhere else.
    _persist(path, {"writer_id": expected, "derivation": digest})
    return expected


def derivation_digest(account: str, basename: str, org: Optional[str] = None) -> str:
    """The public name for the digest the container forwards beside the id."""
    return _derivation_digest(account, basename, org)


__all__ = [
    "ACCOUNT_ENV",
    "FORWARDED_DERIVATION_ENV",
    "FORWARDED_ID_ENV",
    "IDENTITY_REL",
    "LEGACY_ID_MODES",
    "ORG_ENV",
    "PREFIX",
    "derivation_digest",
    "derive",
    "detected_mode",
    "forwarded_pair",
    "identity_path",
    "writer_id",
]
