"""repograph CLI.

Commands:
  activate  interactive: auth -> selection -> clone -> pipeline -> load
  run       headless (CI): token + repo list from env/config
  query     blast-radius from a changed symbol or a git diff
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
@click.option("--diff", "diff_ref", default=None, help="Git ref to diff against (e.g. origin/main); maps the diff to changed symbols.")
@click.option("--repo", "diff_repo", default=None, help="Restrict --diff to one configured repo.")
@click.option("--max-depth", default=10, show_default=True, help="Maximum traversal depth.")
def query(changed_id, diff_ref, diff_repo, max_depth, **kwargs):
    """Blast radius: who is affected if this symbol changes?"""
    cfg = _cfg(kwargs)
    if not changed_id and not diff_ref:
        raise click.ClickException("pass --changed <node id> or --diff <git ref>")

    changed_ids = [changed_id] if changed_id else []
    if diff_ref:
        changed_ids.extend(_ids_from_diff(cfg, diff_ref, diff_repo))
        if not changed_ids:
            click.echo("Diff maps to no indexed symbols; nothing to report.")
            return
        click.echo(f"Changed nodes from diff: {len(changed_ids)}")

    from repograph.load.neo4j_loader import Neo4jLoader
    from repograph.query.blast_radius import blast_radius, format_results

    loader = Neo4jLoader(cfg.neo4j.uri, cfg.neo4j.user, cfg.neo4j.password, cfg.neo4j.database)
    try:
        seen: dict[str, dict] = {}
        for cid in changed_ids:
            for r in blast_radius(loader, cid, max_depth):
                prev = seen.get(r["id"])
                if prev is None or (r.get("confidence") or 0) > (prev.get("confidence") or 0):
                    seen[r["id"]] = r
        results = sorted(seen.values(), key=lambda r: (r.get("distance", 0), -(r.get("confidence") or 0)))
        header = changed_ids[0] if len(changed_ids) == 1 else f"{len(changed_ids)} changed nodes"
        click.echo(format_results(results, header))
    finally:
        loader.close()


def _ids_from_diff(cfg: Config, ref: str, only_repo: str | None) -> list[str]:
    from repograph.query.diff import changed_line_ranges, changed_node_ids

    try:
        nodes, _ = load_ir(cfg)
    except FileNotFoundError:
        raise click.ClickException("no IR found; run `repograph reindex` or `repograph run` first")

    out: list[str] = []
    repo_dirs = {p.name: p for p in cfg.clone_dir.iterdir() if (p / ".git").exists()} if cfg.clone_dir.exists() else {}
    if Path(".git").exists():  # allow running inside a target repo checkout
        repo_dirs.setdefault(Path.cwd().name, Path.cwd())
    for repo, root in repo_dirs.items():
        if only_repo and repo != only_repo:
            continue
        try:
            ranges = changed_line_ranges(root, ref)
        except Exception:
            continue
        out.extend(changed_node_ids(nodes, repo, ranges))
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


if __name__ == "__main__":
    sys.exit(main())
