"""Steward command-line interface.

Command specifications live in .ai/requirements/features/ (one PRD per command).
"""

from datetime import date, datetime
from pathlib import Path

import click


@click.group()
@click.version_option()
def main() -> None:
    """Steward — association treasury management with hledger."""


@main.command()
@click.option("--year", type=int, required=True, help="Calendar year to generate for.")
@click.option(
    "--date",
    "booking_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Booking date (default: today).",
)
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Data repository root.",
)
@click.option("--dry-run", is_flag=True, help="Print transactions, write nothing.")
def generate_dues(
    year: int, booking_date: date | None, root: Path, dry_run: bool
) -> None:
    """Generate annual membership-dues accruals (PRD: dues-generation.md)."""
    from .generate_dues import generate_dues as run

    if isinstance(booking_date, datetime):
        booking_date = booking_date.date()
    try:
        report = run(root, year, booking_date=booking_date, dry_run=dry_run)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(report.render())


@main.command(name="import")
@click.argument("bank_export", type=click.Path(exists=True))
@click.option(
    "--format", "format_name", required=True, help="Mapping from bank_formats.yaml."
)
@click.option("--dry-run", is_flag=True, help="Print transactions, write nothing.")
def import_(bank_export: str, format_name: str, dry_run: bool) -> None:
    """Import a bank statement export (PRD: bank-import.md)."""
    raise click.ClickException("not implemented")


@main.command()
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Data repository root.",
)
def validate(root: Path) -> None:
    """Validate YAML schemas and journals (PRD: validation.md)."""
    from .validate import run_validation

    errors = run_validation(root)
    for error in errors:
        click.echo(f"error: {error}", err=True)
    if errors:
        raise click.ClickException(f"validation failed with {len(errors)} error(s)")
    click.echo("validation passed")


@main.command()
@click.option("--force", is_flag=True, help="Commit despite unresolved review tags.")
def commit(force: bool) -> None:
    """Validate, check review tags, and git-commit (PRD: commit-and-close.md)."""
    raise click.ClickException("not implemented")


@main.command()
@click.option("--year", type=int, required=True, help="Fiscal year to close.")
def close(year: int) -> None:
    """Run the annual accounting close via hledger (PRD: commit-and-close.md)."""
    raise click.ClickException("not implemented")


@main.command(name="init")
@click.argument("directory", type=click.Path(), required=False)
def init(directory: str | None) -> None:
    """Scaffold a new private treasury data repository (PRD: init.md)."""
    raise click.ClickException("not implemented")
