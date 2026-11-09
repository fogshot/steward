# Tech Stack

> **Scope of this document:** technology choices and their rationale. System structure and data formats live in [architecture.md](architecture.md).

| Concern | Choice |
|---------|--------|
| Accounting engine | hledger |
| CLI & automation language | Python ≥ 3.11 |
| CLI framework | Click |
| Data serialization | YAML (via PyYAML) |
| Money arithmetic | Python `decimal.Decimal` |
| Version control | Git |
| Package/project manager | uv |
| Build backend | hatchling |
| Tests | pytest |
| Lint & format | Ruff |
| Type checking | mypy (strict) |

## hledger

Provides powerful command-line reporting, commodity tracking (EUR), and strict double-entry verification. hledger is a plain text accounting system, which means:

- Data is stored in simple plain text files, which can be easily read by humans, tracked with version control software such as Git, and maintained with text processing tools. This facilitates auditing, portability, and longevity of your valuable accounting data.
- The data format is flexible and easy to write or generate.
- Data and software is kept local, private and under control.
- There is a fast command-line interface, which makes the tool easy to script, automate, and integrate into custom workflows.
- The model of operation is simple: put a log of transactions in, get reports out.
- It is light-weight, non-distracting, and great for learning more of double-entry bookkeeping and accounting.
- Compared to other PTA apps, hledger aims to be the easiest to use, best maintained, best documented, and robust option.

Steward invokes `hledger` as an external binary (subprocess) for validation (`hledger check`) and reporting; it does not link against hledger. The supported and CI-tested version is **1.52.4**; other versions are not currently supported.

## Python

- Python is widely known, reads well, and provides lots of functionality out of the box (e.g. `datetime`).
- A lot of powerful third-party libraries are available.
- Python does not require compilation or lots of setup and is therefore relatively easy to run, even for non-technical people.

The minimum supported Python version is **3.11** (decided at scaffolding, 2026-11-08).

### Click

While Click adds an external dependency, it can be easily installed with pip and offers superior developer ergonomics and validation over built-in alternatives like argparse.

### PyYAML

Parses the YAML master data. Amounts are loaded as `Decimal` from quoted strings, never as binary floats.

## YAML

YAML is plain text, therefore no external database or other dependency is required. It can easily be version-controlled, e.g. using Git.
While JSON might be a more popular data serialization format for most use cases over YAML, YAML does offer better human-readability and therefore has a lower barrier-to-entry.

## Git

Provides complete historical change logs, attribution, and easy synchronization. The treasury repository doubles as the audit trail and the handover medium.

## Development tooling

- **uv** — fast, modern project/dependency manager; handles the virtualenv, lockfile (`uv.lock`), and Python versions. Setup is two commands (`uv sync`, `uv run …`), which keeps the bus factor low.
- **hatchling** — build backend; zero-config for the src-layout.
- **pytest** — test runner; `tests/` mirrors `src/steward/`.
- **Ruff** — linter and formatter in one (replaces flake8/black/isort).
- **mypy** — strict-mode type checking; worth it from day one given the `Decimal` money invariant.
- **pre-commit** — runs Ruff, mypy, and file-hygiene hooks on every commit (`uvx pre-commit install` once per clone).
- **GitHub Actions** — CI runs lint, type check, and tests on Python 3.11–3.13 for every push and PR (`.github/workflows/ci.yml`).
