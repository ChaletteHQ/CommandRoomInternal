"""While a battery runs, an upward walk never binds a real book (cr1#1240).

Every self-locating resolver walks up from the working directory to the first
directory holding ``_hq/data/entities.json``. From a checkout that sits inside
a real workspace folder (``<book>/dev/wt-<N>/``), the first one that walk
meets is the real book, so a battery run wrote test rows into its live ledger.

The battery runner (``tests/run_all.py``) sets ``CR1_BATTERY_ISOLATED=1`` in
every suite's environment. While it is set, a walk may bind a root only if the
root is under the system temp directory (where suites build their fixtures),
under this checkout itself, or under a path listed in ``CR1_BATTERY_ALLOW``
(``os.pathsep``-separated). Anything else, a real book above the checkout
included, is treated as "no workspace here".

Explicit roots (an argument, ``CR_WORKSPACE``) are not walks and are not
affected: a suite that names its fixture gets it.

Outside a battery the marker is unset and this module allows everything, so
production resolution is unchanged.

The rule lives here once. Every resolver that walks ``.parents`` looking for
the anchor calls ``walk_may_bind``; ``tests/run_guard_battery_isolation_test.py``
fails any new walker that does not.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping, Optional

MARKER_ENV = "CR1_BATTERY_ISOLATED"
ALLOW_ENV = "CR1_BATTERY_ALLOW"

# shared/scripts/battery_isolation.py -> parents[2] is command-room/, [3] the repo.
_CHECKOUT = Path(__file__).resolve().parents[3]


def active(env: Optional[Mapping[str, str]] = None) -> bool:
    """True while a battery has set the marker."""
    e = os.environ if env is None else env
    return bool(str(e.get(MARKER_ENV, "") or "").strip())


def _resolve(p: Path) -> Path:
    try:
        return p.resolve()
    except Exception:
        return p


def allowed_bases(env: Optional[Mapping[str, str]] = None) -> list[Path]:
    """Where a walk may bind while the marker is set."""
    e = os.environ if env is None else env
    bases = [_resolve(Path(tempfile.gettempdir())), _CHECKOUT]
    extra = str(e.get(ALLOW_ENV, "") or "")
    bases += [_resolve(Path(x).expanduser()) for x in extra.split(os.pathsep) if x.strip()]
    return bases


def _under(path: Path, bases: Iterable[Path]) -> bool:
    for b in bases:
        if path == b or b in path.parents:
            return True
    return False


def walk_may_bind(root, env: Optional[Mapping[str, str]] = None) -> bool:
    """May an upward walk bind ``root``? Always True outside a battery.

    Never raises: a path that cannot be judged is refused while the marker is
    set (a battery losing a fixture is a red test; a battery writing into a
    real book is the bug this exists for).
    """
    if not active(env):
        return True
    try:
        return _under(_resolve(Path(root)), allowed_bases(env))
    except Exception:
        return False
