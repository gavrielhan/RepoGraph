"""Stage 5: blast-radius query.

Reverse reachability from a changed symbol across dependency edges. Edge
direction convention: an edge X -[:CALLS|IMPORTS|CONSUMES|INHERITS]-> Y
means "X depends on Y", so everything that can reach the changed node over
those edges is affected by a change to it.

Results carry the minimum edge confidence along the path so consumers can
rank instead of hard-gating (fuzzy resolution means recall < 100%).
"""

from __future__ import annotations

from repograph.load.neo4j_loader import Neo4jLoader

DEPENDENCY_EDGES = "CALLS|IMPORTS|CONSUMES|INHERITS"

BLAST_RADIUS_CYPHER = f"""
MATCH (changed {{id: $id}})
MATCH path = (changed)<-[:{DEPENDENCY_EDGES}*1..%d]-(affected)
WITH affected, path,
     reduce(c = 1.0, r IN relationships(path) |
            CASE WHEN r.confidence IS NULL THEN c
                 WHEN r.confidence < c THEN r.confidence ELSE c END) AS confidence
RETURN DISTINCT affected.id AS id,
                affected.repo AS repo,
                affected.path AS path,
                affected.owner AS owner,
                max(confidence) AS confidence,
                min(length(path)) AS distance
ORDER BY distance, confidence DESC, repo, path
"""


def blast_radius(loader: Neo4jLoader, symbol_id: str, max_depth: int = 10) -> list[dict]:
    return loader.run_query(BLAST_RADIUS_CYPHER % max_depth, id=symbol_id)


def format_results(results: list[dict], changed_id: str) -> str:
    if not results:
        return f"No downstream dependents found for {changed_id}."
    lines = [f"Blast radius for {changed_id} ({len(results)} affected):", ""]
    owners: dict[str, list[dict]] = {}
    for r in results:
        owners.setdefault(r.get("owner") or "(no owner)", []).append(r)
    for owner in sorted(owners):
        lines.append(f"  {owner}")
        for r in owners[owner]:
            conf = r.get("confidence")
            conf_s = f"  conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
            lines.append(f"    {r['id']}  (repo={r['repo']}, dist={r['distance']}{conf_s})")
        lines.append("")
    return "\n".join(lines).rstrip()
