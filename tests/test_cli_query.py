from click.testing import CliRunner

from repograph.cli import main
from repograph.ir import Edge, Node, write_jsonl
from repograph.load.history import IndexRun, append_run
import json


def test_query_and_blast_are_aliases():
    runner = CliRunner()
    q = runner.invoke(main, ["query", "--help"])
    b = runner.invoke(main, ["blast", "--help"])
    assert q.exit_code == 0
    assert b.exit_code == 0
    assert "--pr" in q.output
    assert "--branch" in q.output
    assert "--offline" in q.output
    assert "--json" in q.output
    assert "--pr" in b.output


def test_query_rejects_mixed_sources():
    runner = CliRunner()
    result = runner.invoke(main, ["query", "--pr", "1", "--changed", "x::y::z"])
    assert result.exit_code != 0
    assert "only one of" in result.output


def test_query_json_includes_freshness_and_results(tmp_path):
    ir_dir = tmp_path / "graph"
    write_jsonl(ir_dir / "nodes.jsonl", [
        Node(id="lib::a.py::base", kind="function", name="base", repo="lib", path="a.py"),
        Node(id="app::b.py::use", kind="function", name="use", repo="app", path="b.py"),
    ])
    write_jsonl(ir_dir / "edges.jsonl", [
        Edge("CALLS", "app::b.py::use", "lib::a.py::base", {"confidence": 0.9}),
    ])
    append_run(
        ir_dir,
        IndexRun(
            id="run-1",
            sha="abcdef123456",
            at="2026-08-16T00:00:00Z",
            source="cli",
            repo_shas={"lib": "abcdef", "app": "123456"},
        ),
    )
    result = CliRunner().invoke(main, [
        "query", "--offline", "--json", "--ir-dir", str(ir_dir),
        "--changed", "lib::a.py::base",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["freshness"]["repo_count"] == 2
    assert payload["count"] == 1
    assert payload["results"][0]["id"] == "app::b.py::use"


def test_find_returns_symbol_ids(tmp_path):
    ir_dir = tmp_path / "graph"
    write_jsonl(ir_dir / "nodes.jsonl", [
        Node(
            id="app::jobs.py::run_job", kind="function", name="run_job",
            repo="app", path="jobs.py", owner="@jobs",
        ),
    ])
    write_jsonl(ir_dir / "edges.jsonl", [])
    result = CliRunner().invoke(
        main, ["find", "run_job", "--json", "--ir-dir", str(ir_dir)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["matches"][0]["id"] == "app::jobs.py::run_job"
    assert "freshness" in payload


def test_query_rejects_unknown_guessed_symbol_id(tmp_path):
    ir_dir = tmp_path / "graph"
    write_jsonl(ir_dir / "nodes.jsonl", [
        Node(id="app::jobs.py::run_job", kind="function", name="run_job", repo="app"),
    ])
    write_jsonl(ir_dir / "edges.jsonl", [])
    result = CliRunner().invoke(main, [
        "query", "--offline", "--ir-dir", str(ir_dir),
        "--changed", "app::jobs.py::guessed",
    ])
    assert result.exit_code != 0
    assert "repograph find" in result.output


def test_empty_nodes_jsonl_is_an_error(tmp_path):
    ir_dir = tmp_path / "graph"
    ir_dir.mkdir()
    (ir_dir / "nodes.jsonl").write_bytes(b"")
    (ir_dir / "edges.jsonl").write_text("")
    result = CliRunner().invoke(main, ["find", "f", "--ir-dir", str(ir_dir)])
    assert result.exit_code != 0
    assert "empty" in result.output
    assert "Traceback" not in result.output


def test_missing_ir_names_resolved_path(tmp_path):
    result = CliRunner().invoke(
        main, ["find", "x", "--ir-dir", str(tmp_path / "missing")]
    )
    assert result.exit_code != 0
    assert str((tmp_path / "missing").resolve()) in result.output


def test_query_offline_skips_neo4j_when_history_is_missing(tmp_path, monkeypatch):
    ir_dir = tmp_path / "graph"
    write_jsonl(ir_dir / "nodes.jsonl", [
        Node(id="lib::a.py::base", kind="function", name="base", repo="lib", path="a.py"),
    ])
    write_jsonl(ir_dir / "edges.jsonl", [])
    monkeypatch.setattr(
        "repograph.load.neo4j_loader.Neo4jLoader",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("offline must not open Neo4j")),
    )
    result = CliRunner().invoke(main, [
        "query", "--offline", "--json", "--ir-dir", str(ir_dir),
        "--neo4j-password", "secret",
        "--changed", "lib::a.py::base",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["freshness"]["available"] is False


def test_truncated_ir_is_an_error_not_empty_results(tmp_path):
    ir_dir = tmp_path / "graph"
    ir_dir.mkdir()
    (ir_dir / "nodes.jsonl").write_text('{ "id": "app::a.py::f", "kind": "function"\n')
    (ir_dir / "edges.jsonl").write_text("")
    result = CliRunner().invoke(main, ["find", "f", "--ir-dir", str(ir_dir)])
    assert result.exit_code != 0
    assert "truncated or invalid JSONL" in result.output
    assert "Traceback" not in result.output
