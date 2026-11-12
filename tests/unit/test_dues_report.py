"""Dues report integration tests with synthetic journals."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from steward.cli import main
from steward.dues_report import _balance_output, _sum_exact, report_dues
from steward.generate_dues import generate_dues
from tests.unit.test_bank_import import MEMBERS, make_repo


def setup_journal(root: Path, content: str) -> None:
    directory = root / "journals"
    directory.mkdir()
    (directory / "main.journal").write_text("include **.journal\n")
    (directory / "activity.journal").write_text(content)


def run(root: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["report", "dues", "--root", str(root), *args])
    return result.exit_code, result.output


def test_report_includes_names_dates_credits_and_unattributed(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    members = (root / "members.yaml").read_text()
    members += MEMBERS[MEMBERS.index('  - id: "1001"') :].replace("1001", "1002")
    (root / "members.yaml").write_text(members)
    setup_journal(
        root,
        "2025-01-01 dues  ; member-id: 1001\n"
        "    assets:receivables:dues    €120.10\n"
        "    income:dues               €-120.10\n\n"
        "2026-02-01 payment  ; member-id: 1001\n"
        "    assets:bank:checking        €20.00\n"
        "    assets:receivables:dues    €-20.00\n\n"
        "2026-02-01 payment  ; member-id: 1002\n"
        "    assets:bank:checking        €30.00\n"
        "    assets:receivables:dues    €-30.00\n\n"
        "2026-02-01 unknown  ; member-id: 9000\n"
        "    assets:receivables:dues     €7.00\n"
        "    income:dues                €-7.00\n\n"
        "2026-02-01 untagged\n"
        "    assets:receivables:dues     €3.00\n"
        "    income:dues                €-3.00\n\n"
        "2026-02-02 adjustment  ; member-id: 1001\n"
        "    assets:receivables:dues     €5.00\n"
        "    income:dues                €-5.00\n",
    )
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    code, output = run(root, "--as-of", "2026-02-01")
    assert code == 0, output
    assert "1001  John Doe  €100.10" in output
    assert "1002  John Doe  €-30.00" in output
    assert "9000  Unknown member  €7.00" in output
    assert "(missing)  Unattributed  €3.00" in output
    assert "Warning:" in output
    assert "Net receivable: €80.10" in output
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    code, later = run(root, "--as-of", "2026-02-02")
    assert code == 0, later
    assert "Net receivable: €85.10" in later


def test_empty_journal_and_today_default(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    setup_journal(root, "")
    code, output = run(root)
    assert code == 0, output
    assert f"as of {date.today()}" in output
    assert "No nonzero balances." in output
    assert "Net receivable: €0.00" in output


def test_close_date_effects(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))
    result = CliRunner().invoke(main, ["close", "--root", str(root), "--year", "2026"])
    assert result.exit_code == 0, result.output
    code, closing = run(root, "--as-of", "2026-12-31")
    assert code == 0, closing
    assert "No nonzero balances." in closing
    code, opening = run(root, "--as-of", "2027-01-01")
    assert code == 0, opening
    assert "1001  John Doe  €120.00" in opening


@pytest.mark.parametrize("as_of", ["not-a-date", "2026-1-1", "9999-12-31"])
def test_invalid_date_fails(tmp_path: Path, as_of: str) -> None:
    code, output = run(make_repo(tmp_path), "--as-of", as_of)
    assert code != 0
    assert "Error" in output


def test_foreign_currency_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    setup_journal(
        root,
        "2026-01-01 foreign  ; member-id: 1001\n"
        "    assets:receivables:dues    $10\n"
        "    income:dues               $-10\n",
    )
    code, output = run(root, "--as-of", "2026-12-31")
    assert code != 0
    assert "unsupported commodity" in output


def test_missing_journal_and_duplicate_members_fail(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    code, output = run(root)
    assert code != 0 and "journal not found" in output
    members = root / "members.yaml"
    content = members.read_text()
    members.write_text(content + content[content.index('  - id: "1001"') :])
    code, output = run(root)
    assert code != 0 and "duplicate member id" in output


@pytest.mark.parametrize(
    "data", ["bad json", "[]", json.dumps([[], [{"acommodity": "$"}]])]
)
def test_malformed_hledger_output_fails(data: str) -> None:
    with pytest.raises(RuntimeError, match="invalid hledger balance output"):
        _balance_output(data)


def test_version_and_report_help() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.output
    assert CliRunner().invoke(main, ["report", "dues", "--help"]).exit_code == 0


def test_report_does_not_require_other_configs(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    setup_journal(root, "")
    (root / "bank_formats.yaml").unlink()
    (root / "payer_aliases.yaml").unlink()
    assert report_dues(root, date(2026, 1, 1)).total == 0


def test_former_member_name_zero_balance_and_precision(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    members = root / "members.yaml"
    members.write_text(
        members.read_text()
        .replace('name: "John Doe"', 'name: "Current Name"')
        .replace("left: null", 'left: "2025-12-31"')
    )
    setup_journal(
        root,
        "2025-01-01 dues  ; member-id: 1001\n"
        "    assets:receivables:dues    €1.234\n"
        "    income:dues               €-1.234\n\n"
        "2026-01-01 payment  ; member-id: 1001\n"
        "    assets:bank:checking       €1.234\n"
        "    assets:receivables:dues   €-1.234\n",
    )
    code, output = run(root, "--as-of", "2025-12-31")
    assert code == 0, output
    assert "1001  Current Name  €1.234" in output
    code, output = run(root, "--as-of", "2026-01-01")
    assert code == 0, output
    assert "No nonzero balances." in output


def test_today_default_excludes_future(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    setup_journal(
        root,
        "9998-01-01 future  ; member-id: 1001\n"
        "    assets:receivables:dues    €120\n"
        "    income:dues               €-120\n",
    )
    code, output = run(root)
    assert code == 0, output
    assert "No nonzero balances." in output


@pytest.mark.parametrize("config", ["", "journals: 123\n"])
def test_invalid_config_fails(tmp_path: Path, config: str) -> None:
    root = make_repo(tmp_path)
    (root / "steward.yaml").write_text(config)
    code, output = run(root)
    assert code != 0
    assert "steward.yaml" in output


def test_bad_hledger_and_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path)
    setup_journal(root, "bad journal text\n")
    code, output = run(root)
    assert code != 0
    assert "hledger balance failed" in output
    monkeypatch.setenv("PATH", str(tmp_path))
    code, output = run(root)
    assert code != 0
    assert "hledger binary not found" in output


def test_inconsistent_balance_total_fails() -> None:
    amount = {
        "acommodity": "€",
        "aquantity": {"decimalMantissa": 100, "decimalPlaces": 2},
    }
    data = json.dumps([[["1001", "1001", 0, [amount]]], []])
    with pytest.raises(RuntimeError, match="do not match"):
        _balance_output(data)


def test_full_account_total_mismatch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path)
    setup_journal(root, "")

    def fake_query(
        main: Path, end: date, *, pivot: bool
    ) -> tuple[dict[str, Decimal], Decimal]:
        if pivot:
            return {"1001": Decimal("12")}, Decimal("12")
        return {"assets:receivables:dues": Decimal("13")}, Decimal("13")

    monkeypatch.setattr("steward.dues_report._query", fake_query)
    code, output = run(root, "--as-of", "2026-01-01")
    assert code != 0
    assert "do not match" in output


def test_decimal_sum_preserves_more_than_context_precision() -> None:
    assert _sum_exact(
        [Decimal("123456789012345678901234567890.01"), Decimal("0.001")]
    ) == Decimal("123456789012345678901234567890.011")
