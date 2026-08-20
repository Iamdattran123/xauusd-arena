# -*- coding: utf-8 -*-
"""AI TRADER — quản lý vốn mô phỏng $1.000, lệnh mở/chờ, SL/TP, bài học, điểm.

Các cải tiến v2 so với bản cũ:
  1. PHÁT LẠI NẾN: mỗi phiên duyệt TUẦN TỰ mọi nến mới kể từ lần chạy trước —
     không bỏ sót SL/TP khi cron trễ/vps nghỉ giữa chừng.
  2. GAP cuối tuần: nến mở cửa vượt qua SL/TP → khớp tại giá MỞ (trượt giá thực tế).
  3. CHẶN GIAO DỊCH khi: thị trường đóng (cuối tuần/lễ) hoặc dữ liệu là dữ liệu mẫu.
  4. Lệnh chờ chỉ tính "tuổi" trong các phiên thị trường MỞ (không hết hạn oan cuối tuần).
"""
import math
import time

from .config import MAX_PENDING, MAX_POSITIONS, PENDING_EXPIRY_SESSIONS, TIMEFRAMES, any_api_key
from .indicators import clamp
from .llm import _build_chain, call_llm, extract_json
from .state import read_json, write_json


# ----------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------
def new_trader_state():
    return {
        "balance": 1000.0, "start_balance": 1000.0, "peak": 1000.0, "max_dd": 0.0,
        "positions": [], "position": None, "pending_orders": [], "history": [], "trades": 0, "wins": 0,
        "total_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
        "trader_score": 1000.0, "lessons": [],
        "sessions": 0, "closed_sessions": 0, "trade_tf": "",
        "last_candle_ts": 0, "data_quality": "",
        "equity_points": [{"t": int(time.time() * 1000), "e": 1000.0}],
    }


def load_trader_state(out_dir):
    p = out_dir + "/trader_state.json"
    st = read_json(p)
    if isinstance(st, dict) and "balance" in st:
        merged = new_trader_state()
        merged.update(st)
        # migrate: state cũ dùng "position" đơn → chuyển sang danh sách "positions"
        if merged.get("position"):
            if not isinstance(merged.get("positions"), list):
                merged["positions"] = []
            if not any(pp.get("id") == merged["position"].get("id") for pp in merged["positions"]):
                merged["positions"].append(merged["position"])
        merged["position"] = None
        for key in ("positions", "pending_orders", "lessons", "equity_points", "history"):
            if not isinstance(merged.get(key), list):
                merged[key] = []
        merged.setdefault("trader_score", 1000.0)
        merged.setdefault("trade_tf", "")
        merged.setdefault("last_candle_ts", 0)
        merged.setdefault("closed_sessions", 0)
        merged.setdefault("data_quality", "")
        return merged
    return new_trader_state()


def save_trader_state(st, out_dir):
    import os
    os.makedirs(out_dir, exist_ok=True)
    write_json(os.path.join(out_dir, "trader_state.json"), st)


# ----------------------------------------------------------------------
# QUYẾT ĐỊNH
# ----------------------------------------------------------------------
def trader_heuristic(consensus, prob_up, price, atr, target, sup=None, res=None):
    """Fallback khi không có LLM — có thể đặt lệnh chờ như trader chuyên nghiệp."""
    action, sl, tp = "hold", None, None
    strong = abs(consensus) >= 0.25
    tf = "4h" if strong else "1h"
    otype, trigger = "market", 0
    if consensus >= 0.15 and prob_up > 0.52:
        action = "long"
        sl = price - max(1.5 * atr, price * 0.003)
        tp = target if target > price else price + 2 * max(1.5 * atr, price * 0.003)
        if (tp - price) / (price - sl) < 1.2:
            tp = price + 2 * (price - sl)
        if sup and 0.3 * atr < (price - sup) < 3 * atr:
            otype, trigger = "buy_limit", sup
            sl = trigger - max(1.5 * atr, price * 0.003)
            tp = trigger + 2 * (trigger - sl)
    elif consensus <= -0.15 and prob_up < 0.48:
        action = "short"
        sl = price + max(1.5 * atr, price * 0.003)
        tp = target if target < price else price - 2 * max(1.5 * atr, price * 0.003)
        if (price - tp) / (sl - price) < 1.2:
            tp = price - 2 * (sl - price)
        if res and 0.3 * atr < (res - price) < 3 * atr:
            otype, trigger = "sell_limit", res
            sl = trigger + max(1.5 * atr, price * 0.003)
            tp = trigger - 2 * (sl - trigger)
    if action == "hold":
        rr = 0
    elif otype == "market":
        rr = (tp - price) / (price - sl) if action == "long" else (price - tp) / (sl - price)
    else:
        rr = (tp - trigger) / (trigger - sl) if action == "long" else (trigger - tp) / (sl - trigger)
    reason = ("Đứng ngoài bảo toàn vốn — hội đồng chưa đủ phân cực." if action == "hold"
              else f"Tự quyết {action.upper()} ({'lệnh chờ ' + otype + ' @ ' + f'${trigger:,.0f}' if otype != 'market' else 'vào ngay'}) "
                   f"theo phán quyết hội đồng ({consensus:+.2f}), P(tăng) {prob_up*100:.0f}%.")
    return {"action": action, "tf": tf, "sl": sl, "tp": tp, "risk": 0.01, "rr": rr,
            "reason": reason, "llm": False, "order_type": otype, "trigger": trigger}


