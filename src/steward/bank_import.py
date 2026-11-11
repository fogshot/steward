"""`steward import` — bank statement import (PRD: bank-import.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import bank, journal, resolve
from .dues import compute_dues
from .model import (
    load_bank_formats,
    load_categorization,
    load_config,
    load_members,
    load_payer_aliases,
)
from .validate import run_validation

# Beyond this many flagged rows, the report summarizes by reason instead of
# listing each row — the treasurer's working queue is `hledger print tag:review`.
MAX_LISTED_FLAGGED = 20

# Review reasons that re-importing re-resolves against current rules/aliases
# (decision #14). partial/overpaid/ambiguous are human decisions.
RE_RESOLVABLE = ("unmatched", "uncategorized")


@dataclass
class ImportReport:
    parsed: int = 0
    imported: int = 0
    duplicates: int = 0
    committed_flagged: int = 0
    resolved: int = 0
    flagged: list[tuple[str, Decimal, str]] = field(default_factory=list)
    journal_files: list[Path] = field(default_factory=list)
    dry_run: bool = False
    dry_run_output: str = ""

    def render(self) -> str:
        lines = [
            f"parsed: {self.parsed}",
            f"imported: {self.imported}",
            f"duplicates: {self.duplicates}",
        ]
        if self.committed_flagged:
            lines.append(
                f"  ({self.committed_flagged} committed with open review flags"
                " — resolve manually)"
            )
        resolved_label = "would resolve" if self.dry_run else "resolved"
        lines.append(f"{resolved_label}: {self.resolved}")
        if self.journal_files:
            label = "would be written to:" if self.dry_run else "written to:"
            lines.append(label)
            for path in self.journal_files:
                lines.append(f"  {path}")
        lines.append(f"flagged for review: {len(self.flagged)}")
        if len(self.flagged) <= MAX_LISTED_FLAGGED:
            for description, amount, reason in self.flagged:
                lines.append(f"  {description}  €{amount:.2f}  review: {reason}")
        else:
            counts: dict[str, int] = {}
            for _, _, reason in self.flagged:
                counts[reason] = counts.get(reason, 0) + 1
            for reason in sorted(counts):
                lines.append(f"  {reason}: {counts[reason]}")
        if self.flagged:
            lines.append("resolve with: hledger print tag:review")
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
    members, _ = load_members(root / "members.yaml")
    members_by_id = {member.id: member for member in members.members}
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
    review_entries = (
        journal.query_review_entries(journals_dir) if journals_exist else {}
    )
    committed = journal.committed_import_hashes(root)

    report = ImportReport(parsed=len(rows), dry_run=dry_run)
    survivors: list[tuple[bank.BankRow, str]] = []
    candidates: list[tuple[bank.BankRow, str, str, Path]] = []
    for row in rows:
        fp = bank.fingerprint(row)
        if fp not in existing:
            survivors.append((row, fp))
            continue
        state = review_entries.get(fp)
        if state and state[0] in RE_RESOLVABLE and fp not in committed:
            candidates.append((row, fp, state[0], state[1]))
        else:
            report.duplicates += 1
            if state and fp in committed:
                report.committed_flagged += 1

    # Receivable balances per matched member, decremented as rows are
    # classified: a second payment to the same member in one file is
    # compared against the remainder, not the stale opening balance.
    balances: dict[str, Decimal] = {}
    annual_dues: dict[tuple[str, int], Decimal] = {}

    def balance_for(member_id: str) -> Decimal:
        if member_id not in balances:
            balances[member_id] = (
                journal.query_receivable_balance(journals_dir, member_id)
                if journals_exist
                else Decimal(0)
            )
        return balances[member_id]

    def classify_row(row: bank.BankRow) -> bank.Classification:
        member_ids = bank.match_members(row.payee, aliases) if row.amount >= 0 else []
        matched = (
            {member_ids[0]: balance_for(member_ids[0])} if len(member_ids) == 1 else {}
        )
        booking_year_dues = None
        if len(member_ids) == 1:
            member_id = member_ids[0]
            key = (member_id, row.date.year)
            if key not in annual_dues:
                annual_dues[key] = compute_dues(
                    members_by_id[member_id], members, row.date.year, config.proration
                )
            booking_year_dues = annual_dues[key]
        category = bank.categorize(row.payee, row.description, categorization)
        return bank.classify(
            row, member_ids, matched, category, booking_year_dues=booking_year_dues
        )

    bookings: list[journal.Booking] = []
    for row, fp in survivors:
        result = classify_row(row)
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
        report.imported += 1

    # Re-resolution (decision #14): re-classify uncommitted flagged duplicates
    # against current rules/aliases and rewrite entries that now resolve.
    rewrites: dict[Path, list[tuple[bank.BankRow, str, str, bank.Classification]]] = {}
    for row, fp, old_reason, path in candidates:
        result = classify_row(row)
        old_account = bank.INCOME_SUSPENSE if row.amount >= 0 else bank.EXPENSE_SUSPENSE
        new_account = result.account2 if row.amount >= 0 else result.account1
        if (
            new_account == old_account
            and result.member_id is None
            and result.review == old_reason
        ):
            report.duplicates += 1
            report.flagged.append((row.booking_description, row.amount, old_reason))
            continue
        rewrites.setdefault(path, []).append((row, fp, old_reason, result))

    rewrites_applied = 0
    for path, entries in rewrites.items():
        content = path.read_text(encoding="utf-8")
        applied: list[tuple[bank.BankRow, bank.Classification]] = []
        for row, fp, old_reason, result in entries:
            new_content = resolve.rewrite_entry(
                content,
                fp,
                new_account=result.account2 if row.amount >= 0 else result.account1,
                amount=abs(row.amount),
                member_id=result.member_id,
                review=result.review,
            )
            if new_content is None:
                # unexpected block shape (e.g. hand-edited): leave untouched
                report.flagged.append((row.booking_description, row.amount, old_reason))
                continue
            content = new_content
            applied.append((row, result))
        if applied:
            rewrites_applied += len(applied)
            if not dry_run:
                path.write_text(content, encoding="utf-8")
            for row, result in applied:
                if result.member_id is not None:
                    balances[result.member_id] -= row.amount
                if result.review is None:
                    report.resolved += 1
                else:
                    report.flagged.append(
                        (row.booking_description, row.amount, result.review)
                    )

    report.journal_files = sorted(
        {
            journal.period_file(journals_dir, config.journal_granularity, b.date)
            for b in bookings
        }
    )
    output = journal.append_bookings(
        journals_dir, bookings, config.journal_granularity, dry_run=dry_run
    )
    if dry_run:
        report.dry_run_output = output
    elif bookings or rewrites_applied:
        journal.check(journals_dir)
    return report
