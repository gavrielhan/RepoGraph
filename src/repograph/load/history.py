"""Index-run history: what changed in each graph build.

The live Neo4j graph is still current-only. Each pipeline run appends an
IndexRun so you can ask "what changed in this commit?" without a temporal
graph.

Two stores:
  - ``<ir_dir>/runs.jsonl`` — git-friendly, can live in a GitHub repo
  - ``(:IndexRun)-[:RECORDED]->(:Change)`` in Neo4j when a database is configured

GitHub cannot host Neo4j. Actions *can* refresh both stores on every push.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from repograph.ids import parse_id
from repograph.ir import Node
from repograph.load.snapshot import SnapshotDiff

RUNS_FILENAME = "runs.jsonl"


@dataclass
class Change:
    op: str  # upsert | delete
    node_id: str
    kind: str | None = None
    name: str | None = None
    repo: str | None = None
    path: str | None = None
    owner: str | None = None
    signature: str | None = None


@dataclass
class IndexRun:
    id: str
    sha: str
    at: str
    source: str  # ci | cli
    trigger_repo: str = ""
    repo_shas: dict[str, str] = field(default_factory=dict)
    upserted: int = 0
    deleted: int = 0
    changes: list[Change] = field(default_factory=list)

    def to_json(self) -> dict:
        payload = asdict(self)
        return payload


def collect_repo_shas(repo_roots: dict[str, Path]) -> dict[str, str]:
    shas: dict[str, str] = {}
    for name, root in repo_roots.items():
        sha = _git_sha(root)
        if sha:
            shas[name] = sha
    return shas


def trigger_sha(repo_shas: dict[str, str]) -> str:
    env = os.environ.get("GITHUB_SHA") or os.environ.get("REPOGRAPH_GIT_SHA") or ""
    if env:
        return env
    cwd = _git_sha(Path.cwd())
    if cwd:
        return cwd
    return next(iter(repo_shas.values()), "unknown")


def trigger_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or Path.cwd().name


def source_label() -> str:
    return "ci" if os.environ.get("CI", "").lower() == "true" else "cli"


def build_index_run(
    diff: SnapshotDiff,
    nodes: list[Node],
    repo_roots: dict[str, Path],
) -> IndexRun:
    by_id = {n.id: n for n in nodes}
    repo_shas = collect_repo_shas(repo_roots)
    sha = trigger_sha(repo_shas)
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_id = f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{sha[:12]}"

    changes: list[Change] = []
    for n in diff.upsert_nodes:
        changes.append(
            Change(
                op="upsert",
                node_id=n.id,
                kind=n.kind,
                name=n.name,
                repo=n.repo,
                path=n.path,
                owner=n.owner,
                signature=n.signature,
            )
        )
    for nid in diff.delete_node_ids:
        prev = by_id.get(nid)
        parsed = parse_id(nid)
        changes.append(
            Change(
                op="delete",
                node_id=nid,
                kind=prev.kind if prev else None,
                name=prev.name if prev else parsed.get("qualname"),
                repo=prev.repo if prev else parsed.get("repo"),
                path=prev.path if prev else parsed.get("path"),
            )
        )

    return IndexRun(
        id=run_id,
        sha=sha,
        at=at,
        source=source_label(),
        trigger_repo=trigger_repo(),
        repo_shas=repo_shas,
        upserted=len(diff.upsert_nodes),
        deleted=len(diff.delete_node_ids),
        changes=changes,
    )


def append_run(ir_dir: Path, run: IndexRun) -> Path:
    ir_dir.mkdir(parents=True, exist_ok=True)
    path = ir_dir / RUNS_FILENAME
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(run.to_json(), ensure_ascii=False) + "\n")
    return path


def load_runs(ir_dir: Path) -> list[IndexRun]:
    path = ir_dir / RUNS_FILENAME
    if not path.exists():
        return []
    runs: list[IndexRun] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rec["changes"] = [Change(**c) for c in rec.get("changes", [])]
        runs.append(IndexRun(**rec))
    return runs


def latest_run(ir_dir: Path) -> IndexRun | None:
    runs = load_runs(ir_dir)
    return runs[-1] if runs else None


def freshness(ir_dir: Path, stale_after_days: int = 7) -> dict:
    """Return machine-readable graph freshness for CLI and agent consumers."""
    run = latest_run(ir_dir)
    if run is None:
        return {
            "available": False,
            "stale": True,
            "warning": f"No index history found in {ir_dir / RUNS_FILENAME}.",
            "ir_dir": str(ir_dir),
        }

    invalid_timestamp = False
    try:
        indexed_at = datetime.fromisoformat(run.at.replace("Z", "+00:00"))
        age_seconds = max(0, int((datetime.now(timezone.utc) - indexed_at).total_seconds()))
    except ValueError:
        age_seconds = 0
        invalid_timestamp = True
    stale = age_seconds > stale_after_days * 86400
    fetch_status = getattr(run, "fetch_status", {}) or {}
    failed_fetches = sorted(
        repo for repo, status in fetch_status.items()
        if status.get("status") == "failed"
    )
    warnings = []
    if invalid_timestamp:
        warnings.append("index timestamp is invalid")
    if stale:
        warnings.append(f"graph is older than {stale_after_days} days")
    if failed_fetches:
        warnings.append("fetch failed for " + ", ".join(failed_fetches))
    return {
        "available": True,
        "run_id": run.id,
        "sha": run.sha,
        "indexed_at": run.at,
        "age_seconds": age_seconds,
        "age_days": round(age_seconds / 86400, 2),
        "stale": stale or bool(failed_fetches) or invalid_timestamp,
        "repo_count": len(run.repo_shas),
        "repo_shas": run.repo_shas,
        "fetch_status": fetch_status,
        "warning": "; ".join(warnings) if warnings else None,
        "ir_dir": str(ir_dir),
    }


def format_freshness(info: dict) -> str:
    if not info.get("available"):
        return f"WARNING: {info['warning']}"
    age = _format_age(info["age_seconds"])
    line = (
        f"Graph indexed {info['indexed_at']} ({age} ago), "
        f"sha={info['sha'][:12]}, repos={info['repo_count']}."
    )
    if info.get("warning"):
        return f"WARNING: {info['warning']}.\n{line}"
    return line


def format_runs(runs: list[IndexRun], limit: int = 20) -> str:
    if not runs:
        return "No index runs recorded yet."
    lines = []
    for run in reversed(runs[-limit:]):
        repos = ", ".join(f"{k}@{v[:8]}" for k, v in sorted(run.repo_shas.items())) or "(no shas)"
        lines.append(
            f"{run.id}  sha={run.sha[:12]}  {run.source}  "
            f"+{run.upserted}/-{run.deleted}  {run.trigger_repo}  {repos}"
        )
    return "\n".join(lines)


def format_run_changes(run: IndexRun) -> str:
    lines = [
        f"Index run {run.id}",
        f"  sha={run.sha}  at={run.at}  source={run.source}",
        f"  upserted={run.upserted}  deleted={run.deleted}",
        "",
    ]
    for c in run.changes:
        loc = c.path or ""
        lines.append(f"  {c.op:6} {c.node_id}  {c.kind or ''} {loc}".rstrip())
    return "\n".join(lines).rstrip()


def _git_sha(root: Path) -> str | None:
    if not root.exists():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
