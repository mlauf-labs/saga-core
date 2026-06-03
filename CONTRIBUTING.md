# Contributing to DocStore

Thanks for your interest in contributing! This project follows a few conventions to
keep it maintainable and release-friendly.

## Getting started

```bash
# 1. Install uv: https://docs.astral.sh/uv/
# 2. Install dependencies and the pre-commit hooks
uv sync
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

## Development workflow

We use **Git Flow**:

- `main` — released, production-ready code (tagged releases only).
- `develop` — integration branch for the next release.
- `feature/<name>` — new features; branch from and merge into `develop`.
- `fix/<name>` — non-urgent fixes; branch from and merge into `develop`.
- `release/<version>` — release stabilisation; branch from `develop`, merge into
  `main` **and** `develop`.
- `hotfix/<version>` — urgent production fixes; branch from `main`, merge into `main`
  **and** `develop`.

Open pull requests against `develop` (or `main` for hotfixes).

## Quality gates (run before pushing)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

CI runs the same checks. All must pass; coverage must stay ≥ 80% on core packages.

## Coding standards

- Python **3.12+**, **fully typed** (`mypy --strict`).
- **English** for all code, comments, and docs.
- No hard-coded config or secrets — use `config/*.yaml` + env.
- Prompts/tool descriptions live in `prompts/*.md`.
- Meaningful, actionable error messages; structured logging (no `print`).
- New behaviour comes with unit tests.

See [`AGENTS.md`](AGENTS.md) for a deeper tour of the codebase.

## Commit messages — Conventional Commits

Format: `type(scope): summary`

Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`.

Examples:

```
feat(mcp): add hybrid_search tool (FR-19)
fix(converters): handle empty Kreuzberg response (FR-4)
docs: document backup directory layout (FR-30)
```

Breaking changes: add `!` after the type/scope or a `BREAKING CHANGE:` footer.
Release notes/changelog are generated from these messages.

## Reporting issues

Use the issue templates. Include reproduction steps, expected vs. actual behaviour,
logs (with secrets redacted), and your environment.

By contributing, you agree that your contributions are licensed under the project's
[Apache-2.0 license](LICENSE).
