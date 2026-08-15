"""Clone / fetch selected repos.

- Shallow clones (--depth 1) over HTTPS with the token passed as an
  Authorization header via `git -c`, so it is never written to
  .git/config or shell history.
- "Use existing checkout" mode (GitHub Actions workspaces) skips cloning
  when the directory already exists.
- Per-repo path globs limit parsing scope for monorepos / huge repos; the
  globs live on the RepoSpec and are applied at parse time.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from repograph.config import Config, RepoSpec


class CloneError(RuntimeError):
    pass


def ensure_repos(cfg: Config, repos: list[RepoSpec], token: str = "") -> dict[str, Path]:
    """Clone or refresh each repo. Returns {repo short name -> checkout dir}."""
    cfg.clone_dir.mkdir(parents=True, exist_ok=True)
    roots: dict[str, Path] = {}
    for spec in repos:
        dest = cfg.clone_dir / spec.name
        if dest.exists() and (dest / ".git").exists():
            if not cfg.use_existing_checkout:
                _refresh(dest, token)
        elif dest.exists() and cfg.use_existing_checkout:
            pass  # pre-checked-out workspace without .git metadata is fine
        else:
            _clone(spec, dest, token)
        roots[spec.name] = dest
    return roots


def _auth_args(token: str) -> list[str]:
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {basic}"]


def _clone(spec: RepoSpec, dest: Path, token: str) -> None:
    url = spec.clone_url or f"https://github.com/{spec.full_name}.git"
    cmd = ["git", *_auth_args(token), "clone", "--depth", "1", "--quiet", url, str(dest)]
    result = subprocess.run(
        cmd, capture_output=True, text=True, env={"GIT_TERMINAL_PROMPT": "0", "PATH": _path()}
    )
    if result.returncode != 0:
        raise CloneError(f"clone of {spec.full_name} failed: {result.stderr.strip()}")


def _refresh(dest: Path, token: str) -> None:
    for cmd in (
        ["git", *_auth_args(token), "fetch", "--depth", "1", "--quiet", "origin"],
        ["git", "reset", "--hard", "--quiet", "FETCH_HEAD"],
    ):
        result = subprocess.run(
            cmd, cwd=dest, capture_output=True, text=True,
            env={"GIT_TERMINAL_PROMPT": "0", "PATH": _path()},
        )
        if result.returncode != 0:
            return  # keep the existing checkout; parsing stale code beats failing


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")
