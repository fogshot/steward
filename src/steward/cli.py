"""Steward command-line interface.

Command specifications live in .ai/requirements/features/ (one PRD per command).
"""

import click


@click.group()
@click.version_option()
def main() -> None:
    """Steward — association treasury management with hledger."""


@main.command()
@click.option("--year", type=int, required=True, help="Fiscal year to generate for.")
@click.option("--dry-run", is_flag=True, help="Print transactions, write nothing.")
def generate_dues(year: int, dry_run: bool) -> None:
    """Generate annual membership-dues accruals (PRD: dues-generation.md)."""
    raise click.ClickException("not implemented")


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
def validate() -> None:
    """Validate YAML schemas and journals (PRD: validation.md)."""
    raise click.ClickException("not implemented")


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
