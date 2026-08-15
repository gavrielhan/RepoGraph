"""Map a git / PR diff to changed node IDs.

Line mapping uses both sides of each hunk: the old side matches the last
indexed snapshot (usually the default branch), and the new side catches
edits whose lines moved. Deleted files hit every symbol on that path.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repograph.ir import Node

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_REMOTE_RE = re.compile(
    r"(?:github\.com[:/]|git@[^:]+:)(?P<owner>[^/]+)/(?P<name>[^/.]+?)(?:\.git)?$"
)


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    added: bool = False
    deleted: bool = False
    old_ranges: list[tuple[int, int]] = field(default_factory=list)
    new_ranges: list[tuple[int, int]] = field(default_factory=list)


def parse_unified_diff(text: str, default_path: str | None = None) -> list[FileDiff]:
    """Parse a unified diff into per-file line ranges (old and new sides)."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    old_path: str | None = None

    def flush():
        nonlocal current
        if current is not None:
            files.append(current)
            current = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            old_path = None
            continue
        if line.startswith("--- "):
            rest = line[4:].strip()
            if rest.startswith("a/"):
                rest = rest[2:]
            old_path = None if rest == "/dev/null" else rest
            continue
        if line.startswith("+++ "):
            rest = line[4:].strip()
            if rest.startswith("b/"):
                rest = rest[2:]
            new_path = None if rest == "/dev/null" else rest
            path = new_path or old_path or default_path
            if path is None:
                continue
            current = FileDiff(
                path=path,
                old_path=old_path,
                added=old_path is None,
                deleted=new_path is None,
            )
            continue
        if (m := _HUNK_RE.match(line)):
            if current is None:
                if default_path is None:
                    continue
                current = FileDiff(path=default_path, old_path=default_path)
            old_start, old_count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            new_start, new_count = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            if old_count > 0:
                current.old_ranges.append((old_start, old_start + old_count - 1))
            elif old_start > 0:
                current.old_ranges.append((old_start, old_start))
            if new_count > 0:
                current.new_ranges.append((new_start, new_start + new_count - 1))
            elif new_start > 0:
                current.new_ranges.append((new_start, new_start))
    flush()
    return files


def ranges_from_file_diffs(diffs: list[FileDiff]) -> dict[str, list[tuple[int, int]]]:
    """Union of old+new ranges keyed by the path that exists in the IR (old path
    for deletes/renames, otherwise the new path)."""
    out: dict[str, list[tuple[int, int]]] = {}
    for d in diffs:
        key = d.old_path if d.deleted and d.old_path else d.path
        combined = list(d.old_ranges) + list(d.new_ranges)
        if d.deleted:
            combined.append((1, 10_000_000))  # whole file
        if combined:
            out.setdefault(key, []).extend(combined)
        if d.old_path and d.old_path != d.path and not d.deleted:
            out.setdefault(d.old_path, []).extend(d.old_ranges or combined)
    return out


def changed_line_ranges(repo_root: Path, ref: str = "HEAD") -> dict[str, list[tuple[int, int]]]:
    """{repo-relative path: [(start_line, end_line), ...]} vs `ref`.

    Includes the working tree (uncommitted changes). Use `ref...HEAD` as
    `ref` for committed-only three-dot diffs.
    """
    out = subprocess.run(
        ["git", "diff", "--unified=0", ref, "--"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    return ranges_from_file_diffs(parse_unified_diff(out))


def changed_node_ids(
    nodes: list[Node], repo: str, ranges: dict[str, list[tuple[int, int]]]
) -> list[str]:
    """Symbols whose line span intersects a changed range, plus changed modules."""
    hit: list[str] = []
    for n in nodes:
        if n.repo != repo or not n.path or n.path not in ranges:
            continue
        if n.kind == "module":
            hit.append(n.id)
            continue
        if n.start_line is None or n.end_line is None:
            continue
        for start, end in ranges[n.path]:
            if n.start_line <= end and start <= n.end_line:
                hit.append(n.id)
                break
    return hit


# --------------------------------------------------------------------------
# local branch / git identity


def git_root(start: Path | None = None) -> Path | None:
    cwd = start or Path.cwd()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def current_branch(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def default_base_ref(repo_root: Path) -> str:
    """Best guess at the branch a PR would target: origin/HEAD, then main/master."""
    candidates = []
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if symbolic.returncode == 0:
        ref = symbolic.stdout.strip()
        if ref.startswith("refs/remotes/"):
            candidates.append(ref[len("refs/remotes/"):])
        else:
            candidates.append(ref)
    candidates.extend(["origin/main", "origin/master", "main", "master"])
    for ref in candidates:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=repo_root, capture_output=True, text=True,
        )
        if probe.returncode == 0:
            return ref
    return "HEAD"


def merge_base(repo_root: Path, ref: str, base: str) -> str:
    result = subprocess.run(
        ["git", "merge-base", ref, base],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return base


def branch_diff_ranges(
    repo_root: Path,
    branch: str = "HEAD",
    base: str | None = None,
    include_uncommitted: bool = True,
) -> tuple[str, dict[str, list[tuple[int, int]]]]:
    """Diff a local branch against the merge-base of `base`.

    Returns (resolved_base_ref, path -> ranges). Uncommitted changes are
    included when `branch` is HEAD and `include_uncommitted` is true — that
    is the "what would my PR look like right now" view.
    """
    base_ref = base or default_base_ref(repo_root)
    mb = merge_base(repo_root, branch, base_ref)
    if include_uncommitted and branch in ("HEAD", current_branch(repo_root)):
        spec = mb
    else:
        spec = f"{mb}...{branch}"
    ranges = changed_line_ranges(repo_root, spec)
    return base_ref, ranges


def remote_identity(repo_root: Path) -> tuple[str, str] | None:
    """(owner, name) from origin, or None if not a GitHub remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root, capture_output=True, text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return parse_github_remote(result.stdout.strip())


def parse_github_remote(url: str) -> tuple[str, str] | None:
    url = url.strip()
    m = _REMOTE_RE.search(url)
    if not m:
        return None
    return m.group("owner"), m.group("name")


def infer_graph_repo(repo_root: Path, nodes: list[Node], hint: str | None = None) -> str | None:
    """Map a checkout onto a graph `repo` name (short name used in node IDs)."""
    candidates: list[str] = []
    if hint:
        candidates.append(hint)
    identity = remote_identity(repo_root)
    if identity:
        candidates.append(identity[1])
    candidates.append(repo_root.name)

    indexed = {n.repo for n in nodes if n.repo}
    if not indexed:
        return candidates[0] if candidates else None

    def norm(s: str) -> str:
        return s.lower().replace("-", "_")

    indexed_norm = {norm(r): r for r in indexed}
    for cand in candidates:
        if cand in indexed:
            return cand
        if norm(cand) in indexed_norm:
            return indexed_norm[norm(cand)]
    return candidates[0] if candidates else None
