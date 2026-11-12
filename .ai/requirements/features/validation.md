# PRD: Validation

> **Status:** Phase 1, implemented (2026-11-09).
> Canonical schemas: [../../docs/architecture.md](../../docs/architecture.md) §3 (all data formats), §9 (error handling & safety).
> **Known gap:** validation checks bank format definitions but does not parse a sample export, even when one is available. The sample-export check in §4 remains unmet.

## 1. Purpose

Give the treasurer — and CI — a single command that proves the data repository is internally consistent: well-formed YAML, referential integrity across files, and journals that pass hledger's own checks.

## 2. Command

```
steward validate
```

Exit code 0 when everything passes, non-zero otherwise; every failure is printed with file, location, and reason.

## 3. Schema Validation Approach

**Canonical schema = typed Python dataclasses, not a schema file.** YAML is parsed into dataclasses (`Member`, `TierEntry`, `FeeOverride`, …); validation is a set of named check functions over them.

Rationale (decided 2026-11-09):

- Most checks in §4 are **cross-field and cross-file** logic (period overlap, tier references, alias uniqueness across members) — declarative schema languages like JSON Schema express only structure, which is a minority of the rules.
- Error messages must name file, location, and reason (§2) — hand-written checks produce these directly; schema-validator errors would need a translation layer.
- No new dependency (`jsonschema`); dataclasses + PyYAML + stdlib suffice.
- **No drift**: a schema file plus Python cross-checks would be two canonical schemas. The dataclasses are the single internal model that `generate-dues` and `import` also operate on — `validate` is the front door to the same code, not a bolt-on.
- Each check function maps 1:1 to a rule in §4 and becomes a named test (per the testing strategy in AGENTS.md).

## 4. Checks

### 4.1 Schema & referential integrity (Steward)

- `members.yaml`: unique member IDs (never reused); `fee_tiers` entries per tier are ascending by `effective`; `tiers` entries reference defined `fee_tiers` keys and are ascending by `effective`; `membership` periods of one member do not overlap; `fee_overrides` have `start <= end` and do not overlap for the same member; all dates ISO `YYYY-MM-DD`; all amounts quoted decimal strings representing non-negative numbers (zero allowed, e.g. a full waiver; negative amounts are rejected).
- `payer_aliases.yaml`: every key references an existing member ID; alias regexes compile; no alias string assigned to two different members.
- `categorization.yaml`: every rule's `account` is well-formed; regexes compile.
- `bank_formats.yaml`: every mapping names a known parser adapter and maps all required internal fields (`date`, `amount`, `payee`, `description`, `account`); when a sample export is available, the mapping parses it without errors.
- `steward.yaml`: known keys; `journal_granularity`, `dues.proration`, `closing.*` values within their domains.

### 4.2 Journal checks (hledger)

- `hledger -f journals/main.journal check` must pass.
- `ordereddates` is **not** enabled — appends are not guaranteed to land in date order ([architecture.md §3.6](../../docs/architecture.md#36-journal-conventions)).

## 5. Usage Contexts

- Run manually before `steward commit` (which invokes it as step 1 — see [commit-and-close.md](commit-and-close.md)).
- Suitable for CI on every commit of the data repository.

## 6. Acceptance Criteria

General:

- A clean example repository (built from the `*.example.yaml` files) passes with exit code 0.
- All failures are reported in a single run (no fail-fast on the first error); each message names file, location, and reason; exit code is non-zero.

`members.yaml`:

- A member referencing an undefined tier fails, naming member, tier, and file.
- Overlapping `membership` periods fail, naming the member and the conflicting periods.
- Non-ascending `effective` entries fail — both in `fee_tiers` and in a member's `tiers`.
- Overlapping `fee_overrides` on the same member fail; an override with `start > end` fails.
- A negative amount (in `fee_tiers` or `fee_overrides`) fails; zero passes.
- A malformed date or an unquoted float amount fails with a type-level message.

Other files:

- An alias key with no matching member ID fails; the same alias string under two different members fails.
- An alias with an uncompilable regex fails with its member ID and list position.
- A `categorization.yaml` rule with an uncompilable regex fails.
- A `bank_formats.yaml` mapping naming an unknown parser adapter, or missing a required internal field, fails.
- A `steward.yaml` with an unknown key or an out-of-domain value (`journal_granularity`, `dues.proration`, `closing.*`) fails.
- A journal with an unbalanced transaction fails via `hledger check`.
