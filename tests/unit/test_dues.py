"""Tests for `steward generate-dues` — one per PRD §6 acceptance criterion."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from steward.cli import main
from steward.dues import compute_dues
from steward.generate_dues import generate_dues
from steward.model import load_members

CONFIG = """\
association: "Test"
currency: "EUR"
journals: "journals"
journal_granularity: "monthly"
dues:
  proration: "{proration}"
closing:
  sign: false
  remote: null
"""

ALIASES = "aliases: {}\n"
CATEGORIES = "categories: []\n"
FORMATS = "formats: {}\n"

MEMBERS_TEMPLATE = """\
fee_tiers:
  regular:
    - effective: "2020-01-01"
      amount: "120.00"
  reduced:
    - effective: "2020-01-01"
      amount: "60.00"
members:
{members}
"""

JOHN = """\
  - id: "1001"
    name: "John Doe"
    tiers:
      - effective: "{joined}"
        tier: "regular"
    membership:
      - joined: "{joined}"
        left: {left}
"""


def make_repo(tmp_path: Path, members: str, proration: str = "monthly") -> Path:
    (tmp_path / "steward.yaml").write_text(CONFIG.format(proration=proration))
    (tmp_path / "members.yaml").write_text(MEMBERS_TEMPLATE.format(members=members))
    (tmp_path / "payer_aliases.yaml").write_text(ALIASES)
    (tmp_path / "categorization.yaml").write_text(CATEGORIES)
    (tmp_path / "bank_formats.yaml").write_text(FORMATS)
    return tmp_path


def john(joined: str = "2022-01-01", left: str = "null") -> str:
    return JOHN.format(joined=joined, left=left)


def dues_for(tmp_path: Path, members: str, year: int, proration: str = "monthly"):
    root = make_repo(tmp_path, members, proration)
    members_file, errors = load_members(root / "members.yaml")
    assert errors == []
    return compute_dues(members_file.members[0], members_file, year, proration)


def journal_files(root: Path) -> list[Path]:
    return sorted((root / "journals").rglob("*.journal"))


def journal_text(root: Path) -> str:
    return "\n".join(
        p.read_text() for p in journal_files(root) if p.name != "main.journal"
    )


# --- proration & segments ---------------------------------------------------


def test_april_joiner_booked_nine_twelfths(tmp_path: Path) -> None:
    assert dues_for(tmp_path, john(joined="2026-04-17"), 2026) == 90.00


def test_join_april_leave_june_booked_three_twelfths(tmp_path: Path) -> None:
    assert (
        dues_for(tmp_path, john(joined="2026-04-17", left='"2026-06-30"'), 2026)
        == 30.00
    )


def test_rejoining_member_booked_six_twelfths(tmp_path: Path) -> None:
    members = """\
  - id: "1001"
    name: "John Doe"
    tiers:
      - effective: "2022-01-01"
        tier: "regular"
    membership:
      - joined: "2022-01-01"
        left: "2024-12-31"
      - joined: "2026-07-01"
        left: null
"""
    assert dues_for(tmp_path, members, 2026) == 60.00


def test_partial_override_prorated_over_window(tmp_path: Path) -> None:
    members = (
        john()
        + """\
    fee_overrides:
      - start: "2026-01-01"
        end: "2026-03-31"
        amount: "60.00"
        reason: "partial exemption"
"""
    )
    assert dues_for(tmp_path, members, 2026) == 105.00


def test_daily_proration(tmp_path: Path) -> None:
    members = """\
  - id: "1001"
    name: "John Doe"
    tiers:
      - effective: "2026-09-23"
        tier: "regular"
    membership:
      - joined: "2026-09-23"
        left: null
"""
    root = make_repo(tmp_path, members, proration="daily")
    (root / "members.yaml").write_text(
        (root / "members.yaml")
        .read_text()
        .replace('amount: "120.00"', 'amount: "365.00"')
    )
    members_file, errors = load_members(root / "members.yaml")
    assert errors == []
    # Sep 23 – Dec 31 = 100 days; 100/365 × €365 = €100.00
    assert compute_dues(members_file.members[0], members_file, 2026, "daily") == 100.00


def test_none_proration_full_fee_for_december_joiner(tmp_path: Path) -> None:
    assert (
        dues_for(tmp_path, john(joined="2026-12-24"), 2026, proration="none") == 120.00
    )


def test_mid_year_tier_change_split_at_boundary(tmp_path: Path) -> None:
    members = """\
  - id: "1001"
    name: "John Doe"
    tiers:
      - effective: "2022-01-01"
        tier: "regular"
      - effective: "2026-07-01"
        tier: "reduced"
    membership:
      - joined: "2022-01-01"
        left: null
