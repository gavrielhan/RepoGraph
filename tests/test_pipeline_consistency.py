from repograph.config import load_config
from repograph.ir import Node
from repograph.load.snapshot import build_snapshot, save_snapshot
from repograph import pipeline


class FakeLoader:
    instance = None

    def __init__(self, *args):
        self.loaded = []
        self.state = None
        FakeLoader.instance = self

    def ensure_constraints(self):
        pass

    def graph_state_run_id(self):
        return "different-run"

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
        return len(ids)

    def set_graph_state(self, run_id):
        self.state = run_id

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
    assert FakeLoader.instance.cleared
    assert FakeLoader.instance.state == stats.run_id
