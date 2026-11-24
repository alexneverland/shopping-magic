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


if __name__ == "__main__":
    unittest.main()
