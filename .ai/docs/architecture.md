# Architecture

> **Scope of this document:** canonical system structure — components, directory layout, data schemas, journal conventions, posting models, and the write path.
> - Vision, principles, roadmap, decision log: [../requirements/high-level-design.md](../requirements/high-level-design.md)
> - Feature specifications (PRDs): [../requirements/features/](../requirements/features/)
> - Technology choices and rationale: [tech-stack.md](tech-stack.md)

## 1. System Overview

Steward is a file-processing CLI that **orchestrates hledger**. It reads YAML master data and bank exports, computes *what* must be booked, and delegates *all journal writes, validation, and reporting* to the `hledger` binary.

```
            ┌───────────────────────────────────────────┐
            │               steward (CLI)               │
            │   generate-dues │ import │ validate       │
            └───────┬─────────────────────┬─────────────┘
                    │ reads               │ enriched CSV (temp file)
        ┌───────────▼──────────┐   ┌──────▼────────────────────────┐
        │  Plain-text files    │   │ hledger (binary)              │
        │  steward.yaml        │   │  import → append transactions │
        │  members.yaml        │   │  check / print / reg / bal    │
        │  payer_aliases.yaml  │   └──────┬────────────────────────┘
        │  categorization.yaml │          │ appends
        │  bank_formats.yaml   │
        │  rules/steward.rules │
        │  journals/*/*.journal│◄─────────┘
        └──────────────────────┘
     journals/ is written exclusively by hledger
```

**Core principle: Steward decides *what* to book; hledger owns *how* it is written.** Steward never opens a journal file for writing. Every generated transaction — dues accruals, adjustments, imported bank rows — enters the journal through `hledger import` (§6).

### Responsibility matrix

| Task | Owner | Mechanism |
|------|-------|-----------|
| Parse bank exports | Steward | bank-specific parser |
| Normalize rows, compute fingerprints | Steward | content hash (§7.2) |
| Match payer → member | Steward | `payer_aliases.yaml` (§3.3) |
| Compute dues, deltas, adjustments | Steward | temporal master data (§3.2) |
| Render & append transactions | **hledger** | `hledger import` + static rules file (§6) |
| Validate journals | **hledger** | `hledger check` |
| Reports & queries | **hledger** | `bal`, `reg`, `print`, … (Phase 2 reporting wraps these) |
| Manual one-off entries | **hledger** | `hledger add` — no Steward code needed |
| Commit (tamper-evidence) | Steward | `steward commit` wraps validate → review-check → `git commit` (§10) |
| Annual accounting close | **hledger** | `steward close` runs `hledger close` (§10) |

Rationale: hledger is the single owner of journal syntax and formatting, so Steward can never emit an invalid or misformatted journal. Anything hledger's CLI already does well is not reimplemented in Python.

## 2. Directory Layout

```
<association-treasury>/         # separate, private Git repository (see §8)
├── steward.yaml            # configuration (see 3.1)
├── members.yaml            # master data (see 3.2)
├── payer_aliases.yaml      # payer alias → member mapping (see 3.3)
├── categorization.yaml     # regex → account rules for non-member rows (see 3.4)
├── bank_formats.yaml       # bank CSV dialect → internal model mappings (see 3.5)
├── rules/
│   └── steward.rules       # static hledger CSV rules for the write path (§6)
├── journals/
│   ├── main.journal        # static: `include **.journal`; default entry point for hledger
│   ├── 2025/
│   │   └── …
│   └── 2026/
│       ├── 01.journal      # one file per month (default granularity)
│       ├── 02.journal
│       └── …
└── docs/
    └── runbook.md          # handover runbook (per-association)
```

## 3. Data Formats & Schemas

### 3.1 `steward.yaml` — configuration

```yaml
association: "Musterverein e.V."
currency: "EUR"
journals: "journals"          # directory containing the journal files
journal_granularity: "monthly"  # monthly (default) | yearly
dues:
  proration: "monthly"        # monthly (default) | daily | none
closing:
  sign: false                 # GPG-sign closing commits/tags
  remote: null                # e.g. "origin" — push after each closing commit
```

### 3.2 `members.yaml` — master data

