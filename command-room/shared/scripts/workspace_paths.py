#!/usr/bin/env python3
"""
Persisted file-pointers are WORKSPACE-RELATIVE (SPEC BRIEFMERGE §C).

WHY
---

A path that is correct where it was WRITTEN is not correct where it is READ.
The Command Room workspace is Drive-synced across machines and also mounts
inside Cowork's per-session sandbox, so the SAME logical folder resolves under
three different absolute roots depending on where a fire happened to run:

  1. a session mount   — `/sessions/<session-slug>/mnt/<workspace>/_hq/...`
     (dead the moment that session ends; a scheduled fire in a cloud session
     writes pointers that are already invalid when the customer reads them);
  2. one machine's user root — `<Drive>:\\Users\\<name>\\...\\<workspace>\\_hq\\...`;
  3. the OTHER machine's user root — same shape, different `<name>`, and the
     file does not exist at that path on this computer.

Every attachment card or link built from one of those is valid only on the
generating machine during the generating session. Field-verified 2026-08-08:
the FILES were all present in the synced `_hq/meetings/` folder; the POINTERS
had rotted, and the customer got "This file can't be found on your computer."

THE CONTRACT
------------

  * **Writer side.** Nothing this plugin persists into a substrate record may
    carry an absolute path. `assert_workspace_relative()` refuses one at write
    time — drift is a defect the moment it is written, not months later when a
    reader trips on it. The floor is the spec's own regex,
    `PERSISTED_ABSOLUTE_RE` (`^([A-Za-z]:\\|/sessions/)`); `is_absolute_pointer`
    is the wider net (POSIX host roots, UNC, forward-slashed drive letters)
    because those rot the same way.

  * **Reader side.** `normalize_persisted_path()` resolves a stored value to
    the workspace-relative form at READ time, stripping the legacy absolute
    prefixes from rows written before this build. History is NEVER rewritten:
    events.jsonl is append-only, so back-compat lives read-side forever — the
    same posture `receipts.py` takes on legacy task-id spellings.

  * **Attach honestly.** `attach_line()` renders a workspace-relative markdown
    link when the file is actually there and the `syncing` sentence when it is
    not (Drive sync lag). A dead card is worse than an honest line.

  * **Post absolute, persist relative (SPEC BRIEFFIX1 Item A).** The two sides
    are NOT the same string and never were. What gets PERSISTED (a receipt
    pointer, the saved digest snapshot) stays workspace-relative — that is what
    makes one file mean one file on every machine. What gets POSTED into chat
    has to be machine-ABSOLUTE, because Cowork's `computer://` opener resolves
    against this computer's filesystem and a relative href is a card that says
    "this file can't be found on your computer" while the file sits in the
    synced folder (field-verified 2026-08-09). `machine_absolute()` is the
    conversion, keyed to the fire-time workspace root and routed through the
    same anchor machinery as everything else here — never a string concat.

WHY THE NORMALIZER MATCHES SHAPES, NOT PATHS
--------------------------------------------

The three observed roots are two SHAPES (a session mount, and a host-absolute
root) instantiated three times. Hard-coding the three literal roots would fix
exactly the three machines that produced them and nothing else — a fourth
machine, a renamed home folder or a new session slug would rot silently again.
So the normalizer keys on the WORKSPACE ANCHOR (`_hq/`, the segment every
persisted pointer this plugin writes passes through) and takes the remainder
from there. That is machine-independent by construction, and it means no real
machine path ever has to be written down in this file.

Stdlib only. Pure functions except the `exists` probes, which stat the disk.
"""
from __future__ import annotations

import os
import re
from typing import Optional


# The workspace-internal segment every persisted file-pointer passes through.
# `_hq/` is the plugin's one deliverable root (CONTRACT.md Rule 3: briefs to
# `_hq/meetings/`, digests to `_hq/briefings/`, system artifacts to
# `_hq/.system/`). A pointer that does not pass through it is not something
# this module can resolve, and it says so rather than guessing.
WORKSPACE_ANCHORS: tuple = ("_hq",)

