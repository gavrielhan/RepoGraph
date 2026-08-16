"""Layered configuration.

Resolution order (highest wins): CLI flags -> repograph.yaml in cwd -> env vars.

Environment variables:
    REPOGRAPH_NEO4J_URI, REPOGRAPH_NEO4J_USER, REPOGRAPH_NEO4J_PASSWORD
    REPOGRAPH_GITHUB_CLIENT_ID, REPOGRAPH_GITHUB_CLIENT_SECRET
    REPOGRAPH_CLONE_DIR, REPOGRAPH_IR_DIR
    GITHUB_TOKEN (headless auth), CI (headless auto-detection)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILENAME = "repograph.yaml"

# Default GitHub OAuth client id used for the device flow. Device flow needs
# no client secret, so a public client id is safe to ship. Users can override
# with their own OAuth app (required for the localhost web flow).
DEFAULT_DEVICE_CLIENT_ID = ""


@dataclass
class Neo4jConfig:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = ""
    database: str = "neo4j"


@dataclass
class GitHubConfig:
    client_id: str = DEFAULT_DEVICE_CLIENT_ID
    client_secret: str = ""  # only needed for the localhost web flow
    auth_flow: str = "device"  # "device" | "web"
    token: str = ""  # headless: PAT / GITHUB_TOKEN


@dataclass
class RepoSpec:
    full_name: str  # "owner/name"
    clone_url: str = ""
    paths: list[str] = field(default_factory=list)  # optional path globs

    @property
    def name(self) -> str:
        return self.full_name.split("/")[-1]


@dataclass
class Config:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    clone_dir: Path = Path("repos")
    ir_dir: Path = Path(".repograph")
    languages: list[str] = field(default_factory=list)  # allowlist; empty = all supported
    repos: list[RepoSpec] = field(default_factory=list)
    owner_source: str = "codeowners"  # "codeowners" | "registry"
    owner_registry: dict = field(default_factory=dict)  # repo or path-prefix -> owner
    use_existing_checkout: bool = False

    def is_headless(self, headless_flag: bool = False) -> bool:
        return headless_flag or os.environ.get("CI", "").lower() == "true"


def load_config(cwd: Path | None = None, overrides: dict | None = None) -> Config:
    """Build config from env vars, then repograph.yaml, then CLI overrides."""
    cwd = (cwd or Path.cwd()).resolve()
    cfg = Config()

    _apply_env(cfg)

    yaml_path = cwd / CONFIG_FILENAME
    if yaml_path.exists():
        _apply_yaml(cfg, yaml.safe_load(yaml_path.read_text()) or {})

    if overrides:
        _apply_overrides(cfg, overrides)

    cfg.ir_dir = _absolute_from(cwd, cfg.ir_dir)
    cfg.clone_dir = _absolute_from(cwd, cfg.clone_dir)
    return cfg


def _absolute_from(base: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _apply_env(cfg: Config) -> None:
    env = os.environ
    cfg.neo4j.uri = env.get("REPOGRAPH_NEO4J_URI", cfg.neo4j.uri)
    cfg.neo4j.user = env.get("REPOGRAPH_NEO4J_USER", cfg.neo4j.user)
    cfg.neo4j.password = env.get("REPOGRAPH_NEO4J_PASSWORD", cfg.neo4j.password)
    cfg.github.client_id = env.get("REPOGRAPH_GITHUB_CLIENT_ID", cfg.github.client_id)
    cfg.github.client_secret = env.get("REPOGRAPH_GITHUB_CLIENT_SECRET", cfg.github.client_secret)
    cfg.github.token = env.get("GITHUB_TOKEN", cfg.github.token)
    if env.get("REPOGRAPH_CLONE_DIR"):
        cfg.clone_dir = Path(env["REPOGRAPH_CLONE_DIR"])
    if env.get("REPOGRAPH_IR_DIR"):
        cfg.ir_dir = Path(env["REPOGRAPH_IR_DIR"])


def _apply_yaml(cfg: Config, data: dict) -> None:
    neo = data.get("neo4j", {})
    cfg.neo4j.uri = neo.get("uri", cfg.neo4j.uri)
    cfg.neo4j.user = neo.get("user", cfg.neo4j.user)
    cfg.neo4j.password = neo.get("password", cfg.neo4j.password)
    cfg.neo4j.database = neo.get("database", cfg.neo4j.database)

    gh = data.get("github", {})
    cfg.github.client_id = gh.get("client_id", cfg.github.client_id)
    cfg.github.client_secret = gh.get("client_secret", cfg.github.client_secret)
    cfg.github.auth_flow = gh.get("auth_flow", cfg.github.auth_flow)

    if "clone_dir" in data:
        cfg.clone_dir = Path(data["clone_dir"])
    if "ir_dir" in data:
        cfg.ir_dir = Path(data["ir_dir"])
    cfg.languages = data.get("languages", cfg.languages)
    cfg.owner_source = data.get("owner_source", cfg.owner_source)
    cfg.owner_registry = data.get("owner_registry", cfg.owner_registry)
    cfg.use_existing_checkout = data.get("use_existing_checkout", cfg.use_existing_checkout)

    for entry in data.get("repos", []):
        if isinstance(entry, str):
            cfg.repos.append(RepoSpec(full_name=entry))
        else:
            cfg.repos.append(
                RepoSpec(
                    full_name=entry["full_name"],
                    clone_url=entry.get("clone_url", ""),
                    paths=entry.get("paths", []),
                )
            )


def _apply_overrides(cfg: Config, overrides: dict) -> None:
    """CLI flags. Only non-None values win."""
    simple = {
        "neo4j_uri": ("neo4j", "uri"),
        "neo4j_user": ("neo4j", "user"),
        "neo4j_password": ("neo4j", "password"),
        "client_id": ("github", "client_id"),
        "client_secret": ("github", "client_secret"),
        "auth_flow": ("github", "auth_flow"),
        "token": ("github", "token"),
    }
    for key, (section, attr) in simple.items():
        val = overrides.get(key)
        if val:
            setattr(getattr(cfg, section), attr, val)
    if overrides.get("clone_dir"):
        cfg.clone_dir = Path(overrides["clone_dir"])
    if overrides.get("ir_dir"):
        cfg.ir_dir = Path(overrides["ir_dir"])
    if overrides.get("languages"):
        cfg.languages = list(overrides["languages"])
    if overrides.get("repos"):
        cfg.repos = [RepoSpec(full_name=r) for r in overrides["repos"]]
