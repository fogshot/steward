"""`steward init` — assisted treasury setup (PRD: init.md).

Scaffolds a new private treasury data repository: directory layout from
architecture.md §2, config from the interactive answers / CLI options,
Git repository with an initial commit, and a validation smoke test.
Scaffold file contents come from src/steward/templates/ (bundled with the
package — an installed Steward has no repository checkout to copy from).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import click

from .journal import MAIN_JOURNAL_CONTENT, RULES_CONTENT
from .model import PRORATION_VALUES
from .validate import run_validation

SKELETON_FILES = (
    "members.yaml",
    "payer_aliases.yaml",
    "categorization.yaml",
    "bank_formats.yaml",
)

GITIGNORE_CONTENT = ".DS_Store\nThumbs.db\n*.csv\n"

INITIAL_COMMIT_MESSAGE = "Initial treasury scaffold"

NEXT_STEPS = """\
Next steps:
  1. Fill in members.yaml — fee tiers and members.
  2. Map your bank's CSV export in bank_formats.yaml.
  3. Run your first import: steward import <export.csv> --format <mapping>.

Privacy: this repository will contain personal data (member names, bank
transactions). Keep it private — private hosting only, never a public remote."""


@dataclass
class InitReport:
    root: Path
    association: str

    def render(self) -> str:
        return (
            f'Initialized treasury for "{self.association}" in {self.root.resolve()}\n'
            "Validation: passed\n"
            f"Initial commit created.\n\n{NEXT_STEPS}"
        )


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _template_text(name: str) -> str:
    return (files("steward") / "templates" / name).read_text(encoding="utf-8")


def _yaml_string(value: str) -> str:
    """A double-quoted scalar; JSON string syntax is valid YAML."""
    return json.dumps(value, ensure_ascii=False)


def _substitute(
    template: str, pattern: str, replacement: Callable[[re.Match[str]], str]
) -> str:
    result, count = re.subn(pattern, replacement, template, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(
            "templates/steward.yaml does not match the expected shape; "
            "template and substitution patterns have drifted"
        )
    return result


def _render_config(name: str, currency: str, proration: str) -> str:
    """steward.yaml from the bundled template with the answers substituted."""
    template = _template_text("steward.yaml")
    template = _substitute(
        template, r"^association:.*$", lambda m: f"association: {_yaml_string(name)}"
    )
    template = _substitute(
        template, r"^currency:.*$", lambda m: f"currency: {_yaml_string(currency)}"
    )
    return _substitute(
        template,
        r'^(\s*proration:\s*)"[^"]*"',
        lambda m: m.group(1) + _yaml_string(proration),
    )


def _write(target: Path, relative: str, content: str) -> None:
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_scaffold(target: Path, name: str, currency: str, proration: str) -> None:
    _write(target, "steward.yaml", _render_config(name, currency, proration))
    for filename in SKELETON_FILES:
        _write(target, filename, _template_text(filename))
    _write(target, "rules/steward.rules", RULES_CONTENT)
    _write(target, "journals/main.journal", MAIN_JOURNAL_CONTENT)
    _write(target, "docs/runbook.md", _template_text("runbook.md"))
    _write(target, ".gitignore", GITIGNORE_CONTENT)


def _run_git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("git binary not found on PATH") from None
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _git_config_get(root: Path, key: str) -> str | None:
    result = _run_git(root, "config", "--get", key, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _resolve_identity(
    root: Path, git_name: str | None, git_email: str | None, interactive: bool
) -> tuple[str, str, bool]:
    """Git author identity: options → configured user.name/user.email → prompt.

    Returns (name, email, from_config). Identity from options or prompts is
    written to the new repository's local config by the caller.
    """
    if git_name and git_email:
        return git_name, git_email, False
    name = _git_config_get(root, "user.name")
    email = _git_config_get(root, "user.email")
    if name and email:
        return name, email, True
    if not interactive:
        raise RuntimeError(
            "Git author identity not configured: set user.name and user.email "
            "(git config --global) or pass --git-name/--git-email"
        )
    if not name:
        name = click.prompt("Git author name")
    if not email:
        email = click.prompt("Git author email")
    return name, email, False


def _missing_ancestors(target: Path) -> list[Path]:
    """Directories mkdir(parents=True) would create, target first."""
    missing: list[Path] = []
    current = target
    while not current.exists():
        missing.append(current)
        current = current.parent
    return missing


_SCAFFOLD_ENTRIES = (
    ".git",
    ".gitignore",
    "steward.yaml",
    *SKELETON_FILES,
    "rules",
    "journals",
    "docs",
)


def _cleanup(target: Path, missing_ancestors: list[Path]) -> None:
    """Remove everything the failed run created (PRD: no half-scaffold)."""
    for name in _SCAFFOLD_ENTRIES:
        path = target / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    for directory in missing_ancestors:
        try:
            directory.rmdir()
        except OSError:
            break  # not empty — something else lives here


def init_repository(
    directory: Path,
    *,
    no_input: bool = False,
    association_name: str | None = None,
    currency: str | None = None,
    proration: str | None = None,
    git_name: str | None = None,
    git_email: str | None = None,
) -> InitReport:
    """Scaffold a new treasury data repository. See the PRD for behavior."""
    if bool(git_name) != bool(git_email):
        raise RuntimeError("--git-name and --git-email must be given together")
    if proration is not None and proration not in PRORATION_VALUES:
        raise RuntimeError(
            f"proration must be one of {PRORATION_VALUES}, got {proration!r}"
        )
    target = directory
    if target.exists():
        if not target.is_dir():
            raise RuntimeError(f"{target}: exists and is not a directory")
        if any(target.iterdir()):
            raise RuntimeError(f"{target}: directory is not empty")

    interactive = not no_input and _stdin_is_interactive()
    default_name = target.resolve().name or "treasury"
    if interactive:
        if association_name is None:
            association_name = click.prompt("Association name", default=default_name)
        if currency is None:
            currency = click.prompt("Currency", default="EUR")
        if proration is None:
            proration = click.prompt(
                "Proration policy",
                type=click.Choice(PRORATION_VALUES),
                default="monthly",
            )
    name = association_name or default_name
    currency = currency or "EUR"
    proration = proration or "monthly"

    missing = _missing_ancestors(target)
    target.mkdir(parents=True, exist_ok=True)
    try:
        _write_scaffold(target, name, currency, proration)
        _run_git(target, "init")
        author_name, author_email, from_config = _resolve_identity(
            target, git_name, git_email, interactive
        )
        if not from_config:
            _run_git(target, "config", "user.name", author_name)
            _run_git(target, "config", "user.email", author_email)
        _run_git(target, "add", "-A")
        _run_git(target, "commit", "-m", INITIAL_COMMIT_MESSAGE)
        errors = run_validation(target)
        if errors:
            raise RuntimeError(
                "validation of the new repository failed:\n"
                + "\n".join(f"  {error}" for error in errors)
            )
    except Exception:
        _cleanup(target, missing)
        raise
    return InitReport(root=target, association=name)
