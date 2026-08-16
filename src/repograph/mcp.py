"""Local stdio MCP server for RepoGraph's portable JSONL graph."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from repograph.config import Config, load_config
from repograph.load.history import freshness
from repograph.pipeline import load_ir
from repograph.query.blast_radius import blast_radius_ir
from repograph.query.find import find_symbols as lookup_symbols


class GraphCache:
    """Reload IR whenever either JSONL file changes."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.nodes = []
        self.edges = []
        self._stamp: tuple | None = None

    def load(self, force: bool = False):
        stamp = tuple(
            (path.stat().st_mtime_ns, path.stat().st_size)
            for path in (
                self.cfg.ir_dir / "nodes.jsonl",
                self.cfg.ir_dir / "edges.jsonl",
            )
            if path.exists()
        )
        if force or stamp != self._stamp or len(stamp) != 2:
            self.nodes, self.edges = load_ir(self.cfg)
            self._stamp = stamp
        return self.nodes, self.edges


def create_server(cfg: Config) -> MCPServer:
    cache = GraphCache(cfg)
    server = MCPServer(
        "RepoGraph",
        instructions=(
            "Use find_symbols before blast_radius when you do not have an exact "
            "RepoGraph symbol ID. Always inspect freshness in blast-radius results."
        ),
    )

    @server.tool(structured_output=True)
    def find_symbols(pattern: str, limit: int = 20) -> dict[str, Any]:
        """Find indexed symbols from a partial name, path, signature, or ID.

        Use this before blast_radius instead of guessing a repo::path::qualname
        ID. An empty match list means no indexed symbol matched; it does not
        prove that the symbol is absent from source code.
        """
        nodes, _ = cache.load()
        return {
            "freshness": freshness(cfg.ir_dir),
            "matches": lookup_symbols(nodes, pattern, limit=limit),
        }

    @server.tool(structured_output=True)
    def blast_radius(symbol_id: str, max_depth: int = 10) -> dict[str, Any]:
        """Return ranked downstream impact evidence for one exact symbol ID.

        This is static-analysis evidence, not a perfect hard gate: dynamic
        imports, reflection, generated code, and unresolved calls can be
        missing. Treat stale or failed-fetch freshness as a warning that the
        result may be incomplete. An empty result is not proof of safety when
        the symbol is absent or the graph is stale.
        """
        nodes, edges = cache.load()
        known_ids = {node.id for node in nodes}
        if symbol_id not in known_ids:
            return {
                "freshness": freshness(cfg.ir_dir),
                "symbol_id": symbol_id,
                "symbol_found": False,
                "error": "Symbol ID is not present in the indexed graph; call find_symbols.",
                "count": 0,
                "results": [],
            }
        results = blast_radius_ir(nodes, edges, symbol_id, max_depth=max_depth)
        return {
            "freshness": freshness(cfg.ir_dir),
            "symbol_id": symbol_id,
            "symbol_found": True,
            "count": len(results),
            "results": results,
        }

    @server.tool(structured_output=True)
    def graph_freshness() -> dict[str, Any]:
        """Report when and from which repository SHAs the graph was indexed."""
        return freshness(cfg.ir_dir)

    @server.tool(structured_output=True)
    def refresh() -> dict[str, Any]:
        """Reload graph JSONL files after an external RepoGraph reindex.

        The server also detects JSONL mtime/size changes automatically. This
        tool only invalidates the in-process cache; it does not fetch repos or
        run an index build.
        """
        nodes, edges = cache.load(force=True)
        return {
            "freshness": freshness(cfg.ir_dir),
            "nodes": len(nodes),
            "edges": len(edges),
        }

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve RepoGraph tools over MCP stdio.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(os.environ.get("REPOGRAPH_PROJECT_ROOT", Path.cwd())),
        help="Directory containing repograph.yaml (default: cwd or REPOGRAPH_PROJECT_ROOT).",
    )
    args = parser.parse_args()
    create_server(load_config(cwd=args.project_root)).run("stdio")


if __name__ == "__main__":
    main()
