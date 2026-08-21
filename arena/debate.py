# -*- coding: utf-8 -*-
"""Hội đồng 4 chuyên gia tranh luận nhiều vòng + tính đồng thuận có trọng số."""
import time

from .config import AGENTS, TIMEFRAMES, agent_conf, any_api_key
from .indicators import clamp
from .llm import call_agent_json, mock_agent
from . import datasources


def market_snapshot(price, price_src, klines, ind, tf_key, context="", history="",
                    market_status=None, data_note=""):
    tf = TIMEFRAMES[tf_key]
    L = [f"Giá hiện tại: ${price:,.2f}/oz (nguồn: {price_src}).",
         f"Khung {tf['label']}: dự báo {tf['steps']} nến ≈ {tf['label']} tới."]
    if market_status:
        L.append("⚖️ TRẠNG THÁI THỊ TRƯỜNG: " + market_status["text"])
        if not market_status["open"]:
            L.append("Lưu ý: thị trường đang NGHỈ — thanh khoản thấp, giá hiện tại là giá đóng phiên cuối. "
                     "Khi mở cửa lại có thể có GAP (nhảy gap) do sự kiện cuối tuần. "
                     "Hãy phản ánh rủi ro gap này trong lập trường.")
        if market_status.get("next_close"):
            L.append(f"Đóng cửa phiên tiếp theo: {market_status['next_close']}.")
    if data_note:
        L.append(f"📡 CHẤT LƯỢNG DỮ LIỆU: {data_note}")
    if PRICE_INFO_line():
        L.append(PRICE_INFO_line())
    if history:
        L.append("LỊCH SỬ CÁC PHIÊN GẦN ĐÂY (hệ thống nhớ — tham khảo để phân tích):")
        L.append(history)
    if ind:
        L.append(f"Dữ liệu {ind['n']} nến gần nhất — RSI(14): {ind['rsi']:.1f} | EMA20: ${ind['ema20']:,.2f} | "
                 f"EMA50: ${ind['ema50']:,.2f} | MACD hist: {ind['macd_hist']:+.2f} | ATR: ${ind['atr']:,.2f} | Xu hướng EMA20/50: {ind['trend']}.")
        L.append(f"Hỗ trợ gần nhất: ${ind['sup']:,.2f} | Kháng cự gần nhất: ${ind['res']:,.2f} | "
                 f"Biến động thực tế: {ind['vol']*100:.2f}%/nến | Nến gần nhất: {ind['last_ret_pct']:+.2f}%.")
    if context:
        L.append(f"Bối cảnh bổ sung từ người điều hành: {context}")
    return "\n".join(L)


def PRICE_INFO_line():
    p = datasources.PRICE_INFO
    if p.get("futures") and p.get("spot") and abs(p["spot"] - p["futures"]) > 1:
        return (f"Lưu ý giá: spot XAUUSD ${p['spot']:,.2f} vs futures GC=F ${p['futures']:,.2f} "
                f"(chênh ${p['spot']-p['futures']:+,.2f} — futures thường lệch spot).")
    return ""


def build_prompt(agent, round_no, prev, snap):
    persona = agent["persona"]
    NO_FAB = ('\nTUYỆT ĐỐI chỉ dùng các con số, mức giá, vùng hỗ trợ/kháng cự CÓ TRONG dữ liệu được cung cấp ở trên. '
              'KHÔNG bịa ra mức giá, chỉ báo hay sự kiện không có trong dữ liệu.\n')
    if round_no == 1:
        return (f"{persona}\nĐọc kỹ dữ liệu thị trường sau và đưa ra LẬP TRƯỜNG BAN ĐẦU của bạn về giá vàng trong thời gian tới:\n"
                f"---\n{snap}\n---\n{NO_FAB}"
                'Trả về DUY NHẤT một JSON hợp lệ (không markdown, không giải thích thêm):\n'
                '{"sentiment_score": <số từ -1.0 (rất tiêu cực/bán mạnh) đến +1.0 (rất tích cực/mua mạnh)>, "confidence": <số 0.0-1.0>, "reasoning": "<luận điểm chính bằng tiếng Việt, 2-3 câu, nêu rõ con số/căn cứ>"}')
    if round_no == 2:
        others = "\n".join(f"• {x['title']} (tâm lý {x['stance']:+.2f}): {x.get('reasoning') or x.get('reason') or ''}"
                           for x in prev if x["key"] != agent["key"])
        mine = next((x.get("reasoning") or x.get("reason") or "" for x in prev if x["key"] == agent["key"]), "")
        return (f"{persona}\nLập trường ban đầu của bạn: {mine}\n"
                f"Đây là lập trường của các chuyên gia khác trong HỘI ĐỒNG:\n{others}\n"
                f"Nhiệm vụ PHẢN BIỆN: chỉ ra 1-2 lỗ hổng logic / luận điểm yếu / rủi ro bị bỏ sót quan trọng nhất trong các quan điểm trên (đặc biệt quan điểm đối lập với bạn), rồi CẬP NHẬT tâm lý của bạn sau khi nghe phản biện.\n{NO_FAB}"
                'Trả về DUY NHẤT một JSON hợp lệ:\n'
                '{"critique": "<phản biện ngắn gọn, sắc bén, tiếng Việt>", "revised_sentiment": <số -1.0 đến +1.0>, "confidence": <số 0.0-1.0>, "reasoning": "<lập trường sau phản biện, 2 câu>"}')
    myEntry = next((x for x in prev if x["key"] == agent["key"]), None)
    mine = (myEntry.get("reasoning") or myEntry.get("reason") or "") if myEntry else ""
    my_stance = myEntry.get("stance", 0) if myEntry else 0
    critiques = "\n".join(f"• Phản biện của {x['title']}: {x.get('critique') or '(không có)'}"
                          for x in prev if x["key"] != agent["key"])
    return (f"{persona}\nLập trường hiện tại của bạn: {mine} (tâm lý {my_stance:+.2f})\n"
            f"Các phản biện dành cho lập trường của bạn:\n{critiques}\n"
            f"Nhiệm vụ ĐIỀU CHỈNH CUỐI CÙNG: cân nhắc các phản biện (giữ vững nếu phản biện không thuyết phục, điều chỉnh nếu có cơ sở), đưa ra LẬP TRƯỜNG CUỐI CÙNG.\n{NO_FAB}"
            'Trả về DUY NHẤT một JSON hợp lệ:\n'
            '{"sentiment_score": <số -1.0 đến +1.0>, "confidence": <số 0.0-1.0>, "reasoning": "<lập trường cuối cùng, 2-3 câu, tiếng Việt>"}')


