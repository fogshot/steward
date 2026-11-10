"""Tests for resolve.py — review-resolution rewrites of uncommitted entries."""

from __future__ import annotations

from decimal import Decimal

from steward.resolve import rewrite_entry

HASH = "5c5dceff8ff211b3b8144980cae8f1f8e5599f386b27a0fb09ff604bd1e3468e"
OTHER_HASH = "4adfb7ebc563f516566496e40fe92295b362448829aeb8ff22b8d8c2f45fa3ad"

ENTRY = f"""\
2026-02-05 Some Stranger | donation?  ; import-hash: {HASH}, review: unmatched
    assets:bank:checking          €50.00
    income:unknown               €-50.00
"""

OUTGOING_ENTRY = f"""\
2026-01-15 Bank Corp | account fee  ; import-hash: {HASH}, review: uncategorized
    expenses:unknown              €9.99
    assets:bank:checking         €-9.99
"""

NEIGHBOR = f"""\
2026-02-03 John Doe | dues  ; import-hash: {OTHER_HASH}, member-id: 1001
    assets:bank:checking            €600.00
    assets:receivables:dues        €-600.00

"""


def test_rewrite_unmatched_to_member_clearing() -> None:
    result = rewrite_entry(
        ENTRY,
        HASH,
        new_account="assets:receivables:dues",
        amount=Decimal("50.00"),
        member_id="1001",
        review=None,
    )
    assert result is not None
    header, *postings = result.splitlines()
    assert f"import-hash: {HASH}" in header
    assert "member-id: 1001" in header
    assert "review" not in header
    assert "Some Stranger | donation?" in header
    assert any("assets:receivables:dues" in p and "€-50.00" in p for p in postings)
    assert any("assets:bank:checking" in p and "€50.00" in p for p in postings)
    assert "income:unknown" not in result


def test_rewrite_uncategorized_to_category_account() -> None:
    result = rewrite_entry(
        OUTGOING_ENTRY,
        HASH,
        new_account="expenses:bank-fees",
        amount=Decimal("9.99"),
        member_id=None,
        review=None,
    )
    assert result is not None
    assert "expenses:bank-fees" in result
    assert "expenses:unknown" not in result
    assert "review" not in result.splitlines()[0]


def test_rewrite_to_new_review_reason_keeps_tag() -> None:
    result = rewrite_entry(
        ENTRY,
        HASH,
        new_account="assets:receivables:dues",
        amount=Decimal("50.00"),
        member_id="1001",
        review="partial",
    )
    assert result is not None
    header = result.splitlines()[0]
    assert "member-id: 1001" in header
    assert "review: partial" in header


def test_rewrite_preserves_neighbor_entries() -> None:
    content = NEIGHBOR + ENTRY
    result = rewrite_entry(
        content,
        HASH,
        new_account="income:donations",
        amount=Decimal("50.00"),
        member_id=None,
        review=None,
    )
    assert result is not None
    assert result.startswith(NEIGHBOR)


def test_rewrite_realigns_posting_amounts() -> None:
    result = rewrite_entry(
        ENTRY,
        HASH,
        new_account="assets:receivables:dues",
        amount=Decimal("50.00"),
        member_id="1001",
        review=None,
    )
    assert result is not None
    postings = result.splitlines()[1:]
    columns = [p.index("€") for p in postings]
    assert columns[0] == columns[1]


def test_unknown_hash_returns_none() -> None:
    assert (
        rewrite_entry(
            ENTRY,
            OTHER_HASH,
            new_account="income:donations",
            amount=Decimal("50.00"),
            member_id=None,
            review=None,
        )
        is None
    )


def test_amount_mismatch_returns_none() -> None:
    # the entry was hand-edited after import: the hash no longer matches content
    assert (
        rewrite_entry(
            ENTRY,
            HASH,
            new_account="income:donations",
            amount=Decimal("75.00"),
            member_id=None,
            review=None,
        )
        is None
    )


def test_extra_unknown_tag_returns_none() -> None:
    content = ENTRY.replace("review: unmatched", "review: unmatched, note: check this")
    assert (
        rewrite_entry(
            content,
            HASH,
            new_account="income:donations",
            amount=Decimal("50.00"),
            member_id=None,
            review=None,
        )
        is None
    )


def test_split_posting_shape_returns_none() -> None:
    content = ENTRY + "    income:donations            €-25.00\n"
    # three postings: not a Steward-generated two-posting block
    assert (
        rewrite_entry(
            content.replace("€-50.00", "€-25.00"),
            HASH,
            new_account="income:donations",
            amount=Decimal("50.00"),
            member_id=None,
            review=None,
        )
        is None
    )


def test_hash_on_non_header_line_returns_none() -> None:
    content = (
        "2026-02-05 Some Stranger | donation?\n"
        f"    ; import-hash: {HASH}, review: unmatched\n"
        "    assets:bank:checking          €50.00\n"
        "    income:unknown               €-50.00\n"
    )
    assert (
        rewrite_entry(
            content,
            HASH,
            new_account="income:donations",
            amount=Decimal("50.00"),
            member_id=None,
            review=None,
        )
        is None
    )
