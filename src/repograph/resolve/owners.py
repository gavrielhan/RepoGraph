"""Owner resolution: CODEOWNERS first, config registry as fallback.

Every node gets an `owner` so blast-radius output is directly actionable.
CODEOWNERS semantics: gitignore-style patterns, last matching rule wins.
"""

from __future__ import annotations

from pathlib import Path

CODEOWNERS_LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


class OwnerResolver:
    def __init__(self, repo_roots: dict[str, Path], registry: dict | None = None):
        self._rules: dict[str, list[tuple[object, str]]] = {}
        self._registry = registry or {}
        for repo, root in repo_roots.items():
            self._rules[repo] = _load_codeowners(root)

    def owner_for(self, repo: str, path: str | None) -> str | None:
        # CODEOWNERS: last matching rule wins
        owner = None
        if path:
            for spec, owners in self._rules.get(repo, []):
                if spec.match_file(path):
                    owner = owners
        if owner:
            return owner
        # registry fallback: exact "repo/path-prefix" first, then "repo"
        if path:
            for key, val in self._registry.items():
                if "/" in key and f"{repo}/{path}".startswith(key):
                    return val
        return self._registry.get(repo)


def _load_codeowners(root: Path) -> list[tuple[object, str]]:
    import pathspec

    for loc in CODEOWNERS_LOCATIONS:
        f = root / loc
        if f.exists():
            rules = []
            for line in f.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                pattern, owners = parts[0], " ".join(parts[1:])
                if not owners:
                    continue
                spec = pathspec.PathSpec.from_lines("gitignore", [pattern])
                rules.append((spec, owners))
            return rules
    return []
