"""Tests for `steward import` — one per PRD §6 acceptance criterion, plus edge cases."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from steward.bank_import import import_bank_export
from steward.cli import main
from steward.generate_dues import generate_dues
from steward.journal import check, query_receivable_balance

CONFIG = """\
association: "Test"
currency: "EUR"
journals: "journals"
journal_granularity: "monthly"
dues:
  proration: "monthly"
closing:
  sign: false
  remote: null
"""

MEMBERS = """\
fee_tiers:
  regular:
    - effective: "2020-01-01"
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

ALIASES = """\
aliases:
  "1001":
    - "John Doe"
    - "Jane Doe"
"""

CATEGORIES = """\
categories:
  - match: "account fee|card statement"
    account: "expenses:bank-fees"
"""

FORMATS = """\
formats:
  test-bank:
    parser: csv
    parser_options:
      delimiter: ";"
      encoding: "utf-8"
    columns:
      date: "Date"
      amount: "Amount"
      payee: "Payee"
      description: "Purpose"
      account: "IBAN"
    date_format: "%Y-%m-%d"
    decimal_mark: "."
  test-bank-ref:
    parser: csv
    parser_options:
      delimiter: ";"
      encoding: "utf-8"
    columns:
      date: "Date"
      amount: "Amount"
      payee: "Payee"
      description: "Purpose"
      reference: "Booking Text"
      account: "IBAN"
    date_format: "%Y-%m-%d"
    decimal_mark: "."
    reference_pattern: 'End-to-End-Ref\\.?:\\s*(\\S+)'
"""

HEADER = "Date;Amount;Payee;Purpose;IBAN\n"
REF_HEADER = "Date;Amount;Payee;Purpose;Booking Text;IBAN\n"


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "steward.yaml").write_text(CONFIG)
    (tmp_path / "members.yaml").write_text(MEMBERS)
    (tmp_path / "payer_aliases.yaml").write_text(ALIASES)
    (tmp_path / "categorization.yaml").write_text(CATEGORIES)
    (tmp_path / "bank_formats.yaml").write_text(FORMATS)
    return tmp_path


def write_export(root: Path, rows: list[str], name: str = "export.csv") -> Path:
    path = root / name
    path.write_text(HEADER + "".join(f"{row}\n" for row in rows))
    return path


def journal_files(root: Path) -> list[Path]:
    return sorted((root / "journals").rglob("*.journal"))


def journal_text(root: Path) -> str:
    return "\n".join(
        p.read_text() for p in journal_files(root) if p.name != "main.journal"
    )


def accrue_2026(root: Path) -> None:
    """Book John's 2026 dues (€120) so a receivable balance exists."""
    generate_dues(root, 2026, booking_date=date(2026, 1, 1))


# --- acceptance criteria (PRD §6) ----------------------------------------------


