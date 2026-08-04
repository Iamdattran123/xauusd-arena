#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XAU/USD AI DEBATE ARENA — Python Backend (MULTI-PROVIDER)
==========================================================
Mô phỏng đa tác nhân AI tranh luận + đám đông bỏ phiếu + Monte Carlo + AI Trader $1000
với ĐỊNH TUYẾN 3 NHÀ CUNG CẤP: Gemini (AI Studio) · Groq · OpenRouter.

API key lấy từ biến môi trường (GitHub Secrets):
    OPENROUTER_API_KEY   (sk-or-v1-...)
    GEMINI_API_KEY       (AIza...)
    GROQ_API_KEY         (gsk_...)

Cấu hình mặc định (hardcode, có thể ghi đè bằng config.json):
    Macro_Analyst      -> gemini     / gemini-2.5-flash
    Technical_Analyst  -> gemini     / gemini-2.5-flash
    Institutional_Whale-> openrouter / deepseek/deepseek-v4-flash-0731
    Retail_Crowd       -> groq       / llama-3.3-70b-versatile
    trader             -> openrouter / deepseek/deepseek-r1:free (tự fallback nếu không khả dụng)

Cách dùng:
    python app.py                          # chạy 1 phiên
    python app.py --watch 360              # tự chạy lại mỗi 6 giờ (24/7)
    python app.py --serve 8000             # web server xem dashboard
    python app.py --force-summary          # ép AI Trader tổng kết ngay
