# Storage CLI contract

Requires Python 3.10+ with standard-library `sqlite3`. There are no pip
dependencies and no network calls. Invoke the absolute path to
`scripts/shopping_store.py` relative to the loaded skill folder.

Pass one UTF-8 JSON object via stdin or `--request-file /abs/path/request.json`.
Output is one UTF-8 JSON envelope: `{"ok":true,"result":...}` (plus top-level
`"storage"` on `context`) or `{"ok":false,"error":"code"}` with nonzero exit
status. `--version` and `--help` are informational CLI flags.

## PowerShell UTF-8 pattern (Windows)

On Windows PowerShell, pass requests through a temporary UTF-8 file (no BOM) so
Greek or Unicode text never gets corrupted by console codepages, and delete the
temporary file afterward even on failure:

```powershell
$req = Join-Path ([IO.Path]::GetTempPath()) ('shopping-magic-' + [guid]::NewGuid() + '.json')
try {
    [IO.File]::WriteAllText($req, '{"action":"context"}', [Text.UTF8Encoding]::new($false))
    python 'C:\path\to\shopping-magic\skills\shopping-magic\scripts\shopping_store.py' --request-file $req
} finally {
    Remove-Item -LiteralPath $req -ErrorAction SilentlyContinue
}
```

From Python hosts, invoke `subprocess.run([sys.executable, script_path], input=json.dumps(req), encoding="utf-8", ...)` with `shell=False`. Never interpolate user text or JSON into shell strings, and never place temporary request files inside repositories.

## Data directory and Windows shared profile

Precedence: `--data-dir PATH`, then `SHOPPING_MAGIC_DATA_DIR`, then:

| OS | Default directory |
|---|---|
| Windows | `%USERPROFILE%\.shopping-magic\data` |
| macOS | `~/Library/Application Support/ShoppingMagic` |
| Linux / other | `$XDG_DATA_HOME/shopping-magic` (fallback `~/.local/share/shopping-magic`) |

The SQLite database is `shopping-magic.sqlite3`, completely separate from Meal
Magic. Storing data inside the installed skill directory is refused
(`data_directory_inside_skill`). Symlinks and Windows reparse points/junctions in
target or ancestor paths are refused (`redirected_profile_path`).

When using the default Windows location without `--data-dir` or
`SHOPPING_MAGIC_DATA_DIR`, a cross-process file lock and `.origins.json`
identity guard ensure Codex CLI, Claude Code, and Antigravity share the same
physical file safely:

- `storage-info` is read-only and creates no directories, locks, or database.
- First write/context stages a clean SQLite file and atomically links it into
  place.
- If a previously initialized database goes missing (`shared_profile_missing`),
  changes OS file identity (`shared_profile_identity_changed`), or `.origins.json`
  is malformed (`invalid_profile_origins`), the helper fails closed rather than
  creating a blank profile.
- Unrecognized SQLite/non-SQLite files (`unrecognized_database`), corrupt DBs
  (`invalid_database`), and newer schema versions (`unsupported_schema_version`)
  are refused without resetting user data.

## Actions reference

Unknown top-level fields fail immediately with `unknown_fields` before opening
the database or replaying receipts.

