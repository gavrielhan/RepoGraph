"""Clone / fetch selected repos.

- Shallow clones (--depth 1) over HTTPS with the token passed as an
  Authorization header via `git -c`, so it is never written to
  .git/config or shell history.
- Refresh is fetch + fast-forward only. It never ``reset --hard``, so a
  dirty working tree or a diverged feature branch stays intact.
- "Use existing checkout" mode (GitHub Actions workspaces) skips cloning
  when the directory already exists.
- Per-repo path globs limit parsing scope for monorepos / huge repos; the
  globs live on the RepoSpec and are applied at parse time.
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
    """Clone missing repos and fast-forward existing git checkouts."""
    cfg.clone_dir.mkdir(parents=True, exist_ok=True)
    result = FetchResult()
    for spec in repos:
        dest = cfg.clone_dir / spec.name
        if dest.exists() and (dest / ".git").exists():
            if not cfg.use_existing_checkout:
                result.statuses[spec.name] = _refresh(dest, token)
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
    """Fast-forward existing checkouts. Never clones and never resets."""
    result = FetchResult()
    for name, dest in roots.items():
        result.roots[name] = dest
        if (dest / ".git").exists():
            result.statuses[name] = _refresh(dest, token)
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


def _refresh(dest: Path, token: str) -> dict:
    """Update remote refs, then fast-forward the current branch if it is safe.

    Never runs ``reset --hard``. A dirty tree or a non-ff merge leaves HEAD
    and the working tree unchanged and records why.
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
    upstream = _upstream(dest)
    if not upstream:
        return {
            "status": "current",
            "reason": "no upstream configured",
            "sha": before,
        }
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
