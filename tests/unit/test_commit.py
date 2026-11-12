"""Integration tests for the treasury commit command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from steward.cli import main
from steward.init_impl import init_repository


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for key in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "treasury"
    init_repository(root, no_input=True, git_name="Test", git_email="test@example.org")
    git(root, "config", "core.hooksPath", "/dev/null")
    return root


def test_commit_stages_all_changes(repo: Path) -> None:
    (repo / "extra.txt").write_text("new\n")
    result = CliRunner().invoke(main, ["commit", "--root", str(repo)])
    assert result.exit_code == 0, result.output
    assert git(repo, "show", "HEAD:extra.txt") == "new"
    assert git(repo, "status", "--porcelain") == ""


def test_open_review_blocks_commit_until_forced(repo: Path) -> None:
    path = repo / "journals" / "2026" / "02.journal"
    path.parent.mkdir()
    path.write_text(
        "2026-02-01 Unknown  ; review: unmatched\n"
        "    assets:bank:checking    €10\n"
        "    income:unknown         €-10\n"
    )
    before = git(repo, "rev-parse", "HEAD")
    result = CliRunner().invoke(main, ["commit", "--root", str(repo)])
    assert result.exit_code != 0
    assert "review: unmatched" in result.output
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""
    result = CliRunner().invoke(main, ["commit", "--root", str(repo), "--force"])
    assert result.exit_code == 0, result.output
    assert "review: unmatched" in git(repo, "show", "HEAD:journals/2026/02.journal")


def test_validation_failure_leaves_git_untouched(repo: Path) -> None:
    (repo / "steward.yaml").write_text("unknown_key: true\n")
    before = git(repo, "rev-parse", "HEAD")
    result = CliRunner().invoke(main, ["commit", "--root", str(repo), "--force"])
    assert result.exit_code != 0
    assert "validation failed" in result.output
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize(
    "old,new", [("sign: false", 'sign: "true"'), ("remote: null", "remote: 123")]
)
def test_invalid_closing_configuration_cannot_silently_disable_safety(
    repo: Path, old: str, new: str
) -> None:
    config = repo / "steward.yaml"
    config.write_text(config.read_text().replace(old, new))
    before = git(repo, "rev-parse", "HEAD")
    result = CliRunner().invoke(main, ["commit", "--root", str(repo)])
    assert result.exit_code != 0
    assert "validation failed" in result.output
    assert git(repo, "rev-parse", "HEAD") == before


def test_dry_run_does_not_stage_or_commit(repo: Path) -> None:
    (repo / "extra.txt").write_text("new\n")
    before = git(repo, "rev-parse", "HEAD")
    result = CliRunner().invoke(main, ["commit", "--root", str(repo), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "extra.txt" in result.output
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""


def test_pushes_to_configured_remote(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    config = repo / "steward.yaml"
    config.write_text(config.read_text().replace("remote: null", 'remote: "origin"'))
    result = CliRunner().invoke(main, ["commit", "--root", str(repo)])
    assert result.exit_code == 0, result.output
    branch = git(repo, "symbolic-ref", "--short", "HEAD")
    remote_head = subprocess.run(
        ["git", f"--git-dir={remote}", "rev-parse", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert remote_head == git(repo, "rev-parse", "HEAD")


def test_push_failure_reports_existing_commit(repo: Path) -> None:
    config = repo / "steward.yaml"
    config.write_text(config.read_text().replace("remote: null", 'remote: "missing"'))
    before = git(repo, "rev-parse", "HEAD")
    result = CliRunner().invoke(main, ["commit", "--root", str(repo)])
    assert result.exit_code != 0
    assert "commit succeeded, but push failed" in result.output
    assert git(repo, "rev-parse", "HEAD") != before


def test_signing_failure_prevents_commit(repo: Path) -> None:
    config = repo / "steward.yaml"
    config.write_text(config.read_text().replace("sign: false", "sign: true"))
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "gpg.program", "/usr/bin/false")
    before = git(repo, "rev-parse", "HEAD")
    result = CliRunner().invoke(main, ["commit", "--root", str(repo)])
    assert result.exit_code != 0
    assert "git commit" in result.output
    assert git(repo, "rev-parse", "HEAD") == before
