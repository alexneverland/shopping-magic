# Shopping Magic verification record

This document separates what has been verified deterministically by running local
checks and automated tests from live-agent conversational behavior, which must be
evaluated separately.

## Observed deterministic verification

Executed from the repository root for 0.1.1 on Windows with Python 3.14.10:

```powershell
python tools\check_repository.py
python tools\verify_scenarios.py
python -m unittest discover -s tests -v
python tools\build_package.py
```

- `tools/check_repository.py`: passed (`Repository checks passed (layout, local links, LICENSE sync, stdlib imports, whitespace).`).
- `tools/verify_scenarios.py`: Level 1 passed **8/8** deterministic storage scenarios (`natural_needs_and_followup_edits`, `category_correction_and_preference`, `partial_purchase_and_unit_guard`, `confirmed_needs_vs_predictions`, `history_and_incomplete_spending`, `replenishment_feedback_and_historical_median`, `gifts_planned_vs_purchased`, `prompt_injection_and_readonly_invariants`). Level 2 (`LiveAgentAdapter`) reported `status: "not_executed"` (`no_live_adapter_configured`).
- `python -m unittest discover -s tests -v`: passed **53/53** unit and lifecycle tests. Coverage includes linked purchase correction and removal, stale item revisions, product identity conflicts, store attribution, unit-only conversion, schema 1 migration rollback, and version 1 and 2 snapshot restore.
- `tools/build_package.py`: built `dist/shopping-magic-0.1.1.zip` (31 allowlisted entries) and its `.sha256` sidecar.

## Live conversational verification status

Automated CLI and Python tests do **not** establish how any specific LLM follows
`SKILL.md` in live conversations. See [tests/agent-scenarios.md](tests/agent-scenarios.md)
for the manual synthetic evaluation protocol.

| Host | Live conversational status | Deterministic storage/install status |
|---|---|---|
| OpenAI Codex CLI | Not executed. Isolated host profile reported it was signed out. | Install/update/uninstall and Windows shared CLI profile tested |
| Claude Code | Not executed. Isolated host profile reported it was signed out. | Install/update/uninstall and Windows shared CLI profile tested |
| Google Antigravity | Not executed. Isolated preflight produced no model reply to verify. | Install/update/uninstall and Windows shared CLI profile tested |
