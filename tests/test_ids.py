from repograph import ids


def test_symbol_id_scheme():
    assert ids.symbol_id("axiom_tox_poc", "grid_master.py", "run_grid") == (
        "axiom_tox_poc::grid_master.py::run_grid"
    )


def test_module_and_repo_ids():
    assert ids.repo_id("r") == "r"
    assert ids.module_id("r", "a/b.py") == "r::a/b.py"


def test_path_normalization():
    assert ids.module_id("r", "./a\\b.py") == "r::a/b.py"


def test_parse_id_roundtrip():
    parsed = ids.parse_id("repo::pkg/mod.py::Cls.method")
    assert parsed == {"repo": "repo", "path": "pkg/mod.py", "qualname": "Cls.method"}
    assert ids.parse_id("repo")["path"] is None


def test_dataset_id():
    assert ids.dataset_id("simulated_trials") == "dataset::simulated_trials"
