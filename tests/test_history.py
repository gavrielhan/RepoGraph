from pathlib import Path

from repograph.ir import Edge, Node
from repograph.load.history import (
    IndexRun,
    append_run,
    build_index_run,
    format_freshness,
    freshness,
    load_runs,
)
from repograph.load.snapshot import SnapshotDiff
from repograph.query.blast_radius import blast_radius_ir


def test_index_run_records_upserts_and_deletes(tmp_path):
    nodes = [Node(id="r::a.py::f", kind="function", name="f", repo="r", path="a.py")]
    diff = SnapshotDiff(
        upsert_nodes=nodes,
        delete_node_ids=["r::a.py::gone"],
    )
    run = build_index_run(diff, nodes, {})
    assert run.upserted == 1
    assert run.deleted == 1
    ops = {(c.op, c.node_id) for c in run.changes}
    assert ("upsert", "r::a.py::f") in ops
    assert ("delete", "r::a.py::gone") in ops
    append_run(tmp_path, run)
    loaded = load_runs(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].sha == run.sha


def test_blast_radius_ir_walks_callers():
    nodes = [
        Node(id="lib::u.py::base", kind="function", name="base", repo="lib", path="u.py", owner="@a"),
        Node(id="app::r.py::use", kind="function", name="use", repo="app", path="r.py", owner="@b"),
        Node(id="app::r.py::wrap", kind="function", name="wrap", repo="app", path="r.py", owner="@b"),
    ]
    edges = [
        Edge("CALLS", "app::r.py::use", "lib::u.py::base", {"confidence": 0.9}),
        Edge("CALLS", "app::r.py::wrap", "app::r.py::use", {"confidence": 0.8}),
        Edge("CONTAINS", "app::r.py", "app::r.py::use"),  # ignored
    ]
    rows = blast_radius_ir(nodes, edges, "lib::u.py::base")
    ids = {r["id"]: r for r in rows}
    assert "app::r.py::use" in ids
    assert "app::r.py::wrap" in ids
    assert ids["app::r.py::use"]["distance"] == 1
    assert ids["app::r.py::wrap"]["distance"] == 2
    assert ids["app::r.py::wrap"]["confidence"] == 0.8


def test_freshness_reports_stale_index(tmp_path):
    append_run(
        tmp_path,
        IndexRun(
            id="old",
            sha="abcdef123456",
            at="2020-01-01T00:00:00Z",
            source="ci",
            repo_shas={"app": "abcdef"},
            fetch_status={"app": {"status": "failed", "reason": "denied"}},
        ),
    )
    info = freshness(tmp_path)
    assert info["stale"]
    assert info["repo_count"] == 1
    assert "fetch failed for app" in info["warning"]
    assert format_freshness(info).startswith("WARNING:")


def test_freshness_reads_last_runs_line_only(tmp_path):
    import json

    first = {
        "id": "old",
        "sha": "111111111111",
        "at": "2020-01-01T00:00:00Z",
        "source": "ci",
        "changes": [{"op": "upsert", "node_id": f"n{i}"} for i in range(50)],
    }
    second = {
        "id": "new",
        "sha": "abcdef123456",
        "at": "2026-08-16T00:00:00Z",
        "source": "cli",
        "repo_shas": {"app": "abcdef"},
    }
    (tmp_path / "runs.jsonl").write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n"
    )
    info = freshness(tmp_path)
    assert info["run_id"] == "new"
    assert info["source"] == "ir"


def test_freshness_falls_back_to_neo4j_meta(tmp_path):
    info = freshness(
        tmp_path,
        neo4j_meta={
            "id": "neo-run",
            "sha": "abcdef123456",
            "at": "2026-08-16T00:00:00Z",
            "repo_shas": {"app": "abcdef"},
        },
    )
    assert info["available"]
    assert info["source"] == "neo4j"
    assert info["run_id"] == "neo-run"
