"""Stage 1: tree-sitter parse -> IR nodes + pending edges.

The engine is language-agnostic. Per-language ``.scm`` query files declare
what to extract through a fixed capture-name convention:

    @def.function / @def.method / @def.class   whole definition node
    @def.name                                   name identifier
    @def.params                                 parameter list (optional)
    @def.doc                                    docstring node (optional)
    @call            call node        @call.name    callee expression
    @import          import node      @import.module module string/name
                                      @import.name   imported symbol (optional)
    @inherit         class node       @inherit.name  superclass name
    @data.write      call/stmt node   @data.write.target  string literal
    @data.read       call/stmt node   @data.read.target   string literal

Captures prefixed with ``_`` are query-internal (predicate anchors) and
ignored by the engine. Scopes and qualified names are computed structurally
from definition byte ranges, so query files stay simple.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from repograph import ids
from repograph.ir import Edge, Node, PendingEdge
from repograph.parse.languages import (
    get_parser_and_query,
    language_for_path,
    run_query_matches,
)

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".idea", ".vscode",
}
MAX_FILE_BYTES = 2_000_000  # skip generated/vendored monsters


@dataclass
class ParseResult:
    nodes: list[Node] = field(default_factory=list)
    pending: list[PendingEdge] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)  # CONTAINS / DEFINES

    def extend(self, other: "ParseResult") -> None:
        self.nodes.extend(other.nodes)
        self.pending.extend(other.pending)
        self.edges.extend(other.edges)


def parse_repo(
    repo_name: str,
    repo_root: Path,
    languages: list[str] | None = None,
    path_globs: list[str] | None = None,
) -> ParseResult:
    """Walk a repo, parse every file with a registered grammar, emit IR."""
    result = ParseResult()
    result.nodes.append(Node(id=ids.repo_id(repo_name), kind="repo", name=repo_name, repo=repo_name))

    ignore = _load_ignores(repo_root)
    for file_path in _walk(repo_root, ignore, path_globs):
        lang = language_for_path(file_path)
        if lang is None or (languages and lang not in languages):
            continue
        rel = file_path.relative_to(repo_root).as_posix()
        try:
            source = file_path.read_bytes()
        except OSError:
            continue
        if len(source) > MAX_FILE_BYTES:
            continue
        result.extend(parse_file(repo_name, rel, source, lang))
    return result


def parse_file(repo_name: str, rel_path: str, source: bytes, lang: str) -> ParseResult:
    result = ParseResult()
    parser, query = get_parser_and_query(lang)
    tree = parser.parse(source)

    mod_id = ids.module_id(repo_name, rel_path)
    result.nodes.append(
        Node(
            id=mod_id, kind="module", name=rel_path, repo=repo_name, path=rel_path,
            lang=lang, start_line=1, end_line=tree.root_node.end_point[0] + 1,
        )
    )
    result.edges.append(Edge(type="DEFINES", src=ids.repo_id(repo_name), dst=mod_id))

    matches = run_query_matches(query, tree.root_node)

    # ---- pass 1: definitions (needed for scope/qualname computation) ----
    defs: list[_Def] = []
    for _pattern, caps in matches:
        caps = _normalize(caps)
        def_node, def_kind = _def_capture(caps)
        if def_node is None:
            continue
        name_node = _first(caps, "def.name")
        if name_node is None:
            continue
        defs.append(
            _Def(
                node=def_node,
                kind=def_kind,
                name=_text(source, name_node),
                params=_text(source, _first(caps, "def.params")) if _first(caps, "def.params") else None,
                doc=_clean_string(_text(source, _first(caps, "def.doc"))) if _first(caps, "def.doc") else None,
            )
        )
    defs = _merge_same_range(defs)
    defs.sort(key=lambda d: (d.node.start_byte, -d.node.end_byte))
    _assign_qualnames(defs)
    scope_index = _ScopeIndex(defs)

    for d in defs:
        kind = d.kind
        if kind == "function" and d.parent is not None and d.parent.kind == "class":
            kind = "method"
        signature = f"{d.name}{d.params}" if d.params else d.name
        doc = d.doc if d.doc is not None else _leading_comment(source, d.node)
        sym_id = ids.symbol_id(repo_name, rel_path, d.qualname)
        result.nodes.append(
            Node(
                id=sym_id, kind=kind, name=d.name, repo=repo_name, path=rel_path,
                signature=signature, docstring=doc, lang=lang,
                start_line=d.node.start_point[0] + 1, end_line=d.node.end_point[0] + 1,
            )
        )
        result.edges.append(Edge(type="CONTAINS", src=mod_id, dst=sym_id))

    # ---- pass 2: references ----
    for _pattern, caps in matches:
        caps = _normalize(caps)
        if "call.name" in caps:
            for node in caps["call.name"]:
                result.pending.append(
                    PendingEdge(
                        type="CALLS_PENDING", repo=repo_name, src_file=rel_path,
                        src_scope=scope_index.scope_at(node.start_byte),
                        target=_text(source, node),
                        meta={"line": node.start_point[0] + 1},
                    )
                )
        if "import.module" in caps:
            names = [_text(source, n) for n in caps.get("import.name", [])]
            whole_import = _first(caps, "import")
            for node in caps["import.module"]:
                text = _text(source, node)
                if whole_import is not None and node.id == whole_import.id:
                    # capture is the whole statement (e.g. Scala): drop the keyword
                    parts = text.split(None, 1)
                    text = parts[1] if len(parts) > 1 else text
                result.pending.append(
                    PendingEdge(
                        type="IMPORTS_PENDING", repo=repo_name, src_file=rel_path,
                        src_scope=scope_index.scope_at(node.start_byte),
                        target=_clean_string(text),
                        meta={"names": names, "line": node.start_point[0] + 1},
                    )
                )
        if "inherit.name" in caps:
            for node in caps["inherit.name"]:
                result.pending.append(
                    PendingEdge(
                        type="INHERITS_PENDING", repo=repo_name, src_file=rel_path,
                        src_scope=scope_index.scope_at(node.start_byte),
                        target=_text(source, node),
                        meta={"line": node.start_point[0] + 1},
                    )
                )
        for direction, edge_type in (("data.write.target", "PRODUCES_PENDING"), ("data.read.target", "CONSUMES_PENDING")):
            for node in caps.get(direction, []):
                literal = _clean_string(_text(source, node))
                if not literal:
                    continue
                result.pending.append(
                    PendingEdge(
                        type=edge_type, repo=repo_name, src_file=rel_path,
                        src_scope=scope_index.scope_at(node.start_byte),
                        target=literal,
                        meta={"line": node.start_point[0] + 1},
                    )
                )
    return result


# --------------------------------------------------------------------------
# helpers


@dataclass
class _Def:
    node: object
    kind: str
    name: str
    params: str | None = None
    doc: str | None = None
    parent: "_Def | None" = None
    qualname: str = ""


def _merge_same_range(defs: list["_Def"]) -> list["_Def"]:
    """Multiple query patterns may match the same definition node (e.g. a
    with-params and a without-params variant). Merge them, preferring
    non-null fields, so qualnames don't self-nest."""
    merged: dict[tuple[int, int], _Def] = {}
    for d in defs:
        key = (d.node.start_byte, d.node.end_byte)
        prev = merged.get(key)
        if prev is None:
            merged[key] = d
        else:
            prev.params = prev.params or d.params
            prev.doc = prev.doc or d.doc
    return list(merged.values())


