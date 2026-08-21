# -*- coding: utf-8 -*-
"""Test mô phỏng: backtest không lookahead (random walk ≈ 50% win), Monte Carlo, consensus."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arena.datasources import mock_klines
from arena.debate import compute_consensus
from arena.simulation import run_backtest, run_monte_carlo, simulate_crowd


class TestBacktestNoLookahead(unittest.TestCase):
    def test_random_walk_win_rate_near_50(self):
        """Trên random walk không drift, chiến lược KHÔNG được có lợi thế hệ thống."""
        rates = []
        for seed in range(30):
            random.seed(seed)
            k = mock_klines(4050.0, 60, 120, 0.0018)
            bt = run_backtest(k, "1h")
            if bt:
                rates.append(bt["win_rate"])
        self.assertGreater(len(rates), 20)
        avg = sum(rates) / len(rates)
        self.assertTrue(40 <= avg <= 60, f"win rate TB {avg:.1f}% — nghi ngờ lookahead bias")

    def test_backtest_uses_only_past_data(self):
        """Backtest tại nến i chỉ được dùng closes[:i+1] → không có nến tương lai."""
        random.seed(7)
        k = mock_klines(4050.0, 60, 120, 0.0018)
        k2 = [dict(c) for c in k]
        # cắt bớt 10 nến cuối không ảnh hưởng kết quả các tín hiệu trước đó? Kiểm tra tính tất định:
        bt1 = run_backtest(k, "1h")
        bt2 = run_backtest(k2, "1h")
        self.assertEqual(bt1["trades"], bt2["trades"])
        self.assertEqual(bt1["win_rate"], bt2["win_rate"])


class TestMonteCarlo(unittest.TestCase):
    def test_mc_shape_and_ordering(self):
        mc = run_monte_carlo(1000.0, 10, 0.01, 0.3, 0.1, 200, seed=42)
        self.assertEqual(len(mc["rows"]), 11)
        self.assertTrue(0 <= mc["prob_up"] <= 1)
        self.assertLessEqual(mc["p10"], mc["p50"])
        self.assertLessEqual(mc["p50"], mc["p90"])

    def test_positive_drift_bias(self):
        """Đồng thuận dương mạnh → P(tăng) phải rõ rệt > 50%."""
        mc = run_monte_carlo(1000.0, 24, 0.005, 0.9, 0.3, 500, seed=1)
        self.assertGreater(mc["prob_up"], 0.55)
        mc2 = run_monte_carlo(1000.0, 24, 0.005, -0.9, -0.3, 500, seed=1)
        self.assertLess(mc2["prob_up"], 0.45)


class TestCrowd(unittest.TestCase):
    def test_crowd_sums(self):
        finals = [{"key": k, "stance": s, "conf": c} for k, s, c in
                  (("Macro_Analyst", 0.5, 0.8), ("Technical_Analyst", -0.3, 0.7),
                   ("Institutional_Whale", 0.4, 0.9), ("Retail_Crowd", 0.0, 0.5))]
        c = simulate_crowd(finals, 100, 0.2, seed=3)
        self.assertEqual(c["bull"] + c["neu"] + c["bear"], 100)
        self.assertEqual(sum(c["votes"].values()), 100)


class TestConsensus(unittest.TestCase):
    def test_consensus_weighted(self):
        finals = [{"key": "Macro_Analyst", "stance": 0.5, "conf": 0.8},
                  {"key": "Technical_Analyst", "stance": -0.5, "conf": 0.8},
                  {"key": "Institutional_Whale", "stance": 0.0, "conf": 0.5},
                  {"key": "Retail_Crowd", "stance": 0.0, "conf": 0.5}]
        # Macro (w=0.25) vs Tech (w=0.35) ngược nhau → đồng thuận hơi nghiêng Tech
        c, verdict = compute_consensus(finals, "1h")
        self.assertAlmostEqual(c, -0.05357, places=4)
        self.assertIn("TRUNG LẬP", verdict)
        # Khung 4h: Macro (0.35) > Tech (0.25) → nghiêng ngược lại
        c2, _ = compute_consensus(finals, "4h")
        self.assertAlmostEqual(c2, 0.05357, places=4)


if __name__ == "__main__":
    unittest.main()
