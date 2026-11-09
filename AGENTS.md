# AGENTS.md — Instructions for AI Agents

## Canonical design documents: always consult `.ai/`

Before writing or changing any code, **always consult the design documents under [.ai/](.ai/)**. They are the canonical specification of what Steward is and does. Which document answers which question:

| Question | Document |
|----------|----------|
| Why does Steward exist? Principles, roadmap, decision log | [.ai/requirements/high-level-design.md](.ai/requirements/high-level-design.md) |
| What exactly should command `X` do? Behavior, edge cases, acceptance criteria | [.ai/requirements/features/](.ai/requirements/features/) (one PRD per command) |
| Data schemas, journal conventions, posting models, write path | [.ai/docs/architecture.md](.ai/docs/architecture.md) |
| Technology choices and rationale | [.ai/docs/tech-stack.md](.ai/docs/tech-stack.md) |

If code and docs disagree, the docs win — or the docs are deliberately updated first, in the same change.

## Documentation maintenance contract

- **Each fact lives in exactly one document.** Cross-reference instead of duplicating. The scope of each document is declared in its header — respect it.
- **Design decisions** are recorded in the decision log (HLD §7) with date and rationale; the canonical spec is updated in architecture.md or the relevant PRD. Never silently reverse a logged decision.
- **New features** get a PRD in `.ai/requirements/features/` before implementation.
- User-facing documentation (runbook, CLI reference) does **not** belong in `.ai/` — it lives in the root-level `docs/` (canonical) and is scaffolded into the association's data repo by `steward init`.

## Architectural invariants — never violate these

1. **Steward never writes journal files.** All journal writes go through `hledger import` fed by a Steward-generated enriched CSV and the static `rules/steward.rules` (architecture.md §6).
2. **Journals are append-only.** Corrections are adjustment entries, never edits. Once-committed periods are never rewritten.
3. **No personal data in this repository.** Real `members.yaml`, `payer_aliases.yaml`, journals, and bank exports live in a separate private data repo; only `*.example.yaml` files belong here.
4. **Amounts are decimal strings**, parsed as `Decimal` — never binary floats.
5. **`rules/steward.rules` stays static** — no conditionals, no content interpretation. All stateful logic (aliases, balances, categories, dedup) lives in Steward.

## Dependencies: do not add by default

**Do not add new dependencies without explicit user approval.** The standard library covers most of Steward's needs (`decimal`, `hashlib`, `re`, `csv`, `subprocess`, `tempfile`, `datetime`, `pathlib`). Every dependency is something a successor treasurer must install, version, and trust — the low-bus-factor principle applies to the dependency tree.

- The current, deliberately chosen dependencies are `click` and `pyyaml` (rationale: [tech-stack.md](.ai/docs/tech-stack.md)). That is the bar a new dependency must clear.
- Before proposing one, check whether the stdlib or an existing dependency already solves the problem.
- Dev-only tools (pytest, ruff, mypy) are exempt but still belong in the dev dependency group, never in runtime dependencies.

## Testing strategy

- **Test the decision points, not the plumbing.** Risk concentrates in pure functions: rate resolution (override → tier → fee_tier), proration math, fingerprint normalization, alias matching, confidence classification. Unit-test these exhaustively — every bug there is a wrong amount of money.
- **Golden-file tests for the write path.** Scenario input (members.yaml + bank CSV) → expected journal output, compared verbatim, using the real hledger binary. Golden files double as living documentation of the posting models (architecture.md §5).
- **Acceptance criteria → tests, mechanically.** Every PRD's acceptance-criteria section becomes named tests. If a criterion can't be tested, the PRD is vague — fix the PRD.
- **Fixtures from the example files.** `members.example.yaml` plus a small synthetic bank CSV cover most scenarios; edge cases use inline YAML strings in the test, not more files.
- Layout: `tests/unit/` (pure logic), `tests/golden/` (end-to-end via hledger), `tests/test_cli.py` (thin CLI wiring).

## Commit policy

- **Never commit without explicit user approval.** Do not run `git commit` on your own initiative. Instead, stage the changes, summarize them, and propose a commit — the user reviews first and says when to commit. Appropriate times to *propose* a commit: a completed PRD acceptance criterion, a coherent refactor, a settled design decision with its doc updates.
- **Pre-commit must pass before committing, and its failure must block the commit.** Run `uvx pre-commit run --all-files` as a standalone step and check its exit code; only commit when it has passed. Never chain it into a commit command in a way that lets a failure slip through, and never use `git commit --no-verify`.
- **Small, complete, single-purpose commits.** Each commit leaves the repo green: tests, lint, and type checks pass.
- **Docs and code change together.** A behavior change and its PRD/architecture update land in the same commit — never "code now, docs later".
- **Decision-log entries ride along.** A commit that settles a design question includes the dated decision-log update (HLD §7).
- **Messages:** imperative subject (`Add X`, `Fix Y`), body explaining *why* when non-obvious. No Conventional-Commits ceremony.
- **Never commit:** real member data, bank exports, `.latest.*` files, temp CSVs (see invariant 3 and `.gitignore`).

## Repository state

- The project is scaffolded (src-layout, Click CLI stubs for all six commands). Implemented: `steward validate` (model.py dataclasses + checks.py named checks + validate.py orchestration), `steward generate-dues` (dues.py segment/proration engine + journal.py hledger write path + generate_dues.py orchestration), and `steward import` (bank.py parsing/fingerprint/matching/classification + bank_import.py orchestration). Implement remaining features against the PRDs in `.ai/requirements/features/`.
- Example files (`members.example.yaml`, `payer_aliases.example.yaml`, `steward.example.yaml`, `categorization.example.yaml`, `bank_formats.example.yaml`) must stay consistent with the canonical schemas in architecture.md §3 — `tests/test_examples.py` validates them.

## Validation commands

Run before committing; all must pass:

```sh
uv run pytest        # tests
uv run ruff check .  # lint
uv run mypy src/     # type check (strict)
```

Setup: `uv sync` (installs runtime + dev dependencies into `.venv`).

Pre-commit hooks (ruff, mypy, file hygiene) run the same checks locally: `uvx pre-commit install` once, then they run on every `git commit`. CI (`.github/workflows/ci.yml`) runs lint, type check, and tests on Python 3.11–3.13 for every push and PR.
