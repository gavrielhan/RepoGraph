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


DEPENDENCY_REL = {"CALLS", "IMPORTS", "CONSUMES", "INHERITS"}


def blast_radius(loader: Neo4jLoader, symbol_id: str, max_depth: int = 10) -> list[dict]:
    return loader.run_query(BLAST_RADIUS_CYPHER % max_depth, id=symbol_id)


def blast_radius_ir(nodes: list, edges: list, symbol_id: str, max_depth: int = 10) -> list[dict]:
    """Same reverse reachability over the JSONL IR — used when Neo4j is not configured
    (graph files committed to GitHub)."""
    from collections import deque

    by_id = {n.id: n for n in nodes}
    inbound: dict[str, list[tuple[str, float]]] = {}
    for e in edges:
        if e.type not in DEPENDENCY_REL:
            continue
        conf = e.meta.get("confidence")
        inbound.setdefault(e.dst, []).append((e.src, conf if isinstance(conf, (int, float)) else 1.0))

    best: dict[str, tuple[int, float]] = {}
    q = deque()
    for src, edge_conf in inbound.get(symbol_id, []):
        best[src] = (1, edge_conf)
        q.append(src)
    while q:
        nid = q.popleft()
        dist, conf = best[nid]
        if dist >= max_depth:
            continue
        for src, edge_conf in inbound.get(nid, []):
            next_dist, next_conf = dist + 1, min(conf, edge_conf)
            prev = best.get(src)
            if prev is None or next_dist < prev[0] or (next_dist == prev[0] and next_conf > prev[1]):
                best[src] = (next_dist, next_conf)
                q.append(src)

    rows = []
    for nid, (dist, conf) in best.items():
        n = by_id.get(nid)
        rows.append({
            "id": nid,
            "repo": n.repo if n else None,
            "path": n.path if n else None,
            "owner": n.owner if n else None,
            "confidence": conf,
            "distance": dist,
        })
    rows.sort(key=lambda r: (r["distance"], -(r["confidence"] or 0), r.get("repo") or "", r.get("path") or ""))
    return rows


def format_results(
    results: list[dict],
    changed_id: str,
    changed_ids: list[str] | None = None,
) -> str:
    changed_ids = changed_ids or []
    if not results:
        body = [f"No downstream dependents found for {changed_id}."]
        if len(changed_ids) > 1:
            body.append("")
            body.append("Changed symbols:")
            body.extend(f"  {cid}" for cid in changed_ids)
        return "\n".join(body)
    lines = [f"Blast radius for {changed_id} ({len(results)} affected):", ""]
    if len(changed_ids) > 1:
        lines.append("Changed symbols:")
        lines.extend(f"  {cid}" for cid in changed_ids)
        lines.append("")
    owners: dict[str, list[dict]] = {}
    for r in results:
        owners.setdefault(r.get("owner") or "(no owner)", []).append(r)
    lines.append("Affected:")
    for owner in sorted(owners):
        lines.append(f"  {owner}")
        for r in owners[owner]:
            conf = r.get("confidence")
            conf_s = f"  conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
            lines.append(f"    {r['id']}  (repo={r['repo']}, dist={r['distance']}{conf_s})")
        lines.append("")
    return "\n".join(lines).rstrip()
