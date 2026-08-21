# -*- coding: utf-8 -*-
"""Test AI Trader: gap cuối tuần, phát lại nến, chặn giao dịch khi đóng/offline, migrate state."""
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arena.config import load_config
from arena.trader import (load_trader_state, new_trader_state, replay_candles,
                          save_trader_state, trader_step, _initial_candle_ts)


def make_args():
    return types.SimpleNamespace(timeframe="1h", out=None, seed=None, no_trader=False)


def make_mc():
    return {"target": 110.0, "p10": 95.0, "p90": 120.0, "prob_up": 0.6}


def make_ind():
    return {"atr": 2.0, "sup": 98.0, "res": 112.0, "momentum": 0.1, "rsi": 55,
            "ema20": 101, "ema50": 99, "macd_hist": 0.5, "vol": 0.002,
            "last_ret_pct": 0.1, "trend": "TĂNG", "n": 80}


FINALS = [{"key": "Macro_Analyst", "title": "Vĩ mô", "icon": "🏛️", "stance": 0.3, "conf": 0.8, "reason": "r"},
          {"key": "Technical_Analyst", "title": "Kỹ thuật", "icon": "📐", "stance": 0.1, "conf": 0.7, "reason": "r"},
          {"key": "Institutional_Whale", "title": "Quỹ", "icon": "🐋", "stance": 0.2, "conf": 0.8, "reason": "r"},
          {"key": "Retail_Crowd", "title": "Nhỏ lẻ", "icon": "👥", "stance": 0.05, "conf": 0.6, "reason": "r"}]


