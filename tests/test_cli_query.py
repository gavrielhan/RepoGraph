from click.testing import CliRunner

from repograph.cli import main


def test_query_and_blast_are_aliases():
    runner = CliRunner()
    q = runner.invoke(main, ["query", "--help"])
    b = runner.invoke(main, ["blast", "--help"])
    assert q.exit_code == 0
    assert b.exit_code == 0
    assert "--pr" in q.output
    assert "--branch" in q.output
    assert "--pr" in b.output


def test_query_rejects_mixed_sources():
    runner = CliRunner()
    result = runner.invoke(main, ["query", "--pr", "1", "--changed", "x::y::z"])
    assert result.exit_code != 0
    assert "only one of" in result.output
