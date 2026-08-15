# repograph

Cross-repository code knowledge graph in Neo4j.

Authenticate with GitHub, pick repos, parse them into a symbol/dependency
graph, and query blast radius. The same pipeline runs on every push via
GitHub Actions.

```
activate / run
  auth + repos → clone → parse → IR → resolve → Neo4j → query
```

## Install

Python 3.11+. Neo4j 5.x is optional if you keep the IR in git (`--offline`).

```bash
pip install .
cp repograph.example.yaml repograph.yaml
repograph activate
```

Device flow is the default. Optional localhost web flow: your own OAuth app
and `github.auth_flow: web`.

## Query

```bash
repograph query                         # current branch vs origin/main
repograph query --pr 42                 # open GitHub PR
repograph query --changed 'repo::path.py::fn'
repograph query --offline               # use graph JSONL, no Neo4j
repograph runs                          # what each index run changed
repograph runs --sha abc123
repograph reindex                       # incremental; --full to rewrite
```

`repograph blast` is an alias for `query`. Config: CLI flags →
`repograph.yaml` → env. `CI=true` or `--headless` skips the browser.

## History

The live graph is current-only. Each build appends an **index run**
(git SHA, time, upserted/deleted node ids) to `runs.jsonl` and, when Neo4j
is configured, to `(:IndexRun)-[:RECORDED]->(:Change)`.

GitHub cannot host Neo4j. What *can* sit in GitHub is the IR
(`nodes.jsonl`, `edges.jsonl`, `runs.jsonl`). Actions rebuild it on every
push to `main` and commit it to a `graph` branch. Point Neo4j Aura at the
same run if you want Cypher.

Copy `examples/github-workflow.yml` into each indexed repo (or one data
repo that lists them all).

## Schema

Nodes: `Repo`, `Module`, `Symbol`, `Dataset`, `IndexRun`, `Change`.  
Edges: `DEFINES`, `CONTAINS`, `CALLS`, `IMPORTS`, `INHERITS`, `PRODUCES`,
`CONSUMES`, `RECORDED`, `TOUCHED`.

IDs: `repo`, `repo::path`, `repo::path::qualname`.

## Dev

```bash
pip install -e '.[dev]' && pytest -q
```
