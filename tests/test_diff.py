import subprocess
from pathlib import Path

from repograph.ir import Node
from repograph.query.diff import (
    branch_diff_ranges,
    changed_line_ranges,
    changed_node_ids,
    infer_graph_repo,
    parse_github_remote,
    parse_unified_diff,
    ranges_from_file_diffs,
)
from repograph.query.pr import parse_pr_spec


def git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    )


def _nodes():
    return [
        Node(id="r::mod.py", kind="module", name="mod.py", repo="r", path="mod.py"),
        Node(id="r::mod.py::a", kind="function", name="a", repo="r", path="mod.py", start_line=1, end_line=2),
        Node(id="r::mod.py::b", kind="function", name="b", repo="r", path="mod.py", start_line=5, end_line=6),
    ]


def test_diff_maps_to_symbols(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    f = tmp_path / "mod.py"
    f.write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")

    f.write_text("def a():\n    return 1\n\n\ndef b():\n    return 999\n")

    ranges = changed_line_ranges(tmp_path, "HEAD")
    assert "mod.py" in ranges
    hit = changed_node_ids(_nodes(), "r", ranges)
    assert "r::mod.py::b" in hit
    assert "r::mod.py::a" not in hit
    assert "r::mod.py" in hit


def test_unrelated_repo_not_matched():
    nodes = [Node(id="x::mod.py::a", kind="function", name="a", repo="x", path="mod.py", start_line=1, end_line=2)]
    assert changed_node_ids(nodes, "r", {"mod.py": [(1, 2)]}) == []


def test_parse_unified_diff_old_and_new_sides():
    text = """diff --git a/mod.py b/mod.py
--- a/mod.py
+++ b/mod.py
@@ -5,2 +8,2 @@
"""
    files = parse_unified_diff(text)
    assert files[0].path == "mod.py"
    assert files[0].old_ranges == [(5, 6)]
    assert files[0].new_ranges == [(8, 9)]


def test_deleted_file_covers_whole_path():
    text = """diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
"""
    ranges = ranges_from_file_diffs(parse_unified_diff(text))
    assert ranges["gone.py"][-1] == (1, 10_000_000)


def test_github_patch_without_headers_uses_default_path():
    patch = "@@ -10,1 +10,2 @@\n"
    files = parse_unified_diff(patch, default_path="src/app.py")
    assert files[0].path == "src/app.py"
    assert files[0].old_ranges == [(10, 10)]


def test_parse_pr_spec_variants(tmp_path):
    assert parse_pr_spec("owner/repo#42") == ("owner", "repo", 42)
    assert parse_pr_spec("owner/repo/42") == ("owner", "repo", 42)
    assert parse_pr_spec("https://github.com/owner/repo/pull/99") == ("owner", "repo", 99)
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", "git@github.com:acme/widget.git")
    assert parse_pr_spec("7", tmp_path) == ("acme", "widget", 7)


def test_parse_github_remote():
    assert parse_github_remote("https://github.com/acme/widget.git") == ("acme", "widget")
    assert parse_github_remote("git@github.com:acme/widget.git") == ("acme", "widget")


def test_infer_graph_repo_normalizes_dashes():
    nodes = [Node(id="axiom_core", kind="repo", name="axiom_core", repo="axiom_core")]
    assert infer_graph_repo(Path("/tmp/axiom-core"), nodes, hint="axiom-core") == "axiom_core"


def test_branch_diff_vs_main(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    f = tmp_path / "mod.py"
    f.write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    git(tmp_path, "checkout", "-q", "-b", "feat/change-b")
    f.write_text("def a():\n    return 1\n\n\ndef b():\n    return 999\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "change b")

    base, ranges = branch_diff_ranges(tmp_path, branch="HEAD", base="main", include_uncommitted=False)
    assert base == "main"
    hit = changed_node_ids(_nodes(), "r", ranges)
    assert "r::mod.py::b" in hit
    assert "r::mod.py::a" not in hit
