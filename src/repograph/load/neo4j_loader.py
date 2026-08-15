"""Stage 4: idempotent, batched Neo4j loading.

- Uniqueness constraints on `id` per label, created first.
- Nodes:  UNWIND $batch AS n MERGE (s:Label {id: n.id}) SET s += n.props
- Edges:  grouped by (type, src label, dst label) so MATCH uses the id
  index, then MERGE so re-runs update rather than duplicate.
- Batches of 5000 rows per transaction.
"""

from __future__ import annotations

import json

from repograph.ir import Edge, Node

KIND_LABEL = {
    "repo": "Repo",
    "module": "Module",
    "dataset": "Dataset",
    "function": "Symbol",
    "method": "Symbol",
    "class": "Symbol",
}
LABELS = sorted(set(KIND_LABEL.values()))
HISTORY_LABELS = ["IndexRun", "Change"]
BATCH_SIZE = 5000


class Neo4jLoader:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    # ---- schema ----------------------------------------------------------

    def ensure_constraints(self) -> None:
        with self._session() as session:
            for label in LABELS + HISTORY_LABELS:
                session.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (s:{label}) REQUIRE s.id IS UNIQUE"
                ).consume()

    # ---- loading ---------------------------------------------------------

    def load_nodes(self, nodes: list[Node], extra_props: dict | None = None) -> int:
        total = 0
        by_label: dict[str, list[dict]] = {}
        for n in nodes:
            label = KIND_LABEL.get(n.kind)
            if label is None:
                continue
            props = {k: v for k, v in n.to_json().items() if v is not None}
            if extra_props:
                props.update(extra_props)
            by_label.setdefault(label, []).append({"id": n.id, "props": props})

        with self._session() as session:
            for label, rows in by_label.items():
                cypher = (
                    f"UNWIND $batch AS n MERGE (s:{label} {{id: n.id}}) SET s += n.props"
                )
                for batch in _chunks(rows, BATCH_SIZE):
                    session.run(cypher, batch=batch).consume()
                    total += len(batch)
        return total

    def load_edges(self, edges: list[Edge], nodes: list[Node]) -> int:
        """Edges are grouped by (type, src label, dst label) so both MATCH
        clauses hit the per-label id index."""
        label_of = {n.id: KIND_LABEL.get(n.kind) for n in nodes}
        groups: dict[tuple[str, str, str], list[dict]] = {}
        for e in edges:
            src_l, dst_l = label_of.get(e.src), label_of.get(e.dst)
            if not src_l or not dst_l:
                continue
            props = {k: v for k, v in e.meta.items() if v is not None}
            groups.setdefault((e.type, src_l, dst_l), []).append(
                {"src": e.src, "dst": e.dst, "props": props}
            )

        total = 0
        with self._session() as session:
            for (etype, src_l, dst_l), rows in groups.items():
                cypher = (
                    f"UNWIND $batch AS e "
                    f"MATCH (src:{src_l} {{id: e.src}}) "
                    f"MATCH (dst:{dst_l} {{id: e.dst}}) "
                    f"MERGE (src)-[r:{etype}]->(dst) SET r += e.props"
                )
                for batch in _chunks(rows, BATCH_SIZE):
                    session.run(cypher, batch=batch).consume()
                    total += len(batch)
        return total

    def load_index_run(self, run) -> None:
        """Persist an IndexRun plus Change records. Call after upserting nodes
        and before deleting removed ones so TOUCHED can still MATCH symbols."""
        run_props = {
            "id": run.id,
            "sha": run.sha,
            "at": run.at,
            "source": run.source,
            "trigger_repo": run.trigger_repo,
            "repo_shas": json.dumps(run.repo_shas),
            "upserted": run.upserted,
            "deleted": run.deleted,
        }
        changes = [
            {
                "id": f"{run.id}:{c.op}:{c.node_id}",
                "op": c.op,
                "node_id": c.node_id,
                "kind": c.kind,
                "name": c.name,
                "repo": c.repo,
                "path": c.path,
                "owner": c.owner,
                "signature": c.signature,
            }
            for c in run.changes
        ]
        with self._session() as session:
            session.run(
                "MERGE (r:IndexRun {id: $id}) SET r += $props",
                id=run.id, props=run_props,
            ).consume()
            for batch in _chunks(changes, BATCH_SIZE):
                session.run(
                    "UNWIND $batch AS c "
                    "MATCH (r:IndexRun {id: $rid}) "
                    "MERGE (ch:Change {id: c.id}) SET ch += c "
                    "MERGE (r)-[:RECORDED]->(ch)",
                    batch=batch, rid=run.id,
                ).consume()
            upsert_ids = [c.node_id for c in run.changes if c.op == "upsert"]
            for batch in _chunks(upsert_ids, BATCH_SIZE):
                session.run(
                    "UNWIND $batch AS nid "
                    "MATCH (r:IndexRun {id: $rid}) "
                    "MATCH (s {id: nid}) "
                    "MERGE (r)-[:TOUCHED {op: 'upsert'}]->(s)",
                    batch=batch, rid=run.id,
                ).consume()

    def list_index_runs(self, limit: int = 20) -> list[dict]:
        return self.run_query(
            "MATCH (r:IndexRun) RETURN r.id AS id, r.sha AS sha, r.at AS at, "
            "r.source AS source, r.trigger_repo AS trigger_repo, "
            "r.upserted AS upserted, r.deleted AS deleted "
            "ORDER BY r.at DESC LIMIT $limit",
            limit=limit,
        )

    # ---- incremental deletes ----------------------------------------------

    def delete_nodes(self, node_ids: list[str]) -> int:
        if not node_ids:
            return 0
        with self._session() as session:
            for batch in _chunks(node_ids, BATCH_SIZE):
                session.run(
                    "UNWIND $batch AS nid MATCH (s {id: nid}) DETACH DELETE s",
                    batch=batch,
                ).consume()
        return len(node_ids)

    def delete_edges(self, edge_keys: list[tuple[str, str, str]]) -> int:
        """edge_keys: (type, src_id, dst_id)."""
        if not edge_keys:
            return 0
        by_type: dict[str, list[dict]] = {}
        for etype, src, dst in edge_keys:
            by_type.setdefault(etype, []).append({"src": src, "dst": dst})
        with self._session() as session:
            for etype, rows in by_type.items():
                for batch in _chunks(rows, BATCH_SIZE):
                    session.run(
                        f"UNWIND $batch AS e "
                        f"MATCH (src {{id: e.src}})-[r:{etype}]->(dst {{id: e.dst}}) DELETE r",
                        batch=batch,
                    ).consume()
        return len(edge_keys)

    # ---- queries -----------------------------------------------------------

    def run_query(self, cypher: str, **params) -> list[dict]:
        with self._session() as session:
            return [dict(rec) for rec in session.run(cypher, **params)]

    def _session(self):
        return self._driver.session(database=self._database)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
