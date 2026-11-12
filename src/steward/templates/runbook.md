# Treasury Runbook

How to run the association's treasury with Steward: the recurring routine, the correction policy, and the handover. This is the canonical runbook — `steward init` copies it into every treasury data repository.

**Audience:** the serving treasurer — and their successor. No hledger or Git expertise is assumed beyond the commands shown here.

## Contents

- [What you are looking at](#what-you-are-looking-at)
- [Tools](#tools)
- [Setup](#setup-once)
- [The routine](#the-routine)
  - [Book the dues claims](#once-a-year-book-the-dues-claims)
  - [Import a bank statement](#per-bank-statement-import)
  - [Resolve review flags](#resolve-review-flags)
  - [Commit](#commit--the-recording-ritual)
  - [Corrections](#corrections--read-this-before-touching-old-entries)
  - [Close the books](#once-a-year-close-the-books)
- [Handover](#handover)
- [Everyday queries](#everyday-queries)
  - [Outstanding dues by member](#outstanding-dues-by-member)
- [Privacy](#privacy)

## What you are looking at

The treasury is a **private Git repository** of plain-text files:

- `steward.yaml` — configuration (association name, currency, proration policy, closing options).
- `members.yaml` — member master data: fee tiers, membership periods, individual exceptions. How to record joins, resignations, tier changes, and fee changes is documented in the master-data reference in the Steward repository's README.
- `payer_aliases.yaml` — which bank payer names belong to which member (including third-party payers, e.g. a spouse).
- `categorization.yaml` — rules that categorize non-member transactions.
- `bank_formats.yaml` — how to read your bank's CSV export.
- `journals/` — the financial records, one hledger journal file per month by default (or per year if configured).
- `rules/steward.rules` — static hledger rules for the import write path; never edit.

Steward computes *what* to book; [hledger](https://hledger.org) owns the journals and does the accounting. Journal entries become immutable once committed — until then they are working state: you may add manual entries (`hledger add` or a text editor — e.g. cash transactions), and uncommitted entries may still change when review flags resolve (see below).

Payer aliases are case-insensitive regular expressions searched within the normalized payer name. Use `^John Doe$` for an exact match and `'J\. Doe'` for a literal period. Review existing aliases containing punctuation before importing: regex metacharacters have special meaning, and broad patterns may match the wrong payer. If patterns for multiple members match, Steward flags the payment as ambiguous.

## Tools

Install once: **Git**, **hledger**, and **Steward** — see the Installation section of the Steward repository's README. All commands run from the root of the treasury repository.

Financial postings currently use euros; selecting another currency code during setup does not enable non-EUR accounting.

## Setup (once)

```
steward init my-treasury
cd my-treasury
```

scaffolds a complete, valid repository and runs a first validation. Then:

1. Fill in `members.yaml` (fee tiers and members).
2. Map your bank's CSV export in `bank_formats.yaml` (column names, date and number formats).
3. Add payer aliases and categorization rules as they become known — the import routine below teaches you both over time.

Run `steward validate` after every configuration edit; it checks all files and the journals.

## The routine

### Once a year: book the dues claims

```
steward generate-dues --year 2026 --dry-run   # preview
steward generate-dues --year 2026
```

Books one dues claim per active member. Safe to re-run: unchanged members are skipped, and if master data changed after the run (a fee increase, a waiver), the next run books an **adjustment** for the difference — never a duplicate, never an edit. `--date` changes the booking date; don't use it to backdate a correction into a closed period.

### Per bank statement: import

```
steward import export.csv --format <your-bank> --dry-run   # preview
steward import export.csv --format <your-bank>
```

- Re-importing the same or an overlapping export is safe: known rows are counted as duplicates unless an eligible uncommitted review entry is re-classified.
- New rows land in the journal of their booking month by default, or booking year if configured.
- The report gives counts and lists flagged rows with their reasons up to 20; above that it summarizes them by reason.

The current `--dry-run` does not append transactions, but may create empty period files or directories, or a missing rules file. The dues-generation dry run has the same limitation and reports a summary rather than the rendered transactions.

### Resolve review flags

Steward never asks interactively; uncertain rows are imported with a `; review: <reason>` tag. List everything awaiting attention:

```
hledger -f journals/main.journal print tag:review
```

| Reason | Meaning | Resolution |
|--------|---------|------------|
| `unmatched` | Incoming payment, payer unknown | Add the payer to the member's aliases in `payer_aliases.yaml` (or a rule to `categorization.yaml`), then re-run the import — the entry resolves itself |
| `ambiguous` | Payer matches several members | Correct overlapping aliases in `payer_aliases.yaml` and re-run the import, or pick the member manually |
| `partial` | Payment below both the open balance and the dues calculated for its booking year (or below the open balance if those dues are zero) | The remainder stays open; review the installment and remove the tag to acknowledge |
| `overpaid` | Amount above the open balance | Decide: donation, next year's dues, or refund; adjust the entry accordingly |
| `uncategorized` | Outgoing payment matching no rule | Add a rule to `categorization.yaml`, then re-run the import — the entry resolves itself |

The learning loop: updating aliases or rules, then re-running `steward import` with the same export, re-classifies **uncommitted** `unmatched`, `uncategorized`, and `ambiguous` entries. The account and tags are updated when the classification changes; a `partial` or `overpaid` flag may still require attention. A first import of a long history converges in a few rule-and-rerun cycles. You can also edit an uncommitted flagged entry by hand; either way, resolution happens **before commit**. Once an entry is committed it is immutable: resolve it with a later adjustment entry (see Corrections).

### Commit — the recording ritual

```
steward commit
```

Validates everything, refuses while unresolved `review:` tags exist (`--force` overrides), stages all non-ignored changes, and commits the repository. Inspect the changes before committing: the scaffold ignores `*.csv`, but other sensitive exports or files may not be ignored. Use `steward commit --dry-run` to preview without staging or committing. If a configured push fails, the local commit still exists; fix the remote and push it separately. Commit whenever new entries exist — daily, weekly, at least monthly. Frequent small commits *are* the audit trail: fiscal regulations (GoBD-style) demand timely, tamper-evident recording, and Git provides it.

For stricter tamper-resistance, `steward.yaml` offers `closing.sign: true` (GPG-signed commits — proves *who* recorded) and `closing.remote` (push after commit — server-side timestamps outside your control). Setup for both is below.

#### Optional: GPG-signed commits

Signed commits help prove *who* recorded each commit. One-time setup:

1. **Create a key** if you don't have one: `gpg --full-generate-key` (RSA, at least 3072 bits; use your own name and email). List your keys with `gpg --list-secret-keys --keyid-format=long` and note the key ID (the part after `rsa3072/`).
2. **Tell Git to use it**: `git config --global user.signingkey <key-id>`.
3. **Enable signing** in `steward.yaml`: set `closing.sign: true`. From now on `steward commit` signs every commit; it fails if the key is missing or the passphrase can't be entered.
4. **Back up the key** (`gpg --export-secret-keys <key-id> > key.gpg`, stored safely). The key is *yours* — it is never handed over; a successor creates their own (see Handover).

#### Optional: push to a remote

Pushing after each commit adds server-side timestamps outside your control — and doubles as the off-site backup. One-time setup:

1. **Create a private repository** with your hosting provider (e.g. a private GitHub repository). Private, never public — the treasury contains personal data.
2. **Connect it**: `git remote add origin <url>` from the treasury repository, then `git push -u origin HEAD` once to establish the tracking branch.
3. **Enable pushing** in `steward.yaml`: set `closing.remote: "origin"`. From now on `steward commit` pushes after every commit.

Both options affect only future commits and can be enabled independently or together.

### Corrections — read this before touching old entries

**Never edit or delete a committed entry.** Not for an auditor's finding or a typo. The commit history is the audit trail; rewriting it destroys exactly what it proves.

A correction is a **new entry in the current period** that books the difference (an adjustment), dated today, explaining itself in the description. Use a text editor or `hledger -f <current-period-journal> add`. Steward's own commands follow the same rule.

A new late bank transaction is **not** a correction to an existing entry: importing it appends a row to its booking-period file, even if that file has been committed. If the fiscal year has already been closed, don't assume the closing and opening balances automatically update: `steward close` refuses to rerun. Review the impact with your accountant and make appropriate current-period adjustments.

### Once a year: close the books

```
steward close --year 2026
```

Runs the annual accounting close: income and expense accounts are zeroed into equity in the last period file, and opening balances carry into the new year. Once per fiscal year — never monthly. Use `steward close --year 2026 --dry-run` to preflight without changing journals. The command refuses to close a year twice; if a write fails, inspect the journals before retrying. Close does not commit: run `steward commit` afterward.

Close supports unpriced euro amounts with at most cent precision. It also refuses an earlier year if a later year has already been closed. Review late transactions and pending adjustments before closing.

## Handover

A successor takes over with:

1. A clone of this repository (plus access to the remote, if `closing.remote` is configured).
2. The tools: Git, hledger, Steward.
3. This runbook.

Smoke test for the new treasurer: `steward validate` must pass. If possible, walk through one full cycle together: import a statement, resolve flags, commit.

Hand over alongside the repository: online-banking access and any open `review:` flags with context. If commits are signed, the successor creates **their own** GPG key and configures it (`user.signingkey`) — signing keys are never transferred: a key proves *who* signed, and past commits stay verifiable against the previous treasurer's public key.

## Everyday queries

Steward delegates reporting to hledger. Run these commands from the treasury
repository root. They use the default journal path; if you changed `journals`
in `steward.yaml`, substitute that directory for `journals`.

```
hledger -f journals/main.journal bal --end tomorrow  # current balances of all accounts
hledger -f journals/main.journal bal assets:receivables:dues --end tomorrow  # total dues balance
hledger -f journals/main.journal print tag:member-id=1001  # one member's transactions
hledger -f journals/main.journal print tag:review  # everything awaiting resolution
hledger -f journals/main.journal reg assets:bank:checking  # bank account movements
steward validate  # full consistency check (also suitable for CI)
```

`--end tomorrow` includes today's postings but excludes future-dated entries.
Without an end date, hledger includes future-dated entries as well.

### Outstanding dues by member

Use Steward to show balances with member names and IDs:

```
steward report dues                        # through today
steward report dues --as-of 2026-12-31    # through this date, inclusive
```

The report uses the names currently in `members.yaml`, including for historical
dates. Positive balances are outstanding dues; negative balances are credits
or overpayments. Unknown IDs and untagged postings are shown with a warning.
Zero balances are omitted. The total is net of credits and unattributed amounts.

Alternatively, group the receivables account directly in hledger:

```
hledger -f journals/main.journal bal assets:receivables:dues --pivot member-id --end tomorrow
```

Look up the IDs in `members.yaml`. Positive balances are outstanding dues;
negative balances are credits or overpayments. Zero balances are omitted by
default. Untagged receivable postings cannot be attributed to a member by
this report and should be investigated.

For the balances at the end of 2026, use the next day as the exclusive end
date. For a month-by-month view of 2026, include historical balances so that
dues carried over from before the period are not omitted:

```
hledger -f journals/main.journal bal assets:receivables:dues --pivot member-id --end 2027-01-01
hledger -f journals/main.journal bal assets:receivables:dues --pivot member-id --monthly --historical --begin 2026-01-01 --end 2027-01-01
```

These are balances **as of** those dates, not a report of unpaid dues for a
specific dues year. Payments clear the member's total balance and are not
assigned to a year; filtering on the accrual's `year` tag would omit payments.
After annual close, the December 31 closing postings zero the receivable
balance, and the January 1 opening postings restore it. An as-of December 31
report therefore includes the closing postings; use a date in the new year
to see the carried-forward balance.

## Privacy

This repository contains personal data (member names, bank transactions). Keep it private: private hosting only, never a public remote, and think before sharing exports or journal files.
