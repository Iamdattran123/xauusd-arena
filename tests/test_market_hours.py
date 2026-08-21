# -*- coding: utf-8 -*-
"""Test lịch phiên thị trường (cuối tuần, ngày lễ, giờ nghỉ hằng ngày)."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arena.market_hours import is_market_open, next_market_close, next_market_open, status_info


def utc(y, m, d, hh=0, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=dt.timezone.utc)


class TestMarketHours(unittest.TestCase):
    def test_weekend_closed(self):
        self.assertFalse(is_market_open(utc(2026, 8, 22, 12)))   # Thứ 7
        self.assertFalse(is_market_open(utc(2026, 8, 23, 21)))   # CN trước 22:00

    def test_sunday_open(self):
        self.assertTrue(is_market_open(utc(2026, 8, 23, 22)))    # CN 22:00
        self.assertTrue(is_market_open(utc(2026, 8, 24, 3)))     # Thứ 2 rạng sáng

    def test_friday_close(self):
        self.assertTrue(is_market_open(utc(2026, 8, 21, 20)))    # T6 20:00
        self.assertFalse(is_market_open(utc(2026, 8, 21, 21)))   # T6 21:00 đóng

    def test_daily_break(self):
        self.assertTrue(is_market_open(utc(2026, 8, 20, 20)))    # T5 20:00
        self.assertFalse(is_market_open(utc(2026, 8, 20, 21, 30)))  # T5 21:30 nghỉ bảo trì
        self.assertTrue(is_market_open(utc(2026, 8, 20, 22, 30)))   # T5 22:30 mở lại

    def test_holiday_closed(self):
        # 04/07/2026 là thứ 7 → nghỉ quan sát vào thứ 6 03/07/2026
        self.assertFalse(is_market_open(utc(2026, 7, 3, 14)))
        self.assertTrue(is_market_open(utc(2026, 7, 2, 14)))

    def test_early_close(self):
        # 24/12/2026: đóng sớm 18:00 UTC
        self.assertTrue(is_market_open(utc(2026, 12, 24, 12)))
        self.assertFalse(is_market_open(utc(2026, 12, 24, 18, 30)))

    def test_next_open_from_weekend(self):
        nxt = next_market_open(utc(2026, 8, 22, 12))  # trưa thứ 7
        self.assertEqual(nxt.weekday(), 6)            # chủ nhật
        self.assertEqual((nxt.hour, nxt.minute), (22, 0))

    def test_next_close_from_weekday(self):
        nxt = next_market_close(utc(2026, 8, 20, 12))  # thứ 5
        self.assertIsNotNone(nxt)
        self.assertEqual((nxt.hour, nxt.minute), (21, 0))

    def test_status_info(self):
        s = status_info(utc(2026, 8, 22, 12))
        self.assertFalse(s["open"])
        self.assertIn("ĐÓNG", s["text"])
        self.assertIn("next_open", s)
        s2 = status_info(utc(2026, 8, 20, 12))
        self.assertTrue(s2["open"])


if __name__ == "__main__":
    unittest.main()
