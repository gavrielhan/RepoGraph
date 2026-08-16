"""Intermediate representation (Stage 2).

Two JSONL files connect the pipeline stages:

``nodes.jsonl`` — one record per node:
    { id, kind, name, repo, path, owner, signature, docstring,
      start_line, end_line, lang }

    kind is one of: "repo", "module", "function", "method", "class",
    "dataset".

``edges.jsonl`` — pending edges out of the parser (Stage 1):
    { type: "CALLS_PENDING" | "IMPORTS_PENDING"
          | "PRODUCES_PENDING" | "CONSUMES_PENDING",
      src_file, src_scope, target, meta }

    and resolved edges out of the resolver (Stage 3):
    { type: "CALLS" | "IMPORTS" | "INHERITS" | "PRODUCES" | "CONSUMES"
          | "CONTAINS" | "DEFINES",
      src, dst, meta }

`meta` is a flat dict; the resolver stamps `confidence` (0..1) into it so
downstream consumers can rank instead of hard-gating on edge presence.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


class IRError(ValueError):
    """Invalid or truncated IR JSONL."""

PENDING_TYPES = {
    "CALLS_PENDING",
    "IMPORTS_PENDING",
    "PRODUCES_PENDING",
    "CONSUMES_PENDING",
    "INHERITS_PENDING",
}

RESOLVED_TYPES = {
    "CALLS",
    "IMPORTS",
    "INHERITS",
    "PRODUCES",
    "CONSUMES",
    "CONTAINS",
    "DEFINES",
}


@dataclass
class Node:
    id: str
    kind: str
    name: str
    repo: str
    path: str | None = None
    owner: str | None = None
    signature: str | None = None
    docstring: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    lang: str | None = None

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PendingEdge:
    type: str
    src_file: str  # repo-relative path of the file the reference appears in
    src_scope: str  # qualname of enclosing definition; "" = module level
    target: str  # raw name / module string / dataset literal
    repo: str
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Edge:
    type: str
    src: str  # node id
    dst: str  # node id
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


def write_jsonl(path: Path, records: Iterable) -> int:
    """Atomically write records (dataclasses or dicts) to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    n = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in records:
                payload = rec.to_json() if hasattr(rec, "to_json") else rec
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                n += 1
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return n


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise IRError(
                    f"truncated or invalid JSONL at {path}:{lineno}: {exc.msg}"
                ) from exc


def load_nodes(path: Path) -> list[Node]:
    return [Node(**rec) for rec in read_jsonl(path)]


def load_pending_edges(path: Path) -> list[PendingEdge]:
    return [PendingEdge(**rec) for rec in read_jsonl(path) if rec["type"] in PENDING_TYPES]


def load_resolved_edges(path: Path) -> list[Edge]:
    return [Edge(**rec) for rec in read_jsonl(path) if rec["type"] in RESOLVED_TYPES]