# The file-pointer fields this plugin persists into substrate records. Kept as
# data so the writers and the guard-tier sweep read one list.
POINTER_FIELDS: tuple = (
    "brief_path",
    "digest_path",
    "prep_path",
    "artifact_path",
    "deliverable_path",
    "output_path",
    "pack_path",
    # WALKFIX1 Item C. `doc` does not end in `_path`, which is exactly why the
    # BRIEFMERGE §C sweep missed it and why `visual_gate.log_visual_gate`
    # persisted a `/sessions/...` root into two live events. The membership
    # test for this tuple is "does this key carry a file pointer", never "does
    # the name look like one".
    "doc",
    # Same build, same sweep: `recover_corruption` already persisted this one
    # relative, so nothing was broken — but the reusable sweep could not SEE
    # it, and an unswept-but-correct writer is one edit away from an unswept
    # incorrect one.
    "quarantine_file",
)

# The generated-deliverable file types CONTRACT.md Rule 3 governs — the ones a
# skill produces and the customer is meant to OPEN. Kept as data because two
# things key on it: the post-time conversion (which of a payload's links need
# absolutizing) and the fence that refuses a chat payload still carrying the
# relative form. A source-citation URL is not in this set and never gets
# touched.
DELIVERABLE_SUFFIXES: tuple = (".docx", ".pdf", ".xlsx", ".pptx")

# SPEC BRIEFMERGE §C, verbatim: the writer-side refusal floor. Kept as its own
# constant so the pin can assert the literal contract rather than a paraphrase.
PERSISTED_ABSOLUTE_RE = re.compile(r"^([A-Za-z]:\\|/sessions/)")

# The three legacy shapes, by name. Order matters: the session mount is
# checked first because it is also POSIX-absolute.
SHAPE_RELATIVE = "relative"
SHAPE_SESSION_MOUNT = "session_mount"
SHAPE_WINDOWS_ABSOLUTE = "windows_absolute"
SHAPE_POSIX_ABSOLUTE = "posix_absolute"
SHAPE_UNRESOLVABLE = "unresolvable"
SHAPE_EMPTY = "empty"

LEGACY_ABSOLUTE_SHAPES: tuple = (
    SHAPE_SESSION_MOUNT,
    SHAPE_WINDOWS_ABSOLUTE,
    SHAPE_POSIX_ABSOLUTE,
)

# `/sessions/<slug>/mnt/<workspace-basename>/` — Cowork's per-session sandbox
# mount. The slug is machine-generated and never reproduced here.
_SESSION_MOUNT_RE = re.compile(r"^/sessions/[^/]+/mnt/[^/]+/")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_UNC_RE = re.compile(r"^\\\\[^\\]+\\")

# A URL scheme (`computer://`, `https://`, `mailto:`) — TWO or more characters
# before the colon, so a Windows drive letter (`C:`) is not read as one. A
# value carrying a scheme is already a URL: it is not a workspace-relative
# pointer, and the post-time conversion must leave it exactly as it found it
# (converting an already-converted link is how a double-prefixed href happens).
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")

# The one sentence the brief says when the pointer resolves but the file is
# not on this machine yet. Pinned: a dead card is the failure this replaces,
# and a paraphrase ("file missing", "couldn't open") reads as breakage.
# Platform-neutral since v5.11.1 (BUG-8538): the previous wording named
# "Drive", which read as a lie to a customer whose workspace syncs through
# OneDrive / SharePoint.
SYNCING_TEXT = "syncing — open from your cloud drive"


# Why a pointer could not be made portable. These are REASONS, not statuses:
# they ride the receipt so a degrade is explicable months later, and they are
# what `resolve_pointer` returns instead of an empty string nobody can
# interpret (BRIEFMERGE review F-6b).
REASON_NO_ANCHOR = (
    "absolute path passes through no workspace anchor "
    f"({'/'.join(WORKSPACE_ANCHORS)}/) — nothing here can make it portable"
)
REASON_AMBIGUOUS_ANCHOR = (
    "absolute path passes through the workspace anchor more than once and no "
    "workspace root was supplied — the readings are indistinguishable, and a "
    "confidently wrong path is a wrong file"
)
REASON_EMPTY = "no pointer value"


