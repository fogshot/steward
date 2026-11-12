# PRD: Membership Dues Balance Report

> **Status:** Draft for review; not implemented.
> Canonical schemas and posting models: [../../docs/architecture.md](../../docs/architecture.md) §3.2 and §5.

## 1. Purpose

Show the treasurer each member's outstanding dues or credit by name and ID,
without requiring a manual lookup in `members.yaml`. hledger remains the
accounting engine: Steward requests balances from hledger and adds names from
the member master data. No separate name-alias file is maintained.

## 2. Command

```
steward report dues [--as-of YYYY-MM-DD] [--root PATH]
```

`--as-of` is an inclusive booking date. If omitted, it defaults to the current
local date. `--root` selects the treasury repository; its default follows the
existing CLI convention.

## 3. Inputs

- `steward.yaml` — the configured journals directory.
- `members.yaml` — member IDs and current names; see architecture §3.2.
- The configured `journals/main.journal` and its included journals — dues
  receivable postings, read through the supported hledger CLI.

## 4. Behavior

1. **Validate inputs.** Reject an invalid date, unreadable or invalid required
   configuration or member data, a missing journal, or an unavailable hledger
   binary with a useful error and a nonzero exit status. An empty but valid
   journal is not an error. Reporting does not require unrelated bank import
   configuration.
2. **Query hledger.** Request the balance of `assets:receivables:dues`,
   grouped by `member-id`, for all postings through the as-of date, inclusive.
   Include prior-period postings; exclude later postings. Use a structured
   hledger output format rather than parsing an aligned terminal display.
   The command translates the inclusive date to hledger's exclusive end date;
   reject a date that cannot be advanced by one day rather than overflowing.
3. **Enrich.** Join each nonzero member balance to `members.yaml` by exact ID.
   Use the name currently in the master data, even for an earlier as-of date.
   Do not filter by current membership status: former members may still owe
   dues or hold a credit. The ID remains visible to distinguish identical names.
4. **Report.** Print the as-of date, ID, name and signed balance for each
   nonzero member balance, sorted by ID, and the net receivable total. Positive
   amounts are outstanding dues; negative amounts are credits or overpayments.
   Monetary amounts are handled exactly, never as binary floats. A fully paid
   member with a zero balance is omitted. If there are no nonzero balances,
   state that explicitly and show a zero total.
5. **Account for unrecognized postings.** Include nonzero balances for unknown
   IDs and postings without an ID as visibly labeled separate rows, preserving
   their signed amounts in the net total. Warn that they require investigation;
   do not attribute them to a known member or silently omit them. The total
   includes all reported balances, including credits and unattributed amounts.
6. **Fail safely.** A failed hledger query, malformed or unsupported result,
   inconsistent totals, or unsupported commodity must produce an explicit
   error and nonzero exit status, not a partial or success-shaped report.
   The command is read-only and does not change journals or configuration.

Example presentation (exact spacing is not significant):

```text
Dues balances as of 2026-12-31
Member ID  Name       Balance
1001       John Doe   €120.00
1002       Jane Doe   €-20.00
Net receivable:       €100.00
```

## 5. Scope and Edge Cases

| Case | Behavior |
|------|----------|
| Payment or adjustment after the as-of date | Excluded, even if it is already recorded in the journal |
| Accrual before the requested date and payment on that date | Both included |
| Identical names | Separate rows identified by ID |
| Member has left | Include their nonzero balance |
| Unknown member ID or missing ID | Show the balance and an investigation warning, as specified in §4 |
| Credit or overpayment | Show the negative balance; include it in the net total |
| Multiple currencies or a non-EUR commodity | Fail explicitly; do not combine unlike amounts or present a partial total |
| Duplicate member IDs or invalid master data | Fail explicitly rather than choosing a name arbitrarily |

This is a **balance as of a booking date**, not a list of unpaid dues for a
specific dues year. Payments are not assigned to a year; see architecture
§5.2. Monthly breakdowns, payment allocation or aging by dues year, CSV export,
and reports covering other accounts are outside this initial feature.

## 6. Acceptance Criteria

- Two members with nonzero balances appear with their correct IDs, current
  names, signed amounts, and net total; members with zero balances do not.
- Accruals, adjustments and payments through the inclusive as-of date affect
  the result; transactions dated the next day do not. With no `--as-of`, the
  local current day is used and future-dated transactions are excluded.
- An earlier balance includes carried-over dues and uses the name currently
  in `members.yaml`; a former member's balance is still shown.
- Two members with the same name remain distinguishable by their IDs.
- A credit appears as a negative amount and reduces the net total.
- Unknown-ID and untagged nonzero balances are shown separately with warnings
  and included in the net total, which agrees with hledger's account balance.
- An empty valid journal reports no nonzero balances and a zero total.
- Invalid dates, invalid required input, missing journals, failed or malformed
  hledger output, inconsistent totals, and unsupported commodities fail with
  an actionable error and nonzero exit status.
- Running the report does not modify treasury files.
- The implementation's integration tests exercise the supported hledger binary
  with synthetic accruals, payments, adjustments, credits and unattributed
  postings; the user-facing runbook documents the command and date semantics.
