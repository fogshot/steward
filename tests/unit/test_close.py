"""Annual close integration tests with real hledger."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from steward.cli import main
from steward.generate_dues import generate_dues
from steward.journal import check
from tests.unit.test_bank_import import make_repo


def hledger(root: Path, *args: str) -> str:
    return subprocess.run(
        ["hledger", "-f", str(root / "journals" / "main.journal"), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_close_preserves_member_balance_and_zeros_income(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    result = CliRunner().invoke(main, ["close", "--root", str(root), "--year", "2026"])
    assert result.exit_code == 0, result.output
    assert (root / "journals" / "2026" / "12.journal").exists()
    assert (root / "journals" / "2027" / "01.journal").exists()
    assert "€120" in hledger(
        root, "balance", "assets:receivables:dues", "tag:member-id=1001", "-p", "2027"
    )
    assert "€120" in hledger(
        root, "balance", "assets:receivables:dues", "tag:member-id=1001"
    )
    assert "income:dues" not in hledger(root, "balance", "income:dues")
    check(root / "journals")
    again = CliRunner().invoke(main, ["close", "--root", str(root), "--year", "2026"])
    assert again.exit_code != 0
    assert "already closed" in again.output


def test_close_dry_run_does_not_change_journals(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    before = {p: p.read_bytes() for p in (root / "journals").rglob("*") if p.is_file()}
    result = CliRunner().invoke(
        main, ["close", "--root", str(root), "--year", "2026", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "Would close" in result.output
    assert "closing balances" in result.output
    assert "opening balances" in result.output
    assert before == {
        p: p.read_bytes() for p in (root / "journals").rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("year", [0, 9999])
def test_close_rejects_invalid_year(tmp_path: Path, year: int) -> None:
    root = make_repo(tmp_path)
    result = CliRunner().invoke(
        main, ["close", "--root", str(root), "--year", str(year)]
    )
    assert result.exit_code != 0
    assert "year must be between" in result.output


def test_zero_net_receivables_still_carry_individual_balances(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    journals = root / "journals"
    journals.mkdir()
    (journals / "main.journal").write_text("include **.journal\n")
    (journals / "activity.journal").write_text(
        "2026-01-01 dues  ; member-id: 1001\n"
        "    assets:receivables:dues    €120\n"
        "    income:dues               €-120\n\n"
        "2026-02-01 advance  ; member-id: 1002\n"
        "    assets:bank:checking       €120\n"
        "    assets:receivables:dues   €-120\n"
    )
    rules = root / "rules"
    rules.mkdir()
    from steward.journal import RULES_CONTENT

    (rules / "steward.rules").write_text(RULES_CONTENT)
    result = CliRunner().invoke(main, ["close", "--root", str(root), "--year", "2026"])
    assert result.exit_code == 0, result.output
    assert "€120" in hledger(
        root, "balance", "tag:member-id=1001", RECEIVABLES, "-p", "2027"
    )
    assert "€-120" in hledger(
        root, "balance", "tag:member-id=1002", RECEIVABLES, "-p", "2027"
    )
    check(journals)


RECEIVABLES = "assets:receivables:dues"


def test_yearly_granularity_uses_year_files(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    config = root / "steward.yaml"
    config.write_text(config.read_text().replace("monthly", "yearly", 1))
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    result = CliRunner().invoke(main, ["close", "--root", str(root), "--year", "2026"])
    assert result.exit_code == 0, result.output
    assert "closing balances" in (root / "journals" / "2026.journal").read_text()
    assert "opening balances" in (root / "journals" / "2027.journal").read_text()
    check(root / "journals")


def test_unsupported_commodity_fails_without_writes(tmp_path: Path) -> None:
    from steward.journal import MAIN_JOURNAL_CONTENT, RULES_CONTENT

    root = make_repo(tmp_path)
    journals = root / "journals"
    journals.mkdir()
    (journals / "main.journal").write_text(MAIN_JOURNAL_CONTENT)
    source = journals / "activity.journal"
    source.write_text(
        "2026-01-01 foreign\n"
        "    assets:bank:checking    $10\n"
        "    income:donations       $-10\n"
    )
    rules = root / "rules"
    rules.mkdir()
    (rules / "steward.rules").write_text(RULES_CONTENT)
    before = {p: p.read_bytes() for p in journals.rglob("*") if p.is_file()}
    result = CliRunner().invoke(main, ["close", "--root", str(root), "--year", "2026"])
    assert result.exit_code != 0
    assert "only unpriced EUR" in result.output
    assert before == {p: p.read_bytes() for p in journals.rglob("*") if p.is_file()}


def test_empty_year_is_closed_only_once(tmp_path: Path) -> None:
    from steward.journal import MAIN_JOURNAL_CONTENT, RULES_CONTENT

    root = make_repo(tmp_path)
    journals = root / "journals"
    journals.mkdir()
    (journals / "main.journal").write_text(MAIN_JOURNAL_CONTENT)
    rules = root / "rules"
    rules.mkdir()
    (rules / "steward.rules").write_text(RULES_CONTENT)
    args = ["close", "--root", str(root), "--year", "2026"]
    first = CliRunner().invoke(main, args)
    assert first.exit_code == 0, first.output
    second = CliRunner().invoke(main, args)
    assert second.exit_code != 0
    assert "already closed" in second.output


def test_close_preserves_untagged_receivable(tmp_path: Path) -> None:
    from steward.journal import MAIN_JOURNAL_CONTENT, RULES_CONTENT

    root = make_repo(tmp_path)
    journals = root / "journals"
    journals.mkdir()
    (journals / "main.journal").write_text(MAIN_JOURNAL_CONTENT)
    (journals / "activity.journal").write_text(
        "2026-01-01 dues\n"
        "    assets:receivables:dues     €40\n"
        "    income:dues                €-40\n"
    )
    rules = root / "rules"
    rules.mkdir()
    (rules / "steward.rules").write_text(RULES_CONTENT)
    result = CliRunner().invoke(main, ["close", "--root", str(root), "--year", "2026"])
    assert result.exit_code == 0, result.output
    assert "€40" in hledger(root, "balance", RECEIVABLES, "-p", "2027")
    check(journals)
