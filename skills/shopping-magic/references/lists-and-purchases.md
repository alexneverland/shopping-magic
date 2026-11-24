# Lists, categories, and purchase history

Read before adding, updating, listing, or clearing shopping items, inferring or
correcting categories, logging purchases, or answering history/spending queries.

## 1. Conversational lists & implicit references

1. Call `context` first to inspect current `lists`, `open_items`,
   `category_preferences`, and `products`. Check `context.pagination`: if
   `open_items.truncated` or `products.truncated` is `true`, paginate with
   `item-list` (`status: "open"`, `offset`) or `product-search` (`query` /
   `offset`) before assuming an item or product is absent.
2. Resolve conversational updates in place:
   - *"We need milk and feta"* → `item-add` for each item with a unique
     `request_key`.
   - *"Actually, make that three cartons of milk"* → find the open milk item
     from `context` and call `item-update` with its `id` and `expected_revision`
     instead of creating a second row.
   - *"Remove the feta, Sofia already bought it"* → if the user is just clearing
     their need (or reporting someone else bought it without logging a personal
     purchase event), either `item-remove` or `item-update` (`status: "bought"`)
     based on context; do not fabricate a personal `purchase-log` entry with
     invented price/quantity.
   - *"Something for the bathroom"* → create a list item with `status: "idea"` or
     `"needed"` and notes capturing what they said without inventing specific
     products.
3. Preserve missing optional details as `null` or `""`. Never invent a quantity,
   unit, store, or price when the user did not provide one.
4. For destructive bulk removal (*"clear the supermarket list"*), summarize what
   will be cleared and ask for confirmation before calling `list-clear` with
   `confirm: true`.

## 2. Semantic category & list inference

Use your own semantic understanding — combined with the user's `lists`,
`category_preferences`, and `products` defaults from `context` — to choose the
destination list and optional subcategory:

- Groceries, dairy, produce, pantry staples → list `Supermarket` (`kind: "supermarket"`)
- Light bulbs, batteries, cleaning/home hardware, filters → list `Home` (`kind: "home"`)
- Gifts, presents → list `Gifts` (`kind: "gifts"`)
- Medicines, bandages, vitamins, first-aid → list `Pharmacy` (`kind: "pharmacy"`)
- Personal care items (e.g., shampoo, soap) → follow `category_preferences` /
  existing `Personal Care` list if the user prefers one, otherwise `Supermarket`
  is a sensible default.
- Other specialized stores or topics (pet shop, bakery, bookstore, hardware) →
  create/reuse a clear custom list when helpful (`kind: "custom"`).

When the user corrects a placement (e.g., *"No, put those batteries under Home"*):

1. Move the item with `item-update` (`list_name: "Home"`, `list_kind: "home"`,
   `remember_list_preference: true`).
2. When the correction reflects a general rule (e.g., *"Always put shampoo under
   Personal Care"*), also save a rule with `category-pref-set`.

Ask a clarification question only when picking the wrong list/item would
materially affect the result (for example, two distinct open items for the same
product).

## 3. Confirmed needs vs. predictions

When the user asks *"What do I need for the house?"*, *"What's left on the
supermarket list?"*, or *"What am I still missing?"*:

- Query `item-list` (with `status: "open"` and the matching `list_name` or
  `list_kind`) or inspect `open_items` from `context`.
- Present the actual confirmed open items first, cleanly grouped by list/status
  (including remaining quantity for `partially_bought` items).
- Only if relevant `replenishment_suggestions` are overdue or due soon, you may
  mention them in a **separate, clearly labeled secondary section** (e.g.,
  *"Estimated restocks coming up (not on your list yet)"*). Never merge
  predictions into the confirmed shopping list.

## 4. Logging purchases & partial fulfillment

When the user reports actual purchases:

- *"I bought milk and feta today, but couldn't find coffee"* →
  - Call `purchase-log` for milk (passing its matching open `item_id` and
    `expected_item_revision`) and for feta (passing its matching `item_id`).
  - Leave coffee untouched as an open item on the list.
- *"I needed six bottles of milk but bought four"* →
  - Call `purchase-log` with `item_id`, `expected_item_revision`, `quantity: "4"`,
    `unit: "bottles"`, and `fulfillment_mode: "auto"`.
  - The store records a purchase of 4 bottles and keeps the shopping item open
    with `status: "partially_bought"` and `remaining_quantity: "2"`.
- If `purchase-log` returns `incompatible_units` (e.g., item said `1 pack` and
  user bought `250 g`), ask or verify whether the purchase satisfied the whole
  item, and if confirmed, pass `fulfillment_mode: "mark_bought"` (or adjust
  remaining quantity via `item-update`).
- When the purchase location is missing, leave `store` empty even if the item
  has a preferred store. If you provide both item and product identifiers,
  use the same product. On `purchase_product_mismatch`, reload the records
  before retrying with the correct identifiers.
- When the user corrects a purchase quantity or confirms deletion of a purchase,
  use `purchase-update` or `purchase-remove`. The CLI updates the linked
  remainder when it can verify the prior item state. On
  `purchase_item_state_requires_review`, show the current remainder and ask
  what should remain on the list. Use `keep_item_state: true` for a confirmed
  history-only change. If the user also gives a new remainder, apply it with
  `item-update` and the current `expected_revision`.

## 5. Purchase history & spending questions

Use `purchase-list` and `spending-summary` with explicit filters (`query`,
`product_id`, `store`, `list_name`, `on`, `from_date`, `to_date`):

- Report exact stored purchase dates, quantities, stores, and prices.
- If `spending-summary` reports `is_complete: false` (`unpriced_count > 0`),
  state clearly how many purchases lack recorded prices and report the known
  subtotal without inventing missing amounts.
- Distinguish clearly between *"no recorded purchase in history"* and *"you
  never bought it in real life"*.