def trader_llm_prompt(consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, st,
                      market_status=None):
    panel = "\n".join(f"• {f['title']}: {f['stance']:+.2f} (tự tin {f['conf']*100:.0f}%) — {f.get('reasoning') or f.get('reason') or ''}"
                      for f in finals)
    pos = st.get("positions") or []
    pos_line = ("ĐANG MỞ: " + " · ".join(f"{p['dir'].upper()} {p['tf']}@{p['entry']:,.0f}" for p in pos)) if pos else ""
    pend = st.get("pending_orders") or []
    pend_line = ("LỆNH CHỜ: " + " · ".join(f"{p['type'].upper()} {p['trigger']:,.0f}" for p in pend)) if pend else ""
    hist = st.get("history") or []
    hist_line = ""
    if hist:
        recent = hist[:8]
        hist_line = "\n".join(f"  • {h['dir'].upper()} {h['tf']} entry ${h['entry']:,.0f} → exit ${h['exit']:,.0f} "
                              f"({h['exit_reason']}) P&L {h['pnl']:+,.2f}$" for h in recent)
    lessons = (st.get("lessons") or [])[-6:]
    lessons_line = "\n".join(f"  - {l}" for l in lessons) if lessons else "  (chưa có)"
    score = st.get("trader_score", 1000.0)
    trades = st.get("trades", 0)
    wins = st.get("wins", 0)
    wr = f"{wins/max(1,trades)*100:.0f}%" if trades else "—"
    market_line = ""
    if market_status:
        market_line = "⚖️ TRẠNG THÁI THỊ TRƯỜNG: " + market_status["text"]
        if market_status["open"]:
            market_line += "\nThị trường đang mở — có thể giao dịch bình thường."
        else:
            market_line += "\n⚠️ Thị trường đang ĐÓNG — KHÔNG được mở lệnh mới phiên này."
    return (f"Bạn là AI TRADER chuyên nghiệp độc lập, quản lý quỹ mô phỏng ${balance:,.2f} giao dịch vàng (XAU/USD). "
            f"HỘI ĐỒNG CHỈ GÓP Ý — bạn có quyền nghe hoặc giữ lập trường riêng. Ưu tiên bảo toàn vốn.\n\n"
            f"{market_line}\n\n"
            f"HỘI ĐỒNG GÓP Ý (tham khảo):\n{panel}\nĐồng thuận ròng: {consensus:+.3f} ({verdict})\n\n"
            f"THỊ TRƯỜNG:\nGiá: ${price:,.2f} · ATR: ${atr:,.2f} · Hỗ trợ: ${sup:,.2f} · Kháng cự: ${res:,.2f}\n"
            f"Monte Carlo: mục tiêu ${target:,.2f} · P10 ${p10:,.2f} · P90 ${p90:,.2f} · P(tăng) {prob_up*100:.0f}%\n"
            f"{pos_line}\n{pend_line}\n\n"
            f"📜 LỊCH SỬ GIAO DỊCH CỦA BẠN (8 lệnh gần nhất — nhớ để phân tích tiếp, KHÔNG phải phiên mới):\n{hist_line}\n\n"
            f"🧠 BÀI HỌC ĐÃ RÚT RA:\n{lessons_line}\n"
            f"🏆 ĐIỂM KINH NGHIỆM: {score:.0f} · Số lệnh: {trades} · Win rate: {wr}\n\n"
            f"NHIỆM VỤ: Tự suy nghĩ độc lập, dựa vào lịch sử + bài học + dự đoán tiếp tục, quyết định giao dịch phiên này.\n"
            f"- Không nên giao dịch → action \"hold\".\n"
            f"- Giao dịch NGAY → order_type \"market\".\n"
            f"- Đặt LỆNH CHỜ (chuyên nghiệp) → order_type \"buy_limit\" | \"buy_stop\" | \"sell_limit\" | \"sell_stop\", kèm \"trigger\" = MỨC GIÁ chờ.\n"
            f"- SL/TP là MỨC GIÁ cụ thể, risk_pct 0.5-3, khung thời gian 15m/1h/4h/1D.\n"
            f'Trả về DUY NHẤT JSON: {{"action": "long|short|hold", "order_type": "market|buy_limit|buy_stop|sell_limit|sell_stop", '
            f'"trigger": <giá chờ, bỏ 0 nếu market>, "timeframe": "15m|1h|4h|1D", "sl": <số>, "tp": <số>, '
            f'"risk_pct": <số>, "reason": "<lý do 2-3 câu tiếng Việt, nêu bạn có nghe hội đồng hay giữ lập trường riêng>"}}')


