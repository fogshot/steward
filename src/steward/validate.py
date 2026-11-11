"""`steward validate` — run all checks, report every failure (PRD: validation.md).

No fail-fast: all file checks run and all failures are collected. The
hledger journal gate runs only when YAML is clean (a broken data model
should not block journal diagnostics, but hledger output interleaved with
YAML errors would be confusing) and only when a journals directory exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import checks
from .model import (
    ValidationError,
    load_bank_formats,
    load_categorization,
    load_config,
    load_members,
    load_payer_aliases,
)


def run_validation(root: Path) -> list[ValidationError]:
    """Run all YAML schema/referential checks and the hledger journal gate."""
    errors: list[ValidationError] = []

    config, config_errors = load_config(root / "steward.yaml")
    errors.extend(config_errors)
    members, member_errors = load_members(root / "members.yaml")
    errors.extend(member_errors)
    aliases, alias_errors = load_payer_aliases(root / "payer_aliases.yaml")
    errors.extend(alias_errors)
    categorization, cat_errors = load_categorization(root / "categorization.yaml")
    errors.extend(cat_errors)
    formats, format_errors = load_bank_formats(root / "bank_formats.yaml")
    errors.extend(format_errors)

    errors.extend(checks.all_member_checks(members))
    errors.extend(checks.check_alias_keys_reference_members(aliases, members))
    errors.extend(checks.check_aliases_unique_across_members(aliases))
    errors.extend(checks.check_alias_regexes(aliases))
    errors.extend(checks.check_categorization_regexes(categorization))
    errors.extend(checks.check_bank_formats(formats))
    errors.extend(checks.check_config_domains(config))

    journals_dir = root / config.journals
    if journals_dir.is_dir():
        errors.extend(_check_journals(journals_dir))
    return errors


def _check_journals(journals_dir: Path) -> list[ValidationError]:
    main_journal = journals_dir / "main.journal"
    target = main_journal if main_journal.exists() else journals_dir
    try:
        result = subprocess.run(
            ["hledger", "-f", str(target), "check"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return [
            ValidationError(
                "journals", "hledger check", "hledger binary not found on PATH"
            )
        ]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[0] if detail else "unknown hledger error"
        return [ValidationError("journals", "hledger check", reason)]
    return []