class TestTrader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.cfg = load_config(path="/nonexistent/config.json")

    # ---- GAP + phát lại nến ----
    def test_gap_through_sl_fills_at_open(self):
        st = new_trader_state()
        st["balance"] = 1000.0
        st["positions"] = [{"id": 1, "dir": "long", "tf": "1h", "entry": 100.0, "sl": 95.0, "tp": 115.0,
                            "rr": 3.0, "qty": 1.0, "risk_pct": 0.01, "opened_at": "2026-08-20 10:00:00",
                            "reason": "test", "llm": False}]
        st["last_candle_ts"] = 1000
        # Nến mở cửa 94 (< SL 95) → GAP qua SL, khớp tại giá mở (trượt giá)
        candles = [{"t": 2000, "o": 94.0, "h": 96.0, "l": 93.0, "c": 95.5}]
        events = replay_candles(st, candles, self.tmp)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "closed")
        r = events[0]["record"]
        self.assertEqual(r["exit"], 94.0)
        self.assertIn("GAP", r["exit_reason"])
        self.assertEqual(st["positions"], [])
        self.assertEqual(st["trades"], 1)
        self.assertAlmostEqual(r["pnl"], -6.0)
        self.assertEqual(st["last_candle_ts"], 2000)

    def test_sl_before_tp_in_same_candle(self):
        st = new_trader_state()
        st["positions"] = [{"id": 1, "dir": "long", "tf": "1h", "entry": 100.0, "sl": 95.0, "tp": 105.0,
                            "rr": 1.0, "qty": 1.0, "risk_pct": 0.01, "opened_at": "x", "reason": "t", "llm": False}]
        # Cùng 1 nến chạm cả SL và TP → thận trọng: tính SL trước
        events = replay_candles(st, [{"t": 5000, "o": 100.0, "h": 106.0, "l": 94.0, "c": 104.0}], self.tmp)
        self.assertEqual(events[0]["record"]["exit"], 95.0)
        self.assertIn("SL", events[0]["record"]["exit_reason"])

    def test_replay_skips_old_candles(self):
        st = new_trader_state()
        st["positions"] = [{"id": 1, "dir": "long", "tf": "1h", "entry": 100.0, "sl": 95.0, "tp": 115.0,
                            "rr": 3.0, "qty": 1.0, "risk_pct": 0.01, "opened_at": "x", "reason": "t", "llm": False}]
        st["last_candle_ts"] = 3000
        candles = [
            {"t": 1000, "o": 90, "h": 91, "l": 89, "c": 90},   # cũ — phải bị bỏ qua
            {"t": 4000, "o": 101, "h": 103, "l": 100, "c": 102},  # mới — không chạm SL/TP
        ]
        events = replay_candles(st, candles, self.tmp)
        self.assertEqual(events, [])
        self.assertEqual(len(st["positions"]), 1)

    # ---- Lệnh chờ: gap + hết hạn ----
    def test_pending_activated_with_gap(self):
        st = new_trader_state()
        st["pending_orders"] = [{"id": 9, "dir": "long", "type": "buy_limit", "trigger": 98.0,
                                 "sl": 95.0, "tp": 106.0, "rr": 2.66, "risk_pct": 0.01,
                                 "reason": "t", "llm": False, "sessions_alive": 0}]
        st["last_candle_ts"] = 1000
        events = replay_candles(st, [{"t": 2000, "o": 97.0, "h": 99.0, "l": 96.5, "c": 98.5}], self.tmp)
        self.assertEqual(events[0]["event"], "activated")
        self.assertEqual(events[0]["order"]["fill"], 97.0)  # khớp tại giá mở (gap qua trigger 98)
        self.assertEqual(len(st["positions"]), 1)
        self.assertEqual(st["positions"][0]["entry"], 97.0)
        self.assertEqual(st["pending_orders"], [])

    def test_pending_expiry(self):
        st = new_trader_state()
        st["pending_orders"] = [{"id": 9, "dir": "long", "type": "buy_limit", "trigger": 98.0,
                                 "sl": 95.0, "tp": 106.0, "rr": 2.66, "risk_pct": 0.01,
                                 "reason": "t", "llm": False, "sessions_alive": 7}]
        candles = [{"t": 2000, "o": 99, "h": 100, "l": 98.9, "c": 99.5}]
        events = replay_candles(st, candles, self.tmp)
        self.assertTrue(any(e["event"] == "expired" for e in events))
        self.assertEqual(st["pending_orders"], [])

    # ---- Chặn giao dịch ----
    def test_closed_market_blocks_trading(self):
        st = new_trader_state()
        save_trader_state(st, self.tmp)
        market_status = {"open": False, "text": "🔴 THỊ TRƯỜNG ĐÓNG (cuối tuần)"}
        market_data = {"synthetic": False, "price_quality": "spot"}
        klines = [{"t": 1000, "o": 100, "h": 101, "l": 99, "c": 100.5}]
        st2, events, decided, reason = trader_step(
            self.cfg, make_args(), make_ind(), make_mc(), 0.2, "MUA NHẸ 📈", FINALS,
            100.5, klines, self.tmp, market_status, market_data)
        self.assertEqual(st2["positions"], [])
        self.assertEqual(st2["closed_sessions"], 1)
        self.assertIn("ĐÓNG", reason)

    def test_synthetic_data_blocks_trading(self):
        st = new_trader_state()
        save_trader_state(st, self.tmp)
        market_status = {"open": True, "text": "🟢 THỊ TRƯỜNG MỞ"}
        market_data = {"synthetic": True, "price_quality": "default"}
        klines = [{"t": 1000, "o": 100, "h": 101, "l": 99, "c": 100.5}]
        st2, events, decided, reason = trader_step(
            self.cfg, make_args(), make_ind(), make_mc(), 0.2, "MUA NHẸ 📈", FINALS,
            100.5, klines, self.tmp, market_status, market_data)
        self.assertEqual(st2["positions"], [])
        self.assertEqual(st2["trades"], 0)
        self.assertIn("DỮ LIỆU", reason)

    # ---- State ----
    def test_state_roundtrip(self):
        st = new_trader_state()
        st["balance"] = 1234.5
        save_trader_state(st, self.tmp)
        st2 = load_trader_state(self.tmp)
        self.assertEqual(st2["balance"], 1234.5)
        self.assertIsInstance(st2["positions"], list)
        self.assertIsInstance(st2["pending_orders"], list)

    def test_initial_candle_ts_from_position(self):
        """State cũ có lệnh mở nhưng chưa có last_candle_ts → mốc = nến chứa thời điểm mở lệnh."""
        import datetime
        st = new_trader_state()
        st["positions"] = [{"id": 1, "dir": "long", "tf": "1h", "entry": 100.0, "sl": 95.0, "tp": 115.0,
                            "rr": 3.0, "qty": 1.0, "risk_pct": 0.01, "opened_at": "2026-08-20 08:34:20",
                            "reason": "t", "llm": False}]
        step = 3600_000
        t0 = int(datetime.datetime(2026, 8, 20, 8, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        klines = [{"t": t0 + i * step, "o": 100, "h": 101, "l": 99, "c": 100} for i in range(10)]
        got = _initial_candle_ts(st, klines)
        self.assertEqual(got, t0)  # đầu nến 08:00 chứa 08:34

    def test_initial_candle_ts_fallback(self):
        """Không có lệnh mở/chờ → lấy 3 nến cuối."""
        st = new_trader_state()
        step = 3600_000
        klines = [{"t": 1000 * step + i * step, "o": 100, "h": 101, "l": 99, "c": 100} for i in range(10)]
        self.assertEqual(_initial_candle_ts(st, klines), klines[-3]["t"])

    def test_migrate_old_single_position(self):
        st = new_trader_state()
        st["position"] = {"id": 7, "dir": "long", "tf": "1h", "entry": 100, "sl": 95, "tp": 110,
                          "rr": 2.0, "qty": 1, "risk_pct": 0.01, "opened_at": "x", "reason": "t", "llm": False}
        save_trader_state(st, self.tmp)
        st2 = load_trader_state(self.tmp)
        self.assertIsNone(st2["position"])
        self.assertEqual(st2["positions"][0]["id"], 7)


if __name__ == "__main__":
    unittest.main()
