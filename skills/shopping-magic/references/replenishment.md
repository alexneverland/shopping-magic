# Intelligent replenishment & consumables

Read before saving or updating replenishment intervals, interpreting cycle
feedback, answering *"when did I buy X / when will it run out?"*, or surfacing
restock suggestions.

## 1. Fundamental boundaries

- **Predictions are always estimates (`is_estimate: true`).** They never create
  confirmed `shopping_items` and never create `purchases`.
- Ask or wait before adding a replenishment suggestion to a shopping list unless
  the user explicitly asks to add it.
- **No medical or dosing assumptions:** If a user records pharmacy items,
  vitamins, or medication with a duration (e.g., *"these tablets last 30 days"*),
  compute dates strictly from their stated interval or purchase dates. Never
  claim medical, dosage, or clinical accuracy, and never infer dosages from pack
  sizes alone.
- **No false background reminders:** Distinguish between:
  - *"I'll probably need coffee next week"* → store an expectation/interval
    (`replenishment-set` or `purchase-log` with `replenishment_days` /
    `expected_next_on`).
  - *"Remind me next Tuesday to buy coffee"* → SQLite storage has no background
    scheduler or push notifications. Be honest about that limitation; you can
    still add a shopping item with `target_date` (status `"planned"` /
    `"needed"`) so it shows up when queried, and state that no automatic
    notification will fire unless the host agent provides a separate reminder
    integration.

## 2. Explicit user intervals & calendar recurrence

When the user states how long a consumable lasts or how often they buy it:

- *"I bought coffee today. I'll need it again in a week."* → call `purchase-log`
  with `bought_on`, `replenishment_days: 7` (which saves `interval_days: 7` on
  the product and sets `expected_next_on` 7 days later).
- *"I change the water filter every three months."* → call `replenishment-set`
  with `mode: "calendar_months"`, `value: 3`, `source: "user_explicit"`.
- *"We usually buy toilet paper every two weeks."* → `mode: "interval_days"`,
  `value: 14`, `source: "user_explicit"`.

## 3. History-based estimation & transparency

When `replenishment-suggestions` evaluates products without an explicit rule:

- It looks at gaps between distinct purchase dates (`observed_intervals_count`)
  and any user-reported `actual_duration_days`.
- With at least 2 intervals (or explicit cycle feedback), it computes an
  outlier-resistant median estimate, `confidence` (`high`, `medium`, `low`), and
  `regularity` (`regular`, `moderate`, `irregular`).
- Always present historical calculations transparently as estimates (for
  example: *"Based on 3 previous intervals around 8 days, you may run out around
  YYYY-MM-DD — estimate, confidence: high"*), and distinguish them from
  user-defined schedules (`basis: "explicit_*"`).
- If `status` is `"insufficient_history"` or `"awaiting_purchase_history"`, say so
  plainly instead of guessing a date.

## 4. Handling user cycle feedback

Example: *"Actually, that coffee lasted twelve days this time."*

1. Inspect recent purchases (`purchase-list`) and the product's replenishment
   settings (`product-search` or `replenishment-suggestions`).
2. Record the one-cycle duration on the relevant purchase using `purchase-update`
   (`finished_on` or `actual_duration_days: 12`). This updates that specific
   cycle without silently overwriting an explicit long-term rule (e.g., 7 days).
3. If the user has an explicit rule that differs noticeably from recent feedback,
   mention the explicit rule and ask briefly whether this was a one-off cycle or
   whether they want to update their default interval (using `replenishment-set`
   with `source: "user_corrected"`).
