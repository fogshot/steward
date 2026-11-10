# Treasury Runbook

How to run the association's treasury with Steward: the recurring routine, the correction policy, and the handover. This is the canonical runbook — `steward init` copies it into every treasury data repository.

**Audience:** the serving treasurer — and their successor. No hledger or Git expertise is assumed beyond the commands shown here.

## What you are looking at

The treasury is a **private Git repository** of plain-text files:

- `steward.yaml` — configuration (association name, currency, proration policy, closing options).
- `members.yaml` — member master data: fee tiers, membership periods, individual exceptions. How to record joins, resignations, tier changes, and fee changes is documented in the master-data reference in the Steward repository's README.
- `payer_aliases.yaml` — which bank payer names belong to which member (including third-party payers, e.g. a spouse).
- `categorization.yaml` — rules that categorize non-member transactions.
- `bank_formats.yaml` — how to read your bank's CSV export.
- `journals/` — the financial records, one hledger journal file per month.
- `rules/steward.rules` — static hledger rules for the import write path; never edit.

Steward computes *what* to book; [hledger](https://hledger.org) owns the journals and does the accounting. Journal entries become immutable once committed — until then they are working state: you may add manual entries (`hledger add` or a text editor — e.g. cash transactions), and uncommitted entries may still change when review flags resolve (see below).

## Tools

Install once: **Git**, **hledger**, and **Steward** — see the Installation section of the Steward repository's README. All commands run from the root of the treasury repository.

## Setup (once)

```
steward init my-treasury
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

Books one dues claim per active member. Safe to re-run: unchanged members are skipped, and if master data changed after the run (a fee increase, a waiver), the next run books an **adjustment** for the difference — never a duplicate, never an edit.

### Per bank statement: import

```
steward import export.csv --format <your-bank> --dry-run   # preview
steward import export.csv --format <your-bank>
```

- Re-importing the same or an overlapping export is safe: known rows are dropped as duplicates and listed in the report.
- Every row lands in the journal of its booking month.
- The report lists imported rows, dropped duplicates, and every row flagged for review, with its reason.

### Resolve review flags

Steward never asks interactively; uncertain rows are imported with a `; review: <reason>` tag. List everything awaiting attention:

```
hledger print tag:review
```

| Reason | Meaning | Resolution |
|--------|---------|------------|
| `unmatched` | Incoming payment, payer unknown | Add the payer to the member's aliases in `payer_aliases.yaml` (or a rule to `categorization.yaml`), then re-run the import — the entry resolves itself |
| `ambiguous` | Payer matches several members | Pick the member manually |
| `partial` | Installment: amount below the open balance | Nothing to fix — the remainder stays open; remove the tag to acknowledge |
| `overpaid` | Amount above the open balance | Decide: donation, next year's dues, or refund; adjust the entry accordingly |
| `uncategorized` | Outgoing payment matching no rule | Add a rule to `categorization.yaml`, then re-run the import — the entry resolves itself |

The learning loop: adding an alias or rule, then re-running `steward import` with the same export, re-resolves every **uncommitted** flagged entry it covers — account fixed, `review:` tag removed — and makes future rows resolve themselves. A first import of a long history converges in a few rule-and-rerun cycles. You can also edit an uncommitted flagged entry by hand; either way, resolution happens **before commit**. Once an entry is committed it is immutable: resolve it with a later adjustment entry (see Corrections).

### Commit — the recording ritual

```
steward commit
```

Validates everything, refuses while unresolved `review:` tags exist (`--force` overrides), and commits the repository. Commit whenever new entries exist — daily, weekly, at least monthly. Frequent small commits *are* the audit trail: fiscal regulations (GoBD-style) demand timely, tamper-evident recording, and Git provides it.

For stricter tamper-resistance, `steward.yaml` offers `closing.sign: true` (GPG-signed commits — proves *who* recorded) and `closing.remote` (push after commit — server-side timestamps outside your control). Setup for both is below.

#### Optional: GPG-signed commits

Signed commits prove *who* recorded each period and make tags unforgeable. One-time setup:

1. **Create a key** if you don't have one: `gpg --full-generate-key` (RSA, at least 3072 bits; use your own name and email). List your keys with `gpg --list-secret-keys --keyid-format=long` and note the key ID (the part after `rsa3072/`).
2. **Tell Git to use it**: `git config --global user.signingkey <key-id>`.
3. **Enable signing** in `steward.yaml`: set `closing.sign: true`. From now on `steward commit` signs every commit; it fails if the key is missing or the passphrase can't be entered.
4. **Back up the key** (`gpg --export-secret-keys <key-id> > key.gpg`, stored safely). The key is *yours* — it is never handed over; a successor creates their own (see Handover).

#### Optional: push to a remote

Pushing after each commit adds server-side timestamps outside your control — and doubles as the off-site backup. One-time setup:

1. **Create a private repository** with your hosting provider (e.g. a private GitHub repository). Private, never public — the treasury contains personal data.
2. **Connect it**: `git remote add origin <url>` from the treasury repository, then `git push -u origin main` once to establish the tracking branch.
3. **Enable pushing** in `steward.yaml`: set `closing.remote: "origin"`. From now on `steward commit` pushes after every commit.

Both options affect only future commits and can be enabled independently or together.

### Corrections — read this before touching old entries

**Never edit or delete an entry in a committed period.** Not for a late bank statement, not for an auditor's finding, not for a typo. The commit history is the audit trail; rewriting it destroys exactly what it proves.

A correction is a **new entry in the current period** that books the difference (an adjustment), dated today, explaining itself in the description. Use `hledger add` or a text editor. Steward's own commands follow the same rule.

A late bank statement is **not** a correction: just import it. Its rows are appended to their booking month's file — appends are always allowed. If that month was already committed, the append shows up as an isolated Git diff, which is exactly what an auditor wants to see.

### Once a year: close the books

```
steward close --year 2026
```

Runs the annual accounting close: income and expense accounts are zeroed into equity in the last period file, and opening balances carry into the new year. Once per fiscal year — never monthly.

## Handover

A successor takes over with:

1. A clone of this repository (plus access to the remote, if `closing.remote` is configured).
2. The tools: Git, hledger, Steward.
3. This runbook.

Smoke test for the new treasurer: `steward validate` must pass. If possible, walk through one full cycle together: import a statement, resolve flags, commit.

Hand over alongside the repository: online-banking access and any open `review:` flags with context. If commits are signed, the successor creates **their own** GPG key and configures it (`user.signingkey`) — signing keys are never transferred: a key proves *who* signed, and past commits stay verifiable against the previous treasurer's public key.

## Everyday queries

Steward delegates reporting to hledger. Useful commands:

```
hledger bal assets:receivables:dues        # total outstanding dues
hledger print tag:member-id=1001           # everything booked for one member
hledger print tag:review                   # everything awaiting resolution
hledger reg assets:bank:checking           # bank account movements
steward validate                           # full consistency check (also suitable for CI)
```

## Privacy

This repository contains personal data (member names, bank transactions). Keep it private: private hosting only, never a public remote, and think before sharing exports or journal files.
