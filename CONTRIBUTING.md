# Contributing to Shopping Magic

Use [issues](https://github.com/alexneverland/shopping-magic/issues) for
reproducible bugs and scoped feature proposals. Examples, fixtures, and bug
reports must use synthetic data only. Read [SECURITY.md](SECURITY.md) before
reporting anything involving local data safety.

## Local setup

Shopping Magic requires Python 3.10+ with standard-library `sqlite3`. There are
no pip dependencies. From the repository root:

```text
python tools/check_repository.py
python tools/verify_scenarios.py
python -m unittest discover -s tests -v
python tools/build_package.py
```

On macOS/Linux, use `python3` if `python` points to an older interpreter. All
automated tests use isolated temporary directories and never write to your real
`~/.shopping-magic` directory or real agent skill folders.

## Pull requests

1. Make focused changes with tests for any changed persistence, fulfillment,
   replenishment, migration, concurrency, or installer behavior.
2. Run all repository checks, scenario verification, and `unittest` discovery.
3. Keep `SKILL.md`, `references/*.md`, `README.md`, `VERIFICATION.md`, and
   `tools/build_package.py` synchronized.

## Architectural boundaries

- The hosting AI model understands natural language and infers categories/intent.
  The Python CLI only accepts explicit structured JSON actions.
- Keep runtime dependencies strictly inside the Python standard library.
- Keep user data outside the skill installation folder and independent from
  Meal Magic.
- Maintain a single portable skill across Codex CLI, Claude Code, and
  Antigravity; do not introduce host-specific storage forks.
- Keep live conversational test status honest: deterministic storage tests and
  Level 1 scenario checks do not verify real LLM behavior. Follow
  [tests/agent-scenarios.md](tests/agent-scenarios.md) when evaluating live
  agents in isolated synthetic profiles.
