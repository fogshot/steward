"""Bank export parsing, fingerprinting, and row classification (PRD: bank-import.md).

Pure functions over the dataclasses from model.py — no journal I/O, no
hledger. Parser adapters handle file-level mechanics (delimiter, encoding,
header/footer lines); the declarative format mapping (bank_formats.yaml,
architecture.md §3.5) describes how columns map onto the internal model.
"""

from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from .model import BankFormat, CategorizationFile, PayerAliasesFile

CENT = Decimal("0.01")

BANK_ACCOUNT = "assets:bank:checking"
RECEIVABLES_ACCOUNT = "assets:receivables:dues"
INCOME_SUSPENSE = "income:unknown"
EXPENSE_SUSPENSE = "expenses:unknown"


@dataclass
class BankRow:
    """Internal transaction model every format mapping must produce (§3.5)."""

    date: date  # booking date, never the value date (decision #8)
    amount: Decimal  # signed: positive = incoming
    currency: str
    payee: str
    description: str
    reference: str
    account: str

    @property
    def booking_description(self) -> str:
        """Journal description: `payee | description` (§5.2), skipping empty parts."""
        return " | ".join(part for part in (self.payee, self.description) if part)


# --- parsing -----------------------------------------------------------------


def parse_export(path: Path, fmt: BankFormat) -> list[BankRow]:
    """Parse a bank export through a format mapping into sorted internal rows.

    Parsers must not assume row order (decision #9): exports may be
    newest-first. Rows are sorted by date (fingerprint as tiebreak) so
    journals read chronologically and re-exports render identically.
    """
    if fmt.parser != "csv":
        raise RuntimeError(
            f"bank format {fmt.name!r}: unknown parser adapter {fmt.parser!r}"
        )
    records = _read_csv_records(path, fmt)
    _check_mapped_columns(records, fmt, path.name)
    rows = [
        _row_from_record(record, fmt, path.name, row_number)
        for row_number, record in enumerate(records, start=1)
    ]
    return sorted(rows, key=lambda row: (row.date, fingerprint(row)))


def _read_csv_records(path: Path, fmt: BankFormat) -> list[dict[str, str]]:
    """File-level mechanics: encoding, skipped header/footer lines, delimiter."""
    options = fmt.parser_options
    delimiter = str(options.get("delimiter", ","))
    encoding = str(options.get("encoding", "utf-8"))
    skip_header = _option_int(options, "skip_header", fmt.name)
    skip_footer = _option_int(options, "skip_footer", fmt.name)
    try:
        with path.open(encoding=encoding, newline="") as handle:
            for _ in range(skip_header):
                next(handle, None)
            rows = list(csv.reader(handle, delimiter=delimiter))
    except (csv.Error, TypeError, ValueError, LookupError) as exc:
        raise RuntimeError(
            f"{path.name}: cannot parse as csv (format {fmt.name!r}): {exc}"
        ) from exc
    if skip_footer:
        rows = rows[:-skip_footer]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row, strict=False)) for row in rows[1:]]


def _option_int(options: Mapping[str, object], key: str, fmt_name: str) -> int:
    value = options.get(key, 0)
    try:
        return int(str(value))
    except ValueError:
        raise RuntimeError(
            f"bank format {fmt_name!r}: parser_options.{key} must be an integer, "
            f"got {value!r}"
        ) from None


def _mapped_column_names(columns: Mapping[str, object]) -> list[str]:
    names: list[str] = []
    for spec in columns.values():
        if isinstance(spec, str):
            names.append(spec)
        elif isinstance(spec, list):
            names.extend(str(item) for item in spec)
        elif isinstance(spec, dict):  # amount: {debit, credit}
            names.extend(str(item) for item in spec.values())
    return names


def _check_mapped_columns(
    records: list[dict[str, str]], fmt: BankFormat, file: str
) -> None:
    if not records:
        return
    available = set(records[0])
    missing = [
        name for name in _mapped_column_names(fmt.columns) if name not in available
    ]
    if missing:
        raise RuntimeError(
            f"{file}: format {fmt.name!r} maps columns not present in the export: "
            f"{', '.join(missing)} (have: {', '.join(sorted(available))})"
        )


def _row_from_record(
    record: dict[str, str], fmt: BankFormat, file: str, row_number: int
) -> BankRow:
    columns = fmt.columns
    date_text = _column_text(record, columns.get("date"), "date", fmt.name)
    return BankRow(
        date=_parse_date(date_text, fmt, file, row_number),
        amount=_amount_from_record(record, fmt, file, row_number),
        currency=_column_text(record, columns.get("currency"), "currency", fmt.name),
        payee=_column_text(record, columns.get("payee"), "payee", fmt.name),
        description=_column_text(
            record, columns.get("description"), "description", fmt.name
        ),
        reference=_reference_text(record, fmt),
        account=_column_text(record, columns.get("account"), "account", fmt.name),
    )


def _column_text(
    record: dict[str, str], spec: object, field: str, fmt_name: str
) -> str:
    """First non-empty value among the mapped columns (§3.5 multi-column fields)."""
    if spec is None:
        return ""
    names = [spec] if isinstance(spec, str) else spec
    if not isinstance(names, list):
        raise RuntimeError(
            f"bank format {fmt_name!r}: columns.{field} must be a column name "
            f"or a list of column names, got {spec!r}"
        )
    for name in names:
        value = record.get(str(name), "").strip()
        if value:
            return value
    return ""


def _parse_date(text: str, fmt: BankFormat, file: str, row_number: int) -> date:
    date_format = fmt.date_format or "%Y-%m-%d"
    try:
        return datetime.strptime(text, date_format).date()
    except ValueError:
        raise RuntimeError(
            f"{file}: row {row_number}: date {text!r} does not match "
            f"date_format {date_format!r}"
        ) from None


