"""Annual close calculated by hledger and imported through Steward's write path."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import journal
from .model import load_config
from .validate import run_validation

RECEIVABLES = "assets:receivables:dues"
CLOPEN_EQUITY = "equity:opening/closing balances"
RETAIN_EQUITY = "equity:retained earnings"
CLOSE_TAG = "steward-close-year"


def _hledger(args: list[str], *, input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["hledger", *args], input=input_text, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError("hledger not found on PATH") from exc
    if result.returncode:
        raise RuntimeError(
            f"hledger failed: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("hledger returned invalid JSON") from exc


def _amount(amounts: list[dict[str, Any]]) -> Decimal:
    total = Decimal(0)
    for amount in amounts:
        if (
            amount["acommodity"] != "€"
            or amount["acost"] is not None
            or amount["acostbasis"] is not None
        ):
            raise RuntimeError("close supports only unpriced EUR amounts")
        quantity = amount["aquantity"]
        value = Decimal(quantity["decimalMantissa"]).scaleb(-quantity["decimalPlaces"])
        if value != value.quantize(Decimal("0.01")):
            raise RuntimeError(
                "close cannot represent amounts with fractions of a cent"
            )
        total += value
    return total


def _transactions(main: Path, year: int, mode: str) -> list[dict[str, Any]]:
    text = _hledger(
        [
            "-f",
            str(main),
            "close",
            f"--{mode}",
            "-e",
            f"{year + 1:04d}-01-01",
        ]
    )
    result = _json(_hledger(["-f", "-", "print", "-I", "-O", "json"], input_text=text))
    if not isinstance(result, list) or not all(
        isinstance(item, dict) for item in result
    ):
        raise RuntimeError("unsupported hledger close transaction output")
    return result


def _member_balances(main: Path, year: int) -> list[tuple[str | None, Decimal]]:
    output = _hledger(
        [
            "-f",
            str(main),
            "balance",
            "acct:^assets:receivables:dues$",
            "--pivot",
            "member-id",
            "--flat",
            "-e",
            f"{year + 1:04d}-01-01",
            "-O",
            "json",
        ]
    )
    rows, _ = _json(output)
    return [(row[0] or None, _amount(row[3])) for row in rows]


def _bookings(main: Path, year: int) -> list[journal.Booking]:
    members = _member_balances(main, year)
    result: list[journal.Booking] = []
    receivable_closing = Decimal(0)
    receivable_opening = Decimal(0)
    for mode, equity in (("retain", RETAIN_EQUITY), ("clopen", CLOPEN_EQUITY)):
        for transaction in _transactions(main, year, mode):
            day = date.fromisoformat(transaction["tdate"])
            for posting in transaction["tpostings"]:
                account = posting["paccount"]
                if account == equity:
                    continue
                amount = _amount(posting["pamount"])
                if not amount:
                    continue
                if mode == "clopen" and account == RECEIVABLES:
                    if day.year == year:
                        receivable_closing += amount
                    else:
                        receivable_opening += amount
                    continue
                result.append(
                    journal.Booking(
                        date=day,
                        description=transaction["tdescription"],
                        account1=account,
                        account2=equity,
                        amount=amount,
                        tags={CLOSE_TAG: str(year)},
                    )
                )
    total = sum((value for _, value in members), Decimal(0))
    if receivable_closing != -total or receivable_opening != total:
        raise RuntimeError("member receivables do not match hledger close")
    for member, balance in members:
        if not balance:
            continue
        tags = {CLOSE_TAG: str(year)}
        if member is not None:
            tags["member-id"] = member
        for day, amount, description in (
            (date(year, 12, 31), -balance, "closing balances"),
            (date(year + 1, 1, 1), balance, "opening balances"),
        ):
            result.append(
                journal.Booking(
                    date=day,
                    description=description,
                    account1=RECEIVABLES,
                    account2=CLOPEN_EQUITY,
                    amount=amount,
                    tags=tags,
                )
            )
    # A zero-value marker makes an empty close detectable on subsequent runs.
    result.append(
        journal.Booking(
            date=date(year, 12, 31),
            description=f"Annual close {year}",
            account1=RETAIN_EQUITY,
            account2=RETAIN_EQUITY,
            amount=Decimal(0),
            tags={CLOSE_TAG: str(year)},
        )
    )
    return result


def close_year(root: Path, year: int, *, dry_run: bool = False) -> str:
    if not 1 <= year <= 9998:
        raise RuntimeError("year must be between 1 and 9998")
    errors = run_validation(root)
    if errors:
        raise RuntimeError(
            "validation failed; fix these errors first:\n"
            + "\n".join(f"  {error}" for error in errors)
        )
    config, config_errors = load_config(root / "steward.yaml")
    if config_errors:
        raise RuntimeError("\n".join(str(error) for error in config_errors))
    journals_dir = root / config.journals
    main = journals_dir / "main.journal"
    if not main.is_file():
        raise RuntimeError(f"journal not found: {main}")
    existing = _hledger(["-f", str(main), "tags", "--values", CLOSE_TAG]).splitlines()
    if str(year) in existing:
        raise RuntimeError(f"year {year} already closed or partially closed")
    if any(value.isdigit() and int(value) > year for value in existing):
        raise RuntimeError(f"cannot close {year} after a later year has been closed")
    try:
        bookings = _bookings(main, year)
    except (IndexError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise RuntimeError(f"unsupported hledger close output: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="steward-close-") as directory:
        temp_root = Path(directory)
        copied = temp_root / "journals"
        shutil.copytree(journals_dir, copied)
        rules = root / "rules" / "steward.rules"
        if not rules.is_file():
            raise RuntimeError(f"rules file not found: {rules}")
        (temp_root / "rules").mkdir()
        shutil.copy2(rules, temp_root / "rules" / "steward.rules")
        preview = (
            journal.append_bookings(
                copied, bookings, config.journal_granularity, dry_run=True
            )
            if dry_run
            else ""
        )
        journal.append_bookings(copied, bookings, config.journal_granularity)
        journal.check(copied)
    if dry_run:
        return (
            f"Would close {year}: {len(bookings)} entries; journals unchanged\n"
            f"{preview.rstrip()}"
        )
    try:
        journal.append_bookings(journals_dir, bookings, config.journal_granularity)
        journal.check(journals_dir)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(
            f"close {year} failed while writing; "
            f"inspect journals before retrying: {exc}"
        ) from exc
    return f"Closed {year}: {len(bookings)} entries; run steward commit to record them"
