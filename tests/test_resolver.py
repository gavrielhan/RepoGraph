from repograph.resolve.resolver import normalize_dataset


def edge_set(edges, etype):
    return {(e.src, e.dst) for e in edges if e.type == etype}


class TestCallResolution:
    def test_cross_repo_call_via_import(self, resolved):
        _, edges = resolved
        assert (
            "app::run.py::run_job",
            "corelib::corelib/utils.py::load_config",
        ) in edge_set(edges, "CALLS")

    def test_lexical_scope_beats_repo_wide(self, resolved):
        _, edges = resolved
        assert (
            "corelib::corelib/utils.py::Engine.run",
            "corelib::corelib/utils.py::BaseEngine.setup",
        ) in edge_set(edges, "CALLS")

    def test_confidence_present_on_all_edges(self, resolved):
        _, edges = resolved
        assert all("confidence" in e.meta for e in edges if e.type == "CALLS")


class TestInheritsAndImports:
    def test_cross_repo_inherits(self, resolved):
        _, edges = resolved
        assert (
            "app::run.py::AppEngine",
            "corelib::corelib/utils.py::Engine",
        ) in edge_set(edges, "INHERITS")

    def test_cross_repo_import_via_manifest(self, resolved):
        _, edges = resolved
        assert (
            "app::run.py",
            "corelib::corelib/utils.py",
        ) in edge_set(edges, "IMPORTS")

    def test_external_imports_dropped(self, resolved):
        _, edges = resolved
        assert not any("pandas" in e.dst for e in edges if e.type == "IMPORTS")


class TestDataEdges:
    def test_writer_to_reader_without_import(self, resolved):
        _, edges = resolved
        assert (
            "app::run.py::run_job",
            "corelib::corelib/io.py::persist_orders",
        ) in edge_set(edges, "CONSUMES")

    def test_sql_consumes_python_producer(self, resolved):
        _, edges = resolved
        assert (
            "app::reports.sql",
            "corelib::corelib/io.py::persist_orders",
        ) in edge_set(edges, "CONSUMES")

    def test_dataset_nodes_created(self, resolved):
        dataset_nodes, _ = resolved
        names = {n.name for n in dataset_nodes}
        assert {"orders", "order_summary", "monthly_report", "audit_log"} <= names

    def test_no_self_consumption(self, resolved):
        _, edges = resolved
        assert not any(e.src == e.dst for e in edges)


class TestDatasetNormalization:
    def test_paths_and_schemes(self):
        assert normalize_dataset("s3://bucket/dir/orders.parquet") == "orders"
        assert normalize_dataset("/mnt/data/Orders.csv") == "orders"

    def test_fstring_interpolation(self):
        assert normalize_dataset("orders_{run_id}") == "orders_*"

    def test_generic_names_rejected(self):
        assert normalize_dataset("data") is None
        assert normalize_dataset("tmp") is None
        assert normalize_dataset("ab") is None

    def test_schema_qualified_table(self):
        assert normalize_dataset("analytics.orders") == "analytics.orders"


class TestOwners:
    def test_codeowners_last_match_wins(self, parsed, resolved):
        owners = {n.id: n.owner for n in parsed.nodes}
        assert owners["corelib::corelib/io.py::persist_orders"] == "@acme/data"
        assert owners["corelib::corelib/utils.py::load_config"] == "@acme/platform"

    def test_github_dir_codeowners(self, parsed, resolved):
        owners = {n.id: n.owner for n in parsed.nodes}
        assert owners["app::reports.sql"] == "@acme/analytics"
        assert owners["app::run.py"] == "@acme/app"