class AbsolutePathError(ValueError):
    """A persisted file-pointer carried an absolute path. Raised at WRITE
    time by `assert_workspace_relative` — the value never reaches the
    substrate, so no reader on another machine ever sees a dead pointer."""


class UnresolvablePointerError(ValueError):
    """A stored pointer names nothing this workspace can resolve. Raised only
    by the explicit `..._strict` reader — the rendering path never raises,
    because a health read must never break a fire. Carries the REASON, so a
    caller degrading on it can put that reason on the receipt instead of the
    empty string that used to come back (review F-6b: an empty string is a
    value downstream code can mistake for a valid one)."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason or message


# ---------------------------------------------------------------------------
# Writer side
# ---------------------------------------------------------------------------

def _norm_sep(value) -> str:
    """Forward-slash form, whitespace-stripped. Non-strings become ''."""
    if not isinstance(value, str):
        return ""
    return value.strip().replace("\\", "/")


def is_absolute_pointer(value) -> bool:
    """True when `value` is machine- or session-bound in ANY of the observed
    shapes: a drive letter (either separator), a UNC share, or a leading `/`.

    Wider than `PERSISTED_ABSOLUTE_RE` on purpose — the spec's floor names the
    two shapes that were caught in the field, and a POSIX host root
    (`/Users/<name>/...`, a macOS workspace) rots identically.
    """
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if not raw:
        return False
    if PERSISTED_ABSOLUTE_RE.match(raw):
        return True
    if _UNC_RE.match(raw):
        return True
    if _DRIVE_RE.match(raw):
        return True
    return _norm_sep(raw).startswith("/")


def assert_workspace_relative(value, *, field: str = "path") -> str:
    """Return `value` unchanged, or raise `AbsolutePathError`.

    Call this on EVERY file-pointer field on its way into a substrate record.
    An empty/None value passes (an absent pointer is not a rotten one) —
    refusing it here would push callers into writing a placeholder path.

    >>> assert_workspace_relative("_hq/meetings/Call_Prep_sample_2026-01-02.docx")
    '_hq/meetings/Call_Prep_sample_2026-01-02.docx'
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return value
    if not isinstance(value, str):
        raise AbsolutePathError(f"{field} must be a string path; got {value!r}")
    if is_absolute_pointer(value):
        raise AbsolutePathError(
            f"{field} must be workspace-relative (e.g. '_hq/meetings/<file>.docx'); "
            f"got the absolute path {value!r}. An absolute path is valid only on "
            f"the machine and session that wrote it — persist relative, resolve "
            f"at render time via workspace_paths.normalize_persisted_path()."
        )
    return value


def to_workspace_relative(value, workspace_root=None) -> str:
    """The WRITER's converter: absolute (or already-relative) → the
    workspace-relative form to persist.

    Resolution order:
      1. already relative → normalized and returned;
      2. the value passes through a workspace anchor (`_hq/`) → the remainder
         from that anchor, which is machine-independent;
      3. `workspace_root` is supplied and the value sits under it → the
         remainder after the root;
      4. otherwise "" — the caller has a pointer this module cannot make
         portable, and an empty value is an honest absent one, never a
         silently-persisted absolute.
    """
    return normalize_persisted_path(value, workspace_root=workspace_root)


# ---------------------------------------------------------------------------
# Reader side — normalization of legacy rows (never a history rewrite)
# ---------------------------------------------------------------------------

def _anchor_offsets(norm: str) -> list:
    """Every offset in `norm` at which a workspace-anchor SEGMENT begins.

    Segment, not substring: `/_hq/` and a leading `_hq/` count; `my_hq/` does
    not. Returned ascending, so `len()` is the ambiguity test and `[0]` /
    `[-1]` are the two candidate readings.
    """
    offsets = []
    for anchor in WORKSPACE_ANCHORS:
        if norm.startswith(anchor + "/"):
            offsets.append(0)
        needle = "/" + anchor + "/"
        start = 0
        while True:
            idx = norm.find(needle, start)
            if idx < 0:
                break
            offsets.append(idx + 1)
            start = idx + 1
    return sorted(set(offsets))


