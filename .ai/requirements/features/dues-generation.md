# PRD: Dues Generation

> **Status:** Phase 1, specified — not yet implemented.
> Canonical schemas: [../../docs/architecture.md](../../docs/architecture.md) §3.2 (master data), §5.1/§5.3 (posting models), §6 (write path), §7.1 (idempotency).

## 1. Purpose

Generate annual membership-dues accruals from temporal master data, idempotently: re-running the command for the same year must never duplicate entries, and master-data changes after a run must produce adjustment entries, never edits.

## 2. Command

```
steward generate-dues --year 2026 [--dry-run]
```

## 3. Inputs

- `members.yaml` — fee tiers, tier histories, membership periods, fee overrides ([architecture.md §3.2](../../docs/architecture.md#32-membersyaml--master-data)).
- `steward.yaml` — `dues.proration` policy.
- `journals/main.journal` — existing `(member-id, year)` tags and previously booked amounts (read via hledger).

## 4. Behavior

1. **Determine liability.** For each member: active during the target year (any `membership` period intersecting the calendar year)?
2. **Resolve the rate** for each liable member: active `fee_overrides` entry → applicable `tiers` entry → `fee_tiers` rate effective at the accrual date. All amounts are annual rates.
3. **Prorate** per `dues.proration` (`monthly` default: whole liable months / 12; `daily`; `none`) using the segment model: split the year at every boundary — join/leave, tier changes, override start/end — prorate each segment at its applicable annual rate, sum, round half-up to cents once.
4. **Pre-flight scan.** Collect all `(member-id, year)` tag pairs across the journals. For each member:
   - No existing entry → book the full accrual (posting model §5.1).
   - Existing entry, same computed amount → skip.
   - Existing entry, different computed amount → book an **adjustment** for the difference (posting model §5.3), carrying the same `member-id`/`year` tags.
5. **Render** all bookings through the `hledger import` write path (§6), grouped by posting period.
6. **Report** to stdout: members booked, skipped, adjusted, with amounts.

## 5. Edge Cases

| Case | Behavior |
|------|----------|
| Member joined and left within the target year | Prorated over the liable months/days of the intersection |
| Re-joining member (multiple periods) | Each period prorated separately, summed |
| Override amount `0.00` covering the whole year (full waiver) | No accrual booked; listed as waived in the report |
| Override covering part of the year | Override rate prorated over its window; tier rate prorated over the rest (segment model) |
| Member left in a prior year | Skipped entirely |
| Rate changed after accrual was booked | Adjustment entry for the difference, dated in the current period |

## 6. Acceptance Criteria

- Running twice in a row: second run books nothing, reports all members as skipped.
- Changing a tier rate after the first run: next run books exactly one adjustment per affected member, for the difference only.
- A member joining in April with `monthly` proration and a €120 annual rate is booked €80.00 (8/12).
- A member with a €60/yr override covering Jan–Mar and a €120/yr tier rate is booked €105.00 (3/12 × €60 + 9/12 × €120).
- `--dry-run` prints the transactions and writes nothing (via `hledger import --dry-run`).
- After a real run, `hledger check` passes (write-path gate).
