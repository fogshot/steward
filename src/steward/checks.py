"""Named validation checks — one function per rule in the validation PRD §4.

Each function takes the parsed dataclasses and returns a list of
ValidationError. All checks run; there is no fail-fast.
"""

from __future__ import annotations

import re

from .model import (
    GRANULARITY_VALUES,
    KNOWN_PARSERS,
    PRORATION_VALUES,
    REQUIRED_BANK_FIELDS,
    BankFormatsFile,
    CategorizationFile,
    Config,
    MembersFile,
    PayerAliasesFile,
    ValidationError,
)

MEMBERS_FILE = "members.yaml"
ALIASES_FILE = "payer_aliases.yaml"
CATEGORIZATION_FILE = "categorization.yaml"
BANK_FORMATS_FILE = "bank_formats.yaml"
CONFIG_FILE = "steward.yaml"


def check_unique_member_ids(members: MembersFile) -> list[ValidationError]:
    seen: dict[str, int] = {}
    errors: list[ValidationError] = []
    for i, member in enumerate(members.members):
        if member.id in seen:
            errors.append(
                ValidationError(
                    MEMBERS_FILE,
                    f"members[{i}] (id {member.id})",
                    f"duplicate member id; first defined at members[{seen[member.id]}]",
                )
            )
        else:
            seen[member.id] = i
    return errors


def check_fee_tiers_ascending(members: MembersFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for tier_name, entries in members.fee_tiers.items():
        for i in range(1, len(entries)):
            if entries[i].effective < entries[i - 1].effective:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"fee_tiers.{tier_name}[{i}]",
                        f"effective {entries[i].effective} is before previous entry "
                        f"{entries[i - 1].effective}; entries must be ascending",
                    )
                )
    return errors


def check_tiers_reference_defined_fee_tiers(
    members: MembersFile,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for member in members.members:
        for j, assignment in enumerate(member.tiers):
            if assignment.tier not in members.fee_tiers:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"member {member.id} tiers[{j}]",
                        f"tier {assignment.tier!r} is not defined under fee_tiers",
                    )
                )
    return errors


def check_tiers_ascending(members: MembersFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for member in members.members:
        for j in range(1, len(member.tiers)):
            current, previous = member.tiers[j], member.tiers[j - 1]
            if current.effective < previous.effective:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"member {member.id} tiers[{j}]",
                        f"effective {current.effective} is before previous entry "
                        f"{previous.effective}; entries must be ascending",
                    )
                )
    return errors


def check_membership_periods_disjoint(members: MembersFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for member in members.members:
        periods = sorted(member.membership, key=lambda p: p.joined)
        for j in range(1, len(periods)):
            prev, cur = periods[j - 1], periods[j]
            if prev.left is None or cur.joined <= prev.left:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"member {member.id}",
                        f"membership periods overlap: joined {prev.joined} "
                        f"left {prev.left} overlaps joined {cur.joined} "
                        f"left {cur.left}",
                    )
                )
    return errors


def check_fee_overrides_valid(members: MembersFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for member in members.members:
        for j, override in enumerate(member.fee_overrides):
            if override.start > override.end:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"member {member.id} fee_overrides[{j}]",
                        f"start {override.start} is after end {override.end}",
                    )
                )
        sorted_overrides = sorted(member.fee_overrides, key=lambda o: o.start)
        for j in range(1, len(sorted_overrides)):
            prev, cur = sorted_overrides[j - 1], sorted_overrides[j]
            if cur.start <= prev.end:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"member {member.id}",
                        f"fee_overrides overlap: {prev.start}..{prev.end} "
                        f"overlaps {cur.start}..{cur.end}",
                    )
                )
    return errors


