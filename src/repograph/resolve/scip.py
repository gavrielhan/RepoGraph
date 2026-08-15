"""SCIP integration seam.

Fuzzy name-matching in the resolver gets roughly 80% recall and misses
dynamic dispatch. When precision proves insufficient for a language, run a
SCIP indexer (scip-python, scip-typescript, scip-java, ...) over the
checkout and feed its output through this module. The contract:

    edges = load_scip_edges(index_path, repo_name)
    extra_nodes, resolved = resolve(nodes, pending, repo_roots,
                                    preresolved=edges)

`load_scip_edges` must return `repograph.ir.Edge` records whose src/dst use
the standard ID scheme (repo::path::qualname). The resolver merges them at
confidence 1.0 and they win over fuzzy matches for the same (type, src,
dst). Nothing downstream (loader, queries) changes — that is the point of
the seam: SCIP swaps in per-language without touching Stage 4/5.

Implementation sketch (not yet wired):
  1. `scip-python index . --output index.scip` in the checkout.
  2. Parse the protobuf (`pip install scip-proto` or generated bindings).
  3. For every occurrence with `is_definition == False`, map
     `symbol` -> its definition document/range, convert both sides to
     repograph IDs via the document path + enclosing-symbol name, and emit
     CALLS/IMPORTS edges.
"""

from __future__ import annotations

from pathlib import Path

from repograph.ir import Edge


def load_scip_edges(index_path: Path, repo_name: str) -> list[Edge]:
    raise NotImplementedError(
        "SCIP ingestion is a documented integration seam, not yet implemented. "
        "See module docstring for the contract; converted edges plug into "
        "resolve(..., preresolved=edges) without loader changes."
    )