def _assign_qualnames(defs: list["_Def"]) -> None:
    """defs must be sorted by (start_byte, -end_byte). Nesting by containment."""
    stack: list[_Def] = []
    for d in defs:
        while stack and not (
            stack[-1].node.start_byte <= d.node.start_byte and d.node.end_byte <= stack[-1].node.end_byte
        ):
            stack.pop()
        d.parent = stack[-1] if stack else None
        d.qualname = f"{d.parent.qualname}.{d.name}" if d.parent else d.name
        stack.append(d)


class _ScopeIndex:
    """Innermost enclosing definition qualname for a byte offset."""

    def __init__(self, defs: list["_Def"]):
        self._defs = defs  # already sorted by (start, -end)

    def scope_at(self, byte: int) -> str:
        best = ""
        for d in self._defs:
            if d.node.start_byte <= byte <= d.node.end_byte:
                best = d.qualname  # keeps narrowing: sorted outer-first
            elif d.node.start_byte > byte:
                break
        return best


def _def_capture(caps: dict) -> tuple[object | None, str]:
    for cap, kind in (("def.function", "function"), ("def.method", "method"), ("def.class", "class")):
        if cap in caps:
            return caps[cap][0], kind
    return None, ""


def _normalize(caps: dict) -> dict:
    """Capture dicts may hold a node or a list depending on binding version."""
    out = {}
    for name, val in caps.items():
        if name.startswith("_"):
            continue
        out[name] = val if isinstance(val, list) else [val]
    return out


def _first(caps: dict, name: str):
    nodes = caps.get(name)
    return nodes[0] if nodes else None


def _text(source: bytes, node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


_STR_PREFIX = re.compile(r"^[a-zA-Z]{0,3}(['\"])")


def _clean_string(text: str) -> str:
    """Strip quotes and string prefixes (f/r/b), collapse triple quotes."""
    text = text.strip()
    m = _STR_PREFIX.match(text)
    if m:
        quote = m.group(1)
        for q in (quote * 3, quote):
            prefix_end = m.end() - 1
            if text[prefix_end:].startswith(q) and text.endswith(q):
                return text[prefix_end + len(q) : -len(q)].strip()
    return text


def _leading_comment(source: bytes, node) -> str | None:
    sib = getattr(node, "prev_named_sibling", None)
    if sib is not None and "comment" in sib.type:
        return _text(source, sib).lstrip("/#- ").strip() or None
    return None


def _load_ignores(repo_root: Path):
    """Combine .gitignore and .repographignore via pathspec if available."""
    patterns: list[str] = []
    for name in (".gitignore", ".repographignore"):
        f = repo_root / name
        if f.exists():
            patterns.extend(f.read_text(errors="replace").splitlines())
    if not patterns:
        return None
    try:
        import pathspec

        return pathspec.PathSpec.from_lines("gitignore", patterns)
    except ImportError:
        return None


def _walk(repo_root: Path, ignore, path_globs: list[str] | None):
    import fnmatch
    import os

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            rel = fpath.relative_to(repo_root).as_posix()
            if ignore is not None and ignore.match_file(rel):
                continue
            if path_globs and not any(fnmatch.fnmatch(rel, g) for g in path_globs):
                continue
            yield fpath
