"""Tests for `steward import` — one per PRD §6 acceptance criterion, plus edge cases."""

from __future__ import annotations

import subprocess
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
    assert first.imported == 1
    second = import_bank_export(root, export, "test-bank")
    assert second.imported == 0
    assert second.duplicates == 1
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
    assert report.imported == 1
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
    assert report.imported == 0
    assert report.duplicates == 2
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
    assert first.imported == 1
    assert second.imported == 0
    assert second.duplicates == 1


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


def test_report_render_lists_counts_flagged_rows_and_journal_files(
    tmp_path: Path,
) -> None:
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
    assert "parsed: 2" in rendered
    assert "imported: 2" in rendered
    assert "duplicates: 0" in rendered
    assert "flagged for review: 1" in rendered
    assert "Unknown Person | donation?" in rendered
    assert "review: unmatched" in rendered
    assert "written to:" in rendered
    assert "2026/01.journal" in rendered
    assert "2026/02.journal" in rendered
    assert "hledger print tag:review" in rendered
    # imported rows are not listed individually — only flagged ones
    assert "account fee" not in rendered.split("written to:")[0].replace(
        "flagged for review", ""
    )


def test_report_render_dry_run_marks_files_as_would_be_written(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    report = import_bank_export(root, export, "test-bank", dry_run=True)
    assert "would be written to:" in report.render()


def test_report_render_summarizes_many_flagged_rows_by_reason(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    rows = [
        f"2026-01-{day:02d};-9.99;Shop {day};purchase {day};DE001"
        for day in range(1, 26)
    ]
    report = import_bank_export(root, write_export(root, rows), "test-bank")
    rendered = report.render()
    assert "flagged for review: 25" in rendered
    assert "  uncategorized: 25" in rendered
    assert "Shop 1" not in rendered  # individual rows not listed
    assert "hledger print tag:review" in rendered


# --- re-resolution (decision #14) -----------------------------------------------


def git_commit_all(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@t.t",
            "commit",
            "-qm",
            "commit",
        ],
        check=True,
    )


def test_reimport_with_new_rule_resolves_uncommitted_entry(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-01-15;-9.99;Random Shop;office stuff;DE001"])
    first = import_bank_export(root, export, "test-bank")
    assert first.resolved == 0
    assert "expenses:unknown" in journal_text(root)

    categories = (root / "categorization.yaml").read_text()
    (root / "categorization.yaml").write_text(
        categories + '  - match: "office stuff"\n    account: "expenses:office"\n'
    )
    second = import_bank_export(root, export, "test-bank")
    assert second.resolved == 1
    assert second.duplicates == 0
    assert second.flagged == []
    text = journal_text(root)
    assert "expenses:office" in text
    assert "expenses:unknown" not in text
    assert "review:" not in text
    check(root / "journals")  # write-path gate still passes


def test_reimport_with_new_alias_resolves_to_member_clearing(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(root, ["2026-02-03;120.00;J. Doe;membership dues;DE001"])
    first = import_bank_export(root, export, "test-bank")
    assert [reason for _, _, reason in first.flagged] == ["unmatched"]

    aliases = (root / "payer_aliases.yaml").read_text()
    (root / "payer_aliases.yaml").write_text(aliases + '    - "J. Doe"\n')
    second = import_bank_export(root, export, "test-bank")
    assert second.resolved == 1
    text = journal_text(root)
    assert "member-id: 1001" in text
    assert "assets:receivables:dues" in text
    assert "review:" not in text
    assert query_receivable_balance(root / "journals", "1001") == 0


def test_committed_entries_are_never_rewritten(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-01-15;-9.99;Random Shop;office stuff;DE001"])
    import_bank_export(root, export, "test-bank")
    git_commit_all(root)

    categories = (root / "categorization.yaml").read_text()
    (root / "categorization.yaml").write_text(
        categories + '  - match: "office stuff"\n    account: "expenses:office"\n'
    )
    report = import_bank_export(root, export, "test-bank")
    assert report.resolved == 0
    assert report.duplicates == 1
    assert report.committed_flagged == 1
    text = journal_text(root)
    assert "expenses:unknown" in text  # untouched
    assert "review: uncategorized" in text
    assert "committed with open review flags" in report.render()


def test_partial_flags_are_not_auto_reresolved(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(root, ["2026-02-03;60.00;John Doe;installment;DE001"])
    first = import_bank_export(root, export, "test-bank")
    assert [reason for _, _, reason in first.flagged] == ["partial"]
    second = import_bank_export(root, export, "test-bank")
    assert second.resolved == 0
    assert second.duplicates == 1
    assert "review: partial" in journal_text(root)


def test_reresolution_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-01-15;-9.99;Random Shop;office stuff;DE001"])
    import_bank_export(root, export, "test-bank")
    categories = (root / "categorization.yaml").read_text()
    (root / "categorization.yaml").write_text(
        categories + '  - match: "office stuff"\n    account: "expenses:office"\n'
    )
    report = import_bank_export(root, export, "test-bank", dry_run=True)
    assert report.resolved == 1
    assert "would resolve: 1" in report.render()
    assert "expenses:unknown" in journal_text(root)  # unchanged on disk


def test_reresolution_updates_receivable_balance(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    accrue_2026(root)
    export = write_export(root, ["2026-02-03;120.00;J. Doe;membership dues;DE001"])
    import_bank_export(root, export, "test-bank")
    aliases = (root / "payer_aliases.yaml").read_text()
    (root / "payer_aliases.yaml").write_text(aliases + '    - "J. Doe"\n')
    second = import_bank_export(root, export, "test-bank")
    assert second.resolved == 1
    assert query_receivable_balance(root / "journals", "1001") == 0


def test_cli_import(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    export = write_export(root, ["2026-02-03;120.00;John Doe;membership dues;DE001"])
    result = CliRunner().invoke(
        main,
        ["import", str(export), "--format", "test-bank", "--root", str(root)],
    )
    assert result.exit_code == 0, result.output
    assert "imported: 1" in result.output