| Action | Required fields | Optional fields | Result summary |
|---|---|---|---|
| `storage-info` | — | — | Read-only paths, existence, `profile_id`, table counts |
| `context` | — | `as_of` (`YYYY-MM-DD`), `horizon_days` (1–90, default 7), `recent_purchases_limit` (1–100, default 20) | Lists, open items, upcoming gifts (queried independently), category prefs, products, recent purchases, secondary `replenishment_suggestions`, `pagination` (`returned`, `total`, `truncated`, `next_offset`), top-level `storage` |
| `list-save` | `name` | `id`, `kind`, `notes`, `archived` | Created or updated list with counts |
| `list-list` | — | `include_archived` (bool, default false) | Lists with `open_count` and `total_count` |
| `product-save` | `name` | `id`, `expected_revision`, `aliases`, `default_list_id`, `default_list_name`, `default_list_kind`, `default_category`, `default_unit`, `default_brand`, `default_store`, `notes` | Canonical product identity and defaults |
| `product-search` | — | `query`, `list_id`, `limit` (1–100, default 20), `offset` | Accent/case-insensitive product search |
| `category-pref-set` | `topic`, plus `list_id` or `list_name` | `list_kind`, `category`, `notes` | Remembered topic -> list/category routing rule |
| `category-pref-remove` | `id` | — | Removed routing preference |
| `item-add` | `name`, `request_key`, plus `list_id` or `list_name` (or linked product default list) | `list_kind`, `product_id`, `category`, `quantity`, `unit`, `brand`, `preferred_store`, `notes`, `priority`, `status` (`idea`/`planned`/`needed`), `target_date`, `recipient`, `occasion`, `occasion_recurring`, `budget_amount`, `budget_currency` | Created shopping item with `revision: 1` |
| `item-get` | `id` | — | Single hydrated shopping item |
| `item-list` | — | `list_id`, `list_name`, `list_kind`, `category`, `status` (default `open`, or `all`, or exact status), `recipient`, `product_id`, `due_before`, `due_after`, `gifts_only` (bool), `query`, `limit` (1–100, default 50), `offset` | Matching shopping items only (no replenishment estimates) |
| `item-update` | `id`, `expected_revision`, `request_key` (plus at least one updatable field) | `name`, `list_id`, `list_name`, `list_kind`, `category`, `quantity`, `remaining_quantity`, `unit`, `brand`, `preferred_store`, `notes`, `priority`, `status`, `target_date`, `recipient`, `occasion`, `occasion_recurring`, `budget_amount`, `budget_currency`, `remember_list_preference` | Updated item with incremented revision |
| `item-remove` | `id`, `request_key` | `expected_revision` | Removed item (`purchases` preserved) |
| `list-clear` | `request_key`, `confirm` (`true`), plus `list_id` or `list_name` | `statuses` (list of statuses; default open statuses) | Removed item count; preserves purchases |
| `purchase-log` | `bought_on` (`YYYY-MM-DD`), `request_key`, plus `item_id` or `product_id` or `product_name` | `expected_item_revision`, `list_id`, `list_name`, `list_kind`, `category`, `quantity`, `unit`, `brand`, `store`, `price`, `currency`, `notes`, `fulfillment_mode` (`auto`/`mark_bought`/`keep_open`), `expected_next_on` (purchase-specific override), `replenishment_days`, `replenishment_months`, `replenishment_note` | Recorded purchase + optional `updated_item` + `product` + `suggested_next_on` |
| `purchase-update` | `id`, `request_key` (plus at least one updatable field) | `bought_on`, `quantity`, `unit`, `brand`, `store`, `price`, `currency`, `notes`, `expected_next_on`, `finished_on`, `actual_duration_days`, `keep_item_state` | Updated purchase / one-cycle duration feedback |
| `purchase-list` | — | `product_id`, `query`, `store`, `list_id`, `list_name`, `category`, `on`, `from_date`, `to_date`, `limit` (1–100, default 50), `offset` | Purchase history newest first |
| `purchase-remove` | `id`, `request_key`, `confirm` (`true`) | `keep_item_state` | Removed purchase record |
| `spending-summary` | — | `store`, `list_id`, `list_name`, `category`, `product_id`, `on`, `from_date`, `to_date` | Exact currency totals, store breakdown, `is_complete` flag |
| `replenishment-set` | `mode` (`none`/`interval_days`/`calendar_months`), plus `product_id` or `product_name` | `value` (int >= 1 when mode != `none`), `basis_quantity`, `basis_unit`, `note`, `source` (`user_explicit`/`user_corrected`), `request_key` | Updated product replenishment rule |
| `replenishment-suggestions` | — | `as_of`, `horizon_days` (1–365, default 14), `product_id`, `query`, `list_id`, `list_name`, `include_all` (bool) | Read-only replenishment estimates (`is_estimate: true`) |
| `export` | — | — | Portable JSON snapshot (`schema_version: 3`), omitting `mutation_requests` |
| `import` | `data`, `request_key` | — | Atomic snapshot restore into an empty profile only |

## Validation, types, and idempotency

