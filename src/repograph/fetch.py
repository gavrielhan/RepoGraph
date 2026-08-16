"""Clone / fetch selected repos.

- Shallow clones (--depth 1) over HTTPS with the token passed as an
  Authorization header via `git -c`, so it is never written to
  .git/config or shell history.
- ``activate`` / ``run`` own the clone directory: clean checkouts may
  ``reset --hard FETCH_HEAD`` so depth-1 clones can actually advance.
- ``reindex`` may point at real working trees: it never resets. It
  fast-forwards the current branch, and unshallows if a depth-1 history
  has no merge base.
- Dirty working trees are left untouched in both modes.
- "Use existing checkout" mode (GitHub Actions workspaces) skips cloning
  when the directory already exists.
"""

from __future__ import annotations

import base64
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repograph.config import Config, RepoSpec


class CloneError(RuntimeError):
    pass


@dataclass
class FetchResult:
    roots: dict[str, Path] = field(default_factory=dict)
    statuses: dict[str, dict] = field(default_factory=dict)


def ensure_repos(cfg: Config, repos: list[RepoSpec], token: str = "") -> FetchResult:
    """Clone missing repos. Owned checkouts may hard-reset to FETCH_HEAD."""
    cfg.clone_dir.mkdir(parents=True, exist_ok=True)
    result = FetchResult()
    for spec in repos:
        dest = cfg.clone_dir / spec.name
        if dest.exists() and (dest / ".git").exists():
            if not cfg.use_existing_checkout:
                result.statuses[spec.name] = _refresh(dest, token, allow_reset=True)
            else:
                result.statuses[spec.name] = {
                    "status": "skipped",
                    "reason": "existing checkout requested",
                    "sha": _git_sha(dest),
                }
        elif dest.exists() and cfg.use_existing_checkout:
            result.statuses[spec.name] = {
                "status": "skipped",
                "reason": "existing checkout has no git metadata",
                "sha": None,
            }
        else:
            _clone(spec, dest, token)
            result.statuses[spec.name] = {
                "status": "cloned",
                "sha": _git_sha(dest),
            }
        result.roots[spec.name] = dest
    return result


def refresh_existing(roots: dict[str, Path], token: str = "") -> FetchResult:
    """Update existing checkouts without cloning or resetting."""
    result = FetchResult()
    for name, dest in roots.items():
        result.roots[name] = dest
        if (dest / ".git").exists():
            result.statuses[name] = _refresh(dest, token, allow_reset=False)
        else:
            result.statuses[name] = {
                "status": "skipped",
                "reason": "no git metadata",
                "sha": None,
            }
    return result


def _auth_args(token: str) -> list[str]:
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {basic}"]


def _clone(spec: RepoSpec, dest: Path, token: str) -> None:
    url = spec.clone_url or f"https://github.com/{spec.full_name}.git"
    cmd = ["git", *_auth_args(token), "clone", "--depth", "1", "--quiet", url, str(dest)]
    result = _run(cmd)
    if result.returncode != 0:
        raise CloneError(f"clone of {spec.full_name} failed: {result.stderr.strip()}")


def _refresh(dest: Path, token: str, *, allow_reset: bool = False) -> dict:
    """Fetch origin, then update HEAD if the working tree is clean.

    Owned clones (``allow_reset=True``) hard-reset to FETCH_HEAD so a
    depth-1 history can advance. Reindex (``allow_reset=False``) only
    fast-forwards the current branch, unshallowing when git refuses to
    merge unrelated shallow histories.
    """
    before = _git_sha(dest)
    fetch = _run(
        ["git", *_auth_args(token), "fetch", "--depth", "1", "--quiet", "origin"],
        cwd=dest,
    )
    if fetch.returncode != 0:
        return {
            "status": "failed",
            "operation": "fetch",
            "reason": _safe_error(fetch.stderr.strip() or fetch.stdout.strip(), token),
            "sha": before,
        }
    if _is_dirty(dest):
        return {
            "status": "dirty",
            "reason": "working tree has uncommitted changes",
            "sha": before,
        }
    if allow_reset:
        reset = _run(["git", "reset", "--hard", "--quiet", "FETCH_HEAD"], cwd=dest)
        if reset.returncode != 0:
            return {
                "status": "failed",
                "operation": "reset",
                "reason": _safe_error(reset.stderr.strip() or reset.stdout.strip(), token),
                "sha": before,
            }
        after = _git_sha(dest)
        return {
            "status": "updated" if before != after else "current",
            "previous_sha": before,
            "sha": after,
        }
    return _fast_forward(dest, token, before)


def _fast_forward(dest: Path, token: str, before: str | None) -> dict:
    upstream = _upstream(dest)
    if not upstream:
        return {
            "status": "current",
            "reason": "no upstream configured",
            "sha": before,
        }
    merge = _run(["git", "merge", "--ff-only", "--quiet", upstream], cwd=dest)
    if merge.returncode != 0 and _is_shallow(dest):
        unshallow = _run(
            ["git", *_auth_args(token), "fetch", "--unshallow", "--quiet", "origin"],
            cwd=dest,
        )
        if unshallow.returncode == 0:
            merge = _run(["git", "merge", "--ff-only", "--quiet", upstream], cwd=dest)
    if merge.returncode != 0:
        return {
            "status": "failed",
            "operation": "merge",
            "reason": _safe_error(
                merge.stderr.strip() or merge.stdout.strip() or "not a fast-forward",
                token,
            ),
            "sha": before,
        }
    after = _git_sha(dest)
    return {
        "status": "updated" if before != after else "current",
        "previous_sha": before,
        "sha": after,
    }


def _is_dirty(dest: Path) -> bool:
    result = _run(["git", "status", "--porcelain"], cwd=dest)
    return bool(result.stdout.strip()) if result.returncode == 0 else True


def _is_shallow(dest: Path) -> bool:
    return (dest / ".git" / "shallow").exists() or (
        _run(["git", "rev-parse", "--is-shallow-repository"], cwd=dest).stdout.strip() == "true"
    )


def _upstream(dest: Path) -> str | None:
    result = _run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=dest)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_sha(dest: Path) -> str | None:
    result = _run(["git", "rev-parse", "HEAD"], cwd=dest)
    return result.stdout.strip() if result.returncode == 0 else None


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": _path()},
    )


def _safe_error(message: str, token: str) -> str:
    """Keep fetch diagnostics without persisting credentials."""
    if not token:
        return message
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return message.replace(token, "[REDACTED]").replace(basic, "[REDACTED]")


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")
