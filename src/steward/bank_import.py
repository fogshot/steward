"""`steward import` — bank statement import (PRD: bank-import.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import bank, journal
from .model import (
    load_bank_formats,
    load_categorization,
    load_config,
    load_payer_aliases,
)
from .validate import run_validation


@dataclass
class ImportReport:
    imported: list[tuple[str, Decimal]] = field(default_factory=list)
    duplicates: list[tuple[str, Decimal]] = field(default_factory=list)
    flagged: list[tuple[str, Decimal, str]] = field(default_factory=list)
    dry_run_output: str = ""

    def render(self) -> str:
        lines = [f"imported: {len(self.imported)}"]
        for description, amount in self.imported:
            lines.append(f"  {description}  €{amount:.2f}")
        lines.append(f"duplicates: {len(self.duplicates)}")
        for description, amount in self.duplicates:
            lines.append(f"  {description}  €{amount:.2f}")
        lines.append(f"flagged: {len(self.flagged)}")
        for description, amount, reason in self.flagged:
            lines.append(f"  {description}  €{amount:.2f}  review: {reason}")
        if self.dry_run_output:
            lines.append("")
            lines.append(self.dry_run_output.rstrip())
        return "\n".join(lines)


def import_bank_export(
    root: Path, export: Path, format_name: str, dry_run: bool = False
) -> ImportReport:
    """Import a bank statement export. See the PRD for behavior."""
    errors = run_validation(root)
    if errors:
        raise RuntimeError(
            "validation failed; fix these errors first:\n"
            + "\n".join(f"  {error}" for error in errors)
        )

    config, _ = load_config(root / "steward.yaml")
    aliases, _ = load_payer_aliases(root / "payer_aliases.yaml")
    categorization, _ = load_categorization(root / "categorization.yaml")
    formats, _ = load_bank_formats(root / "bank_formats.yaml")
    fmt = formats.formats.get(format_name)
    if fmt is None:
        known = ", ".join(sorted(formats.formats)) or "(none)"
        raise RuntimeError(f"unknown format {format_name!r}; known: {known}")

    rows = bank.parse_export(export, fmt)
    journals_dir = root / config.journals
    journals_exist = journals_dir.is_dir()
    existing = journal.query_import_hashes(journals_dir) if journals_exist else set()

    report = ImportReport()
    survivors: list[tuple[bank.BankRow, str]] = []
    for row in rows:
        fp = bank.fingerprint(row)
        if fp in existing:
            report.duplicates.append((row.booking_description, row.amount))
        else:
            survivors.append((row, fp))

    # Receivable balances per matched member, decremented as rows are
    # classified: a second payment to the same member in one file is
    # compared against the remainder, not the stale opening balance.
    balances: dict[str, Decimal] = {}

    def balance_for(member_id: str) -> Decimal:
        if member_id not in balances:
            balances[member_id] = (
                journal.query_receivable_balance(journals_dir, member_id)
                if journals_exist
                else Decimal(0)
            )
        return balances[member_id]

    bookings: list[journal.Booking] = []
    for row, fp in survivors:
        member_ids = bank.match_members(row.payee, aliases) if row.amount >= 0 else []
        matched = (
            {member_ids[0]: balance_for(member_ids[0])} if len(member_ids) == 1 else {}
        )
        category = bank.categorize(row.payee, row.description, categorization)
        result = bank.classify(row, member_ids, matched, category)
        tags = {"import-hash": fp}
        if result.member_id is not None:
            tags["member-id"] = result.member_id
            balances[result.member_id] -= row.amount
        if result.review is not None:
            tags["review"] = result.review
            report.flagged.append((row.booking_description, row.amount, result.review))
        bookings.append(
            journal.Booking(
                date=row.date,
                description=row.booking_description,
                account1=result.account1,
                account2=result.account2,
                amount=result.amount,
                tags=tags,
            )
        )
        report.imported.append((row.booking_description, row.amount))

    output = journal.append_bookings(
        journals_dir, bookings, config.journal_granularity, dry_run=dry_run
    )
    if dry_run:
        report.dry_run_output = output
    elif bookings:
        journal.check(journals_dir)
    return report
