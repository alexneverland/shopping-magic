# Gifts and planned purchases

Read before creating, updating, checking, or resolving gifts and dated planned
purchases.

## 1. Recording a gift or planned purchase

Example: *"I need a birthday present for George by December 15, around €30."*

1. Inspect `context` to check if a matching gift item already exists (avoid
   duplicate entries for the same recipient and occasion).
2. Add or update the item on the `Gifts` list (`list_name: "Gifts"`,
   `list_kind: "gifts"`) with only explicitly provided facts:
   - `name`: e.g., `"Birthday present for George"` (or the specific gift idea if
     the user named one)
   - `recipient`: `"George"`
   - `occasion`: `"Birthday"`
   - `target_date`: resolved ISO `YYYY-MM-DD` date
   - `budget_amount` / `budget_currency`: e.g., `"30"` / `"EUR"` (omit if unknown)
   - `status`: `"idea"` (just brainstorming), `"planned"` (scheduled/decided), or
     `"needed"` (still to buy; good default for *"I need a present by..."*)
   - `occasion_recurring`: default `false`. Set `true` **only** if the user
     explicitly states the occasion repeats annually or wants it tracked every
     year.
3. Never invent a person's birthday date, budget, or gift idea if the user did
   not state it.

## 2. Answering gift & due-date queries

- *"Do I have any gifts coming up?"* / *"What purchases are due this week?"*:
  - Check `item-list` (`status: "open"`, optionally `list_kind: "gifts"` or
    `due_before` / `due_after`).
  - Clearly separate **planned/pending gifts** (`idea`, `planned`, `needed`,
    `partially_bought`) from **bought gifts**.
- *"What should I buy for George?"*:
  - Check open items with `recipient: "George"` AND past purchases (`purchase-list`
    with `query: "George"` or notes) if helpful, keeping ideas/pending items
    separate from historical purchases.
- *"Did I already get Sofia's present?"*:
  - Check both `item-list` (`status: "all"`, `recipient: "Sofia"`) and
    `purchase-list` (`query: "Sofia"`).
  - Never claim a gift is bought unless its status is `"bought"` or a matching
    actual purchase is recorded in `purchases`.
  - When a user confirms buying a gift, use `purchase-log` with the gift's
    `item_id` (passing actual store/price/date if known) so the purchase history
    and gift status stay accurate.
