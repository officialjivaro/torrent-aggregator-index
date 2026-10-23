from __future__ import annotations

import os
import stat
from pathlib import Path

from jtorrent_backend.fsutil import remove_tree


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | (stat.S_IEXEC if path.is_dir() else 0))


def test_remove_tree_deletes_read_only_files_and_folders(tmp_path: Path) -> None:
    root = tmp_path / "public"
    locked_dir = root / "data" / "v4" / "releases" / "old" / "postings" / "10"
    locked_dir.mkdir(parents=True)
    locked_file = locked_dir / "part-0001.json"
    locked_file.write_text("{}", encoding="utf-8")
    (root / "data" / "status.json").write_text("{}", encoding="utf-8")
    _make_read_only(locked_file)
    _make_read_only(locked_dir)

    remove_tree(root)

    assert not root.exists()


def test_remove_tree_deletes_an_empty_read_only_folder(tmp_path: Path) -> None:
    root = tmp_path / "public"
    empty = root / "postings" / "10"
    empty.mkdir(parents=True)
    _make_read_only(empty)

    remove_tree(root)

    assert not root.exists()