def _anchor_split(norm: str) -> Optional[str]:
    """The remainder starting at the workspace anchor, or None when there is
    no anchor — and None ALSO when there is more than one (review F-6a).

    The doubled-anchor case has two readings and no way to choose between them
    without a workspace root: `<root>/_hq/x/<ws>/_hq/meetings/f` wants the LAST
    anchor, `<ws>/_hq/meetings/_hq/note` wants the FIRST, and the string alone
    cannot tell you which shape you are holding. Picking one silently means
    that half the time the reader resolves to a path that is confidently wrong
    — a different file, or a file that does not exist and renders as forever
    syncing. So the heuristic refuses, the caller degrades with a stated
    reason, and the exact answer is available whenever a workspace root IS
    supplied (see `normalize_persisted_path`, which strips the root first).
    """
    offsets = _anchor_offsets(norm)
    if len(offsets) != 1:
        return None
    return norm[offsets[0]:]


def classify_pointer(value) -> str:
    """Which shape a stored pointer is in — one of the SHAPE_* constants.

    The three legacy absolute shapes are named separately so a reader can
    report WHICH rot it just repaired, and so the pin can prove all three
    normalize to the same relative path rather than asserting on one.
    """
    if not isinstance(value, str) or not value.strip():
        return SHAPE_EMPTY
    raw = value.strip()
    norm = _norm_sep(raw)
    if _SESSION_MOUNT_RE.match(norm):
        return SHAPE_SESSION_MOUNT
    if _DRIVE_RE.match(raw) or _UNC_RE.match(raw):
        return SHAPE_WINDOWS_ABSOLUTE
    if norm.startswith("/"):
        return SHAPE_POSIX_ABSOLUTE
    return SHAPE_RELATIVE


def _resolve(value, workspace_root=None) -> tuple:
    """(relative, reason). `relative` is "" exactly when `reason` is set."""
    if not isinstance(value, str) or not value.strip():
        return "", REASON_EMPTY
    norm = _norm_sep(value)
    while norm.startswith("./"):
        norm = norm[2:]
    shape = classify_pointer(value)

    if shape == SHAPE_RELATIVE:
        return norm.lstrip("/"), ""

    if shape == SHAPE_SESSION_MOUNT:
        # The mount prefix is an EXACT shape, so whatever follows it IS the
        # workspace-relative path. Do not re-anchor the remainder: a file
        # legitimately living under `_hq/meetings/_hq/` would lose its
        # intermediate segments to the anchor heuristic (review F-6a).
        return _SESSION_MOUNT_RE.sub("", norm, count=1), ""

    # Host-absolute (Windows drive, UNC, or POSIX user root).
    #
    # ROOT FIRST (review F-6a). When this machine's workspace root is known
    # and the stored value sits under it, the answer is exact and no heuristic
    # is needed — including for the doubled-anchor shapes the heuristic
    # refuses. The anchor heuristic below exists for the case this build is
    # actually about: a row written under a DIFFERENT root, where no exact
    # answer is available.
    if workspace_root:
        root = _norm_sep(str(workspace_root)).rstrip("/")
        if root and norm.lower().startswith(root.lower() + "/"):
            return norm[len(root) + 1:], ""

    anchored = _anchor_split(norm)
    if anchored:
        return anchored, ""
    if len(_anchor_offsets(norm)) > 1:
        return "", REASON_AMBIGUOUS_ANCHOR
    return "", REASON_NO_ANCHOR


def normalize_persisted_path(value, *, workspace_root=None) -> str:
    """READ-side normalizer: any stored pointer → the workspace-relative form.

    All three legacy absolute shapes resolve to the SAME relative path, which
    is the whole point — the row a cloud session wrote, the row one machine
    wrote and the row the other machine wrote all name one file.

    Returns "" when the value names nothing this workspace can resolve. Use
    `resolve_pointer` (or `normalize_persisted_path_strict`) when you need the
    REASON rather than a bare empty string — a caller that degrades on this
    should be able to say why. It never fabricates a path and it never writes
    the repair back: events.jsonl is append-only history and normalization is
    a read-time concern, forever.
    """
    return _resolve(value, workspace_root)[0]


