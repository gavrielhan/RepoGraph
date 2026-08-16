# RepoGraph

RepoGraph builds a dependency graph from one or more GitHub repositories and
answers questions such as:

- What code and which owners could be affected by my current branch?
- What is the blast radius of an open pull request?
- Which repositories call or import this symbol?
- What changed during each graph indexing run?

It parses source code with tree-sitter, resolves calls, imports, inheritance,
and dataset reads/writes, then stores the result as portable JSONL files and,
optionally, in Neo4j.

```text
GitHub repos → parse → resolve → nodes.jsonl + edges.jsonl → Neo4j (optional)
                              └→ runs.jsonl (index history)
```

## Choose how to store the graph

RepoGraph supports two modes:

1. **GitHub-only:** commit `nodes.jsonl`, `edges.jsonl`, and `runs.jsonl` to
   a `graph` branch. Use `repograph query --offline`. This requires no database.
2. **Shared Neo4j:** load the same graph into Neo4j Aura or a self-hosted
   Neo4j instance. This enables Cypher and gives a team one shared live graph.

GitHub does not run Neo4j itself. It can store the portable graph files and
use GitHub Actions to rebuild them whenever a repository changes.

## Requirements

- Python 3.11+
- Git
- A GitHub account
- Optional: Neo4j 5.x or Neo4j Aura

Supported languages: Python, SQL, JavaScript, TypeScript/TSX, Java, Scala,
Go, and Bash.

## Install

```bash
git clone https://github.com/gavrielhan/RepoGraph.git
cd RepoGraph
python -m venv .venv
source .venv/bin/activate
pip install .
cp repograph.example.yaml repograph.yaml
```

Keep passwords and tokens out of `repograph.yaml`. Prefer environment
variables such as `REPOGRAPH_NEO4J_PASSWORD` and `GITHUB_TOKEN`.

## First run: interactive setup

`repograph activate` signs in to GitHub, opens a repository picker, clones the
selected repositories, and builds the graph.

### 1. Create a GitHub OAuth app

Open <https://github.com/settings/applications/new>, create an OAuth app, and
enable **Device Flow** in its settings. Put its client ID in
`repograph.yaml`:

```yaml
github:
  client_id: "YOUR_CLIENT_ID"
  auth_flow: device

ir_dir: .repograph
```

Device Flow is the default and does not require shipping a client secret.

### 2. Optional: start local Neo4j

Skip this step if you only want the JSONL graph and offline queries.

```bash
docker run -d --name repograph-neo4j \
  -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/testpassword \
  neo4j:5

export REPOGRAPH_NEO4J_PASSWORD=testpassword
```

The default URI is `bolt://localhost:7687`.

### 3. Activate RepoGraph

```bash
repograph activate
```

Follow the device-login prompt, select repositories in the browser, and click
**Build graph**. Output is written under `ir_dir`; when a Neo4j password is
configured, the same run is also loaded into Neo4j.

## Analyze changes

Run these commands from a repository that has already been indexed.
Every query starts with the indexed SHA, age, and repository count. RepoGraph
warns when the graph is over seven days old; do not treat an empty result from
a stale graph as proof that a change is safe.

### Current branch or possible PR

```bash
repograph query
```

This compares the current branch—including uncommitted changes—with its merge
base on `origin/main`, `origin/master`, `main`, or `master`.

```bash
repograph query --base origin/develop  # choose another target branch
repograph query --committed            # ignore uncommitted changes
repograph blast                        # alias for `repograph query`
```

### Open GitHub pull request

```bash
repograph query --pr 42
repograph query --pr owner/repository#42
repograph query --pr https://github.com/owner/repository/pull/42
```

A PR number by itself uses the checkout's `origin` remote. Private PRs require
`GITHUB_TOKEN` or the token cached by `repograph activate`.

### One known symbol

Symbol IDs use `repo::path::qualified_name`:

```bash
repograph query --changed 'service::src/jobs.py::run_job'
```

### Query graph files without Neo4j

```bash
repograph query --offline
```

Set `REPOGRAPH_IR_DIR` or `ir_dir` to the directory containing
`nodes.jsonl` and `edges.jsonl`.

## Reindex and inspect history

```bash
repograph reindex          # fetch origins, then index incrementally
repograph reindex --no-fetch  # deliberately use local checkouts
repograph reindex --full   # rebuild every node
repograph runs             # recent index runs
repograph runs --sha abc123
```

Every build appends an index run containing the Git SHA, timestamp, and
upserted/deleted nodes to `runs.jsonl`. With Neo4j enabled, it also creates:

```text
(:IndexRun)-[:RECORDED]->(:Change)
(:IndexRun)-[:TOUCHED]->(:Symbol)
```

The dependency graph represents the latest indexed state; index runs preserve
the change history.

## Automate updates with GitHub Actions

Copy `examples/github-workflow.yml` into
`.github/workflows/repograph.yml` in an indexed repository.

The example workflow:

1. Runs after every push to `main`.
2. Rebuilds the graph.
3. Publishes the JSONL graph to a `graph` branch.
4. Optionally loads Neo4j when `NEO4J_URI` and `NEO4J_PASSWORD` secrets exist.
5. Calculates and comments the blast radius on pull requests.

For GitHub-only storage, no Neo4j secrets are needed. For a shared live graph,
add these repository or organization secrets:

- `NEO4J_URI`
- `NEO4J_PASSWORD`
- `REPOGRAPH_CLONE_TOKEN` when indexing private repositories other than the
  repository running the workflow; grant only `contents: read`.

For a real cross-repository graph, use one dedicated graph repository:

1. Copy `examples/dedicated-graph-workflow.yml` into that repository.
2. Put every target repository in its `repograph.yaml`.
3. Copy `examples/service-dispatch.yml` into each service so pushes trigger the
   central rebuild.
4. Configure a read-only `REPOGRAPH_CLONE_TOKEN` for private source repos and
   a `REPOGRAPH_DISPATCH_TOKEN` in each service that can dispatch to the graph
   repository.

The scheduled rebuild in the example is a safety net for missed dispatches.

## Headless use

CI does not use OAuth or a browser. Define repositories in
`repograph.yaml` or pass repeated `--repos` arguments:

```bash
export GITHUB_TOKEN=...
repograph run --headless \
  --repos owner/service-a \
  --repos owner/service-b
```

Configuration precedence is: command-line flags, `repograph.yaml`, then
environment variables.

## What is extracted

- Repositories, modules, functions, methods, and classes
- Calls, imports, and inheritance
- Dataset producers and consumers discovered from common code/SQL patterns
- CODEOWNERS or configured fallback owners
- Per-edge confidence for approximate resolution

Static analysis cannot see every dynamic import, `getattr`, or runtime-built
name. Treat the result as ranked impact evidence, not a perfect hard gate.

## Development

```bash
pip install -e '.[dev]'
pytest -q
```

The graph schema and IR types are defined in `src/repograph/ir.py`; language
queries live under `src/repograph/parse/queries/`.