"""
import argparse
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
import urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    import numpy as np
except ImportError:
    sys.exit("Thiếu thư viện numpy — chạy: pip install numpy")

# =====================================================================
# CẤU HÌNH CHUNG
# =====================================================================
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

TIMEFRAMES = {
    "15m": {"label": "15 phút", "step_min": 15,  "yahoo_interval": "15m", "yahoo_range": "2d",  "binance": "15m", "vol": 0.0009, "steps": 16, "resample": 1, "weights": {"Macro_Analyst": 0.15, "Technical_Analyst": 0.45, "Institutional_Whale": 0.25, "Retail_Crowd": 0.15}},
    "1h":  {"label": "1 giờ",   "step_min": 60,  "yahoo_interval": "60m", "yahoo_range": "5d",  "binance": "1h",  "vol": 0.0018, "steps": 24, "resample": 1, "weights": {"Macro_Analyst": 0.25, "Technical_Analyst": 0.35, "Institutional_Whale": 0.25, "Retail_Crowd": 0.15}},
    "4h":  {"label": "4 giờ",   "step_min": 240, "yahoo_interval": "60m", "yahoo_range": "12d", "binance": "4h",  "vol": 0.0035, "steps": 18, "resample": 4, "weights": {"Macro_Analyst": 0.35, "Technical_Analyst": 0.25, "Institutional_Whale": 0.30, "Retail_Crowd": 0.10}},
    "1D":  {"label": "1 ngày",  "step_min": 1440,"yahoo_interval": "1d",  "yahoo_range": "6mo", "binance": "1d",  "vol": 0.0075, "steps": 30, "resample": 1, "weights": {"Macro_Analyst": 0.40, "Technical_Analyst": 0.15, "Institutional_Whale": 0.35, "Retail_Crowd": 0.10}},
}

AGENTS = [
    {"key": "Macro_Analyst", "title": "Chuyên gia Vĩ mô", "icon": "🏛️",
     "persona": "Bạn là chuyên gia kinh tế vĩ mô cấp cao tại một quỹ đầu tư toàn cầu. Trọng tâm: chính sách lãi suất Fed, chỉ số DXY, lạm phát CPI/PCE, lợi suất trái phiếu Mỹ, rủi ro địa chính trị và vai trò trú ẩn của vàng."},
    {"key": "Technical_Analyst", "title": "Chuyên gia Kỹ thuật", "icon": "📐",
     "persona": "Bạn là chuyên gia phân tích kỹ thuật với 15 năm kinh nghiệm giao dịch vàng. Trọng tâm: RSI, MACD, EMA, ATR, vùng hỗ trợ/kháng cự, mô hình nến và cấu trúc xu hướng."},
    {"key": "Institutional_Whale", "title": "Quỹ & Ngân hàng TW", "icon": "🐋",
     "persona": "Bạn đại diện cho ngân hàng trung ương và quỹ ETF vàng lớn (SPDR Gold, iShares). Trọng tâm: dòng tiền tổ chức, nhu cầu tích trữ, giao dịch OTC, thanh khoản và chi phí cơ hội."},
    {"key": "Retail_Crowd", "title": "Đám đông Nhỏ lẻ", "icon": "👥",
     "persona": "Bạn đại diện cho tâm lý nhà đầu tư cá nhân trên mạng xã hội. Trọng tâm: FOMO, bắt đáy, chốt lời, đòn bẩy, dòng tiền nhỏ lẻ và tâm lý theo đám đông."},
]

# ---------------------------------------------------------------------
# CẤU HÌNH MẶC ĐỊNH (hardcode theo yêu cầu)
# Mỗi tác nhân: {"provider": "gemini|groq|openrouter", "model": "..."}
# ---------------------------------------------------------------------
DEFAULT_AGENT_CONFIG = {
    "Macro_Analyst":        {"provider": "openrouter", "model": "qwen/qwen3-32b"},
    "Technical_Analyst":    {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash-0731"},
    "Institutional_Whale":  {"provider": "cohere",     "model": "command-r-plus-08-2024"},
    "Retail_Crowd":         {"provider": "groq",       "model": "llama-3.3-70b-versatile"},
}

DEFAULT_TRADER_CONFIG = {
    "provider": "openrouter",
    "model": "deepseek/deepseek-v4-flash-0731",  # (deepseek-r1:free đã bị OpenRouter gỡ -> dùng v4-flash rẻ & còn hiệu lực)
    "risk_pct": 1.0,
    "summary_every": 300,
    "telegram_token": "",
    "telegram_chat_id": "",
}

# ---------------------------------------------------------------------
# NHÀ CUNG CẤP — endpoint + tên biến môi trường lấy key (GitHub Secrets)
# ---------------------------------------------------------------------
PROVIDER_META = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "env": "OPENROUTER_API_KEY",
        "style": "openai",          # chuẩn OpenAI chat/completions
        "extra_headers": {"HTTP-Referer": "https://localhost", "X-Title": "XAUUSD AI Debate Arena"},
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "env": "GROQ_API_KEY",
        "style": "openai",
        "extra_headers": {},
    },
    "gemini": {
        # Chuẩn gốc của Google: POST /v1beta/models/{model}:generateContent?key=...
        "base": "https://generativelanguage.googleapis.com/v1beta/models",
        "env": "GEMINI_API_KEY",
        "style": "google",
        "extra_headers": {},
    },
    "cohere": {
        # Cohere API v2: POST /v2/chat (native)
        "url": "https://api.cohere.com/v2/chat",
        "env": "COHERE_API_KEY",
        "style": "cohere",
        "extra_headers": {"X-Client-Name": "xauusd-arena"},
    },
}

# Chuỗi model dự phòng TỪNG NHÀ CUNG CẤP — khi model chính lỗi/429
FALLBACK_CHAIN = {
    "gemini":     [("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash-lite")],
    "groq":       [("groq", "llama-3.3-70b-versatile"), ("groq", "llama-3.1-8b-instant")],
    "cohere":     [("cohere", "command-r-plus-08-2024"), ("cohere", "command-r-08-2024")],
    "openrouter": [("openrouter", "qwen/qwen3-32b"),
                   ("openrouter", "deepseek/deepseek-v4-flash-0731"),
                   ("openrouter", "google/gemma-4-31b-it:free")],
}

MOCK = {
    "Macro_Analyst": {
        1: (0.55, 0.80, "DXY suy yếu và kỳ vọng Fed hạ lãi suất từ quý IV củng cố vai trò trú ẩn của vàng; rủi ro địa chính trị duy trì dòng mua phòng thủ của các NHTW."),
        2: ("Quan điểm kỹ thuật quá nhấn mạnh RSI ngắn hạn — vàng trong chu kỳ vĩ mô nới lỏng thường vượt vùng quá mua trong thời gian dài; bỏ qua sức ép DXY.", 0.52, 0.82, "Sau khi cân nhắc phản biện: vẫn duy trì xu hướng tăng vĩ mô nhưng thừa nhận khả năng điều chỉnh kỹ thuật ngắn hạn trước vùng kháng cự; hạ nhẹ mức độ tích cực."),
        3: (0.50, 0.82, "Sau khi cân nhắc phản biện: vẫn duy trì xu hướng tăng vĩ mô nhưng thừa nhận khả năng điều chỉnh kỹ thuật ngắn hạn trước vùng kháng cự; hạ nhẹ mức độ tích cực."),
    },
    "Technical_Analyst": {
        1: (-0.20, 0.75, "RSI tiệm cận vùng quá mua 68-70, MACD có dấu hiệu phân kỳ âm nhẹ; giá chạm vùng kháng cự nhiều lần chưa phá — xác suất điều chỉnh ngắn hạn cao."),
        2: ("Lập luận vĩ mô đúng hướng nhưng chưa định lượng: nếu Fed hạ lãi suất, mô hình nến tăng vẫn chiếm ưu thế; kháng cự có thể bị phá trong 1-2 phiên.", -0.10, 0.72, "Giữ quan điểm thận trọng ngắn hạn: chờ xác nhận phá kháng cự đi kèm khối lượng trước khi đuổi mua; nâng nhẹ điểm do lực vĩ mô hỗ trợ."),
        3: (-0.10, 0.72, "Giữ quan điểm thận trọng ngắn hạn: chờ xác nhận phá kháng cự đi kèm khối lượng trước khi đuổi mua; nâng nhẹ điểm do lực vĩ mô hỗ trợ."),
    },
    "Institutional_Whale": {
        1: (0.40, 0.90, "Lượng vàng do các NHTW nắm giữ tăng tháng thứ 9 liên tiếp; dòng vốn ETF vàng toàn cầu chuyển sang ròng dương, tạo lớp hỗ trợ mua tại vùng giá hiện tại."),
        2: ("Phản biện kỹ thuật chỉ đúng cho nhà giao dịch lướt sóng; dòng tiền tổ chức quan tâm tích trữ 6-12 tháng, đợt điều chỉnh 1-2% không thay đổi chiến lược.", 0.45, 0.88, "Giữ lập trường tích cực: đợt điều chỉnh kỹ thuật nếu xảy ra sẽ là cơ hội gia tăng vị thế của dòng tiền lớn tại vùng hỗ trợ EMA50."),
        3: (0.45, 0.88, "Giữ lập trường tích cực: đợt điều chỉnh kỹ thuật nếu xảy ra sẽ là cơ hội gia tăng vị thế của dòng tiền lớn tại vùng hỗ trợ EMA50."),
    },
    "Retail_Crowd": {
        1: (0.10, 0.60, "Tâm lý mạng xã hội phân hoá: một bộ phận FOMO đuổi mua phá đỉnh, phần còn lại chờ điều chỉnh để bắt đáy; chưa có dòng tiền nhỏ lẻ áp đảo."),
        2: ("Quan điểm quỹ bỏ qua rủi ro thanh khoản: nếu thanh khoản toàn cầu thắt chặt bất ngờ, dòng tiền tổ chức cũng có thể đảo chiều nhanh; nhỏ lẻ thường bị cuốn theo.", 0.05, 0.58, "Sau phản biện, giữ tâm lý trung lập nhẹ: phần lớn nhỏ lẻ đang đứng ngoài quan sát, chỉ tham gia khi có tín hiệu phá đỉnh rõ ràng kèm khối lượng."),
        3: (0.05, 0.58, "Sau phản biện, giữ tâm lý trung lập nhẹ: phần lớn nhỏ lẻ đang đứng ngoài quan sát, chỉ tham gia khi có tín hiệu phá đỉnh rõ ràng kèm khối lượng."),
    },
}


def load_config():
    """Đọc cấu hình: key từ biến môi trường (GitHub Secrets) + ghi đè bằng config.json (tùy chọn)."""
    cfg = {
        # API keys — ưu tiên biến môi trường, config.json có thể ghi đè
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "gemini_api_key":     os.getenv("GEMINI_API_KEY", ""),
        "groq_api_key":       os.getenv("GROQ_API_KEY", ""),
        "cohere_api_key":     os.getenv("COHERE_API_KEY", ""),
        # models: {agent_key: {"provider": ..., "model": ...}}
        "models": {k: dict(v) for k, v in DEFAULT_AGENT_CONFIG.items()},
        "trader": dict(DEFAULT_TRADER_CONFIG),
    }
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                user = json.load(f)
            for k in ("openrouter_api_key", "gemini_api_key", "groq_api_key", "cohere_api_key"):
                if user.get(k):
                    cfg[k] = user[k]
            # models: hỗ trợ cả dạng mới {"provider","model"} lẫn dạng cũ (string -> openrouter)
            if isinstance(user.get("models"), dict):
                for key, val in user["models"].items():
                    if isinstance(val, str):
                        cfg["models"][key] = {"provider": "openrouter", "model": val}
                    elif isinstance(val, dict) and val.get("model"):
                        cfg["models"][key] = {"provider": val.get("provider", "openrouter"), "model": val["model"]}
            if isinstance(user.get("trader"), dict):
                cfg["trader"].update(user["trader"])
        except Exception as e:
            print(f"⚠️ Lỗi đọc config.json: {e}")
    return cfg


def agent_conf(cfg, key):
    """Trả về {'provider','model'} cho 1 tác nhân (hỗ trợ cả config cũ dạng string)."""
    c = cfg["models"].get(key, {})
    if isinstance(c, str):
        return {"provider": "openrouter", "model": c}
    return {"provider": c.get("provider", "openrouter"), "model": c.get("model", "")}


# =====================================================================
# DỮ LIỆU REALTIME
# =====================================================================
def http_json(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_price():
    """Giá vàng realtime: Yahoo GC=F → gold-api.com → Binance PAXG."""
    try:
        d = http_json("https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=1d", timeout=6)
        p = float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
        return p, "Yahoo Finance · GC=F (COMEX)"
    except Exception:
        pass
    try:
        d = http_json("https://api.gold-api.com/price/XAU", timeout=6)
        return float(d["price"]), "gold-api.com · XAU spot"
    except Exception:
        pass
    try:
        d = http_json("https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT", timeout=6)
        return float(d["price"]), "Binance · PAXG"
    except Exception:
        return 4050.0, "Giá mặc định (offline)"


def fetch_klines(tf_key):
    """Nến lịch sử: Yahoo (resample 4h nếu cần) → Binance PAXG."""
    cfg = TIMEFRAMES[tf_key]
    try:
        d = http_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval={cfg['yahoo_interval']}&range={cfg['yahoo_range']}",
            timeout=10)
        r = d["chart"]["result"][0]
        ts, q = r["timestamp"], r["indicators"]["quote"][0]
        raw = [{"t": t * 1000, "o": o, "h": h, "l": lo, "c": c}
               for t, o, h, lo, c in zip(ts, q["open"], q["high"], q["low"], q["close"])
               if o is not None and c is not None]
        if cfg["resample"] > 1:
            raw = resample(raw, cfg["step_min"] * 60000)
        raw = raw[:-1]
        if len(raw) > 10:
            return raw, f"Yahoo · GC=F {cfg['label']}"
    except Exception:
        pass
    try:
        d = http_json(f"https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval={cfg['binance']}&limit=260", timeout=8)
        raw = [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4])} for k in d]
        if len(raw) > 10:
            return raw, f"Binance · PAXGUSDT {cfg['label']}"
    except Exception:
        pass
    print("⚠️ Không lấy được nến lịch sử — sinh dữ liệu mẫu.")
    return mock_klines(4050.0, cfg["step_min"], 120, cfg["vol"]), "Dữ liệu mẫu (offline)"


def resample(klines, step_ms):
    out = []
    for k in klines:
        bucket = math.floor(k["t"] / step_ms) * step_ms
        if out and out[-1]["t"] == bucket:
            out[-1]["h"] = max(out[-1]["h"], k["h"])
            out[-1]["l"] = min(out[-1]["l"], k["l"])
            out[-1]["c"] = k["c"]
        else:
            out.append({"t": bucket, "o": k["o"], "h": k["h"], "l": k["l"], "c": k["c"]})
    return out


def mock_klines(price, step_min, n, vol):
    out, p = [], price * 0.98
    step = step_min * 60000
    t0 = math.floor(time.time() * 1000 / step) * step - (n - 1) * step
    for i in range(n):
        r = random.gauss(0, vol)
        o, c = p, p * (1 + r)
        h = max(o, c) * (1 + abs(random.gauss(0, vol * 0.5)))
        l = min(o, c) * (1 - abs(random.gauss(0, vol * 0.5)))
        out.append({"t": t0 + i * step, "o": o, "h": h, "l": l, "c": c})
        p = c
    return out


# =====================================================================
# CHỈ BÁO KỸ THUẬT
# =====================================================================
def calc_ema(c, p):
    k, out, e = 2 / (p + 1), [], c[0]
    for i, x in enumerate(c):
        e = x if i == 0 else x * k + e * (1 - k)
        out.append(e)
    return out


def calc_rsi(c, p=14):
    g = l = 0.0
    out = [50.0]
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        if i <= p:
            g += max(d, 0) / p
            l += max(-d, 0) / p
            if i == p:
                out.append(100 - 100 / (1 + g / (l if l else 1e-9)))
        else:
            g = (g * (p - 1) + max(d, 0)) / p
            l = (l * (p - 1) + max(-d, 0)) / p
            out.append(100 - 100 / (1 + g / (l if l else 1e-9)))
    return out


def calc_macd(c):
    e12, e26 = calc_ema(c, 12), calc_ema(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = calc_ema(macd, 9)
    return macd, sig, [m - s for m, s in zip(macd, sig)]


def calc_atr(k, p=14):
    trs = []
    for i in range(1, len(k)):
        trs.append(max(k[i]["h"] - k[i]["l"], abs(k[i]["h"] - k[i - 1]["c"]), abs(k[i]["l"] - k[i - 1]["c"])))
    s = trs[-p:]
    return sum(s) / len(s)


def compute_indicators(klines, tf_key):
    closes = [k["c"] for k in klines]
    n = len(closes)
    rsi = calc_rsi(closes)
    macd, sig, hist = calc_macd(closes)
    ema20, ema50 = calc_ema(closes, 20)[-1], calc_ema(closes, 50)[-1]
    atr = calc_atr(klines)
    res = max(k["h"] for k in klines[-30:])
    sup = min(k["l"] for k in klines[-30:])
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, n - 48), n)]
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0
    cpd = 1440 / TIMEFRAMES[tf_key]["step_min"]
    vol = max(0.0003, min(0.05, sd * math.sqrt(max(1, cpd))))
    last_ret = closes[-1] / closes[-2] - 1 if n > 1 else 0
    momentum = max(-1, min(1, last_ret * 50))
    return {
        "rsi": rsi[-1], "ema20": ema20, "ema50": ema50, "macd_hist": hist[-1], "atr": atr,
        "res": res, "sup": sup, "vol": vol, "momentum": momentum,
        "last_ret_pct": last_ret * 100, "trend": "TĂNG" if ema20 > ema50 else "GIẢM", "n": n,
    }


# =====================================================================
# TÁC NHÂN AI — ĐA NHÀ CUNG CẤP (Gemini · Groq · OpenRouter)
# =====================================================================
def market_snapshot(cfg, price, price_src, klines, ind, tf_key, context=""):
    tf = TIMEFRAMES[tf_key]
    L = [f"Giá hiện tại: ${price:,.2f}/oz (nguồn: {price_src}).",
         f"Khung {tf['label']}: dự báo {tf['steps']} nến ≈ {tf['label']} tới."]
    if ind:
        L.append(f"Dữ liệu {ind['n']} nến gần nhất — RSI(14): {ind['rsi']:.1f} | EMA20: ${ind['ema20']:,.2f} | "
                 f"EMA50: ${ind['ema50']:,.2f} | MACD hist: {ind['macd_hist']:+.2f} | ATR: ${ind['atr']:,.2f} | Xu hướng EMA20/50: {ind['trend']}.")
        L.append(f"Hỗ trợ gần nhất: ${ind['sup']:,.2f} | Kháng cự gần nhất: ${ind['res']:,.2f} | "
                 f"Biến động thực tế: {ind['vol']*100:.2f}%/nến | Nến gần nhất: {ind['last_ret_pct']:+.2f}%.")
    if context:
        L.append(f"Bối cảnh bổ sung từ người điều hành: {context}")
    return "\n".join(L)


def build_prompt(agent, round_no, prev, snap):
    persona = agent["persona"]
    NO_FAB = '\nTUYỆT ĐỐI chỉ dùng các con số, mức giá, vùng hỗ trợ/kháng cự CÓ TRONG dữ liệu được cung cấp ở trên. KHÔNG bịa ra mức giá, chỉ báo hay sự kiện không có trong dữ liệu.\n'
    if round_no == 1:
        return f"""{persona}
