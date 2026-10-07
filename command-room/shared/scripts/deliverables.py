#!/usr/bin/env python3
"""The ONE landing door for a rendered deliverable (SPEC_NIGHTM2 §5, DELIV1).

WHY THIS EXISTS
---------------
In the merged Claude app the process that renders a `.docx` and the folder the
customer opens it from are on different machines. `brief_writer.make_brief`
ended in `doc.save(output_path)` against a path built under the container's
idea of the workspace root — a path that exists nowhere the customer can
reach. The file was produced, the chat said it was saved, and the folder stayed
empty (gap analysis §2.7 row 7, G8a-deliverables).

So a deliverable now has two halves, always in this order:

  1. BUILD in the session's own scratch — `/mnt/user-data/outputs/<name>` where
     that directory exists (the container's outbound mount), a private temp
     directory everywhere else. Never the workspace.
  2. LAND through the access layer — `workspace_access.write` under
     `<project>/deliverables/` (or `_hq/...`), which runs where the data is: in
     place on the mount in `vm` mode, in this shell on a legacy or local seat.
     One verb, one envelope, the layer's own path fences.

In `container` mode there is no workspace on this host, so a landing REFUSES
and says so in one composed sentence — and leaves no file anywhere, including
the scratch copy. A document the customer cannot open is not a deliverable; a
document nobody can find, that the chat claims was saved, is worse.

WHAT THIS MODULE IS NOT
-----------------------
It is not a second access layer. The workspace root, the host mode, the path
fences and the `create_parents` rule all come from `workspace_access` — this
module calls them, it does not restate them. It adds exactly one thing the
layer does not have: a BYTES door. `workspace_access.write` refuses a payload
that is not valid UTF-8 (`binary_unsupported`), and a `.docx` / `.pptx` is a
zip. Every other rule is the layer's, applied by calling the layer's own
functions, so a fence tightened there tightens here (pinned by removal in
tests/run_deliv1_test.py). See the build record's Seams: the intended home for
`_land_bytes` is `workspace_access.write` itself, whose CLI face already
decodes a `b64` argument into the payload it then refuses.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import workspace_access as _wa  # noqa: E402


# ---------------------------------------------------------------------------
# Where a deliverable is BUILT
# ---------------------------------------------------------------------------

#: The container's outbound mount (breakdown section 3.4). A file written here
#: is what the app can hand to the device; nothing else in the container can be.
CONTAINER_SCRATCH = "/mnt/user-data/outputs"

#: The VM's per-session home. Ephemeral (P4) and invisible to the customer's
#: machine, so a path under it is never a link and never a delivery target.
SESSION_PREFIX = "/sessions/"

#: What this module will land. Anything else belongs to the substrate writers.
DELIVERABLE_SUFFIXES = (".docx", ".pptx", ".html", ".htm", ".xlsx", ".pdf")


def scratch_root() -> Path:
    """The directory a deliverable is rendered into before it is landed.

    The container's outbound mount when it exists (that is the only place the
    app can deliver a file from), else a private temp directory. Never the
    workspace: the whole point is that the render happens off the substrate and
    the landing is one fenced verb.
    """
    outputs = Path(CONTAINER_SCRATCH)
    try:
        if outputs.is_dir():
            return outputs
    except OSError:
        pass
    return Path(tempfile.gettempdir())


def scratch_path(final_path: Union[str, Path]) -> Path:
    """A unique file in the scratch root carrying `final_path`'s own name.

    The name is kept because every gate downstream (the leak scan, the deck
    grammar, the caller's own assertion) reads it, and because a scratch file
    that carries the deliverable's name is self-explaining in a bug report.
    """
    name = Path(str(final_path)).name or "deliverable"
    holder = Path(tempfile.mkdtemp(prefix="cr-deliv-", dir=str(scratch_root())))
    return holder / name


def is_container_scratch(path: Union[str, Path]) -> bool:
    """True for a path under the container's outbound mount.

    Such a path is real in exactly one process and exists on no machine the
    customer owns, so it is never an opener URL and never a persisted pointer.
    """
    if not path:
        return False
    norm = str(path).replace("\\", "/")
    return norm == CONTAINER_SCRATCH or norm.startswith(CONTAINER_SCRATCH + "/")


def is_session_path(path: Union[str, Path]) -> bool:
    """True for a path under the sandbox VM's per-session home."""
    if not path:
        return False
    return str(path).replace("\\", "/").startswith(SESSION_PREFIX)


# ---------------------------------------------------------------------------
# Where a deliverable LANDS
# ---------------------------------------------------------------------------

R_NOT_A_DELIVERABLE = "not_a_deliverable"
R_OUTSIDE_WORKSPACE = "refused_path"
R_NO_WORKSPACE = _wa.R_NO_WORKSPACE
R_NOT_ATTACHED = _wa.R_NOT_ATTACHED
R_MISSING_PARENT = _wa.R_MISSING_PARENT

#: Where a deliverable may NOT land, however the name is spelled. The layer's
#: own fences already refuse an escape, the runtime cache and the writer lock;
#: this one refuses the workspace's own DATA, which is not a place a document
#: goes. It is a deny-shape rather than the three-folder allow-shape the review
#: suggested, because the product lands documents in far more than three
#: folders today — meetings, briefings, operator reports, board packs,
#: insights, contracts, dormant scans and cleanup reports are all real
#: destinations in the shipped skills — so an allow-list of three would refuse
#: landings that work today. What the
#: review MEASURED is exactly what this closes: a deliverable-suffixed name
#: inside the substrate directory (`_hq/data/events.jsonl.docx`, F-7).
#: `_hq/.cache` is deliberately NOT here: the layer fences the runtime cache
#: itself (`fence_path` → `refused_runtime_cache`, "code is never data"), and
#: restating it here would answer with this file's reason instead of the
#: layer's for a target the layer already owns.
RESERVED_DIRECTORIES = (
    "_hq/data",
    "_hq/config",
    "_hq/staging",
    "_hq/.system",
)
R_RESERVED_LOCATION = "refused_location"

#: The environment variable carrying the workspace folder's absolute path on
#: the CUSTOMER'S OWN COMPUTER — what the desktop app knows the folder by, what
#: the scheduled-chat bootloader bakes in as `<WORKSPACE_ABSOLUTE_PATH>`, and
#: what `get_device_info`'s `connectedFolders` returns. In the sandbox VM the
#: same folder is mounted under a per-session path, so this is the only
#: spelling of it that the customer's own machine can open.
DEVICE_ROOT_ENV = "CR_DEVICE_WORKSPACE"

#: The composed customer sentences, one per reason class. No path, no session
#: id, no machine name, no product surface — each names what happened and the
#: one thing that fixes it, and the fix it names is one the customer can
#: actually carry out. They live in one block so `validate_chat_output` gates
#: all of them and so no caller can invent a sixth.
REFUSED_LINE = (
    "I put the document together but could not reach your workspace folder "
    "from here, so nothing was saved - allow the folder on your computer and "
    "ask again."
)

#: The grant dropped mid-run (F22): the folder is known, it is simply not open
#: to this run right now. The same instruction, said in the present tense.
NOT_ATTACHED_LINE = (
    "I put the document together but your workspace folder is not open to me "
    "right now, so nothing was saved - allow the folder on your computer and "
    "ask again."
)

#: FOLDERGUARD did its job: the project's folder is not there. Telling this
#: customer to allow the folder sends them to do a thing that cannot help,
#: which is how a refusal turns into a support ticket (review F-5).
MISSING_PARENT_LINE = (
    "I put the document together but that project does not have a folder yet, "
    "so nothing was saved - create the project first and ask again."
)

#: The target itself was refused: an escape, the runtime cache, the writer
#: lock, the workspace's own data directory, or a name this door does not
#: carry. Nothing the customer did is wrong and nothing they can allow will
#: change it, so the sentence promises the retry rather than asking for one.
REFUSED_TARGET_LINE = (
    "I put the document together but could not save it where it was aimed, so "
    "nothing was saved - ask again and I will put it with your other documents."
)

#: A document engine this run does not have and cannot fetch (F-6: the sandbox
#: VM carries python-docx but not python-pptx, and has no network to install
#: one). Said by the composer instead of shelling out to a package installer
#: that cannot succeed.
DEPENDENCY_LINE = (
    "I could not build that file here because the tool that makes this kind of "
    "document is not available in this run - ask again on your computer, or "
    "ask me for it in another format."
)

_LINE_BY_REASON = {
    R_NOT_ATTACHED: NOT_ATTACHED_LINE,
    R_MISSING_PARENT: MISSING_PARENT_LINE,
    R_RESERVED_LOCATION: REFUSED_TARGET_LINE,
    R_NOT_A_DELIVERABLE: REFUSED_TARGET_LINE,
    _wa.R_REFUSED_PATH: REFUSED_TARGET_LINE,
    _wa.R_REFUSED_RUNTIME: REFUSED_TARGET_LINE,
    _wa.R_REFUSED_LOCK: REFUSED_TARGET_LINE,
    _wa.R_REFUSED_APPEND_ONLY: REFUSED_TARGET_LINE,
}

#: The document LANDED and this run cannot spell the folder the customer
#: opens. Said verbatim, and it is the whole answer: the walk's prep invented a
#: breadcrumb with a character that appears nowhere in the tree, which reads
#: like a path and is not one. A workspace-relative location under `_hq/` is
#: Rule-4-allowed (R-GW-1: a plain folder name in an honest sentence is
#: cosmetic, not the leak class); a session mount path, a `computer://` link or
#: anything under `/sessions/` is not, and none of them opens anything.
#: `{filename}` is the document's own name and nothing else.
PC_PATH_UNKNOWN_LINE = (
    "Saved under _hq/meetings/{filename} in your Command Room folder."
)


def saved_line(rel: str) -> str:
    """The one sentence a caller prints when the PC path is unknown."""
    name = Path(str(rel)).name
    line = PC_PATH_UNKNOWN_LINE.format(filename=name)
    try:
        from chat_output_validator import validate_chat_output  # type: ignore
    except ImportError:
        return line
    result = validate_chat_output(line)
    if not getattr(result, "ok", True):
        raise RuntimeError(
            "the saved-location line does not pass validate_chat_output: "
            + "; ".join(str(v) for v in getattr(result, "violations", []))
        )
    return line


#: The OTHER form of the same sentence: the one a run says when it DOES know
#: where the folder lives on the customer's own computer (FIX3 F3-4, ruling
#: R-RW-4). There are exactly two forms and this module owns both. The walk of
#: 2026-09-21 landed the prep correctly and then named it `→ Call Prep — Sam
#: Sample` — an arrow, a title, and no target at all — because the module
#: answered `device_path` and left the SENTENCE to the caller.
#: IDENT1 I-3 (R-RW2-4): the path sits in a CODE SPAN. The app renders chat
#: as markdown, and a bare Windows path loses every backslash before an
#: underscore - the 2026-09-22 prep said `<Workspace>_hq\meetings` on
#: screen, a folder that does not exist. Inside backticks markdown leaves
#: the bytes alone.
SAVED_TO_LINE = "Saved to `{path}`."


def opener_spelling(path) -> str:
    """A path in the spelling the OPENER uses: native, not forward-slashed.

    `device_join` forward-slashes the root because that is what
    `brief_path.get_brief_artifact_url` wants as INPUT; what that function
    emits, and therefore what a reader sees in a link, is the native form with
    a drive letter and backslashes. A sentence naming the same file has to
    agree with the link beside it, so the spelling lives in one function.
    """
    text = str(path or "")
    detect = text.replace("\\", "/").lstrip("/")
    if len(detect) >= 2 and detect[1] == ":" and detect[0].isalpha():
        return detect.replace("/", "\\")
    return text


def saved_to_line(device_path) -> str:
    """The one sentence a caller prints when the PC path IS known."""
    line = SAVED_TO_LINE.format(path=opener_spelling(device_path))
    try:
        from chat_output_validator import validate_chat_output  # type: ignore
    except ImportError:
        return line
    result = validate_chat_output(line)
    if not getattr(result, "ok", True):
        raise RuntimeError(
            "the saved-to line does not pass validate_chat_output: "
            + "; ".join(str(v) for v in getattr(result, "violations", []))
        )
    return line


def opener_line(device_path, rel) -> str:
    """The sentence, whichever form this run can honestly say.

    The path form when it passes the gate, the words form otherwise. A path
    that names a place a deliverable may not go — the workspace's own data
    directory — cannot be spoken aloud, and every fence above this refuses to
    land there in the first place, so the fallback is unreachable on a healthy
    tree. It exists so that removing one of those fences reds THAT fence's own
    named check instead of aborting the whole run on a sentence (FIX3 F3-4,
    found by deliv1's own removal harness).

    An EMPTY device path is not a path form. `saved_to_line("")` formats
    `Saved to .`, which the gate has no reason to refuse — it is a
    well-formed sentence about nowhere — so the words form was never reached
    and the caller was told the file is somewhere it could read as a
    directory. Nothing shipped reaches this (`land()` guards on `root`,
    `Delivery` always carries a path), but the contract this docstring states
    is the contract, and an unreachable branch that answers wrongly is one
    refactor away from being reachable (FIX PASS 1, review L-2).
    """
    if not str(device_path or "").strip():
        return saved_line(rel)
    try:
        return saved_to_line(device_path)
    except Exception:  # noqa: BLE001 — a sentence never blocks a landing
        pass
    try:
        return saved_line(rel)
    except Exception:  # noqa: BLE001
        return ""


#: Every sentence this module can say, for the gate and for a reviewer who
#: wants to read them all in one place.
ALL_LINES = (REFUSED_LINE, NOT_ATTACHED_LINE, MISSING_PARENT_LINE,
             REFUSED_TARGET_LINE, DEPENDENCY_LINE,
             PC_PATH_UNKNOWN_LINE.format(filename="Call_Prep.docx"),
             SAVED_TO_LINE.format(path="C:\\Users\\sample\\Sample Brain"
                                       "\\_hq\\meetings\\Call_Prep.docx"))


def refusal_line(reason: str = "") -> str:
    """The composed line for ONE refused landing, validated before it is used.

    One sentence per REASON, not one sentence for every reason. The reason a
    landing failed decides what the customer should do next, and a line that
    names the wrong remedy — "allow the folder" for a project folder that does
    not exist — is worse than no line, because they will go and do it (review
    F-5).

    `validate_chat_output` is the gate every composed customer sentence passes
    (M1 standing fence 5). A sentence that cannot pass it is a bug in this
    module, so the failure is loud rather than silent.
    """
    line = _LINE_BY_REASON.get(str(reason or ""), REFUSED_LINE)
    try:
        from chat_output_validator import validate_chat_output  # type: ignore
    except ImportError:
        return line
    result = validate_chat_output(line)
    if not getattr(result, "ok", True):
        raise RuntimeError(
            "a deliverables refusal line does not pass validate_chat_output: "
            + "; ".join(str(v) for v in getattr(result, "violations", []))
        )
    return line


def device_workspace_root(ctx: Dict[str, Any],
                          env: Optional[Dict[str, str]] = None,
                          device_root: Union[str, Path, None] = None) -> str:
    """The workspace folder's absolute path on the CUSTOMER'S OWN COMPUTER.

    Four sources, in order, and an honest "" when none of them answers:

      1. a root the caller was handed and passed in;
      2. `CR_DEVICE_WORKSPACE` in the environment — the bootloader's
         `<WORKSPACE_ABSOLUTE_PATH>`, which the model already holds at fire
         time from `get_device_info`'s `connectedFolders`;
      3. `device_path` on the layer's own context, for the day the resolver
         learns to carry it (seam: `workspace_access.resolve` / `discover`);
      4. any seat that is NOT the sandbox VM — there the machine holding the
         data IS the customer's computer, so the layer's root already is the
         device root, and every legacy and local seat answers here.

    "" means "this run does not know the customer-side spelling of the folder",
    and the caller must then say where the document went in words rather than
    mint a link to a path that exists in one session on one machine.
    """
    if device_root:
        return str(device_root)
    env = os.environ if env is None else env
    named = str(env.get(DEVICE_ROOT_ENV, "") or "").strip()
    if named:
        return named
    carried = str(ctx.get("device_path") or "").strip()
    if carried:
        return carried
    if ctx.get("mode") != _wa.VM and ctx.get("vm_path"):
        return str(ctx["vm_path"])
    return ""


def device_join(device_root: Union[str, Path], rel: str) -> str:
    """`<device root>/<rel>` in the root's own spelling, forward-slashed.

    `brief_path.get_brief_artifact_url` recognises a Windows drive letter and
    re-spells it with backslashes, so a forward-slashed `C:/...` here is
    exactly the shape the opener wants.
    """
    base = str(device_root).replace("\\", "/").rstrip("/")
    return base + "/" + Path(str(rel)).as_posix()


def _same_file(one: Union[str, Path], other: Union[str, Path]) -> bool:
    """True when two strings name the SAME path on this host.

    `device_join` forward-slashes the root because that is the spelling
    `brief_path.get_brief_artifact_url` wants, so on a Windows seat the
    customer's path and the caller's own path are two spellings of one file
    and `==` on the strings answers False. `Path.__eq__` compares the parsed
    parts instead — separator-insensitive, and case-insensitive on Windows —
    which is the question actually being asked (review H-1).
    """
    if not one or not other:
        return False
    try:
        return Path(str(one)) == Path(str(other))
    except (TypeError, ValueError):
        return False


def _reserved_location(rel: str) -> bool:
    """True for a target inside the workspace's own DATA (review F-7).

    `_is_append_only` fences the ledger by NAME, so `events.jsonl.docx` walked
    straight past it and landed in the data directory (measured, review P4).
    The directory is the right unit: nothing in there is a document.
    """
    posix = Path(str(rel)).as_posix().lstrip("./")
    return any(posix == d or posix.startswith(d + "/")
               for d in RESERVED_DIRECTORIES)


def _traverses_up(rel: str) -> bool:
    """True when `rel` carries a `..` segment, wherever in the name it sits.

    The first of the two belts review F-1 asked for. Nothing this door lands
    needs to climb: every shipped caller names its destination from the
    workspace root down. A `..` in a deliverable path is therefore always
    either a bug or an attempt to aim somewhere the caller was not given, and
    a fence that reads the name as WRITTEN cannot be walked around by one.
    """
    return ".." in Path(str(rel)).as_posix().split("/")


def _resolved_relative(vm_path: Union[str, Path, None],
                       rel: str) -> Optional[str]:
    """`rel` as the workspace-relative posix path the LAYER will really write.

    `None` when the layer itself refuses the target (an escape, the runtime
    cache, the writer lock) or the root cannot be read — in that case the
    layer's own answer is the one the caller gets, and this module says
    nothing extra about a path that never reaches a file.

    This is the SECOND belt (review F-1): `_reserved_location` reads the name
    as it was written, while `workspace_access.fence_path` RESOLVES it, so
    `projects/<org>/deliverables/../../../_hq/data/x.docx` walked past the
    reserved-directory check as written and landed in the workspace's own data
    directory as resolved (measured in vm mode out of a staged runtime).
    Asking the reserved question of the resolved name closes the gap for every
    spelling of it, including the ones `..` is not the only way to write.
    """
    if not vm_path:
        return None
    try:
        root = Path(str(vm_path)).resolve()
    except (OSError, ValueError):
        return None
    target, reason = _wa.fence_path(root, rel)
    if reason is not None or target is None:
        return None
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        return None


def workspace_relative(final_path: Union[str, Path],
                       workspace_root: Union[str, Path]) -> Optional[str]:
    """`final_path` as a workspace-relative posix path, or None when it is not
    inside `workspace_root`. Never a guess: a path outside the workspace has no
    relative form and the caller keeps its direct-save behaviour."""
    if not final_path or not workspace_root:
        return None
    try:
        root = Path(str(workspace_root)).resolve()
        target = Path(str(final_path)).resolve()
    except (OSError, ValueError):
        return None
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        return None


def land(workspace_root: Union[str, Path, None], rel: str,
         data: Union[str, bytes], *,
         create_parents: Optional[bool] = None,
         holder: str = "deliverables",
         device_root: Union[str, Path, None] = None,
         ctx: Optional[Dict[str, Any]] = None,
         env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Land `data` at `<workspace>/<rel>` through the access layer.

    Text goes through `workspace_access.write` unchanged — one verb, one
    envelope, the layer's fences, running wherever the data is. Bytes take the
    same fences and the same `create_parents` rule through `_land_bytes`,
    because the layer's text door refuses a zip.

    Both payload kinds go through `workspace_access._preflight` FIRST, exactly
    as the layer's own verbs do, and for both of its halves: there is no
    workspace on this host (the container), and the folder grant is not
    currently held (`folder_not_attached`, which a bridge reconnect can drop
    mid-run — F22). This file used to restate the first half by hand and drop
    the second, so a `.docx` under `_hq/` fabricated a whole directory tree in
    the VM's own filesystem and reported a save that reached nothing, while its
    `.html` twin refused correctly (review F-2, measured).

    Returns the layer's envelope shape plus `sha256`, `bytes`, `landed_path`
    (where the bytes are, on the machine that holds the data), `device_path`
    (where the CUSTOMER opens them — see `device_workspace_root`),
    `pc_path_unknown`, and on a refusal `line` — the composed sentence the
    caller says, chosen by reason. `ok:false` is a stop: nothing was written
    and nothing should be retried another way.
    """
    started = time.time()
    ctx = _wa.resolve(env=env) if ctx is None else ctx
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    refusal = _wa._preflight("land", ctx, started)
    if refusal is not None:
        refusal["rel"] = rel
        refusal["line"] = refusal_line(str(refusal.get("reason") or ""))
        return refusal
    if not _is_deliverable(rel):
        return _refuse("land", ctx, R_NOT_A_DELIVERABLE, rel=rel)
    # Both belts, in the order that gives the customer the most precise
    # reason (review F-1). The reserved question is asked of the name as
    # written AND of the name the layer will resolve it to; the `..` refusal
    # behind it catches a climb that lands somewhere ordinary, which the
    # reserved question has no opinion about. Neither one echoes the reserved
    # directory back: the composed sentence is the same one every refused
    # target gets (section 7.3).
    if _reserved_location(rel):
        return _refuse("land", ctx, R_RESERVED_LOCATION, rel=rel)
    if _reserved_location(_resolved_relative(ctx.get("vm_path"), rel) or ""):
        return _refuse("land", ctx, R_RESERVED_LOCATION, rel=rel)
    if _traverses_up(rel):
        return _refuse("land", ctx, R_OUTSIDE_WORKSPACE, rel=rel)
    if workspace_root and not _same_root(workspace_root, ctx["vm_path"]):
        # The caller named a different root than the layer resolved. Landing
        # against the layer's root anyway would put the customer's document in
        # a folder nobody asked for; landing against the caller's would bypass
        # the resolver. Refuse rather than choose.
        return _refuse("land", ctx, R_OUTSIDE_WORKSPACE, rel=rel)

    if _is_text(payload):
        out = _wa.write(rel, payload.decode("utf-8"),
                        create_parents=create_parents, holder=holder, ctx=ctx)
    else:
        out = _land_bytes(rel, payload, create_parents=create_parents, ctx=ctx)
    out["bytes"] = len(payload)
    out["sha256"] = hashlib.sha256(payload).hexdigest()
    if out.get("ok"):
        out["landed_path"] = str(Path(str(ctx["vm_path"])) / rel)
        # The path the CUSTOMER holds — the only one a link may ever carry.
        # When this run does not know the customer-side spelling of the folder,
        # the answer is the workspace-RELATIVE path and `pc_path_unknown`, so a
        # caller says where the document went in words and mints no link at
        # all. It is never the mount path: that carries a session id and opens
        # nothing (review F-1).
        root = device_workspace_root(ctx, env, device_root)
        out["device_path"] = (device_join(root, rel) if root
                              else Path(str(rel)).as_posix())
        out["pc_path_unknown"] = not root
        if not root:
            # The caller prints THIS, verbatim, and nothing else about where
            # the document went. Composed here rather than left to the skill:
            # a sentence a model writes about a location it cannot name is
            # how the walk got a breadcrumb (R-DELIV1-2).
            out["line"] = saved_line(rel)
        # ALWAYS a sentence, in one of exactly two forms (FIX3 F3-4). The
        # previous round answered a PATH when the PC path was known and a
        # SENTENCE only when it was not, so the one case that had the most to
        # say had no words for it, and the chat invented an arrow.
        out.update({"opener_line": (opener_line(out["device_path"], rel)
                                    if root else out["line"])})
    else:
        out.setdefault("line", refusal_line(str(out.get("reason") or "")))
    return out


def land_file(workspace_root: Union[str, Path, None], rel: str,
              built: Union[str, Path], *,
              create_parents: Optional[bool] = None,
              device_root: Union[str, Path, None] = None,
              ctx: Optional[Dict[str, Any]] = None,
              env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`land` the bytes of a file built in the scratch, then remove the scratch.

    The scratch copy goes on BOTH paths — landed or refused. A refused
    deliverable that leaves a file behind is the residue class this lane exists
    to end (spec section 5 item 5): the next reader cannot tell it from a good
    one, and in the container it is the only copy that ever existed.
    """
    built = Path(str(built))
    try:
        payload = built.read_bytes()
    except OSError as exc:
        return _refuse("land", ctx or _wa.resolve(env=env), "read_failed",
                       rel=rel, detail=str(exc))
    try:
        return land(workspace_root, rel, payload,
                    create_parents=create_parents, device_root=device_root,
                    ctx=ctx, env=env)
    finally:
        discard(built)


def discard(built: Union[str, Path, None]) -> bool:
    """Remove a scratch file and the private directory holding it.

    Never raises: the scratch lives in the container or in a temp directory,
    both of which permit `unlink` (the workspace mount does not — P2 — which is
    exactly why a refused document must never have been landed in the first
    place). Returns True when nothing of it is left.
    """
    if not built:
        return True
    path = Path(str(built))
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        return False
    parent = path.parent
    try:
        if parent.name.startswith("cr-deliv-") and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    return not path.exists()


# ---------------------------------------------------------------------------
# The composer's two-line integration
# ---------------------------------------------------------------------------

class Delivery:
    """One deliverable's build-then-land plan, resolved once by the composer.

    `build_path` is where the renderer writes and every gate reads. `finish()`
    lands it and returns the path the customer's machine will hold; `discard()`
    throws the build away (a refused gate).

    Three outcomes, decided once, from facts rather than from a mode name:

      * the target is inside a workspace THIS host can see — land it through
        the layer (the merged `vm` seat, and a legacy or local seat writing
        into its own workspace: the same verb, running in this shell);
      * the target, or the workspace the caller named, is somewhere this host
        does not hold — a container scratch, a staged upload, or a folder that
        simply is not here — land it, which REFUSES, because a save that
        cannot reach the customer's folder must say so rather than write into
        a directory tree that dies with the session;
      * anything else — a plain path on this machine, which is every test
        fixture and every direct call — `build_path` IS the final path and
        `finish()` is a no-op, byte-for-byte today's behaviour (M1 ruling 23,
        dual backend).
    """

    __slots__ = ("final_path", "build_path", "landing", "rel", "ctx",
                 "workspace_root", "envelope", "device_root", "landed_path",
                 "device_path", "pc_path_unknown", "opener_line")

    def __init__(self, final_path: Union[str, Path],
                 workspace_root: Union[str, Path, None] = None,
                 ctx: Optional[Dict[str, Any]] = None,
                 env: Optional[Dict[str, str]] = None,
                 device_root: Union[str, Path, None] = None) -> None:
        self.final_path = str(final_path)
        self.ctx = _wa.resolve(env=env) if ctx is None else ctx
        self.workspace_root = str(workspace_root) if workspace_root else None
        self.device_root = str(device_root) if device_root else None
        self.envelope: Optional[Dict[str, Any]] = None
        #: Where the bytes are (the machine holding the data) and where the
        #: customer opens them. Equal on every seat that is not the sandbox VM.
        self.landed_path = self.final_path
        self.device_path = self.final_path
        self.pc_path_unknown = False
        #: The SENTENCE, always, in one of exactly two forms (FIX3 F3-4). A
        #: caller prints this and nothing else of its own about the location.
        self.opener_line = opener_line(self.final_path,
                                       Path(self.final_path).name)
        root = self.ctx.get("vm_path")
        self.rel = workspace_relative(self.final_path, root) if root else None
        if self.rel and _is_deliverable(self.rel):
            self.landing = True
            self.build_path = str(scratch_path(self.final_path))
        elif _unreachable_target(self.final_path, self.workspace_root):
            # The render still happens — the gates run on real bytes — and the
            # landing refuses. A workspace path must never be the render target
            # here, because in the container it fabricates a directory tree
            # nobody will ever open and the chat then reports a saved file.
            self.landing = True
            self.rel = self.rel or _fallback_rel(self.final_path)
            self.build_path = str(scratch_path(self.final_path))
        else:
            self.landing = False
            self.build_path = self.final_path

    def finish(self) -> str:
        """Land the built file and return the path THE CUSTOMER holds.

        The return is the envelope's `device_path`, never its `landed_path`.
        On the merged seat those name two different machines, and only one of
        them can be opened.

        On every seat where the plugin and the folder share a filesystem they
        name ONE file, and the return is then the caller's OWN `final_path`
        string, byte for byte — not a re-spelling of it. That matters because
        `device_join` forward-slashes the root for the opener, so on a Windows
        legacy or local seat the two strings differ by their separators while
        naming the same file, and a caller that links, logs or receipts the
        return would silently start carrying a spelling it never used before
        (review H-1). `_same_file` is the test; the file never moved, so the
        answer is the string the caller handed in.

        When this run does not know the customer-side spelling of the folder,
        the return is the workspace-RELATIVE path and `pc_path_unknown` is
        true — which the opener refuses to turn into a link, so the caller says
        where the document went in words instead of minting a dead one that
        carries a session id (review F-1).

        Raises `DeliveryRefused` when the layer refuses — carrying the one
        composed line for that reason — after the scratch copy is gone.
        """
        if not self.landing:
            return self.final_path
        envelope = land_file(self.workspace_root or self.ctx.get("vm_path"),
                             str(self.rel), self.build_path,
                             device_root=self.device_root, ctx=self.ctx)
        self.envelope = envelope
        if not envelope.get("ok"):
            raise DeliveryRefused(envelope.get("line") or refusal_line(), envelope)
        self.landed_path = str(envelope.get("landed_path") or self.final_path)
        self.device_path = str(envelope.get("device_path") or self.landed_path)
        self.pc_path_unknown = bool(envelope.get("pc_path_unknown"))
        if _same_file(self.device_path, self.final_path):
            # The file never moved, so the sentence names the caller's OWN
            # spelling of it - the same reasoning as the return value below
            # (review H-1), applied to the words as well as to the path.
            self.opener_line = opener_line(self.final_path,
                                           str(self.rel or
                                               Path(self.final_path).name))
            return self.final_path
        self.opener_line = str(
            envelope.get("opener_line")
            or opener_line(self.device_path,
                           str(self.rel or Path(self.final_path).name)))
        return self.device_path

    def discard(self) -> bool:
        """Throw the build away — a gate refused it. Nothing is landed.

        Removes `build_path` whichever it is: the scratch copy when the
        document was going to be landed, and the file itself on a seat that
        renders straight to its final path — which is the older
        "A REFUSED SAVE LEAVES NO FILE" behaviour, unchanged, on the only
        seats that still take it.
        """
        return discard(self.build_path)


class DeliveryRefused(RuntimeError):
    """The access layer refused the landing. No file exists anywhere."""

    def __init__(self, line: str, envelope: Dict[str, Any]) -> None:
        super().__init__(line)
        self.line = line
        self.envelope = envelope
        self.reason = str(envelope.get("reason") or "")


def refusal_sentence(exc: BaseException) -> Optional[str]:
    """The one composed sentence for an exception the BASH BOUNDARY should
    render as words, or None when this is not one of ours.

    The eight composer instructions tell the model "say its one sentence and
    stop". Nothing in the tree caught the exception, so a `python3
    brief_writer.py '<json>'` line answered with a traceback whose frames spell
    the runtime path — a session id in what the model reads and may echo
    (review F-4). This is the predicate the two CLI entry points use.

    Tight on purpose: only a sentence from this module's own block counts, so
    an arbitrary exception carrying a `.line` attribute cannot smuggle prose
    onto a customer surface.
    """
    line = getattr(exc, "line", None)
    if isinstance(line, str) and line in ALL_LINES:
        return line
    return None


def dependency_line() -> str:
    """The composed sentence for a missing document engine, gate-checked."""
    line = DEPENDENCY_LINE
    try:
        from chat_output_validator import validate_chat_output  # type: ignore
    except ImportError:
        return line
    result = validate_chat_output(line)
    if not getattr(result, "ok", True):
        raise RuntimeError(
            "deliverables.DEPENDENCY_LINE does not pass validate_chat_output: "
            + "; ".join(str(v) for v in getattr(result, "violations", []))
        )
    return line


def offline_runtime(env: Optional[Dict[str, str]] = None) -> bool:
    """True where `pip install` cannot succeed, so nothing may try it.

    The sandbox VM and the cloud container both run without a network (gap
    analysis §1.1), and the VM's package set is fixed at install time:
    python3.10 + stdlib + python-docx + openpyxl, and NO python-pptx. An
    unfenced `pip install` there is a subprocess that cannot help, whose only
    contribution is a confusing failure in place of a sentence (review F-6).

    Asked as a POSITIVE signal, never as the container POSTURE. The layer maps
    `unknown` onto the container posture for writers — correctly, because a
    writer that cannot tell where it is must refuse — but `unknown` is also
    what a developer box and CI answer, and refusing the install there would
    break the machine where the install is exactly the right thing to do. So
    the two no-network environments are named directly: the merged app (its own
    signal pair) and the sandbox VM (this file running out of the runtime
    installed inside the workspace).

    Conservative on every other axis: no `workspace_access`, an unreadable
    environment, an exception — all answer False, which is today's behaviour on
    every seat that has always been allowed to install.
    """
    try:
        env = dict(os.environ) if env is None else env
        if _wa._env_detect_mode(env) == "merged_cloud":
            return True
        return _wa._running_from_runtime_cache() is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _is_deliverable(rel: str) -> bool:
    return str(rel).lower().endswith(DELIVERABLE_SUFFIXES)


def _unreachable_target(final_path: Union[str, Path],
                        workspace_root: Optional[str]) -> bool:
    """True when the caller is about to save into a folder this host does not
    hold — the container's exact shape, asked as a fact.

    Three ways to be unreachable, none of which needs a mode name:

      * the target itself is a container path (`/mnt/user-data/...`) — the
        outbound scratch or a staged read-only snapshot, gone when the session
        ends;
      * the caller named a workspace root that is a container path — the same
        thing one level up, which is how a staged upload becomes a fake root;
      * the caller named a workspace root that is not a directory here at all,
        which is what `$HOME/mnt/<basename>` and `/sessions/<id>/mnt/<name>`
        look like from the cloud container.

    A caller that named no workspace root and no container path is writing a
    plain path on this machine, and this module leaves it alone.
    """
    target = str(final_path or "")
    if is_container_scratch(target):
        return True
    if not workspace_root:
        return False
    if is_container_scratch(workspace_root):
        return True
    try:
        return not Path(str(workspace_root)).is_dir()
    except OSError:
        return True


def _fallback_rel(final_path: Union[str, Path]) -> str:
    """The relative name a container-mode render would have landed at.

    Only ever used to name the refusal; nothing is written. `_hq/meetings/` is
    where Rule 3 puts a brief whose project could not be resolved.
    """
    return "_hq/meetings/" + (Path(str(final_path)).name or "deliverable")


def _is_text(payload: bytes) -> bool:
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _same_root(a: Union[str, Path], b: Union[str, Path]) -> bool:
    try:
        return Path(str(a)).resolve() == Path(str(b)).resolve()
    except (OSError, ValueError):
        return False


def _refuse(verb: str, ctx: Dict[str, Any], reason: str,
            **fields: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "verb": verb,
        "reason": reason,
        "ws": ctx.get("vm_path"),
        "mode": ctx.get("mode"),
        "runtime_version": ctx.get("runtime_version"),
    }
    out.update(fields)
    out["line"] = refusal_line(reason)
    return out


def _land_bytes(rel: str, payload: bytes, *, create_parents: Optional[bool],
                ctx: Dict[str, Any]) -> Dict[str, Any]:
    """The bytes door — the layer's fences, the layer's parent rule, an atomic
    replace. Every refusal below is `workspace_access`'s own answer, asked of
    `workspace_access`: `fence_path` for the three path fences,
    `_is_append_only` for the ledger, `_create_parents_default` for FOLDERGUARD.
    Nothing about where a write may go is decided in this file.
    """
    target, reason = _wa.fence_path(Path(str(ctx["vm_path"])), rel)
    if reason is not None:
        return _refuse("land", ctx, reason, rel=rel)
    if _wa._is_append_only(target.name):
        return _refuse("land", ctx, _wa.R_REFUSED_APPEND_ONLY, rel=rel,
                       next="append_jsonl")
    if target.name in _wa.REV_FILES:
        # The layer writes these two under the sentinel lock and maintains a
        # `.rev` sidecar beside them; this door does neither. Unreachable while
        # `DELIVERABLE_SUFFIXES` holds no `.json`, and kept in front of the
        # write so that widening the suffix list can never silently turn a
        # locked, versioned file into a blind overwrite (review F-8).
        return _refuse("land", ctx, R_NOT_A_DELIVERABLE, rel=rel)
    parents = (_wa._create_parents_default(rel) if create_parents is None
               else bool(create_parents))
    if parents:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _refuse("land", ctx, "write_failed", rel=rel, detail=str(exc))
    elif not target.parent.is_dir():
        return _refuse("land", ctx, _wa.R_MISSING_PARENT, rel=rel)
    before = _mtime_ns(target)
    try:
        _atomic_write_bytes(target, payload)
    except OSError as exc:
        return _refuse("land", ctx, "write_failed", rel=rel, detail=str(exc))
    return {"ok": True, "verb": "land", "rel": rel, "ws": ctx.get("vm_path"),
            "mode": ctx.get("mode"),
            "runtime_version": ctx.get("runtime_version"),
            "mtime_ns_before": before, "mtime_ns_after": _mtime_ns(target),
            "rev": None}


def _mtime_ns(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Temp sibling -> fsync -> `os.replace`, exactly as `atomic_write_text`
    does it for text. `os.replace` over an existing file works on the merged
    mount (P1_RESULT_2026-09-19); `unlink` does not, which is why the failure
    path below tolerates a temp file it cannot remove rather than raising over
    it.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="." + target.name + ".tmp.",
                                    suffix=".write", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise

# ---------------------------------------------------------------------------
# DOCS1 D-2 — a Claude Doc created anyway is exported into the folder
# ---------------------------------------------------------------------------
#
# The document-routing rule (DOCSFENCE1; DOCS1 D-1) says a document is produced
# by the owning skill and lands in the folder. When the host's built-in docs
# skill wins the routing question anyway, the page exists only on claude.ai
# and the folder, the ledger and the receipts have nothing. This is the way
# back: the owning skill (or the customer, `export that doc`) reads the doc
# through the DISCOVERED docs seam (`tool_discovery.discover_docs_tool` —
# never a remembered tool id; a model call, so the bytes arrive here as an
# argument) and lands it as a NEW file under the kind's folder, through
# `land` and every fence it owns, with a receipt row for `append_jsonl`.
#
# The doc itself is left where it is. The product never deletes what it did
# not make (R-DOCS1-2, default).

EXPORT_EVENT = "deliverable_landed"
EXPORT_SOURCE = "claude_doc"

#: Where each kind lands (R-DOCS1-1 defaults): meeting-shaped documents go
#: where the prep and the past-meeting brief go; a project deliverable goes
#: to that project's `deliverables/`; the rest to `_hq/notes/`. `{project}`
#: is the caller's project folder name and is REQUIRED for those kinds — a
#: deliverable with no project has no folder, and the door does not invent one.
DOC_EXPORT_KINDS: Dict[str, tuple] = {
    "call_prep": ("_hq/meetings", "Call_Prep"),
    "past_meeting": ("_hq/meetings", "Past_Meeting"),
    "meeting_notes": ("_hq/meetings", "Past_Meeting"),
    "weekly_recap": ("_hq/meetings", "Weekly_Recap"),
    "memo": ("{project}/deliverables", "Memo"),
    "one_pager": ("{project}/deliverables", "OnePager"),
    "decision_memo": ("{project}/deliverables", "DecisionMemo"),
    "notes": ("_hq/notes", "Notes"),
}
#: The export formats the docs seam offers that are deliverables here. Markdown
#: is deliberately NOT one (CONTRACT Rule 27: a customer-facing document is
#: never `.md`).
DOC_EXPORT_FORMATS: Dict[str, str] = {"docx": ".docx", "html": ".html", "pdf": ".pdf"}
#: A second export of the same doc inside this window refreshes the file in
#: place and composes NO second receipt row (occurrence-keyed, like a prep).
EXPORT_WINDOW_HOURS = 24
R_BAD_EXPORT = "bad_args"
R_EXPORT_TARGET = "refused_location"

EXPORT_REFUSED_LINE = (
    "I could not save that doc into your Command Room folder because the request "
    "did not say what kind of document it is or which project it belongs to."
)
#: The deny-shape refusal, in the kind's own words (review N-10).
EXPORT_TARGET_LINE = "I could not save that doc where a {kind} goes, so nothing was saved."
#: Plain words for each kind, for that sentence only.
_KIND_WORDS: Dict[str, str] = {
    "call_prep": "meeting prep", "past_meeting": "meeting brief",
    "meeting_notes": "meeting brief", "weekly_recap": "weekly recap",
    "memo": "memo", "one_pager": "one-pager", "decision_memo": "decision memo",
    "notes": "note",
}
#: What the first bytes of each export format look like. `land` has no magic
#: check of its own (a composer's bytes come from the render chokepoint); an
#: export's bytes come from a connector and are checked HERE (review N-11).
_FORMAT_MAGIC: Dict[str, bytes] = {"docx": b"PK\x03\x04", "pdf": b"%PDF"}


def export_target_line(kind: str) -> str:
    """The deny-shape sentence for `kind`, gated like every other line here."""
    line = EXPORT_TARGET_LINE.format(kind=_KIND_WORDS.get(kind, "document"))
    try:
        from chat_output_validator import validate_chat_output  # type: ignore
    except ImportError:
        return line
    if not getattr(validate_chat_output(line), "ok", True):
        return REFUSED_TARGET_LINE
    return line


def binascii_error():
    """`binascii.Error` — the exception a strict base64 decode raises."""
    import binascii

    return binascii.Error


def _doc_id_of(doc_ref) -> str:
    """The stable key of a doc: the last path segment of its link (or the id
    as given). A `claude.ai/[code/]artifact/<title>-<id>` link keys on its
    full last segment — the same link, the same key, which is all the
    refresh-in-place lookup needs."""
    text = str(doc_ref or "").strip().rstrip("/")
    if "://" in text or "/" in text:
        text = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        text = text.rsplit("/", 1)[-1]
    return text


def _export_target_fenced(rel: str, folder: str) -> bool:
    """THE deny-shape of an export target (DOCS1 D-2 pass line (d)): the file
    sits DIRECTLY in the kind's folder — its parent is that folder, its name
    has no separator and no `..`, and the folder itself is not one of the
    reserved data directories. `land` refuses a climb on its own; this fence
    is the one that says a doc's TITLE can never choose the folder."""
    posix = Path(str(rel)).as_posix()
    name = posix.rsplit("/", 1)[-1] if "/" in posix else posix
    parent = posix[: -len(name) - 1] if "/" in posix else ""
    if parent != folder.strip("/"):
        return False
    if not name or ".." in name or "/" in name or "\\" in name:
        return False
    if _traverses_up(posix) or _reserved_location(posix):
        return False
    return True


def _prior_export(workspace_root, doc_id: str) -> Optional[Dict[str, Any]]:
    """The newest `deliverable_landed` row for this doc, or None."""
    if not workspace_root or not doc_id:
        return None
    try:
        import events_io  # type: ignore

        rows = [e for e in events_io.iter_events(workspace_root)
                if isinstance(e, dict) and e.get("type") == EXPORT_EVENT
                and isinstance(e.get("data"), dict)
                and e["data"].get("source") == EXPORT_SOURCE
                and e["data"].get("doc_id") == doc_id]
    except Exception:  # noqa: BLE001 - an unreadable ledger means "no prior"
        return None
    return rows[-1] if rows else None


def _within_window(row: Optional[Dict[str, Any]], now=None) -> bool:
    if not row:
        return False
    ts = row.get("ts") or row.get("timestamp")
    if not ts:
        return False
    try:
        from datetime import datetime, timezone, timedelta

        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return (now - when) <= timedelta(hours=EXPORT_WINDOW_HOURS)
    except (TypeError, ValueError):
        return False


def export_claude_doc(workspace_root: Union[str, Path, None], doc_ref: str, *,
                      kind: str, title: str, content_base64: str,
                      format: str = "docx", project: Optional[str] = None,
                      date_iso: Optional[str] = None,
                      source_skill: str = "workspace-manager",
                      ctx: Optional[Dict[str, Any]] = None,
                      env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Land a Claude Doc's exported bytes in the folder, once per doc.

    `content_base64` is what the discovered docs `export` tool returned (base64
    of the doc in `format`) — the model made that call; nothing here talks to
    a connector. `kind` picks the folder and the filename prefix
    (`DOC_EXPORT_KINDS`); `project` is required for the project-deliverable
    kinds. The name is the product's own: `<Prefix>_<slug>_<date><ext>`.

    Idempotent: a prior `deliverable_landed` row for the same `doc_id` whose
    file still exists means the SAME `rel` is refreshed in place; inside
    `EXPORT_WINDOW_HOURS` no second row is composed (`receipt_row` is None).

    Returns `land`'s envelope plus `rel`, `opener_line`, `receipt_row`
    (the row the caller appends through `append_jsonl`, or None),
    `refreshed`, `doc_id`. `ok:false` carries `line` — the one sentence to say.
    """
    started = time.time()
    try:
        from receipts import require_writer_identity  # type: ignore

        require_writer_identity(env, workspace_root=workspace_root)
    except ImportError:
        pass
    ctx = _wa.resolve(env=env) if ctx is None else ctx
    kind_key = str(kind or "").strip().lower()
    fmt = str(format or "docx").strip().lower()
    doc_id = _doc_id_of(doc_ref)
    title_text = str(title or "").strip()
    if kind_key not in DOC_EXPORT_KINDS or fmt not in DOC_EXPORT_FORMATS \
            or not doc_id or not title_text:
        out = _refuse("export_claude_doc", ctx, R_BAD_EXPORT, rel="", kind=kind_key,
                      format=fmt, doc_id=doc_id)
        out["line"] = EXPORT_REFUSED_LINE
        return out
    folder_tpl, prefix = DOC_EXPORT_KINDS[kind_key]
    if "{project}" in folder_tpl:
        proj = str(project or "").strip().strip("/")
        if not proj or ".." in proj or "/" in proj or "\\" in proj:
            out = _refuse("export_claude_doc", ctx, R_BAD_EXPORT, rel="", kind=kind_key,
                          detail="project required")
            out["line"] = EXPORT_REFUSED_LINE
            return out
        folder = folder_tpl.format(project=proj)
    else:
        folder = folder_tpl
    try:
        import base64

        payload = base64.b64decode(str(content_base64 or "").strip(), validate=True)
    except (ValueError, TypeError, binascii_error()):
        payload = b""
    magic = _FORMAT_MAGIC.get(fmt)
    well_formed = bool(payload) and (payload.startswith(magic) if magic else _is_text(payload))
    if not well_formed:
        # Garbage never lands as a document with a receipt (review N-11).
        out = _refuse("export_claude_doc", ctx, R_BAD_EXPORT, rel="", kind=kind_key,
                      detail="empty content" if not payload else "not a " + fmt)
        out["line"] = EXPORT_REFUSED_LINE
        return out

    from brief_path import _slugify  # type: ignore

    day = str(date_iso or "").strip() or time.strftime("%Y-%m-%d")
    prior = _prior_export(workspace_root, doc_id)
    prior_rel = (prior or {}).get("data", {}).get("rel") if prior else None
    refreshed = False
    rel = None
    if prior_rel and workspace_root and (Path(str(workspace_root)) / str(prior_rel)).is_file():
        rel = str(prior_rel)
        refreshed = True
    if rel is None:
        ext = DOC_EXPORT_FORMATS[fmt]
        stem = f"{folder}/{prefix}_{_slugify(title_text)}_{day}"
        rel = stem + ext
        # A DIFFERENT doc already landed under this name today (the same
        # title, or a punctuation-only title): never overwrite it. The name
        # takes the doc's own tail so the two stay two files (review N-9).
        if workspace_root and (Path(str(workspace_root)) / rel).is_file():
            tail = re.sub(r"[^A-Za-z0-9]", "", doc_id)[-6:] or "copy"
            rel = f"{stem}-{tail}{ext}"
    # The deny-shape (pass line (d)): the title never chooses the folder.
    if not _export_target_fenced(rel, folder):
        out = _refuse("export_claude_doc", ctx, R_EXPORT_TARGET, rel=rel)
        out["line"] = export_target_line(kind_key)
        return out

    out = land(workspace_root, rel, payload, create_parents=None, ctx=ctx, env=env)
    out["doc_id"] = doc_id
    out["refreshed"] = refreshed
    out["receipt_row"] = None
    if not out.get("ok"):
        return out
    if not (refreshed and _within_window(prior)):
        data: Dict[str, Any] = {"source": EXPORT_SOURCE, "doc_id": doc_id,
                                "doc_ref": str(doc_ref or "").strip(), "rel": rel,
                                "kind": kind_key, "title": title_text, "format": fmt,
                                "refreshed": refreshed, "bytes": out.get("bytes"),
                                "sha256": out.get("sha256")}
        try:
            from receipts import machine_fields  # type: ignore

            data.update(machine_fields(env))
        except ImportError:
            pass
        out["receipt_row"] = {"type": EXPORT_EVENT, "source_skill": source_skill,
                              "data": data}
    out["elapsed_ms"] = int((time.time() - started) * 1000)
    return out


__all__ = [
    "ALL_LINES",
    "CONTAINER_SCRATCH",
    "DELIVERABLE_SUFFIXES",
    "DEPENDENCY_LINE",
    "DEVICE_ROOT_ENV",
    "Delivery",
    "DeliveryRefused",
    "MISSING_PARENT_LINE",
    "NOT_ATTACHED_LINE",
    "REFUSED_LINE",
    "REFUSED_TARGET_LINE",
    "RESERVED_DIRECTORIES",
    "dependency_line",
    "device_join",
    "device_workspace_root",
    "discard",
    "offline_runtime",
    "refusal_sentence",
    "is_container_scratch",
    "is_session_path",
    "land",
    "land_file",
    "refusal_line",
    "scratch_path",
    "scratch_root",
    "workspace_relative",
    # DOCS1 D-2
    "DOC_EXPORT_FORMATS",
    "DOC_EXPORT_KINDS",
    "EXPORT_EVENT",
    "EXPORT_REFUSED_LINE",
    "EXPORT_SOURCE",
    "EXPORT_TARGET_LINE",
    "EXPORT_WINDOW_HOURS",
    "export_claude_doc",
    "export_target_line",
]
