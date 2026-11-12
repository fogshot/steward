"""Steward command-line interface.

Command specifications live in .ai/requirements/features/ (one PRD per command).
"""

from datetime import date, datetime
from pathlib import Path

import click

from .model import PRORATION_VALUES


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
@click.argument("bank_export", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format", "format_name", required=True, help="Mapping from bank_formats.yaml."
)
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Data repository root.",
)
@click.option("--dry-run", is_flag=True, help="Print transactions, write nothing.")
def import_(bank_export: Path, format_name: str, root: Path, dry_run: bool) -> None:
    """Import a bank statement export (PRD: bank-import.md)."""
    from .bank_import import import_bank_export as run

    try:
        report = run(root, bank_export, format_name, dry_run=dry_run)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(report.render())


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
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Data repository root.",
)
@click.option("--dry-run", is_flag=True, help="Show changes without committing.")
def commit(force: bool, root: Path, dry_run: bool) -> None:
    """Validate, check review tags, and git-commit (PRD: commit-and-close.md)."""
    from .commit_impl import commit_repository

    try:
        report = commit_repository(root, force=force, dry_run=dry_run)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(report)


@main.command()
@click.option("--year", type=int, required=True, help="Fiscal year to close.")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Data repository root.",
)
@click.option("--dry-run", is_flag=True, help="Preview without writing journals.")
def close(year: int, root: Path, dry_run: bool) -> None:
    """Run the annual accounting close via hledger (PRD: commit-and-close.md)."""
    from .close_impl import close_year

    try:
        report = close_year(root, year, dry_run=dry_run)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(report)


@main.command(name="init")
@click.argument("directory", type=click.Path(path_type=Path), required=False)
@click.option(
    "--no-input", is_flag=True, help="Do not prompt; use options and defaults."
)
@click.option("--name", "association_name", default=None, help="Association name.")
@click.option("--currency", default=None, help="Currency code (default: EUR).")
@click.option(
    "--proration",
    type=click.Choice(PRORATION_VALUES),
    default=None,
    help="Dues proration policy (default: monthly).",
)
@click.option(
    "--git-name", default=None, help="Git author name for the initial commit."
)
@click.option(
    "--git-email", default=None, help="Git author email for the initial commit."
)
def init(
    directory: Path | None,
    no_input: bool,
    association_name: str | None,
    currency: str | None,
    proration: str | None,
    git_name: str | None,
    git_email: str | None,
) -> None:
    """Scaffold a new private treasury data repository (PRD: init.md)."""
    from .init_impl import init_repository as run

    try:
        report = run(
            directory if directory is not None else Path("."),
            no_input=no_input,
            association_name=association_name,
            currency=currency,
            proration=proration,
            git_name=git_name,
            git_email=git_email,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(report.render())