Đọc kỹ dữ liệu thị trường sau và đưa ra LẬP TRƯỜNG BAN ĐẦU của bạn về giá vàng trong thời gian tới:
---
{snap}
---
{NO_FAB}
Trả về DUY NHẤT một JSON hợp lệ (không markdown, không giải thích thêm):
{{"sentiment_score": <số từ -1.0 (rất tiêu cực/bán mạnh) đến +1.0 (rất tích cực/mua mạnh)>, "confidence": <số 0.0-1.0>, "reasoning": "<luận điểm chính bằng tiếng Việt, 2-3 câu, nêu rõ con số/căn cứ>"}}"""
    if round_no == 2:
        others = "\n".join(f"• {x['title']} (tâm lý {x['stance']:+.2f}): {x['reasoning']}"
                           for x in prev if x["key"] != agent["key"])
        mine = next((x["reasoning"] for x in prev if x["key"] == agent["key"]), "")
        return f"""{persona}
Lập trường ban đầu của bạn: {mine}
Đây là lập trường của các chuyên gia khác trong HỘI ĐỒNG:
{others}
Nhiệm vụ PHẢN BIỆN: chỉ ra 1-2 lỗ hổng logic / luận điểm yếu / rủi ro bị bỏ sót quan trọng nhất trong các quan điểm trên (đặc biệt quan điểm đối lập với bạn), rồi CẬP NHẬT tâm lý của bạn sau khi nghe phản biện.
{NO_FAB}
Trả về DUY NHẤT một JSON hợp lệ:
{{"critique": "<phản biện ngắn gọn, sắc bén, tiếng Việt>", "revised_sentiment": <số -1.0 đến +1.0>, "confidence": <số 0.0-1.0>, "reasoning": "<lập trường sau phản biện, 2 câu>"}}"""
    myEntry = next((x for x in prev if x["key"] == agent["key"]), None)
    mine = myEntry.get("reasoning", "") if myEntry else ""
    my_stance = myEntry.get("stance", 0) if myEntry else 0
    critiques = "\n".join(f"• Phản biện của {x['title']}: {x.get('critique') or '(không có)'}"
                          for x in prev if x["key"] != agent["key"])
    return f"""{persona}
