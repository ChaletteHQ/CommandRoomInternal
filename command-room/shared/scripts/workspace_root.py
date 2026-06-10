"""Canonical workspace-root resolution + write-path safety.

Two problems this solves, both seen in real workspaces:

1. Writes landing in the wrong tree. A skill that resolves the root from the
   current working directory could anchor inside the product's own
   ``Command Room/`` subfolder and duplicate client work there (the "EOS Path
   duplicated under Command Room/" class of bug). The true workspace root is
   the directory that contains ``_hq/data/entities.json`` — nothing else is.

2. No single definition of "root". Scripts each take a ``workspace_root``
   argument; when a caller doesn't have one, it should resolve it the same way
   everywhere. ``find_workspace_root`` is that one way.

Read-only. Pure path logic — does not touch the substrate.
"""

from __future__ import annotations

from pathlib import Path

# The substrate anchor. A directory IS the workspace root iff this file exists
# directly beneath it.
_ANCHOR = Path("_hq") / "data" / "entities.json"

# The product's own folder inside a workspace. Client/project work must never be
# written under here — it is the Command Room plugin's collateral, not a thread.
_PRODUCT_SUBFOLDER = "Command Room"


def is_workspace_root(path: Path) -> bool:
    """True if ``path`` is a workspace root (contains _hq/data/entities.json)."""
    return (path / _ANCHOR).is_file()


def find_workspace_root(start: Path | str | None = None) -> Path:
    """Walk upward from ``start`` to the directory containing the substrate.

    Returns the first ancestor (including ``start`` itself) that contains
    ``_hq/data/entities.json``. Raises FileNotFoundError if none is found —
    callers should treat that as "not inside a Command Room workspace" rather
    than guessing a root.
    """
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    candidate = here if here.is_dir() else here.parent
    for d in (candidate, *candidate.parents):
        if is_workspace_root(d):
            return d
    raise FileNotFoundError(
        f"No workspace root at or above {here} "
        f"(looked for {_ANCHOR.as_posix()})."
    )


def is_under_product_subfolder(path: Path | str, root: Path | str) -> bool:
    """True if ``path`` resolves to somewhere inside ``<root>/Command Room/``.

    Used to reject client-work writes that would land in the product's own
    folder. The root itself and paths outside it return False.
    """
    root_resolved = Path(root).resolve()
    target = Path(path).resolve()
    product = root_resolved / _PRODUCT_SUBFOLDER
    try:
        target.relative_to(product)
    except ValueError:
        return False
    return True


def assert_safe_write_path(path: Path | str, root: Path | str) -> Path:
    """Return the resolved path if it's a safe place to write client work.

    Raises ValueError if the path is inside ``<root>/Command Room/``. Call this
    in any code path that creates a project/deliverable folder so a misresolved
    root can't silently duplicate work into the product subfolder.
    """
    if is_under_product_subfolder(path, root):
        raise ValueError(
            f"Refusing to write client work under the product folder: {path}. "
            f"'{_PRODUCT_SUBFOLDER}/' holds plugin collateral, not threads. "
            f"Resolve the workspace root with find_workspace_root() and write "
            f"to a top-level folder instead."
        )
    return Path(path).resolve()


if __name__ == "__main__":
    import sys

    start = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        print(find_workspace_root(start))
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
