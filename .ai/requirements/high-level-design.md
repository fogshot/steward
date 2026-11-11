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
- **Fee overrides** are dated individual exceptions (waivers, custom rates) that take precedence over tier rates. Override amounts are annual rates prorated over their active window, like any other rate segment.
- **Proration** decides liability for partial years (join/leave mid-year). It is a global policy in `steward.yaml` — `monthly` (default), `daily`, or `none` — because it encodes the association's statutes, not a property of a fee tier. Example: with `monthly`, a member joining in April is liable for 9/12 of the annual fee (April through December inclusive).

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

- `steward.yaml` — association configuration (currency, journal granularity, proration, closing options). The fiscal year is the calendar year.
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
- **Phase 2: Monthly accrual cadence** (optional) — `dues.accrual_cadence: "annual" | "monthly"` controlling whether `generate-dues` books one claim per year or one per month. Changes posting dates and counts, not rate math (decision #11).
- **Future: configurable fiscal year** — Phase 1 assumes the fiscal year is the calendar year (no `fiscal_year_start` in `steward.yaml`). Supporting other fiscal years has wide-ranging implications that must be designed together: journal file layout (a fiscal year would span two calendar-year directories), accrual dates, `daily` proration denominators, the meaning of the `year:` tag, and closing file placement.
- **Phase 3: General Budgeting & Cost Centers**
  - Target vs. actual variance analysis using `hledger budget`.
  - Cost-center tagging for specific projects, clubhouses, or events.
- **Phase 4: Advanced Administrative Workflows**
  - Automated SEPA Direct Debit XML (`pain.008`) generation from outstanding dues.
  - Dunning and payment reminder generation.
- **Phase 4: DATEV Export** — hledger has no native DATEV support, so this is a Steward-owned export: journal transactions mapped to a DATEV-compatible CSV (e.g. `EXTF`-format booking batch) for handover to a tax advisor. Requires a configurable account mapping (Steward chart of accounts → DATEV chart, e.g. SKR03/04) in the data repo.

## 7. Decision Log

The canonical specification of each decision lives in the referenced document; this log preserves the *what* and the *why*.

| # | Decision | Spec |
|---|----------|------|
| 1 | **Proration** is a configurable global policy `dues.proration` in `steward.yaml`, default `monthly` (join in April → 9/12 of annual fee, April through December inclusive). Global by design — it encodes the association's statutes, not a property of a fee tier; per-tier override only if a real requirement appears. | [architecture.md §3.2](../docs/architecture.md#32-membersyaml--master-data) |
| 2 | **Tier changes** use an effective-dated `tiers` history per member, symmetric with `fee_tiers`. Mid-year changes compose with proration: split at tier boundaries, prorate each segment, sum, round once. | [architecture.md §3.2](../docs/architecture.md#32-membersyaml--master-data) |
| 3 | **Bank export formats** are handled by parser adapters + declarative column mappings in `bank_formats.yaml`; mappings express split debit/credit columns, multi-column fields, per-format date/number parsing, and reference extraction. | [architecture.md §3.5](../docs/architecture.md#35-bank-format-mappings-bank_formatsyaml) |
| 4 | **Payment matching** is hybrid auto-accept: a unique alias and matching amount book automatically; uncertain results carry a `; review: <reason>` tag and appear in a post-import report. The amount heuristic was refined by decision #15. No interactive UI. | [architecture.md §3.3](../docs/architecture.md#33-payer_aliasesyaml--bank-payer-matching) |
| 5 | **Real (personal) data** lives in a separate, private data repository per association; the public Steward repo carries only `*.example.yaml` files. `steward init` assists with setup. | [architecture.md §8](../docs/architecture.md#8-privacy--data-protection), [features/init.md](features/init.md) |
| 6 | **Commit & close**: `steward commit` wraps validate → review-check → git commit; commits are frequent and flexible (daily/weekly, at least monthly), decoupled from the annual accounting close (`steward close`, running `hledger close`). Tamper-resistance is configurable: plain commits by default, optional signed commits/tags and remote push. Once-committed entries are never changed, only corrected via later adjustment entries. | [architecture.md §10](../docs/architecture.md#10-committing-closing--tamper-evidence), [features/commit-and-close.md](features/commit-and-close.md) |
| 7 | **No per-period payment mapping**: payments clear the member's total `assets:receivables:dues` balance; money is fungible and per-period aging is derived from the `year` tags on *accruals*, not payments. Early payments drive the balance into credit until the next accrual absorbs it. | [architecture.md §5.2](../docs/architecture.md#5-posting-models) |
| 8 | **Booking date** is the transaction date. Value dates concern bank-internal interest settlement and are irrelevant at association scale; they are not recorded. | [architecture.md §3.5](../docs/architecture.md#35-bank-format-mappings-bank_formatsyaml) |
| 9 | **Row order**: parsers must not assume any row order (exports may be newest-first). Rows are sorted by date before rendering — not for correctness (hledger and the content-hash dedup are order-insensitive), but so journal files read chronologically for humans. | [architecture.md §3.5](../docs/architecture.md#35-bank-format-mappings-bank_formatsyaml) |
| 10 | **Categorization** lives in Steward, not in hledger rules files. Non-member rows are categorized by an ordered first-match-wins regex list in `categorization.yaml`; rows matching no rule book to a suspense account with a `review:` tag. `rules/steward.rules` stays fully static. Rationale: hledger rules are stateless (no balances, no aliases), would mix member-aware and content rules in one file, and could leak personal data into a shareable file. | [architecture.md §3.4](../docs/architecture.md#34-categorizationyaml--non-member-transaction-rules) |
| 11 | **Dues reference period stays annual** (decided 2026-11-09): all rates — including `fee_overrides` — are annual amounts; ISO dates everywhere (no `YYYY-MM` period keys). Overrides are prorated over their active window via the segment model (split at join/leave/tier/override boundaries, prorate each segment, sum, round once); overlapping overrides are invalid. A configurable reference period was rejected: it would introduce two date grammars in one file without eliminating the segment machinery. Monthly *accrual cadence* (booking twelve claims instead of one) may be added later without touching the schema. | [architecture.md §3.2](../docs/architecture.md#32-membersyaml--master-data) |
| 12 | **Review-reason split for rows matching nothing** (decided 2026-11-09): member matching applies to incoming payments only (an outgoing row to a member is a refund, never a dues clearing). An incoming row with no alias match and no categorization rule is flagged `review: unmatched` (unknown payer — resolution: add an alias); an outgoing row matching no rule is flagged `review: uncategorized` (unknown expense — resolution: add a rule). §3.3 and §3.4 left the split between the two suspense reasons open; the sign-based split makes the "third-party payer → unmatched" edge case and the §3.3 resolutions consistent. | [architecture.md §3.3](../docs/architecture.md#33-payer_aliasesyaml--bank-payer-matching) |
| 13 | **Pin hledger** (decided 2026-11-09): use a single tested version for reproducible journal operations and CI; older distribution packages can handle journal includes differently. | [tech-stack.md §hledger](../docs/tech-stack.md#hledger) |
| 13 | **`steward init` scaffolding contract** (decided 2026-11-09): the target directory must be empty (no `--force` — never merge into an existing treasury); non-interactive mode via `--no-input` or non-TTY stdin, with defaults and CLI options; prompts cover only association name, currency, and proration — `journal_granularity` and `closing.*` keep their safe defaults (a first-time treasurer cannot evaluate GPG signing or remotes; the runbook documents both). The scaffold is **initially committed** so the audit trail starts at birth; Git identity resolves from `--git-name`/`--git-email` → interactive prompt → configured `user.name`/`user.email`, failing with an error when none is available. The data repo's `.gitignore` covers OS artifacts and `*.csv` bank exports — Steward's own temp files never land there (enriched CSV lives in the OS temp dir), so no Steward-specific pattern exists. No public-remote detection: a fresh `git init` has no remote to inspect and visibility is not locally determinable — `init` prints a privacy notice instead. | [features/init.md](features/init.md) |
| 14 | **Immutability boundary = committed, not written** (decided 2026-11-10): uncommitted journal entries are working state and may be rewritten; only once `steward commit` records them in Git do they become immutable (corrections to committed entries stay adjustment entries, §5.3/§10.4). `steward import` re-resolves uncommitted entries in place when re-processed rows are covered by updated aliases/rules — amounts never change, only the non-bank posting account and tags. Rationale: first imports of long histories produced dozens of review flags, and resolving each by hand after adding a rule was pure toil — users worked around it by deleting journal files and re-importing. The commit command (decision #6) already exists as the frequent-use gate, so the Git analogy — only published history is sacred — fits the workflow. | [architecture.md §3.6, §6, §7.2](../docs/architecture.md#36-journal-conventions), [features/bank-import.md](features/bank-import.md) |
| 15 | **Partial-payment review** (decided 2026-11-11): compare a payment with the dues calculated for its booking year as well as the total receivable, rather than flagging a full year's payment merely because older dues remain. Preserve total-balance overpayment checks and fungible payment postings (decision #7). | [architecture.md §3.3](../docs/architecture.md#33-payer_aliasesyaml--bank-payer-matching) |
| 16 | **Payer aliases are regex searches** (decided 2026-11-11): search the payer name for each alias pattern rather than requiring exact equality, allowing titles or other bank-supplied text. Overlapping matches remain ambiguous; existing aliases with regex metacharacters must be reviewed. | [architecture.md §3.3](../docs/architecture.md#33-payer_aliasesyaml--bank-payer-matching) |
