import asyncio

from mcp import Client

from repograph.config import load_config
from repograph.ir import Edge, Node, write_jsonl
from repograph.mcp import GraphCache, create_server


def test_mcp_registers_agent_tools(tmp_path):
    server = create_server(load_config(cwd=tmp_path))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {"find_symbols", "blast_radius", "graph_freshness", "refresh"}
    assert "not a perfect hard gate" in tools["blast_radius"].description


def test_graph_cache_reloads_changed_ir(tmp_path):
    cfg = load_config(cwd=tmp_path)
    write_jsonl(cfg.ir_dir / "nodes.jsonl", [
        Node(id="app::a.py::one", kind="function", name="one", repo="app"),
    ])
    write_jsonl(cfg.ir_dir / "edges.jsonl", [])
    cache = GraphCache(cfg)
    nodes, _ = cache.load()
    assert [node.name for node in nodes] == ["one"]

    write_jsonl(cfg.ir_dir / "nodes.jsonl", [
        Node(id="app::a.py::two", kind="function", name="two", repo="app"),
    ])
    write_jsonl(cfg.ir_dir / "edges.jsonl", [
        Edge("CALLS", "app::a.py::two", "app::a.py::two"),
    ])
    nodes, edges = cache.load()
    assert [node.name for node in nodes] == ["two"]
    assert len(edges) == 1


def test_find_symbols_over_mcp_protocol(tmp_path):
    cfg = load_config(cwd=tmp_path)
    write_jsonl(cfg.ir_dir / "nodes.jsonl", [
        Node(id="app::jobs.py::run_job", kind="function", name="run_job", repo="app"),
    ])
    write_jsonl(cfg.ir_dir / "edges.jsonl", [])

    async def call():
        async with Client(create_server(cfg)) as client:
            result = await client.call_tool("find_symbols", {"pattern": "run_job"})
            return result.structured_content

    payload = asyncio.run(call())
    assert payload["matches"][0]["id"] == "app::jobs.py::run_job"


def test_graph_cache_retries_when_files_change_during_load(tmp_path, monkeypatch):
    from repograph import mcp as mcp_mod

    cfg = load_config(cwd=tmp_path)
    write_jsonl(cfg.ir_dir / "nodes.jsonl", [
        Node(id="app::a.py::one", kind="function", name="one", repo="app"),
    ])
    write_jsonl(cfg.ir_dir / "edges.jsonl", [])
    original = mcp_mod.load_ir
    calls = {"n": 0}

    def mutating(cfg_):
        calls["n"] += 1
        result = original(cfg_)
        if calls["n"] == 1:
            write_jsonl(cfg.ir_dir / "nodes.jsonl", [
                Node(id="app::a.py::two", kind="function", name="two", repo="app"),
            ])
            write_jsonl(cfg.ir_dir / "edges.jsonl", [])
        return result

    monkeypatch.setattr(mcp_mod, "load_ir", mutating)
    nodes, _ = GraphCache(cfg).load()
    assert calls["n"] == 2
    assert [node.name for node in nodes] == ["two"]
