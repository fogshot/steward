# Steward

A command-line application to streamline association financial management through plain-text accounting, idempotent workflows, and effortless handovers.

Steward serves volunteer treasurers of small member associations. It tracks membership dues over time, imports bank statements and matches payments to members, and keeps auditable financial records — all in plain text files ([hledger](https://hledger.org) journals and YAML), version-controlled with Git.

> **Status:** Phase 1 commands are implemented. Known gaps against their specifications are noted in the relevant PRDs. The canonical design documents live in [.ai/](.ai/):
> - [High-level design](.ai/requirements/high-level-design.md) — vision, principles, roadmap, decision log
> - [Feature PRDs](.ai/requirements/features/) — per-command specifications
> - [Architecture](.ai/docs/architecture.md) — data schemas, journal conventions, write path
> - [Tech stack](.ai/docs/tech-stack.md) — technology choices and rationale

## Contents

- [Documentation map](#documentation-map)
- [Installation](#installation)
- [Commands](#commands)
- [User Manual: master data](#user-manual-master-data)
  - [Configuration reference](#configuration-reference-stewardyaml)
  - [Managing members](#managing-members)
  - [Fee structure change](#fee-structure-change)
  - [Individual exceptions & adjustments](#individual-exceptions--adjustments)

## Documentation map

- **README (this file)** — what Steward is, how to install it, and the master-data reference (`members.yaml` semantics).
- **[docs/runbook.md](docs/runbook.md)** — how to *operate* a treasury: the recurring routine, review-flag resolution, commit cadence, correction policy, and handover. `steward init` copies it into every treasury repository.

## Installation

Requirements: **Python ≥ 3.11**, **hledger 1.52.4** (the supported, CI-tested version), **Git**, and **uv** for the installation commands below.

```sh
git clone <this-repository> && cd steward
uv sync                 # creates .venv with all dependencies
uv run steward --help   # run via uv
```

For a permanent install, `uv tool install .` (or `pipx install .`) puts `steward` on your PATH.

## Commands

| Command | What it does |
|---------|--------------|
| `steward init [directory]` | Scaffold a new private treasury data repository |
| `steward validate` | Check all YAML files and journals for consistency |
| `steward generate-dues --year 2026` | Book annual membership-dues accruals (idempotent) |
| `steward import <export.csv> --format <mapping>` | Import a bank statement: dedup, match payers to members, categorize |
| `steward commit` | Validate, check review flags, and git-commit the treasury |
| `steward close --year 2026` | Run the annual accounting close via hledger |
| `steward report dues [--as-of YYYY-MM-DD]` | Show member dues balances with names |

`generate-dues`, `import`, `commit`, and `close` support `--dry-run`; `init` does not. The operational routine — when to run what, and how to resolve review flags — is in [docs/runbook.md](docs/runbook.md).

## User Manual: master data

The treasury's master data lives in `members.yaml` in the data repository (scaffolded by `steward init`). This section is the reference for keeping it correct over time.

### Configuration reference: `steward.yaml`

```yaml
association: "Musterverein e.V."
currency: "EUR"
journals: "journals"            # directory containing the journal files
journal_granularity: "monthly"  # monthly (default) | yearly
dues:
  proration: "monthly"          # monthly (default) | daily | none
closing:
  sign: false                   # GPG-sign commits made by `steward commit`
  remote: null                  # e.g. "origin" — push after each commit
```

The `closing.*` keys control tamper-resistance for `steward commit` (see the runbook for the setup walkthrough):

- `sign: true` — every commit is GPG-signed, proving *who* recorded it. Requires a GPG key configured in Git (`user.signingkey`); commit fails without one.
- `remote: "origin"` — after each commit, Steward pushes to the named remote, adding server-side timestamps outside the treasurer's control. Requires the remote to be configured in Git (`git remote add`).

Git commit timestamps alone are forgeable, so associations with stricter requirements should enable one or both. Both default to off and can be enabled at any time — they affect only future commits.

Financial postings and annual close currently support euros. Setting another
`currency` code during setup does not enable non-EUR accounting.

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
