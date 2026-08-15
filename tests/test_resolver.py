from repograph.resolve.resolver import normalize_dataset


def edge_set(edges, etype):
    return {(e.src, e.dst) for e in edges if e.type == etype}


class TestCallResolution:
    def test_cross_repo_call_via_import(self, resolved):
        _, edges = resolved
        assert (
            "axiom_tox_poc::grid_master.py::run_grid",
            "axiom_core::axiom_core/utils.py::load_config",
        ) in edge_set(edges, "CALLS")

    def test_lexical_scope_beats_repo_wide(self, resolved):
        _, edges = resolved
        # self.setup inside GridEngine.run resolves via class scope walk
        assert (
            "axiom_core::axiom_core/utils.py::GridEngine.run",
            "axiom_core::axiom_core/utils.py::BaseEngine.setup",
        ) in edge_set(edges, "CALLS")

    def test_confidence_present_on_all_edges(self, resolved):
        _, edges = resolved
        assert all("confidence" in e.meta for e in edges if e.type == "CALLS")


class TestInheritsAndImports:
    def test_cross_repo_inherits(self, resolved):
        _, edges = resolved
        assert (
            "axiom_tox_poc::grid_master.py::ToxGrid",
            "axiom_core::axiom_core/utils.py::GridEngine",
        ) in edge_set(edges, "INHERITS")

    def test_cross_repo_import_via_manifest(self, resolved):
        _, edges = resolved
        assert (
            "axiom_tox_poc::grid_master.py",
            "axiom_core::axiom_core/utils.py",
        ) in edge_set(edges, "IMPORTS")

    def test_external_imports_dropped(self, resolved):
        _, edges = resolved
        assert not any("pandas" in e.dst for e in edges if e.type == "IMPORTS")


class TestDataEdges:
    def test_writer_to_reader_without_import(self, resolved):
        """File A writes simulated_trials, file B reads it, no import between them."""
        _, edges = resolved
        assert (
            "axiom_tox_poc::grid_master.py::run_grid",
            "axiom_core::axiom_core/io.py::persist_trials",
        ) in edge_set(edges, "CONSUMES")

    def test_sql_consumes_python_producer(self, resolved):
        _, edges = resolved
        assert (
            "axiom_tox_poc::reports.sql",
            "axiom_core::axiom_core/io.py::persist_trials",
        ) in edge_set(edges, "CONSUMES")

    def test_dataset_nodes_created(self, resolved):
        dataset_nodes, _ = resolved
        names = {n.name for n in dataset_nodes}
        assert {"simulated_trials", "trial_summary", "monthly_report", "audit_log"} <= names

    def test_no_self_consumption(self, resolved):
        _, edges = resolved
        assert not any(e.src == e.dst for e in edges)


class TestDatasetNormalization:
    def test_paths_and_schemes(self):
        assert normalize_dataset("s3://bucket/dir/simulated_trials.parquet") == "simulated_trials"
        assert normalize_dataset("/mnt/data/Simulated_Trials.csv") == "simulated_trials"

    def test_fstring_interpolation(self):
        assert normalize_dataset("trials_{run_id}") == "trials_*"

    def test_generic_names_rejected(self):
        assert normalize_dataset("data") is None
        assert normalize_dataset("tmp") is None
        assert normalize_dataset("ab") is None

    def test_schema_qualified_table(self):
        assert normalize_dataset("analytics.simulated_trials") == "analytics.simulated_trials"


class TestOwners:
    def test_codeowners_last_match_wins(self, parsed, resolved):
        owners = {n.id: n.owner for n in parsed.nodes}
        assert owners["axiom_core::axiom_core/io.py::persist_trials"] == "@axiom/data-team"
        assert owners["axiom_core::axiom_core/utils.py::load_config"] == "@axiom/platform-team"

    def test_github_dir_codeowners(self, parsed, resolved):
        owners = {n.id: n.owner for n in parsed.nodes}
        assert owners["axiom_tox_poc::reports.sql"] == "@axiom/analytics-team"
        assert owners["axiom_tox_poc::grid_master.py"] == "@axiom/tox-team"
