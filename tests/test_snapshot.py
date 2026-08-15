from repograph.ir import Edge, Node
from repograph.load.snapshot import build_snapshot, diff_snapshot


def make_node(nid, **kw):
    return Node(id=nid, kind="function", name=nid.split("::")[-1], repo="r", **kw)


def test_first_run_upserts_everything():
    nodes = [make_node("r::a.py::f"), make_node("r::a.py::g")]
    diff = diff_snapshot(None, nodes, [])
    assert len(diff.upsert_nodes) == 2
    assert diff.delete_node_ids == []


def test_unchanged_nodes_skipped():
    nodes = [make_node("r::a.py::f")]
    snap = build_snapshot(nodes, [])
    diff = diff_snapshot(snap, nodes, [])
    assert diff.upsert_nodes == []
    assert diff.unchanged == 1


def test_changed_props_detected():
    before = [make_node("r::a.py::f", signature="f(a)")]
    after = [make_node("r::a.py::f", signature="f(a, b)")]  # signature diff = breaking change
    snap = build_snapshot(before, [])
    diff = diff_snapshot(snap, after, [])
    assert [n.id for n in diff.upsert_nodes] == ["r::a.py::f"]


def test_removed_nodes_and_edges_deleted():
    e = Edge("CALLS", "r::a.py::f", "r::a.py::g")
    snap = build_snapshot([make_node("r::a.py::f"), make_node("r::a.py::g")], [e])
    diff = diff_snapshot(snap, [make_node("r::a.py::f")], [])
    assert diff.delete_node_ids == ["r::a.py::g"]
    assert diff.delete_edge_keys == [("CALLS", "r::a.py::f", "r::a.py::g")]
