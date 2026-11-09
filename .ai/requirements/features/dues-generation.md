# PRD: Dues Generation

> **Status:** Phase 1, implemented (2026-11-09).
> Canonical schemas: [../../docs/architecture.md](../../docs/architecture.md) §3.2 (master data), §5.1/§5.3 (posting models), §6 (write path), §7.1 (idempotency).

## 1. Purpose

Generate annual membership-dues accruals from temporal master data, idempotently: re-running the command for the same year must never duplicate entries, and master-data changes after a run must produce adjustment entries, never edits.

## 2. Command

```
steward generate-dues --year 2026 [--date YYYY-MM-DD] [--dry-run]
```

`--date` overrides the booking date (default: the command's run date) — e.g. run at the end of January but book the accruals as of January 1st.

## 3. Inputs

- `members.yaml` — fee tiers, tier histories, membership periods, fee overrides ([architecture.md §3.2](../../docs/architecture.md#32-membersyaml--master-data)).
- `steward.yaml` — `dues.proration` policy.
- `journals/main.journal` — existing `(member-id, year)` tags and previously booked amounts (read via hledger).

## 4. Behavior

1. **Validate.** Run `steward validate` first; abort on any failure — dues must never be generated from invalid master data.
2. **Determine liability.** For each member: active during the target year (any `membership` period intersecting the calendar year)?
3. **Resolve the rate** for each liable member, per segment: active `fee_overrides` entry → applicable `tiers` entry → `fee_tiers` rate effective in that segment. All amounts are annual rates.
4. **Prorate** per `dues.proration` (`monthly` default: whole liable months / 12; `daily`; `none`) using the segment model: split the year at every boundary — join/leave, tier changes, override start/end — prorate each segment at its applicable annual rate, sum, round half-up to cents once.
5. **Pre-flight scan.** Collect all `(member-id, year)` tag pairs and their booked amounts across the journals. For each member:
   - No existing entry → book the accrual (posting model §5.1) — including €0.00 accruals for full-year waivers, so the `(member-id, year)` tag exists and re-runs stay idempotent.
   - Existing entry, same computed amount → skip.
   - Existing entry, different computed amount → book an **adjustment** for the difference (posting model §5.3), carrying the same `member-id`/`year` tags.
6. **Render** all bookings through the `hledger import` write path (§6), grouped by posting period. All bookings — accruals and adjustments alike — are **dated with the command's run date** (or `--date` when given): what counts is when the treasurer recorded the claim, not the period it refers to (the `year:` tag carries that).
7. **Report** to stdout: counts and amounts for members booked, skipped, adjusted, and waived.

## 5. Edge Cases

| Case | Behavior |
|------|----------|
| Member joined and left within the target year | Prorated over the liable months/days of the intersection |
| Re-joining member (multiple periods) | Each period prorated separately, summed |
| Override amount `0.00` covering the whole year (full waiver) | €0.00 accrual booked (keeps the `member-id`/`year` tag, so re-runs skip it and a later waiver removal triggers a normal adjustment); listed as waived in the report |
| Override covering part of the year | Override rate prorated over its window; tier rate prorated over the rest (segment model) |
| Member left in a prior year | Skipped entirely |
| Rate changed after accrual was booked | Adjustment entry for the difference, dated with the run date like any other booking |

## 6. Acceptance Criteria

Idempotency & adjustments:

- Running twice in a row: second run books nothing, reports all members as skipped.
- Changing a tier rate after the first run: next run books exactly one adjustment per affected member, for the difference only.
- Removing a full-year waiver after the first run: next run books an adjustment for the full computed amount.

Proration & segments:

- A member joining in April with `monthly` proration and a €120 annual rate is booked €90.00 (9/12 — April through December inclusive).
- A member joining in April and leaving in June with `monthly` proration and a €120 annual rate is booked €30.00 (3/12).
- A re-joining member (left 2024, re-joined July of the target year) with `monthly` proration and a €120 annual rate is booked €60.00 (6/12).
- A member with a €60/yr override covering Jan–Mar and a €120/yr tier rate is booked €105.00 (3/12 × €60 + 9/12 × €120).
- With `daily` proration, a member liable for exactly 100 days of a non-leap year at €365/yr is booked €100.00.
- With `none` proration, a member joining in December is booked the full annual fee.
- A member switching `regular` (€120/yr) → `reduced` (€60/yr) effective July 1st with `monthly` proration is booked €90.00.

Waivers & skipping:

- A member with a full-year €0.00 override is booked a €0.00 accrual carrying `member-id`/`year` tags, and is listed as waived in the report; the second run skips them.
- A member who left in a prior year is skipped entirely (no accrual, no report entry beyond the skipped count).

Booking & safety:

- All bookings are dated with the command's run date and land in the journal file of that period (e.g. run on 2026-03-15 → `journals/2026/03.journal` with monthly granularity); the `year:` tag carries the dues period.
- `--date 2026-01-01` books with that date instead, landing in `journals/2026/01.journal`.
- The command aborts when `steward validate` fails.
- The report lists counts and amounts for booked, skipped, adjusted, and waived members.
- `--dry-run` prints the transactions and writes nothing (via `hledger import --dry-run`).
- After a real run, `hledger check` passes (write-path gate).