Canonical field names: `membership` (list of periods), `tiers` (effective-dated tier history), `fee_overrides` (list of dated exceptions). See [members.example.yaml](../../members.example.yaml) for a complete example.

```yaml
fee_tiers:
  regular:
    - effective: "2020-01-01"
      amount: "100.00"
    - effective: "2026-01-01"
      amount: "120.00"

members:
  - id: "1001"                # unique string; quoted to preserve leading zeros
    name: "John Doe"
    tiers:                    # effective-dated tier history, ascending by `effective`
      - effective: "2022-01-01"   # first entry should match the first `joined` date
        tier: "regular"           # must reference a key of fee_tiers
    membership:               # list of active periods; supports re-joins
      - joined: "2022-01-01"
        left: null            # null = still active; omitting `left` is equivalent
    fee_overrides:            # optional; take precedence over tier rates
      - start: "2025-01-01"   # inclusive
        end: "2025-12-31"     # inclusive
        amount: "0.00"
        reason: "Board approval 2024-11-15"
```

Rules:

- All dates are quoted ISO `YYYY-MM-DD` strings.
- All amounts are **quoted decimal strings**, never YAML floats, to avoid binary floating-point artifacts (e.g. `343.33`). They are parsed as `Decimal` and must be **non-negative** (zero allowed, e.g. a full waiver; negative amounts are rejected by `steward validate`).
- `id` values are unique across all members, forever — IDs of departed members are never reused.
- The `membership` periods of one member must not overlap.
- `fee_tiers` entries per tier are ascending by `effective`; the applicable rate for a date is the latest entry with `effective <= date`.
- `tiers` is an effective-dated history, symmetric with `fee_tiers`: entries are ascending by `effective`, and the applicable tier for a date is the latest entry with `effective <= date`. A member switching from `regular` to `reduced` gets a new entry; past entries are never edited, so historical recalculations stay stable.
- Rate resolution for an accrual date: active `fee_overrides` entry → member's applicable `tiers` entry → latest `fee_tiers` entry for that tier with `effective <= accrual date`.
- **All amounts are annual rates**, including `fee_overrides` amounts. The reference period for dues is one year; there is no per-month rate unit (decision #11 in the HLD).
- **Segment model** — the year's dues are computed by splitting the year at every boundary date — join/leave, tier changes, override start/end — and prorating each segment at its applicable annual rate by the configured policy; segments are summed and the total is rounded half-up to cents once. Examples (`monthly` proration):
  - Tier change: `regular` (€120/yr) → `reduced` (€60/yr) effective 2026-07-01 → 6/12 × €120 + 6/12 × €60 = €90.00.
  - Partial override: €60/yr override covering Jan–Mar, `regular` €120/yr otherwise → 3/12 × €60 + 9/12 × €120 = €105.00.
- **Proration** for partial years is governed by `dues.proration` in `steward.yaml` (§3.1):
  - `monthly` (default): liability is counted in whole calendar months. Joining on any day in April makes April liable → 8/12 of the annual fee; leaving in June makes the member liable through June → 6/12.
  - `daily`: exact liable days / days in year.
  - `none`: full annual fee regardless of join/leave date (statutes with "full fee on entry" clauses).
  - The policy is global by design: it encodes the association's statutes, not a property of a fee tier. If a real requirement for per-tier policies ever appears, an optional `proration` key on a tier can override the global default without breaking the schema.
- **`fee_overrides`** entries are annual rates prorated over their active window (`start`–`end`, inclusive) via the segment model — they are not lump sums, and not prorated beyond their window. A full-year waiver (`amount: "0.00"` covering the whole year) therefore yields dues of €0. Overlapping overrides on the same member are invalid (rejected by `steward validate`).

### 3.3 `payer_aliases.yaml` — bank payer matching

```yaml
aliases:
  "1001":                     # member id from members.yaml
    - "John Doe"
    - "J. Doe"
    - "Doe, John"
    - "Jane Doe"              # third-party payer (spouse) paying for 1001
```

- Keys must reference existing member IDs. Member names are **not** duplicated here — `members.yaml` is the single source of truth for names.
- Aliases map **payers**, not just members: a third party paying on a member's behalf (e.g. a parent or spouse) is added to that member's alias list.
- Matching is case-insensitive, ignores extra whitespace, and compares against the payee/sender field of a bank transaction.

**Match confidence** (decision #4 in the HLD) — matching is automatic, with low-confidence results flagged for review instead of confirmed interactively:

- **High confidence** — exactly one member matches **and** the amount equals that member's total outstanding dues receivable (across all periods, per decision #7): the clearing posting (§5.2) is booked automatically.
- **Low confidence** — the row is still imported, but carries a `; review: <reason>` tag and appears in a post-import report:

  | `review` value | Condition | Typical resolution |
  |----------------|-----------|--------------------|
  | `unmatched` | No alias matches the payer | Add an alias (e.g. third-party payer), or categorize as non-dues income/expense |
  | `ambiguous` | Multiple members match | Pick the member manually |
  | `partial` | Alias matches, amount < outstanding receivable | Installment payment: book the partial clearing; the remainder stays open |
  | `overpaid` | Alias matches, amount > outstanding receivable | Decide manually: donation, next period's dues, or refund |
  | `uncategorized` | Non-member row matching no rule in `categorization.yaml` (§3.4) | Edit the journal entry, or add a rule so future rows self-categorize |

- Review state is plain journal metadata: everything awaiting attention is one hledger query away (`hledger print tag:review`). Resolving a flagged row means editing its tags/postings in the journal — an explicit human edit, not a Steward rewrite.
- The alias check runs against the normalized payer name; the amount check runs against the member's current `assets:receivables:dues` balance as reported by hledger.

### 3.4 `categorization.yaml` — non-member transaction rules

All categorization logic lives in Steward, not in hledger rules files (decision #10 in the HLD). hledger CSV rules are stateless — no access to journal balances or `payer_aliases.yaml` — and mixing member-aware conditionals into `rules/steward.rules` would break its static, personal-data-free property.

Non-member rows are categorized by an **ordered, first-match-wins** list of regex → account rules:

```yaml
categories:
  - match: "account fee|card statement"   # case-insensitive regex on payee + description
    account: "expenses:bank-fees"
  - match: "hosting-provider"
    account: "expenses:hosting"
```

- Rules are evaluated in order; the first match wins. No priorities, negation, or multi-condition matching — an association's recurring counterparties don't need them, and the `review:` mechanism is the safety net.
- Rows matching no rule book their second posting to a suspense account (`expenses:unknown` / `income:unknown`, sign-dependent) and are flagged `; review: uncategorized` (§3.3).
- The file lives in the data repo but contains no personal data — like `bank_formats.yaml`, it is shareable between associations.
- The learning loop: an uncategorized row is a one-time manual fix; adding a rule makes next month's identical row categorize itself.

### 3.5 Bank format mappings (`bank_formats.yaml`)

Bank exports differ in far more than column names — column order, separators, encodings, date formats, decimal marks, and even the *shape* of the amount (one signed column vs. separate debit/credit columns). Steward therefore separates **parsing** from **interpretation**:

- A small set of **parser adapters** (Python) handles file-level mechanics: delimiter, quoting, encoding (incl. BOM), header/footer lines, multi-line records.
- A declarative **format mapping** (`bank_formats.yaml`) describes how a bank's columns map onto Steward's internal transaction model. Mappings are named, versioned, and shareable between associations banking at the same institute.

```yaml
formats:
  bank-a-v1:                      # mapping name; referenced by `steward import --format`
    parser: csv                   # parser adapter to use
    parser_options:
      delimiter: ";"
      encoding: "utf-8-sig"       # strips the byte-order mark
      skip_header: 1
    columns:                      # internal field ← CSV column header
      date: "Booking Date"        # booking date, never the value date (decision #8)
      amount: "Amount"            # single signed column
      currency: "Currency"
      payee: ["Originator", "Beneficiary"]  # first non-empty wins (direction-dependent)
      description: "Purpose"
      reference: "Booking Text"   # mined for an end-to-end reference (see below)
      account: "Account IBAN"
    date_format: "%d.%m.%Y"       # 09.11.2026
    decimal_mark: ","             # -774,51 → -774.51
```

Internal transaction model (what every mapping must produce): `date`, `amount` (signed `Decimal`), `currency`, `payee`, `description`, `reference`, `account`.

`date` is always the **booking date** (decision #8 in the HLD): the day the entry hits the account. Value dates concern bank-internal interest settlement and are not recorded — mappings simply don't map the value-date column.

Variations the mapping must be able to express:

- **Split debit/credit columns** (some banks export separate `Debit`/`Credit` columns instead of one signed column):

  ```yaml
  amount:
    debit: "Debit"      # outgoing amounts, sign-flipped to negative
    credit: "Credit"    # incoming amounts
  ```

- **Multi-column fields** with first-non-empty-wins semantics (e.g. `payee` above: some banks fill an originator column for incoming payments and a beneficiary column for outgoing ones).
- **Date and number formats** per mapping (`date_format`, `decimal_mark`, optional `thousands_separator`).
- **Reference extraction**: `reference_pattern` (regex) mines a stable end-to-end reference from a text column — e.g. `End-to-End-Ref\.?:\s*(\S+)` against a booking-text column. When present, the reference takes precedence over the description in the dedup fingerprint (§7.2).

A mapping that cannot be expressed declaratively (e.g. amounts embedded in prose text) is a new parser adapter, not a hack in the mapping layer. `steward validate` checks mappings against a sample export.

**Row order** (decision #9 in the HLD): parser adapters must not assume any row order — exports may be newest-first. Steward sorts rows by date before rendering. This is presentation normalization, not correctness: hledger reports and the content-hash dedup (§7.2) are order-insensitive, but chronological journal files serve human inspection.

### 3.6 Journal conventions

- **The fiscal year is the calendar year.** There is no `fiscal_year_start` configuration; a configurable fiscal year is a possible future feature (HLD §6) with wide-ranging implications (journal layout, accrual dates, closing file placement) that must be designed then.
- **One file per month** (default): `journals/YYYY/MM.journal`. With `journal_granularity: "yearly"`, one file per year: `journals/YYYY.journal`.
- `journals/main.journal` is static and never needs editing: it contains only `include **.journal` (hledger globs match all journal files in the directory and below, and always exclude the including file).
- Monthly granularity supports fiscal regulations that require timely, tamper-evident recording (e.g. GoBD): each month is closed and committed to Git, keeping every commit small and reviewable. Because `import` appends each transaction to the file of its posting month, previously committed month files stay untouched; a late backdated append shows up as an isolated Git diff.
- **Once-committed entries are never changed** — corrections are later adjustment entries (§5.3), so a closed period's history in Git stays intact.
- Journal files are **written exclusively by `hledger import`** (§6). Humans may add manual entries (`hledger add` or an editor); Steward never modifies journal files itself.
- Currency commodity: `€` (EUR), declared once per file.
- Generated transactions carry metadata tags:
  - `; member-id: 1001` — links a transaction to a member.
  - `; year: 2026` — the dues period an **accrual or adjustment** belongs to. Payments carry **no** `year` tag: they clear the member's total receivable balance, and per-period aging is derived from the accrual side (decision #7 in the HLD).
  - `; import-hash: <sha256>` — deduplication fingerprint for imported bank rows.
  - `; review: <reason>` — marks an imported row as needing manual resolution (§3.3).
- Files are append-only. Corrections are new transactions (adjustments), never edits.
- Appends are not guaranteed to land in date order (e.g. a January accrual generated in March). hledger handles unordered dates fine; do not enable `hledger check ordereddates`.

## 4. Chart of Accounts (minimal)

| Account | Purpose |
|---------|---------|
| `assets:bank:checking` | Association bank account |
| `assets:cash` | Cash on hand |
| `assets:receivables:dues` | Outstanding membership dues |
| `income:dues` | Membership dues income |
| `income:unknown` | Suspense: uncategorized incoming payments (§3.4) |
| `expenses:unknown` | Suspense: uncategorized outgoing payments (§3.4) |
| `expenses:…` | Free-form expense categories |
| `equity:opening-balances` | Opening balances / retained equity |

## 5. Posting Models

The following are the *logical* posting models. Physically, Steward never writes this syntax itself — every transaction is rendered and appended by `hledger import` from an enriched CSV row (§6).

### 5.1 Dues accrual (generated by `generate-dues`)

Accruals are dated with the **command's run date**: what counts is when the treasurer recorded the claim, not the period it refers to — the `year:` tag carries the dues period. Full-year waivers are booked as €0.00 accruals so the `member-id`/`year` tags exist and re-runs stay idempotent.

```
2026-01-01 Membership dues 2026 — John Doe
    ; member-id: 1001
    ; year: 2026
    assets:receivables:dues          €120.00
    income:dues                     €-120.00
```

### 5.2 Bank payment clearing a receivable (generated by `import`)

```
2026-02-03 John Doe | membership dues
    ; import-hash: 9f2c…
    ; member-id: 1001
    assets:bank:checking             €120.00
    assets:receivables:dues         €-120.00
```

Payments are **not** mapped to a definite dues period (decision #7 in the HLD): they clear the member's total `assets:receivables:dues` balance. Early payments (e.g. next year's dues paid in October) drive the balance into credit until the corresponding accrual absorbs it; late payments simply settle the accumulated balance. Per-period reporting ("which years are still open?") is derived from the `year` tags on the accrual side.

Low-confidence matches carry an additional `; review: <reason>` tag (§3.3); unmatched rows omit `member-id` and book the second posting to a suspense account (e.g. `income:unknown` / `expenses:unknown`) until resolved.

### 5.3 Adjustment (master data changed after the accrual was booked)

Adjustments carry the same `member-id`/`year` tags and book only the difference (positive or negative) between the corrected and the previously booked amount. Like all bookings, they are dated with the command's run date — never backdated into a closed period.

## 6. Write Path: `hledger import`

All journal writes flow through a single pipeline:

1. **Compute.** Steward produces a set of bookings (dues accruals, adjustments, or matched bank transactions).
2. **Enrich.** Each booking becomes one row of an internal *enriched CSV*, written to a temp file:

   `date, description, account1, account2, amount, comment`

   - `amount` is signed: `+amount` posts to `account1`, the balancing `-amount` to `account2` (hledger CSV convention). `import` writes both posting amounts explicitly.
   - `comment` carries the metadata tags as comma-separated text, e.g. `member-id: 1001, year: 2026` or `import-hash: 9f2c…, member-id: 1001` (§3.6). Steward builds this string; empty tags are simply omitted.
3. **Render & append.** Rows are grouped by posting period (default: month); per period file, Steward runs:

   ```
   hledger import -f journals/YYYY/MM.journal --rules rules/steward.rules <tmp.csv>
   ```

   `rules/steward.rules` is short, static, and checked in — no conditionals of any kind, no personal data. All content interpretation (aliases, balances, categories) happens in Steward before rendering (§3.3, §3.4):

   ```
   skip 1
   fields date, description, account1, account2, amount, comment
   date-format %Y-%m-%d
   decimal-mark .
   currency €
   ```

4. **Gate.** After a real (non-dry-run) import, Steward runs `hledger -f journals/main.journal check` and reports any failure.

Consequences:

- **One writer.** Journal syntax, amount alignment, and tag formatting are owned by hledger; Steward cannot emit a malformed journal.
- **`--dry-run` for free.** Steward's `--dry-run` maps to `hledger import --dry-run`, which prints the transactions that would be appended and writes nothing.
- **hledger's built-in overlap detection is deliberately bypassed.** `import` tracks a `.latest.<file>` state file per *stable* input filename (latest imported date, plus the count of same-date records). Steward feeds it a fresh temp file of already-deduplicated rows, so no `.latest` state ever applies; deduplication is Steward's content-hash pre-filter (§7.2), which is robust against overlapping re-exports.
- **Limitations.** One CSV row yields one two-posting transaction — sufficient for all posting models in §5. Rare multi-split transactions (e.g. one bank payout covering both dues and a donation) are entered manually with `hledger add`. Steward ensures the target period's journal file (and its year directory) exists, creating it empty if needed, before invoking `import`.

## 7. Idempotency Mechanisms

### 7.1 Dues generation — pre-flight scan

Before rendering, `generate-dues` collects all `(member-id, year)` tag pairs across the journals (via `journals/main.journal`). A member/year combination that already exists is skipped or adjusted — never duplicated.

### 7.2 Bank import — content hashing

`hledger import` has built-in overlap detection, but it keys on a `.latest.<file>` state file per stable input filename (latest date + same-date record count). Re-downloaded overlapping exports — new filename, reordered rows — defeat it. Steward therefore deduplicates itself, before invoking hledger:

```
fingerprint = SHA-256(account | date | amount | payee | description)
```

Normalization before hashing:

- `date`: ISO `YYYY-MM-DD`.
- `amount`: decimal with exactly two fraction digits, `.` as separator, no thousands separators (bank exports may use German `1.234,56` formatting).
- `payee` / `description`: Unicode-normalized (NFC), case-folded, whitespace collapsed and trimmed.
- `account`: the importing account (e.g. IBAN), so transfers between two own accounts tracked by Steward do not collide.
- `reference`: when the format mapping extracts a stable end-to-end reference (§3.5), it **replaces** `description` in the fingerprint — banks occasionally reword description texts between overlapping exports, while end-to-end references are stable.

Enforcement: before rendering, Steward drops every row whose fingerprint already appears as an `; import-hash:` tag in `journals/`. The fingerprint is then written into the transaction as that tag, making the journal itself the dedup database.

Known limitation: rows without a stable reference remain vulnerable to description rewording between exports; such duplicates must be resolved manually.

## 8. Privacy & Data Protection

Master data and bank journals contain personal data (GDPR relevance):

- **Real data lives in a separate, private Git repository per association** (decision #5 in the HLD), laid out as in §2. The public Steward repository carries only `*.example.yaml` files — never real member data, journals, or bank exports. `bank_formats.yaml` and `categorization.yaml` contain no personal data and may be shared between associations.
- The data repo's `.gitignore` covers OS artifacts (e.g. `.DS_Store`) and temp files. `steward init` scaffolds the data repo, including its `.gitignore` — see [init.md](../../requirements/features/init.md).
- The enriched CSV (§6) contains personal data but exists only as a temp file for the duration of a command run.

## 9. Error Handling & Safety

- All mutating commands support `--dry-run`, mapped to `hledger import --dry-run` (prints planned transactions, writes nothing).
- `steward validate` runs the schema checks from §3 plus `hledger -f journals/main.journal check`; it is suitable for CI on every commit. Full check list: [validation.md](../../requirements/features/validation.md).
- Every real import is followed by the `hledger check` gate (§6 step 4).

## 10. Committing, Closing & Tamper-Evidence

Regulatory compliance (GoBD-style: tamper-evidence, timeliness, traceability) comes from **Git, not from hledger**. `hledger close` is pure accounting hygiene — it appends closing/opening transactions to an editable text file and contributes nothing to tamper-evidence. The commit is the compliance mechanism.

### 10.1 `steward commit` — the recording ritual

One command wraps the routine:

1. `steward validate` (schema checks + `hledger check`).
2. Check for unresolved `; review:` tags; refuse to commit while any exist unless `--force` is given.
3. `git commit` the data repository with a standard message; optionally sign (`closing.sign: true`) and push (`closing.remote`, e.g. `"origin"`) — see §3.1 and §10.3.

Commits should happen **flexibly — daily, weekly, at least monthly** — whenever new entries exist. Frequent small commits keep the audit trail granular and reviewable.

### 10.2 `steward close` — the annual accounting close

`hledger close` runs **once per fiscal year**, not monthly: closing transactions zero income/expense accounts into equity in the last period file, and opening balances carry assets/liabilities into the first file of the new year.

### 10.3 Configurable tamper-resistance

| Level | Mechanism | Property |
|-------|-----------|----------|
| Default | Plain commits | No silent edits: history rewrites break the hash chain |
| Optional | `closing.sign: true` — GPG-signed commits/tags | Proves *who* recorded the period; tags are unforgeable |
| Optional | `closing.remote: "origin"` — push after commit | Server-side timestamps outside the treasurer's control |

Git commit timestamps alone are forgeable (`GIT_COMMITTER_DATE`), so associations with stricter requirements should enable signing and/or a remote.

### 10.4 Corrections to closed periods

A late bank statement or an auditor's finding in an already-committed period is **never** fixed by editing that period's file — the correction is booked as an adjustment entry in the current period (§5.3). The runbook must state this explicitly.
