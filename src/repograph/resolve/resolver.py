"""Stage 3: turn pending edges into real graph edges.

Resolution strategies, in order of confidence:

  Calls / inherits:
    1. lexical scope walk within the same file          (0.90)
    2. imported-name match via the file's import table  (0.90)
    3. exact qualname match within the repo             (0.80)
    4. unique name match within the repo                (0.60)
    5. ambiguous name match (max 2 candidates)          (0.40 each)
    unresolved -> dropped. Downstream consumers rank by `confidence`
    instead of hard-gating, so missing edges (dynamic dispatch, getattr,
    config-built names) degrade recall, not correctness.

  Imports:
    relative -> path-resolved within the repo           (0.95)
    absolute -> module index within the repo            (0.95)
    cross-repo via package-name -> repo manifest map    (0.90 module,
                                                         0.75 repo fallback)
    external packages are skipped.

  Data edges:
    string literals normalized (case, scheme/path prefixes, extensions,
    f-string interpolations -> wildcards), matched producer <-> consumer.
    Exact match 0.8, schema-qualified suffix match 0.7, wildcard 0.5.
    Very short or stoplisted names are dropped (false-positive control).

SCIP seam: pass `preresolved` edges (e.g. converted from scip-python /
scip-java output, see resolve/scip.py). They are merged at confidence 1.0
and win over fuzzy matches for the same (src, dst, type).
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from pathlib import Path

from repograph import ids
from repograph.ir import Edge, Node, PendingEdge
from repograph.resolve.manifests import normalize_package_name, package_map
from repograph.resolve.owners import OwnerResolver

SELF_PREFIXES = ("self.", "this.", "cls.")
DATASET_STOPLIST = {
    "data", "output", "input", "tmp", "temp", "test", "df", "table",
    "tables", "result", "results", "out", "file", "path", "dataset",
}
_EXT_RE = re.compile(
    r"\.(parquet|csv|json|jsonl|delta|feather|orc|avro|pkl|pickle|h5|hdf5|txt|tsv|xlsx)$"
)
_SCHEME_RE = re.compile(r"^[a-z0-9+.-]+://")


def resolve(
    nodes: list[Node],
    pending: list[PendingEdge],
    repo_roots: dict[str, Path],
    owner_registry: dict | None = None,
    preresolved: list[Edge] | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Returns (extra_nodes, resolved_edges).

    extra_nodes = Dataset nodes created during data-edge reconciliation.
    Also annotates `owner` on the given nodes in place.
    """
    index = _Index(nodes)
    index.register_imports(pending)
    pkg_map = package_map(repo_roots)

    _annotate_owners(nodes, repo_roots, owner_registry)

    edges: list[Edge] = []
    produces: list[tuple[str, str, dict]] = []  # (src_node_id, raw_literal, meta)
    consumes: list[tuple[str, str, dict]] = []

    for p in pending:
        src = index.source_node_id(p)
        if src is None:
            continue
        if p.type == "CALLS_PENDING":
            edges.extend(_resolve_callish(p, src, "CALLS", index, pkg_map))
        elif p.type == "INHERITS_PENDING":
            edges.extend(_resolve_callish(p, src, "INHERITS", index, pkg_map))
        elif p.type == "IMPORTS_PENDING":
            edges.extend(_resolve_import(p, src, index, pkg_map))
        elif p.type == "PRODUCES_PENDING":
            produces.append((src, p.target, dict(p.meta)))
        elif p.type == "CONSUMES_PENDING":
            consumes.append((src, p.target, dict(p.meta)))

    dataset_nodes, data_edges = _reconcile_data_edges(produces, consumes)
    edges.extend(data_edges)

    if preresolved:
        for e in preresolved:
            e.meta.setdefault("confidence", 1.0)
            e.meta["source"] = e.meta.get("source", "scip")
        edges.extend(preresolved)

    return dataset_nodes, _dedupe(edges)


# --------------------------------------------------------------------------
# indexes


