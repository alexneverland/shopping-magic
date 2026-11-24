"""Unit tests for purchase history, partial purchases, units, and spending totals."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def test_linked_purchase_rejects_a_different_product(self) -> None:
        milk = self.store.handle({
            "action": "item-add", "request_key": "milk", "name": "Milk", "list_name": "Audit",
        })["result"]
        tea = self.store.handle({"action": "product-save", "name": "Tea"})["result"]
        for index, fields in enumerate(({"product_id": tea["id"]}, {"product_name": "Tea"})):
            with self.subTest(fields=fields), self.assertRaisesRegex(shopping_store.StoreError, "purchase_product_mismatch"):
                self.store.handle({
                    "action": "purchase-log", "request_key": f"wrong-{index}", "bought_on": "2026-11-24",
                    "item_id": milk["id"], **fields,
                })
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"], [])
        self.assertEqual(self.store.handle({"action": "item-get", "id": milk["id"]})["result"]["status"], "needed")
        product = self.store.handle({
            "action": "product-save", "name": "Milk", "aliases": ["Dairy Milk"],
        })["result"]
        logged = self.store.handle({
            "action": "purchase-log", "request_key": "alias", "bought_on": "2026-11-24",
            "item_id": milk["id"], "product_id": product["id"], "product_name": "Dairy Milk",
        })["result"]
        self.assertEqual(logged["updated_item"]["status"], "bought")

    def test_purchase_without_store_does_not_inherit_preferred_store(self) -> None:
        milk = self.store.handle({
            "action": "item-add", "request_key": "milk", "name": "Milk", "list_name": "Audit",
            "preferred_store": "Market A",
        })["result"]
        logged = self.store.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": milk["id"],
            "bought_on": "2026-11-24", "price": "5.20", "currency": "EUR",
        })["result"]
        self.assertEqual(logged["purchase"]["store"], "")
        filtered = self.store.handle({"action": "spending-summary", "store": "Market A"})["result"]
        self.assertEqual(filtered["purchase_count"], 0)
        self.assertEqual(self.store.handle({"action": "spending-summary"})["result"]["totals_by_currency"], {"EUR": "5.2"})

    def test_unit_only_edit_converts_total_and_remaining(self) -> None:
        item = self.store.handle({
            "action": "item-add", "request_key": "coffee", "name": "Coffee", "list_name": "Audit",
            "quantity": "2", "unit": "kg",
        })["result"]
        item = self.store.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": item["id"],
            "bought_on": "2026-11-24", "quantity": "500", "unit": "g",
        })["result"]["updated_item"]
        edit = {
            "action": "item-update", "request_key": "unit", "id": item["id"],
            "expected_revision": item["revision"],
        }
        with self.assertRaisesRegex(shopping_store.StoreError, "incompatible_units"):
            self.store.handle(dict(edit, unit="bottles"))
        updated = self.store.handle(dict(edit, unit="g"))["result"]
        self.assertEqual((updated["quantity"], updated["remaining_quantity"]), ("2000", "1500"))

    def test_linked_purchase_correction_and_removal_restore_remaining(self) -> None:
        milk = self.store.handle({
            "action": "item-add", "request_key": "milk", "name": "Milk", "list_name": "Audit",
            "quantity": "6", "unit": "bottles",
        })["result"]
        logged = self.store.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": milk["id"],
            "bought_on": "2026-11-24", "quantity": "8", "unit": "bottles",
        })["result"]
        purchase_id = logged["purchase"]["id"]
        update = {"action": "purchase-update", "request_key": "fix", "id": purchase_id, "quantity": "1"}
        with self.assertRaisesRegex(shopping_store.StoreError, "incompatible_units"):
            self.store.handle(dict(update, unit="kg"))
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"][0]["quantity"], "8")
        corrected = self.store.handle(update)["result"]
        history = self.store.handle({"action": "purchase-list"})["result"]
        self.assertEqual([(entry["id"], entry["quantity"]) for entry in history], [(purchase_id, "1")])
        self.assertEqual(self.store.handle(update)["result"], corrected)
        partial = self.store.handle({"action": "item-get", "id": milk["id"]})["result"]
        self.assertEqual((partial["status"], partial["remaining_quantity"]), ("partially_bought", "5"))
        self.assertEqual(partial["revision"], milk["revision"] + 2)
        self.store.handle({"action": "purchase-remove", "request_key": "remove", "id": purchase_id, "confirm": True})
        reopened = self.store.handle({"action": "item-get", "id": milk["id"]})["result"]
        self.assertEqual((reopened["status"], reopened["remaining_quantity"]), ("needed", "6"))
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"], [])

    def test_unit_only_purchase_edit_preserves_physical_amount(self) -> None:
        item = self.store.handle({
            "action": "item-add", "request_key": "coffee", "name": "Coffee",
            "list_name": "Audit", "quantity": "2", "unit": "kg",
        })["result"]
        logged = self.store.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": item["id"],
            "bought_on": "2026-11-24", "quantity": "500", "unit": "g",
        })["result"]
        edit = {"action": "purchase-update", "id": logged["purchase"]["id"], "request_key": "convert"}
        with self.assertRaisesRegex(shopping_store.StoreError, "incompatible_units"):
            self.store.handle(dict(edit, unit="pack"))
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"][0]["quantity"], "500")
        self.store.handle(dict(edit, unit="kg"))
        corrected = self.store.handle({"action": "purchase-list"})["result"][0]
        self.assertEqual((corrected["quantity"], corrected["unit"]), ("0.5", "kg"))
        self.assertEqual(self.store.handle({"action": "item-get", "id": item["id"]})["result"], logged["updated_item"])
        self.store.handle({
            "action": "purchase-remove", "id": corrected["id"], "request_key": "remove", "confirm": True,
        })
        self.assertEqual(self.store.handle({"action": "item-get", "id": item["id"]})["result"]["remaining_quantity"], "2")

    def test_new_product_retains_item_preferred_store_without_attributing_purchase(self) -> None:
        item = self.store.handle({
            "action": "item-add", "request_key": "coffee", "name": "Coffee",
            "list_name": "Audit", "preferred_store": "Market A",
        })["result"]
        snapshot = self.store.handle({"action": "export"})["result"]
        snapshot["shopping_items"][0]["product_id"] = None
        snapshot["products"] = []
        target = shopping_store.Store(Path(self.tmp.name) / "productless")
        target.handle({"action": "import", "request_key": "import", "data": snapshot})
        logged = target.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": item["id"], "bought_on": "2026-11-24",
        })["result"]
        self.assertEqual(logged["product"]["default_store"], "Market A")
        self.assertEqual(logged["purchase"]["store"], "")

    def test_import_rejects_invalid_restorable_fulfillment(self) -> None:
        item = self.store.handle({
            "action": "item-add", "request_key": "milk", "name": "Milk", "list_name": "Audit",
            "quantity": "6", "unit": "bottles",
        })["result"]
        self.store.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": item["id"],
            "bought_on": "2026-11-24", "quantity": "4", "unit": "bottles",
        })
        snapshot = self.store.handle({"action": "export"})["result"]
        effect = snapshot["purchases"][0]["fulfillment"]
        target = shopping_store.Store(Path(self.tmp.name) / "invalid-fulfillment")
        request = {"action": "import", "request_key": "import", "data": snapshot}
        for remainder in ("0", "7", "5"):
            effect["remaining_quantity"] = remainder
            with self.subTest(remainder=remainder), self.assertRaisesRegex(shopping_store.StoreError, "invalid_import_fulfillment"):
                target.handle(request)
            self.assertEqual(target.handle({"action": "purchase-list"})["result"], [])
        # A profile restored by an earlier helper may already contain an inconsistent effect.
        legacy = shopping_store.Store(Path(self.tmp.name) / "previously-imported-effect")
        with patch.object(shopping_store, "saved_fulfillment_matches_item", return_value=True):
            legacy.handle(request)
        removal = {
            "action": "purchase-remove", "request_key": "remove", "confirm": True,
            "id": snapshot["purchases"][0]["id"],
        }
        with self.assertRaisesRegex(shopping_store.StoreError, "purchase_item_state_requires_review"):
            legacy.handle(removal)
        self.assertEqual(len(legacy.handle({"action": "purchase-list"})["result"]), 1)
        self.assertEqual(legacy.handle({"action": "item-get", "id": item["id"]})["result"]["remaining_quantity"], "2")
        effect["remaining_quantity"] = "6"
        self.assertTrue(target.handle(request)["result"]["imported"])

    def test_stale_edit_requires_explicit_history_only_choice(self) -> None:
        item = self.store.handle({
            "action": "item-add", "request_key": "milk", "name": "Milk", "list_name": "Audit",
            "quantity": "6", "unit": "bottles",
        })["result"]
        logged = self.store.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": item["id"],
            "bought_on": "2026-11-24", "quantity": "4", "unit": "bottles",
        })["result"]
        changed = self.store.handle({
            "action": "item-update", "id": item["id"], "request_key": "manual",
            "expected_revision": logged["updated_item"]["revision"], "remaining_quantity": "3",
        })["result"]
        purchase_id = logged["purchase"]["id"]
        update = {"action": "purchase-update", "id": purchase_id, "request_key": "fix", "quantity": "1"}
        remove = {"action": "purchase-remove", "id": purchase_id, "request_key": "remove", "confirm": True}
        for request in (update, remove):
            with self.assertRaisesRegex(shopping_store.StoreError, "purchase_item_state_requires_review"):
                self.store.handle(request)
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"][0]["quantity"], "4")
        self.store.handle(dict(update, keep_item_state=True))
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"][0]["quantity"], "1")
        override = dict(remove, keep_item_state=True)
        removed = self.store.handle(override)
        self.assertEqual(self.store.handle(override), removed)
        self.assertEqual(self.store.handle({"action": "purchase-list"})["result"], [])
        self.assertEqual(self.store.handle({"action": "item-get", "id": item["id"]})["result"], changed)

    def test_snapshot_preserves_fulfillment_and_accepts_legacy_shape(self) -> None:
        item = self.store.handle({
            "action": "item-add", "request_key": "coffee", "name": "Coffee", "list_name": "Audit",
        })["result"]
        logged = self.store.handle({
            "action": "purchase-log", "request_key": "buy", "item_id": item["id"],
            "bought_on": "2026-11-24", "fulfillment_mode": "mark_bought",
        })["result"]
        snapshot = self.store.handle({"action": "export"})["result"]
        remove = {
            "action": "purchase-remove", "id": logged["purchase"]["id"], "request_key": "remove", "confirm": True,
        }
        restored = shopping_store.Store(Path(self.tmp.name) / "restored")
        restored.handle({"action": "import", "request_key": "import", "data": snapshot})
        self.assertEqual(restored.handle({"action": "export"})["result"], snapshot)
        restored.handle(remove)
        self.assertEqual(restored.handle({"action": "item-get", "id": item["id"]})["result"]["status"], "needed")

        snapshot["schema_version"] = 1
        del snapshot["purchases"][0]["fulfillment"]
        for product in snapshot["products"]:
            del product["replenishment_basis_quantity"]
            del product["replenishment_basis_unit"]
        legacy = shopping_store.Store(Path(self.tmp.name) / "legacy")
        legacy.handle({"action": "import", "request_key": "import", "data": snapshot})
        with self.assertRaisesRegex(shopping_store.StoreError, "purchase_item_state_requires_review"):
            legacy.handle(remove)
        legacy.handle(dict(remove, keep_item_state=True))
        self.assertEqual(legacy.handle({"action": "item-get", "id": item["id"]})["result"]["status"], "bought")

        self.store.handle({"action": "item-remove", "id": item["id"], "request_key": "remove-item"})
        detached = self.store.handle({"action": "export"})["result"]
        self.assertIsNone(detached["purchases"][0]["shopping_item_id"])
        self.assertIsNone(detached["purchases"][0]["fulfillment"])
        target = shopping_store.Store(Path(self.tmp.name) / "detached")
        target.handle({"action": "import", "request_key": "import", "data": detached})
        self.assertEqual(target.handle({"action": "export"})["result"], detached)

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
