"""Read-only member dues balances calculated by hledger."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import yaml

from .checks import all_member_checks, check_config_domains
from .model import load_config, load_members

ACCOUNT_QUERY = "acct:^assets:receivables:dues$"


def _sum_exact(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    exponents = [value.as_tuple().exponent for value in values]
    if not all(isinstance(exponent, int) for exponent in exponents):
        raise ValueError("non-finite balance")
    fractional = max(
        [0, *(-exponent for exponent in exponents if isinstance(exponent, int))]
    )
    integer = max([0, *(value.adjusted() + 1 for value in values if value)])
    with localcontext() as context:
        context.prec = max(28, integer + fractional + len(str(len(values))) + 1)
        return sum(values, Decimal(0))


def _amount(value: object) -> Decimal:
    if not isinstance(value, list):
        raise ValueError("amounts must be a list")
    amounts: list[Decimal] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid amount")
        if item.get("acommodity") != "€":
            raise ValueError("unsupported commodity; only EUR is supported")
        quantity = item.get("aquantity")
        if not isinstance(quantity, dict):
            raise ValueError("invalid quantity")
        mantissa = quantity.get("decimalMantissa")
        places = quantity.get("decimalPlaces")
        if (
            type(mantissa) is not int
            or type(places) is not int
            or not 0 <= places <= 100
        ):
            raise ValueError("invalid decimal quantity")
        amounts.append(Decimal(f"{mantissa}e-{places}"))
    return _sum_exact(amounts)


def _balance_output(output: str) -> tuple[dict[str, Decimal], Decimal]:
    try:
        result = json.loads(output)
        if not isinstance(result, list) or len(result) != 2:
            raise ValueError("expected balance rows and total")
        rows, total = result
        if not isinstance(rows, list):
            raise ValueError("invalid balance rows")
        balances: dict[str, Decimal] = {}
        for row in rows:
            if (
                not isinstance(row, list)
                or len(row) != 4
                or not isinstance(row[0], str)
            ):
                raise ValueError("invalid balance row")
            if row[0] in balances:
                raise ValueError("duplicate balance row")
            balances[row[0]] = _amount(row[3])
        net = _amount(total)
        if _sum_exact(list(balances.values())) != net:
            raise ValueError("balance rows do not match hledger total")
        return balances, net
    except (ValueError, ArithmeticError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid hledger balance output: {exc}") from exc


def _query(main: Path, end: date, *, pivot: bool) -> tuple[dict[str, Decimal], Decimal]:
    command = [
        "hledger",
        "-f",
        str(main),
        "balance",
        ACCOUNT_QUERY,
        "--flat",
        "--end",
        end.isoformat(),
        "-O",
        "json",
    ]
    if pivot:
        command.extend(["--pivot", "member-id"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("hledger binary not found on PATH") from exc
    except OSError as exc:
        raise RuntimeError(f"could not run hledger: {exc}") from exc
    if result.returncode:
        raise RuntimeError(
            f"hledger balance failed: {(result.stderr or result.stdout).strip()}"
        )
    return _balance_output(result.stdout)


def _format_amount(value: Decimal) -> str:
    text = format(value, "f")
    if "." not in text:
        text += ".00"
    else:
        text = text.ljust(text.index(".") + 3, "0")
    return f"€{text}"


@dataclass(frozen=True)
class DuesReport:
    as_of: date
    balances: dict[str, Decimal]
    names: dict[str, str]
    total: Decimal

    def render(self) -> str:
        lines = [f"Dues balances as of {self.as_of.isoformat()}"]
        rows = [
            (key, amount) for key, amount in sorted(self.balances.items()) if amount
        ]
        if rows:
            lines.append("Member ID  Name  Balance")
            for member_id, amount in rows:
                name = self.names.get(member_id)
                if not member_id:
                    lines.append(f"(missing)  Unattributed  {_format_amount(amount)}")
                elif name is None:
                    lines.append(
                        f"{member_id}  Unknown member  {_format_amount(amount)}"
                    )
                else:
                    lines.append(f"{member_id}  {name}  {_format_amount(amount)}")
            if any(not key or key not in self.names for key, _ in rows):
                lines.append(
                    "Warning: unknown or missing member IDs require investigation."
                )
        else:
            lines.append("No nonzero balances.")
        lines.append(f"Net receivable: {_format_amount(self.total)}")
        lines.append(
            "Positive balances are outstanding; negative balances are credits."
        )
        lines.append(
            "Balances include annual-close postings; zero does not imply payment."
        )
        return "\n".join(lines)


def report_dues(root: Path, as_of: date | None = None) -> DuesReport:
    day = as_of or date.today()
    try:
        end = day + timedelta(days=1)
    except OverflowError as exc:
        raise RuntimeError(
            "as-of date is too late to calculate an exclusive end date"
        ) from exc
    try:
        config_path = root / "steward.yaml"
        config, config_errors = load_config(config_path)
        members, member_errors = load_members(root / "members.yaml")
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RuntimeError(
            f"could not read treasury configuration or members: {exc}"
        ) from exc
    if not isinstance(raw_config, dict):
        raise RuntimeError("steward.yaml: expected a configuration mapping")
    if "journals" in raw_config and (
        not isinstance(raw_config["journals"], str) or not raw_config["journals"]
    ):
        raise RuntimeError("steward.yaml: journals must be a non-empty string")
    errors = (
        config_errors
        + member_errors
        + check_config_domains(config)
        + all_member_checks(members)
    )
    if errors:
        raise RuntimeError(
            "invalid treasury data:\n" + "\n".join(str(e) for e in errors)
        )
    main = root / config.journals / "main.journal"
    if not main.is_file():
        raise RuntimeError(f"journal not found: {main}")
    balances, total = _query(main, end, pivot=True)
    _, account_total = _query(main, end, pivot=False)
    if total != account_total:
        raise RuntimeError("hledger member balances do not match the receivables total")
    return DuesReport(day, balances, {m.id: m.name for m in members.members}, total)
