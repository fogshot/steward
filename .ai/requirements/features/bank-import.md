# PRD: Bank Statement Import

> **Status:** Phase 1, implemented (2026-11-09).
> Canonical schemas: [../../docs/architecture.md](../../docs/architecture.md) §3.3 (payer matching), §3.4 (categorization), §3.5 (bank formats), §5.2 (posting model), §6 (write path), §7.2 (dedup).

## 1. Purpose

Import bank statement exports into the journals without duplicates (even across overlapping re-exports), clearing member dues receivables where confidently matched and categorizing other rows where possible — flagging everything uncertain for manual review instead of asking interactively.

## 2. Command

```
steward import <bank-export> --format <mapping> [--dry-run]
```

## 3. Inputs

- The bank export file (CSV dialect described by the named mapping).
- `bank_formats.yaml` — column mapping, parser options, date/number formats, `reference_pattern` (§3.5).
- `payer_aliases.yaml` — payer → member matching (§3.3).
- `members.yaml` and `steward.yaml` — calculate the payment booking year's dues for the partial-payment review threshold (§3.3).
- `categorization.yaml` — ordered regex → account rules for non-member rows (§3.4).
- `journals/` — existing `import-hash` tags (dedup) and receivable balances (confidence check), read via hledger.

## 4. Behavior

1. **Parse** through the named format mapping into the internal transaction model: `date` (booking date, never value date), `amount` (signed `Decimal`), `currency`, `payee`, `description`, `reference`, `account`. Parsers must not assume row order.
2. **Sort** rows by date (presentation normalization for human-readable journals; not correctness).
3. **Deduplicate.** Compute `SHA-256(account | date | amount | payee | description-or-reference)` per row (normalization rules in §7.2; a stable end-to-end `reference` replaces `description` when extracted). Rows whose fingerprint already appears as an `; import-hash:` tag are duplicates — with one exception: a duplicate whose journal entry is **uncommitted** and flagged `review: unmatched | uncategorized` is re-classified against the current aliases/rules, and if it now resolves, the entry is rewritten in place (non-bank posting account and tags; amounts never change) and reported as resolved (decision #14). Committed entries and `partial`/`overpaid`/`ambiguous` flags are never re-resolved.
4. **Match & categorize** each surviving row (member matching applies to incoming payments only — an outgoing row to a member is a refund or similar, never a dues clearing):
   - A unique alias match books the clearing posting (§5.2) with `member-id`; the amount determines whether review is needed using the thresholds in architecture §3.3.
   - A payment below both the total outstanding balance and the positive dues for its booking year is flagged `partial`; a payment above the total balance is flagged `overpaid`. Multiple members matching → suspense account, no `member-id`, flagged `ambiguous`.
   - Non-member rows → first-match-wins against `categorization.yaml`; no match → suspense account (`expenses:unknown` / `income:unknown`, sign-dependent) + `; review: unmatched` (incoming — unknown payer, e.g. a third-party payer) or `; review: uncategorized` (outgoing — unknown expense). See decision #12.
   - The amount check compares against the member's running receivable balance: when one file contains several payments for the same member, each row is checked against the remainder after the preceding rows.
5. **Render** through the `hledger import` write path (§6), grouped by posting month.
6. **Report** to stdout as a run summary: parsed count, imported count, dropped-duplicate count, resolved count (re-resolved duplicates, step 3), the journal file(s) written (or that would be written, with `--dry-run`), and the flagged rows with their `review:` reasons. Imported and duplicate rows are **not** listed individually — they need no action. Flagged rows are listed individually up to a threshold (20), beyond which they are summarized by reason; the report then points to `hledger print tag:review`, the treasurer's working queue.

## 5. Edge Cases

| Case | Behavior |
|------|----------|
| Overlapping re-export (same rows, new filename/order) | All duplicates dropped; nothing appended |
| Re-import after adding a rule/alias | Uncommitted `unmatched`/`uncategorized` entries covered by the new rule/alias are rewritten in place and reported as resolved; committed entries stay flagged (resolve via adjustment) |
| Bank rewords descriptions between exports | Rows with a stable end-to-end reference still dedup; others may duplicate → manual resolution (known limitation, §7.2) |
| Installment payment (amount below both booking-year dues and total receivable) | Partial clearing booked, flagged `review: partial` |
| A full year's dues paid while older dues remain outstanding | Clearing booked without a `partial` flag; the remaining total receivable stays open |
| Overpayment (amount > receivable) | Flagged `review: overpaid`; treasurer decides donation / next period / refund |
| Third-party payer | `review: unmatched` until the payer is added to the member's aliases |
| Early payment of next year's dues | Clears the total balance into credit; absorbed by the next accrual (no period mapping, decision #7) |
| Transfer between two own accounts | `account` in the fingerprint prevents collision |

## 6. Acceptance Criteria

- Re-importing the same file: zero new transactions, report shows all rows as duplicates.
- Re-importing after adding a categorization rule: uncommitted `uncategorized` entries matching the rule are rewritten to the rule's account with the `review:` tag removed, reported as resolved, and `hledger check` still passes; committed entries are untouched.
- Re-importing after adding a payer alias: an uncommitted `unmatched` entry gains the `member-id` tag and the receivable-clearing posting account.
- A row whose payer alias matches and whose amount equals the member's receivable gets a `member-id` tag and a receivable-clearing posting, no `review:` tag.
- Alias regexes search the normalized payer name case-insensitively: a name pattern matches the same name with a title; patterns matching different members produce `review: ambiguous`.
- With €240 outstanding for two €120 years, a €120 payment is not flagged `partial`, while a €60 payment is; €120 remains outstanding after the full-year payment.
- If booking-year dues are zero, a payment below the outstanding balance is still flagged `partial`; overpayments remain compared with the total outstanding balance.
- An unknown payer lands in a suspense account with `review: uncategorized` or `review: unmatched` and appears in the report.
- The report shows counts (parsed/imported/duplicates), the written journal file(s), and flagged rows with their reasons — individually up to 20, summarized by reason beyond that.
- `--dry-run` prints the transactions and writes nothing.
- After a real run, `hledger check` passes (write-path gate).
