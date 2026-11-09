"""The repo-root example files must validate clean (PRD §6: clean example repo)."""

import shutil
from pathlib import Path

from click.testing import CliRunner

from steward.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_example_files_validate_clean(tmp_path: Path) -> None:
    for example in REPO_ROOT.glob("*.example.yaml"):
        shutil.copy(example, tmp_path / example.name.replace(".example.yaml", ".yaml"))
    result = CliRunner().invoke(main, ["validate", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "validation passed" in result.output
