# Contributing

Thank you for considering a contribution! This document explains how to set up
your development environment and submit changes.

## Prerequisites

Install these tools:

- [Python 3.14+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Just](https://just.systems/man/en/installation.html) (optional — you can run
  `uv run` commands directly)

Then:

```bash
uv sync --all-groups
```

If you're working in a Git checkout, also install the local hooks:

```bash
uv run pre-commit install --install-hooks
```

## Development Workflow

```bash
# Format and auto-fix
just fmt

# Lint + type check
just lint

# Run tests
just test

# Build and verify the wheel in an isolated temp environment
just smoke

# Run everything (format → lint → test)
just check
```

**Without Just**, run the equivalent commands:

```bash
uv run ruff check --fix .
uv run ruff format .
uv run ruff check .
uv run mypy src scripts tests
uv run pytest --cov=pidprobe --cov-branch --cov-report=term-missing:skip-covered --cov-fail-under=80
uv build && uv run python scripts/smoke_test.py
```

## Integration Tests and the macOS Root Requirement

Tests marked `integration` attach to a real child process with
`sys.remote_exec` (PEP 768) instead of faking the injection. They are the only
tests that prove the round trip actually works, so they are worth running
before you send a change that touches injection, the channel, or the
collectors.

```bash
# Just the ones that attach to a live target
uv run pytest -m integration

# Everything except them
uv run pytest -m "not integration"
```

They skip themselves where injection is impossible:

| Condition | What happens |
| --- | --- |
| `sys.remote_exec` missing (interpreter built with `PYTHON_DISABLE_REMOTE_DEBUG`) | skipped |
| macOS, not root | skipped — `requires root on macOS (task_for_pid)` |
| macOS, root | runs |
| Linux, `kernel.yama.ptrace_scope` ≤ 1 | runs |

**On macOS you need `sudo` to run them.** `sys.remote_exec` has to take the
target's task port, and macOS grants that only to root or to a binary carrying
the `com.apple.system-task-ports` entitlement — the same constraint
`pidprobe doctor` reports as the `task_for_pid` check, and the reason it tells
macOS users to run `sudo pidprobe snap <PID>`. It is a platform rule, not
something pidprobe can work around. Locally:

```bash
uv sync --group dev
sudo .venv/bin/python -B -m pytest -m integration -p no:cacheprovider
```

Call the virtual environment's interpreter directly rather than `sudo uv run`:
`sudo` resets `HOME`, so `uv` would otherwise resolve a different cache and
Python install as root. `-B` and `-p no:cacheprovider` stop the root process
from leaving `__pycache__` and `.pytest_cache` entries your own user cannot
overwrite afterwards.

### What CI does

The `Test` matrix (Python 3.14 and 3.15 × Ubuntu and macOS) runs the whole
suite unprivileged, exactly as you would in your own shell:

- **Ubuntu** — the integration tests run in that unprivileged pass. The job
  also sets `PIDPROBE_REQUIRE_INTEGRATION=1`, which turns "injection is
  unavailable" from a skip into a failure, so integration coverage cannot
  quietly disappear behind a green check.
- **macOS** — they skip in the unprivileged pass (`task_for_pid` is denied to
  uid 501), and a second step re-runs `pytest -m integration` under `sudo`,
  also with `PIDPROBE_REQUIRE_INTEGRATION=1`. GitHub's macOS runners give the
  `runner` user passwordless `sudo`, and root does get the task port there, so
  every integration test really does execute on macOS. Only that step is
  privileged; the unprivileged pass is still what the rest of the matrix
  exercises.

So a red macOS job can mean the injection path broke on macOS specifically —
it is not a platform where integration coverage is taken on faith.

## Pull Request Process

1. Fork the repository and create a branch from `main`
2. Make your changes
3. Ensure `just check` passes
4. Write or update tests for your changes
5. Open a pull request using the PR template

### Code Standards

- All public functions and methods must have type annotations
- mypy strict mode must pass
- Ruff must pass with no warnings
- Maintain or improve test coverage (minimum 80%)

### Commit Messages

Use Conventional Commits for both commits and PR titles:

```
<type>(<optional-scope>): <short summary>
```

Examples:

- `feat: add JSON export support`
- `fix(api): handle empty input`
- `docs: update installation guide`

Recommended types: `feat`, `fix`, `docs`, `refactor`, `test`, `ci`, `chore`,
`perf`, `build`.

### Changelog Policy

`CHANGELOG.md` (in [Keep a Changelog](https://keepachangelog.com/) format) is
the canonical, human-curated record of user-facing changes. Add an entry
under `[Unreleased]` for any user-facing change in the same PR that makes it.

GitHub's auto-generated release notes (via `.github/release.yml` categories)
are supplementary — useful for a quick PR-by-PR diff, but `CHANGELOG.md` is
what users should read to understand what changed in a release.

## Getting Help

If something is unclear, open an issue or start a discussion. We're happy to
help you get started.