def run_debate(cfg, snap, rounds):
    timeline, prev = [], None
    live = any_api_key(cfg)
    for r in range(1, rounds + 1):
        entries = []
        for agent in AGENTS:
            conf = agent_conf(cfg, agent["key"])
            print(f"  🤖 [Vòng {r}] {agent['title']}... ({conf['provider']}/{conf['model']})")
            time.sleep(1.2)  # giãn cách giữa các agent — tránh rate limit
            if not live:
                print("     (chưa có API key — dữ liệu mẫu)")
                time.sleep(0.1)
                entries.append({**{"key": agent["key"], "title": agent["title"], "icon": agent["icon"]}, **mock_agent(agent, r)})
                continue
            try:
                out = call_agent_json(agent, r, prev, snap, cfg)
                j = out["json"]
                if r in (1, 3):
                    entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"],
                                    "stance": clamp(_gfloat(j, ["sentiment_score", "sentiment"]), -1, 1),
                                    "conf": clamp(_gfloat(j, ["confidence"], 0.7), 0, 1),
                                    "reason": str(j.get("reasoning", j.get("reason", "")))[:600],
                                    "critique": None, "model": out["model"], "fallback": False})
                else:
                    entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"],
                                    "stance": clamp(_gfloat(j, ["revised_sentiment", "sentiment_score", "sentiment"]), -1, 1),
                                    "conf": clamp(_gfloat(j, ["confidence"], 0.7), 0, 1),
                                    "reason": str(j.get("reasoning", ""))[:400] or "(đã cập nhật sau phản biện)",
                                    "critique": str(j.get("critique", ""))[:500],
                                    "model": out["model"], "fallback": False})
            except Exception as e:
                print(f"  ⚠️ {agent['title']} dùng dữ liệu dự phòng: {str(e)[:100]}")
                entries.append({**{"key": agent["key"], "title": agent["title"], "icon": agent["icon"]}, **mock_agent(agent, r)})
        ordered = [next(x for x in entries if x["key"] == a["key"]) for a in AGENTS]
        timeline.append({"round": r, "entries": ordered})
        prev = [{k: x.get(k) for k in ("key", "title", "stance", "conf", "reason", "critique")} for x in ordered]
    return timeline, prev


def _gfloat(obj, keys, default=0.0):
    import math
    for k in keys:
        v = obj.get(k)
        if isinstance(v, (int, float)) and math.isfinite(v):
            return float(v)
    return default


def compute_consensus(finals, tf_key):
    w = TIMEFRAMES[tf_key]["weights"]
    num = den = 0.0
    for f in finals:
        eff = w[f["key"]] * (0.5 + f["conf"] / 2)
        num += eff * f["stance"]
        den += eff
    c = num / den if den else 0
    verdict = ("MUA MẠNH 📈" if c >= 0.3 else "MUA NHẸ 📈" if c >= 0.1 else
               "TRUNG LẬP ↔️" if c > -0.1 else "BÁN NHẸ 📉" if c > -0.3 else "BÁN MẠNH 📉")
    return c, verdict