class _Index:
    def __init__(self, nodes: list[Node]):
        self.by_id = {n.id: n for n in nodes}
        self.sym_by_repo_qualname: dict[tuple[str, str], list[Node]] = defaultdict(list)
        self.sym_by_repo_name: dict[tuple[str, str], list[Node]] = defaultdict(list)
        self.sym_by_file: dict[tuple[str, str], dict[str, Node]] = defaultdict(dict)
        self.module_by_repo_dotted: dict[tuple[str, str], list[Node]] = defaultdict(list)
        self.module_by_repo_path: dict[tuple[str, str], Node] = {}
        self.imports_by_file: dict[tuple[str, str], list[tuple[str, list[str]]]] = defaultdict(list)

        for n in nodes:
            if n.kind in ("function", "method", "class"):
                qualname = ids.parse_id(n.id)["qualname"] or n.name
                self.sym_by_repo_qualname[(n.repo, qualname)].append(n)
                self.sym_by_repo_name[(n.repo, n.name)].append(n)
                self.sym_by_file[(n.repo, n.path)][qualname] = n
            elif n.kind == "module":
                self.module_by_repo_path[(n.repo, n.path)] = n
                for dotted in _dotted_candidates(n.path):
                    self.module_by_repo_dotted[(n.repo, dotted)].append(n)

    def register_imports(self, pending: list[PendingEdge]) -> None:
        for p in pending:
            if p.type == "IMPORTS_PENDING":
                names = [n for n in p.meta.get("names", []) if n]
                self.imports_by_file[(p.repo, p.src_file)].append((p.target, names))

    def source_node_id(self, p: PendingEdge) -> str | None:
        if p.src_scope:
            sym = self.sym_by_file.get((p.repo, p.src_file), {}).get(p.src_scope)
            if sym:
                return sym.id
        mod = self.module_by_repo_path.get((p.repo, p.src_file))
        return mod.id if mod else None

    def resolve_module(self, repo: str, dotted: str, pkg_map: dict[str, str]) -> tuple[Node | None, str | None, float]:
        """-> (module_node, fallback_repo_name, confidence)."""
        mods = self.module_by_repo_dotted.get((repo, dotted))
        if mods:
            return mods[0], None, 0.95
        top = normalize_package_name(dotted.split(".")[0].split("/")[0])
        other = pkg_map.get(top)
        if other and other != repo:
            mods = self.module_by_repo_dotted.get((other, dotted))
            if mods:
                return mods[0], None, 0.90
            return None, other, 0.75
        return None, None, 0.0


def _dotted_candidates(path: str) -> set[str]:
    p = path
    if p.endswith((".py", ".pyi")):
        p = p.rsplit(".", 1)[0]
    else:
        p = re.sub(r"\.[a-z]+$", "", p)
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    parts = [part for part in p.split("/") if part]
    out = set()
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        if cand:
            out.add(cand)
    return out


# --------------------------------------------------------------------------
# calls / inherits


def _resolve_callish(p: PendingEdge, src: str, edge_type: str, index: _Index, pkg_map: dict) -> list[Edge]:
    target = p.target
    for pref in SELF_PREFIXES:
        if target.startswith(pref):
            target = target[len(pref):]
            break

    meta_base = {"raw": p.target, "line": p.meta.get("line")}

    # 1. lexical scope walk within the same file
    file_syms = index.sym_by_file.get((p.repo, p.src_file), {})
    scope_parts = p.src_scope.split(".") if p.src_scope else []
    for depth in range(len(scope_parts), -1, -1):
        prefix = ".".join(scope_parts[:depth])
        qualname = f"{prefix}.{target}" if prefix else target
        if qualname in file_syms and file_syms[qualname].id != src:
            return [Edge(edge_type, src, file_syms[qualname].id, {**meta_base, "confidence": 0.9})]

    # 2. imported names
    for module, names in index.imports_by_file.get((p.repo, p.src_file), []):
        hit = None
        if target in names or target.split(".")[0] in names:
            hit = target
        elif target.startswith(module + "."):
            hit = target[len(module) + 1 :]
        if hit is None:
            continue
        mod_node, _fallback, conf = index.resolve_module(p.repo, module, pkg_map)
        if mod_node is None:
            continue
        sym = index.sym_by_file.get((mod_node.repo, mod_node.path), {}).get(hit)
        if sym:
            return [Edge(edge_type, src, sym.id, {**meta_base, "confidence": round(conf, 2)})]

    # 3. exact qualname within repo
    exact = index.sym_by_repo_qualname.get((p.repo, target), [])
    exact = [s for s in exact if s.id != src]
    if exact:
        return [Edge(edge_type, src, exact[0].id, {**meta_base, "confidence": 0.8})]

    # 4/5. name match within repo
    name = target.split(".")[-1]
    candidates = [s for s in index.sym_by_repo_name.get((p.repo, name), []) if s.id != src]
    if len(candidates) == 1:
        return [Edge(edge_type, src, candidates[0].id, {**meta_base, "confidence": 0.6})]
    if len(candidates) == 2:
        return [Edge(edge_type, src, c.id, {**meta_base, "confidence": 0.4}) for c in candidates]
    return []


# --------------------------------------------------------------------------
# imports


