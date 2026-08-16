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