"""
    assert dues_for(tmp_path, members, 2026) == 90.00


# --- waivers & skipping -----------------------------------------------------


def test_full_year_waiver_books_zero_accrual_and_skips_on_rerun(tmp_path: Path) -> None:
    members = (
        john()
        + """\
    fee_overrides:
      - start: "2026-01-01"
        end: "2026-12-31"
        amount: "0.00"
        reason: "waiver"
"""
    )
    root = make_repo(tmp_path, members)
    report = generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    assert report.waived == ["John Doe"]
    assert report.booked == []
    text = journal_text(root)
    assert "member-id: 1001" in text and "year: 2026" in text
    report2 = generate_dues(root, 2026, booking_date=date(2026, 2, 1))
    assert report2.skipped == ["John Doe"]
    assert report2.waived == []


def test_member_left_prior_year_skipped_entirely(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john(left='"2025-12-31"'))
    report = generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    assert report.booked == [] and report.skipped == [] and report.waived == []
    assert journal_text(root) == ""


# --- idempotency & adjustments ----------------------------------------------


def test_second_run_books_nothing(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    report = generate_dues(root, 2026, booking_date=date(2026, 2, 1))
    assert report.skipped == ["John Doe"]
    assert report.booked == [] and report.adjusted == []
    assert journal_text(root).count("Membership dues 2026") == 1


def test_rate_change_books_difference_adjustment(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    members_path = root / "members.yaml"
    old = '- effective: "2020-01-01"\n      amount: "120.00"'
    new = old + '\n    - effective: "2026-01-01"\n      amount: "150.00"'
    members_path.write_text(members_path.read_text().replace(old, new))
    report = generate_dues(root, 2026, booking_date=date(2026, 3, 1))
    assert report.adjusted == [("John Doe", 30.00)]
    text = journal_text(root)
    assert "adjustment" in text
    assert "30.00" in text


def test_waiver_removal_books_full_adjustment(tmp_path: Path) -> None:
    members = (
        john()
        + """\
    fee_overrides:
      - start: "2026-01-01"
        end: "2026-12-31"
        amount: "0.00"
        reason: "waiver"
"""
    )
    root = make_repo(tmp_path, members)
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    members_path = root / "members.yaml"
    members_path.write_text(members_path.read_text().split("fee_overrides:")[0])
    report = generate_dues(root, 2026, booking_date=date(2026, 3, 1))
    assert report.adjusted == [("John Doe", 120.00)]


# --- booking & safety -------------------------------------------------------


def test_bookings_dated_with_run_date_and_land_in_period_file(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    generate_dues(root, 2026, booking_date=date(2026, 3, 15))
    journal_file = root / "journals" / "2026" / "03.journal"
    assert journal_file.exists()
    text = journal_file.read_text()
    assert "2026-03-15" in text
    assert "year: 2026" in text


def test_date_option_overrides_booking_date(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    journal_file = root / "journals" / "2026" / "01.journal"
    assert journal_file.exists()
    assert "2026-01-01" in journal_file.read_text()


def test_aborts_when_validation_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john().replace('tier: "regular"', 'tier: "student"'))
    with pytest.raises(RuntimeError, match="validation failed"):
        generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    assert journal_text(root) == ""


def test_report_lists_counts_and_amounts(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    report = generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    rendered = report.render()
    assert "booked: 1" in rendered
    assert "John Doe" in rendered and "120.00" in rendered
    assert "skipped: 0" in rendered and "waived: 0" in rendered


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    report = generate_dues(root, 2026, booking_date=date(2026, 1, 1), dry_run=True)
    assert report.booked == [("John Doe", 120.00)]
    assert journal_text(root) == ""


def test_hledger_check_passes_after_real_run(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    from steward.journal import check

    check(root / "journals")  # raises on failure


def test_cli_generate_dues(tmp_path: Path) -> None:
    root = make_repo(tmp_path, john())
    result = CliRunner().invoke(
        main,
        [
            "generate-dues",
            "--year",
            "2026",
            "--date",
            "2026-01-01",
            "--root",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "booked: 1" in result.output
