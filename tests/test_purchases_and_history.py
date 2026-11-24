"""Unit tests for purchase history, partial purchases, units, and spending totals."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/shopping-magic/scripts"))
import shopping_store  # noqa: E402


class PurchasesAndHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sm-purchases-")
        self.addCleanup(self.tmp.cleanup)
        self.store = shopping_store.Store(Path(self.tmp.name))

    def test_partial_purchase_and_metric_unit_conversion(self) -> None:
        milk = self.store.handle({
            "action": "item-add",
            "request_key": "p-milk",
            "name": "Milk",
            "list_name": "Supermarket",
            "list_kind": "supermarket",
            "quantity": "6",
            "unit": "bottles",
        })["result"]

        # Buy 4 bottles -> 2 bottles remain open.
        first = self.store.handle({
            "action": "purchase-log",
            "request_key": "p-buy4",
            "item_id": milk["id"],
            "expected_item_revision": milk["revision"],
            "bought_on": "2026-11-24",
            "quantity": "4",
            "unit": "bottle",
        })["result"]
        self.assertEqual(first["updated_item"]["status"], "partially_bought")
        self.assertEqual(first["updated_item"]["remaining_quantity"], "2")

        # Buy remaining 2 bottles -> item resolves to bought.
        second = self.store.handle({
            "action": "purchase-log",
            "request_key": "p-buy2",
            "item_id": milk["id"],
            "expected_item_revision": first["updated_item"]["revision"],
            "bought_on": "2026-11-25",
            "quantity": "2",
            "unit": "μπουκάλια",
        })["result"]
        self.assertEqual(second["updated_item"]["status"], "bought")
        self.assertEqual(second["updated_item"]["remaining_quantity"], "0")

        # Metric unit conversion: 2 kg needed, 500 g bought -> 1.5 kg remaining.
        potatoes = self.store.handle({
            "action": "item-add",
            "request_key": "p-pot",
            "name": "Potatoes",
            "list_name": "Supermarket",
            "quantity": "2",
            "unit": "kg",
        })["result"]
        partial_pot = self.store.handle({
            "action": "purchase-log",
            "request_key": "p-pot-buy",
            "item_id": potatoes["id"],
            "bought_on": "2026-11-24",
            "quantity": "500",
            "unit": "g",
        })["result"]
        self.assertEqual(partial_pot["updated_item"]["status"], "partially_bought")
        self.assertEqual(partial_pot["updated_item"]["remaining_quantity"], "1.5")

    def test_incompatible_units_fail_atomically_and_mark_bought_overrides(self) -> None:
        coffee = self.store.handle({
            "action": "item-add",
            "request_key": "u-cof",
            "name": "Coffee",
            "list_name": "Supermarket",
            "quantity": "1",
            "unit": "pack",
        })["result"]
        with self.assertRaisesRegex(shopping_store.StoreError, "incompatible_units"):
            self.store.handle({
                "action": "purchase-log",
                "request_key": "u-cof-bad",
                "item_id": coffee["id"],
                "bought_on": "2026-11-24",
                "quantity": "250",
                "unit": "g",
            })
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"], [])
        self.assertEqual(self.store.handle({"action": "item-get", "id": coffee["id"]})["result"]["status"], "needed")

        # Explicit mark_bought resolves the item cleanly.
        resolved = self.store.handle({
            "action": "purchase-log",
            "request_key": "u-cof-ok",
            "item_id": coffee["id"],
            "bought_on": "2026-11-24",
            "quantity": "250",
            "unit": "g",
            "fulfillment_mode": "mark_bought",
        })["result"]
        self.assertEqual(resolved["updated_item"]["status"], "bought")
        self.assertEqual(resolved["purchase"]["quantity"], "250")

    def test_purchase_without_quantity_never_invents_one(self) -> None:
        result = self.store.handle({
            "action": "purchase-log",
            "request_key": "no-qty-1",
            "product_name": "Light Bulbs",
            "bought_on": "2026-11-20",
        })["result"]
        self.assertIsNone(result["purchase"]["quantity"])
        self.assertIsNone(result["purchase"]["price"])

    def test_spending_summary_marks_incomplete_totals_and_uses_exact_decimals(self) -> None:
        self.store.handle({
            "action": "purchase-log",
            "request_key": "sp-1",
            "product_name": "Milk",
            "bought_on": "2026-11-20",
            "store": "Market A",
            "price": "1.10",
            "currency": "EUR",
        })
        self.store.handle({
            "action": "purchase-log",
            "request_key": "sp-2",
            "product_name": "Feta",
            "bought_on": "2026-11-21",
            "store": "Market A",
            "price": "4.20",
            "currency": "EUR",
        })
        self.store.handle({
            "action": "purchase-log",
            "request_key": "sp-3",
            "product_name": "Bread",
            "bought_on": "2026-11-22",
            "store": "Market A",
        })
        summary = self.store.handle({
            "action": "spending-summary",
            "store": "Market A",
            "from_date": "2026-11-01",
            "to_date": "2026-11-30",
        })["result"]
        self.assertEqual(summary["purchase_count"], 3)
        self.assertEqual(summary["priced_count"], 2)
        self.assertEqual(summary["unpriced_count"], 1)
        self.assertFalse(summary["is_complete"])
        self.assertEqual(summary["totals_by_currency"], {"EUR": "5.3"})

    def test_purchase_update_remove_and_retry(self) -> None:
        logged = self.store.handle({
            "action": "purchase-log",
            "request_key": "pu-1",
            "product_name": "Tea",
            "bought_on": "2026-11-10",
            "price": "3.00",
            "currency": "EUR",
        })["result"]
        pid = logged["purchase"]["id"]
        # Exact retry replays cleanly without duplicate row.
        replayed = self.store.handle({
            "action": "purchase-log",
            "request_key": "pu-1",
            "product_name": "Tea",
            "bought_on": "2026-11-10",
            "price": "3.00",
            "currency": "EUR",
        })["result"]
        self.assertEqual(logged, replayed)
        self.assertEqual(len(self.store.handle({"action": "purchase-list"})["result"]), 1)

        upd = self.store.handle({
            "action": "purchase-update",
            "id": pid,
            "request_key": "pu-upd",
            "store": "Organic Shop",
            "finished_on": "2026-11-20",
        })["result"]
        self.assertEqual(upd["actual_duration_days"], 10)
        self.assertEqual(upd["store"], "Organic Shop")

        with self.assertRaisesRegex(shopping_store.StoreError, "purchase_remove_requires_confirmation"):
            self.store.handle({"action": "purchase-remove", "id": pid, "request_key": "pu-del-no"})
        rem = self.store.handle({
            "action": "purchase-remove",
            "id": pid,
            "request_key": "pu-del-yes",
            "confirm": True,
        })["result"]
        self.assertTrue(rem["removed"])
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"], [])


if __name__ == "__main__":
    unittest.main()
