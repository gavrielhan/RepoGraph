"""Map a git diff to changed node IDs.

`repograph query --diff <ref>` runs `git diff` in each checkout, parses
hunk headers into changed line ranges, and intersects them with symbol
line ranges from the IR. The resulting node IDs feed the blast-radius
query.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from repograph.ir import Node

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_line_ranges(repo_root: Path, ref: str = "HEAD") -> dict[str, list[tuple[int, int]]]:
    """{repo-relative path: [(start_line, end_line), ...]} of new-side changes."""
    out = subprocess.run(
        ["git", "diff", "--unified=0", ref, "--"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+++ /dev/null"):
            current = None
        elif current and (m := _HUNK_RE.match(line)):
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            end = start + max(count - 1, 0)
            ranges.setdefault(current, []).append((start, end))
    return ranges


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
