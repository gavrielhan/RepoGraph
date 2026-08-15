"""Pipeline orchestration: parse -> IR -> resolve -> load.

Both the interactive path (`activate`) and the headless path (`run`) call
into this module once they have a token and a repo list — that is the seam
between the two modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repograph.config import Config
from repograph.ir import Edge, Node, load_nodes, load_resolved_edges, write_jsonl
from repograph.load.history import append_run, build_index_run
from repograph.load.neo4j_loader import Neo4jLoader
from repograph.load.snapshot import (
    build_snapshot,
    diff_snapshot,
    load_latest_snapshot,
    save_snapshot,
)
from repograph.parse.engine import ParseResult, parse_repo
from repograph.resolve import resolve


@dataclass
class PipelineStats:
    nodes: int = 0
    pending_edges: int = 0
    resolved_edges: int = 0
    loaded_nodes: int = 0
    loaded_edges: int = 0
    deleted_nodes: int = 0
    deleted_edges: int = 0
    unchanged_nodes: int = 0
    run_id: str = ""

    def summary(self) -> str:
        run = f" run={self.run_id}" if self.run_id else ""
        return (
            f"nodes={self.nodes} pending={self.pending_edges} resolved={self.resolved_edges} | "
            f"loaded {self.loaded_nodes} nodes / {self.loaded_edges} edges, "
            f"deleted {self.deleted_nodes} nodes / {self.deleted_edges} edges, "
            f"unchanged {self.unchanged_nodes}{run}"
        )


def build_ir(cfg: Config, repo_roots: dict[str, Path], path_globs: dict[str, list[str]] | None = None) -> tuple[list[Node], list[Edge]]:
    """Stages 1-3: parse all repos, resolve, write IR files. Returns (nodes, edges)."""
    combined = ParseResult()
    for repo, root in repo_roots.items():
        globs = (path_globs or {}).get(repo) or None
        combined.extend(parse_repo(repo, root, languages=cfg.languages or None, path_globs=globs))

    dataset_nodes, resolved = resolve(
        combined.nodes,
        combined.pending,
        repo_roots,
        owner_registry=cfg.owner_registry,
    )
    all_nodes = combined.nodes + dataset_nodes
    all_edges = combined.edges + resolved  # structural CONTAINS/DEFINES + resolved

    cfg.ir_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(cfg.ir_dir / "nodes.jsonl", all_nodes)
    write_jsonl(cfg.ir_dir / "edges.jsonl", list(combined.pending) + all_edges)
    return all_nodes, all_edges


def load_ir(cfg: Config) -> tuple[list[Node], list[Edge]]:
    """Read the last-written IR from disk."""
    nodes = load_nodes(cfg.ir_dir / "nodes.jsonl")
    edges = load_resolved_edges(cfg.ir_dir / "edges.jsonl")
    return nodes, edges


def run_pipeline(
    cfg: Config,
    repo_roots: dict[str, Path],
    path_globs: dict[str, list[str]] | None = None,
    full: bool = False,
    skip_load: bool = False,
) -> PipelineStats:
    """Full pipeline. `full=False` loads incrementally against the last
    snapshot; `full=True` rewrites every node."""
    stats = PipelineStats()
    nodes, edges = build_ir(cfg, repo_roots, path_globs)
    stats.nodes = len(nodes)
    stats.resolved_edges = len(edges)

    previous = None if full else load_latest_snapshot(cfg.ir_dir)
    diff = diff_snapshot(previous, nodes, edges)
    stats.unchanged_nodes = diff.unchanged
    run = build_index_run(diff, nodes, repo_roots)
    append_run(cfg.ir_dir, run)
    stats.run_id = run.id

    if skip_load or not cfg.neo4j.password:
        save_snapshot(cfg.ir_dir, build_snapshot(nodes, edges))
        return stats

    loader = Neo4jLoader(
        cfg.neo4j.uri, cfg.neo4j.user, cfg.neo4j.password, cfg.neo4j.database
    )
    try:
        loader.ensure_constraints()
        extra = {"git_sha": run.sha, "indexed_at": run.at}
        stats.loaded_nodes = loader.load_nodes(diff.upsert_nodes, extra_props=extra)
        stats.loaded_edges = loader.load_edges(edges, nodes)
        loader.load_index_run(run)
        stats.deleted_edges = loader.delete_edges(diff.delete_edge_keys)
        stats.deleted_nodes = loader.delete_nodes(diff.delete_node_ids)
    finally:
        loader.close()

    save_snapshot(cfg.ir_dir, build_snapshot(nodes, edges))
    return stats
