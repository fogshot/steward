# Steward

A command-line application to streamline association financial management through plain-text accounting, idempotent workflows, and effortless handovers.

Steward serves volunteer treasurers of small member associations. It tracks membership dues over time, imports bank statements and matches payments to members, and keeps auditable financial records — all in plain text files ([hledger](https://hledger.org) journals and YAML), version-controlled with Git.

> **Status:** design draft. The canonical design documents live in [.ai/](.ai/):
> - [High-level design](.ai/requirements/high-level-design.md) — vision, principles, roadmap, decision log
> - [Feature PRDs](.ai/requirements/features/) — per-command specifications
> - [Architecture](.ai/docs/architecture.md) — data schemas, journal conventions, write path
> - [Tech stack](.ai/docs/tech-stack.md) — technology choices and rationale

Treasurer-facing documentation: [docs/runbook.md](docs/runbook.md) — the operational runbook (routine, correction policy, handover).

## User Manual

### Managing members

#### A member joins

Add a new entry in `members.yaml` under `members`. The first `tiers` entry should match the `joined` date:

```yaml
- id: "1001"
  name: "John Doe"
  tiers:
    - effective: "2022-01-01"
      tier: "regular"
  membership:
    - joined: "2022-01-01"
```

#### A member resigns / dies

Do *not* delete the member. Update their active period with a `left` date:

```yaml
membership:
  - joined: "2022-01-01"
    left: "2026-06-30"
```

- When generating dues for 2027 onward, Steward skips this member entirely.
- Past journal entries stay intact and verifiable against historical data.

#### A member switches fee tier

Add a new entry to their `tiers` history — never edit past entries:

```yaml
tiers:
  - effective: "2024-01-01"
    tier: "regular"
  - effective: "2025-07-01"   # switched to reduced, e.g. became a student
    tier: "reduced"
```

Mid-year switches are prorated per segment (see `dues.proration` in `steward.yaml`).

### Fee structure change

When the general assembly votes to raise fees (e.g. from 2025 onward), add a new effective entry under `fee_tiers` with the starting date of the new fee:

```yaml
reduced:
  - effective: "2020-01-01"
    amount: "343.33"
  - effective: "2025-01-01"
    amount: "400.00"
```

- You don't need to edit individual member records — their tier automatically picks up the new rate for any accrual calculation starting on or after the effective date.
- Amounts are quoted decimal strings, never floats.

### Individual exceptions & adjustments

Use `fee_overrides` for a specific member. Steward checks for an active override for the target date before falling back to the tier rate. Override amounts are annual rates, prorated over their active window — an override covering only part of a year applies only to that part:

```yaml
fee_overrides:
  - start: "2025-01-01"
    end: "2025-12-31"
    amount: "0.00"           # 100% exemption for 2025 (e.g. student waiver)
    reason: "Board approval 2024-11-15"
```
