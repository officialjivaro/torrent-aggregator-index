"""Filesystem helpers shared by the index builders."""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Callable


def _make_writable_and_retry(function: Callable[..., Any], path: str, error: Any) -> None:
    """Clear read-only bits on a failing entry (and its parent), then retry once.

    Windows refuses to delete files and directories that carry the read-only
    attribute, and POSIX refuses to remove entries from a read-only directory.
    Anything that still fails after this is a real error and is raised.
    """
    for target in (path, os.path.dirname(path)):
        try:
            os.chmod(target, os.stat(target).st_mode | stat.S_IWRITE)
        except OSError:
            pass
    function(path)


def remove_tree(path: Path | str) -> None:
    """Remove a directory tree, including entries marked read-only."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    else:  # pragma: no cover - Python 3.11 fallback
        shutil.rmtree(path, onerror=lambda function, target, _info: _make_writable_and_retry(function, target, _info))
