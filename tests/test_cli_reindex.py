from click.testing import CliRunner

from repograph import fetch
from repograph.cli import main
from repograph.fetch import CloneError, FetchResult
from repograph.pipeline import PipelineStats


def test_reindex_fast_forwards_existing_checkouts(tmp_path, monkeypatch):
    checkout = tmp_path / "repos" / "app"
    checkout.mkdir(parents=True)
    (checkout / ".git").mkdir()
    called = {}

    def fake_refresh(roots, token=""):
        called["roots"] = dict(roots)
        called["token"] = token
        return FetchResult(
            roots=roots,
            statuses={name: {"status": "current", "sha": "abc"} for name in roots},
        )

    monkeypatch.setattr(fetch, "refresh_existing", fake_refresh)
    monkeypatch.setattr(
        "repograph.cli.run_pipeline",
        lambda cfg, roots, **kwargs: called.update(pipeline=roots, status=kwargs.get("fetch_status")) or PipelineStats(),
    )
    monkeypatch.setattr(
        "repograph.cli.ensure_repos",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("reindex must not clone via ensure_repos")),
    )

    result = CliRunner().invoke(main, [
        "reindex",
        "--clone-dir", str(tmp_path / "repos"),
        "--ir-dir", str(tmp_path / "graph"),
    ])
    assert result.exit_code == 0, result.output
    assert called["roots"] == {"app": checkout}


def test_reindex_no_fetch_skips_git(tmp_path, monkeypatch):
    checkout = tmp_path / "repos" / "app"
    checkout.mkdir(parents=True)
    monkeypatch.setattr(
        fetch,
        "refresh_existing",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    monkeypatch.setattr("repograph.cli.run_pipeline", lambda *a, **k: PipelineStats())
    result = CliRunner().invoke(main, [
        "reindex", "--no-fetch",
        "--clone-dir", str(tmp_path / "repos"),
        "--ir-dir", str(tmp_path / "graph"),
    ])
    assert result.exit_code == 0, result.output


def test_reindex_missing_checkout_is_click_error(tmp_path):
    result = CliRunner().invoke(main, [
        "reindex",
        "--clone-dir", str(tmp_path / "missing"),
        "--ir-dir", str(tmp_path / "graph"),
    ])
    assert result.exit_code != 0
    assert "no checkouts found" in result.output
    assert "Traceback" not in result.output


def test_reindex_clone_error_is_click_error(tmp_path, monkeypatch):
    checkout = tmp_path / "repos" / "app"
    checkout.mkdir(parents=True)
    (checkout / ".git").mkdir()
    monkeypatch.setattr(
        fetch,
        "refresh_existing",
        lambda *a, **k: (_ for _ in ()).throw(CloneError("clone of acme/app failed")),
    )
    result = CliRunner().invoke(main, [
        "reindex",
        "--clone-dir", str(tmp_path / "repos"),
        "--ir-dir", str(tmp_path / "graph"),
    ])
    assert result.exit_code != 0
    assert "clone of acme/app failed" in result.output
    assert "Traceback" not in result.output


def test_reindex_preserves_use_existing_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "repos" / "app"
    checkout.mkdir(parents=True)
    (checkout / ".git").mkdir()
    seen = {}

    from repograph import cli as cli_mod

    real_cfg = cli_mod._cfg

    def wrapped(kwargs):
        cfg = real_cfg(kwargs)
        cfg.use_existing_checkout = True
        return cfg

    monkeypatch.setattr(cli_mod, "_cfg", wrapped)
    monkeypatch.setattr(
        fetch,
        "refresh_existing",
        lambda roots, token="": FetchResult(roots=roots, statuses={}),
    )
    monkeypatch.setattr(
        "repograph.cli.run_pipeline",
        lambda cfg, roots, **kwargs: seen.update(use_existing=cfg.use_existing_checkout) or PipelineStats(),
    )
    result = CliRunner().invoke(main, [
        "reindex",
        "--clone-dir", str(tmp_path / "repos"),
        "--ir-dir", str(tmp_path / "graph"),
    ])
    assert result.exit_code == 0, result.output
    assert seen["use_existing"] is True
