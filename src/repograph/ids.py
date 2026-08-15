"""Central node ID scheme.

Deterministic IDs are what make incremental re-index and MERGE idempotency
work, so every component builds IDs through this module and nowhere else.

Scheme:
    repo node    ->  <repo>
    module node  ->  <repo>::<path>
    symbol node  ->  <repo>::<path>::<qualname>

`repo` is the short repo name (not owner/name), `path` is the
repo-relative POSIX path, and `qualname` is the dot-joined nesting of
definition names (e.g. ``OuterClass.method``).

Example: ``app::run.py::run_job``
"""

from __future__ import annotations

SEP = "::"


def repo_id(repo: str) -> str:
    return repo


def module_id(repo: str, path: str) -> str:
    return f"{repo}{SEP}{_norm_path(path)}"


def symbol_id(repo: str, path: str, qualname: str) -> str:
    return f"{repo}{SEP}{_norm_path(path)}{SEP}{qualname}"


def dataset_id(name: str) -> str:
    """Datasets are global (cross-repo by nature)."""
    return f"dataset{SEP}{name}"


def parse_id(node_id: str) -> dict:
    """Split an ID back into its parts. Returns keys: repo, path, qualname
    (path/qualname may be None for repo/module IDs)."""
    parts = node_id.split(SEP)
    return {
        "repo": parts[0],
        "path": parts[1] if len(parts) > 1 else None,
        "qualname": parts[2] if len(parts) > 2 else None,
    }


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")