def normalize_persisted_path_strict(value, *, workspace_root=None) -> str:
    """`normalize_persisted_path`, but RAISES `UnresolvablePointerError`
    (carrying the reason) instead of returning "".

    For write-adjacent callers that must not let an unresolvable pointer flow
    onward as an empty string — the shape a downstream reader can mistake for
    a legitimately absent value. Rendering paths keep the non-raising form:
    a read must never break a fire.
    """
    relative, reason = _resolve(value, workspace_root)
    if relative:
        return relative
    raise UnresolvablePointerError(
        f"cannot resolve {value!r} to a workspace-relative path: {reason}", reason
    )


def resolve_pointer(value, workspace_root=None) -> dict:
    """The full read-time resolution of one persisted pointer.

    Returns `{raw, shape, relative, reason, absolute, exists, normalized}`:
      * `shape`      — which of the SHAPE_* forms the stored value was in;
      * `relative`   — the workspace-relative path ("" when unresolvable);
      * `reason`     — WHY it is unresolvable ("" when it resolved). Never let
                       a caller degrade on a bare empty string: the reason is
                       what makes the degrade explicable on the receipt months
                       later (review F-6b);
      * `absolute`   — `relative` resolved against THIS machine's workspace
                       root (None without a root);
      * `exists`     — whether that file is on this disk right now (False
                       without a root, or when the sync hasn't landed);
      * `normalized` — True when a legacy absolute shape was repaired on read.
    """
    shape = classify_pointer(value)
    relative, reason = _resolve(value, workspace_root)
    if not relative:
        shape = SHAPE_UNRESOLVABLE if shape != SHAPE_EMPTY else SHAPE_EMPTY
    absolute = None
    exists = False
    if relative and workspace_root:
        root = _norm_sep(str(workspace_root)).rstrip("/")
        absolute = f"{root}/{relative}"
        try:
            exists = os.path.isfile(absolute)
        except OSError:
            exists = False
    return {
        "raw": value if isinstance(value, str) else "",
        "shape": shape,
        "relative": relative,
        "reason": reason,
        "absolute": absolute,
        "exists": exists,
        "normalized": shape in LEGACY_ABSOLUTE_SHAPES,
    }


# ---------------------------------------------------------------------------
# Attach honestly
# ---------------------------------------------------------------------------

def attach_line(label: str, value, workspace_root=None) -> str:
    """ONE line naming a deliverable: a workspace-relative markdown link when
    the file is on this machine, the `syncing` sentence when it is not.

    The link target is the RELATIVE path (per the source-link convention) so
    the same rendered line means the same file on every machine — an absolute
    `computer://` target is exactly what rotted.

    >>> attach_line("Prep", "_hq/meetings/Call_Prep_sample_2026-01-02.docx")
    'Prep — syncing — open from your cloud drive'
    """
    text = str(label or "").strip() or "Brief"
    res = resolve_pointer(value, workspace_root)
    if res["relative"] and res["exists"]:
        return f"[{text}]({res['relative']})"
    return f"{text} — {SYNCING_TEXT}"


# ---------------------------------------------------------------------------
# Post-time conversion (SPEC BRIEFFIX1 Item A)
# ---------------------------------------------------------------------------

def is_workspace_relative_doc(value) -> bool:
    """True when `value` is a WORKSPACE-RELATIVE pointer at a generated
    deliverable — the exact shape that must never reach chat as an href.

    Three conditions, all required: not absolute (an absolute pointer is the
    OTHER bug and `assert_workspace_relative` owns it), passes through a
    workspace anchor (`_hq/` — a bare `notes.docx` names nothing this module
    can resolve and is not this fence's business), and carries a deliverable
    suffix. A `.md`, a `.json` or a source URL is untouched: Rule 3's
    `computer://` surface is for documents the customer opens.

    >>> is_workspace_relative_doc("_hq/meetings/Call_Prep_sample_2026-01-02.docx")
    True
    >>> is_workspace_relative_doc("_hq/briefings/morning-2026-01-02.md")
    False
    >>> is_workspace_relative_doc("C:/ws/_hq/meetings/x.docx")
    False
    >>> is_workspace_relative_doc("computer://C:\\\\ws\\\\_hq\\\\meetings\\\\x.docx")
    False
    """
    if not isinstance(value, str) or not value.strip():
        return False
    if is_absolute_pointer(value) or _SCHEME_RE.match(value.strip()):
        return False
    norm = _norm_sep(value)
    while norm.startswith("./"):
        norm = norm[2:]
    if not norm.lower().endswith(DELIVERABLE_SUFFIXES):
        return False
    return bool(_anchor_offsets(norm))


