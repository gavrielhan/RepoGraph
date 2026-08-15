"""Package-manifest readers: build the package-name -> repo map.

Cross-repo import resolution relies on the load-bearing premise that the
target repos install each other as packages. Each ecosystem's manifest
declares the package name; when repo B imports a package owned by repo A we
can emit the cross-repo edge.

Supported manifests: pyproject.toml, setup.py, package.json, go.mod,
build.sbt. Names are normalized (dashes -> underscores, lowercased) because
Python distribution names ("axiom-core") differ from import names
("axiom_core").
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def package_map(repo_roots: dict[str, Path]) -> dict[str, str]:
    """{normalized package name -> repo name} across all checkouts."""
    mapping: dict[str, str] = {}
    for repo, root in repo_roots.items():
        for name in packages_in_repo(root):
            mapping[normalize_package_name(name)] = repo
    return mapping


def packages_in_repo(root: Path) -> set[str]:
    names: set[str] = set()
    names.update(_pyproject(root))
    names.update(_setup_py(root))
    names.update(_package_json(root))
    names.update(_go_mod(root))
    names.update(_build_sbt(root))
    names.update(_python_top_level_packages(root))
    return names


def _pyproject(root: Path) -> set[str]:
    f = root / "pyproject.toml"
    if not f.exists():
        return set()
    try:
        import tomllib

        data = tomllib.loads(f.read_text(errors="replace"))
    except Exception:
        return set()
    names = set()
    if name := data.get("project", {}).get("name"):
        names.add(name)
    if name := data.get("tool", {}).get("poetry", {}).get("name"):
        names.add(name)
    for pkg in data.get("tool", {}).get("setuptools", {}).get("packages", []):
        if isinstance(pkg, str):
            names.add(pkg)
    return names


_SETUP_NAME = re.compile(r"""name\s*=\s*["']([^"']+)["']""")


def _setup_py(root: Path) -> set[str]:
    f = root / "setup.py"
    if not f.exists():
        return set()
    m = _SETUP_NAME.search(f.read_text(errors="replace"))
    return {m.group(1)} if m else set()


def _package_json(root: Path) -> set[str]:
    f = root / "package.json"
    if not f.exists():
        return set()
    try:
        data = json.loads(f.read_text(errors="replace"))
    except json.JSONDecodeError:
        return set()
    name = data.get("name")
    return {name} if name else set()


def _go_mod(root: Path) -> set[str]:
    f = root / "go.mod"
    if not f.exists():
        return set()
    for line in f.read_text(errors="replace").splitlines():
        if line.startswith("module "):
            mod = line.split()[1]
            return {mod, mod.split("/")[-1]}
    return set()


_SBT_NAME = re.compile(r"""name\s*:=\s*["']([^"']+)["']""")


def _build_sbt(root: Path) -> set[str]:
    f = root / "build.sbt"
    if not f.exists():
        return set()
    m = _SBT_NAME.search(f.read_text(errors="replace"))
    return {m.group(1)} if m else set()


def _python_top_level_packages(root: Path) -> set[str]:
    """Directories with __init__.py at repo root or under src/ are importable
    package names even when the manifest name differs."""
    names = set()
    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                names.add(child.name)
    return names
