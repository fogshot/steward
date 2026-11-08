# High-Level Design — Steward

> **Scope of this document:** product-level vision, principles, core concepts, roadmap, and the decision log.
> - Feature specifications (PRDs): [features/](features/)
> - Canonical data schemas, file formats, and posting models: [../docs/architecture.md](../docs/architecture.md)
> - Technology choices and rationale: [../docs/tech-stack.md](../docs/tech-stack.md)
>
> Maintenance rule: keep each fact in exactly one of these documents; cross-reference instead of duplicating.

## 1. Vision

**Steward** is a command-line application built to streamline association financial management through plain-text accounting, idempotent workflows, and effortless handovers.

It serves volunteer treasurers of small member associations (e.g. German *eingetragene Vereine*) who need to:

- track membership dues over time,
- import bank statements and match payments to members,
- keep auditable financial records,
- hand over the office to a successor with minimal friction.

### Non-goals

- No GUI, no database server, no cloud service.
- Steward is not a full bookkeeping suite: it generates and maintains [hledger](https://hledger.org) journals; hledger performs the accounting and reporting.
- No direct tax filing (e.g. ELSTER). Export formats for tax advisors are in scope — see DATEV on the roadmap (§6).

## 2. Design Principles

1. **Auditability** — Every financial movement — from annual membership claims (accruals) to incoming bank wire transfers — is recorded as an immutable double-entry transaction using **hledger**. Journal entries are appended, never edited; corrections are expressed as adjustment entries.
2. **Plain text** — All master data, financial records, and documentation live in human-readable files (`.yaml`, `.journal`), inspectable with any text editor and version-controlled via **Git**.
3. **Low bus factor (handover-friendly)** — No hidden database state, proprietary apps, or complex GUI dependencies. A successor takes over the treasury by cloning the repository and reading a short runbook.
4. **Separation of concerns** — Master data (`members.yaml`) is strictly decoupled from financial history (`.journal` files), preventing state duplication and historical drift.
5. **Idempotency & safety** — Every mutating command is safe to re-run: it computes a delta against existing state and appends only what is missing. Commands never overwrite existing entries, and all mutating commands support `--dry-run`.
6. **hledger-first delegation** — Anything hledger's CLI already does well — rendering and appending transactions, validation, reporting, manual entry — is delegated to hledger, not reimplemented. Steward computes only what hledger cannot: temporal master-data logic, payer matching, and dedup fingerprints. Steward never writes journal files directly.

## 3. Core Concepts

### 3.1 Temporal master data

All membership-related data is modeled as time ranges, so a single `members.yaml` covers the entire history without per-year copies:

- **Fee tiers** are effective-dated price lists (e.g. `regular` costs 100.00 from 2020-01-01, 120.00 from 2026-01-01).
- **Tier history** — each member's tier assignments are effective-dated too, symmetric with `fee_tiers`, so a member switching tiers mid-year keeps stable historical calculations.
- **Membership** is a list of `joined`/`left` periods, supporting resignations and re-joins.
- **Fee overrides** are dated individual exceptions (waivers, custom rates) that take precedence over tier rates.
- **Proration** decides liability for partial years (join/leave mid-year). It is a global policy in `steward.yaml` — `monthly` (default), `daily`, or `none` — because it encodes the association's statutes, not a property of a fee tier. Example: with `monthly`, a member joining in April is liable for 8/12 of the annual fee.

### 3.2 Accrual-based dues tracking

Dues are booked as claims when they arise: debit `assets:receivables:dues`, credit `income:dues`. Incoming payments later clear the receivable. "Who owes what?" is therefore answerable at any time with a plain hledger query.

### 3.3 Append-only journals

Financial history lives in hledger journal files — one file per month by default, grouped in year directories. Monthly granularity supports fiscal regulations that require timely recording and a tamper-evident audit trail: each month is closed and committed to Git, so the commit history doubles as the regulatory audit trail. Journal entries are appended, never edited — and Steward never writes journal files directly: every generated transaction is rendered and appended by `hledger import` (see [architecture.md §6](../docs/architecture.md#6-write-path-hledger-import)). Machine-readable metadata tags (`member-id:`, `year:`, `import-hash:`, `review:`) make every generated entry traceable and enable idempotent re-runs.

## 4. Features

Phase 1 (current focus) — each feature has a dedicated PRD:

| Feature | Command(s) | PRD |
|---------|-----------|-----|
| Dues generation | `steward generate-dues` | [features/dues-generation.md](features/dues-generation.md) |
| Bank statement import | `steward import` | [features/bank-import.md](features/bank-import.md) |
| Validation | `steward validate` | [features/validation.md](features/validation.md) |
| Commit & close | `steward commit`, `steward close` | [features/commit-and-close.md](features/commit-and-close.md) |
| Assisted treasury setup | `steward init` | [features/init.md](features/init.md) |

## 5. Data Schemas (Overview)

Canonical definitions live in [architecture.md §3](../docs/architecture.md#3-data-formats--schemas).

- `steward.yaml` — association configuration (currency, journal granularity, proration, closing options).
- `members.yaml` — effective-dated `fee_tiers`; `members` with `id`, `name`, effective-dated `tiers` history, `membership` periods, `fee_overrides`.
- `payer_aliases.yaml` — maps payer names/aliases from bank transactions to member IDs.
- `categorization.yaml` — ordered regex → account rules for non-member transactions (bank fees, vendors, other income/expenses).
- `bank_formats.yaml` — declarative mappings from bank-specific CSV dialects to Steward's internal transaction model.
- `journals/YYYY/MM.journal` — monthly hledger journals (default granularity); `journals/main.journal` includes them all via a static glob.
- `rules/steward.rules` — static hledger CSV rules used by the write path.

## 6. Future Expansion Roadmap

- **Phase 2: Automated Financial Reporting** — the annual treasurer's report for the general assembly:
  - Automated compilation of opening/closing bank balances, category-wise income/expenses, and member counts.
  - Sequential receipt numbering and internal voucher (self-receipt) generation.
- **Phase 3: General Budgeting & Cost Centers**
  - Target vs. actual variance analysis using `hledger budget`.
  - Cost-center tagging for specific projects, clubhouses, or events.
- **Phase 4: Advanced Administrative Workflows**
  - Automated SEPA Direct Debit XML (`pain.008`) generation from outstanding dues.
  - Dunning and payment reminder generation.
- **Phase 4: DATEV Export** — hledger has no native DATEV support, so this is a Steward-owned export: journal transactions mapped to a DATEV-compatible CSV (e.g. `EXTF`-format booking batch) for handover to a tax advisor. Requires a configurable account mapping (Steward chart of accounts → DATEV chart, e.g. SKR03/04) in the data repo.

## 7. Decision Log

All design decisions to date (2026-11-08). The canonical specification of each decision lives in the referenced document; this log preserves the *what* and the *why*.

| # | Decision | Spec |
|---|----------|------|
| 1 | **Proration** is a configurable global policy `dues.proration` in `steward.yaml`, default `monthly` (join in April → 8/12 of annual fee). Global by design — it encodes the association's statutes, not a property of a fee tier; per-tier override only if a real requirement appears. | [architecture.md §3.2](../docs/architecture.md#32-membersyaml--master-data) |
| 2 | **Tier changes** use an effective-dated `tiers` history per member, symmetric with `fee_tiers`. Mid-year changes compose with proration: split at tier boundaries, prorate each segment, sum, round once. | [architecture.md §3.2](../docs/architecture.md#32-membersyaml--master-data) |
| 3 | **Bank export formats** are handled by parser adapters + declarative column mappings in `bank_formats.yaml`; mappings express split debit/credit columns, multi-column fields, per-format date/number parsing, and reference extraction. | [architecture.md §3.5](../docs/architecture.md#35-bank-format-mappings-bank_formatsyaml) |
| 4 | **Payment matching** is hybrid auto-accept: high-confidence matches (unique alias **and** amount equals the outstanding receivable) book automatically; everything else is imported with a `; review: <reason>` tag and listed in a post-import report. No interactive UI. | [architecture.md §3.3](../docs/architecture.md#33-payer_aliasesyaml--bank-payer-matching) |
| 5 | **Real (personal) data** lives in a separate, private data repository per association; the public Steward repo carries only `*.example.yaml` files. `steward init` assists with setup. | [architecture.md §8](../docs/architecture.md#8-privacy--data-protection), [features/init.md](features/init.md) |
| 6 | **Commit & close**: `steward commit` wraps validate → review-check → git commit; commits are frequent and flexible (daily/weekly, at least monthly), decoupled from the annual accounting close (`steward close`, running `hledger close`). Tamper-resistance is configurable: plain commits by default, optional signed commits/tags and remote push. Once-committed entries are never changed, only corrected via later adjustment entries. | [architecture.md §10](../docs/architecture.md#10-committing-closing--tamper-evidence), [features/commit-and-close.md](features/commit-and-close.md) |
| 7 | **No per-period payment mapping**: payments clear the member's total `assets:receivables:dues` balance; money is fungible and per-period aging is derived from the `year` tags on *accruals*, not payments. Early payments drive the balance into credit until the next accrual absorbs it. | [architecture.md §5.2](../docs/architecture.md#5-posting-models) |
| 8 | **Booking date** is the transaction date. Value dates concern bank-internal interest settlement and are irrelevant at association scale; they are not recorded. | [architecture.md §3.5](../docs/architecture.md#35-bank-format-mappings-bank_formatsyaml) |
| 9 | **Row order**: parsers must not assume any row order (exports may be newest-first). Rows are sorted by date before rendering — not for correctness (hledger and the content-hash dedup are order-insensitive), but so journal files read chronologically for humans. | [architecture.md §3.5](../docs/architecture.md#35-bank-format-mappings-bank_formatsyaml) |
| 10 | **Categorization** lives in Steward, not in hledger rules files. Non-member rows are categorized by an ordered first-match-wins regex list in `categorization.yaml`; unmatched rows book to a suspense account flagged `review: uncategorized`. `rules/steward.rules` stays fully static. Rationale: hledger rules are stateless (no balances, no aliases), would mix member-aware and content rules in one file, and could leak personal data into a shareable file. | [architecture.md §3.4](../docs/architecture.md#34-categorizationyaml--non-member-transaction-rules) |
