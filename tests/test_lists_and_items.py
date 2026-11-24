"""Unit tests for lists, items, category preferences, Greek Unicode, and retries."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/shopping-magic/scripts"))
import shopping_store  # noqa: E402


class ListsAndItemsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sm-lists-")
        self.addCleanup(self.tmp.cleanup)
        self.store = shopping_store.Store(Path(self.tmp.name))

    def test_add_update_and_remove_items_with_greek_normalization(self) -> None:
        feta = self.store.handle({
            "action": "item-add",
            "request_key": "item-feta-1",
            "name": "Φέτα ΠΟΠ",
            "list_name": "Σούπερ Μάρκετ",
            "list_kind": "supermarket",
            "quantity": 0.5,
            "unit": "kg",
            "preferred_store": "Βασιλόπουλος",
            "notes": "σε άλμη",
        })["result"]
        self.assertEqual(feta["revision"], 1)
        self.assertEqual(feta["quantity"], "0.5")
        self.assertEqual(feta["remaining_quantity"], "0.5")
        self.assertEqual(feta["status"], "needed")

        # Accent- and case-insensitive search (including final sigma).
        hits = self.store.handle({
            "action": "item-list",
            "query": "φετα βασιλοπουλος",
        })["result"]
        self.assertEqual([h["id"] for h in hits], [feta["id"]])

        # Follow-up update: "make it 1 kg"
        updated = self.store.handle({
            "action": "item-update",
            "id": feta["id"],
            "expected_revision": 1,
            "request_key": "item-feta-upd",
            "quantity": "1",
            "unit": "kg",
        })["result"]
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["quantity"], "1")
        self.assertEqual(updated["remaining_quantity"], "1")

        # Optimistic concurrency protects against stale revision.
        with self.assertRaisesRegex(shopping_store.StoreError, "revision_conflict"):
            self.store.handle({
                "action": "item-update",
                "id": feta["id"],
                "expected_revision": 1,
                "request_key": "item-feta-stale",
                "quantity": "2",
            })

        # Remove with matching revision.
        removed = self.store.handle({
            "action": "item-remove",
            "id": feta["id"],
            "expected_revision": 2,
            "request_key": "item-feta-del",
        })["result"]
        self.assertTrue(removed["removed"])
        self.assertEqual(self.store.handle({"action": "item-list"})["result"], [])

    def test_optional_quantity_stays_null_and_is_not_invented(self) -> None:
        item = self.store.handle({
            "action": "item-add",
            "request_key": "item-bath-1",
            "name": "Something for the bathroom",
            "list_name": "Home",
            "list_kind": "home",
            "status": "idea",
        })["result"]
        self.assertIsNone(item["quantity"])
        self.assertIsNone(item["remaining_quantity"])
        self.assertEqual(item["unit"], "")
        self.assertEqual(item["status"], "idea")

    def test_category_correction_and_product_aliases(self) -> None:
        bat = self.store.handle({
            "action": "item-add",
            "request_key": "item-bat-1",
            "name": "Μπαταρίες ΑΑ",
            "list_name": "Supermarket",
            "list_kind": "supermarket",
        })["result"]
        self.store.handle({
            "action": "product-save",
            "id": bat["product_id"],
            "name": "Μπαταρίες ΑΑ",
            "aliases": ["AA Batteries", "μπαταρια αα"],
        })
        moved = self.store.handle({
            "action": "item-update",
            "id": bat["id"],
            "expected_revision": 1,
            "request_key": "item-bat-move",
            "list_name": "Home",
            "list_kind": "home",
            "category": "Electrical",
            "remember_list_preference": True,
        })["result"]
        self.assertEqual(moved["list_name"], "Home")

        # Subsequent add by alias without explicit list uses remembered default_list_id.
        second = self.store.handle({
            "action": "item-add",
            "request_key": "item-bat-2",
            "name": "AA Batteries",
            "quantity": "4",
            "unit": "pieces",
        })["result"]
        self.assertEqual(second["product_id"], bat["product_id"])
        self.assertEqual(second["list_name"], "Home")

        pref = self.store.handle({
            "action": "category-pref-set",
            "topic": "shampoo",
            "list_name": "Personal Care",
            "list_kind": "personal_care",
            "category": "Hair",
        })["result"]
        self.assertEqual(pref["list_kind"], "personal_care")
        removed = self.store.handle({"action": "category-pref-remove", "id": pref["id"]})["result"]
        self.assertTrue(removed["removed"])

    def test_idempotent_retries_and_request_key_conflict(self) -> None:
        req = {
            "action": "item-add",
            "request_key": "retry-key-1",
            "name": "Coffee",
            "list_name": "Supermarket",
            "list_kind": "supermarket",
        }
        first = self.store.handle(req)["result"]
        second = self.store.handle(req)["result"]
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.handle({"action": "item-list"})["result"]), 1)

        with self.assertRaisesRegex(shopping_store.StoreError, "request_key_conflict"):
            self.store.handle({**req, "name": "Tea"})

    def test_list_clear_requires_confirmation_and_preserves_purchases(self) -> None:
        item = self.store.handle({
            "action": "item-add",
            "request_key": "clr-1",
            "name": "Bread",
            "list_name": "Supermarket",
            "list_kind": "supermarket",
        })["result"]
        self.store.handle({
            "action": "purchase-log",
            "request_key": "clr-p1",
            "product_name": "Milk",
            "list_name": "Supermarket",
            "bought_on": "2026-11-20",
        })
        with self.assertRaisesRegex(shopping_store.StoreError, "list_clear_requires_confirmation"):
            self.store.handle({
                "action": "list-clear",
                "list_name": "Supermarket",
                "request_key": "clr-no-confirm",
            })
        self.assertEqual(len(self.store.handle({"action": "item-list"})["result"]), 1)

        cleared = self.store.handle({
            "action": "list-clear",
            "list_name": "Supermarket",
            "confirm": True,
            "request_key": "clr-yes",
        })["result"]
        self.assertEqual(cleared["removed_count"], 1)
        self.assertEqual(self.store.handle({"action": "item-list"})["result"], [])
        self.assertEqual(len(self.store.handle({"action": "purchase-list"})["result"]), 1)
        self.assertNotEqual(item["id"], "")

    def test_rejects_unknown_fields_and_boolean_numbers(self) -> None:
        with self.assertRaisesRegex(shopping_store.StoreError, "unknown_fields"):
            self.store.handle({"action": "item-list", "unexpected": 1})
        with self.assertRaisesRegex(shopping_store.StoreError, "invalid_quantity"):
            self.store.handle({
                "action": "item-add",
                "request_key": "bad-bool",
                "name": "Milk",
                "list_name": "Supermarket",
                "quantity": True,
            })
        with self.assertRaisesRegex(shopping_store.StoreError, "list_required"):
            self.store.handle({
                "action": "item-add",
                "request_key": "no-list",
                "name": "Unseen Product",
            })

    def test_reopening_bought_item_restores_remaining_quantity(self) -> None:
        milk = self.store.handle({
            "action": "item-add",
            "request_key": "reopen-1",
            "name": "Milk",
            "list_name": "Supermarket",
            "quantity": "6",
            "unit": "bottles",
        })["result"]
        bought = self.store.handle({
            "action": "item-update",
            "id": milk["id"],
            "expected_revision": 1,
            "request_key": "reopen-buy",
            "status": "bought",
        })["result"]
        self.assertEqual(bought["remaining_quantity"], "0")

        reopened = self.store.handle({
            "action": "item-update",
            "id": milk["id"],
            "expected_revision": bought["revision"],
            "request_key": "reopen-need",
            "status": "needed",
        })["result"]
        self.assertEqual(reopened["remaining_quantity"], "6")
        self.assertIsNone(reopened["resolved_at"])

        # Reopening directly to partially_bought requires an explicit positive remaining_quantity.
        bought_again = self.store.handle({
            "action": "item-update",
            "id": milk["id"],
            "expected_revision": reopened["revision"],
            "request_key": "reopen-buy2",
            "status": "bought",
        })["result"]
        with self.assertRaisesRegex(shopping_store.StoreError, "invalid_remaining_quantity"):
            self.store.handle({
                "action": "item-update",
                "id": milk["id"],
                "expected_revision": bought_again["revision"],
                "request_key": "reopen-partial-bad",
                "status": "partially_bought",
            })

        # Reopening with a new quantity sets remaining_quantity to that new quantity.
        reopened_new_qty = self.store.handle({
            "action": "item-update",
            "id": milk["id"],
            "expected_revision": bought_again["revision"],
            "request_key": "reopen-new-qty",
            "status": "needed",
            "quantity": "4",
        })["result"]
        self.assertEqual(reopened_new_qty["remaining_quantity"], "4")

    def test_renaming_item_reconciles_product_link_for_purchases(self) -> None:
        typo = self.store.handle({
            "action": "item-add",
            "request_key": "rename-1",
            "name": "Mlik",
            "list_name": "Supermarket",
        })["result"]
        renamed = self.store.handle({
            "action": "item-update",
            "id": typo["id"],
            "expected_revision": typo["revision"],
            "request_key": "rename-2",
            "name": "Milk",
        })["result"]
        self.assertEqual(renamed["name"], "Milk")
        self.assertNotEqual(renamed["product_id"], typo["product_id"])

        logged = self.store.handle({
            "action": "purchase-log",
            "request_key": "rename-buy",
            "item_id": renamed["id"],
            "bought_on": "2026-11-24",
            "store": "AB",
        })["result"]
        self.assertEqual(logged["purchase"]["product_name"], "Milk")
        self.assertEqual(logged["purchase"]["product_id"], renamed["product_id"])

    def test_null_remaining_quantity_rejected_for_quantified_item(self) -> None:
        milk = self.store.handle({
            "action": "item-add",
            "request_key": "null-rem-1",
            "name": "Bottled Water",
            "list_name": "Supermarket",
            "quantity": "6",
            "unit": "bottles",
        })["result"]
        with self.assertRaisesRegex(shopping_store.StoreError, "invalid_remaining_quantity"):
            self.store.handle({
                "action": "item-update",
                "id": milk["id"],
                "expected_revision": milk["revision"],
                "request_key": "null-rem-bad",
                "remaining_quantity": None,
            })

    def test_unit_conversion_when_updating_partially_bought_quantity(self) -> None:
        flour = self.store.handle({
            "action": "item-add",
            "request_key": "conv-1",
            "name": "Flour",
            "list_name": "Supermarket",
            "quantity": "2",
            "unit": "kg",
        })["result"]
        partial = self.store.handle({
            "action": "purchase-log",
            "request_key": "conv-buy",
            "item_id": flour["id"],
            "bought_on": "2026-11-24",
            "quantity": "1",
            "unit": "kg",
        })["result"]
        self.assertEqual(partial["updated_item"]["status"], "partially_bought")
        self.assertEqual(partial["updated_item"]["remaining_quantity"], "1")

        # Updating target quantity with a compatible unit converts fulfilled 1 kg -> 1000 g.
        converted = self.store.handle({
            "action": "item-update",
            "id": flour["id"],
            "expected_revision": partial["updated_item"]["revision"],
            "request_key": "conv-upd",
            "quantity": "2000",
            "unit": "g",
        })["result"]
        self.assertEqual(converted["remaining_quantity"], "1000")
        self.assertEqual(converted["status"], "partially_bought")

        # Incompatible unit change without explicit remaining_quantity fails atomically.
        with self.assertRaisesRegex(shopping_store.StoreError, "incompatible_units"):
            self.store.handle({
                "action": "item-update",
                "id": flour["id"],
                "expected_revision": converted["revision"],
                "request_key": "conv-bad",
                "quantity": "4",
                "unit": "packs",
            })

        # Explicit remaining_quantity is accepted when switching incompatible units.
        explicit = self.store.handle({
            "action": "item-update",
            "id": flour["id"],
            "expected_revision": converted["revision"],
            "request_key": "conv-explicit",
            "quantity": "4",
            "unit": "packs",
            "remaining_quantity": "2",
        })["result"]
        self.assertEqual(explicit["remaining_quantity"], "2")
        self.assertEqual(explicit["unit"], "packs")


if __name__ == "__main__":
    unittest.main()
