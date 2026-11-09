# PRD: Assisted Treasury Setup (`steward init`)

> **Status:** Phase 1, specified — not yet implemented.
> Canonical spec: [../../docs/architecture.md](../../docs/architecture.md) §2 (directory layout), §8 (privacy & data protection).

## 1. Purpose

Real data lives in a **separate, private Git repository per association** (decision #5). `steward init` scaffolds that repository so a new treasurer — or a successor — gets a complete, valid, private treasury in minutes, serving the low-bus-factor principle: the tool that runs the treasury also sets it up.

## 2. Command

```
steward init [directory] [--no-input] [--name <association>] [--currency <code>]
             [--proration <monthly|daily|none>] [--git-name <name>] [--git-email <email>]
```

- `directory` defaults to the current directory (`.`).
- **Interactive mode** (default, requires a TTY): prompts for association name, currency, and proration policy — the answers a first-time treasurer can give confidently. `journal_granularity` and `closing.*` are **not** prompted for: their defaults are safe, and evaluating them (GPG signing, remotes, journal layout) requires experience a new treasurer does not yet have; the runbook documents both. Prompts also ask for Git author name and email when not already configured (§3 step 3).
- **Non-interactive mode** (`--no-input`, or automatically when stdin is not a TTY): no prompts; values come from the options above, falling back to the defaults `currency: "EUR"`, `proration: "monthly"`. `--name` defaults to the directory name.

## 3. Behavior

1. **Preflight.** The target directory must not exist or must be empty; otherwise abort with an error (no `--force` — scaffolding is one command away once the directory is cleared, and a silent merge into an existing treasury must never happen).
2. **Create the directory layout** ([architecture.md §2](../../docs/architecture.md#2-directory-layout)):
   - `steward.yaml` — from the interactive answers / options.
   - `members.yaml`, `payer_aliases.yaml`, `categorization.yaml`, `bank_formats.yaml` — copies of the `*.example.yaml` files bundled with the Steward package, with every data entry commented out and a header pointing to the matching example file. Commented-out entries keep the scaffold free of placeholder personal data while leaving in-file guidance for the treasurer's first edits.
   - `rules/steward.rules` — the static write-path rules file, written from the same canonical content the write path uses (architecture.md §6).
   - `journals/main.journal` — static `include **.journal`.
   - `docs/runbook.md` — a copy of the canonical handover runbook, bundled with the Steward package (an installed Steward has no repository checkout to copy from).
3. **Git.** `git init`, then create the **initial commit** containing the full scaffold, so the audit trail starts at birth. Author identity resolution, in order: `--git-name`/`--git-email` options → interactive prompt → configured `user.name`/`user.email` (repo, then global). If no identity is available from any of these, abort with an error naming the missing configuration. Prompted or option-given identities are written to the new repository's local Git config so future commits work.
4. **`.gitignore`** covering OS artifacts (e.g. `.DS_Store`) and `*.csv` bank exports — the files that must never be committed (invariant 3). Steward's own temp files never land in the data repo (the enriched CSV lives in the OS temp dir, §6), so no Steward-specific pattern is needed.
5. **Smoke test.** Run `steward validate` on the new repository. A failure (e.g. hledger binary missing) aborts the command with a non-zero exit code — a treasury that does not validate on day one is not a valid scaffold.
6. **Print next steps**: fill in members, configure the bank format mapping, run the first import — and the privacy notice (§4).

Exit code is 0 on success, non-zero on any failure (preflight, identity, validation). A failed run leaves no half-scaffolded directory behind: everything created so far is removed.

## 4. Privacy

- The scaffolded repository is private by convention. `init` prints a privacy notice with the next steps: the repository contains personal data — keep hosting private, never push to a public remote. There is no public-remote *detection*: a freshly `git init`'d directory has no remote to inspect, and no local check can tell a remote's visibility.
- No personal data is written by `init` — skeletons contain commented-out placeholder entries only.

## 5. Edge Cases

| Case | Behavior |
|------|----------|
| Target directory exists and is non-empty | Abort with an error; nothing is created or modified |
| `steward init` with no argument | Scaffolds the current directory (must be empty) |
| stdin is not a TTY (script, CI, pipe) | Non-interactive mode automatically; missing values fall back to defaults |
| No Git identity available anywhere | Abort with an error naming the missing `user.name`/`user.email` configuration |
| hledger binary missing | The validation smoke test fails; the command reports it and exits non-zero |
| Directory name with spaces or non-ASCII characters | Works; used as-is for the directory and as the default association name |

## 6. Acceptance Criteria

Happy path:

- `steward init treasury && cd treasury && steward validate` passes out of the box.
- All scaffolded files match the canonical schemas in architecture.md §3.
- `rules/steward.rules`, `journals/main.journal` (containing `include **.journal`), and `docs/runbook.md` exist; the runbook covers import routine, commit cadence, correction policy (never edit committed periods), and handover.
- `.gitignore` excludes `.DS_Store` and `*.csv`.
- The scaffold is a Git repository whose initial commit contains all scaffolded files; `git log` shows exactly one commit.
- The next-steps output is printed, including the privacy notice.

Interactivity & defaults:

- Answering the prompts produces a `steward.yaml` with exactly those values (association name, currency, proration).
- `--no-input` with no options produces `steward.yaml` with the directory name as association, `currency: "EUR"`, `proration: "monthly"`, `closing.sign: false`, `closing.remote: null`, and `journal_granularity: "monthly"`.
- Options override defaults without prompting (`--name "Mein Verein" --currency "CHF" --proration daily`).

Git identity:

- With a configured global Git identity, the initial commit uses it.
- With no configured identity, interactive mode prompts for name and email, writes them to the repository's local Git config, and the initial commit uses them.
- With no configured identity, `--no-input --git-name "A" --git-email "a@b.c"` succeeds and the initial commit uses them.
- With no configured identity and no `--git-name`/`--git-email` in non-interactive mode, the command fails with an error naming the missing configuration and leaves no directory behind.

Safety:

- `steward init existing-non-empty-dir` fails with an error and leaves the directory untouched.
- A run in a non-TTY environment without options succeeds using defaults.
