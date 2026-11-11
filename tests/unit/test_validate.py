"""Unit tests for `steward validate` — one test per PRD §6 acceptance criterion."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from steward.cli import main
from steward.validate import run_validation

VALID_CONFIG = """\
association: "Test Association"
currency: "EUR"
journals: "journals"
journal_granularity: "monthly"
dues:
  proration: "monthly"
closing:
  sign: false
  remote: null
"""

VALID_MEMBERS = """\
fee_tiers:
  regular:
    - effective: "2020-01-01"
      amount: "100.00"
    - effective: "2026-01-01"
      amount: "120.00"
members:
  - id: "1001"
    name: "John Doe"
    tiers:
      - effective: "2022-01-01"
        tier: "regular"
    membership:
      - joined: "2022-01-01"
        left: null
"""

VALID_ALIASES = """\
aliases:
  "1001":
    - "John Doe"
"""

VALID_CATEGORIZATION = """\
categories:
  - match: "account fee"
    account: "expenses:bank-fees"
"""

VALID_BANK_FORMATS = """\
formats:
  bank-a-v1:
    parser: csv
    columns:
      date: "Booking Date"
      amount: "Amount"
      payee: "Originator"
      description: "Purpose"
      account: "Account IBAN"
"""

VALID_FILES = {
    "steward.yaml": VALID_CONFIG,
    "members.yaml": VALID_MEMBERS,
    "payer_aliases.yaml": VALID_ALIASES,
    "categorization.yaml": VALID_CATEGORIZATION,
    "bank_formats.yaml": VALID_BANK_FORMATS,
}


def make_repo(tmp_path: Path, **overrides: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, content in VALID_FILES.items():
        (tmp_path / name).write_text(overrides.get(name, content))
    return tmp_path


def reasons(tmp_path: Path) -> list[str]:
    return [str(e) for e in run_validation(tmp_path)]


# --- general criteria -------------------------------------------------------


def test_clean_repo_passes(tmp_path: Path) -> None:
    assert run_validation(make_repo(tmp_path)) == []


def test_all_failures_reported_in_one_run(tmp_path: Path) -> None:
    members = VALID_MEMBERS.replace('tier: "regular"', 'tier: "student"').replace(
        'amount: "100.00"', 'amount: "-5.00"'
    )
    errors = run_validation(make_repo(tmp_path, **{"members.yaml": members}))
    assert any("not defined under fee_tiers" in str(e) for e in errors)
    assert any("negative" in str(e) for e in errors)
    assert len(errors) >= 2  # no fail-fast


def test_cli_exit_codes_and_message_format(tmp_path: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(main, ["validate", "--root", str(make_repo(tmp_path / "ok"))])
    assert ok.exit_code == 0
    bad = runner.invoke(
        main,
        [
            "validate",
            "--root",
            str(make_repo(tmp_path / "bad", **{"members.yaml": "members: []"})),
        ],
    )
    assert bad.exit_code != 0
    assert "error:" in bad.output


# --- members.yaml criteria --------------------------------------------------


def test_undefined_tier_fails_naming_member_tier_file(tmp_path: Path) -> None:
    members = VALID_MEMBERS.replace('tier: "regular"', 'tier: "student"')
    errors = reasons(make_repo(tmp_path, **{"members.yaml": members}))
    assert any("members.yaml" in e and "1001" in e and "student" in e for e in errors)


def test_overlapping_membership_periods_fail(tmp_path: Path) -> None:
    members = VALID_MEMBERS.replace(
        "left: null",
        'left: "2023-06-30"\n      - joined: "2023-01-01"\n        left: null',
    )
    errors = reasons(make_repo(tmp_path, **{"members.yaml": members}))
    assert any("1001" in e and "overlap" in e and "2023-01-01" in e for e in errors)


def test_non_ascending_fee_tiers_fail(tmp_path: Path) -> None:
    members = VALID_MEMBERS.replace(
        'effective: "2026-01-01"', 'effective: "2019-01-01"'
    )
    errors = reasons(make_repo(tmp_path, **{"members.yaml": members}))
    assert any("fee_tiers.regular" in e and "ascending" in e for e in errors)


def test_non_ascending_member_tiers_fail(tmp_path: Path) -> None:
    members = VALID_MEMBERS.replace(
        'tier: "regular"',
        'tier: "regular"\n      - effective: "2021-01-01"\n        tier: "regular"',
    )
    errors = reasons(make_repo(tmp_path, **{"members.yaml": members}))
    assert any("1001" in e and "tiers" in e and "ascending" in e for e in errors)


def test_overlapping_fee_overrides_fail(tmp_path: Path) -> None:
    members = (
        VALID_MEMBERS
        + """\
    fee_overrides:
      - start: "2025-01-01"
        end: "2025-06-30"
        amount: "0.00"
        reason: "waiver"
      - start: "2025-03-01"
        end: "2025-12-31"
        amount: "10.00"
        reason: "second"
"""
    )
    errors = reasons(make_repo(tmp_path, **{"members.yaml": members}))
    assert any("1001" in e and "fee_overrides overlap" in e for e in errors)


def test_fee_override_start_after_end_fails(tmp_path: Path) -> None:
    members = (
        VALID_MEMBERS
        + """\
    fee_overrides:
      - start: "2025-12-31"
        end: "2025-01-01"
        amount: "0.00"
        reason: "inverted"
