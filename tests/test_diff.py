import subprocess

from repograph.ir import Node
from repograph.query.diff import changed_line_ranges, changed_node_ids


def git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    )


def test_diff_maps_to_symbols(tmp_path):
    git(tmp_path, "init", "-q")
    f = tmp_path / "mod.py"
    f.write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")

    # change only function b (line 6)
    f.write_text("def a():\n    return 1\n\n\ndef b():\n    return 999\n")

    ranges = changed_line_ranges(tmp_path, "HEAD")
    assert "mod.py" in ranges

    nodes = [
        Node(id="r::mod.py", kind="module", name="mod.py", repo="r", path="mod.py"),
        Node(id="r::mod.py::a", kind="function", name="a", repo="r", path="mod.py", start_line=1, end_line=2),
        Node(id="r::mod.py::b", kind="function", name="b", repo="r", path="mod.py", start_line=5, end_line=6),
    ]
    hit = changed_node_ids(nodes, "r", ranges)
    assert "r::mod.py::b" in hit
    assert "r::mod.py::a" not in hit
    assert "r::mod.py" in hit  # the module itself counts as changed


def test_unrelated_repo_not_matched():
    nodes = [Node(id="x::mod.py::a", kind="function", name="a", repo="x", path="mod.py", start_line=1, end_line=2)]
    assert changed_node_ids(nodes, "r", {"mod.py": [(1, 2)]}) == []
