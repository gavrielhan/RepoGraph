from repograph.parse.engine import parse_file


def by_id(nodes):
    return {n.id: n for n in nodes}


class TestPythonParsing:
    def test_definitions_and_docstrings(self, parsed):
        nodes = by_id(parsed.nodes)
        run = nodes["axiom_core::axiom_core/utils.py::GridEngine.run"]
        assert run.kind == "method"
        assert run.signature == "run(self, grid, n_jobs=1)"  # defaults kept for signature diffs
        assert run.docstring == "Execute the grid."
        assert run.start_line and run.end_line and run.start_line < run.end_line

    def test_nested_qualnames_and_kinds(self, parsed):
        nodes = by_id(parsed.nodes)
        assert nodes["axiom_core::axiom_core/utils.py::GridEngine"].kind == "class"
        assert nodes["axiom_core::axiom_core/utils.py::_expand"].kind == "function"

    def test_scoped_calls(self, parsed):
        calls = [
            p for p in parsed.pending
            if p.type == "CALLS_PENDING" and p.src_file == "grid_master.py"
        ]
        scoped = {(c.src_scope, c.target) for c in calls}
        assert ("run_grid", "load_config") in scoped
        assert ("run_grid", "GridEngine") in scoped
        assert ("ToxGrid.summarize", "spark.table") in scoped

    def test_imports_capture_names(self, parsed):
        imports = [
            p for p in parsed.pending
            if p.type == "IMPORTS_PENDING" and p.target == "axiom_core.utils"
        ]
        names = {n for p in imports for n in p.meta.get("names", [])}
        assert {"GridEngine", "load_config"} <= names

    def test_data_edges_from_python(self, parsed):
        produces = {(p.src_file, p.target) for p in parsed.pending if p.type == "PRODUCES_PENDING"}
        consumes = {(p.src_file, p.target) for p in parsed.pending if p.type == "CONSUMES_PENDING"}
        assert ("axiom_core/io.py", "simulated_trials") in produces  # df.to_parquet
        assert ("axiom_core/io.py", "trial_summary") in produces  # .write.saveAsTable
        assert ("grid_master.py", "simulated_trials") in consumes  # pd.read_parquet
        assert ("grid_master.py", "trial_summary") in consumes  # spark.table


class TestSqlParsing:
    def test_sql_data_edges(self, parsed):
        sql = [p for p in parsed.pending if p.src_file == "reports.sql"]
        produces = {p.target for p in sql if p.type == "PRODUCES_PENDING"}
        consumes = {p.target for p in sql if p.type == "CONSUMES_PENDING"}
        assert produces == {"monthly_report", "audit_log"}
        assert {"simulated_trials", "trial_summary", "monthly_report"} <= consumes


class TestOtherLanguages:
    def test_javascript(self):
        res = parse_file("t", "a.js", b"""
import { helper } from './util.js';
function main(x) { return helper(x); }
class Runner extends Base { start() { main(1); } }
""", "javascript")
        kinds = {n.id.split("::")[-1]: n.kind for n in res.nodes if n.kind not in ("repo", "module")}
        assert kinds == {"main": "function", "Runner": "class", "Runner.start": "method"}
        assert any(p.type == "INHERITS_PENDING" and p.target == "Base" for p in res.pending)
        assert any(p.type == "IMPORTS_PENDING" and p.target == "./util.js" for p in res.pending)

    def test_go(self):
        res = parse_file("t", "a.go", b"""
package main
import "github.com/example/core/pkg"
type Runner struct { x int }
func (r *Runner) Start() { pkg.Load() }
""", "go")
        kinds = {n.name: n.kind for n in res.nodes if n.kind not in ("repo", "module")}
        assert kinds == {"Runner": "class", "Start": "method"}

    def test_bash(self):
        res = parse_file("t", "a.sh", b"setup() {\n  echo hi\n}\nsetup\n", "bash")
        assert any(n.name == "setup" and n.kind == "function" for n in res.nodes)
        assert any(p.target == "setup" and p.src_scope == "" for p in res.pending)

    def test_scala_no_duplicate_defs(self):
        res = parse_file("t", "A.scala", b"""
class Runner(x: Int) extends Base {
  def start(n: Int): Unit = { helper(n) }
}
""", "scala")
        syms = [n.id.split("::")[-1] for n in res.nodes if n.kind not in ("repo", "module")]
        assert sorted(syms) == ["Runner", "Runner.start"]


class TestIgnores:
    def test_repographignore(self, tmp_path):
        from repograph.parse.engine import parse_repo

        (tmp_path / "keep.py").write_text("def kept(): pass\n")
        (tmp_path / "skip.py").write_text("def skipped(): pass\n")
        (tmp_path / ".repographignore").write_text("skip.py\n")
        res = parse_repo("t", tmp_path)
        names = {n.name for n in res.nodes if n.kind == "function"}
        assert names == {"kept"}
