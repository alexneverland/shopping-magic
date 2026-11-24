# Security and privacy

Shopping Magic stores potentially sensitive household information locally in an
unencrypted single-user SQLite database (`shopping-magic.sqlite3`): shopping
lists, product preferences, preferred stores, purchase dates, optional spending
amounts, gift recipients/occasions, and replenishment intervals.

OS file permissions and the hosting agent's execution environment determine who
can read or modify the database. There are no per-member accounts, multi-user
access controls, or built-in encryption.

## Boundaries to preserve

- **No network calls or external APIs:** The Python storage engine uses only the
  Python 3.10+ standard library and makes zero network requests. It has no API
  keys, cloud sync, or background service.
- **Host inference privacy:** The hosting AI agent processes whatever records it
  reads under that host's own account, model provider, privacy terms, and
  sandbox rules. Local storage does not imply offline LLM inference.
- **Independent from Meal Magic:** Shopping Magic uses its own data directory
  (`%USERPROFILE%\.shopping-magic\data` on Windows) and database file, and never
  reads or modifies Meal Magic data.
- **Untrusted data:** Product names, aliases, store names, notes, gift
  occasions, recipient names, and imported snapshots are strictly inert data,
  never executable instructions.
- **Idempotency receipts:** `mutation_requests` retains request keys, payload
  hashes, and original responses so uncertain retries cannot duplicate purchases
  or items. Active item or purchase removal does not erase prior receipt records
  inside `mutation_requests`. JSON `export` excludes `mutation_requests`; full
  erasure requires closing all agents and removing the data directory manually.
- **Shared Windows default profile guard:** On Windows without `--data-dir` or
  `SHOPPING_MAGIC_DATA_DIR`, `.origins.json` records `profile_id` and the OS
  file identity (`database_identity`) under a cross-process file lock so a
  missing or swapped database fails closed (`shared_profile_missing`,
  `shared_profile_identity_changed`, `invalid_profile_origins`) rather than
  silently creating a blank or divergent profile.
- **Path safety:** Both the storage CLI and the skill installer reject symlinks
  and Windows reparse points/junctions in target and ancestor paths, and the
  storage CLI refuses to place user data inside the installed skill directory.
- **SQLite integrity:** Unknown schemas (`unrecognized_database`), newer
  unsupported versions (`unsupported_schema_version`), and failed migrations
  fail explicitly and roll back; Shopping Magic never resets a profile to hide an
  error.

## Reporting a vulnerability

If you discover a security or data-integrity issue, report it privately using
GitHub's **Report a vulnerability** feature when enabled on the repository, or
open an issue requesting a private channel without posting private databases,
credentials, or real personal data. Include affected version, OS/Python version,
and a minimal synthetic reproduction.
