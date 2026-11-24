"""Unit tests for explicit, calendar, and history-based replenishment logic."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/shopping-magic/scripts"))
import shopping_store  # noqa: E402


class ReplenishmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sm-replenish-")
        self.addCleanup(self.tmp.cleanup)
        self.store = shopping_store.Store(Path(self.tmp.name))

    def test_explicit_day_and_calendar_month_intervals(self) -> None:
        coffee = self.store.handle({
            "action": "purchase-log",
            "request_key": "r-cof-1",
            "product_name": "Coffee",
            "bought_on": "2026-11-01",
            "replenishment_days": 7,
        })["result"]
        self.assertIsNone(coffee["purchase"]["expected_next_on"])
        self.assertEqual(coffee["suggested_next_on"], "2026-11-08")
        self.assertEqual(coffee["product"]["replenishment_mode"], "interval_days")
        self.assertEqual(coffee["product"]["replenishment_value"], 7)

        # Water filter every 3 calendar months from Jan 31 clamps cleanly to April 30.
        flt = self.store.handle({
            "action": "purchase-log",
            "request_key": "r-flt-1",
            "product_name": "Water Filter",
            "list_name": "Home",
            "list_kind": "home",
            "bought_on": "2026-01-31",
            "replenishment_months": 3,
        })["result"]
        self.assertIsNone(flt["purchase"]["expected_next_on"])
        self.assertEqual(flt["suggested_next_on"], "2026-04-30")

        # Leap year check: Jan 31 + 1 calendar month = Feb 29, 2024.
        self.assertEqual(shopping_store.add_months(shopping_store.date(2024, 1, 31), 1).isoformat(), "2024-02-29")

        due = self.store.handle({
            "action": "replenishment-suggestions",
            "as_of": "2026-11-06",
            "horizon_days": 7,
        })["result"]["suggestions"]
        cof_sug = next(s for s in due if s["product_name"] == "Coffee")
        self.assertEqual(cof_sug["basis"], "explicit_interval_days")
        self.assertEqual(cof_sug["status"], "due_soon_estimate")
        self.assertEqual(cof_sug["days_until_due"], 2)
        self.assertTrue(cof_sug["is_estimate"])
        self.assertFalse(cof_sug["confirmed_need_exists"])

        # Updating Coffee's long-term rule via replenishment-set immediately shifts suggestions.
        self.store.handle({
            "action": "replenishment-set",
            "product_id": coffee["product"]["id"],
            "mode": "interval_days",
            "value": 14,
            "source": "user_corrected",
            "request_key": "r-cof-upd-14",
        })
        shifted = self.store.handle({
            "action": "replenishment-suggestions",
            "product_id": coffee["product"]["id"],
            "as_of": "2026-11-06",
        })["result"]["suggestions"][0]
        self.assertEqual(shifted["basis"], "explicit_interval_days")
        self.assertEqual(shifted["suggested_next_on"], "2026-11-15")
        self.assertEqual(shifted["days_until_due"], 9)

    def test_historical_median_and_outlier_safeguard(self) -> None:
        # 4 normal purchases spaced 8 days apart plus one 40-day vacation gap outlier.
        dates = ("2026-08-01", "2026-08-09", "2026-08-17", "2026-09-26", "2026-10-04", "2026-10-12")
        for idx, day in enumerate(dates):
            self.store.handle({
                "action": "purchase-log",
                "request_key": f"hist-{idx}",
                "product_name": "Espresso Beans",
                "bought_on": day,
            })
        res = self.store.handle({
            "action": "replenishment-suggestions",
            "query": "Espresso",
            "as_of": "2026-10-18",
        })["result"]["suggestions"][0]
        self.assertEqual(res["basis"], "historical_median")
        self.assertEqual(res["estimated_interval_days"], 8)
        self.assertEqual(res["outliers_ignored"], 1)
        self.assertEqual(res["suggested_next_on"], "2026-10-20")
        self.assertEqual(res["confidence"], "high")
        self.assertEqual(res["status"], "due_soon_estimate")

    def test_sparse_history_and_same_day_split_purchases(self) -> None:
        # Two purchases on the same date count as 0 intervals -> insufficient_history.
        for idx in (1, 2):
            self.store.handle({
                "action": "purchase-log",
                "request_key": f"same-day-{idx}",
                "product_name": "Soap",
                "bought_on": "2026-11-01",
            })
        sug = self.store.handle({
            "action": "replenishment-suggestions",
            "query": "Soap",
            "as_of": "2026-11-10",
        })["result"]["suggestions"][0]
        self.assertEqual(sug["status"], "insufficient_history")
        self.assertIsNone(sug["suggested_next_on"])

    def test_one_off_feedback_does_not_overwrite_explicit_rule(self) -> None:
        p = self.store.handle({
            "action": "purchase-log",
            "request_key": "fb-1",
            "product_name": "Painkillers",
            "list_name": "Pharmacy",
            "list_kind": "pharmacy",
            "bought_on": "2026-11-01",
            "replenishment_days": 30,
        })["result"]
        self.store.handle({
            "action": "purchase-update",
            "id": p["purchase"]["id"],
            "request_key": "fb-upd",
            "actual_duration_days": 45,
        })
        sug = self.store.handle({
            "action": "replenishment-suggestions",
            "product_id": p["product"]["id"],
            "as_of": "2026-11-24",
        })["result"]["suggestions"][0]
        self.assertEqual(sug["replenishment_value"], 30)
        self.assertEqual(sug["suggested_next_on"], "2026-12-01")
        self.assertEqual(sug["latest_feedback_days"], 45)

    def test_cycle_feedback_updates_historical_estimate_without_explicit_rule(self) -> None:
        # Purchase 1 lasts 12 days (user finished_on feedback), Purchase 2 bought 16 days later,
        # Purchase 3 logged with actual_duration_days=14 -> samples [12, 14] -> median 13.
        p1 = self.store.handle({
            "action": "purchase-log",
            "request_key": "cf-1",
            "product_name": "Herbal Tea",
            "bought_on": "2026-10-01",
        })["result"]
        self.store.handle({
            "action": "purchase-update",
            "id": p1["purchase"]["id"],
            "request_key": "cf-1-fb",
            "finished_on": "2026-10-13",
        })
        p2 = self.store.handle({
            "action": "purchase-log",
            "request_key": "cf-2",
            "product_name": "Herbal Tea",
            "bought_on": "2026-10-17",
        })["result"]
        self.store.handle({
            "action": "purchase-update",
            "id": p2["purchase"]["id"],
            "request_key": "cf-2-fb",
            "actual_duration_days": 14,
        })
        sug = self.store.handle({
            "action": "replenishment-suggestions",
            "product_id": p1["product"]["id"],
            "as_of": "2026-10-28",
        })["result"]["suggestions"][0]
        self.assertEqual(sug["basis"], "historical_median")
        self.assertEqual(sug["estimated_interval_days"], 13)
        self.assertEqual(sug["suggested_next_on"], "2026-10-30")
        self.assertEqual(sug["latest_feedback_days"], 14)

    def test_predictions_never_appear_in_confirmed_shopping_lists(self) -> None:
        self.store.handle({
            "action": "purchase-log",
            "request_key": "sep-1",
            "product_name": "Detergent",
            "list_name": "Home",
            "list_kind": "home",
            "bought_on": "2026-11-01",
            "replenishment_days": 14,
        })
        self.assertEqual(self.store.handle({"action": "item-list", "status": "open"})["result"], [])
        ctx = self.store.handle({"action": "context", "as_of": "2026-11-20"})["result"]
        self.assertEqual(ctx["open_items"], [])
        self.assertEqual(len(ctx["replenishment_suggestions"]), 1)
        self.assertEqual(ctx["replenishment_suggestions"][0]["status"], "overdue_estimate")

    def test_replenishment_scales_explicit_duration_by_purchase_quantity(self) -> None:
        rule = {
            "action": "replenishment-set", "request_key": "set-units", "product_name": "Soap",
            "mode": "interval_days", "value": 7, "basis_quantity": "1", "basis_unit": "piece",
        }
        self.assertEqual(self.store.handle(rule), self.store.handle(rule))
        purchase = self.store.handle({
            "action": "purchase-log", "request_key": "buy-five", "product_name": "Soap",
            "bought_on": "2026-11-24", "quantity": "5",
        })["result"]
        self.assertEqual(purchase["suggested_next_on"], "2026-12-29")
        query = {"action": "replenishment-suggestions", "query": "Soap", "as_of": "2026-11-24"}
        suggestion = self.store.handle(query)["result"]["suggestions"][0]
        self.assertEqual(suggestion["basis"], "explicit_quantity_interval")
        self.assertEqual(suggestion["estimated_interval_days"], 35)
        self.assertEqual(suggestion["suggested_next_on"], purchase["suggested_next_on"])
        self.assertTrue(suggestion["is_estimate"])

        snapshot = self.store.handle({"action": "export"})["result"]
        with tempfile.TemporaryDirectory(prefix="sm-scaled-restore-") as restored:
            target = shopping_store.Store(Path(restored))
            target.handle({"action": "import", "request_key": "restore", "data": snapshot})
            self.assertEqual(target.handle(query)["result"]["suggestions"][0], suggestion)
        self.store.handle({
            "action": "purchase-update", "request_key": "correct-count",
            "id": purchase["purchase"]["id"], "quantity": "3",
        })
        corrected = self.store.handle(query)["result"]["suggestions"][0]
        self.assertEqual(corrected["estimated_interval_days"], 21)
        self.assertEqual(corrected["suggested_next_on"], "2026-12-15")

    def test_quantity_rule_handles_compatible_and_unknown_units(self) -> None:
        self.store.handle({
            "action": "replenishment-set", "product_name": "Kasseri", "mode": "interval_days",
            "value": 7, "basis_quantity": "400", "basis_unit": "g",
        })
        for index, (quantity, unit, due) in enumerate((
            ("0.8", "kg", "2026-12-08"),
            ("100", "g", "2026-11-26"),
            ("2", "packs", None),
            ("800", "", None),
            (None, "g", None),
        )):
            with self.subTest(quantity=quantity, unit=unit):
                recorded = self.store.handle({
                    "action": "purchase-log", "request_key": f"quantity-{index}", "product_name": "Kasseri",
                    "bought_on": "2026-11-24", "quantity": quantity, "unit": unit,
                })["result"]
                self.assertEqual(recorded["suggested_next_on"], due)
                suggestion = self.store.handle({
                    "action": "replenishment-suggestions", "query": "Kasseri", "as_of": "2026-11-24",
                })["result"]["suggestions"][0]
                self.assertEqual(suggestion["suggested_next_on"], due)
                if due is None:
                    self.assertEqual(suggestion["status"], "quantity_estimate_unavailable")

    def test_quantity_rule_can_be_replaced_with_a_fixed_interval(self) -> None:
        rule = {"action": "replenishment-set", "product_name": "Soap", "mode": "interval_days", "value": 7}
        with self.assertRaisesRegex(shopping_store.StoreError, "invalid_replenishment_basis"):
            self.store.handle(dict(rule, mode="calendar_months", basis_quantity="1"))
        self.store.handle(dict(rule, basis_quantity="1"))
        self.store.handle(rule)
        result = self.store.handle({
            "action": "purchase-log", "request_key": "after-fixed", "product_name": "Soap",
            "bought_on": "2026-11-24", "quantity": "5",
        })["result"]
        self.assertEqual(result["suggested_next_on"], "2026-12-01")


if __name__ == "__main__":
    unittest.main()
