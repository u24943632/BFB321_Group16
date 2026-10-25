"""EOQ / safety stock / reorder point against hand-calculated values."""

import math
import unittest

from models import eoq


class CalculateEoqTests(unittest.TestCase):
    def test_textbook_example(self):
        # sqrt(2 * 1000 * 100 / 5) = sqrt(40 000) = 200
        self.assertAlmostEqual(eoq.calculate_eoq(1000, 100, 5), 200.0)

    def test_non_round_result(self):
        # sqrt(2 * 12 000 * 450 / 18) = sqrt(600 000) = 774.5967
        self.assertAlmostEqual(eoq.calculate_eoq(12_000, 450, 18), 774.5967, places=4)

    def test_rejects_zero_or_negative_inputs(self):
        for args in [(0, 100, 5), (1000, 0, 5), (1000, 100, 0), (-1, 100, 5)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                eoq.calculate_eoq(*args)


class OrdersAndCostTests(unittest.TestCase):
    def test_orders_per_year(self):
        self.assertAlmostEqual(eoq.orders_per_year(1000, 200), 5.0)

    def test_total_cost_at_eoq_has_equal_ordering_and_holding(self):
        # ordering = 5 * 100 = 500; holding = 100 * 5 = 500 -> 1000
        self.assertAlmostEqual(eoq.total_annual_cost(1000, 200, 100, 5), 1000.0)

    def test_total_cost_includes_purchase_cost(self):
        # 1000 + 1000 units * R12 = 13 000
        self.assertAlmostEqual(eoq.total_annual_cost(1000, 200, 100, 5, unit_cost=12), 13_000.0)

    def test_eoq_minimises_cost(self):
        q = eoq.calculate_eoq(1000, 100, 5)
        at_eoq = eoq.total_annual_cost(1000, q, 100, 5)
        self.assertLess(at_eoq, eoq.total_annual_cost(1000, q * 0.8, 100, 5))
        self.assertLess(at_eoq, eoq.total_annual_cost(1000, q * 1.2, 100, 5))

    def test_negative_unit_cost_rejected(self):
        with self.assertRaises(ValueError):
            eoq.total_annual_cost(1000, 200, 100, 5, unit_cost=-1)


class SafetyStockAndRopTests(unittest.TestCase):
    def test_z_scores(self):
        self.assertAlmostEqual(eoq.z_score(0.95), 1.6449, places=4)
        self.assertAlmostEqual(eoq.z_score(0.975), 1.9600, places=4)
        self.assertAlmostEqual(eoq.z_score(0.5), 0.0, places=9)

    def test_z_score_bounds(self):
        for level in (0, 1, 1.5, -0.1):
            with self.subTest(level=level), self.assertRaises(ValueError):
                eoq.z_score(level)

    def test_safety_stock(self):
        # 1.6449 * 10 * sqrt(4) = 32.897
        self.assertAlmostEqual(eoq.safety_stock(10, 4, 0.95), 32.897, places=3)

    def test_zero_variability_means_no_safety_stock(self):
        self.assertEqual(eoq.safety_stock(0, 4), 0.0)

    def test_reorder_point(self):
        # 50/day * 4 days + 32.9 = 232.9
        self.assertAlmostEqual(eoq.reorder_point(50, 4, 32.9), 232.9)

    def test_stock_status(self):
        self.assertEqual(eoq.stock_status(300, 232.9, 32.9), eoq.STATUS_OK)
        self.assertEqual(eoq.stock_status(232.9, 232.9, 32.9), eoq.STATUS_REORDER)
        self.assertEqual(eoq.stock_status(20, 232.9, 32.9), eoq.STATUS_CRITICAL)


class PlanReorderTests(unittest.TestCase):
    def test_plan_combines_all_formulas(self):
        plan = eoq.plan_reorder("BGA", annual_demand=12_500, order_cost=100, holding_cost=5,
                                daily_demand_std=10, lead_time_days=4, on_hand=150,
                                working_days=250)
        self.assertAlmostEqual(plan.eoq, math.sqrt(2 * 12_500 * 100 / 5))   # 707.1
        self.assertAlmostEqual(plan.safety_stock, 32.897, places=3)
        self.assertAlmostEqual(plan.reorder_point, 50 * 4 + plan.safety_stock)
        self.assertEqual(plan.status, eoq.STATUS_REORDER)


if __name__ == "__main__":
    unittest.main()