Lập trường hiện tại của bạn: {mine} (tâm lý {my_stance:+.2f})
Các phản biện dành cho lập trường của bạn:
{critiques}
Nhiệm vụ ĐIỀU CHỈNH CUỐI CÙNG: cân nhắc các phản biện (giữ vững nếu phản biện không thuyết phục, điều chỉnh nếu có cơ sở), đưa ra LẬP TRƯỜNG CUỐI CÙNG.
{NO_FAB}
Trả về DUY NHẤT một JSON hợp lệ:
{{"sentiment_score": <số -1.0 đến +1.0>, "confidence": <số 0.0-1.0>, "reasoning": "<lập trường cuối cùng, 2-3 câu, tiếng Việt>"}}"""


def extract_json(text):
    """Trích xuất JSON từ text của LLM (bỏ <think>, markdown, văn bản thừa)."""
    s = str(text or "")
    for tag in ("<think>", "</think>"):
        s = s.replace(tag, "")
    s = s.replace("```json", "").replace("```", "")
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return o
    except Exception:
        pass
    for i in range(len(s)):
        if s[i] != "{":
            continue
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(s[i:j + 1])
                        if isinstance(o, dict):
                            return o
                    except Exception:
                        pass
                    break
    return None


def gfloat(obj, keys, default=0.0):
    for k in keys:
        v = obj.get(k)
        if isinstance(v, (int, float)) and math.isfinite(v):
            return float(v)
    return default


# =====================================================================
# 💬 CALL_LLM — ĐỊNH TUYẾN NHÀ CUNG CẤP (yêu cầu chính)
#   call_llm(role, prompt, cfg)
#     role = "Macro_Analyst" | "Technical_Analyst" | "Institutional_Whale"
#            | "Retail_Crowd" | "trader"
#   -> tự tra provider+model của role trong cfg -> gửi đúng endpoint
#   -> TRẢ VỀ CẤU TRÚC THỐNG NHẤT: {"text", "prompt_tokens", "completion_tokens", "model", "provider"}
# =====================================================================
def call_llm(role, prompt, cfg, provider=None, model=None):
    if role == "trader":
        conf = cfg["trader"]
        if isinstance(conf, str):
            conf = {"provider": "openrouter", "model": conf}
        provider = provider or conf.get("provider", "openrouter")
        model = model or conf.get("model", "")
    else:
        conf = agent_conf(cfg, role)
        provider = provider or conf["provider"]
        model = model or conf["model"]

    meta = PROVIDER_META.get(provider)
    if not meta:
        raise ValueError(f"Nhà cung cấp không hợp lệ: {provider}")
    key = cfg.get(meta["env"].lower()) or os.getenv(meta["env"], "")
    if not key:
        raise ValueError(f"Thiếu API key {meta['env']} cho nhà cung cấp {provider} (đặt biến môi trường/GitHub Secret)")

    if meta["style"] == "google":
        # ---------- GEMINI — chuẩn gốc Google generateContent ----------
        url = f"{meta['base']}/{model}:generateContent?key={key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0.4, "maxOutputTokens": 700}}
        headers = {"Content-Type": "application/json", **meta["extra_headers"]}
        data = _http_post(url, payload, headers, timeout=90)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"Gemini trả lỗi: {json.dumps(data, ensure_ascii=False)[:200]}")
        usage = data.get("usageMetadata", {})
        ptok = int(usage.get("promptTokenCount", 0) or 0)
        ctok = int(usage.get("candidatesTokenCount", 0) or 0)
    elif meta["style"] == "cohere":
        # ---------- COHERE — API v2 chat (native) ----------
        url = meta["url"]
        payload = {"model": model,
                   "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                   "temperature": 0.4, "max_tokens": 700}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key,
                   **meta["extra_headers"]}
        data = _http_post(url, payload, headers, timeout=90)
        try:
            parts = data["message"]["content"]
            text = "".join(c.get("text", "") for c in parts if isinstance(c, dict))
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"Cohere trả lỗi: {json.dumps(data, ensure_ascii=False)[:200]}")
        usage = data.get("usage", {})
        tk = usage.get("tokens", {}) or {}
        billed = usage.get("billed_units", {}) or {}
        ptok = int(tk.get("input_tokens", 0) or billed.get("input_tokens", 0) or 0)
        ctok = int(tk.get("output_tokens", 0) or billed.get("output_tokens", 0) or 0)
    else:
        # ---------- GROQ / OPENROUTER — chuẩn OpenAI chat/completions ----------
        url = meta["url"]
        payload = {"model": model,
                   "messages": [{"role": "user", "content": prompt}],
                   "temperature": 0.4, "max_tokens": 700}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key,
                   **meta["extra_headers"]}
        data = _http_post(url, payload, headers, timeout=90)
        try:
            msg = data["choices"][0]["message"]
            text = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"{provider} trả lỗi: {json.dumps(data, ensure_ascii=False)[:200]}")
        usage = data.get("usage", {})
        ptok = int(usage.get("prompt_tokens", 0) or 0)
        ctok = int(usage.get("completion_tokens", 0) or 0)

    if not text:
        raise ValueError(f"{provider} trả về nội dung rỗng")
    return {"text": text, "prompt_tokens": ptok, "completion_tokens": ctok,
            "model": model, "provider": provider}


def _http_post(url, payload, headers, timeout=90):
    """POST JSON, trả dict; ném lỗi kèm status + retry-after khi HTTP lỗi."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", errors="ignore"))
        except Exception:
            body = {}
        retry_after = e.headers.get("Retry-After", "0")
        err = RuntimeError(f"HTTP {e.code}: {json.dumps(body, ensure_ascii=False)[:200]}")
        err.status = e.code
        err.retry_after = int(retry_after) if retry_after and retry_after.isdigit() else 0
        raise err


def call_agent_json(agent, round_no, prev, snap, cfg):
    """Gọi LLM cho 1 tác nhân theo chuỗi dự phòng đa nhà cung cấp, trả về dict đã parse."""
    role = agent["key"]
    chosen = agent_conf(cfg, role)
    chain = [(chosen["provider"], chosen["model"])]
    for p, m in FALLBACK_CHAIN.get(chosen["provider"], []):
        if (p, m) not in chain:
            chain.append((p, m))
    prompt = build_prompt(agent, round_no, prev, snap)
    last_err = ""
    for ci, (provider, model) in enumerate(chain):
        try:
            res = call_llm(role, prompt, cfg, provider=provider, model=model)
            j = extract_json(res["text"])
            if not j:
                last_err = "JSON không hợp lệ"
                if ci == 0:
                    print(f"  🔁 {agent['title']} ({provider}/{model}) trả lời sai định dạng — thử lại...")
                continue
            # chuẩn hóa sentiment/confidence/reasoning
            if round_no in (1, 3):
                j.setdefault("confidence", 0.7)
            else:
                j.setdefault("confidence", 0.7)
                if not j.get("revised_sentiment") and j.get("sentiment_score") is not None:
                    j["revised_sentiment"] = j["sentiment_score"]
            return {"json": j, "model": f"{provider}/{model}", "provider": provider,
                    "prompt_tokens": res["prompt_tokens"], "completion_tokens": res["completion_tokens"]}
        except Exception as e:
            last_err = str(e)
            if ci < len(chain) - 1:
                print(f"  🔄 {agent['title']} ({provider}/{model}) lỗi: {last_err[:100]} → thử {chain[ci+1][0]}/{chain[ci+1][1]}...")
            else:
                print(f"  ⚠️ {agent['title']} hết chuỗi dự phòng — dùng dữ liệu mẫu. ({last_err[:100]})")
    raise RuntimeError(last_err or "không gọi được LLM")


def mock_agent(agent, round_no):
    m = MOCK[agent["key"]][round_no]
    if round_no == 1:
        return {"stance": m[0], "conf": m[1], "reason": m[2], "critique": None, "model": "(mẫu)", "fallback": True}
    if round_no == 2:
        return {"critique": m[0], "stance": m[1], "conf": m[2], "reason": m[3], "model": "(mẫu)", "fallback": True}
    return {"stance": m[0], "conf": m[1], "reason": m[2], "critique": None, "model": "(mẫu)", "fallback": True}


def run_debate(cfg, snap, rounds):
    timeline, prev = [], None
    any_key = cfg["openrouter_api_key"] or cfg["gemini_api_key"] or cfg["groq_api_key"]
    for r in range(1, rounds + 1):
        entries = []
        for agent in AGENTS:
            conf = agent_conf(cfg, agent["key"])
            disp = f"{conf['provider']}/{conf['model']}"
            print(f"  🤖 [Vòng {r}] {agent['title']}... ({disp})")
            if not any_key:
                print("     (chưa có API key — dữ liệu mẫu)")
                time.sleep(0.1)
                entries.append({**{"key": agent["key"], "title": agent["title"], "icon": agent["icon"]}, **mock_agent(agent, r)})
                continue
            try:
                out = call_agent_json(agent, r, prev, snap, cfg)
                j = out["json"]
                if r in (1, 3):
                    stance = max(-1.0, min(1.0, gfloat(j, ["sentiment_score", "sentiment"])))
                    conf = max(0.0, min(1.0, gfloat(j, ["confidence"], 0.7)))
                    reason = str(j.get("reasoning", j.get("reason", "")))[:600]
                    entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"],
                                    "stance": stance, "conf": conf, "reason": reason, "critique": None,
                                    "model": out["model"], "fallback": False})
                else:
                    critique = str(j.get("critique", ""))[:500]
                    stance = max(-1.0, min(1.0, gfloat(j, ["revised_sentiment", "sentiment_score", "sentiment"])))
                    conf = max(0.0, min(1.0, gfloat(j, ["confidence"], 0.7)))
                    reason = str(j.get("reasoning", ""))[:400] or "(đã cập nhật sau phản biện)"
                    entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"],
                                    "stance": stance, "conf": conf, "reason": reason, "critique": critique,
                                    "model": out["model"], "fallback": False})
            except Exception as e:
                print(f"  ⚠️ {agent['title']} dùng dữ liệu dự phòng: {str(e)[:100]}")
                entries.append({**{"key": agent["key"], "title": agent["title"], "icon": agent["icon"]}, **mock_agent(agent, r)})
        ordered = [next(x for x in entries if x["key"] == a["key"]) for a in AGENTS]
        timeline.append({"round": r, "entries": ordered})
        prev = [{k: x.get(k) for k in ("key", "title", "stance", "conf", "reason", "critique")} for x in ordered]
    return timeline, prev


# =====================================================================
# ĐỒNG THUẬN + ĐÁM ĐÔNG + MONTE CARLO
# =====================================================================
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


