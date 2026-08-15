"""Language registry: file extension -> tree-sitter grammar + query file.

Adding a language = mapping its extensions here and dropping a .scm query
file into parse/queries/. No engine changes required.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

QUERY_DIR = Path(__file__).parent / "queries"

# extension -> language name understood by tree_sitter_language_pack
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".sql": "sql",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".scala": "scala",
    ".sc": "scala",
    ".go": "go",
    ".sh": "bash",
    ".bash": "bash",
}

# languages that share a query file
QUERY_ALIASES: dict[str, str] = {
    "tsx": "typescript",
}


def language_for_path(path: Path | str) -> str | None:
    ext = Path(path).suffix.lower()
    lang = EXTENSION_MAP.get(ext)
    if lang and query_path(lang).exists():
        return lang
    return None


def query_path(lang: str) -> Path:
    return QUERY_DIR / f"{QUERY_ALIASES.get(lang, lang)}.scm"


@lru_cache(maxsize=None)
def get_parser_and_query(lang: str):
    """Returns (Parser, Query) for a language. Cached per language."""
    from tree_sitter_language_pack import get_language, get_parser

    language = get_language(lang)
    parser = get_parser(lang)
    query = _compile_query(language, query_path(lang).read_text())
    return parser, query


def _compile_query(language, source: str):
    try:  # tree-sitter >= 0.25
        from tree_sitter import Query

        return Query(language, source)
    except (ImportError, TypeError):  # older bindings
        return language.query(source)


def run_query_matches(query, root_node) -> list[tuple[int, dict]]:
    """Version-tolerant query execution.

    Returns a list of (pattern_index, {capture_name: [nodes]}).
    """
    try:  # tree-sitter >= 0.25: matches live on QueryCursor
        from tree_sitter import QueryCursor

        return QueryCursor(query).matches(root_node)
    except ImportError:
        return query.matches(root_node)
