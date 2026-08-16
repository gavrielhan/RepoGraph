"""Pipeline orchestration: parse -> IR -> resolve -> load.

Both the interactive path (`activate`) and the headless path (`run`) call
into this module once they have a token and a repo list — that is the seam
between the two modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repograph.config import Config
from repograph.ir import IRError, Edge, Node, load_nodes, load_resolved_edges, write_jsonl
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
    fetch_status: dict[str, dict] | None = None
    consistency_recovery: bool = False

    def summary(self) -> str:
        run = f" run={self.run_id}" if self.run_id else ""
        failed = sorted(
            repo for repo, status in (self.fetch_status or {}).items()
            if status.get("status") == "failed"
        )
        fetch = f" WARNING fetch-failed={','.join(failed)}" if failed else ""
        recovery = " WARNING neo4j-snapshot-mismatch=full-load" if self.consistency_recovery else ""
        return (
            f"nodes={self.nodes} pending={self.pending_edges} resolved={self.resolved_edges} | "
            f"loaded {self.loaded_nodes} nodes / {self.loaded_edges} edges, "
            f"deleted {self.deleted_nodes} nodes / {self.deleted_edges} edges, "
            f"unchanged {self.unchanged_nodes}{run}{fetch}{recovery}"
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
    nodes_path = cfg.ir_dir / "nodes.jsonl"
    edges_path = cfg.ir_dir / "edges.jsonl"
    missing = [str(path) for path in (nodes_path, edges_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "graph IR is missing: "
            + ", ".join(missing)
            + f". Run `repograph run`/`reindex`, or set --ir-dir (resolved to {cfg.ir_dir})."
        )
    try:
        nodes = load_nodes(nodes_path)
        edges = load_resolved_edges(edges_path)
    except IRError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise IRError(f"graph IR is unreadable at {cfg.ir_dir}: {exc}") from exc
    return nodes, edges


def run_pipeline(
    cfg: Config,
    repo_roots: dict[str, Path],
    path_globs: dict[str, list[str]] | None = None,
    full: bool = False,
    skip_load: bool = False,
    fetch_status: dict[str, dict] | None = None,
) -> PipelineStats:
    """Full pipeline. `full=False` loads incrementally against the last
    snapshot; `full=True` rewrites every node."""
    stats = PipelineStats()
    stats.fetch_status = fetch_status or {}
    nodes, edges = build_ir(cfg, repo_roots, path_globs)
    stats.nodes = len(nodes)
    stats.resolved_edges = len(edges)

    previous = None if full else load_latest_snapshot(cfg.ir_dir)
    loader = None
    try:
        if not skip_load and cfg.neo4j.password:
            loader = Neo4jLoader(
                cfg.neo4j.uri, cfg.neo4j.user, cfg.neo4j.password, cfg.neo4j.database
            )
            loader.ensure_constraints()
            if previous:
                snapshot_run_id = previous.get("run_id")
                database_run_id = loader.graph_state_run_id()
                if snapshot_run_id != database_run_id:
                    # Snapshot and Neo4j disagree: upsert every current node.
                    # Do not delete first — DETACH DELETE would drop historical
                    # (:IndexRun)-[:TOUCHED]->(:Symbol) edges, and a missing
                    # local snapshot (gitignore, unrestored CI graph) must not
                    # tear down a shared database.
                    previous = None
                    stats.consistency_recovery = True

        diff = diff_snapshot(previous, nodes, edges)
        stats.unchanged_nodes = diff.unchanged
        run = build_index_run(diff, nodes, repo_roots, fetch_status=fetch_status)
        stats.run_id = run.id

        if loader is None:
            append_run(cfg.ir_dir, run)
            save_snapshot(cfg.ir_dir, build_snapshot(nodes, edges, run_id=run.id))
            return stats

        extra = {"git_sha": run.sha, "indexed_at": run.at}
        stats.loaded_nodes = loader.load_nodes(diff.upsert_nodes, extra_props=extra)
        stats.loaded_edges = loader.load_edges(edges, nodes)
        loader.load_index_run(run)
        stats.deleted_edges = loader.delete_edges(diff.delete_edge_keys)
        stats.deleted_nodes = loader.delete_nodes(diff.delete_node_ids)
        loader.set_graph_state(run)
        append_run(cfg.ir_dir, run)
        save_snapshot(cfg.ir_dir, build_snapshot(nodes, edges, run_id=run.id))
    finally:
        if loader is not None:
            loader.close()
    return stats
