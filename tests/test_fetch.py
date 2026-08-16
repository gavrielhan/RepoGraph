from pathlib import Path

from repograph import fetch


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_refresh_reports_failure_and_keeps_previous_sha(monkeypatch):
    calls = iter([
        Result(stdout="abc123\n"),
        Result(returncode=1, stderr="authentication failed"),
    ])
    monkeypatch.setattr(fetch.subprocess, "run", lambda *args, **kwargs: next(calls))
    status = fetch._refresh(Path("/repo"), "token")
    assert status == {
        "status": "failed",
        "operation": "fetch",
        "reason": "authentication failed",
        "sha": "abc123",
    }


def test_refresh_fast_forwards_without_reset(monkeypatch):
    cmds = []

    def run(cmd, **kwargs):
        cmds.append(cmd)
        mapping = {
            ("git", "rev-parse", "HEAD"): Result(stdout="old\n"),
            ("git", "status", "--porcelain"): Result(),
            ("git", "rev-parse", "--abbrev-ref", "@{u}"): Result(stdout="origin/main\n"),
        }
        if cmd[:3] == ["git", "merge", "--ff-only"]:
            return Result()
        if "fetch" in cmd:
            return Result()
        if cmd[:3] == ["git", "rev-parse", "HEAD"] and any(c[:3] == ["git", "merge", "--ff-only"] for c in cmds[:-1]):
            return Result(stdout="new\n")
        return mapping.get(tuple(cmd), Result())

    monkeypatch.setattr(fetch.subprocess, "run", run)
    status = fetch._refresh(Path("/repo"), "")
    assert status["status"] == "updated"
    assert status["previous_sha"] == "old"
    assert status["sha"] == "new"
    assert any(cmd[:3] == ["git", "merge", "--ff-only"] for cmd in cmds)
    assert not any("reset" in cmd for cmd in cmds)


def test_refresh_refuses_dirty_working_tree(monkeypatch):
    cmds = []

    def run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return Result(stdout="abc123\n")
        if "fetch" in cmd:
            return Result()
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return Result(stdout=" M src/app.py\n")
        raise AssertionError(f"unexpected git command {cmd}")

    monkeypatch.setattr(fetch.subprocess, "run", run)
    status = fetch._refresh(Path("/repo"), "")
    assert status["status"] == "dirty"
    assert status["sha"] == "abc123"
    assert not any("reset" in cmd or "merge" in cmd for cmd in cmds)


def test_refresh_owned_clone_resets_to_fetch_head(monkeypatch):
    cmds = []

    def run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            if any(c[:3] == ["git", "reset", "--hard"] for c in cmds[:-1]):
                return Result(stdout="new\n")
            return Result(stdout="old\n")
        if "fetch" in cmd:
            return Result()
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return Result()
        if cmd[:3] == ["git", "reset", "--hard"]:
            return Result()
        raise AssertionError(f"unexpected git command {cmd}")

    monkeypatch.setattr(fetch.subprocess, "run", run)
    status = fetch._refresh(Path("/repo"), "", allow_reset=True)
    assert status["status"] == "updated"
    assert status["sha"] == "new"
    assert any(cmd[:4] == ["git", "reset", "--hard", "--quiet"] for cmd in cmds)
    assert not any("merge" in cmd for cmd in cmds)


def test_refresh_unshallows_when_fast_forward_fails(monkeypatch):
    cmds = []
    merges = {"n": 0}

    def run(cmd, **kwargs):
        cmds.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            if merges["n"] >= 2:
                return Result(stdout="new\n")
            return Result(stdout="old\n")
        if "--unshallow" in cmd:
            return Result()
        if "fetch" in cmd:
            return Result()
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return Result()
        if cmd[:4] == ["git", "rev-parse", "--is-shallow-repository"]:
            return Result(stdout="true\n")
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return Result(stdout="origin/main\n")
        if cmd[:3] == ["git", "merge", "--ff-only"]:
            merges["n"] += 1
            if merges["n"] == 1:
                return Result(returncode=1, stderr="refusing to merge unrelated histories")
            return Result()
        raise AssertionError(f"unexpected git command {cmd}")

    monkeypatch.setattr(fetch.subprocess, "run", run)
    status = fetch._refresh(Path("/repo"), "", allow_reset=False)
    assert status["status"] == "updated"
    assert any("--unshallow" in cmd for cmd in cmds)
    assert sum(1 for cmd in cmds if cmd[:3] == ["git", "merge", "--ff-only"]) == 2
    assert not any("reset" in cmd for cmd in cmds)


def test_ensure_repos_allows_reset_on_owned_clones(tmp_path, monkeypatch):
    seen = {}
    dest = tmp_path / "app"
    dest.mkdir()
    (dest / ".git").mkdir()

    def fake_refresh(path, token, *, allow_reset=False):
        seen["allow_reset"] = allow_reset
        return {"status": "current", "sha": "abc"}

    monkeypatch.setattr(fetch, "_refresh", fake_refresh)
    monkeypatch.setattr(fetch, "_clone", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not clone")))
    from repograph.config import Config, RepoSpec

    cfg = Config(clone_dir=tmp_path)
    fetch.ensure_repos(cfg, [RepoSpec(full_name="acme/app")])
    assert seen["allow_reset"] is True


def test_refresh_existing_never_resets(tmp_path, monkeypatch):
    seen = {}
    dest = tmp_path / "app"
    dest.mkdir()
    (dest / ".git").mkdir()

    def fake_refresh(path, token, *, allow_reset=False):
        seen["allow_reset"] = allow_reset
        return {"status": "current", "sha": "abc"}

    monkeypatch.setattr(fetch, "_refresh", fake_refresh)
    fetch.refresh_existing({"app": dest})
    assert seen["allow_reset"] is False


def test_refresh_redacts_credentials_from_recorded_error(monkeypatch):
    token = "secret-token"
    calls = iter([
        Result(stdout="abc123\n"),
        Result(returncode=1, stderr=f"authorization failed for {token}"),
    ])
    monkeypatch.setattr(fetch.subprocess, "run", lambda *args, **kwargs: next(calls))
    status = fetch._refresh(Path("/repo"), token)
    assert token not in status["reason"]
    assert "[REDACTED]" in status["reason"]
