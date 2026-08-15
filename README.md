# repograph

Cross-repository code knowledge graph in Neo4j.

`repograph activate` authenticates with GitHub in the browser, lets you pick
repos, clones them, parses every source file across languages into a
symbol/dependency graph, and loads it into Neo4j for blast-radius /
impact-analysis queries. The same core runs headless inside a GitHub Action.

```
activate (CLI)
  └─ auth ──► repo selection ──► clone/fetch
                                    │
                                    ▼
                     Stage 1  parse (tree-sitter, per-language queries)
                                    │  emits nodes + pending edges
                                    ▼
                     Stage 2  IR (nodes.jsonl, edges.jsonl)
                                    │
                                    ▼
                     Stage 3  resolve (intra-repo, cross-repo, data edges)
                                    │
                                    ▼
                     Stage 4  load Neo4j (MERGE/UNWIND, idempotent)
                                    │
                                    ▼
                     Stage 5  query (blast-radius Cypher)  ← consumers
```

## Install

```bash
pip install .            # from a checkout; Python 3.11+
```

You need a reachable Neo4j (5.x). Locally:

```bash
docker run -d --name repograph-neo4j -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/testpassword neo4j:5
```

## Quickstart (interactive)

```bash
cp repograph.example.yaml repograph.yaml   # edit neo4j + github.client_id
repograph activate
```

1. The CLI signs you in with GitHub (device flow by default: you type a
   short code at github.com/login/device).
2. Your browser opens a local selection page — pick repos, click **Build
   graph**, close the tab.
3. The CLI clones the repos shallowly, parses, resolves, and loads Neo4j.

Then ask who is affected by a change:

```bash
repograph query --changed 'axiom_core::axiom_core/utils.py::load_config'
```

```
Blast radius for axiom_core::axiom_core/utils.py::load_config (4 affected):

  Affected:
    @axiom/analytics-team
      axiom_tox_poc::reports.sql  (repo=axiom_tox_poc, dist=2  conf=0.80)
    @axiom/data-team
      axiom_core::axiom_core/io.py::persist_trials  (repo=axiom_core, dist=1  conf=0.95)
  ...
```

On a feature branch, preview the blast radius of the PR you have not opened yet
(working tree included, compared to `origin/main` / `main`):

```bash
repograph query            # current branch vs default base
repograph blast            # same command, shorter name
repograph query --branch   # explicit
repograph query --committed  # ignore uncommitted files
```

For an open GitHub pull request:

```bash
repograph query --pr 42
repograph query --pr owner/repo#42
repograph query --pr https://github.com/owner/repo/pull/42
```

`--pr 42` uses `origin` of the current checkout. Private PRs need `GITHUB_TOKEN`
(or the token cached by `repograph activate`).

You can still map a raw git ref:

```bash
repograph query --diff origin/main
```

Re-index after code changes (incremental against the last snapshot):

```bash
repograph reindex          # only changed nodes are rewritten
repograph reindex --full   # rewrite everything
```

## Commands

| Command | What it does |
| --- | --- |
| `repograph activate` | Interactive: auth → browser selection → clone → pipeline → load. |
| `repograph run --headless` | CI mode: token from `GITHUB_TOKEN`, repo list from config/`--repos`. |
| `repograph query` | Blast radius of the **current branch** vs its merge-base (possible PR). |
| `repograph blast` | Alias for `query`. |
| `repograph query --pr <n\|url>` | Blast radius of an **open GitHub PR**. |
| `repograph query --changed <id>` | Blast radius for one node id. |
| `repograph query --diff <ref>` | Git diff against an explicit ref → blast radius. |
| `repograph reindex [--full]` | Re-run parse/resolve/load on existing checkouts. |

Config resolution order: **CLI flags → `repograph.yaml` in cwd → env vars**
(`REPOGRAPH_NEO4J_URI/_USER/_PASSWORD`, `REPOGRAPH_GITHUB_CLIENT_ID`,
`GITHUB_TOKEN`, `REPOGRAPH_CLONE_DIR`, `REPOGRAPH_IR_DIR`). See
`repograph.example.yaml` for the full reference. Interactive vs headless is
auto-detected: `CI=true` or `--headless` selects the headless path, and
everything after "obtain token + repo list" is identical between the two.

## Authentication

Two interactive designs are supported; **device flow is the default**
because an OAuth client secret cannot ship inside a distributed CLI:

- **Device flow (default).** Create an OAuth app
  (github.com/settings/applications/new), enable *Device flow*, put the
  client id in config. No secret, no localhost callback; the user types a
  code. Works everywhere.
- **Localhost web flow (optional).** For the smoothest "browser bounces
  straight back" UX, register your *own* OAuth app with callback
  `http://127.0.0.1/callback`, set `client_id` + `client_secret`, and set
  `github.auth_flow: web`. The CLI binds a loopback-only server on an
  ephemeral port, validates the OAuth `state` (CSRF), exchanges the code,
  and serves the selection page from the same server.

Scopes: `repo` (needed for private repos) or `public_repo` if you only index
public ones — request the narrowest that works via `--scope`.

Tokens are held in memory for the run and optionally cached in the OS
keychain (`keyring`); they are never written to disk in plaintext. Cloning
passes the token as an HTTP header, not in the remote URL, so it is not
persisted into `.git/config`.

**Headless (CI):** no OAuth. `GITHUB_TOKEN` (or a PAT) + a repo list from
`repograph.yaml` / `--repos`. Minimum token permission: `contents: read` on
each indexed repo.

## Graph schema

Node labels: `Repo`, `Module` (file), `Symbol` (function/method/class),
`Dataset`. Node properties: `id, name, kind, repo, path, owner, signature,
docstring, lang, start_line, end_line`.

Edges:

| Edge | Meaning |
| --- | --- |
| `DEFINES` | repo → module |
| `CONTAINS` | module → symbol |
| `CALLS` | caller symbol → callee symbol |
| `IMPORTS` | module → module (or → repo when only the package is known) |
| `INHERITS` | subclass → superclass |
| `PRODUCES` | writer → dataset |
| `CONSUMES` | reader → dataset, **and** reader → writer directly |

The direct reader→writer `CONSUMES` edge is what makes the canonical
blast-radius query work in one traversal:

```cypher
MATCH (changed:Symbol {id: $id})<-[:CALLS|IMPORTS|CONSUMES*1..]-(affected)
RETURN DISTINCT affected.repo, affected.path, affected.owner
```

Every edge produced by fuzzy resolution carries a `confidence` property
(0–1); the shipped query surfaces the minimum confidence along each path so
consumers can **rank instead of hard-gate**.

### Node ID scheme

Deterministic IDs make incremental re-index and `MERGE` idempotency work.
Defined once in `repograph/ids.py`:

```
repo     ->  <repo>
module   ->  <repo>::<path>
symbol   ->  <repo>::<path>::<qualname>      e.g. axiom_tox_poc::grid_master.py::run_grid
dataset  ->  dataset::<normalized name>
```

## IR spec

Stages communicate through two JSONL files in `ir_dir` (default
`.repograph/`):

`nodes.jsonl`:

```json
{"id": "...", "kind": "function", "name": "...", "repo": "...", "path": "...",
 "owner": "...", "signature": "run(self, grid, n_jobs=1)", "docstring": "...",
 "start_line": 20, "end_line": 26, "lang": "python"}
```

`edges.jsonl` — pending edges from the parser
(`CALLS_PENDING | IMPORTS_PENDING | INHERITS_PENDING | PRODUCES_PENDING |
CONSUMES_PENDING` with `src_file, src_scope, target, meta`) followed by
resolved edges (`type, src, dst, meta`).

Snapshots of every run are kept in `.repograph/snapshots/` — reindex diffs
against the latest one to upsert changed nodes and delete removed
nodes/edges, and historical snapshots support graph-over-time diffs.

## Language support

Python and SQL ship first (highest value for Databricks-style estates), then
JavaScript, TypeScript/TSX, Java, Scala, Go, and Bash. Grammars come from
one dependency (`tree-sitter-language-pack`).

**Adding a language = adding a query file** (`src/repograph/parse/queries/
<lang>.scm`) and mapping its extensions in `parse/languages.py` — no engine
changes. The capture convention (`@def.function`, `@def.name`, `@call.name`,
`@import.module`, `@data.write.target`, …) is documented in
`parse/engine.py`; scopes and qualified names are computed structurally so
query files stay simple.

Data edges capture string literals in write-position calls
(`to_parquet`, `saveAsTable`, `df.write.parquet`, `CREATE TABLE`,
`INSERT INTO`) and read-position calls (`read_parquet`, `spark.table`,
`FROM`, `JOIN`), normalize them (case, path/scheme prefixes, extensions,
f-string interpolations → wildcards), and reconcile producers with
consumers across all repos. That captures the "file A writes
`simulated_trials`, file B reads it, no import between them" case. Short or
generic names (`data`, `tmp`, …) are dropped to control false positives.

## Resolution design (and its limits)

- **Intra-repo:** lexical scope walk in the same file, then exact qualname,
  then unique-name match — each step at decreasing confidence.
- **Cross-repo:** manifests (`pyproject.toml`, `setup.py`, `package.json`,
  `go.mod`, `build.sbt`) build a *package-name → repo* map; when repo B
  imports a package owned by repo A the cross-repo edge is emitted. This is
  reliable **because the target repos pip-install each other** — that is
  the design's load-bearing premise.
- **Owners:** `CODEOWNERS` (root, `.github/`, `docs/`; last match wins) with
  a config-registry fallback, so every node carries an actionable `owner`.
- **Known limits:** dynamic dispatch (`getattr`, dynamic imports,
  config-built names) is invisible to static analysis. Expect <100% edge
  recall; consumers must rank by confidence, not hard-gate. When fuzzy
  matching is not enough for a language, SCIP indexers (scip-python,
  scip-java, scip-typescript, …) can be swapped in per-language through the
  documented seam in `resolve/scip.py` — converted edges enter
  `resolve(..., preresolved=...)` at confidence 1.0 with no loader changes.

## GitHub Action

This repo's CI (`.github/workflows/ci.yml`) runs pytest and builds the
fixture graph into a Neo4j service container — no Aura secrets required.

A composite action lives in `action/`. For indexing *other* GitHub repos
into Aura or a self-hosted Neo4j, copy `examples/github-workflow.yml`.

```yaml
- uses: gavrielhan/RepoGraph/action@main
  with:
    repos: |
      your-org/service-a
      your-org/service-b
    neo4j-uri: ${{ secrets.NEO4J_URI }}
    neo4j-password: ${{ secrets.NEO4J_PASSWORD }}
    github-token: ${{ secrets.REPOGRAPH_CLONE_TOKEN }}  # contents:read
```

No browser, no OAuth, no selection page — the config/inputs replace the
interactive selection. If your workflow already checks the repos out, set
`use-existing-checkout: "true"` and point `clone-dir` at them.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # 54 tests; no live Neo4j required
```

Two fixture repos in `tests/fixtures/` model the target environment: a
library (`axiom_core`) and a consumer (`axiom_tox_poc`) that imports it and
shares datasets with it across Python and SQL.

## Scope

Not a table/warehouse lineage tool, not a hosted service, no web UI beyond
the transient auth/selection page. LLM reasoning over the graph is a
downstream consumer, out of scope here.