def simulate_crowd(finals, n_voters, momentum, seed=None):
    rng = np.random.default_rng(seed)
    mb = max(-0.4, min(0.4, momentum * 0.35 + rng.normal(0, 0.05)))
    stances = np.array([f["stance"] for f in finals])
    confs = np.array([f["conf"] for f in finals])
    biases = np.clip(mb + rng.normal(0, 0.35, n_voters), -1, 1)
    pers = confs[None, :] * (1 - 0.85 * np.abs(stances[None, :] - biases[:, None])) + rng.normal(0, 0.12, (n_voters, len(finals)))
    pers = np.clip(pers, 0, 1.5)
    total = pers.sum(axis=1)
    pos = np.where(total > 0, (pers * stances[None, :]).sum(axis=1) / total, 0)
    bull = int((pos > 0.12).sum())
    bear = int((pos < -0.12).sum())
    neu = n_voters - bull - bear
    crowd_mean = float(pos.mean())
    winners = pers.argmax(axis=1)
    votes = {f["key"]: int((winners == i).sum()) for i, f in enumerate(finals)}
    return {"n_voters": n_voters, "bull": bull, "neu": neu, "bear": bear,
            "crowd_mean": crowd_mean, "votes": votes, "momentum": mb}


def run_monte_carlo(start, steps, vol, consensus, momentum, paths, seed=None):
    rng = np.random.default_rng(seed)
    drift = consensus * vol * 0.45 + momentum * vol * 0.10
    W = rng.normal(0, 1, (paths, steps))
    increments = (drift - 0.5 * vol * vol) + vol * W
    log_paths = np.zeros((paths, steps + 1))
    log_paths[:, 1:] = math.log(start) + np.cumsum(increments, axis=1)
    paths_mat = np.exp(log_paths)
    mean = paths_mat.mean(axis=0)
    p10 = np.percentile(paths_mat, 10, axis=0)
    p50 = np.percentile(paths_mat, 50, axis=0)
    p90 = np.percentile(paths_mat, 90, axis=0)
    prob_up = float((paths_mat[:, -1] > start).mean())
    return {"target": float(mean[-1]), "p10": float(p10[-1]), "p50": float(p50[-1]), "p90": float(p90[-1]),
            "prob_up": prob_up, "drift": drift,
            "rows": [{"mean": float(mean[i]), "p10": float(p10[i]), "p50": float(p50[i]), "p90": float(p90[i])}
                     for i in range(steps + 1)]}


# =====================================================================
# 📈 BACKTEST
# =====================================================================
def run_backtest(klines, tf_key):
    closes = [k["c"] for k in klines]
    n = len(closes)
    cfg = TIMEFRAMES[tf_key]
    steps = cfg["steps"]
    min_n = 40
    if n < min_n + steps:
        return None
    equity = 1.0
    trades = wins = 0
    gross_win = gross_loss = 0.0
    peak = 1.0
    max_dd = 0.0
    for i in range(min_n, n - steps):
        sl = closes[:i + 1]
        ks = klines[:i + 1]
        rsi = calc_rsi(sl)[-1]
        e20, e50 = calc_ema(sl, 20)[-1], calc_ema(sl, 50)[-1]
        _, _, hist = calc_macd(sl)
        hist = hist[-1]
        atr = calc_atr(ks)
        last_ret = sl[-1] / sl[-2] - 1 if len(sl) > 1 else 0
        score = 0.30 if e20 > e50 else -0.30
        score += max(-0.2, min(0.2, (50 - rsi) / 30 * 0.2))
        score += max(-0.25, min(0.25, hist / (atr if atr else 1e-9) * 0.25))
        score += max(-0.15, min(0.15, last_ret * 20))
        if abs(score) < 0.15:
            continue
        d = 1 if score > 0 else -1
        ret = (closes[i + steps] / closes[i] - 1) * d
        trades += 1
        if ret > 0:
            wins += 1
            gross_win += ret
        else:
            gross_loss += -ret
        equity *= (1 + ret)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    if trades == 0:
        return None
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "tf": tf_key,
        "trades": trades, "win_rate": round(wins / trades * 100, 1),
        "total_return_pct": round((equity - 1) * 100, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "points": (n - min_n - steps) // steps + 1,
    }


def save_backtest_history(entry, out_dir):
    p = os.path.join(out_dir, "backtest_history.json")
    hist = []
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    hist.append(entry)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(hist[-500:], f, ensure_ascii=False, indent=2)


# =====================================================================
# XUẤT KẾT QUẢ
# =====================================================================
def build_result(cfg, args, price, price_src, klines, kline_src, ind, timeline, finals,
                 consensus, verdict, crowd, mc):
    tf = TIMEFRAMES[args.timeframe]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timeframe": args.timeframe, "timeframe_label": tf["label"],
        "horizon": f"{tf['steps']} nến", "rounds": args.rounds,
        "price": price, "price_source": price_src,
        "kline_source": kline_src, "n_candles": len(klines),
        "indicators": ind,
        "consensus": consensus, "verdict": verdict,
        "target": mc["target"], "p10": mc["p10"], "p50": mc["p50"], "p90": mc["p90"],
        "prob_up": mc["prob_up"], "drift": mc["drift"], "n_paths": args.paths,
        "crowd": crowd,
        "agents": [{
            "key": f["key"], "title": f["title"], "icon": f["icon"],
            "stance": f["stance"], "conf": f["conf"], "reasoning": f["reason"],
            "model": f["model"], "fallback": f.get("fallback", False),
        } for f in finals],
        "timeline": timeline,
        "monte_carlo_rows": mc["rows"],
        "klines": klines[-90:],
    }


