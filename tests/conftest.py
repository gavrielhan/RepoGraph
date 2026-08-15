from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def repo_roots() -> dict[str, Path]:
    return {
        "axiom_core": FIXTURES / "axiom_core",
        "axiom_tox_poc": FIXTURES / "axiom_tox_poc",
    }


@pytest.fixture(scope="session")
def parsed(repo_roots):
    """Parse both fixture repos once per session."""
    from repograph.parse.engine import ParseResult, parse_repo

    combined = ParseResult()
    for repo, root in repo_roots.items():
        combined.extend(parse_repo(repo, root))
    return combined


@pytest.fixture(scope="session")
def resolved(parsed, repo_roots):
    from repograph.resolve import resolve

    dataset_nodes, edges = resolve(parsed.nodes, parsed.pending, repo_roots)
    return dataset_nodes, edges
