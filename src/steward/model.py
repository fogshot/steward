"""Canonical data model: typed dataclasses + YAML loaders.

The dataclasses in this module ARE the canonical schema (validation PRD §3):
`validate` checks them, and the other commands operate on them. All amounts
are Decimal parsed from quoted strings; all dates are datetime.date parsed
from quoted ISO strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ACCOUNT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*(:[a-zA-Z0-9_-]+)*$")


@dataclass
class ValidationError:
    """A single validation failure with file, location, and reason."""

    file: str
    location: str
    reason: str

    def __str__(self) -> str:
        return f"{self.file}: {self.location}: {self.reason}"


def _read_yaml(path: Path) -> tuple[object, list[ValidationError]]:
    if not path.exists():
        return None, [ValidationError(path.name, "(file)", "file not found")]
    try:
        return yaml.safe_load(path.read_text()), []
    except yaml.YAMLError as exc:
        return None, [ValidationError(path.name, "(file)", f"invalid YAML: {exc}")]


def _parse_date(
    value: object, file: str, location: str
) -> tuple[date | None, ValidationError | None]:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None, ValidationError(
            file, location, f"expected quoted ISO date YYYY-MM-DD, got {value!r}"
        )
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, ValidationError(file, location, f"invalid calendar date {value!r}")


def _parse_amount(
    value: object, file: str, location: str
) -> tuple[Decimal | None, ValidationError | None]:
    if not isinstance(value, str):
        return None, ValidationError(
            file,
            location,
            f"amount must be a quoted decimal string, got {value!r} "
            "(unquoted YAML numbers are floats)",
        )
    try:
        return Decimal(value), None
    except InvalidOperation:
        return None, ValidationError(
            file, location, f"invalid decimal amount {value!r}"
        )


def _expect_map(
    value: object, file: str, location: str
) -> tuple[dict[str, object], ValidationError | None]:
    if not isinstance(value, dict):
        return {}, ValidationError(
            file, location, f"expected a mapping, got {type(value).__name__}"
        )
    return value, None


def _expect_list(
    value: object, file: str, location: str
) -> tuple[list[object], ValidationError | None]:
    if not isinstance(value, list):
        return [], ValidationError(
            file, location, f"expected a list, got {type(value).__name__}"
        )
    return value, None


# --- members.yaml -----------------------------------------------------------


@dataclass
class FeeTierEntry:
    effective: date
    amount: Decimal


@dataclass
class TierAssignment:
    effective: date
    tier: str


@dataclass
class MembershipPeriod:
    joined: date
    left: date | None


@dataclass
class FeeOverride:
    start: date
    end: date
    amount: Decimal
    reason: str


@dataclass
class Member:
    id: str
    name: str
    tiers: list[TierAssignment] = field(default_factory=list)
    membership: list[MembershipPeriod] = field(default_factory=list)
    fee_overrides: list[FeeOverride] = field(default_factory=list)


@dataclass
class MembersFile:
    fee_tiers: dict[str, list[FeeTierEntry]] = field(default_factory=dict)
    members: list[Member] = field(default_factory=list)


def load_members(path: Path) -> tuple[MembersFile, list[ValidationError]]:
    file = path.name
    data, errors = _read_yaml(path)
    if data is None:
        return MembersFile(), errors
    if not isinstance(data, dict):
        return MembersFile(), [ValidationError(file, "(root)", "expected a mapping")]
    result = MembersFile()

    raw_tiers, err = _expect_map(data.get("fee_tiers", {}), file, "fee_tiers")
    if err:
        errors.append(err)
    for tier_name, entries in raw_tiers.items():
        loc = f"fee_tiers.{tier_name}"
        entries, err = _expect_list(entries, file, loc)
        if err:
            errors.append(err)
            continue
        tier_entries: list[FeeTierEntry] = []
        for i, entry in enumerate(entries):
            eloc = f"{loc}[{i}]"
            if not isinstance(entry, dict):
                errors.append(ValidationError(file, eloc, "expected a mapping"))
                continue
            eff, e1 = _parse_date(entry.get("effective"), file, f"{eloc}.effective")
            amt, e2 = _parse_amount(entry.get("amount"), file, f"{eloc}.amount")
            errors.extend(e for e in (e1, e2) if e)
            if eff is not None and amt is not None:
                tier_entries.append(FeeTierEntry(effective=eff, amount=amt))
        result.fee_tiers[str(tier_name)] = tier_entries

    raw_members, err = _expect_list(data.get("members", []), file, "members")
    if err:
        errors.append(err)
    for i, raw in enumerate(raw_members):
        mloc = f"members[{i}]"
        if not isinstance(raw, dict):
            errors.append(ValidationError(file, mloc, "expected a mapping"))
            continue
        member_id = raw.get("id")
        if not isinstance(member_id, str) or not member_id:
            errors.append(
                ValidationError(
                    file, f"{mloc}.id", f"must be a non-empty string, got {member_id!r}"
                )
            )
            member_id = None
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            errors.append(
                ValidationError(
                    file, f"{mloc}.name", f"must be a non-empty string, got {name!r}"
                )
            )
            name = None
        mloc = f"members[{i}] (id {member_id})" if member_id else mloc

        tiers: list[TierAssignment] = []
        raw_t, err = _expect_list(raw.get("tiers", []), file, f"{mloc}.tiers")
        if err:
            errors.append(err)
        for j, t in enumerate(raw_t):
            tloc = f"{mloc}.tiers[{j}]"
            if not isinstance(t, dict):
                errors.append(ValidationError(file, tloc, "expected a mapping"))
                continue
            eff, e1 = _parse_date(t.get("effective"), file, f"{tloc}.effective")
            tname = t.get("tier")
            if not isinstance(tname, str) or not tname:
                errors.append(
                    ValidationError(
                        file,
                        f"{tloc}.tier",
                        f"must be a non-empty string, got {tname!r}",
                    )
                )
                tname = None
            if e1:
                errors.append(e1)
            if eff is not None and tname is not None:
                tiers.append(TierAssignment(effective=eff, tier=tname))

        periods: list[MembershipPeriod] = []
        raw_p, err = _expect_list(raw.get("membership", []), file, f"{mloc}.membership")
        if err:
            errors.append(err)
        for j, p in enumerate(raw_p):
            ploc = f"{mloc}.membership[{j}]"
            if not isinstance(p, dict):
                errors.append(ValidationError(file, ploc, "expected a mapping"))
                continue
            joined, e1 = _parse_date(p.get("joined"), file, f"{ploc}.joined")
            left_raw = p.get("left")
            left: date | None = None
            if left_raw is not None:
                left, e2 = _parse_date(left_raw, file, f"{ploc}.left")
                if e2:
                    errors.append(e2)
            if e1:
                errors.append(e1)
            if joined is not None and (left_raw is None or left is not None):
                periods.append(MembershipPeriod(joined=joined, left=left))

        overrides: list[FeeOverride] = []
        raw_o, err = _expect_list(
            raw.get("fee_overrides", []), file, f"{mloc}.fee_overrides"
        )
        if err:
            errors.append(err)
        for j, o in enumerate(raw_o):
            oloc = f"{mloc}.fee_overrides[{j}]"
            if not isinstance(o, dict):
                errors.append(ValidationError(file, oloc, "expected a mapping"))
                continue
            start, e1 = _parse_date(o.get("start"), file, f"{oloc}.start")
            end, e2 = _parse_date(o.get("end"), file, f"{oloc}.end")
            amt, e3 = _parse_amount(o.get("amount"), file, f"{oloc}.amount")
            reason = o.get("reason", "")
            errors.extend(e for e in (e1, e2, e3) if e)
            if start is not None and end is not None and amt is not None:
                overrides.append(
                    FeeOverride(start=start, end=end, amount=amt, reason=str(reason))
                )

        if member_id is not None and name is not None:
            result.members.append(
                Member(
                    id=member_id,
                    name=name,
                    tiers=tiers,
                    membership=periods,
                    fee_overrides=overrides,
                )
            )
    return result, errors


# --- payer_aliases.yaml -----------------------------------------------------


@dataclass
class PayerAliasesFile:
    aliases: dict[str, list[str]] = field(default_factory=dict)


def load_payer_aliases(path: Path) -> tuple[PayerAliasesFile, list[ValidationError]]:
    file = path.name
    data, errors = _read_yaml(path)
    if data is None:
        return PayerAliasesFile(), errors
    if not isinstance(data, dict):
        return PayerAliasesFile(), [
            ValidationError(file, "(root)", "expected a mapping")
        ]
    raw, err = _expect_map(data.get("aliases", {}), file, "aliases")
    if err:
        errors.append(err)
        return PayerAliasesFile(), errors
    result = PayerAliasesFile()
    for member_id, alias_list in raw.items():
        loc = f"aliases.{member_id}"
        if not isinstance(member_id, str):
            errors.append(
                ValidationError(
                    file, loc, f"member id key must be a string, got {member_id!r}"
                )
            )
            continue
        alias_list, err = _expect_list(alias_list, file, loc)
        if err:
            errors.append(err)
            continue
        for k, alias in enumerate(alias_list):
            if not isinstance(alias, str) or not alias:
                errors.append(
                    ValidationError(
                        file,
                        f"{loc}[{k}]",
                        f"alias must be a non-empty string, got {alias!r}",
                    )
                )
        result.aliases[member_id] = [a for a in alias_list if isinstance(a, str) and a]
    return result, errors


# --- categorization.yaml ----------------------------------------------------


@dataclass
class CategoryRule:
    match: str
    account: str


@dataclass
class CategorizationFile:
    categories: list[CategoryRule] = field(default_factory=list)


def load_categorization(path: Path) -> tuple[CategorizationFile, list[ValidationError]]:
    file = path.name
    data, errors = _read_yaml(path)
    if data is None:
        return CategorizationFile(), errors
    if not isinstance(data, dict):
        return CategorizationFile(), [
            ValidationError(file, "(root)", "expected a mapping")
        ]
    raw, err = _expect_list(data.get("categories", []), file, "categories")
    if err:
        errors.append(err)
        return CategorizationFile(), errors
    result = CategorizationFile()
    for i, rule in enumerate(raw):
        loc = f"categories[{i}]"
        if not isinstance(rule, dict):
            errors.append(ValidationError(file, loc, "expected a mapping"))
            continue
        match = rule.get("match")
        account = rule.get("account")
        if not isinstance(match, str) or not match:
            errors.append(
                ValidationError(
                    file, f"{loc}.match", f"must be a non-empty string, got {match!r}"
                )
            )
            match = None
        if not isinstance(account, str) or not _ACCOUNT_RE.match(account):
            errors.append(
                ValidationError(
                    file,
                    f"{loc}.account",
                    f"must be a well-formed account name, got {account!r}",
                )
            )
            account = None
        if match is not None and account is not None:
            result.categories.append(CategoryRule(match=match, account=account))
    return result, errors


# --- bank_formats.yaml ------------------------------------------------------

REQUIRED_BANK_FIELDS = ("date", "amount", "payee", "description", "account")
KNOWN_PARSERS = ("csv",)


@dataclass
class BankFormat:
    name: str
    parser: str
    columns: dict[str, object] = field(default_factory=dict)
    parser_options: dict[str, object] = field(default_factory=dict)
    date_format: str | None = None
    decimal_mark: str | None = None
    reference_pattern: str | None = None


@dataclass
class BankFormatsFile:
    formats: dict[str, BankFormat] = field(default_factory=dict)


def load_bank_formats(path: Path) -> tuple[BankFormatsFile, list[ValidationError]]:
    file = path.name
    data, errors = _read_yaml(path)
    if data is None:
        return BankFormatsFile(), errors
    if not isinstance(data, dict):
        return BankFormatsFile(), [
            ValidationError(file, "(root)", "expected a mapping")
        ]
    raw, err = _expect_map(data.get("formats", {}), file, "formats")
    if err:
        errors.append(err)
        return BankFormatsFile(), errors
    result = BankFormatsFile()
    for name, mapping in raw.items():
        loc = f"formats.{name}"
        if not isinstance(mapping, dict):
            errors.append(ValidationError(file, loc, "expected a mapping"))
            continue
        parser = mapping.get("parser")
        if not isinstance(parser, str) or not parser:
            errors.append(
                ValidationError(
                    file, f"{loc}.parser", f"must be a non-empty string, got {parser!r}"
                )
            )
            parser = None
        columns = mapping.get("columns", {})
        if not isinstance(columns, dict):
            errors.append(ValidationError(file, f"{loc}.columns", "expected a mapping"))
            columns = {}
        result.formats[str(name)] = BankFormat(
            name=str(name),
            parser=parser or "",
            columns=columns,
            parser_options=mapping.get("parser_options", {}) or {},
            date_format=mapping.get("date_format"),
            decimal_mark=mapping.get("decimal_mark"),
            reference_pattern=mapping.get("reference_pattern"),
        )
    return result, errors


# --- steward.yaml -----------------------------------------------------------

KNOWN_CONFIG_KEYS = {
    "association",
    "currency",
    "journals",
    "journal_granularity",
    "fiscal_year_start",
    "dues",
    "closing",
}
KNOWN_DUES_KEYS = {"proration"}
KNOWN_CLOSING_KEYS = {"sign", "remote"}
PRORATION_VALUES = ("monthly", "daily", "none")
GRANULARITY_VALUES = ("monthly", "yearly")


@dataclass
class Config:
    association: str = ""
    currency: str = "EUR"
    journals: str = "journals"
    journal_granularity: str = "monthly"
    fiscal_year_start: str = "01-01"
    proration: str = "monthly"
    closing_sign: bool = False
    closing_remote: str | None = None


def load_config(path: Path) -> tuple[Config, list[ValidationError]]:
    file = path.name
    data, errors = _read_yaml(path)
    if data is None:
        return Config(), errors
    if not isinstance(data, dict):
        return Config(), [ValidationError(file, "(root)", "expected a mapping")]
    config = Config()
    for key in data:
        if key not in KNOWN_CONFIG_KEYS:
            errors.append(ValidationError(file, key, "unknown configuration key"))
    if isinstance(data.get("association"), str):
        config.association = data["association"]
    if isinstance(data.get("currency"), str):
        config.currency = data["currency"]
    if isinstance(data.get("journals"), str):
        config.journals = data["journals"]
    if isinstance(data.get("journal_granularity"), str):
        config.journal_granularity = data["journal_granularity"]
    if isinstance(data.get("fiscal_year_start"), str):
        config.fiscal_year_start = data["fiscal_year_start"]
    dues = data.get("dues", {})
    if isinstance(dues, dict):
        for key in dues:
            if key not in KNOWN_DUES_KEYS:
                errors.append(
                    ValidationError(file, f"dues.{key}", "unknown configuration key")
                )
        if isinstance(dues.get("proration"), str):
            config.proration = dues["proration"]
    closing = data.get("closing", {})
    if isinstance(closing, dict):
        for key in closing:
            if key not in KNOWN_CLOSING_KEYS:
                errors.append(
                    ValidationError(file, f"closing.{key}", "unknown configuration key")
                )
        if isinstance(closing.get("sign"), bool):
            config.closing_sign = closing["sign"]
        if isinstance(closing.get("remote"), str):
            config.closing_remote = closing["remote"]
    return config, errors
