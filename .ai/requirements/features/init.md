# PRD: Assisted Treasury Setup (`steward init`)

> **Status:** Phase 2, specified — not yet implemented.
> Canonical spec: [../../docs/architecture.md](../../docs/architecture.md) §2 (directory layout), §8 (privacy & data protection).

## 1. Purpose

Real data lives in a **separate, private Git repository per association** (decision #5). `steward init` scaffolds that repository so a new treasurer — or a successor — gets a complete, valid, private treasury in minutes, serving the low-bus-factor principle: the tool that runs the treasury also sets it up.

## 2. Command

```
steward init [directory]
```

Interactive: prompts for association name, currency, proration policy, and closing options (signing, remote).

## 3. Behavior

1. Create the directory layout ([architecture.md §2](../../docs/architecture.md#2-directory-layout)):
   - `steward.yaml` — from the interactive answers.
   - `members.yaml`, `payer_aliases.yaml`, `categorization.yaml`, `bank_formats.yaml` — commented skeletons derived from the `*.example.yaml` files.
   - `rules/steward.rules` — the static write-path rules file (§6).
   - `journals/main.journal` — static `include **.journal`.
   - `docs/runbook.md` — a starter handover runbook (daily/weekly/monthly routine, correction policy, handover steps).
2. `git init`; create a `.gitignore` covering OS artifacts (e.g. `.DS_Store`) and temp files.
3. Run `steward validate` as a final smoke test.
4. Print next steps: fill in members, configure the bank format mapping, run the first import.

## 4. Privacy

- The scaffolded repository is private by convention; `steward init` warns if it detects it was created inside a public remote's clone.
- No personal data is written by `init` — skeletons contain placeholder entries only.

## 5. Acceptance Criteria

- `steward init treasury && cd treasury && steward validate` passes out of the box.
- All scaffolded files match the canonical schemas in architecture.md §3.
- `.gitignore` excludes `.DS_Store` and Steward temp files.
- The generated runbook covers: import routine, commit cadence, correction policy (never edit committed periods), and handover.