def check_amounts_non_negative(members: MembersFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for tier_name, entries in members.fee_tiers.items():
        for i, entry in enumerate(entries):
            if entry.amount < 0:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"fee_tiers.{tier_name}[{i}]",
                        f"amount {entry.amount} is negative; must be non-negative",
                    )
                )
    for member in members.members:
        for j, override in enumerate(member.fee_overrides):
            if override.amount < 0:
                errors.append(
                    ValidationError(
                        MEMBERS_FILE,
                        f"member {member.id} fee_overrides[{j}]",
                        f"amount {override.amount} is negative; must be non-negative",
                    )
                )
    return errors


def check_alias_keys_reference_members(
    aliases: PayerAliasesFile, members: MembersFile
) -> list[ValidationError]:
    member_ids = {m.id for m in members.members}
    return [
        ValidationError(
            ALIASES_FILE,
            f"aliases.{member_id}",
            "no member with this id exists in members.yaml",
        )
        for member_id in aliases.aliases
        if member_id not in member_ids
    ]


def check_aliases_unique_across_members(
    aliases: PayerAliasesFile,
) -> list[ValidationError]:
    owner: dict[str, str] = {}
    errors: list[ValidationError] = []
    for member_id, alias_list in aliases.aliases.items():
        for alias in alias_list:
            key = (
                alias
                if re.search(r"[\\\[\]().*+?{}|^$]", alias)
                else " ".join(alias.casefold().split())
            )
            if key in owner and owner[key] != member_id:
                errors.append(
                    ValidationError(
                        ALIASES_FILE,
                        f"aliases.{member_id}",
                        f"alias {alias!r} is also assigned to member {owner[key]}",
                    )
                )
            else:
                owner[key] = member_id
    return errors


def check_alias_regexes(aliases: PayerAliasesFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for member_id, alias_list in aliases.aliases.items():
        for i, alias in enumerate(alias_list):
            try:
                re.compile(alias, re.IGNORECASE)
            except re.error as exc:
                errors.append(
                    ValidationError(
                        ALIASES_FILE,
                        f"aliases.{member_id}[{i}]",
                        f"regex does not compile: {exc}",
                    )
                )
    return errors


def check_categorization_regexes(
    categorization: CategorizationFile,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for i, rule in enumerate(categorization.categories):
        try:
            re.compile(rule.match, re.IGNORECASE)
        except re.error as exc:
            errors.append(
                ValidationError(
                    CATEGORIZATION_FILE,
                    f"categories[{i}].match",
                    f"regex does not compile: {exc}",
                )
            )
    return errors


def check_bank_formats(formats: BankFormatsFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for name, mapping in formats.formats.items():
        loc = f"formats.{name}"
        if mapping.parser not in KNOWN_PARSERS:
            errors.append(
                ValidationError(
                    BANK_FORMATS_FILE,
                    f"{loc}.parser",
                    f"unknown parser adapter {mapping.parser!r}; "
                    f"known: {', '.join(KNOWN_PARSERS)}",
                )
            )
        for required in REQUIRED_BANK_FIELDS:
            if required not in mapping.columns:
                errors.append(
                    ValidationError(
                        BANK_FORMATS_FILE,
                        f"{loc}.columns",
                        f"required internal field {required!r} is not mapped",
                    )
                )
    return errors


def check_config_domains(config: Config) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if config.journal_granularity not in GRANULARITY_VALUES:
        errors.append(
            ValidationError(
                CONFIG_FILE,
                "journal_granularity",
                f"must be one of {GRANULARITY_VALUES}, "
                f"got {config.journal_granularity!r}",
            )
        )
    if config.proration not in PRORATION_VALUES:
        errors.append(
            ValidationError(
                CONFIG_FILE,
                "dues.proration",
                f"must be one of {PRORATION_VALUES}, got {config.proration!r}",
            )
        )
    return errors


def all_member_checks(members: MembersFile) -> list[ValidationError]:
    errors: list[ValidationError] = []
    for check in (
        check_unique_member_ids,
        check_fee_tiers_ascending,
        check_tiers_reference_defined_fee_tiers,
        check_tiers_ascending,
        check_membership_periods_disjoint,
        check_fee_overrides_valid,
        check_amounts_non_negative,
    ):
        errors.extend(check(members))
    return errors
