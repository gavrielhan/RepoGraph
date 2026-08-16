"""End-to-end fetch tests against a real git repository.

Mocked subprocess tests can only assert which argv we issued. Depth-1
clones actually reject `merge --ff-only` after the upstream advances,
which is the bug these cover.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from repograph import fetch


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "RepoGraph",
        "GIT_AUTHOR_EMAIL": "repograph@example.test",
        "GIT_COMMITTER_NAME": "RepoGraph",
        "GIT_COMMITTER_EMAIL": "repograph@example.test",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def _init_origin(path: Path) -> None:
    path.mkdir()
    init = _git(path, "init", "-b", "main", check=False)
    if init.returncode != 0:
        _git(path, "init")
        _git(path, "checkout", "-b", "main")
    (path / "file.txt").write_text("one\n")
    _git(path, "add", "file.txt")
    _git(path, "commit", "-m", "one")


def _advance(origin: Path, contents: str, message: str) -> None:
    (origin / "file.txt").write_text(contents)
    _git(origin, "add", "file.txt")
    _git(origin, "commit", "-m", message)


def _clone_shallow(origin: Path, dest: Path) -> None:
    # file:// forces the git protocol so --depth 1 is actually shallow.
    subprocess.run(
        ["git", "clone", "--depth", "1", origin.resolve().as_uri(), str(dest)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _head(path: Path) -> str:
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _upstream(path: Path) -> str:
    return _git(path, "rev-parse", "--abbrev-ref", "@{u}").stdout.strip()


def test_ff_only_fails_on_depth_1_after_upstream_advances(tmp_path):
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    _init_origin(origin)
    _clone_shallow(origin, clone)
    assert _git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    _advance(origin, "two\n", "two")

    _git(clone, "fetch", "--depth", "1", "origin")
    merge = _git(clone, "merge", "--ff-only", _upstream(clone), check=False)
    assert merge.returncode != 0
    assert _head(clone) != _head(origin)


def test_owned_shallow_clone_advances_with_reset(tmp_path):
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    _init_origin(origin)
    _clone_shallow(origin, clone)
    _advance(origin, "two\n", "two")

    status = fetch._refresh(clone, "", allow_reset=True)
    assert status["status"] == "updated"
    assert status["sha"] == _head(origin)
    assert (clone / "file.txt").read_text() == "two\n"


def test_reindex_shallow_clone_unshallows_and_fast_forwards(tmp_path):
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    _init_origin(origin)
    _clone_shallow(origin, clone)
    _advance(origin, "two\n", "two")

    status = fetch._refresh(clone, "", allow_reset=False)
    assert status["status"] == "updated"
    assert status["sha"] == _head(origin)
    assert (clone / "file.txt").read_text() == "two\n"
    assert _git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "false"


def test_dirty_tree_is_not_reset_even_on_owned_clones(tmp_path):
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    _init_origin(origin)
    _clone_shallow(origin, clone)
    before = _head(clone)
    (clone / "file.txt").write_text("local edit\n")
    _advance(origin, "two\n", "two")

    status = fetch._refresh(clone, "", allow_reset=True)
    assert status["status"] == "dirty"
    assert status["sha"] == before
    assert _head(clone) == before
    assert (clone / "file.txt").read_text() == "local edit\n"
