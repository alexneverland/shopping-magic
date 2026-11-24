"""Unit tests for gifts, target dates, budgets, and planned vs purchased distinction."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/shopping-magic/scripts"))
import shopping_store  # noqa: E402


class GiftsAndPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sm-gifts-")
        self.addCleanup(self.tmp.cleanup)
        self.store = shopping_store.Store(Path(self.tmp.name))

    def test_gift_planning_and_non_recurring_default(self) -> None:
        george = self.store.handle({
            "action": "item-add",
            "request_key": "g-1",
            "name": "Birthday present for George",
            "list_name": "Gifts",
            "list_kind": "gifts",
            "recipient": "George",
            "occasion": "Birthday",
            "target_date": "2026-12-15",
            "budget_amount": "30",
            "budget_currency": "EUR",
            "status": "needed",
        })["result"]
        sofia = self.store.handle({
            "action": "item-add",
            "request_key": "g-2",
            "name": "Book for Sofia",
            "list_name": "Gifts",
            "list_kind": "gifts",
            "recipient": "Sofia",
            "occasion": "Name day",
            "target_date": "2026-12-05",
            "status": "planned",
            "occasion_recurring": True,
        })["result"]
        self.assertFalse(george["occasion_recurring"])
        self.assertTrue(sofia["occasion_recurring"])

        due_early = self.store.handle({
            "action": "item-list",
            "list_kind": "gifts",
            "due_before": "2026-12-10",
        })["result"]
        self.assertEqual([g["recipient"] for g in due_early], ["Sofia"])

        # Planned gift is not a purchase yet.
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"], [])

        # Buying Sofia's gift resolves it cleanly and leaves George open.
        bought = self.store.handle({
            "action": "purchase-log",
            "request_key": "g-buy-sofia",
            "item_id": sofia["id"],
            "expected_item_revision": sofia["revision"],
            "bought_on": "2026-12-01",
            "store": "Public",
            "price": "18.90",
            "currency": "EUR",
        })["result"]
        self.assertEqual(bought["updated_item"]["status"], "bought")

        ctx = self.store.handle({"action": "context", "as_of": "2026-12-02"})["result"]
        self.assertEqual([g["recipient"] for g in ctx["upcoming_gifts"]], ["George"])
        self.assertFalse(ctx["pagination"]["open_items"]["truncated"])
        self.assertFalse(ctx["pagination"]["upcoming_gifts"]["truncated"])

    def test_context_queries_gifts_independently_and_exposes_truncation(self) -> None:
        # 105 high-priority supermarket items push normal-priority items past open_items limit (100),
        # yet upcoming_gifts still returns the normal-priority gift and pagination flags truncation.
        for idx in range(105):
            self.store.handle({
                "action": "item-add",
                "request_key": f"bulk-sm-{idx}",
                "name": f"Grocery Staple {idx:03d}",
                "list_name": "Supermarket",
                "list_kind": "supermarket",
                "priority": "high",
            })

        gift = self.store.handle({
            "action": "item-add",
            "request_key": "gift-independent",
            "name": "Gift for Nikos",
            "list_name": "Gifts",
            "list_kind": "gifts",
            "recipient": "Nikos",
            "occasion": "Birthday",
            "target_date": "2026-12-03",
            "priority": "normal",
        })["result"]

        ctx = self.store.handle({"action": "context", "as_of": "2026-12-02"})["result"]
        self.assertEqual(len(ctx["open_items"]), 100)
        self.assertNotIn(gift["id"], [item["id"] for item in ctx["open_items"]])
        self.assertEqual([g["id"] for g in ctx["upcoming_gifts"]], [gift["id"]])

        self.assertEqual(ctx["pagination"]["open_items"], {
            "returned": 100,
            "total": 106,
            "truncated": True,
            "next_offset": 100,
        })
        self.assertEqual(ctx["pagination"]["products"], {
            "returned": 100,
            "total": 106,
            "truncated": True,
            "next_offset": 100,
        })
        self.assertEqual(ctx["pagination"]["upcoming_gifts"], {
            "returned": 1,
            "total": 1,
            "truncated": False,
            "next_offset": None,
        })

        next_page = self.store.handle({
            "action": "item-list",
            "status": "open",
            "offset": ctx["pagination"]["open_items"]["next_offset"],
            "limit": 100,
        })["result"]
        self.assertEqual(len(next_page), 6)
        self.assertIn(gift["id"], [item["id"] for item in next_page])

        gifts_page = self.store.handle({
            "action": "item-list",
            "status": "open",
            "gifts_only": True,
        })["result"]
        self.assertEqual([g["id"] for g in gifts_page], [gift["id"]])


if __name__ == "__main__":
    unittest.main()