def _parse_number(text: str, fmt: BankFormat, file: str, row_number: int) -> Decimal:
    decimal_mark = fmt.decimal_mark or "."
    thousands = fmt.thousands_separator
    if thousands is None:
        thousands = "." if decimal_mark == "," else ","
    cleaned = text.strip().replace(thousands, "")
    if decimal_mark != ".":
        cleaned = cleaned.replace(decimal_mark, ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise RuntimeError(
            f"{file}: row {row_number}: invalid amount {text!r}"
        ) from None


def _amount_from_record(
    record: dict[str, str], fmt: BankFormat, file: str, row_number: int
) -> Decimal:
    spec = fmt.columns.get("amount")
    if isinstance(spec, dict):
        debit = record.get(str(spec.get("debit", "")), "").strip()
        credit = record.get(str(spec.get("credit", "")), "").strip()
        if debit and credit:
            raise RuntimeError(
                f"{file}: row {row_number}: both debit and credit columns set"
            )
        if not debit and not credit:
            raise RuntimeError(
                f"{file}: row {row_number}: neither debit nor credit column set"
            )
        amount = _parse_number(debit or credit, fmt, file, row_number)
        return -amount if debit else amount  # debits are sign-flipped (§3.5)
    return _parse_number(
        _column_text(record, spec, "amount", fmt.name), fmt, file, row_number
    )


def _reference_text(record: dict[str, str], fmt: BankFormat) -> str:
    """End-to-end reference: the mapped column, mined by reference_pattern (§3.5)."""
    text = _column_text(record, fmt.columns.get("reference"), "reference", fmt.name)
    if not text or not fmt.reference_pattern:
        return text
    try:
        match = re.search(fmt.reference_pattern, text)
    except re.error as exc:
        raise RuntimeError(
            f"bank format {fmt.name!r}: reference_pattern does not compile: {exc}"
        ) from None
    if match is None:
        return ""
    return match.group(1) if match.groups() else match.group(0)


# --- fingerprint (§7.2) -------------------------------------------------------


def normalize_text(value: str) -> str:
    """NFC, case-folded, whitespace-collapsed text (§3.3 matching, §7.2 hashing)."""
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def fingerprint(row: BankRow) -> str:
    """SHA-256(account | date | amount | payee | description-or-reference).

    A stable end-to-end reference replaces the description when extracted.
    Normalization per §7.2 makes the hash robust against re-export formatting
    differences (number formats, case, whitespace, Unicode composition).
    """
    amount = row.amount.quantize(CENT, rounding=ROUND_HALF_UP)
    if amount == 0:
        amount = abs(amount)  # no negative zero
    key = "|".join(
        [
            normalize_text(row.account),
            row.date.isoformat(),
            f"{amount:.2f}",
            normalize_text(row.payee),
            normalize_text(row.reference or row.description),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# --- matching & classification (§3.3, §3.4) -----------------------------------


def match_members(payee: str, aliases: PayerAliasesFile) -> list[str]:
    """Member IDs whose alias list contains the payer; sorted for determinism."""
    normalized = normalize_text(payee)
    return sorted(
        member_id
        for member_id, alias_list in aliases.aliases.items()
        if any(normalize_text(alias) == normalized for alias in alias_list)
    )


def categorize(payee: str, description: str, rules: CategorizationFile) -> str | None:
    """First-match-wins regex rules on payee + description (§3.4)."""
    text = f"{payee} {description}"
    for rule in rules.categories:
        if re.search(rule.match, text, re.IGNORECASE):
            return rule.account
    return None


@dataclass
class Classification:
    """How one row books: account1 receives +amount, account2 -amount (§6)."""

    account1: str
    account2: str
    amount: Decimal  # always positive; direction encoded by account order
    member_id: str | None
    review: str | None


def classify(
    row: BankRow,
    member_ids: list[str],
    balances: Mapping[str, Decimal],
    category: str | None,
    *,
    booking_year_dues: Decimal | None = None,
) -> Classification:
    """Match confidence and posting accounts for one row (§3.3, §5.2).

    Member matching applies to incoming payments only. High confidence
    uses the booking year's dues and the total outstanding receivable.
    Incoming rows with no alias and no category rule
    are `unmatched` (unknown payer, e.g. a third-party payer); outgoing rows
    matching no rule are `uncategorized` (unknown expense).
    """
    if row.amount >= 0:
        if len(member_ids) == 1:
            member_id = member_ids[0]
            balance = balances.get(member_id, Decimal(0))
            review = None
            if row.amount > balance:
                review = "overpaid"
            elif row.amount < balance and (
                booking_year_dues is None
                or booking_year_dues <= 0
                or row.amount < booking_year_dues
            ):
                review = "partial"
            return Classification(
                BANK_ACCOUNT, RECEIVABLES_ACCOUNT, row.amount, member_id, review
            )
        if len(member_ids) > 1:
            return Classification(
                BANK_ACCOUNT, INCOME_SUSPENSE, row.amount, None, "ambiguous"
            )
        if category is not None:
            return Classification(BANK_ACCOUNT, category, row.amount, None, None)
        return Classification(
            BANK_ACCOUNT, INCOME_SUSPENSE, row.amount, None, "unmatched"
        )
    amount = -row.amount
    if category is not None:
        return Classification(category, BANK_ACCOUNT, amount, None, None)
    return Classification(EXPENSE_SUSPENSE, BANK_ACCOUNT, amount, None, "uncategorized")
