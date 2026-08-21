# -*- coding: utf-8 -*-
"""Test tầng dữ liệu: resample, căn chỉnh nến về spot, sanity giá."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arena.datasources import align_klines_to_price, mock_klines, resample, sanity_price
from arena.config import MAX_ALIGN_RATIO


class TestResample(unittest.TestCase):
    def test_bucket_merge(self):
        step = 3600_000  # 1h
        k = [
            {"t": 0, "o": 100, "h": 105, "l": 99, "c": 104},
            {"t": 1000, "o": 104, "h": 110, "l": 103, "c": 108},
            {"t": step, "o": 108, "h": 109, "l": 107, "c": 107.5},
        ]
        out = resample(k, step)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["o"], 100)
        self.assertEqual(out[0]["h"], 110)
        self.assertEqual(out[0]["l"], 99)
        self.assertEqual(out[0]["c"], 108)  # close của nến cuối trong bucket


class TestAlign(unittest.TestCase):
    def test_align_scales_to_price(self):
        k = [{"t": i, "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0} for i in range(3)]
        out, ratio = align_klines_to_price(k, 102.01)  # +1%
        self.assertAlmostEqual(ratio, 1.01, places=4)
        self.assertAlmostEqual(out[-1]["c"], 102.01, places=4)
        # hình dạng nến giữ nguyên tỷ lệ
        self.assertAlmostEqual(out[0]["h"] / out[0]["c"], 102 / 101, places=6)

    def test_align_refuses_extreme_ratio(self):
        k = [{"t": i, "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0} for i in range(3)]
        out, ratio = align_klines_to_price(k, 108.0)  # +7% → nghi ngờ dữ liệu
        self.assertEqual(ratio, 1.0)
        self.assertEqual(out[-1]["c"], 101.0)

    def test_align_empty(self):
        out, ratio = align_klines_to_price([], 100)
        self.assertEqual(out, [])
        self.assertEqual(ratio, 1.0)


class TestSanity(unittest.TestCase):
    def test_sanity_range(self):
        self.assertTrue(sanity_price(4500))
        self.assertFalse(sanity_price(0))
        self.assertFalse(sanity_price(-50))
        self.assertFalse(sanity_price(10**9))
        self.assertFalse(sanity_price("abc"))
        self.assertFalse(sanity_price(float("nan")))


class TestMockKlines(unittest.TestCase):
    def test_mock_shape(self):
        k = mock_klines(4050.0, 60, 120, 0.0018)
        self.assertEqual(len(k), 120)
        self.assertTrue(all(c["h"] >= max(c["o"], c["c"]) - 1e-9 for c in k))
        self.assertTrue(all(c["l"] <= min(c["o"], c["c"]) + 1e-9 for c in k))
        # khoảng cách giữa các nến đúng bước
        self.assertEqual(k[1]["t"] - k[0]["t"], 3600_000)


if __name__ == "__main__":
    unittest.main()
