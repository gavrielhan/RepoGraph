"""repograph CLI.

Commands:
  activate  interactive: auth -> selection -> clone -> pipeline -> load
  run       headless (CI): token + repo list from env/config
    query     blast-radius of the current branch, a PR, a diff, or a symbol
  runs      list index runs (what each graph build changed)
  reindex   re-run parse/resolve/load (incremental unless --full)

Config resolution order: CLI flags > repograph.yaml in cwd > env vars.
Interactive vs headless is auto-detected (CI=true or --headless).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from repograph.config import Config, RepoSpec, load_config
from repograph.fetch import ensure_repos
from repograph.pipeline import load_ir, run_pipeline


def _common_options(fn):
    for opt in reversed(
        [
            click.option("--neo4j-uri", default=None, help="Neo4j bolt/neo4j URI."),
            click.option("--neo4j-user", default=None, help="Neo4j user."),
            click.option("--neo4j-password", default=None, help="Neo4j password."),
            click.option("--clone-dir", default=None, help="Directory for repo checkouts."),
            click.option("--ir-dir", default=None, help="Directory for IR JSONL + snapshots."),
            click.option("--languages", multiple=True, help="Language allowlist (repeatable)."),
        ]
    ):
        fn = opt(fn)
    return fn


def _cfg(kwargs) -> Config:
    return load_config(overrides=kwargs)


@click.group()
@click.version_option()
def main():
    """Cross-repository code knowledge graph in Neo4j."""


# --------------------------------------------------------------------------


@main.command()
@_common_options
@click.option("--headless", is_flag=True, help="Force headless mode (no browser).")
@click.option("--scope", default="repo", show_default=True, help="OAuth scope; use public_repo for public-only.")
@click.option("--no-cache", is_flag=True, help="Skip the OS keychain token cache.")
def activate(headless, scope, no_cache, **kwargs):
    """Authenticate with GitHub, select repos in the browser, build the graph."""
    cfg = _cfg(kwargs)
    if cfg.is_headless(headless):
        click.echo("CI environment detected; running headless.")
        _run_headless(cfg)
        return

    from repograph.auth import github as gh
    from repograph.auth.selection_server import SelectionServer

    token, server = None, None
    if not no_cache:
        cached = gh.cached_token()
        if cached and gh.token_is_valid(cached):
            token = cached
            click.echo("Using cached GitHub token from the OS keychain.")

    if token is None:
        try:
            if cfg.github.auth_flow == "web":
                click.echo("Opening browser for GitHub sign-in (localhost web flow)…")
                token, server = gh.web_flow_token(cfg.github.client_id, cfg.github.client_secret, scope)
            else:
                token = gh.device_flow_token(cfg.github.client_id, scope, echo=click.echo)
        except gh.AuthError as e:
            raise click.ClickException(str(e))
        if not no_cache:
            gh.cache_token(token)

    click.echo("Fetching your repositories…")
    repos = gh.fetch_user_repos(token)
    if server is None:
        server = SelectionServer()
        import webbrowser

        webbrowser.open(server.select_url)
    server.set_repos(repos)
    click.echo(f"Select repos in your browser: {server.select_url}")

    try:
        selection = server.wait_for_selection()
    except TimeoutError as e:
        raise click.ClickException(str(e))
    finally:
        server.shutdown()

    if not selection:
        raise click.ClickException("no repos selected")
    specs = [RepoSpec(full_name=s["full_name"], clone_url=s.get("clone_url", "")) for s in selection]
    click.echo(f"Selected {len(specs)} repo(s). Cloning…")
    _build(cfg, specs, token)


@main.command(name="run")
@_common_options
@click.option("--headless", is_flag=True, default=True, help="Headless mode (default for run).")
@click.option("--repos", multiple=True, help="owner/name (repeatable); overrides repograph.yaml.")
@click.option("--use-existing-checkout", is_flag=True, help="Skip cloning; use directories already in clone-dir.")
def run_cmd(headless, repos, use_existing_checkout, **kwargs):
    """Headless pipeline for CI: token + repo list from env/config."""
    cfg = _cfg({**kwargs, "repos": list(repos)})
    if use_existing_checkout:
        cfg.use_existing_checkout = True
    _run_headless(cfg)


def _run_headless(cfg: Config):
    from repograph.auth import github as gh

    token = cfg.github.token
    if not token:
        try:
            token = gh.headless_token()
        except gh.AuthError as e:
            raise click.ClickException(str(e))
    if not cfg.repos:
        raise click.ClickException(
            "no repos configured: add a `repos:` list to repograph.yaml or pass --repos owner/name"
        )
    _build(cfg, cfg.repos, token)


def _build(cfg: Config, specs: list[RepoSpec], token: str):
    from repograph.fetch import CloneError

    try:
        roots = ensure_repos(cfg, specs, token)
    except CloneError as e:
        raise click.ClickException(str(e))
    path_globs = {s.name: s.paths for s in specs if s.paths}
    click.echo(f"Parsing {len(roots)} repo(s)…")
    stats = run_pipeline(cfg, roots, path_globs=path_globs)
    click.echo(f"Done: {stats.summary()}")
    click.echo("Try: repograph query --changed '<repo>::<path>::<symbol>'")


# --------------------------------------------------------------------------


@main.command()
@_common_options
@click.option("--changed", "changed_id", default=None, help="Changed node id (repo::path::qualname).")
@click.option("--diff", "diff_ref", default=None, help="Git ref to diff against (e.g. origin/main).")
@click.option("--branch", "branch_ref", default=None, is_flag=False, flag_value="HEAD",
              help="Blast radius of a local branch vs its merge-base. Pass a branch name, or omit the value for HEAD.")
@click.option("--pr", "pr_spec", default=None, help="GitHub PR: 42, owner/repo#42, or a pull URL.")
@click.option("--base", "base_ref", default=None, help="Base ref for --branch (default: origin/HEAD, then main/master).")
@click.option("--committed", is_flag=True, help="With --branch, ignore uncommitted working-tree changes.")
@click.option("--repo", "diff_repo", default=None, help="Graph repo name (or owner/name) to restrict the diff to.")
@click.option("--max-depth", default=10, show_default=True, help="Maximum traversal depth.")
@click.option("--offline", is_flag=True, help="Query the JSONL IR instead of Neo4j (graph files in git).")
def query(changed_id, diff_ref, branch_ref, pr_spec, base_ref, committed, diff_repo, max_depth, offline, **kwargs):
    """Blast radius of a symbol, the current branch, or a GitHub PR.

    With no flags, diffs the current branch (plus uncommitted changes) against
    the default base — the preview of a PR you have not opened yet.
    """
    cfg = _cfg(kwargs)
    sources = [bool(changed_id), bool(diff_ref), branch_ref is not None, bool(pr_spec)]
    if sum(sources) > 1:
        raise click.ClickException("pass only one of --changed, --diff, --branch, or --pr")

    if not any(sources):
        branch_ref = "HEAD"  # default: current branch / possible PR

    header, changed_ids = _resolve_changed(cfg, changed_id, diff_ref, branch_ref, pr_spec, base_ref, committed, diff_repo)
    if not changed_ids:
        click.echo("No indexed symbols in this change; nothing to report.")
        return

    from repograph.query.blast_radius import format_results

    seen: dict[str, dict] = {}
    for cid in changed_ids:
        for r in _blast_one(cfg, cid, max_depth, offline):
            prev = seen.get(r["id"])
            if prev is None or (r.get("confidence") or 0) > (prev.get("confidence") or 0):
                seen[r["id"]] = r
    results = sorted(seen.values(), key=lambda r: (r.get("distance", 0), -(r.get("confidence") or 0)))
    click.echo(format_results(results, header, changed_ids=changed_ids))


def _blast_one(cfg: Config, cid: str, max_depth: int, offline: bool) -> list[dict]:
    from repograph.query.blast_radius import blast_radius, blast_radius_ir

    if offline or not cfg.neo4j.password:
        nodes, edges = load_ir(cfg)
        return blast_radius_ir(nodes, edges, cid, max_depth)
    from repograph.load.neo4j_loader import Neo4jLoader

    loader = Neo4jLoader(cfg.neo4j.uri, cfg.neo4j.user, cfg.neo4j.password, cfg.neo4j.database)
    try:
        return blast_radius(loader, cid, max_depth)
    finally:
        loader.close()


main.add_command(query, name="blast")


def _resolve_changed(
    cfg: Config,
    changed_id,
    diff_ref,
    branch_ref,
    pr_spec,
    base_ref,
    committed,
    diff_repo,
) -> tuple[str, list[str]]:
    if changed_id:
        return changed_id, [changed_id]

    nodes = _load_nodes(cfg)

    if pr_spec:
        from repograph.query.pr import PRError, github_token, parse_pr_spec, pr_diff_ranges

        root = _cwd_git_root()
        try:
            owner, repo, number = parse_pr_spec(pr_spec, root)
        except PRError as e:
            raise click.ClickException(str(e))
        try:
            meta, ranges = pr_diff_ranges(owner, repo, number, github_token(cfg))
        except PRError as e:
            raise click.ClickException(str(e))
        graph_repo = _graph_repo(nodes, hint=diff_repo or repo, root=root)
        ids = _ids_from_ranges(nodes, graph_repo, ranges)
        title = meta.get("title") or f"#{number}"
        header = f"PR {meta['full_name']}#{number} ({title})"
        click.echo(f"{header}: {len(ids)} changed symbol(s) vs {meta.get('base') or 'base'}")
        return header, ids

    if branch_ref is not None:
        from repograph.query.diff import branch_diff_ranges, current_branch, git_root

        root = git_root()
        if root is None:
            raise click.ClickException("not inside a git checkout; pass --changed, --diff, or --pr")
        branch = "HEAD" if branch_ref in ("HEAD", "") else branch_ref
        try:
            resolved_base, ranges = branch_diff_ranges(
                root, branch=branch, base=base_ref, include_uncommitted=not committed,
            )
        except Exception as e:
            raise click.ClickException(f"could not diff against the base branch: {e}")
        graph_repo = _graph_repo(nodes, hint=diff_repo, root=root)
        ids = _ids_from_ranges(nodes, graph_repo, ranges)
        name = current_branch(root) if branch == "HEAD" else branch
        header = f"branch {name} vs {resolved_base}"
        extra = "" if committed else " (including uncommitted changes)"
        click.echo(f"{header}{extra}: {len(ids)} changed symbol(s)")
        return header, ids

    # --diff <ref>
    ids = _ids_from_diff(cfg, diff_ref, diff_repo, nodes)
    click.echo(f"Changed nodes from diff against {diff_ref}: {len(ids)}")
    return f"diff vs {diff_ref}", ids


def _load_nodes(cfg: Config):
    try:
        nodes, _ = load_ir(cfg)
    except FileNotFoundError:
        raise click.ClickException("no IR found; run `repograph reindex` or `repograph run` first")
    return nodes


def _cwd_git_root():
    from repograph.query.diff import git_root

    return git_root()


def _graph_repo(nodes, hint: str | None = None, root=None):
    from repograph.query.diff import infer_graph_repo

    name_hint = hint
    if name_hint and "/" in name_hint:
        name_hint = name_hint.split("/")[-1]
    if root is not None:
        return infer_graph_repo(root, nodes, hint=name_hint)
    if name_hint:
        return infer_graph_repo(Path.cwd(), nodes, hint=name_hint)
    return name_hint


def _ids_from_ranges(nodes, repo: str | None, ranges) -> list[str]:
    from repograph.query.diff import changed_node_ids

    if not repo:
        raise click.ClickException("could not match this checkout to a repo in the graph")
    return sorted(set(changed_node_ids(nodes, repo, ranges)))


def _ids_from_diff(cfg: Config, ref: str, only_repo: str | None, nodes=None) -> list[str]:
    from repograph.query.diff import changed_line_ranges, changed_node_ids, git_root, infer_graph_repo

    if nodes is None:
        nodes = _load_nodes(cfg)

    out: list[str] = []
    repo_dirs = {p.name: p for p in cfg.clone_dir.iterdir() if (p / ".git").exists()} if cfg.clone_dir.exists() else {}
    cwd_root = git_root()
    if cwd_root is not None:
        repo_dirs.setdefault(cwd_root.name, cwd_root)
    for name, root in repo_dirs.items():
        graph_repo = infer_graph_repo(root, nodes, hint=only_repo or name)
        if only_repo and graph_repo != only_repo and name != only_repo:
            continue
        try:
            ranges = changed_line_ranges(root, ref)
        except Exception:
            continue
        if graph_repo:
            out.extend(changed_node_ids(nodes, graph_repo, ranges))
    return sorted(set(out))


# --------------------------------------------------------------------------


@main.command()
@_common_options
@click.option("--full", is_flag=True, help="Rewrite every node instead of diffing against the last snapshot.")
def reindex(full, **kwargs):
    """Re-run parse -> resolve -> load on the existing checkouts."""
    cfg = _cfg(kwargs)
    roots: dict[str, Path] = {}
    if cfg.repos:
        for spec in cfg.repos:
            dest = cfg.clone_dir / spec.name
            if dest.exists():
                roots[spec.name] = dest
    elif cfg.clone_dir.exists():
        roots = {p.name: p for p in sorted(cfg.clone_dir.iterdir()) if p.is_dir()}
    if not roots:
        raise click.ClickException(f"no checkouts found in {cfg.clone_dir}; run `repograph activate` first")

    path_globs = {s.name: s.paths for s in cfg.repos if s.paths}
    click.echo(f"Reindexing {len(roots)} repo(s) ({'full' if full else 'incremental'})…")
    stats = run_pipeline(cfg, roots, path_globs=path_globs, full=full)
    click.echo(f"Done: {stats.summary()}")


@main.command()
@_common_options
@click.option("--sha", default=None, help="Show changes recorded for this git SHA or run id.")
@click.option("--limit", default=20, show_default=True, help="How many recent runs to list.")
def runs(sha, limit, **kwargs):
    """List index runs (what changed in each graph build)."""
    cfg = _cfg(kwargs)
    from repograph.load.history import format_run_changes, format_runs, load_runs

    recorded = load_runs(cfg.ir_dir)
    if sha:
        matches = [r for r in recorded if r.sha.startswith(sha) or r.id.startswith(sha)]
        if not matches:
            raise click.ClickException(f"no index run matching {sha!r} in {cfg.ir_dir / 'runs.jsonl'}")
        click.echo(format_run_changes(matches[-1]))
        return
    click.echo(format_runs(recorded, limit=limit))


if __name__ == "__main__":
    sys.exit(main())
