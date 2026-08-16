"""Resolve human symbol names into deterministic RepoGraph node IDs."""

from __future__ import annotations

from repograph.ir import Node


def find_symbols(nodes: list[Node], pattern: str, limit: int = 50) -> list[dict]:
    needle = pattern.casefold()
    matches = []
    for node in nodes:
        if node.kind not in {"function", "method", "class", "module"}:
            continue
        fields = (node.id, node.name, node.path or "", node.signature or "")
        if not any(needle in field.casefold() for field in fields):
            continue
        exact = node.name.casefold() == needle
        matches.append({
            "id": node.id,
            "kind": node.kind,
            "name": node.name,
            "repo": node.repo,
            "path": node.path,
            "owner": node.owner,
            "signature": node.signature,
            "_exact": exact,
        })
    matches.sort(key=lambda item: (
        not item["_exact"],
        item["name"].casefold(),
        item["repo"].casefold(),
        item["path"] or "",
    ))
    for item in matches:
        item.pop("_exact")
    return matches[:limit]
