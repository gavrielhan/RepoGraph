import os
from pathlib import Path

from repograph.config import load_config


def test_defaults(tmp_path):
    cfg = load_config(cwd=tmp_path)
    assert cfg.neo4j.uri == "bolt://localhost:7687"
    assert cfg.github.auth_flow == "device"


def test_yaml_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOGRAPH_NEO4J_URI", "bolt://from-env:7687")
    (tmp_path / "repograph.yaml").write_text(
        """
neo4j:
  uri: bolt://from-yaml:7687
repos:
  - owner/repo-a
  - full_name: owner/repo-b
    paths: ["src/**"]
languages: [python, sql]
"""
    )
    cfg = load_config(cwd=tmp_path)
    assert cfg.neo4j.uri == "bolt://from-yaml:7687"
    assert [r.full_name for r in cfg.repos] == ["owner/repo-a", "owner/repo-b"]
    assert cfg.repos[1].paths == ["src/**"]
    assert cfg.repos[1].name == "repo-b"
    assert cfg.languages == ["python", "sql"]


def test_cli_flags_override_yaml(tmp_path):
    (tmp_path / "repograph.yaml").write_text("neo4j:\n  uri: bolt://from-yaml:7687\n")
    cfg = load_config(cwd=tmp_path, overrides={"neo4j_uri": "bolt://from-flag:7687"})
    assert cfg.neo4j.uri == "bolt://from-flag:7687"


def test_headless_detection(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    cfg = load_config(cwd=tmp_path)
    assert cfg.is_headless(headless_flag=True)
    assert not cfg.is_headless()
    monkeypatch.setenv("CI", "true")
    assert cfg.is_headless()


def test_env_token(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test")
    cfg = load_config(cwd=tmp_path)
    assert cfg.github.token == "ghs_test"
