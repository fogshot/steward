"""Journal interaction: hledger queries and the hledger-import write path.

Steward never writes journal files directly (architecture.md §6): every
booking is rendered and appended by `hledger import`, fed by an enriched
CSV temp file and the static rules/steward.rules file.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

RULES_CONTENT = """\
skip 1
fields date, description, account1, account2, amount, comment
date-format %Y-%m-%d
decimal-mark .
currency €
"""

MAIN_JOURNAL_CONTENT = "include **.journal\n"

CSV_HEADER = ["date", "description", "account1", "account2", "amount", "comment"]


@dataclass
class Booking:
    """One two-posting transaction to be rendered by hledger import."""

    date: date
    description: str
    account1: str  # receives +amount
    account2: str  # receives -amount
    amount: Decimal
    tags: dict[str, str]


def _run_hledger(
    journals_dir: Path, args: list[str]
) -> subprocess.CompletedProcess[str]:
    main = journals_dir / "main.journal"
    if not main.exists():
        # hledger -f needs a file; an empty main.journal includes everything
        # below via its glob and doubles as the stable entry point.
        journals_dir.mkdir(parents=True, exist_ok=True)
        main.write_text(MAIN_JOURNAL_CONTENT)
    return subprocess.run(
        ["hledger", "-f", str(main), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _tag_values(journals_dir: Path, tag: str) -> set[str]:
    result = _run_hledger(journals_dir, ["tags", "--values", tag])
    if result.returncode != 0:
        raise RuntimeError(f"hledger query failed: {result.stderr.strip()}")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _receivables_sum(journals_dir: Path, *queries: str) -> Decimal:
    """Sum of assets:receivables:dues postings for transactions matching queries."""
    result = _run_hledger(
        journals_dir,
        ["print", *queries, "acct:assets:receivables:dues", "-O", "json"],
    )
    if result.returncode != 0:
        raise RuntimeError(f"hledger query failed: {result.stderr.strip()}")
    total = Decimal(0)
    for txn in json.loads(result.stdout or "[]"):
        for posting in txn.get("tpostings", []):
            if posting.get("paccount") != "assets:receivables:dues":
                continue
            for amount in posting.get("pamount", []):
                quantity = amount.get("aquantity", {})
                total += Decimal(str(quantity.get("floatingPoint", "0")))
    return total


def query_member_year_amounts(journals_dir: Path) -> dict[tuple[str, str], Decimal]:
    """Booked amounts per (member-id, year) tag pair across all journals.

    Sums the assets:receivables:dues postings of transactions carrying both
    tags, so adjustments are included in the total.
    """
    member_ids = _tag_values(journals_dir, "member-id")
    years = _tag_values(journals_dir, "year")
    amounts: dict[tuple[str, str], Decimal] = {}
    for member_id in member_ids:
        for year in years:
            total = _receivables_sum(
                journals_dir, f"tag:member-id={member_id}", f"tag:year={year}"
            )
            if total != 0 or _has_transactions(journals_dir, member_id, year):
                amounts[(member_id, year)] = total
    return amounts


def _has_transactions(journals_dir: Path, member_id: str, year: str) -> bool:
    """True when transactions carry both tags (even with a zero balance)."""
    result = _run_hledger(
        journals_dir,
        ["print", f"tag:member-id={member_id}", f"tag:year={year}", "-O", "json"],
    )
    if result.returncode != 0:
        raise RuntimeError(f"hledger query failed: {result.stderr.strip()}")
    return bool(json.loads(result.stdout or "[]"))


def query_import_hashes(journals_dir: Path) -> set[str]:
    """All import-hash tag values across the journals (dedup database)."""
    return _tag_values(journals_dir, "import-hash")


def query_review_entries(journals_dir: Path) -> dict[str, tuple[str, Path]]:
    """Flagged entries: import-hash → (review reason, journal file)."""
    result = _run_hledger(journals_dir, ["print", "tag:review", "-O", "json"])
    if result.returncode != 0:
        raise RuntimeError(f"hledger query failed: {result.stderr.strip()}")
    entries: dict[str, tuple[str, Path]] = {}
    for txn in json.loads(result.stdout or "[]"):
        tags = {name: value for name, value in txn.get("ttags", [])}
        import_hash, review = tags.get("import-hash"), tags.get("review")
        positions = txn.get("tsourcepos", [])
        if import_hash and review and positions:
            source = positions[0].get("sourceName", "")
            entries[import_hash] = (review, Path(source))
    return entries


def committed_import_hashes(root: Path) -> set[str]:
    """Import hashes present in git HEAD — those entries are immutable.

    An empty set when the repository has no commits yet (or is not a git
    repository at all): nothing is committed, everything is working state.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "grep", "-h", "import-hash:", "HEAD", "--", "."],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return set()
    if result.returncode not in (0, 1):  # 1 = no matches
        return set()
    return set(re.findall(r"import-hash:\s*([0-9a-f]{64})", result.stdout))


def query_receivable_balance(journals_dir: Path, member_id: str) -> Decimal:
    """Open dues balance for one member (postings tagged member-id)."""
    return _receivables_sum(journals_dir, f"tag:member-id={member_id}")


def period_file(journals_dir: Path, granularity: str, day: date) -> Path:
    if granularity == "yearly":
        return journals_dir / f"{day.year}.journal"
    return journals_dir / f"{day.year:04d}" / f"{day.month:02d}.journal"


def render_csv(bookings: list[Booking], path: Path) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for booking in bookings:
            comment = ", ".join(
                f"{key}: {value}" for key, value in booking.tags.items()
            )
            writer.writerow(
                [
                    booking.date.isoformat(),
                    booking.description,
                    booking.account1,
                    booking.account2,
                    f"{booking.amount:.2f}",
                    comment,
                ]
            )


def append_bookings(
    journals_dir: Path,
    bookings: list[Booking],
    granularity: str,
    dry_run: bool = False,
) -> str:
    """Render bookings via hledger import, grouped by posting period.

    Returns hledger's stdout (the rendered transactions with --dry-run).
    """
    if not bookings:
        return ""
    rules = journals_dir.parent / "rules" / "steward.rules"
    rules.parent.mkdir(parents=True, exist_ok=True)
    if not rules.exists():
        rules.write_text(RULES_CONTENT)

    by_period: dict[Path, list[Booking]] = {}
    for booking in bookings:
        target = period_file(journals_dir, granularity, booking.date)
        by_period.setdefault(target, []).append(booking)

    output: list[str] = []
    for journal_file, period_bookings in sorted(by_period.items()):
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        journal_file.touch(exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            render_csv(period_bookings, tmp_path)
            command = [
                "hledger",
                "import",
                "-f",
                str(journal_file),
                "--rules",
                str(rules),
                str(tmp_path),
            ]
            if dry_run:
                command.append("--dry-run")
            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                raise RuntimeError(f"hledger import failed: {result.stderr.strip()}")
            output.append(result.stdout)
        finally:
            tmp_path.unlink(missing_ok=True)
    return "".join(output)


def check(journals_dir: Path) -> None:
    """The write-path gate: hledger check must pass after a real import."""
    result = _run_hledger(journals_dir, ["check"])
    if result.returncode != 0:
        raise RuntimeError(f"hledger check failed: {result.stderr.strip()}")
