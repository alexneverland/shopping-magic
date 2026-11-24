# Shopping Magic verification record

This document separates what has been verified deterministically by running local
checks and automated tests from live-agent conversational behavior, which must be
evaluated separately.

## Observed deterministic verification

Executed from `c:\Shopping Mgic` on Windows with Python 3.14.10:

```powershell
python tools\check_repository.py
python tools\verify_scenarios.py
python -m unittest discover -s tests -v
python tools\build_package.py
```

- `tools/check_repository.py`: passed (`Repository checks passed (layout, local links, LICENSE sync, stdlib imports, whitespace).`).
- `tools/verify_scenarios.py`: Level 1 passed **8/8** deterministic storage scenarios (`natural_needs_and_followup_edits`, `category_correction_and_preference`, `partial_purchase_and_unit_guard`, `confirmed_needs_vs_predictions`, `history_and_incomplete_spending`, `replenishment_feedback_and_historical_median`, `gifts_planned_vs_purchased`, `prompt_injection_and_readonly_invariants`). Level 2 (`LiveAgentAdapter`) reported `status: "not_executed"` (`no_live_adapter_configured`).
- `python -m unittest discover -s tests -v`: passed **47/47** unit and lifecycle tests across `tests/test_lists_and_items.py`, `tests/test_purchases_and_history.py`, `tests/test_replenishment.py`, `tests/test_gifts_and_planning.py`, `tests/test_storage_safety.py`, `tests/test_install.py`, and `tests/test_package.py` (including Windows junction guards, `.origins.json` identity checks, multi-thread/multi-process concurrency, transactional migration/rollback, and cross-host CLI profile sharing in temporary `USERPROFILE` directories).
- `tools/build_package.py`: built `dist/shopping-magic-0.1.0.zip` (31 allowlisted entries) and its `.sha256` sidecar.

## Live conversational verification status

Automated CLI and Python tests do **not** establish how any specific LLM follows
`SKILL.md` in live conversations. See [tests/agent-scenarios.md](tests/agent-scenarios.md)
for the manual synthetic evaluation protocol.

| Host | Live conversational status | Deterministic storage/install status |
|---|---|---|
| OpenAI Codex CLI | Not executed yet | Install/update/uninstall and Windows shared CLI profile tested |
| Claude Code | Not executed yet | Install/update/uninstall and Windows shared CLI profile tested |
| Google Antigravity | Not executed yet | Install/update/uninstall and Windows shared CLI profile tested |