def machine_absolute(value, workspace_root) -> str:
    """A stored pointer → the path on THIS machine, or "" when it cannot be
    made one.

    This is the post-time half of the contract. It resolves the pointer to its
    workspace-relative form FIRST (so a legacy absolute row written under some
    other root normalizes through the same anchor machinery every reader uses)
    and only then joins it to the root it was handed. Never a string concat
    against the raw stored value: that is how a row written on the other
    computer becomes a confidently wrong path here.

    Returns "" — never a guess — when the pointer is unresolvable or no root
    was supplied. An empty answer is what lets the caller degrade honestly;
    a fabricated absolute path is a dead card with extra steps.

    >>> machine_absolute("_hq/meetings/x.docx", "/ws")
    '/ws/_hq/meetings/x.docx'
    >>> machine_absolute("_hq/meetings/x.docx", None)
    ''
    """
    if not workspace_root:
        return ""
    relative, _reason = _resolve(value, workspace_root)
    if not relative:
        return ""
    root = _norm_sep(str(workspace_root)).rstrip("/")
    if not root:
        return ""
    return f"{root}/{relative}"


def scan_events_for_absolute_pointers(events, *, fields=None) -> list:
    """Every persisted pointer in `events` that the WRITER FENCE would have
    refused — the guard-tier sweep over fixture-written events.

    The predicate is `is_absolute_pointer`, deliberately the same one
    `assert_workspace_relative` uses, so the sweep and the fence agree on what
    "absolute" means. They did not, briefly: the sweep matched only the spec's
    literal `PERSISTED_ABSOLUTE_RE`, which misses a forward-slashed drive root
    (`C:/…`) and a backslashed session mount — both of which the fence refuses
    correctly, so nothing could be written, but the sweep is the REUSABLE
    surface and a reader trusting it would have reported clean over a row the
    writer would never have allowed (review F-4). `PERSISTED_ABSOLUTE_RE`
    stays exported as the spec's pinned floor; every hit records whether that
    narrower floor also matched, so the difference stays visible rather than
    quietly absorbed.

    `fields` defaults to the pointer-carrying keys this plugin writes. Legacy
    rows on a real substrate WILL match (that is the bug this build normalizes
    on read); the guard runs over events a fixture just wrote, where a match
    means a writer skipped the assert.
    """
    keys = tuple(fields or POINTER_FIELDS)
    hits = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        for key in keys:
            val = data.get(key)
            if isinstance(val, str) and is_absolute_pointer(val):
                hits.append({
                    "type": ev.get("type"),
                    "field": key,
                    "value": val,
                    "spec_floor": bool(PERSISTED_ABSOLUTE_RE.match(val.strip())),
                })
    return hits


__all__ = [
    "AbsolutePathError",
    "UnresolvablePointerError",
    "REASON_AMBIGUOUS_ANCHOR",
    "REASON_EMPTY",
    "REASON_NO_ANCHOR",
    "DELIVERABLE_SUFFIXES",
    "normalize_persisted_path_strict",
    "PERSISTED_ABSOLUTE_RE",
    "POINTER_FIELDS",
    "SYNCING_TEXT",
    "WORKSPACE_ANCHORS",
    "LEGACY_ABSOLUTE_SHAPES",
    "SHAPE_RELATIVE",
    "SHAPE_SESSION_MOUNT",
    "SHAPE_WINDOWS_ABSOLUTE",
    "SHAPE_POSIX_ABSOLUTE",
    "SHAPE_UNRESOLVABLE",
    "SHAPE_EMPTY",
    "assert_workspace_relative",
    "attach_line",
    "classify_pointer",
    "is_absolute_pointer",
    "is_workspace_relative_doc",
    "machine_absolute",
    "normalize_persisted_path",
    "resolve_pointer",
    "scan_events_for_absolute_pointers",
    "to_workspace_relative",
]
