# Shopping Magic

A local-first AI shopping assistant skill for **OpenAI Codex CLI**, **Claude Code**, and **Google Antigravity**.

Talk to your agent naturally — *"We're out of feta"*, *"Add four E27 light bulbs"*, *"I needed six bottles of milk, bought four"*, *"I need a birthday present for George by December 15, around €30"*, or *"When did I last buy coffee?"* — while a zero-dependency Python standard-library SQLite engine stores your lists, purchase history, gift plans, and replenishment cycles on your own computer.

**Version 0.1.0** · [Download the latest release (ZIP and SHA-256)](https://github.com/alexneverland/shopping-magic/releases/latest) · [All releases](https://github.com/alexneverland/shopping-magic/releases) · [Verification record](VERIFICATION.md)

## Why Shopping Magic exists

Most list tools force people to think like a database (picking lists, menus, units, and fields before typing a single item), while most chat memories forget quantities, mix planned items with actual purchases, or lose track of when you last bought something.

Shopping Magic splits responsibilities cleanly:

- **Your host AI model** handles human language: intent, Greek and English phrasing, synonyms, relative dates, category inference, and conversational follow-ups (*"make the milk three"*, *"remove the feta"*).
- **The Python SQLite helper** (`skills/shopping-magic/scripts/shopping_store.py`) handles structured persistence, exact `Decimal` quantity/spending math, transactions, concurrency locking, idempotent retries, and schema migrations — with zero regex/keyword intent parsers, zero API keys, and zero network calls.

## Main features

- **Natural shopping lists:** Multiple named lists (`Supermarket`, `Home`, `Gifts`, `Pharmacy`, `Personal Care`, or any custom list), optional quantities/units, brands, preferred stores, priorities, target dates, and notes. Missing optional details never block adding an item.
- **Automatic category inference & memory:** The agent infers the right list from context and remembers your corrections (e.g., *"No, put batteries under Home"*) via product defaults and topic routing preferences.
- **Durable purchase history:** Logs actual purchases separately from shopping needs with dates, stores, quantities, and optional prices. Deleting a list item never erases past purchase history.
- **Partial purchases & unit safety:** Buying 4 out of 6 needed bottles keeps 2 bottles open (`partially_bought`). Metric conversions (`kg` ↔ `g`, `l` ↔ `ml`) and singular/plural Greek/English unit forms work automatically; incompatible units (e.g., `bottles` vs `kg`) roll back cleanly instead of guessing.
- **Spending totals without guesswork:** Summarizes spending by currency and store, and explicitly flags incomplete totals whenever some purchases lack recorded prices.
- **Gifts & dated purchases:** Tracks recipient, occasion, target date, approximate budget, and status (`idea` / `planned` / `needed` / `bought`), keeping planned gifts distinct from bought gifts and treating occasions as non-recurring unless stated otherwise.
- **Intelligent replenishment:** Supports explicit intervals (*"every 7 days"*, *"every 3 months"*), outlier-resistant median estimates from purchase history, and one-off cycle feedback without silently overwriting your explicit long-term schedule. Predictions stay separate from confirmed open shopping lists.

## Conversational examples

### Everyday shopping and follow-up corrections

```text
You:   We need milk and feta.
Agent: Added Milk and Feta to Supermarket.

You:   Actually, make that three cartons of milk. And add four E27 light bulbs.
Agent: Updated Milk to 3 cartons (Supermarket). Added E27 light bulbs (4) to Home.

You:   Remove the feta, Sofia already bought it.
Agent: Removed Feta from Supermarket.
```

### Partial purchases and history queries

```text
You:   I needed six bottles of milk, bought four at Sklavenitis for €5.20, and couldn't find coffee.
Agent: Logged 4 bottles of milk (€5.20 at Sklavenitis); 2 bottles remain open on Supermarket. Coffee stays pending.

You:   What do I need for the house?
Agent: Home (confirmed list):
       - E27 light bulbs (4)
       Estimated restocks coming up (not on your list yet):
       - Water filter — last changed 3 months ago (around today).

You:   How much did I spend at Sklavenitis?
Agent: Recorded total: €5.20 across 1 priced purchase (note if any unpriced purchases exist).
```

### Replenishment and gifts (including Greek input)

```text
You:   Πήρα σήμερα βιταμίνη D από το φαρμακείο, κρατάει περίπου 30 μέρες.
Agent: Καταχωρήθηκε η αγορά για σήμερα· εκτιμώμενη επόμενη αγορά σε 30 ημέρες.

You:   I need a birthday present for George by December 15, around €30.
Agent: Added to Gifts: Birthday present for George (due 2026-12-15, budget ~€30, status: needed).
```

## How category inference works

Shopping Magic does not hardcode a brittle keyword dictionary in Python. Instead, the skill instructions guide the host model to:

1. Read `context` first to inspect existing `lists`, `category_preferences`, and `products`.
2. Route clear requests semantically (groceries → `Supermarket`, hardware/household → `Home`, presents → `Gifts`, medicine/first aid → `Pharmacy`, personal care → `Personal Care` or `Supermarket` based on your saved preference).
3. Persist explicit corrections using `item-update` (`remember_list_preference: true`) and `category-pref-set` so future sessions follow your preferences automatically across Codex, Claude Code, and Antigravity.

## How replenishment works

1. **Explicit rules first:** If you say *"I'll need coffee again in a week"* or *"I change the water filter every 3 months"*, Shopping Magic stores `interval_days` or `calendar_months` on the product and calculates the next expected date from your latest purchase.
2. **History-based estimates:** When no explicit schedule is set and at least two purchase intervals (or explicit cycle feedback) exist, `replenishment-suggestions` computes an outlier-resistant median interval and marks `confidence` (`high`, `medium`, `low`) and `regularity`.
3. **One-cycle feedback:** Telling the agent *"that coffee lasted 12 days this time"* updates that specific purchase's `actual_duration_days` without silently overwriting your explicit long-term rule.
4. **Strict separation:** Predictions always carry `is_estimate: true` and are never silently converted into confirmed shopping list items or false purchases.

## Requirements

- **Python 3.10+** with standard-library `sqlite3`. Zero `pip install` steps.
- A local agent capable of reading `SKILL.md` / `references/*.md` and running Python locally (**OpenAI Codex CLI**, **Claude Code**, **Google Antigravity**, or a compatible custom agent).
- Your existing host agent account/runtime. The storage helper makes no network calls and requires no external services or API keys.

## Installation

Clone the repository (or extract the release ZIP), inspect the scripts, and run the installer from the repository root (`python3` on macOS/Linux if needed):

```text
git clone https://github.com/alexneverland/shopping-magic.git
cd shopping-magic
```

### Windows PowerShell

```powershell
# Codex CLI (user scope -> %USERPROFILE%\.agents\skills\shopping-magic)
python tools\manage_skill.py install --agent codex

# Claude Code (user scope -> %USERPROFILE%\.claude\skills\shopping-magic)
python tools\manage_skill.py install --agent claude-code

# Google Antigravity (user scope -> %USERPROFILE%\.gemini\config\skills\shopping-magic)
python tools\manage_skill.py install --agent antigravity
```

### macOS / Linux

```sh
python3 tools/manage_skill.py install --agent codex
python3 tools/manage_skill.py install --agent claude-code
python3 tools/manage_skill.py install --agent antigravity
```

### Project-scoped or custom agent installation

```powershell
# Project-scoped (Codex and Antigravity share .agents\skills\shopping-magic; Claude Code uses .claude\skills\shopping-magic)
python tools\manage_skill.py install --agent codex --scope project --project "C:\MyWorkspace"

# Custom agent (exact final folder name must be shopping-magic)
python tools\manage_skill.py install --agent custom --destination "C:\MyAgent\skills\shopping-magic"
```

| Host | Personal destination | Project destination | Explicit trigger |
|---|---|---|---|
| Codex CLI | `~/.agents/skills/shopping-magic/` | `.agents/skills/shopping-magic/` | `$shopping-magic` |
| Claude Code | `~/.claude/skills/shopping-magic/` | `.claude/skills/shopping-magic/` | `/shopping-magic` |
| Antigravity | `~/.gemini/config/skills/shopping-magic/` | `.agents/skills/shopping-magic/` | `/shopping-magic` |

After installing, start a fresh session (or reload skills in your host) so the host discovers `shopping-magic`.

### Updating and uninstalling

The installer tracks exact file hashes in `.shopping-magic-install.json` and refuses to overwrite locally edited or unknown files or follow symlinks/junctions. Neither `update` nor `uninstall` ever deletes your SQLite database:

```powershell
python tools\manage_skill.py update --agent codex
python tools\manage_skill.py uninstall --agent codex
```

## Local data storage

By default, all hosts running under the same OS user share one local SQLite file (`shopping-magic.sqlite3`), completely separate from Meal Magic:

| OS | Default data directory |
|---|---|
| Windows | `%USERPROFILE%\.shopping-magic\data` |
| macOS | `~/Library/Application Support/ShoppingMagic` |
| Linux / other | `$XDG_DATA_HOME/shopping-magic` (fallback `~/.local/share/shopping-magic`) |

To use an isolated profile (for testing or another household), set `SHOPPING_MAGIC_DATA_DIR` in your environment or pass `--data-dir PATH` to `shopping_store.py`:

```powershell
$env:SHOPPING_MAGIC_DATA_DIR = "C:\IsolatedShoppingProfile"
```

On Windows without an override, `.origins.json` records the shared profile's ID and OS file identity under a cross-process file lock so a missing, replaced, or corrupted database fails closed rather than silently creating a blank profile.

## Backup, export, and import

- **Complete backup:** Close all agents using Shopping Magic and copy the entire data directory (both `shopping-magic.sqlite3` and `.origins.json` on Windows). To restore, close all agents and copy the directory back (or point `--data-dir` / `SHOPPING_MAGIC_DATA_DIR` at the restored copy).
- **Portable JSON export/import:**
  - `{"action":"export"}` emits a portable `schema_version: 1` JSON snapshot of all lists, products, category preferences, shopping items, and purchases (excluding internal `mutation_requests` receipts).
  - `{"action":"import","request_key":"...","data":<export result>}` atomically restores an exported snapshot into an **empty** destination profile (`import_requires_empty_profile` refuses non-empty targets).

## Running tests

All automated tests run in isolated temporary directories using synthetic data only:

```powershell
python tools\check_repository.py
python tools\verify_scenarios.py
python -m unittest discover -s tests -v
python tools\build_package.py
```

## Known limitations

- **No background notifications or daemon:** Shopping Magic stores dates and expectations locally in SQLite, but does not run a background scheduler or send push notifications for reminders.
- **No external price scraping or receipt OCR:** The storage engine makes zero network calls; price checks or receipt parsing depend strictly on what the user shares with the host agent.
- **Host model behavior depends on your host/LLM:** Automated Python tests verify the storage CLI, schema, replenishment calculations, installer, and packaging deterministically. Live conversational evaluation across Codex CLI, Claude Code, and Antigravity is documented in [tests/agent-scenarios.md](tests/agent-scenarios.md) and has not yet been executed by maintainers.
- **Not medical advice:** Pharmacy and supplement durations are simple calendar math on intervals you explicitly report, never clinical or dosage recommendations.

## Security and privacy

Shopping Magic keeps all records local in a single-user unencrypted SQLite database governed by OS file permissions, makes zero network calls, and treats stored text (names, notes, stores, recipients, imports) strictly as inert data. Note that your host AI agent still processes any records it reads under that host's own provider and privacy terms; local storage does not imply offline LLM inference unless your host itself runs locally. See [SECURITY.md](SECURITY.md) for full details.

## License

[MIT License](LICENSE)
