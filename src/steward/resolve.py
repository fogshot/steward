"""Review-resolution rewrites of uncommitted journal entries (decision #14).

The single scoped exception to "Steward never writes journal files": when a
re-imported bank row is now covered by current aliases/rules, its uncommitted
`review: unmatched | uncategorized` entry is rewritten in place — the non-bank
posting account and the header tags change, amounts never do.

rewrite_entry is a pure text transformation with strict shape validation: it
only touches Steward-generated two-posting blocks whose shape and amounts
match the bank row exactly. Anything unexpected returns None and the entry is
left for manual resolution.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_HEADER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.*?)\s*;\s*(\S.*)$")
_POSTING_RE = re.compile(r"^(\s+)(\S(?:.*?\S)?)\s{2,}(\S+)\s*$")
_TAG_RE = re.compile(r"^(import-hash|member-id|review):\s*(.*)$")
_KNOWN_TAGS = ("import-hash", "member-id", "review")

BANK_ACCOUNT = "assets:bank:checking"


def _parse_amount(token: str) -> Decimal | None:
    if not token.startswith("€"):
        return None
    try:
        return Decimal(token[1:])
    except InvalidOperation:
        return None


def rewrite_entry(
    content: str,
    import_hash: str,
    *,
    new_account: str,
    amount: Decimal,
    member_id: str | None,
    review: str | None,
) -> str | None:
    """Rewrite the entry carrying `import_hash` in `content`; None if unsafe.

    The block must be exactly: a transaction header whose trailing comment
    holds only known tags (import-hash matching, optional member-id/review),
    followed by exactly two postings — one to the bank account, one to the
    account being replaced — with equal, opposite amounts of `amount`.
    """
    lines = content.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if f"import-hash: {import_hash}" in line]
    if len(hits) != 1:
        return None
    header_index = hits[0]

    header = _HEADER_RE.match(lines[header_index].rstrip("\n"))
    if header is None:
        return None
    day, description, comment = header.groups()

    tags: dict[str, str] = {}
    for part in comment.split(", "):
        match = _TAG_RE.match(part.strip())
        if match is None:
            return None  # unknown tag or free text: leave for manual resolution
        tags[match.group(1)] = match.group(2).strip()
    if tags.get("import-hash") != import_hash:
        return None

    block_end = header_index + 1
    while block_end < len(lines) and lines[block_end].startswith((" ", "\t")):
        block_end += 1
    posting_lines = lines[header_index + 1 : block_end]
    if len(posting_lines) != 2:
        return None

    postings: list[tuple[str, str, str]] = []  # (indent, account, amount token)
    for line in posting_lines:
        posting = _POSTING_RE.match(line.rstrip("\n"))
        if posting is None:
            return None
        indent, account, token = posting.groups()
        if ";" in account or ";" in token:
            return None
        if _parse_amount(token) is None:
            return None
        postings.append((indent, account, token))

    bank = [p for p in postings if p[1] == BANK_ACCOUNT]
    other = [p for p in postings if p[1] != BANK_ACCOUNT]
    if len(bank) != 1 or len(other) != 1:
        return None
    first = _parse_amount(postings[0][2])
    second = _parse_amount(postings[1][2])
    if first is None or second is None:
        return None
    if abs(first) != abs(amount) or abs(second) != abs(amount):
        return None
    if first == 0 or (first < 0) == (second < 0):
        return None  # not a balanced +/- pair

    new_tags = [f"import-hash: {import_hash}"]
    if member_id is not None:
        new_tags.append(f"member-id: {member_id}")
    if review is not None:
        new_tags.append(f"review: {review}")
    new_header = f"{day} {description}  ; {', '.join(new_tags)}\n"

    replaced = [
        (indent, new_account if account != BANK_ACCOUNT else account, token)
        for indent, account, token in postings
    ]
    width = max(len(account) for _, account, _ in replaced) + 2
    new_postings = [
        f"{indent}{account.ljust(width)}{token}\n"
        for indent, account, token in replaced
    ]

    lines[header_index:block_end] = [new_header, *new_postings]
    return "".join(lines)
