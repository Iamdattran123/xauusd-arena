# -*- coding: utf-8 -*-
"""🎛️ Lệnh Telegram — boss ra lệnh từ xa.

  "trade 1h" / "trade 15p" / "trade 4h" / "trade 1d" — đổi khung giao dịch
  "status" — trạng thái hiện tại
  "market" — giờ mở/đóng cửa thị trường
  "tổng kết ngày" / "tổng kết tổng" — thống kê
  "reset trader" — reset vốn về $1.000
  "stop" — quay lại khung mặc định

Bảo mật: nếu trader.allowed_user_ids được cấu hình → chỉ chủ nhân mới điều khiển được.
"""
import json
import os
import time
import urllib.request

from .config import TIMEFRAMES, UA
from .market_hours import status_info
from .state import read_json, write_json
from .trader import build_summary_report, load_trader_state, new_trader_state, save_trader_state, trader_status_line


def _allowed(cfg, user_id):
    allowed = cfg.get("trader", {}).get("allowed_user_ids") or []
    if not allowed:
        return True
    return str(user_id) in {str(x) for x in allowed}


def telegram_poll_commands(cfg, out_dir, send_fn=None):
    """Đọc tin nhắn Telegram (getUpdates) → xử lý lệnh → lưu trade_tf. Trả trade_tf mới (hoặc '')."""
    from .reports import send_telegram
    send_fn = send_fn or send_telegram
    token = cfg.get("trader", {}).get("telegram_token", "")
    chat = cfg.get("trader", {}).get("telegram_chat_id", "")
    if not token or not chat:
        return ""
    tg_path = os.path.join(out_dir, "tg_state.json")
    tg_state = read_json(tg_path, {"offset": 0, "trade_tf": ""})
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates?offset={tg_state.get('offset', 0) + 1}&timeout=1"
        req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        updates = data.get("result", []) if data.get("ok") else []
        changed = False
        for u in updates:
            up_id = u.get("update_id")
            msg_obj = u.get("message") or {}
            sender = str((msg_obj.get("from") or {}).get("id", ""))
            if not _allowed(cfg, sender):
                tg_state["offset"] = max(tg_state.get("offset", 0), up_id)
                continue
            msg = msg_obj.get("text", "") or ""
            msg_l = msg.lower().strip()
            tf = None
            if "trade" in msg_l or "khung" in msg_l or "giao dịch" in msg_l:
                if "15" in msg_l:
                    tf = "15m"
                elif "4h" in msg_l or "4 giờ" in msg_l:
                    tf = "4h"
                elif "1d" in msg_l or "1 ngày" in msg_l or "ngày" in msg_l:
                    tf = "1D"
                elif "1h" in msg_l or "1 giờ" in msg_l or "giờ" in msg_l:
                    tf = "1h"
            if tf:
                tg_state["trade_tf"] = tf
                changed = True
                send_fn(f"✅ Đã đổi khung giao dịch sang **{TIMEFRAMES[tf]['label']}** — từ phiên tới AI Trader chỉ trade khung này.", cfg)
            elif msg_l in ("status", "trạng thái", "tình hình"):
                st = load_trader_state(out_dir)
                info = trader_status_line(st, st.get("balance", 0))
                tf_now = tg_state.get("trade_tf", "") or "1h"
                send_fn(f"📊 *TRẠNG THÁI*\nKhung giao dịch: {tf_now}\n{info}\n"
                        f"Phiên đã chạy: {st.get('sessions', 0)}", cfg)
            elif msg_l in ("market", "thị trường", "giờ mở", "giờ đóng"):
                s = status_info(None, cfg)
                send_fn(f"⏰ *THỊ TRƯỜNG VÀNG*\n{s['text']}"
                        + (f"\nĐóng cửa phiên tiếp theo: {s['next_close']}" if s.get("open") and s.get("next_close") else ""), cfg)
            elif msg_l in ("tổng kết ngày", "tong ket ngay", "tổng kết hôm nay", "tk ngày", "tk hom nay", "summary day"):
                st = load_trader_state(out_dir)
                send_fn(build_summary_report(st, "day"), cfg)
            elif msg_l in ("tổng kết tổng", "tong ket tong", "tổng kết toàn bộ", "tk tổng", "tk tong", "summary all", "tổng kết tất cả", "tong ket tat ca", "tổng kết"):
                st = load_trader_state(out_dir)
                send_fn(build_summary_report(st, "all"), cfg)
            elif "reset trader" in msg_l or "reset vốn" in msg_l:
                st = new_trader_state()
                save_trader_state(st, out_dir)
                send_fn("↺ Đã reset vốn AI Trader về $1.000.", cfg)
            elif "stop" in msg_l or "dừng" in msg_l:
                tg_state["trade_tf"] = ""
                changed = True
                send_fn("⏸️ Đã quay lại khung mặc định (1h).", cfg)
            tg_state["offset"] = max(tg_state.get("offset", 0), up_id)
        if changed or updates:
            write_json(tg_path, tg_state)
        return tg_state.get("trade_tf", "")
    except Exception as e:
        print(f"⚠️ Poll Telegram lỗi: {str(e)[:100]}")
        return tg_state.get("trade_tf", "")
