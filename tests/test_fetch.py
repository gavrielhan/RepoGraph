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


def test_refresh_reports_updated_sha(monkeypatch):
    calls = iter([
        Result(stdout="old\n"),
        Result(),
        Result(),
        Result(stdout="new\n"),
    ])
    monkeypatch.setattr(fetch.subprocess, "run", lambda *args, **kwargs: next(calls))
    status = fetch._refresh(Path("/repo"), "")
    assert status["status"] == "updated"
    assert status["previous_sha"] == "old"
    assert status["sha"] == "new"


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
