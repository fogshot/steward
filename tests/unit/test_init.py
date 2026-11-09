"""Tests for `steward init` — one per PRD §6 acceptance criterion."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import steward.init_impl as init_impl
from steward.cli import main
from steward.journal import MAIN_JOURNAL_CONTENT, RULES_CONTENT
from steward.model import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES = REPO_ROOT / "src" / "steward" / "templates"

GIT_ENV_VARS = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def no_git_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate Git configuration: no system config, empty global config."""
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in GIT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return empty


@pytest.fixture
def global_git_identity(no_git_identity: Path) -> Path:
    no_git_identity.write_text(
        "[user]\n\tname = Carol Global\n\temail = carol@example.org\n"
    )
    return no_git_identity


# --- happy path ---------------------------------------------------------------


def test_init_validate_roundtrip(tmp_path: Path, global_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code == 0, result.output
    check = CliRunner().invoke(main, ["validate", "--root", str(root)])
    assert check.exit_code == 0, check.output
    assert "validation passed" in check.output


def test_scaffold_layout_and_content(tmp_path: Path, global_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code == 0, result.output
    assert (root / "rules" / "steward.rules").read_text() == RULES_CONTENT
    assert (root / "journals" / "main.journal").read_text() == MAIN_JOURNAL_CONTENT
    runbook = (root / "docs" / "runbook.md").read_text()
    assert "steward import" in runbook  # import routine
    assert "steward commit" in runbook  # commit cadence
    assert "Never edit" in runbook  # correction policy
    assert "Handover" in runbook
    gitignore = (root / ".gitignore").read_text()
    assert ".DS_Store" in gitignore
    assert "*.csv" in gitignore
    for name in [
        "members.yaml",
        "payer_aliases.yaml",
        "categorization.yaml",
        "bank_formats.yaml",
    ]:
        content = (root / name).read_text()
        assert yaml.safe_load(content) is None  # all data entries commented out
        assert content.startswith("# Derived from")


def test_initial_commit_contains_scaffold(
    tmp_path: Path, global_git_identity: Path
) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code == 0, result.output
    log = git(root, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 1
    tracked = git(root, "ls-files").stdout
    for name in [
        "steward.yaml",
        "members.yaml",
        "payer_aliases.yaml",
        "categorization.yaml",
        "bank_formats.yaml",
        "rules/steward.rules",
        "journals/main.journal",
        "docs/runbook.md",
        ".gitignore",
    ]:
        assert name in tracked


def test_next_steps_and_privacy_notice(
    tmp_path: Path, global_git_identity: Path
) -> None:
    result = CliRunner().invoke(
        main, ["init", str(tmp_path / "treasury"), "--no-input"]
    )
    assert result.exit_code == 0, result.output
    assert "Next steps" in result.output
    assert "never a public remote" in result.output


# --- interactivity & defaults ---------------------------------------------------


def test_no_input_defaults(tmp_path: Path, global_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code == 0, result.output
    config, errors = load_config(root / "steward.yaml")
    assert errors == []
    assert config.association == "treasury"  # directory name
    assert config.currency == "EUR"
    assert config.proration == "monthly"
    assert config.closing_sign is False
    assert config.closing_remote is None
    assert config.journal_granularity == "monthly"


def test_options_override_defaults(tmp_path: Path, global_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(
        main,
        [
            "init",
            str(root),
            "--no-input",
            "--name",
            "Mein Verein",
            "--currency",
            "CHF",
            "--proration",
            "daily",
        ],
    )
    assert result.exit_code == 0, result.output
    config, errors = load_config(root / "steward.yaml")
    assert errors == []
    assert config.association == "Mein Verein"
    assert config.currency == "CHF"
    assert config.proration == "daily"


def test_interactive_prompts(
    tmp_path: Path, no_git_identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_impl, "_stdin_is_interactive", lambda: True)
    root = tmp_path / "treasury"
    result = CliRunner().invoke(
        main,
        ["init", str(root)],
        input="Mein Verein\nCHF\ndaily\nAlice\nalice@example.org\n",
    )
    assert result.exit_code == 0, result.output
    config, errors = load_config(root / "steward.yaml")
    assert errors == []
    assert (config.association, config.currency, config.proration) == (
        "Mein Verein",
        "CHF",
        "daily",
    )
    assert git(root, "log", "-1", "--format=%an <%ae>").stdout.strip() == (
        "Alice <alice@example.org>"
    )


def test_non_tty_runs_non_interactive(
    tmp_path: Path, global_git_identity: Path
) -> None:
    # CliRunner's stdin is not a TTY: no prompts even without --no-input
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root)])
    assert result.exit_code == 0, result.output
    config, _ = load_config(root / "steward.yaml")
    assert config.association == "treasury"


def test_init_current_directory(
    tmp_path: Path, global_git_identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "treasury"
    work.mkdir()
    monkeypatch.chdir(work)
    result = CliRunner().invoke(main, ["init", "--no-input"])
    assert result.exit_code == 0, result.output
    config, errors = load_config(work / "steward.yaml")
    assert errors == []
    assert config.association == "treasury"


# --- Git identity ---------------------------------------------------------------


def test_identity_from_global_config(tmp_path: Path, global_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code == 0, result.output
    assert git(root, "log", "-1", "--format=%an <%ae>").stdout.strip() == (
        "Carol Global <carol@example.org>"
    )
    local = git(root, "config", "--local", "--get", "user.name", check=False)
    assert local.returncode != 0  # nothing duplicated into repo-local config


def test_identity_prompt_writes_repo_local_config(
    tmp_path: Path, no_git_identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_impl, "_stdin_is_interactive", lambda: True)
    root = tmp_path / "treasury"
    result = CliRunner().invoke(
        main,
        ["init", str(root)],
        input="\n\n\nAlice\nalice@example.org\n",  # defaults for config prompts
    )
    assert result.exit_code == 0, result.output
    assert git(root, "config", "--local", "user.name").stdout.strip() == "Alice"
    assert (
        git(root, "config", "--local", "user.email").stdout.strip()
        == "alice@example.org"
    )


def test_identity_from_options(tmp_path: Path, no_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(
        main,
        [
            "init",
            str(root),
            "--no-input",
            "--git-name",
            "Bob",
            "--git-email",
            "bob@example.org",
        ],
    )
    assert result.exit_code == 0, result.output
    assert git(root, "log", "-1", "--format=%an <%ae>").stdout.strip() == (
        "Bob <bob@example.org>"
    )
    assert git(root, "config", "--local", "user.name").stdout.strip() == "Bob"


def test_identity_missing_fails_and_leaves_nothing(
    tmp_path: Path, no_git_identity: Path
) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code != 0
    assert "user.name" in result.output
    assert "user.email" in result.output
    assert not root.exists()


def test_single_git_option_fails(tmp_path: Path, no_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    result = CliRunner().invoke(
        main, ["init", str(root), "--no-input", "--git-name", "Bob"]
    )
    assert result.exit_code != 0
    assert "together" in result.output
    assert not root.exists()


# --- safety ---------------------------------------------------------------------


def test_non_empty_directory_fails(tmp_path: Path, global_git_identity: Path) -> None:
    root = tmp_path / "treasury"
    root.mkdir()
    keep = root / "keep.txt"
    keep.write_text("precious")
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code != 0
    assert "not empty" in result.output
    assert keep.read_text() == "precious"
    assert not (root / "steward.yaml").exists()


def test_directory_name_with_spaces_and_unicode(
    tmp_path: Path, global_git_identity: Path
) -> None:
    root = tmp_path / "Müller Verein e.V."
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code == 0, result.output
    config, errors = load_config(root / "steward.yaml")
    assert errors == []
    assert config.association == "Müller Verein e.V."


def test_missing_hledger_fails_and_leaves_nothing(
    tmp_path: Path, global_git_identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # git available, hledger not: the validation smoke test must fail
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_dir))
    root = tmp_path / "treasury"
    result = CliRunner().invoke(main, ["init", str(root), "--no-input"])
    assert result.exit_code != 0
    assert "hledger" in result.output
    assert not root.exists()


# --- template sync ----------------------------------------------------------------


def test_templates_in_sync_with_canonical_files() -> None:
    assert (TEMPLATES / "runbook.md").read_text() == (
        REPO_ROOT / "docs" / "runbook.md"
    ).read_text()
    assert (TEMPLATES / "steward.yaml").read_text() == (
        REPO_ROOT / "steward.example.yaml"
    ).read_text()


def test_skeletons_derived_from_examples() -> None:
    """Skeletons must equal the commenting transformation of the examples."""
    for name in ["members", "payer_aliases", "categorization", "bank_formats"]:
        example = (REPO_ROOT / f"{name}.example.yaml").read_text().splitlines()
        expected = [
            f"# Derived from {name}.example.yaml (Steward repository)"
            " — uncomment and edit."
        ]
        for line in example:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                expected.append(line)
            else:
                expected.append("# " + line)
        assert (TEMPLATES / f"{name}.yaml").read_text() == "\n".join(expected) + "\n"
