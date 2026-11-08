# PRD: Commit & Close

> **Status:** Phase 1, specified — not yet implemented.
> Canonical spec: [../../docs/architecture.md](../../docs/architecture.md) §10 (committing, closing & tamper-evidence).

## 1. Purpose

Regulatory compliance (GoBD-style tamper-evidence, timeliness, traceability) comes from **Git, not hledger**. `steward commit` turns the recording ritual into one command so it actually happens on schedule; `steward close` performs the annual accounting close. Once-committed entries are never changed — only corrected through later adjustment entries.

## 2. Commands

```
steward commit [--force]
steward close --year 2026
```

## 3. `steward commit` Behavior

1. Run `steward validate` (all checks from [validation.md](validation.md)); abort on failure.
2. Check for unresolved `; review:` tags in the journals; refuse to commit while any exist, unless `--force` is given.
3. `git commit` the data repository with a standard message; optionally sign (`closing.sign: true` in `steward.yaml`) and push (`closing.remote`, e.g. `"origin"`).

**Cadence:** flexible — daily, weekly, at least monthly — whenever new entries exist. Frequent small commits keep the audit trail granular and reviewable.

**Tamper-resistance levels** (architecture.md §10.3): plain commits by default (history rewrites break the hash chain); optional GPG-signed commits/tags (proves *who* recorded); optional push to a remote (server-side timestamps outside the treasurer's control). Git commit timestamps alone are forgeable, so stricter associations enable signing and/or a remote.

## 4. `steward close` Behavior

Runs `hledger close` **once per fiscal year** — never monthly: closing transactions zero income/expense accounts into equity in the last period file, and opening balances carry assets/liabilities into the first file of the new year.

## 5. Corrections to Closed Periods

A late bank statement or an auditor's finding in an already-committed period is **never** fixed by editing that period's file — the correction is booked as an adjustment entry in the current period (posting model §5.3). The runbook must state this explicitly.

## 6. Acceptance Criteria

- `steward commit` with open `review:` tags refuses and lists them; with `--force` it commits.
- `steward commit` with failing validation aborts before touching Git.
- With `closing.sign: true`, the produced commit/tag is GPG-signed; with `closing.remote` set, the commit is pushed.
- `steward close --year 2026` appends closing entries to `journals/2026/12.journal` and opening balances to `journals/2027/01.journal` (monthly granularity), and `hledger check` passes afterwards.
