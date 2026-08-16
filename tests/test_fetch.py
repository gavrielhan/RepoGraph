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
