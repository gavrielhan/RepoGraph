"""Loader unit tests against a fake driver (no live Neo4j needed).

The end-to-end path against a real Neo4j is exercised separately (see
README); these tests pin the Cypher shapes and batching behavior.
"""

from unittest.mock import patch

from repograph.ir import Edge, Node
from repograph.load.neo4j_loader import Neo4jLoader


class FakeSession:
    def __init__(self, log):
        self.log = log

    def run(self, cypher, **params):
        self.log.append((" ".join(cypher.split()), params))

        class R:
            def consume(self):
                return None

            def __iter__(self):
                return iter([])

        return R()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, log):
        self.log = log

    def session(self, database=None):
        return FakeSession(self.log)

    def close(self):
        pass


def make_loader(log):
    with patch("neo4j.GraphDatabase.driver", return_value=FakeDriver(log)):
        return Neo4jLoader("bolt://x", "u", "p")


def test_constraints_per_label():
    log = []
    make_loader(log).ensure_constraints()
    cyphers = [c for c, _ in log]
    assert any("FOR (s:Symbol) REQUIRE s.id IS UNIQUE" in c for c in cyphers)
    assert any("FOR (s:Module) REQUIRE s.id IS UNIQUE" in c for c in cyphers)
    assert all("IF NOT EXISTS" in c for c in cyphers)


def test_nodes_merged_by_label():
    log = []
    loader = make_loader(log)
    n = loader.load_nodes(
        [
            Node(id="r::a.py::f", kind="function", name="f", repo="r"),
            Node(id="r::a.py", kind="module", name="a.py", repo="r"),
            Node(id="r", kind="repo", name="r", repo="r"),
        ]
    )
    assert n == 3
    cyphers = [c for c, _ in log]
    assert "UNWIND $batch AS n MERGE (s:Symbol {id: n.id}) SET s += n.props" in cyphers
    assert "UNWIND $batch AS n MERGE (s:Module {id: n.id}) SET s += n.props" in cyphers


def test_edges_grouped_by_type_and_labels():
    log = []
    loader = make_loader(log)
    nodes = [
        Node(id="r::a.py::f", kind="function", name="f", repo="r"),
        Node(id="r::b.py::g", kind="function", name="g", repo="r"),
        Node(id="r::a.py", kind="module", name="a.py", repo="r"),
    ]
    n = loader.load_edges(
        [
            Edge("CALLS", "r::a.py::f", "r::b.py::g", {"confidence": 0.9}),
            Edge("CONTAINS", "r::a.py", "r::a.py::f"),
            Edge("CALLS", "r::a.py::f", "missing::x", {}),  # dropped: unknown dst
        ],
        nodes,
    )
    assert n == 2
    cyphers = [c for c, _ in log]
    assert any(
        "MATCH (src:Symbol {id: e.src}) MATCH (dst:Symbol {id: e.dst}) MERGE (src)-[r:CALLS]->(dst)" in c
        for c in cyphers
    )
    assert any("MATCH (src:Module {id: e.src})" in c and "MERGE (src)-[r:CONTAINS]->(dst)" in c for c in cyphers)


def test_batching():
    log = []
    loader = make_loader(log)
    nodes = [Node(id=f"r::a.py::f{i}", kind="function", name=f"f{i}", repo="r") for i in range(12000)]
    loader.load_nodes(nodes)
    batches = [len(p["batch"]) for _, p in log]
    assert batches == [5000, 5000, 2000]


def test_delete_nodes_detach():
    log = []
    make_loader(log).delete_nodes(["r::a.py::f"])
    assert "DETACH DELETE" in log[0][0]