def trader_llm_decision(cfg, consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, st,
                        market_status=None):
    if not any_api_key(cfg):
        return None
    chosen = cfg.get("trader", {})
    if isinstance(chosen, str):
        chosen = {"provider": "openrouter", "model": chosen}
    chain = _build_chain(cfg, chosen.get("provider", "openrouter"), chosen.get("model", ""))
    prompt = trader_llm_prompt(consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, st,
                               market_status)
    for provider, model in chain:
        try:
            res = call_llm("trader", prompt, cfg, provider=provider, model=model)
            j = extract_json(res["text"])
            if not j:
                continue
            action = str(j.get("action", "")).lower()
            if action not in ("long", "short", "hold"):
                continue
            tf = str(j.get("timeframe", "1h")) if str(j.get("timeframe", "")) in ("15m", "1h", "4h", "1D") else "1h"
            sl, tp = float(j.get("sl", 0) or 0), float(j.get("tp", 0) or 0)
            risk = clamp(float(j.get("risk_pct", 1.0) or 1.0), 0.5, 3.0) / 100
            reason = str(j.get("reason", ""))[:500]
            otype = str(j.get("order_type", "market")).lower()
            trigger = float(j.get("trigger", 0) or 0)
            if otype not in ("market", "buy_limit", "buy_stop", "sell_limit", "sell_stop"):
                otype = "market"
            if action == "hold":
                return {"action": "hold", "tf": tf, "sl": None, "tp": None, "risk": risk, "rr": 0,
                        "reason": reason, "llm": True, "order_type": "hold", "trigger": 0}
            if not (sl > 0 and tp > 0):
                continue
            if otype == "market":
                if action == "long" and not (sl < price < tp):
                    continue
                if action == "short" and not (tp < price < sl):
                    continue
                rr = (tp - price) / (price - sl) if action == "long" else (price - tp) / (sl - price)
                return {"action": action, "tf": tf, "sl": sl, "tp": tp, "risk": risk,
                        "rr": max(0.1, rr), "reason": reason, "llm": True,
                        "order_type": "market", "trigger": 0}
            if not trigger or trigger <= 0:
                continue
            if otype == "buy_limit" and not (trigger < price):
                continue
            if otype == "sell_limit" and not (trigger > price):
                continue
            if otype == "buy_stop" and not (trigger > price):
                continue
            if otype == "sell_stop" and not (trigger < price):
                continue
            rr = (tp - trigger) / (trigger - sl) if action == "long" else (trigger - tp) / (sl - trigger)
            return {"action": action, "tf": tf, "sl": sl, "tp": tp, "risk": risk,
                    "rr": max(0.1, rr), "reason": reason, "llm": True,
                    "order_type": otype, "trigger": trigger}
        except Exception as e:
            print(f"  🔄 Trader ({provider}/{model}) lỗi: {str(e)[:100]}")
    return None


# ----------------------------------------------------------------------
# THỰC THI LỆNH
# ----------------------------------------------------------------------
def trader_create_pending(st, decision, price, out_dir):
    if decision["action"] == "hold":
        return False
    if len(st.get("pending_orders") or []) >= MAX_PENDING:
        print(f"🚫 Đã đủ {MAX_PENDING} lệnh chờ — hủy lệnh chờ cũ trước khi đặt mới.")
        return False
    otype = decision.get("order_type", "market")
    if otype == "market":
        return False
    trigger = decision.get("trigger", 0)
    if not trigger or trigger <= 0:
        return False
    if otype in ("buy_limit", "sell_stop") and decision["action"] != "long":
        return False
    if otype in ("sell_limit", "buy_stop") and decision["action"] != "short":
        return False
    if otype in ("buy_limit", "sell_limit"):
        if otype == "buy_limit" and not (trigger < price):
            return False
        if otype == "sell_limit" and not (trigger > price):
            return False
    st["pending_orders"].append({
        "id": int(time.time() * 1000) + len(st["pending_orders"]),
        "dir": decision["action"], "type": otype, "trigger": trigger,
        "sl": decision["sl"], "tp": decision["tp"], "rr": decision["rr"],
        "risk_pct": decision["risk"], "reason": decision["reason"],
        "llm": decision.get("llm", False), "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sessions_alive": 0,
    })
    save_trader_state(st, out_dir)
    print(f"📌 AI TRADER ĐẶT LỆNH CHỜ: {otype.upper()} {decision['tf']} · chờ ${trigger:,.2f} · "
          f"SL ${decision['sl']:,.2f} · TP ${decision['tp']:,.2f} · RR 1:{decision['rr']:.1f}")
    return True


