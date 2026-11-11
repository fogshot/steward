"""Tests for bank.py — parsing, fingerprints, matching, classification."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from steward.bank import (
    BankRow,
    Classification,
    categorize,
    classify,
    fingerprint,
    match_members,
    normalize_text,
    parse_export,
)
from steward.model import (
    BankFormat,
    CategorizationFile,
    CategoryRule,
    PayerAliasesFile,
)


def make_format(**overrides: object) -> BankFormat:
    defaults: dict[str, object] = {
        "name": "test-bank",
        "parser": "csv",
        "columns": {
            "date": "Date",
            "amount": "Amount",
            "payee": "Payee",
            "description": "Purpose",
            "account": "IBAN",
        },
        "parser_options": {},
        "date_format": "%Y-%m-%d",
        "decimal_mark": ".",
    }
    return BankFormat(**(defaults | overrides))  # type: ignore[arg-type]


def write(tmp_path: Path, content: str, name: str = "export.csv") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def row(**overrides: object) -> BankRow:
    defaults: dict[str, object] = {
        "date": date(2026, 2, 3),
        "amount": Decimal("120.00"),
        "currency": "EUR",
        "payee": "John Doe",
        "description": "membership dues",
        "reference": "",
        "account": "DE001",
    }
    return BankRow(**(defaults | overrides))  # type: ignore[arg-type]


# --- parsing -----------------------------------------------------------------


def test_parse_basic_and_sorted_by_date(tmp_path: Path) -> None:
    # newest-first export: rows must come out ascending by date (decision #9)
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,IBAN\n"
        "2026-02-03,120.00,John Doe,membership dues,DE001\n"
        "2026-01-15,-9.99,Bank Corp,account fee,DE001\n",
    )
    rows = parse_export(path, make_format())
    assert [r.date for r in rows] == [date(2026, 1, 15), date(2026, 2, 3)]
    assert rows[0].amount == Decimal("-9.99")
    assert rows[1].payee == "John Doe"
    assert rows[1].account == "DE001"


def test_parse_delimiter_encoding_and_skip_header(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    path.write_bytes(
        (
            "junk metadata line\n"
            "Date;Amount;Payee;Purpose;IBAN\n"
            "03.02.2026;120,00;John Döe;membership dues;DE001\n"
        ).encode("latin-1")
    )
    fmt = make_format(
        parser_options={"delimiter": ";", "encoding": "latin-1", "skip_header": 1},
        date_format="%d.%m.%Y",
        decimal_mark=",",
    )
    rows = parse_export(path, fmt)
    assert len(rows) == 1
    assert rows[0].date == date(2026, 2, 3)
    assert rows[0].amount == Decimal("120.00")
    assert rows[0].payee == "John Döe"


def test_parse_utf8_bom_stripped(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    path.write_bytes(
        "Date,Amount,Payee,Purpose,IBAN\n"
        "2026-02-03,120.00,John Doe,membership dues,DE001\n".encode("utf-8-sig")
    )
    fmt = make_format(parser_options={"encoding": "utf-8-sig"})
    assert len(parse_export(path, fmt)) == 1


def test_parse_thousands_separator_inferred_from_decimal_mark(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path,
        "Date;Amount;Payee;Purpose;IBAN\n"
        "03.02.2026;-1.234,56;Shop;big purchase;DE001\n",
    )
    fmt = make_format(
        parser_options={"delimiter": ";"},
        date_format="%d.%m.%Y",
        decimal_mark=",",
    )
    assert parse_export(path, fmt)[0].amount == Decimal("-1234.56")


def test_parse_explicit_thousands_separator(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,IBAN\n2026-02-03,1'234.56,Shop,big purchase,DE001\n",
    )
    fmt = make_format(thousands_separator="'")
    assert parse_export(path, fmt)[0].amount == Decimal("1234.56")


def test_parse_split_debit_credit_columns(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Date,Debit,Credit,Payee,Purpose,IBAN\n"
        "2026-02-03,,120.00,John Doe,membership dues,DE001\n"
        "2026-02-04,9.99,,Bank Corp,account fee,DE001\n",
    )
    fmt = make_format(
        columns={
            "date": "Date",
            "amount": {"debit": "Debit", "credit": "Credit"},
            "payee": "Payee",
            "description": "Purpose",
            "account": "IBAN",
        }
    )
    rows = parse_export(path, fmt)
    assert rows[0].amount == Decimal("120.00")
    assert rows[1].amount == Decimal("-9.99")  # debits sign-flipped


def test_parse_debit_and_credit_both_set_is_an_error(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Date,Debit,Credit,Payee,Purpose,IBAN\n"
        "2026-02-03,1.00,2.00,John Doe,membership dues,DE001\n",
    )
    fmt = make_format(
        columns={
            "date": "Date",
            "amount": {"debit": "Debit", "credit": "Credit"},
            "payee": "Payee",
            "description": "Purpose",
            "account": "IBAN",
        }
    )
    with pytest.raises(RuntimeError, match="both debit and credit"):
        parse_export(path, fmt)


def test_parse_multi_column_payee_first_non_empty_wins(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Originator,Beneficiary,Purpose,IBAN\n"
        "2026-02-03,120.00,John Doe,,membership dues,DE001\n"
        "2026-02-04,-9.99,,Bank Corp,account fee,DE001\n",
    )
    fmt = make_format(
        columns={
            "date": "Date",
            "amount": "Amount",
            "payee": ["Originator", "Beneficiary"],
            "description": "Purpose",
            "account": "IBAN",
        }
    )
    rows = parse_export(path, fmt)
    assert [r.payee for r in rows] == ["John Doe", "Bank Corp"]


def test_parse_reference_pattern_extracts_end_to_end_reference(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,Booking Text,IBAN\n"
        "2026-02-03,120.00,John Doe,membership dues,"
        "Transfer End-to-End-Ref: RF-42,DE001\n",
    )
    fmt = make_format(
        columns={
            "date": "Date",
            "amount": "Amount",
            "payee": "Payee",
            "description": "Purpose",
            "reference": "Booking Text",
            "account": "IBAN",
        },
        reference_pattern=r"End-to-End-Ref\.?:\s*(\S+)",
    )
    assert parse_export(path, fmt)[0].reference == "RF-42"


def test_parse_reference_pattern_without_match_yields_empty_reference(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,Booking Text,IBAN\n"
        "2026-02-03,120.00,John Doe,membership dues,plain transfer,DE001\n",
    )
    fmt = make_format(
        columns={
            "date": "Date",
            "amount": "Amount",
            "payee": "Payee",
            "description": "Purpose",
            "reference": "Booking Text",
            "account": "IBAN",
        },
        reference_pattern=r"End-to-End-Ref\.?:\s*(\S+)",
    )
    assert parse_export(path, fmt)[0].reference == ""


def test_parse_reference_column_without_pattern_kept_verbatim(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,Ref,IBAN\n"
        "2026-02-03,120.00,John Doe,membership dues,RF-42,DE001\n",
    )
    fmt = make_format(
        columns={
            "date": "Date",
            "amount": "Amount",
            "payee": "Payee",
            "description": "Purpose",
            "reference": "Ref",
            "account": "IBAN",
        }
    )
    assert parse_export(path, fmt)[0].reference == "RF-42"


def test_parse_skip_footer_drops_trailing_lines(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,IBAN\n"
        "2026-02-03,120.00,John Doe,membership dues,DE001\n"
        "this is,a,bank,footer,line\n",
    )
    fmt = make_format(parser_options={"skip_footer": 1})
    assert len(parse_export(path, fmt)) == 1


def test_parse_header_only_yields_no_rows(tmp_path: Path) -> None:
    path = write(tmp_path, "Date,Amount,Payee,Purpose,IBAN\n")
    assert parse_export(path, make_format()) == []


def test_parse_missing_mapped_column_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, "Date,Amount,Payee,Purpose,IBAN\n2026-02-03,1.00,A,B,C\n")
    fmt = make_format(
        columns={
            "date": "Booking Date",
            "amount": "Amount",
            "payee": "Payee",
            "description": "Purpose",
            "account": "IBAN",
        }
    )
    with pytest.raises(RuntimeError, match="Booking Date"):
        parse_export(path, fmt)


def test_parse_bad_date_is_an_error(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,IBAN\nnot-a-date,1.00,A,B,C\n",
    )
    with pytest.raises(RuntimeError, match="row 1.*date"):
        parse_export(path, make_format())


def test_parse_bad_amount_is_an_error(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Date,Amount,Payee,Purpose,IBAN\n2026-02-03,not-a-number,A,B,C\n",
    )
    with pytest.raises(RuntimeError, match="row 1.*amount"):
        parse_export(path, make_format())


# --- fingerprint (§7.2) -------------------------------------------------------


def test_fingerprint_stable_across_number_formatting() -> None:
    assert fingerprint(row(amount=Decimal("120.00"))) == fingerprint(
        row(amount=Decimal("120.0"))
    )


def test_fingerprint_ignores_case_whitespace_and_unicode_composition() -> None:
    assert fingerprint(row(payee="John  Doe ")) == fingerprint(row(payee="john doe"))
    assert fingerprint(row(payee="Müller")) == fingerprint(
        row(payee="Müller".replace("ü", "u\u0308"))  # NFD decomposition
    )


def test_fingerprint_reference_takes_precedence_over_description() -> None:
    first = row(reference="RF-42", description="wording one")
    second = row(reference="RF-42", description="reworded between exports")
    assert fingerprint(first) == fingerprint(second)
    third = row(description="wording one")
    fourth = row(description="reworded between exports")
    assert fingerprint(third) != fingerprint(fourth)


def test_fingerprint_account_prevents_transfer_collisions() -> None:
    assert fingerprint(row(account="DE001")) != fingerprint(row(account="DE002"))


def test_fingerprint_no_negative_zero() -> None:
    assert fingerprint(row(amount=Decimal("-0.00"))) == fingerprint(
        row(amount=Decimal("0.00"))
    )


# --- matching (§3.3) ----------------------------------------------------------


def aliases_file(mapping: dict[str, list[str]]) -> PayerAliasesFile:
    return PayerAliasesFile(aliases=mapping)


def test_match_members_case_and_whitespace_insensitive() -> None:
    aliases = aliases_file({"1001": ["John Doe"], "1002": ["Jane Roe"]})
    assert match_members("  JOHN   doe ", aliases) == ["1001"]


def test_match_members_third_party_payer_alias() -> None:
    aliases = aliases_file({"1001": ["John Doe", "Jane Doe"]})
    assert match_members("Jane Doe", aliases) == ["1001"]


def test_match_members_no_match() -> None:
    assert match_members("Unknown", aliases_file({"1001": ["John Doe"]})) == []


def test_match_members_ambiguous() -> None:
    aliases = aliases_file({"1001": ["Shared Name"], "1002": ["shared name"]})
    assert match_members("Shared Name", aliases) == ["1001", "1002"]


def test_match_members_searches_payer_for_name() -> None:
    assert match_members("Dr. JOHN DOE", aliases_file({"1001": ["John Doe"]})) == [
        "1001"
    ]


def test_match_members_regex_features_and_normalization() -> None:
    aliases = aliases_file({"1001": [r"^dr\.\s+john\s+doe$"]})
    assert match_members("  Dr.\tJOHN   Doe  ", aliases) == ["1001"]
    assert match_members("Mr. John Doe", aliases) == []


def test_match_members_overlapping_patterns_are_ambiguous() -> None:
    aliases = aliases_file({"1001": ["John Doe"], "1002": [r"Dr\. John"]})
    assert match_members("Dr. John Doe", aliases) == ["1001", "1002"]


def test_match_members_literal_regex_punctuation_and_unicode() -> None:
    aliases = aliases_file({"1001": [r"J\. Müller"]})
    assert match_members("Dr. J. Mu\u0308ller", aliases) == ["1001"]
    assert match_members("Dr. Jx Müller", aliases) == []


# --- categorization (§3.4) ----------------------------------------------------


def rules_file(*rules: tuple[str, str]) -> CategorizationFile:
    return CategorizationFile(
        categories=[CategoryRule(match=m, account=a) for m, a in rules]
    )


def test_categorize_first_match_wins() -> None:
    rules = rules_file(
        ("account fee", "expenses:bank-fees"),
        ("account", "expenses:other"),
    )
    assert categorize("Bank Corp", "account fee", rules) == "expenses:bank-fees"


def test_categorize_case_insensitive_on_payee_and_description() -> None:
    rules = rules_file(("hosting-provider", "expenses:hosting"))
    assert categorize("HOSTING-PROVIDER Ltd", "invoice", rules) == "expenses:hosting"
    assert categorize("invoice", "from Hosting-Provider", rules) == "expenses:hosting"


def test_categorize_no_match() -> None:
    assert categorize("Random", "stuff", rules_file(("fee", "expenses:fees"))) is None


# --- classification (§3.3, §5.2) ----------------------------------------------


def test_classify_high_confidence_clearing() -> None:
    result = classify(row(), ["1001"], {"1001": Decimal("120.00")}, None)
    assert result == Classification(
        account1="assets:bank:checking",
        account2="assets:receivables:dues",
        amount=Decimal("120.00"),
        member_id="1001",
        review=None,
    )


def test_classify_partial_payment() -> None:
    result = classify(
        row(amount=Decimal("60.00")), ["1001"], {"1001": Decimal("120.00")}, None
    )
    assert result.account2 == "assets:receivables:dues"
    assert result.member_id == "1001"
    assert result.review == "partial"


@pytest.mark.parametrize(
    ("amount", "balance", "year_dues", "expected"),
    [
        ("120", "240", "120", None),
        ("180", "240", "120", None),
        ("60", "240", "120", "partial"),
        ("60", "60", "120", None),
        ("61", "60", "120", "overpaid"),
        ("60", "240", "0", "partial"),
    ],
)
def test_classify_booking_year_dues_threshold(
    amount: str, balance: str, year_dues: str, expected: str | None
) -> None:
    result = classify(
        row(amount=Decimal(amount)),
        ["1001"],
        {"1001": Decimal(balance)},
        None,
        booking_year_dues=Decimal(year_dues),
    )
    assert result.review == expected


def test_classify_overpayment() -> None:
    result = classify(
        row(amount=Decimal("150.00")), ["1001"], {"1001": Decimal("120.00")}, None
    )
    assert result.review == "overpaid"
    assert result.member_id == "1001"


def test_classify_ambiguous_books_suspense_without_member() -> None:
    result = classify(row(), ["1001", "1002"], {}, None)
    assert result.account2 == "income:unknown"
    assert result.member_id is None
    assert result.review == "ambiguous"


def test_classify_unmatched_incoming() -> None:
    result = classify(row(), [], {}, None)
    assert result.account2 == "income:unknown"
    assert result.review == "unmatched"


def test_classify_categorized_incoming() -> None:
    result = classify(row(), [], {}, "income:refunds")
    assert result.account1 == "assets:bank:checking"
    assert result.account2 == "income:refunds"
    assert result.review is None


def test_classify_categorized_outgoing() -> None:
    result = classify(row(amount=Decimal("-9.99")), [], {}, "expenses:bank-fees")
    assert result.account1 == "expenses:bank-fees"
    assert result.account2 == "assets:bank:checking"
    assert result.amount == Decimal("9.99")
    assert result.review is None


def test_classify_uncategorized_outgoing() -> None:
    result = classify(row(amount=Decimal("-9.99")), [], {}, None)
    assert result.account1 == "expenses:unknown"
    assert result.account2 == "assets:bank:checking"
    assert result.review == "uncategorized"


def test_classify_outgoing_ignores_alias_matches() -> None:
    # a refund to a member is not a dues payment: no receivable clearing
    result = classify(
        row(amount=Decimal("-50.00")), ["1001"], {"1001": Decimal("120.00")}, None
    )
    assert result.member_id is None
    assert result.review == "uncategorized"


def test_normalize_text_collapses_whitespace_and_case() -> None:
    assert normalize_text("  John\tDoe\n ") == "john doe"
