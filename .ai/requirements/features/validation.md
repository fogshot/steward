# PRD: Validation

> **Status:** Phase 1, specified — not yet implemented.
> Canonical schemas: [../../docs/architecture.md](../../docs/architecture.md) §3 (all data formats), §9 (error handling & safety).

## 1. Purpose

Give the treasurer — and CI — a single command that proves the data repository is internally consistent: well-formed YAML, referential integrity across files, and journals that pass hledger's own checks.

## 2. Command

```
steward validate
```

Exit code 0 when everything passes, non-zero otherwise; every failure is printed with file, location, and reason.

## 3. Checks

### 3.1 Schema & referential integrity (Steward)

- `members.yaml`: unique member IDs (never reused); `tiers` entries reference defined `fee_tiers` keys and are ascending by `effective`; `membership` periods of one member do not overlap; `fee_overrides` have `start <= end`; all dates ISO `YYYY-MM-DD`; all amounts quoted decimal strings.
- `payer_aliases.yaml`: every key references an existing member ID; no alias string assigned to two different members.
- `categorization.yaml`: every rule's `account` is well-formed; regexes compile.
- `bank_formats.yaml`: every mapping names a known parser adapter and maps all required internal fields (`date`, `amount`, `payee`, `description`, `account`); when a sample export is available, the mapping parses it without errors.
- `steward.yaml`: known keys; `journal_granularity`, `dues.proration`, `closing.*` values within their domains.

### 3.2 Journal checks (hledger)

- `hledger -f journals/main.journal check` must pass.
- `ordereddates` is **not** enabled — appends are not guaranteed to land in date order ([architecture.md §3.6](../../docs/architecture.md#36-journal-conventions)).

## 4. Usage Contexts

- Run manually before `steward commit` (which invokes it as step 1 — see [commit-and-close.md](commit-and-close.md)).
- Suitable for CI on every commit of the data repository.

## 5. Acceptance Criteria

- A member referencing an undefined tier fails with a message naming member, tier, and file.
- Overlapping `membership` periods fail, naming the member and the conflicting periods.
- An alias key with no matching member ID fails.
- A journal with an unbalanced transaction fails via `hledger check`.
- A clean example repository (built from the `*.example.yaml` files) passes with exit code 0.
