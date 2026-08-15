# repograph

Cross-repository code knowledge graph in Neo4j.

Authenticate with GitHub, pick repos, parse them into a symbol/dependency
graph, load Neo4j, and query blast radius. The same pipeline runs headless
in GitHub Actions.

```
activate / run
  auth + repos → clone → parse → IR → resolve → Neo4j → query
```

## Install

Python 3.11+ and a reachable Neo4j 5.x.

```bash
pip install .
docker run -d --name repograph-neo4j -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/testpassword neo4j:5
cp repograph.example.yaml repograph.yaml   # neo4j + github.client_id
repograph activate
```

Device flow is the default (no client secret in the CLI). Optional localhost
web flow: register your own OAuth app and set `github.auth_flow: web`.

## Query

```bash
repograph query                         # current branch vs origin/main (possible PR)
repograph query --pr 42                 # open GitHub PR
repograph query --pr owner/repo#42
repograph query --changed 'repo::path.py::fn'
repograph query --diff origin/main
repograph reindex                       # incremental; --full to rewrite
```

`repograph blast` is an alias for `query`. Config order: CLI flags →
`repograph.yaml` → env (`REPOGRAPH_NEO4J_*`, `GITHUB_TOKEN`, …).
`CI=true` or `--headless` skips the browser.

## Schema

Nodes: `Repo`, `Module`, `Symbol`, `Dataset`.  
Edges: `DEFINES`, `CONTAINS`, `CALLS`, `IMPORTS`, `INHERITS`, `PRODUCES`,
`CONSUMES` (reader→dataset and reader→writer).

IDs: `repo`, `repo::path`, `repo::path::qualname`, `dataset::name`.

Resolution is fuzzy (scope, then name match, then package manifests). Edges
carry `confidence`. Dynamic dispatch is invisible; rank, don't hard-gate.
SCIP can plug in via `resolve/scip.py` without changing the loader.

Languages: Python, SQL, JS, TS, Java, Scala, Go, Bash. Add a `.scm` query
file under `parse/queries/` to add another.

## GitHub Action

CI in this repo runs tests and loads the fixture graph into a Neo4j service.
To index other repos into Aura, copy `examples/github-workflow.yml`.

```yaml
- uses: gavrielhan/RepoGraph/action@main
  with:
    repos: |
      your-org/service-a
      your-org/service-b
    neo4j-uri: ${{ secrets.NEO4J_URI }}
    neo4j-password: ${{ secrets.NEO4J_PASSWORD }}
    github-token: ${{ secrets.REPOGRAPH_CLONE_TOKEN }}  # contents: read
```

## Dev

```bash
pip install -e '.[dev]' && pytest -q
```
