"""Smoke tests for the CLI skeleton."""

from click.testing import CliRunner

from steward.cli import main


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ["generate-dues", "import", "validate", "commit", "close", "init"]:
        assert command in result.output


def test_commands_fail_gracefully() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["validate"])
    assert result.exit_code != 0
    assert "not implemented" in result.output
