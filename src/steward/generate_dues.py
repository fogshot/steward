"""`steward generate-dues` — annual dues accruals (PRD: dues-generation.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import journal
from .dues import compute_dues, is_full_year_waiver
from .model import load_config, load_members
from .validate import run_validation


@dataclass
class DuesReport:
    booked: list[tuple[str, Decimal]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    adjusted: list[tuple[str, Decimal]] = field(default_factory=list)
    waived: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        for label, entries in (
            ("booked", self.booked),
            ("adjusted", self.adjusted),
        ):
            lines.append(f"{label}: {len(entries)}")
            for name, amount in entries:
                lines.append(f"  {name}  €{amount:.2f}")
        lines.append(f"skipped: {len(self.skipped)}")
        lines.append(f"waived: {len(self.waived)}")
        for name in self.waived:
            lines.append(f"  {name}")
        return "\n".join(lines)


def generate_dues(
    root: Path, year: int, booking_date: date | None = None, dry_run: bool = False
) -> DuesReport:
    """Generate dues accruals for a calendar year. See the PRD for behavior."""
    errors = run_validation(root)
    if errors:
        raise RuntimeError(
            "validation failed; fix these errors first:\n"
            + "\n".join(f"  {error}" for error in errors)
        )

    config, _ = load_config(root / "steward.yaml")
    members, _ = load_members(root / "members.yaml")
    journals_dir = root / config.journals
    day = booking_date or date.today()

    booked: dict[tuple[str, str], Decimal] = {}
    if journals_dir.is_dir():
        booked = journal.query_member_year_amounts(journals_dir)

    report = DuesReport()
    bookings: list[journal.Booking] = []
    for member in members.members:
        active = any(
            period.joined.year <= year
            and (period.left is None or period.left.year >= year)
            for period in member.membership
        )
        if not active:
            continue  # not active during the target year
        total = compute_dues(member, members, year, config.proration)
        key = (member.id, str(year))
        if key in booked:
            difference = total - booked[key]
            if difference == 0:
                report.skipped.append(member.name)
                continue
            bookings.append(
                journal.Booking(
                    date=day,
                    description=f"Membership dues {year} adjustment — {member.name}",
                    account1="assets:receivables:dues",
                    account2="income:dues",
                    amount=difference,
                    tags={"member-id": member.id, "year": str(year)},
                )
            )
            report.adjusted.append((member.name, difference))
            continue
        bookings.append(
            journal.Booking(
                date=day,
                description=f"Membership dues {year} — {member.name}",
                account1="assets:receivables:dues",
                account2="income:dues",
                amount=total,
                tags={"member-id": member.id, "year": str(year)},
            )
        )
        if is_full_year_waiver(member, members, year, config.proration):
            report.waived.append(member.name)
        else:
            report.booked.append((member.name, total))

    journal.append_bookings(
        journals_dir, bookings, config.journal_granularity, dry_run=dry_run
    )
    if not dry_run and bookings:
        journal.check(journals_dir)
    return report
