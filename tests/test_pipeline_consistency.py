from repograph.config import load_config
from repograph.ir import Node
from repograph.load.snapshot import build_snapshot, save_snapshot
from repograph import pipeline


class FakeLoader:
    instance = None
    extra_ids: list[str] = []

    def __init__(self, *args):
        self.loaded = []
        self.deleted = []
        self.state = None
        FakeLoader.instance = self

    def ensure_constraints(self):
        pass

    def graph_state_run_id(self):
        return "different-run"

    def list_code_node_ids(self):
        return list(self.extra_ids)

    def clear_code_graph(self):
        self.cleared = True

    def load_nodes(self, nodes, extra_props=None):
        self.loaded = nodes
        return len(nodes)

    def load_edges(self, edges, nodes):
        return len(edges)

    def load_index_run(self, run):
        pass

    def delete_edges(self, keys):
        return len(keys)

    def delete_nodes(self, ids):
        self.deleted.extend(ids)
        return len(ids)

    def set_graph_state(self, run):
        self.state = run if isinstance(run, str) else run.id

    def close(self):
        pass


def test_run_pipeline_full_loads_when_neo4j_state_disagrees(tmp_path, monkeypatch):
    cfg = load_config(cwd=tmp_path)
    cfg.neo4j.password = "test"
    node = Node(id="app::a.py::f", kind="function", name="f", repo="app")
    save_snapshot(cfg.ir_dir, build_snapshot([node], [], run_id="snapshot-run"))
    monkeypatch.setattr(pipeline, "build_ir", lambda *args, **kwargs: ([node], []))
    monkeypatch.setattr(pipeline, "Neo4jLoader", FakeLoader)

    stats = pipeline.run_pipeline(cfg, {"app": tmp_path})

    assert stats.consistency_recovery
    assert stats.loaded_nodes == 1
    assert not getattr(FakeLoader.instance, "cleared", False)
    assert FakeLoader.instance.state == stats.run_id


def test_missing_snapshot_does_not_clear_populated_neo4j(tmp_path, monkeypatch):
    cfg = load_config(cwd=tmp_path)
    cfg.neo4j.password = "test"
    node = Node(id="app::a.py::f", kind="function", name="f", repo="app")
    monkeypatch.setattr(pipeline, "build_ir", lambda *args, **kwargs: ([node], []))
    monkeypatch.setattr(pipeline, "Neo4jLoader", FakeLoader)

    stats = pipeline.run_pipeline(cfg, {"app": tmp_path})

    assert not stats.consistency_recovery
    assert stats.loaded_nodes == 1
    assert not getattr(FakeLoader.instance, "cleared", False)


def test_full_prunes_neo4j_ids_missing_from_ir(tmp_path, monkeypatch):
    cfg = load_config(cwd=tmp_path)
    cfg.neo4j.password = "test"
    node = Node(id="app::a.py::f", kind="function", name="f", repo="app")
    FakeLoader.extra_ids = ["app::a.py::f", "app::a.py::gone"]
    monkeypatch.setattr(pipeline, "build_ir", lambda *args, **kwargs: ([node], []))
    monkeypatch.setattr(pipeline, "Neo4jLoader", FakeLoader)

    try:
        stats = pipeline.run_pipeline(cfg, {"app": tmp_path}, full=True)
    finally:
        FakeLoader.extra_ids = []

    assert stats.deleted_nodes == 1
    assert FakeLoader.instance.deleted == ["app::a.py::gone"]
    assert not getattr(FakeLoader.instance, "cleared", False)
