"""Per-run IR snapshots for incremental reindexing and graph-over-time diffs.

A snapshot records every node's content hash and every edge key. On
reindex, the diff against the last snapshot yields:
  - nodes to upsert (new or changed props)
  - node ids to delete (gone from the IR)
  - edge keys to delete (gone from the IR)
Unchanged nodes are skipped; edges are always MERGEd (idempotent, cheap).

Snapshots are kept under <ir_dir>/snapshots/<timestamp>.json with a
`latest.json` pointer, so historical graph states remain diffable.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from repograph.ir import Edge, Node


@dataclass
class SnapshotDiff:
    upsert_nodes: list[Node] = field(default_factory=list)
    delete_node_ids: list[str] = field(default_factory=list)
    delete_edge_keys: list[tuple[str, str, str]] = field(default_factory=list)
    unchanged: int = 0


def node_hash(node: Node) -> str:
    payload = json.dumps(node.to_json(), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def edge_key(edge: Edge) -> str:
    return f"{edge.type}|{edge.src}|{edge.dst}"


def build_snapshot(nodes: list[Node], edges: list[Edge], run_id: str | None = None) -> dict:
    snapshot = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "nodes": {n.id: node_hash(n) for n in nodes},
        "edges": sorted({edge_key(e) for e in edges}),
    }
    if run_id:
        snapshot["run_id"] = run_id
    return snapshot


def diff_snapshot(previous: dict | None, nodes: list[Node], edges: list[Edge]) -> SnapshotDiff:
    diff = SnapshotDiff()
    prev_nodes = (previous or {}).get("nodes", {})
    prev_edges = set((previous or {}).get("edges", []))

    current_ids = set()
    for n in nodes:
        current_ids.add(n.id)
        if prev_nodes.get(n.id) != node_hash(n):
            diff.upsert_nodes.append(n)
        else:
            diff.unchanged += 1
    diff.delete_node_ids = sorted(set(prev_nodes) - current_ids)

    current_edges = {edge_key(e) for e in edges}
    for stale in sorted(prev_edges - current_edges):
        etype, src, dst = stale.split("|", 2)
        diff.delete_edge_keys.append((etype, src, dst))
    return diff


def save_snapshot(ir_dir: Path, snapshot: dict) -> Path:
    snap_dir = ir_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    path = snap_dir / f"{stamp}.json"
    path.write_text(json.dumps(snapshot, indent=1))
    (snap_dir / "latest.json").write_text(json.dumps(snapshot, indent=1))
    return path


def load_latest_snapshot(ir_dir: Path) -> dict | None:
    latest = ir_dir / "snapshots" / "latest.json"
    if latest.exists():
        return json.loads(latest.read_text())
    return None
