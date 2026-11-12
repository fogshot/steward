"""Commit validated treasury changes to Git."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from .model import load_config
from .validate import run_validation


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{command[0]} not found on PATH") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{command[0]} {' '.join(command[1:])} failed: {detail}")
    return result


def commit_repository(root: Path, *, force: bool = False, dry_run: bool = False) -> str:
    errors = run_validation(root)
    if errors:
        raise RuntimeError(
            "validation failed; fix these errors first:\n"
            + "\n".join(f"  {error}" for error in errors)
        )
    config, config_errors = load_config(root / "steward.yaml")
    if config_errors:
        raise RuntimeError("\n".join(str(error) for error in config_errors))
    main = root / config.journals / "main.journal"
    if not main.is_file():
        raise RuntimeError(f"journal not found: {main}")
    review = _run(
        ["hledger", "-f", str(main.resolve()), "print", "tag:review"], cwd=root
    ).stdout.strip()
    if review and not force:
        raise RuntimeError(
            "unresolved review entries; resolve them or use --force:\n" + review
        )

    top = _run(["git", "rev-parse", "--show-toplevel"], cwd=root).stdout.strip()
    if Path(top).resolve() != root.resolve():
        raise RuntimeError("data repository root must be the Git repository root")
    status = _run(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root
    ).stdout
    if not status:
        raise RuntimeError("no changes to commit")
    if dry_run:
        return "Would commit these changes:\n" + status.rstrip()

    _run(["git", "add", "-A"], cwd=root)
    command = ["git", "commit"]
    if config.closing_sign:
        command.append("-S")
    command.extend(["-m", f"Record treasury changes {date.today().isoformat()}"])
    result = _run(command, cwd=root)
    if config.closing_remote:
        try:
            _run(["git", "push", "--", config.closing_remote, "HEAD"], cwd=root)
        except RuntimeError as exc:
            raise RuntimeError(f"commit succeeded, but push failed: {exc}") from exc
    return result.stdout.strip()