def trader_execute(st, decision, price, out_dir):
    if decision["action"] == "hold":
        return False
    if len(st.get("positions") or []) >= MAX_POSITIONS:
        print(f"🚫 Đã đủ {MAX_POSITIONS} lệnh mở — chờ 1 lệnh chạm SL/TP mới được mở tiếp.")
        return False
    sl_dist = abs(decision["sl"] - price)
    if sl_dist < 1e-9:
        return False
    risk_amt = st["balance"] * decision["risk"]
    qty = risk_amt / sl_dist
    if qty * price > st["balance"] * 20:
        return False
    st["positions"].append({"id": int(time.time() * 1000), "dir": decision["action"], "tf": decision["tf"],
                            "entry": price, "sl": decision["sl"], "tp": decision["tp"], "rr": decision["rr"],
                            "qty": qty, "risk_pct": decision["risk"],
                            "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "reason": decision["reason"], "llm": decision.get("llm", False)})
    save_trader_state(st, out_dir)
    print(f"💼 AI TRADER MỞ LỆNH {decision['action'].upper()} {decision['tf']} #{len(st['positions'])}/{MAX_POSITIONS} · "
          f"entry ${price:,.2f} · SL ${decision['sl']:,.2f} · TP ${decision['tp']:,.2f} · RR 1:{decision['rr']:.1f} · rủi ro ${risk_amt:,.2f}")
    return True


# ----------------------------------------------------------------------
# PHÁT LẠI NẾN — kiểm tra SL/TP & lệnh chờ theo TỪNG nến mới (có xử lý gap)
# ----------------------------------------------------------------------
def _parse_ts(s, default=0):
    """'2026-08-20 08:34:20' → epoch ms (UTC). Lỗi → default."""
    try:
        import datetime
        dt = datetime.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
        return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    except Exception:
        return default


def _initial_candle_ts(st, klines):
    """Mốc phát lại ban đầu khi last_candle_ts chưa có (state cũ / lệnh đang mở).

    Lấy từ thời điểm mở lệnh/lệnh chờ sớm nhất — tránh phát lại CẢ lịch sử nến
    lên lệnh đang mở (sẽ đóng lệnh "ngược thời gian").
    """
    marks = []
    for p in st.get("positions") or []:
        marks.append(_parse_ts(p.get("opened_at")))
    for po in st.get("pending_orders") or []:
        marks.append(_parse_ts(po.get("created_at")))
    marks = [m for m in marks if m > 0]
    if marks:
        step = (klines[1]["t"] - klines[0]["t"]) if len(klines) > 1 else 3600_000
        return (min(marks) // step) * step  # về đầu nến chứa thời điểm mở lệnh
    # không có mốc nào → chỉ lấy vài nến cuối (an toàn)
    return klines[-3]["t"] if len(klines) >= 3 else (klines[-1]["t"] if klines else 0)
def _close_position(st, p, exit_p, reason, out_dir):
    """Đóng 1 lệnh, cập nhật điểm/bài học/lịch sử. Trả record đã đóng."""
    mult = 1 if p["dir"] == "long" else -1
    pnl = (exit_p - p["entry"]) * mult * p["qty"]
    pnl_pct = (exit_p / p["entry"] - 1) * mult * 100
    st["balance"] += pnl
    st["peak"] = max(st["peak"], st["balance"])
    st["max_dd"] = max(st["max_dd"], (st["peak"] - st["balance"]) / st["peak"] * 100)
    st["trades"] += 1
    if pnl > 0:
        st["wins"] += 1
        st["gross_win"] += pnl
    else:
        st["gross_loss"] += -pnl
    st["total_pnl"] += pnl
    st["equity_points"].append({"t": int(time.time() * 1000), "e": st["balance"]})
    closed_rec = {**p, "exit": exit_p, "pnl": pnl, "pnl_pct": pnl_pct,
                  "exit_reason": reason, "closed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "balance_after": st["balance"]}
    st["history"].insert(0, closed_rec)
    delta = max(-100.0, min(100.0, pnl_pct * 20.0))  # thắng 1% → +20, thua 1% → -20
    st["trader_score"] = st.get("trader_score", 1000.0) + delta
    lesson = (f"{'✅' if pnl > 0 else '❌'} {p['dir'].upper()} {p['tf']} {pnl_pct:+.1f}% ({reason}) "
              f"-> {'giữ chiến lược' if pnl > 0 else 'cần chờ mốc tốt hơn/tránh vào sớm'}")
    st.setdefault("lessons", []).append(lesson)
    st["lessons"] = st["lessons"][-50:]
    print(f"💼 ĐÓNG LỆNH {p['dir'].upper()} — {reason} · entry ${p['entry']:,.2f} → exit ${exit_p:,.2f} · "
          f"P&L {pnl:+,.2f}$ ({pnl_pct:+.2f}%) · vốn ${st['balance']:,.2f} · 🏆 điểm {st['trader_score']:.0f}")
    return closed_rec


def _check_position_on_candle(st, p, c):
    """Kiểm tra 1 lệnh với 1 nến đã đóng. Trả (exit_p, reason) hoặc (None, '').

    Thứ tự ưu tiên (thận trọng): gap mở cửa → SL → TP (SL thắng nếu cùng nến).
    """
    o, h, l = c["o"], c["h"], c["l"]
    if p["dir"] == "long":
        if o <= p["sl"]:
            return o, "GAP qua SL (trượt giá)"
        if l <= p["sl"]:
            return p["sl"], "chạm CẮT LỖ (SL)"
        if h >= p["tp"]:
            return p["tp"], "chạm CHỐT LỜI (TP)"
    else:
        if o >= p["sl"]:
            return o, "GAP qua SL (trượt giá)"
        if h >= p["sl"]:
            return p["sl"], "chạm CẮT LỖ (SL)"
        if l <= p["tp"]:
            return p["tp"], "chạm CHỐT LỜI (TP)"
    return None, ""


def _pending_fill_price(po, c):
    """Giá khớp lệnh chờ trên 1 nến (ưu tiên gap tại giá mở). Trả giá hoặc None."""
    o, h, l = c["o"], c["h"], c["l"]
    if po["type"] == "buy_limit":
        if o <= po["trigger"]:
            return o
        return po["trigger"] if l <= po["trigger"] else None
    if po["type"] == "buy_stop":
        if o >= po["trigger"]:
            return o
        return po["trigger"] if h >= po["trigger"] else None
    if po["type"] == "sell_limit":
        if o >= po["trigger"]:
            return o
        return po["trigger"] if h >= po["trigger"] else None
    if po["type"] == "sell_stop":
        if o <= po["trigger"]:
            return o
        return po["trigger"] if l <= po["trigger"] else None
    return None


def replay_candles(st, new_candles, out_dir):
    """Phát lại các nến mới kể từ lần chạy trước (tự lọc nến cũ theo last_candle_ts).

    Trả events: [{event: 'closed'|'activated'|'expired'|'cancelled_full', ...}]
    """
    events = []
    last_ts = st.get("last_candle_ts", 0)
    pending_candles = [c for c in new_candles if c["t"] > last_ts]
    if not pending_candles:
        return events
    for c in sorted(pending_candles, key=lambda k: k["t"]):
        # 1) đóng lệnh chạm SL/TP trên nến này
        remaining, closed = [], []
        for p in st.get("positions") or []:
            exit_p, reason = _check_position_on_candle(st, p, c)
            if exit_p is None:
                remaining.append(p)
            else:
                closed.append((p, exit_p, reason))
        st["positions"] = remaining
        for p, exit_p, reason in closed:
            rec = _close_position(st, p, exit_p, reason, out_dir)
            events.append({"event": "closed", "record": rec})

        # 2) kích hoạt / hết hạn lệnh chờ trên nến này
        pend_remaining = []
        for po in st.get("pending_orders") or []:
            po["sessions_alive"] = po.get("sessions_alive", 0) + 1
            fill = _pending_fill_price(po, c)
            if fill is not None:
                if len(st.get("positions") or []) < MAX_POSITIONS:
                    sl_dist = abs(po["sl"] - po["trigger"])
                    qty = st["balance"] * po["risk_pct"] / sl_dist if sl_dist > 1e-9 else 0
                    st["positions"].append({
                        "id": int(time.time() * 1000), "dir": po["dir"], "tf": po.get("tf", "1h"),
                        "entry": fill, "sl": po["sl"], "tp": po["tp"], "rr": po["rr"],
                        "qty": qty, "risk_pct": po["risk_pct"], "pending": True,
                        "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "reason": po["reason"], "llm": po.get("llm", False),
                    })
                    events.append({"event": "activated", "order": {**po, "fill": fill}})
                    print(f"⚡ LỆNH CHỜ KÍCH HOẠT: {po['dir'].upper()} {po['type'].upper()} @ ${fill:,.2f} "
                          f"(SL ${po['sl']:,.2f} · TP ${po['tp']:,.2f})")
                else:
                    events.append({"event": "cancelled_full", "order": po})
                    print(f"🚫 LỆNH CHỜ {po['type'].upper()} bị hủy — đã đủ {MAX_POSITIONS} lệnh mở.")
                continue
            if po["sessions_alive"] >= PENDING_EXPIRY_SESSIONS:
                events.append({"event": "expired", "order": po})
                print(f"🗑️ LỆNH CHỜ HẾT HẠN: {po['type'].upper()} @ ${po['trigger']:,.2f} "
                      f"(sau {po['sessions_alive']} phiên chưa chạm)")
                continue
            pend_remaining.append(po)
        st["pending_orders"] = pend_remaining

        st["last_candle_ts"] = c["t"]
    if events:
        save_trader_state(st, out_dir)
    return events


# ----------------------------------------------------------------------
# PHIÊN TRADER — điều phối mọi thứ
# ----------------------------------------------------------------------
def trader_step(cfg, args, ind, mc, consensus, verdict, finals, price, klines, out_dir,
                market_status, market_data, send_fn=None):
    """Vòng lặp 1 phiên: phát lại nến → quyết định mới (nếu được phép) → sự kiện."""
    st = load_trader_state(out_dir)
    st["sessions"] = st.get("sessions", 0) + 1
    st["data_quality"] = market_data.get("price_quality", "")
    synthetic = bool(market_data.get("synthetic"))
    market_open = bool(market_status.get("open"))
    events = []
    decided = None
    block_reason = ""

    if synthetic:
        block_reason = "⛔ DỮ LIỆU GIẢ LẬP (offline) — AI Trader KHÔNG giao dịch trên dữ liệu mẫu"
    elif not market_open:
        block_reason = "🔴 THỊ TRƯỜNG ĐÓNG — giữ nguyên trạng thái, không mở lệnh mới"
        st["closed_sessions"] = st.get("closed_sessions", 0) + 1

    if block_reason:
        print(block_reason)
        save_trader_state(st, out_dir)  # vẫn lưu sessions/closed_sessions
    else:
        # 1) phát lại các nến mới (không bỏ sót SL/TP dù cron trễ)
        if not st.get("last_candle_ts") and (st.get("positions") or st.get("pending_orders")):
            st["last_candle_ts"] = _initial_candle_ts(st, klines)
            print(f"🕐 Khởi tạo mốc phát lại nến: {time.strftime('%d/%m %H:%M', time.gmtime(st['last_candle_ts']/1000))} "
                  f"(từ lệnh đang mở/chờ — không phát lại lịch sử cũ)")
        events = replay_candles(st, klines, out_dir)
        if klines and not events:
            st["last_candle_ts"] = max(st.get("last_candle_ts", 0), klines[-1]["t"])

        # 2) cảnh báo lệnh GẦN chạm SL/TP (≤ 1 ATR) — 1 lần mỗi lệnh
        try:
            atr_now = ind.get("atr", 0) or 0
            last_c = klines[-1]["c"] if klines else price
            for p in st.get("positions") or []:
                if p.get("warned"):
                    continue
                dist_sl = abs(last_c - p["sl"])
                dist_tp = abs(last_c - p["tp"])
                if atr_now and (dist_sl <= atr_now or dist_tp <= atr_now):
                    near = "SL" if dist_sl <= dist_tp else "TP"
                    p["warned"] = True
                    save_trader_state(st, out_dir)
                    if send_fn:
                        send_fn(f"🔔 *CẢNH BÁO GẦN {near}* — lệnh {p['dir'].upper()} {p['tf']}\n"
                                f"Giá hiện tại ${last_c:,.2f} · {near} ${p['sl' if near == 'SL' else 'tp']:,.2f}\n"
                                f"Cách {min(dist_sl, dist_tp):,.2f}$ (~1 ATR) — chuẩn bị sẵn sàng!", cfg)
        except Exception as e:
            print(f"⚠️ Cảnh báo gần SL/TP lỗi: {e}")

        # 3) khung theo lệnh Telegram
        cmd_tf = st.get("trade_tf", "")
        if cmd_tf and cmd_tf in TIMEFRAMES:
            print(f"🎛️ Khung giao dịch theo lệnh Telegram: {TIMEFRAMES[cmd_tf]['label']}")

        # 4) quyết định mới nếu còn slot
        if len(st.get("positions") or []) < MAX_POSITIONS:
            decided = trader_llm_decision(cfg, consensus, verdict, finals, price, ind["atr"],
                                          ind["sup"], ind["res"], mc["target"], mc["p10"], mc["p90"],
                                          mc["prob_up"], st["balance"], st, market_status)
            if not decided:
                decided = trader_heuristic(consensus, mc["prob_up"], price, ind["atr"], mc["target"],
                                           ind.get("sup"), ind.get("res"))
            if cmd_tf and cmd_tf in TIMEFRAMES:
                decided["tf"] = cmd_tf
            otype = decided.get("order_type", "market")
            if decided["action"] == "hold":
                print(f"🤖 AI TRADER đứng ngoài — {decided['reason'][:120]}")
            elif otype != "market":
                print(f"🤖 AI TRADER: {decided['action'].upper()} {otype.upper()} chờ ${decided.get('trigger', 0):,.2f} · "
                      f"SL ${decided['sl']:,.2f} · TP ${decided['tp']:,.2f} · RR 1:{decided['rr']:.1f}"
                      + (" · (LLM)" if decided.get("llm") else ""))
                trader_create_pending(st, decided, price, out_dir)
            else:
                print(f"🤖 AI TRADER VÀO NGAY: {decided['action'].upper()} {decided['tf']} · "
                      f"SL ${decided['sl']:,.2f} · TP ${decided['tp']:,.2f} · RR 1:{decided['rr']:.1f}"
                      + (" · (LLM)" if decided.get("llm") else ""))
                trader_execute(st, decided, price, out_dir)
        else:
            print(f"🚫 Đã đủ {MAX_POSITIONS} lệnh mở — chờ 1 lệnh chạm SL/TP. (lệnh mở: "
                  + ", ".join(f"{p['dir'].upper()} {p['tf']}" for p in st.get("positions") or []) + ")")

        save_trader_state(st, out_dir)

    # 5) Telegram — mọi sự kiện
    if send_fn:
        for ev in events:
            if ev["event"] == "closed":
                r = ev["record"]
                send_fn(f"💼 *AI TRADER ĐÓNG LỆNH* {r['dir'].upper()} {r['tf']}\nKết quả: {r['exit_reason']}\n"
                        f"Entry ${r['entry']:,.2f} → Exit ${r['exit']:,.2f}\nP&L: {r['pnl']:+,.2f}$ ({r['pnl_pct']:+.2f}%)\n"
                        f"Vốn: ${st['balance']:,.2f} · 🏆 Điểm: {st.get('trader_score', 1000):.0f}", cfg)
            elif ev["event"] == "activated":
                po = ev["order"]
                send_fn(f"⚡ *LỆNH CHỜ KÍCH HOẠT*: {po['dir'].upper()} {po['type'].upper()} @ ${po.get('fill', po['trigger']):,.2f}\n"
                        f"SL ${po['sl']:,.2f} · TP ${po['tp']:,.2f} · RR 1:{po['rr']:.1f}", cfg)
            elif ev["event"] == "expired":
                po = ev["order"]
                send_fn(f"🗑️ *LỆNH CHỜ HẾT HẠN*: {po['type'].upper()} @ ${po['trigger']:,.2f} (không chạm mốc)", cfg)
            elif ev["event"] == "cancelled_full":
                po = ev["order"]
                send_fn(f"🚫 *LỆNH CHỜ BỊ HỦY* (đủ 3 lệnh mở): {po['type'].upper()} @ ${po['trigger']:,.2f}", cfg)
        if decided and decided.get("order_type", "market") != "market" and decided["action"] != "hold":
            send_fn(f"📌 *AI TRADER ĐẶT LỆNH CHỜ*: {decided['action'].upper()} {decided['order_type'].upper()}\n"
                    f"Chờ giá ${decided.get('trigger', 0):,.2f} · SL ${decided['sl']:,.2f} · TP ${decided['tp']:,.2f} · RR 1:{decided['rr']:.1f}", cfg)

    return st, events, decided, block_reason


# ----------------------------------------------------------------------
# THỐNG KÊ / TỔNG KẾT
# ----------------------------------------------------------------------
def trader_perf_line(st):
    if not st:
        return "AI Trader: chưa có dữ liệu"
    trades = st.get("trades", 0)
    wins = st.get("wins", 0)
    wr = f"{wins / max(1, trades) * 100:.0f}%" if trades else "—"
    pnl = st.get("total_pnl", 0.0)
    return (f"🤖 AI Trader THẬT: {trades} lệnh · win {wr} · P&L {pnl:+,.2f}$ · "
            f"vốn ${st.get('balance', 0):,.2f} · 🏆 {st.get('trader_score', 1000):.0f}")


def trader_status_line(st, price):
    if not st:
        return None
    positions = st.get("positions") or []
    pend = st.get("pending_orders") or []
    parts = []
    if positions:
        pos_parts = [f"{p['dir'].upper()} {p['tf']}@{p['entry']:,.0f} (SL {p['sl']:,.0f}/TP {p['tp']:,.0f})"
                     for p in positions]
        parts.append(f"{len(positions)}/{MAX_POSITIONS} lệnh mở: " + " · ".join(pos_parts))
    if pend:
        parts.append(f"{len(pend)} lệnh chờ: " + " · ".join(f"{p['type'].upper()}@{p['trigger']:,.0f}" for p in pend))
    score = st.get("trader_score", 1000.0)
    if not parts:
        if st.get("history"):
            h = st["history"][0]
            parts.append(f"lệnh gần nhất: {h['dir'].upper()} {h['tf']} {h['exit_reason']} {h['pnl']:+,.2f}$")
        else:
            parts.append("đứng ngoài")
    return " · ".join(parts) + f" · vốn ${st['balance']:,.2f} · 🏆 {score:.0f}"


def trader_summary(st, cfg, out_dir, force=False, send_fn=None):
    """Tổng kết định kỳ (summary_every phiên) hoặc ép tay (force)."""
    n = int(cfg.get("trader", {}).get("summary_every", 300))
    if not force and st["sessions"] < n:
        return
    closed = st["history"]
    win_rate = st["wins"] / st["trades"] * 100 if st["trades"] else 0
    pf = st["gross_win"] / st["gross_loss"] if st["gross_loss"] > 0 else (float("inf") if st["gross_win"] > 0 else 0)
    by_tf = {}
    for h in closed:
        by_tf.setdefault(h["tf"], {"n": 0, "win": 0})
        by_tf[h["tf"]]["n"] += 1
        if h["pnl"] > 0:
            by_tf[h["tf"]]["win"] += 1
    ret = st["total_pnl"] / st["start_balance"] * 100
    lines = ["📊 *XAU/USD AI DEBATE ARENA — BÁO CÁO TỔNG KẾT*", "━━━━━━━━━━━━━━━━━",
             f"⏱️ Số phiên: {st['sessions']}" + (f" (nghỉ: {st.get('closed_sessions', 0)})" if st.get("closed_sessions") else "") + ("" if not force else " (tổng kết thủ công)"),
             f"💰 Vốn: ${st['start_balance']:,.2f} → ${st['balance']:,.2f} ({st['total_pnl']:+,.2f}$ · {ret:+.1f}%)",
             f"🎯 Số lệnh: {st['trades']} · Win rate: {win_rate:.1f}%",
             f"🏆 Điểm kinh nghiệm: {st.get('trader_score', 1000):.0f}",
             f"⚖️ Profit factor: {'∞' if pf == float('inf') else f'{pf:.2f}'}",
             f"📉 Drawdown tối đa: {st['max_dd']:.1f}%",
             f"📌 Lệnh chờ đang có: {len(st.get('pending_orders') or [])}",
             "━━━━━━━━━━━━━━━━━", "📆 Theo khung thời gian:"]
    for tf, o in by_tf.items():
        lines.append(f"  • Khung {tf}: {o['n']} lệnh · thắng {o['win']} ({o['win']/o['n']*100:.0f}%)")
    if not by_tf:
        lines.append("  (chưa có lệnh)")
    lines.append("🕘 5 lệnh gần nhất:")
    for h in closed[:5]:
        lines.append(f"  • {h['dir'].upper()} {h['tf']} {h['opened_at']}: {h['pnl']:+,.2f}$ — {h['exit_reason']}")
    if not closed:
        lines.append("  (chưa có lệnh)")
    lessons = (st.get("lessons") or [])[-5:]
    if lessons:
        lines.append("🧠 Bài học gần nhất:")
        for l in lessons:
            lines.append(f"  {l}")
    lines.append("━━━━━━━━━━━━━━━━━\n👨‍💼 *BẠN là người ra quyết định cuối cùng.*")
    text = "\n".join(lines)
    import os
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary_latest.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print("📊 TỔNG KẾT " + str(st["sessions"]) + " phiên — vốn $" + f"{st['balance']:,.2f}" + " · " +
          str(st["trades"]) + " lệnh · win " + f"{win_rate:.1f}%" + " · PF " +
          ("∞" if pf == float('inf') else f"{pf:.2f}"))
    if send_fn:
        send_fn(text, cfg)
    if not force:
        st["sessions"] = 0
    save_trader_state(st, out_dir)


def build_summary_report(st, mode="day"):
    """Tổng kết: 'day' = hôm nay · 'all' = toàn bộ lịch sử."""
    if not st:
        return "Chưa có dữ liệu AI Trader."
    hist = st.get("history") or []
    if mode == "day":
        today = time.strftime("%Y-%m-%d")
        filtered = [h for h in hist if str(h.get("closed_at", ""))[:10] == today]
        label = f"*TỔNG KẾT HÔM NAY ({today})*"
    else:
        filtered = hist
        label = "*TỔNG KẾT TOÀN BỘ LỊCH SỬ*"
    trades = len(filtered)
    wins = sum(1 for h in filtered if h.get("pnl", 0) > 0)
    pnl = sum(h.get("pnl", 0) for h in filtered)
    gross_win = sum(h.get("pnl", 0) for h in filtered if h.get("pnl", 0) > 0)
    gross_loss = sum(-h.get("pnl", 0) for h in filtered if h.get("pnl", 0) < 0)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
    lines = ["📊 *XAU/USD AI DEBATE — " + label + "*", "━━━━━━━━━━━━━━━━━"]
    if mode == "day":
        lines.append(f"⏱️ {time.strftime('%d/%m/%Y %H:%M')}")
    lines.append(f"💰 Vốn: ${st.get('start_balance', 1000):,.2f} → ${st.get('balance', 0):,.2f}")
    lines.append(f"🎯 Số lệnh: {trades} · Thắng: {wins} · Thua: {trades - wins}")
    if trades:
        lines.append(f"⚡ Win rate: {wins / trades * 100:.1f}%")
    lines.append(f"💵 P&L: {pnl:+,.2f}$ · PF: {'∞' if pf == float('inf') else f'{pf:.2f}'}")
    lines.append(f"🏆 Điểm kinh nghiệm: {st.get('trader_score', 1000):.0f}")
    top = filtered[:8]
    if top:
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append("🕘 Các lệnh:")
        for h in top:
            lines.append(f"  • {h.get('dir', '?').upper()} {h.get('tf', '?')} {str(h.get('closed_at', ''))[5:16]}: "
                         f"{h.get('pnl', 0):+,.2f}$ ({h.get('exit_reason', '')})")
    positions = st.get("positions") or []
    pend = st.get("pending_orders") or []
    if positions:
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append(f"📌 Lệnh đang mở ({len(positions)}):")
        for p in positions:
            lines.append(f"  • {p.get('dir', '?').upper()} {p.get('tf', '?')}@{p.get('entry', 0):,.0f} "
                         f"(SL {p.get('sl', 0):,.0f}/TP {p.get('tp', 0):,.0f})")
    if pend:
        lines.append(f"⏳ Lệnh chờ ({len(pend)}):")
        for p in pend:
            lines.append(f"  • {p.get('type', '?').upper()}@{p.get('trigger', 0):,.0f}")
    lessons = (st.get("lessons") or [])[-4:]
    if lessons:
        lines.append("━━━━━━━━━━━━━━━━━")
        lines.append("🧠 Bài học gần nhất:")
        for l in lessons:
            lines.append(f"  {l}")
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append("👨‍💼 *Bạn là người ra quyết định cuối cùng.*")
    return "\n".join(lines)