- **Enums:**
  - `list.kind`: `supermarket`, `home`, `gifts`, `pharmacy`, `personal_care`, `custom`
  - `priority`: `low`, `normal`, `high`, `urgent`
  - `item.status`: `idea`, `planned`, `needed`, `partially_bought`, `bought`, `cancelled`
  - `fulfillment_mode`: `auto`, `mark_bought`, `keep_open`
  - `replenishment_mode`: `none`, `interval_days`, `calendar_months`
- **Decimals & numbers:** `quantity`, `remaining_quantity`, `price`, and
  `budget_amount` use exact decimal arithmetic (`> 0`, except `price` and
  `remaining_quantity` allow `0`). Booleans, `NaN`, and `Infinity` are rejected.
  Nullable fields (`quantity`, `price`, `target_date`, `budget_amount`,
  `expected_next_on`, `finished_on`, `actual_duration_days`, `product_id`,
  `list_id`, `item_id`) accept `null`. String metadata fields (`category`,
  `unit`, `brand`, `preferred_store`, `store`, `notes`, `recipient`, `occasion`,
  `currency`, `budget_currency`, `replenishment_note`) expect strings (`""` to
  clear).
- **Idempotency (`request_key`):** Every mutating action that takes `request_key`
  stores its receipt in `mutation_requests`. Re-sending the exact same
  `request_key` and payload safely returns the original result without creating
  duplicates. Re-using a `request_key` with different fields fails with
  `request_key_conflict`.
- **Unit compatibility on partial purchase:** In `purchase-log` with
  `fulfillment_mode="auto"`, when both the item's `remaining_quantity` and the
  purchase's `quantity` are known, the helper checks unit compatibility
  (including metric conversions such as `kg <-> g` and `l <-> ml`, plus Greek/English
  singular-plural unit synonyms). Incompatible units (e.g., `bottles` vs `kg`)
  roll back the entire transaction and return `incompatible_units`. Use
  `fulfillment_mode="mark_bought"` when the user confirms an item is fully
  covered despite a different unit.
- **Unit-only item edits:** When changing the unit of a quantified item without
  supplying a new quantity, the helper converts both stored quantities. An
  incompatible unit fails with `incompatible_units`. An explicit
  `remaining_quantity` is always interpreted in the requested unit.
- **Purchase identity and store:** When supplied together, `item_id`,
  `product_id`, and `product_name` must identify the same product, including
  recognized aliases. A mismatch returns `purchase_product_mismatch`.
  Purchase `store` is empty when omitted. A list item's `preferred_store`
  never supplies the actual store used for spending totals.
- **Linked purchase corrections:** A quantity or unit change on an automatic
  fulfillment reapplies the purchase to the original remaining quantity. A
  removal restores the prior status and remainder. These changes are atomic
  and require evidence that the linked item revision still matches. Otherwise,
  the CLI returns `purchase_item_state_requires_review`. After the user
  decides to change history while preserving the current list item, retry
  with `keep_item_state: true`. Quantity and unit changes on a purchase
  recorded with `mark_bought` or `keep_open` preserve that decision.
- **Quantity-based replenishment:** In `interval_days` mode, a positive
  `basis_quantity` makes `value` the number of days for that quantity.
  `basis_unit` uses compatible measurement units or a piece count when blank.
  The latest purchase quantity scales the duration, rounded up to a whole day.
  A missing quantity or incompatible unit yields no date.
  Saving a rule without a basis replaces it with a fixed interval.

## Export and import

`export` emits all active domain tables (`lists`, `products`,
`category_preferences`, `shopping_items`, `purchases`) without `mutation_requests`.
Schema 3 snapshots preserve purchase fulfillment metadata and the quantity
basis of replenishment rules. Schema 1 and 2 snapshots can still be imported.
Existing SQLite databases migrate to schema 3 on use, preserving purchases
and retry receipts. Legacy linked purchases have no
fulfillment metadata, so corrections that would affect the list require review.
`import` requires a completely empty destination profile (no rows in any domain
table or `mutation_requests`), failing with `import_requires_empty_profile`
otherwise. For a complete byte-exact backup including retry receipts, close all
agents and copy the whole data directory.