"""
    )
    errors = reasons(make_repo(tmp_path, **{"members.yaml": members}))
    assert any("start" in e and "after end" in e for e in errors)


def test_negative_amounts_fail_zero_passes(tmp_path: Path) -> None:
    bad = VALID_MEMBERS.replace('amount: "100.00"', 'amount: "-1.00"')
    errors = reasons(make_repo(tmp_path, **{"members.yaml": bad}))
    assert any("negative" in e for e in errors)
    zero = VALID_MEMBERS.replace('amount: "100.00"', 'amount: "0.00"')
    assert run_validation(make_repo(tmp_path / "zero", **{"members.yaml": zero})) == []


def test_malformed_date_and_unquoted_amount_fail(tmp_path: Path) -> None:
    bad_date = VALID_MEMBERS.replace(
        'effective: "2020-01-01"', 'effective: "2020-13-01"'
    )
    errors = reasons(make_repo(tmp_path, **{"members.yaml": bad_date}))
    assert any("date" in e for e in errors)
    bad_amount = VALID_MEMBERS.replace('amount: "100.00"', "amount: 100.00")
    errors = reasons(make_repo(tmp_path / "float", **{"members.yaml": bad_amount}))
    assert any("quoted decimal string" in e for e in errors)


# --- other files ------------------------------------------------------------


def test_alias_key_without_member_fails(tmp_path: Path) -> None:
    aliases = VALID_ALIASES.replace('"1001"', '"9999"')
    errors = reasons(make_repo(tmp_path, **{"payer_aliases.yaml": aliases}))
    assert any(
        "payer_aliases.yaml" in e and "9999" in e and "no member" in e for e in errors
    )


def test_same_alias_under_two_members_fails(tmp_path: Path) -> None:
    aliases = VALID_ALIASES + '  "1002":\n    - "john   doe"\n'
    errors = reasons(make_repo(tmp_path, **{"payer_aliases.yaml": aliases}))
    assert any("also assigned" in e for e in errors)


def test_uncompilable_alias_regex_fails(tmp_path: Path) -> None:
    aliases = VALID_ALIASES.replace("John Doe", "[John")
    errors = reasons(make_repo(tmp_path, **{"payer_aliases.yaml": aliases}))
    assert any(
        "payer_aliases.yaml" in e
        and "1001" in e
        and "[0]" in e
        and "regex does not compile" in e
        for e in errors
    )


def test_regex_escapes_with_different_meanings_are_not_duplicates(
    tmp_path: Path,
) -> None:
    aliases = "aliases:\n  '1001': ['\\s']\n  '1002': ['\\S']\n"
    errors = reasons(make_repo(tmp_path, **{"payer_aliases.yaml": aliases}))
    assert not any("also assigned" in error for error in errors)


def test_uncompilable_categorization_regex_fails(tmp_path: Path) -> None:
    categorization = VALID_CATEGORIZATION.replace('"account fee"', '"[unclosed"')
    errors = reasons(make_repo(tmp_path, **{"categorization.yaml": categorization}))
    assert any("regex does not compile" in e for e in errors)


def test_bank_format_unknown_parser_and_missing_field_fail(tmp_path: Path) -> None:
    formats = VALID_BANK_FORMATS.replace("parser: csv", "parser: mt940")
    errors = reasons(make_repo(tmp_path, **{"bank_formats.yaml": formats}))
    assert any("unknown parser adapter" in e and "mt940" in e for e in errors)
    formats = VALID_BANK_FORMATS.replace('      payee: "Originator"\n', "")
    errors = reasons(make_repo(tmp_path / "missing", **{"bank_formats.yaml": formats}))
    assert any("'payee' is not mapped" in e for e in errors)


def test_config_unknown_key_and_out_of_domain_values_fail(tmp_path: Path) -> None:
    config = VALID_CONFIG + "unknown_key: true\n"
    errors = reasons(make_repo(tmp_path, **{"steward.yaml": config}))
    assert any("unknown configuration key" in e for e in errors)
    config = VALID_CONFIG.replace('proration: "monthly"', 'proration: "weekly"')
    errors = reasons(make_repo(tmp_path / "proration", **{"steward.yaml": config}))
    assert any("dues.proration" in e for e in errors)
    config = VALID_CONFIG.replace(
        'journal_granularity: "monthly"', 'journal_granularity: "daily"'
    )
    errors = reasons(make_repo(tmp_path / "granularity", **{"steward.yaml": config}))
    assert any("journal_granularity" in e for e in errors)


# --- journal gate -----------------------------------------------------------


def test_unbalanced_journal_fails_via_hledger(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    journals = root / "journals"
    journals.mkdir()
    (journals / "main.journal").write_text(
        "2026-01-01 Unbalanced\n    assets:bank  €10.00\n    income:dues  €-5.00\n"
    )
    errors = reasons(root)
    assert any("hledger check" in e for e in errors)


def test_no_journals_directory_skips_gate(tmp_path: Path) -> None:
    assert run_validation(make_repo(tmp_path)) == []


def test_missing_files_reported_not_raised(tmp_path: Path) -> None:
    (tmp_path / "steward.yaml").write_text(VALID_CONFIG)
    errors = run_validation(tmp_path)
    messages = [str(e) for e in errors]
    assert any("members.yaml" in m and "file not found" in m for m in messages)
    assert any("payer_aliases.yaml" in m and "file not found" in m for m in messages)