def _resolve_import(p: PendingEdge, src: str, index: _Index, pkg_map: dict) -> list[Edge]:
    target = p.target
    meta_base = {"raw": target, "line": p.meta.get("line")}

    # relative python import: resolve against the importing file's directory
    if target.startswith("."):
        dots = len(target) - len(target.lstrip("."))
        rest = target.lstrip(".")
        base_parts = p.src_file.split("/")[:-1]
        for _ in range(dots - 1):
            if base_parts:
                base_parts.pop()
        dotted = ".".join([*base_parts, *([rest] if rest else [])]).strip(".")
        mods = index.module_by_repo_dotted.get((p.repo, dotted), [])
        if mods:
            return [Edge("IMPORTS", src, mods[0].id, {**meta_base, "confidence": 0.95})]
        return []

    # relative JS-style import: ./util, ../lib/util
    if target.startswith(("./", "../")):
        resolved = _resolve_relative_path(p.repo, p.src_file, target, index)
        if resolved:
            return [Edge("IMPORTS", src, resolved.id, {**meta_base, "confidence": 0.95})]
        return []

    dotted = target.replace("/", ".") if "/" in target else target
    mod_node, fallback_repo, conf = index.resolve_module(p.repo, dotted, pkg_map)
    if mod_node is not None:
        if mod_node.id == src:
            return []
        return [Edge("IMPORTS", src, mod_node.id, {**meta_base, "confidence": round(conf, 2)})]
    if fallback_repo:
        return [Edge("IMPORTS", src, ids.repo_id(fallback_repo), {**meta_base, "confidence": 0.75, "package": target})]
    return []  # external package


def _resolve_relative_path(repo: str, src_file: str, target: str, index: _Index):
    from posixpath import normpath

    base = "/".join(src_file.split("/")[:-1])
    joined = normpath(f"{base}/{target}") if base else normpath(target)
    for cand in (
        joined,
        *(f"{joined}{ext}" for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")),
        *(f"{joined}/index{ext}" for ext in (".js", ".ts")),
    ):
        node = index.module_by_repo_path.get((repo, cand))
        if node:
            return node
    return None


# --------------------------------------------------------------------------
# data edges


def normalize_dataset(raw: str) -> str | None:
    """Normalize a data-target string literal to a matching key.

    Returns None when the name is too generic to be trusted.
    """
    n = raw.strip().strip("'\"").lower()
    n = _SCHEME_RE.sub("", n)
    n = n.removeprefix("dbfs:").removeprefix("/")
    n = re.sub(r"\{[^}]*\}", "*", n)  # f-string interpolations
    n = n.rstrip("/")
    if "/" in n:
        n = n.rsplit("/", 1)[1]
    n = _EXT_RE.sub("", n)
    n = n.strip()
    if len(n) < 3 or n in DATASET_STOPLIST or n == "*":
        return None
    return n


def _table_key(normalized: str) -> str:
    """Last dot-segment: `analytics.orders` -> `orders`."""
    return normalized.split(".")[-1]


def _reconcile_data_edges(
    produces: list[tuple[str, str, dict]],
    consumes: list[tuple[str, str, dict]],
) -> tuple[list[Node], list[Edge]]:
    edges: list[Edge] = []
    dataset_nodes: dict[str, Node] = {}

    def dataset_node(key: str) -> str:
        did = ids.dataset_id(key)
        if did not in dataset_nodes:
            dataset_nodes[did] = Node(id=did, kind="dataset", name=key, repo="")
        return did

    norm_produces = []
    for src, raw, meta in produces:
        norm = normalize_dataset(raw)
        if norm is None:
            continue
        key = _table_key(norm)
        did = dataset_node(key)
        edges.append(Edge("PRODUCES", src, did, {"dataset": key, "raw": raw, "confidence": 0.8, **meta}))
        norm_produces.append((src, norm, key, raw))

    for src, raw, meta in consumes:
        norm = normalize_dataset(raw)
        if norm is None:
            continue
        key = _table_key(norm)
        did = dataset_node(key)
        edges.append(Edge("CONSUMES", src, did, {"dataset": key, "raw": raw, "confidence": 0.8, **meta}))

        # direct reader -> writer edges power the canonical blast-radius query
        for w_src, w_norm, w_key, w_raw in norm_produces:
            if w_src == src:
                continue
            conf = _match_confidence(norm, w_norm, key, w_key)
            if conf > 0:
                edges.append(
                    Edge("CONSUMES", src, w_src, {"dataset": key, "confidence": conf, "derived": True})
                )
    return list(dataset_nodes.values()), edges


def _match_confidence(r_norm: str, w_norm: str, r_key: str, w_key: str) -> float:
    if r_norm == w_norm:
        return 0.8
    if r_key == w_key:
        return 0.7
    if "*" in r_norm or "*" in w_norm:
        if fnmatch.fnmatch(r_norm, w_norm) or fnmatch.fnmatch(w_norm, r_norm):
            return 0.5
    return 0.0


# --------------------------------------------------------------------------
# owners & dedupe


def _annotate_owners(nodes: list[Node], repo_roots: dict[str, Path], registry: dict | None) -> None:
    resolver = OwnerResolver(repo_roots, registry)
    for n in nodes:
        if n.owner is None:
            n.owner = resolver.owner_for(n.repo, n.path)


def _dedupe(edges: list[Edge]) -> list[Edge]:
    best: dict[tuple[str, str, str], Edge] = {}
    for e in edges:
        key = (e.type, e.src, e.dst)
        prev = best.get(key)
        if prev is None or e.meta.get("confidence", 0) > prev.meta.get("confidence", 0):
            best[key] = e
    return list(best.values())
