"""Smoke tests for the CLI skeleton."""

import pytest
from click.testing import CliRunner

from steward.cli import main


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ["generate-dues", "import", "validate", "commit", "close", "init"]:
        assert command in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["commit"],
        ["close", "--year", "2026"],
        ["init"],
    ],
)
def test_unimplemented_commands_fail_gracefully(argv: list[str]) -> None:
    result = CliRunner().invoke(main, argv)
    assert result.exit_code != 0
    assert "not implemented" in result.output