def test_reimporting_same_file_drops_all_rows(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    first = import_bank_export(root, export, "test-bank")
    assert len(first.imported) == 1
    second = import_bank_export(root, export, "test-bank")
    assert second.imported == []
    assert len(second.duplicates) == 1
    assert journal_text(root).count("membership dues") == 1


def test_matched_payment_clears_receivable(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    report = import_bank_export(root, export, "test-bank")
    assert report.flagged == []
    text = (root / "journals" / "2026" / "02.journal").read_text()
    assert "member-id: 1001" in text
    assert "import-hash:" in text
    assert "review:" not in text
    assert "assets:bank:checking" in text and "assets:receivables:dues" in text
    assert query_receivable_balance(root / "journals", "1001") == 0


def test_unknown_payer_flagged_and_reported(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-02-03;50.00;Unknown Person;donation?;DE001"])
    report = import_bank_export(root, export, "test-bank")
    assert len(report.flagged) == 1
    assert report.flagged[0][2] == "unmatched"
    text = journal_text(root)
    assert "income:unknown" in text
    assert "review: unmatched" in text
    assert "member-id" not in text


def test_dry_run_prints_transactions_and_writes_nothing(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    report = import_bank_export(root, export, "test-bank", dry_run=True)
    assert len(report.imported) == 1
    assert "John Doe | membership dues" in report.dry_run_output
    assert journal_text(root) == ""


def test_hledger_check_passes_after_real_run(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    import_bank_export(root, export, "test-bank")
    check(root / "journals")  # raises on failure


# --- edge cases (PRD §5) --------------------------------------------------------


def test_overlapping_reexport_new_filename_and_order(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    rows = [
        "2026-02-03;120.00;John Doe;membership dues;DE001",
        "2026-01-15;-9.99;Bank Corp;account fee;DE001",
    ]
    import_bank_export(root, write_export(root, rows, "jan-feb.csv"), "test-bank")
    reordered = list(reversed(rows))
    report = import_bank_export(
        root, write_export(root, reordered, "export-2026-02.csv"), "test-bank"
    )
    assert report.imported == []
    assert len(report.duplicates) == 2
    assert journal_text(root).count("import-hash:") == 2


def test_reworded_description_with_stable_reference_still_dedups(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)

    def ref_export(name: str, wording: str) -> Path:
        path = root / name
        path.write_text(
            REF_HEADER
            + f"2026-02-03;120.00;John Doe;{wording};End-to-End-Ref: RF-42;DE001\n"
        )
        return path

    first = import_bank_export(
        root, ref_export("a.csv", "membership dues"), "test-bank-ref"
    )
    second = import_bank_export(
        root, ref_export("b.csv", "reworded purpose line"), "test-bank-ref"
    )
    assert len(first.imported) == 1
    assert second.imported == []
    assert len(second.duplicates) == 1


def test_installment_payment_books_partial_clearing(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(root, ["2026-02-03;60.00;John Doe;membership dues;DE001"])
    report = import_bank_export(root, export, "test-bank")
    assert [reason for _, _, reason in report.flagged] == ["partial"]
    text = journal_text(root)
    assert "member-id: 1001" in text
    assert "review: partial" in text
    assert query_receivable_balance(root / "journals", "1001") == Decimal("60.0")


def test_overpayment_flagged(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(root, ["2026-02-03;150.00;John Doe;membership dues;DE001"])
    report = import_bank_export(root, export, "test-bank")
    assert [reason for _, _, reason in report.flagged] == ["overpaid"]
    assert query_receivable_balance(root / "journals", "1001") == Decimal("-30.0")


def test_second_payment_in_same_file_sees_running_balance(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(
        root,
        [
            "2026-02-03;60.00;John Doe;first installment;DE001",
            "2026-02-10;120.00;John Doe;second installment;DE001",
        ],
    )
    report = import_bank_export(root, export, "test-bank")
    # after the €60 installment, €60 remains; the €120 row overpays it
    assert [reason for _, _, reason in report.flagged] == ["partial", "overpaid"]


def test_categorized_outgoing_row(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-01-15;-9.99;Bank Corp;account fee;DE001"])
    report = import_bank_export(root, export, "test-bank")
    assert report.flagged == []
    text = journal_text(root)
    assert "expenses:bank-fees" in text
    assert "review:" not in text


def test_uncategorized_outgoing_row_lands_in_suspense(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-01-15;-9.99;Random Shop;office stuff;DE001"])
    report = import_bank_export(root, export, "test-bank")
    assert [reason for _, _, reason in report.flagged] == ["uncategorized"]
    text = journal_text(root)
    assert "expenses:unknown" in text
    assert "review: uncategorized" in text


def test_third_party_payer_alias_matches(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(
        root, ["2026-02-03;120.00;Jane Doe;membership dues John;DE001"]
    )
    report = import_bank_export(root, export, "test-bank")
    assert report.flagged == []
    assert "member-id: 1001" in journal_text(root)


# --- safety & reporting ----------------------------------------------------------


def test_unknown_format_errors(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    with pytest.raises(RuntimeError, match="unknown format 'nope'"):
        import_bank_export(root, export, "nope")


def test_aborts_when_validation_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    members = (root / "members.yaml").read_text()
    (root / "members.yaml").write_text(
        members.replace('tier: "regular"', 'tier: "student"')
    )
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    with pytest.raises(RuntimeError, match="validation failed"):
        import_bank_export(root, export, "test-bank")
    assert journal_text(root) == ""


def test_report_render_lists_counts_and_flagged_rows(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(
        root,
        [
            "2026-02-03;50.00;Unknown Person;donation?;DE001",
            "2026-01-15;-9.99;Bank Corp;account fee;DE001",
        ],
    )
    report = import_bank_export(root, export, "test-bank")
    rendered = report.render()
    assert "imported: 2" in rendered
    assert "duplicates: 0" in rendered
    assert "flagged: 1" in rendered
    assert "Unknown Person | donation?" in rendered
    assert "review: unmatched" in rendered


def test_cli_import(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    result = CliRunner().invoke(
        main,
        ["import", str(export), "--format", "test-bank", "--root", str(root)],
    )
    assert result.exit_code == 0, result.output
    assert "imported: 1" in result.output