def generate_dashboard_html(data, out_path):
    agents_html = ""
    for a in data["agents"]:
        col = "#10b981" if a["stance"] > 0 else "#ef4444" if a["stance"] < 0 else "#94a3b8"
        lbl = ("MUA MẠNH" if a["stance"] >= 0.3 else "MUA" if a["stance"] >= 0.1 else
               "TRUNG LẬP" if a["stance"] > -0.1 else "BÁN" if a["stance"] > -0.3 else "BÁN MẠNH")
        pct = (a["stance"] + 1) / 2 * 100
        fallback = " · ⚠️ dự phòng" if a.get("fallback") else ""
        agents_html += f"""
        <div style="background:#16223a;border:1px solid #1e2c47;border-radius:10px;padding:12px;border-left:4px solid {col};">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <b style="font-size:13px;">{a['icon']} {a['title']}</b>
            <span style="background:{col};color:#04121a;padding:2px 8px;border-radius:99px;font-size:10.5px;font-weight:800;">{lbl}</span>
          </div>
          <div style="font-size:10.5px;color:#8ea3c0;font-family:monospace;margin-bottom:6px;">{a['model']}{fallback}</div>
          <div style="height:7px;background:#0b1220;border-radius:99px;overflow:hidden;margin-bottom:4px;">
            <div style="height:100%;width:{pct:.1f}%;background:{col};"></div>
          </div>
          <div style="font-size:11px;color:#8ea3c0;display:flex;justify-content:space-between;">
            <span>Tâm lý: {a['stance']:+.2f}</span><span>Tự tin: {a['conf']*100:.0f}%</span>
          </div>
          <div style="font-size:12px;color:#c3d2e8;background:#0b1220;border-radius:8px;padding:8px 10px;margin-top:8px;border-left:3px solid #1e2c47;">{a['reasoning']}</div>
        </div>"""
    d = data
    html = DASHBOARD_TEMPLATE
    html = html.replace("@@TITLE@@", f"XAU/USD — Mô phỏng {d['timeframe_label']} · {d['generated_at']}")
    html = html.replace("@@PRICE@@", f"${d['price']:,.2f}")
    html = html.replace("@@PRICE_SRC@@", d["price_source"])
    html = html.replace("@@KLINES@@", d["kline_source"])
    html = html.replace("@@CONSENSUS@@", f"{d['consensus']:+.3f}")
    html = html.replace("@@CONSENSUS_COLOR@@", "#10b981" if d["consensus"] > 0 else "#ef4444")
    html = html.replace("@@VERDICT@@", d["verdict"])
    html = html.replace("@@TARGET@@", f"${d['target']:,.2f}")
    html = html.replace("@@RANGE@@", f"${d['p10']:,.2f} – ${d['p90']:,.2f}")
    html = html.replace("@@PROB@@", f"{d['prob_up']*100:.1f}%")
    html = html.replace("@@HORIZON@@", f"{d['timeframe_label']} · {d['horizon']} · {d['n_paths']} kịch bản")
    html = html.replace("@@CROWD@@", f"Mua {d['crowd']['bull']} · Trung lập {d['crowd']['neu']} · Bán {d['crowd']['bear']} ({d['crowd']['n_voters']} cử tri)")
    html = html.replace("@@AGENTS@@", agents_html)
    html = html.replace("@@DATA@@", json.dumps({
        "klines": d["klines"], "mc_rows": d["monte_carlo_rows"], "steps": len(d["monte_carlo_rows"]) - 1,
        "crowd": d["crowd"], "verdict": d["verdict"], "consensus": d["consensus"],
        "ind": d["indicators"], "timeframe": d["timeframe"],
    }, ensure_ascii=False).replace("</", "<\\/"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✨ Đã xuất dashboard: {out_path}")


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<style>
body{background:#0b1220;color:#e2e8f0;font-family:'Segoe UI',sans-serif;margin:0;padding:20px;}
.wrap{max-width:1200px;margin:0 auto;}
.hd{background:linear-gradient(135deg,#0f2239,#0e1a2e);border:1px solid #1e2c47;border-radius:14px;padding:18px 22px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:12px;align-items:center;}
.hd h1{margin:0;font-size:18px;} .hd p{margin:3px 0 0;color:#8ea3c0;font-size:12px;}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:14px;}
.mc{background:#111c30;border:1px solid #1e2c47;border-radius:12px;padding:12px 14px;}
.mc .l{font-size:10.5px;text-transform:uppercase;color:#8ea3c0;letter-spacing:.6px;font-weight:700;}
.mc .v{font-size:20px;font-weight:800;margin-top:3px;font-family:Consolas,monospace;}
.panel{background:#111c30;border:1px solid #1e2c47;border-radius:14px;padding:16px 18px;margin-bottom:14px;}
.panel h2{font-size:13px;text-transform:uppercase;letter-spacing:.8px;color:#8ea3c0;margin:0 0 12px;}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:14px;}
@media(max-width:900px){.grid2{grid-template-columns:1fr;}}
.agent{background:#16223a;border:1px solid #1e2c47;border-radius:10px;padding:12px;margin-bottom:10px;}
.foot{margin-top:20px;text-align:center;font-size:11px;color:#8ea3c0;}
</style></head><body><div class="wrap">
<div class="hd">
  <div><h1>🥇 XAU/USD AI Debate Arena — Dashboard</h1>
  <p>Mô phỏng đa tác nhân + đám đông bỏ phiếu + Monte Carlo</p></div>
  <div style="margin-left:auto;text-align:right;font-size:12px;color:#8ea3c0;">
    Giá: <b style="color:#f5c518;font-size:16px;">@@PRICE@@</b><br>@@PRICE_SRC@@ · @@KLINES@@
  </div>
</div>
<div class="metrics">
  <div class="mc"><div class="l">🧠 Đồng thuận ròng</div><div class="v" style="color:@@CONSENSUS_COLOR@@">@@CONSENSUS@@</div><div style="font-size:11px;color:#8ea3c0;">@@VERDICT@@</div></div>
  <div class="mc"><div class="l">🎯 Giá mục tiêu</div><div class="v" style="color:#38bdf8;">@@TARGET@@</div><div style="font-size:11px;color:#8ea3c0;">@@HORIZON@@</div></div>
  <div class="mc"><div class="l">📊 Biên độ 80% (P10-P90)</div><div class="v" style="font-size:15px;padding-top:5px;">@@RANGE@@</div></div>
  <div class="mc"><div class="l">📈 Xác suất tăng</div><div class="v" style="color:#10b981;">@@PROB@@</div><div style="font-size:11px;color:#8ea3c0;">@@CROWD@@</div></div>
</div>
<div class="grid2">
  <div class="panel"><h2>🕯️ Nến thực tế + Dự báo Monte Carlo</h2><div id="chart" style="height:480px;"></div></div>
  <div class="panel"><h2>🤖 Lập trường các chuyên gia</h2>@@AGENTS@@</div>
</div>
<div class="panel"><h2>🗳️ Đám đông bỏ phiếu</h2><div id="vote" style="height:260px;"></div></div>
<div class="foot">⚠️ Công cụ mô phỏng AI phục vụ nghiên cứu & giáo dục — KHÔNG phải lời khuyên tài chính. Chạy bằng app.py · nguồn giá: Yahoo/Binance/gold-api · model: OpenRouter/Gemini/Groq</div>
</div>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.27.0/plotly.min.js"></script>
<script>
const D = @@DATA@@;
const k = D.klines.map(x=>({t:new Date(x.t),o:x.o,h:x.h,l:x.l,c:x.c}));
const mc = D.mc_rows.map((r,i)=>({t:new Date(k[k.length-1].t.getTime()+(i+1)*(D.timeframe==='1D'?86400000:D.timeframe==='4h'?14400000:D.timeframe==='1h'?3600000:900000)),mean:r.mean,p10:r.p10,p90:r.p90}));
Plotly.newPlot('chart',[
 {x:k.map(x=>x.t),open:k.map(x=>x.o),high:k.map(x=>x.h),low:k.map(x=>x.l),close:k.map(x=>x.c),type:'candlestick',name:'Thực tế',increasing:{line:{color:'#10b981'}},decreasing:{line:{color:'#ef4444'}}},
 {x:mc.map(x=>x.t),y:mc.map(x=>x.p90),type:'scatter',mode:'lines',name:'P90',line:{color:'rgba(56,189,248,.4)',dash:'dot'}},
 {x:mc.map(x=>x.t),y:mc.map(x=>x.p10),type:'scatter',mode:'lines',name:'P10-P90',fill:'tonexty',fillcolor:'rgba(56,189,248,.08)',line:{color:'rgba(56,189,248,.4)',dash:'dot'}},
 {x:mc.map(x=>x.t),y:mc.map(x=>x.mean),type:'scatter',mode:'lines',name:'Kỳ vọng',line:{color:'#f5c518',width:2}}
],{paper_bgcolor:'#111c30',plot_bgcolor:'#0b1220',font:{color:'#8ea3c0'},xaxis:{gridcolor:'#1e2c47',type:'date'},yaxis:{gridcolor:'#1e2c47',title:'USD/oz'},margin:{t:20,l:60,r:20,b:40},legend:{orientation:'h'}});
const c = D.crowd, t = c.bull+c.neu+c.bear||1;
Plotly.newPlot('vote',[{values:[c.bull,c.neu,c.bear],labels:['Mua ('+c.bull+')','Trung lập ('+c.neu+')','Bán ('+c.bear+')'],type:'pie',hole:.55,marker:{colors:['#10b981','#64748b','#ef4444']},textinfo:'label+percent',textfont:{color:'#e2e8f0'}}],{paper_bgcolor:'#111c30',font:{color:'#8ea3c0'},showlegend:false,margin:{t:10,b:10,l:10,r:10}});
</script></body></html>
"""


# =====================================================================
# 💼 AI TRADER TỰ TRỊ (Python — chạy 24/7)
# =====================================================================
def new_trader_state():
    return {
        "balance": 1000.0, "start_balance": 1000.0, "peak": 1000.0, "max_dd": 0.0,
        "position": None, "history": [], "trades": 0, "wins": 0,
        "total_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
        "sessions": 0, "equity_points": [{"t": int(time.time() * 1000), "e": 1000.0}],
    }


def load_trader_state(out_dir):
    p = os.path.join(out_dir, "trader_state.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
            if "balance" in st:
                return st
        except Exception:
            pass
    return new_trader_state()


def save_trader_state(st, out_dir):
    with open(os.path.join(out_dir, "trader_state.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def trader_heuristic(consensus, prob_up, price, atr, target):
    action, sl, tp, tf = "hold", None, None, "1h"
    strong = abs(consensus) >= 0.25
    tf = "4h" if strong else "1h"
    if consensus >= 0.15 and prob_up > 0.52:
        action = "long"
        sl = price - max(1.5 * atr, price * 0.003)
        tp = target if target > price else price + 2 * max(1.5 * atr, price * 0.003)
        if (tp - price) / (price - sl) < 1.2:
            tp = price + 2 * (price - sl)
    elif consensus <= -0.15 and prob_up < 0.48:
        action = "short"
        sl = price + max(1.5 * atr, price * 0.003)
        tp = target if target < price else price - 2 * max(1.5 * atr, price * 0.003)
        if (price - tp) / (sl - price) < 1.2:
            tp = price - 2 * (sl - price)
    rr = (tp - price) / (price - sl) if action == "long" else (price - tp) / (sl - price) if action == "short" else 0
    reason = ("Đứng ngoài bảo toàn vốn — hội đồng chưa đủ phân cực." if action == "hold"
              else f"Tự quyết {action.upper()} theo phán quyết hội đồng ({consensus:+.2f}), P(tăng) {prob_up*100:.0f}%.")
    return {"action": action, "tf": tf, "sl": sl, "tp": tp, "risk": 0.01, "rr": rr, "reason": reason, "llm": False}


def trader_llm_prompt(consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, position):
    panel = "\n".join(f"• {f['title']}: {f['stance']:+.2f} (tự tin {f['conf']*100:.0f}%) — {f['reasoning']}" for f in finals)
    pos = f" · ĐANG CÓ LỆNH MỞ: {position['dir']} entry ${position['entry']:,.2f}" if position else ""
    return f"""Bạn là AI TRADER độc lập, chuyên nghiệp, quản lý quỹ mô phỏng 1.000 USD giao dịch vàng (XAU/USD). Đọc phán quyết hội đồng như tham khảo, tự phản biện, ra quyết định của riêng bạn. Ưu tiên bảo toàn vốn.

PHÁN QUYẾT HỘI ĐỒNG:
{panel}
Đồng thuận ròng: {consensus:+.3f} ({verdict})

THỊ TRƯỜNG:
Giá: ${price:,.2f} · ATR: ${atr:,.2f} · Hỗ trợ: ${sup:,.2f} · Kháng cự: ${res:,.2f}
Monte Carlo: mục tiêu ${target:,.2f} · P10 ${p10:,.2f} · P90 ${p90:,.2f} · P(tăng) {prob_up*100:.0f}%
Vốn quỹ: ${balance:,.2f}{pos}

NHIỆM VỤ: Tự suy nghĩ độc lập và quyết định giao dịch phiên này.
- Không nên giao dịch → action "hold".
- Nếu giao dịch: chọn hướng, khung thời gian (15m/1h/4h/1D — ngắn hạn nếu tín hiệu nhanh, dài hạn nếu xu hướng rõ), SL/TP là MỨC GIÁ cụ thể, risk_pct 0.5-3.
Trả về DUY NHẤT JSON: {{"action": "long|short|hold", "timeframe": "15m|1h|4h|1D", "sl": <số>, "tp": <số>, "risk_pct": <số>, "reason": "<lý do 2-3 câu tiếng Việt>"}}"""


def trader_llm_decision(cfg, consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, position):
    """AI Trader quyết định bằng LLM (đa nhà cung cấp + chuỗi dự phòng)."""
    chosen = cfg["trader"]
    if isinstance(chosen, str):
        chosen = {"provider": "openrouter", "model": chosen}
    chain = [(chosen.get("provider", "openrouter"), chosen.get("model", ""))]
    for p, m in FALLBACK_CHAIN.get(chosen.get("provider", "openrouter"), []):
        if (p, m) not in chain:
            chain.append((p, m))
    if not (cfg["openrouter_api_key"] or cfg["gemini_api_key"] or cfg["groq_api_key"]):
        return None
    prompt = trader_llm_prompt(consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, position)
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
            sl, tp = float(j.get("sl", 0)), float(j.get("tp", 0))
            risk = max(0.5, min(3.0, float(j.get("risk_pct", 1.0)))) / 100
            reason = str(j.get("reason", ""))[:500]
            if action == "hold":
                return {"action": "hold", "tf": tf, "sl": None, "tp": None, "risk": risk, "rr": 0, "reason": reason, "llm": True}
            if not (sl > 0 and tp > 0):
                continue
            if action == "long" and not (sl < price < tp):
                continue
            if action == "short" and not (tp < price < sl):
                continue
            rr = (tp - price) / (price - sl) if action == "long" else (price - tp) / (sl - price)
            return {"action": action, "tf": tf, "sl": sl, "tp": tp, "risk": risk, "rr": max(0.1, rr), "reason": reason, "llm": True}
        except Exception as e:
            print(f"  🔄 Trader ({provider}/{model}) lỗi: {str(e)[:100]}")
    return None


def trader_execute(st, decision, price, out_dir):
    if st.get("position"):
        return
    if decision["action"] == "hold":
        return
    sl_dist = abs(decision["sl"] - price)
    if sl_dist < 1e-9:
        return
    risk_amt = st["balance"] * decision["risk"]
    qty = risk_amt / sl_dist
    if qty * price > st["balance"] * 20:
        return
    st["position"] = {
        "id": int(time.time() * 1000), "dir": decision["action"], "tf": decision["tf"],
        "entry": price, "sl": decision["sl"], "tp": decision["tp"], "rr": decision["rr"],
        "qty": qty, "risk_pct": decision["risk"], "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reason": decision["reason"], "llm": decision.get("llm", False),
    }
    save_trader_state(st, out_dir)
    print(f"💼 AI TRADER MỞ LỆNH {decision['action'].upper()} {decision['tf']} · entry ${price:,.2f} · "
          f"SL ${decision['sl']:,.2f} · TP ${decision['tp']:,.2f} · RR 1:{decision['rr']:.1f} · rủi ro ${risk_amt:,.2f}")


def trader_check_position(st, klines, out_dir):
    if not st.get("position"):
        return None
    p = st["position"]
    last = klines[-1]
    exit_p, reason = None, ""
    if p["dir"] == "long":
        if last["l"] <= p["sl"]: exit_p, reason = p["sl"], "chạm CẮT LỖ (SL)"
        elif last["h"] >= p["tp"]: exit_p, reason = p["tp"], "chạm CHỐT LỜI (TP)"
    else:
        if last["h"] >= p["sl"]: exit_p, reason = p["sl"], "chạm CẮT LỖ (SL)"
        elif last["l"] <= p["tp"]: exit_p, reason = p["tp"], "chạm CHỐT LỜI (TP)"
    if exit_p is None:
        return None
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
    st["position"] = None
    st["equity_points"].append({"t": int(time.time() * 1000), "e": st["balance"]})
    st["history"].insert(0, {**p, "exit": exit_p, "pnl": pnl, "pnl_pct": pnl_pct, "exit_reason": reason,
                             "closed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "balance_after": st["balance"]})
    save_trader_state(st, out_dir)
    print(f"💼 ĐÓNG LỆNH {p['dir'].upper()} — {reason} · entry ${p['entry']:,.2f} → exit ${exit_p:,.2f} · "
          f"P&L {pnl:+,.2f}$ ({pnl_pct:+.2f}%) · vốn ${st['balance']:,.2f}")
    return st["history"][0]


def send_telegram(text, cfg):
    token = cfg["trader"].get("telegram_token", "")
    chat = cfg["trader"].get("telegram_chat_id", "")
    if not token or not chat:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"⚠️ Telegram lỗi: {e}")
        return False


def trader_summary(st, cfg, out_dir, force=False):
    n = int(cfg["trader"].get("summary_every", 300))
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
    lines = [
        "📊 *XAU/USD AI DEBATE ARENA — BÁO CÁO TỔNG KẾT*",
        "━━━━━━━━━━━━━━━━━",
        f"⏱️ Số phiên: {st['sessions']}" + (" (tổng kết thủ công)" if force else ""),
        f"💰 Vốn: ${st['start_balance']:,.2f} → ${st['balance']:,.2f} ({st['total_pnl']:+,.2f}$ · {ret:+.1f}%)",
        f"🎯 Số lệnh: {st['trades']} · Win rate: {win_rate:.1f}%",
        f"⚖️ Profit factor: {'∞' if pf == float('inf') else f'{pf:.2f}'}",
        f"📉 Drawdown tối đa: {st['max_dd']:.1f}%",
        "━━━━━━━━━━━━━━━━━",
        "📆 Theo khung thời gian:",
    ]
    for tf, o in by_tf.items():
        lines.append(f"  • Khung {tf}: {o['n']} lệnh · thắng {o['win']} ({o['win']/o['n']*100:.0f}%)")
    if not by_tf:
        lines.append("  (chưa có lệnh)")
    lines.append("🕘 5 lệnh gần nhất:")
    for h in closed[:5]:
        lines.append(f"  • {h['dir'].upper()} {h['tf']} {h['opened_at']}: {h['pnl']:+,.2f}$ — {h['exit_reason']}")
    if not closed:
        lines.append("  (chưa có lệnh)")
    lines.append("━━━━━━━━━━━━━━━━━\n👨‍💼 *BẠN là người ra quyết định cuối cùng.*")
    text = "\n".join(lines)
    with open(os.path.join(out_dir, "summary_latest.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print("📊 TỔNG KẾT " + str(st["sessions"]) + " phiên — vốn $" + f"{st['balance']:,.2f}" + " · " +
          str(st["trades"]) + " lệnh · win " + f"{win_rate:.1f}%" + " · PF " + ("∞" if pf == float('inf') else f"{pf:.2f}"))
    sent = send_telegram(text, cfg)
    if sent:
        print("📨 Đã gửi tổng kết qua Telegram.")
    if not force:
        st["sessions"] = 0
    save_trader_state(st, out_dir)


# =====================================================================
# CHẠY CHÍNH
# =====================================================================
def run_once(cfg, args):
    tf_key = args.timeframe
    print("=" * 64)
    print(f"🥇 XAU/USD AI DEBATE ARENA — phiên {time.strftime('%H:%M:%S')}")
    print(f"Khung: {TIMEFRAMES[tf_key]['label']} · {args.rounds} vòng tranh luận · {args.voters} cử tri · {args.paths} kịch bản")
    keys = []
    if cfg["openrouter_api_key"]: keys.append("OpenRouter")
    if cfg["gemini_api_key"]: keys.append("Gemini")
    if cfg["groq_api_key"]: keys.append("Groq")
    if cfg["cohere_api_key"]: keys.append("Cohere")
    print(f"API: {' + '.join(keys) if keys else 'CHẾ ĐỘ MẪU (chưa có key)'}")
    print("Định tuyến: " + " · ".join(f"{a['title']}→{agent_conf(cfg, a['key'])['provider']}/{agent_conf(cfg, a['key'])['model']}" for a in AGENTS))
    print("-" * 64)

    price, price_src = fetch_price()
    print(f"⚡ Giá realtime: ${price:,.2f} ({price_src})")
    klines, kline_src = fetch_klines(tf_key)
    ind = compute_indicators(klines, tf_key)
    print(f"📊 {len(klines)} nến ({kline_src}) — RSI {ind['rsi']:.1f} · vol {ind['vol']*100:.2f}%/nến · {ind['trend']}")

    snap = market_snapshot(cfg, price, price_src, klines, ind, tf_key, args.context)
    timeline, _ = run_debate(cfg, snap, args.rounds)
    finals = timeline[-1]["entries"]
    consensus, verdict = compute_consensus(finals, tf_key)
    crowd = simulate_crowd(finals, args.voters, ind["momentum"], seed=args.seed)
    mc = run_monte_carlo(price, TIMEFRAMES[tf_key]["steps"], ind["vol"], consensus, ind["momentum"], args.paths, seed=args.seed)

    print("-" * 64)
    for f in finals:
        print(f"  {f['icon']} {f['title']:<28} tâm lý {f['stance']:+.2f} (tự tin {f['conf']*100:.0f}%) — {f['reason'][:80]}")
    print(f"🧠 Đồng thuận ròng: {consensus:+.3f} → {verdict}")
    print(f"🗳️ Đám đông {args.voters} cử tri: Mua {crowd['bull']} · Trung lập {crowd['neu']} · Bán {crowd['bear']}")
    print(f"📈 Mục tiêu ${mc['target']:,.2f} · P10-P90: ${mc['p10']:,.2f} – ${mc['p90']:,.2f} · P(tăng) {mc['prob_up']*100:.1f}%")
    print("-" * 64)

    bt = run_backtest(klines, tf_key)
    if bt:
        print(f"📈 Backtest {tf_key}: win rate {bt['win_rate']}% ({bt['trades']} lệnh) · "
              f"lãi/lỗ {bt['total_return_pct']:+.2f}% · PF {bt['profit_factor'] if bt['profit_factor'] is not None else '∞'} · "
              f"DD tối đa {bt['max_drawdown_pct']}%")
    else:
        print(f"📈 Backtest: chưa đủ dữ liệu lịch sử cho khung {tf_key}.")
    print("=" * 64)

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    data = build_result(cfg, args, price, price_src, klines, kline_src, ind,
                        timeline, finals, consensus, verdict, crowd, mc)
    with open(os.path.join(out_dir, "simulation_latest.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    generate_dashboard_html(data, os.path.join(out_dir, "dashboard.html"))
    bt = run_backtest(klines, tf_key)
    if bt:
        save_backtest_history({**bt, "consensus": consensus, "verdict": verdict, "target": mc["target"]}, out_dir)

    # 💼 AI TRADER — chạy mỗi phiên
    if not args.no_trader:
        trader_step(cfg, args, ind, mc, consensus, verdict, finals, price, klines, out_dir)
    return data


def trader_step(cfg, args, ind, mc, consensus, verdict, finals, price, klines, out_dir):
    st = load_trader_state(out_dir)
    st["sessions"] += 1
    rep = trader_check_position(st, klines, out_dir)
    trader_summary(st, cfg, out_dir)
    if not st.get("position"):
        decision = trader_llm_decision(cfg, consensus, verdict, finals, price, ind["atr"],
                                       ind["sup"], ind["res"], mc["target"], mc["p10"], mc["p90"],
                                       mc["prob_up"], st["balance"], st.get("position"))
        if not decision:
            decision = trader_heuristic(consensus, mc["prob_up"], price, ind["atr"], mc["target"])
        if decision["action"] == "hold":
            print(f"🤖 AI TRADER đứng ngoài — {decision['reason'][:110]}")
        else:
            print(f"🤖 AI TRADER QUYẾT ĐỊNH: {decision['action'].upper()} {decision['tf']} · "
                  f"SL ${decision['sl']:,.2f} · TP ${decision['tp']:,.2f} · RR 1:{decision['rr']:.1f}"
                  + (" · (LLM)" if decision.get("llm") else ""))
            trader_execute(st, decision, price, out_dir)
    else:
        print(f"💼 AI Trader đang trong lệnh {st['position']['dir'].upper()} {st['position']['tf']} — chờ SL/TP.")
    save_trader_state(st, out_dir)
    if rep:
        send_telegram(
            f"💼 *AI TRADER ĐÓNG LỆNH* {rep['dir'].upper()} {rep['tf']}\n"
            f"Kết quả: {rep['exit_reason']}\n"
            f"Entry ${rep['entry']:,.2f} → Exit ${rep['exit']:,.2f}\n"
            f"P&L: {rep['pnl']:+,.2f}$ ({rep['pnl_pct']:+.2f}%)\nVốn: ${st['balance']:,.2f}",
            cfg)
    if st.get("position"):
        send_telegram(
            f"💼 *AI TRADER MỞ LỆNH* {st['position']['dir'].upper()} {st['position']['tf']}\n"
            f"Entry ${st['position']['entry']:,.2f} · SL ${st['position']['sl']:,.2f} · TP ${st['position']['tp']:,.2f}\n"
            f"RR 1:{st['position']['rr']:.1f} · Lý do: {st['position']['reason'][:160]}",
            cfg)


def main():
    ap = argparse.ArgumentParser(description="XAU/USD AI Debate Arena — backend đa nhà cung cấp")
    ap.add_argument("--timeframe", choices=list(TIMEFRAMES), default="1h")
    ap.add_argument("--rounds", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--voters", type=int, default=80)
    ap.add_argument("--paths", type=int, default=300)
    ap.add_argument("--context", default="", help="Bối cảnh bổ sung (tin tức vĩ mô...)")
    ap.add_argument("--watch", type=int, default=0, help="Tự chạy lại mỗi N phút (0 = tắt; VD: 360 = mỗi 6 giờ)")
    ap.add_argument("--serve", type=int, default=0, help="Mở web server cổng N để xem dashboard")
    ap.add_argument("--out", default="output")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-trader", action="store_true", help="Tắt AI Trader")
    ap.add_argument("--force-summary", action="store_true", help="Buộc tổng kết AI Trader ngay")
    args = ap.parse_args()
    cfg = load_config()

    if args.force_summary:
        st = load_trader_state(args.out)
        trader_summary(st, cfg, args.out, force=True)
        print("✅ Đã buộc tổng kết. Xem output/summary_latest.txt (hoặc Telegram).")
        return

    run_once(cfg, args)

    if args.serve:
        os.chdir(args.out)
        handler = SimpleHTTPRequestHandler
        handler.extensions_map = {**handler.extensions_map, ".html": "text/html; charset=utf-8"}
        print(f"🌐 Dashboard tại: http://localhost:{args.serve}/dashboard.html  (Ctrl+C để dừng)")
        ThreadingHTTPServer(("0.0.0.0", args.serve), handler).serve_forever()
        return

    if args.watch > 0:
        print(f"⏰ Chế độ watch: chạy lại mỗi {args.watch} phút. Ctrl+C để dừng.")
        try:
            while True:
                time.sleep(args.watch * 60)
                run_once(cfg, args)
        except KeyboardInterrupt:
            print("\n👋 Đã dừng.")


if __name__ == "__main__":
    main()
 
