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

- *"I bought coffee today. This pack will last a week."* → call `purchase-log`
  with the purchase date and `expected_next_on` seven days later for this purchase.
- *"I change the water filter every three months."* → call `replenishment-set`
  with `mode: "calendar_months"`, `value: 3`, `source: "user_explicit"`.
- *"We usually buy toilet paper every two weeks."* → `mode: "interval_days"`,
  `value: 14`, `source: "user_explicit"`.
- *"Usually one bar of soap lasts me a week."* → use `replenishment-set` with
  `mode: "interval_days"`, `value: 7`, `basis_quantity: "1"`, and
  `basis_unit: "piece"`. When the user later buys five, record `quantity: "5"`
  and use the store's estimate of 35 days from that purchase. A count supplied
  without a unit also works with a piece-based rule.
- *"Usually 400 grams of kasseri lasts me a week."* → use `basis_quantity: "400"`
  and `basis_unit: "g"`. After a purchase of `0.8 kg`, the estimate is
  14 days. When a message includes a purchase and a new standing rule, record
  the purchase, save the rule on its product, then fetch the updated estimate.

A quantity-based rule projects the duration of the latest purchase at the saved
rate, rounding a fraction of a day up. A date given for that specific purchase
takes precedence. When the purchase has no quantity or its unit is incompatible,
the helper returns no date (`quantity_estimate_unavailable`). Ask for the
quantity or comparable unit if the user needs a date. An earlier stock balance
and package-to-weight conversions are outside this calculation.

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
