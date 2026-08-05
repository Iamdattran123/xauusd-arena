#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XAU/USD AI DEBATE ARENA — Backend (bản kiểm tra toàn diện)
===========================================================
Multi-agent debate + crowd voting + Monte Carlo + AI Trader ($1.000 mô phỏng)
+ Telegram báo cáo MỖI PHIÊN — chạy 24/7 qua GitHub Actions hoặc VPS.

Định tuyến 4 nhà cung cấp (đọc key từ biến môi trường / GitHub Secrets):
    OPENROUTER_API_KEY · GROQ_API_KEY · GEMINI_API_KEY · COHERE_API_KEY
    TELEGRAM_BOT_TOKEN · TELEGRAM_CHAT_ID  (để nhận báo cáo mỗi phiên)

Cấu hình mặc định:
    Macro  -> openrouter / qwen/qwen3-32b
    Tech   -> openrouter / deepseek/deepseek-v4-flash-0731
    Whale  -> cohere     / command-r-plus-08-2024
    Retail -> groq       / llama-3.3-70b-versatile
    Trader -> openrouter / deepseek/deepseek-v4-flash-0731

Cách dùng:
    python app.py                      # chạy 1 phiên đầy đủ + báo cáo Telegram
    python app.py --test-telegram      # gửi tin test Telegram rồi thoát
    python app.py --force-summary      # ép AI Trader tổng kết ngay
    python app.py --watch 60           # tự chạy lại mỗi 60 phút
    python app.py --serve 8000         # web server xem dashboard
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
# HẰNG SỐ
# =====================================================================
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
# Groq/Cloudflare chặn UA không phải trình duyệt (Python-urllib -> 403 1010).
# Luôn gửi UA giả Chrome cho mọi request API LLM để tránh bị chặn.
API_URL = "https://openrouter.ai/api/v1/chat/completions"

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

# Cấu hình mặc định (có thể ghi đè bằng config.json)
DEFAULT_AGENT_CONFIG = {
    "Macro_Analyst":        {"provider": "openrouter", "model": "qwen/qwen3-32b"},
    "Technical_Analyst":    {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash-0731"},
    "Institutional_Whale":  {"provider": "cohere",     "model": "command-r-plus-08-2024"},
    "Retail_Crowd":         {"provider": "groq",       "model": "llama-3.3-70b-versatile"},
}

DEFAULT_TRADER_CONFIG = {
    "provider": "openrouter",
    "model": "deepseek/deepseek-v4-flash-0731",
    "risk_pct": 1.0,
    "summary_every": 300,
    "report_every_session": True,
    "telegram_token": "",
    "telegram_chat_id": "",
}

# Endpoint + cách gọi từng nhà cung cấp
PROVIDER_META = {
    "openrouter": {"url": API_URL, "env": "OPENROUTER_API_KEY", "style": "openai",
                   "extra_headers": {"HTTP-Referer": "https://localhost", "X-Title": "XAUUSD AI Debate Arena"}},
    "groq":       {"url": "https://api.groq.com/openai/v1/chat/completions", "env": "GROQ_API_KEY", "style": "openai",
                   "extra_headers": {}},
    "cohere":     {"url": "https://api.cohere.com/v2/chat", "env": "COHERE_API_KEY", "style": "cohere",
                   "extra_headers": {"X-Client-Name": "xauusd-arena"}},
    "gemini":     {"base": "https://generativelanguage.googleapis.com/v1beta/models", "env": "GEMINI_API_KEY",
                   "style": "google", "extra_headers": {}},
}

# Chuỗi dự phòng theo từng nhà cung cấp (khi model chính lỗi/429)
FALLBACK_CHAIN = {
    "gemini":     [("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash-lite")],
    "groq":       [("groq", "llama-3.3-70b-versatile"), ("groq", "llama-3.1-8b-instant")],
    "cohere":     [("cohere", "command-r-plus-08-2024"), ("cohere", "command-r-08-2024")],
    "openrouter": [("openrouter", "qwen/qwen3-32b"),
                   ("openrouter", "deepseek/deepseek-v4-flash-0731"),
                   ("openrouter", "deepseek/deepseek-chat"),
                   ("openrouter", "openai/gpt-4o-mini"),
                   ("openrouter", "google/gemma-4-31b-it:free")],
}
# Model OpenRouter rẻ dùng làm fallback CHÉO (khi provider khác lỗi hết)
OR_FALLBACK_MODELS = ["qwen/qwen3-32b", "deepseek/deepseek-v4-flash-0731", "openai/gpt-4o-mini", "google/gemma-4-31b-it:free"]

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

# =====================================================================
# CẤU HÌNH
# =====================================================================
def load_config():
    """Key từ biến môi trường (GitHub Secrets) + ghi đè tùy chọn bằng config.json."""
    cfg = {
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "gemini_api_key":     os.getenv("GEMINI_API_KEY", ""),
        "groq_api_key":       os.getenv("GROQ_API_KEY", ""),
        "cohere_api_key":     os.getenv("COHERE_API_KEY", ""),
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
            if isinstance(user.get("models"), dict):
                for key, val in user["models"].items():
                    if isinstance(val, str):
                        cfg["models"][key] = {"provider": "openrouter", "model": val}
                    elif isinstance(val, dict) and val.get("model"):
                        cfg["models"][key] = {"provider": val.get("provider", "openrouter"), "model": val["model"]}
            if isinstance(user.get("trader"), dict):
                cfg.get("trader", {}).update(user["trader"])
            if isinstance(user.get("exness"), dict):
                cfg["exness"] = user["exness"]
            if isinstance(user.get("metaapi"), dict):
                cfg["metaapi"] = user["metaapi"]
        except Exception as e:
            print(f"⚠️ Lỗi đọc config.json: {e}")
    cfg.setdefault("exness", {})
    if os.getenv("EXNESS_PASSWORD"):
        cfg["exness"]["password"] = os.getenv("EXNESS_PASSWORD")
    cfg.setdefault("metaapi", {})
    if os.getenv("METAAPI_TOKEN"):
        cfg["metaapi"]["token"] = os.getenv("METAAPI_TOKEN")
    if os.getenv("METAAPI_ACCOUNT_ID"):
        cfg["metaapi"]["account_id"] = os.getenv("METAAPI_ACCOUNT_ID")
    # Telegram: env (Secrets) ưu tiên hơn config.json
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        cfg.get("trader", {})["telegram_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"):
        cfg.get("trader", {})["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
    return cfg


def agent_conf(cfg, key):
    c = cfg["models"].get(key, {})
    if isinstance(c, str):
        return {"provider": "openrouter", "model": c}
    return {"provider": c.get("provider", "openrouter"), "model": c.get("model", "")}


def any_api_key(cfg):
    return bool(cfg.get("openrouter_api_key") or cfg.get("gemini_api_key")
                or cfg.get("groq_api_key") or cfg.get("cohere_api_key"))


# =====================================================================
# UTILS
# =====================================================================
def http_json(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_post(url, payload, headers, timeout=90):
    """POST JSON → dict. Ném lỗi kèm .status và .retry_after khi HTTP lỗi."""
    hdrs = dict(headers)
    hdrs.setdefault("User-Agent", UA["User-Agent"])  # bắt buộc UA trình duyệt (Groq chặn UA bot)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs)
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


def extract_json(text):
    """Bóc JSON từ văn bản LLM (bỏ <think>, markdown, chữ thừa)."""
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


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# =====================================================================
# DỮ LIỆU REALTIME
# =====================================================================
# Lưu cả giá spot + futures để báo cáo chênh lệch (giải thích vì sao giá lệch sàn)
PRICE_INFO = {"spot": None, "spot_src": None, "futures": None, "futures_src": None}


def fetch_price(cfg=None):
    """Giá vàng realtime — ưu tiên XAUUSD SPOT (khớp giá các sàn Exness/TradingView):
    gold-api spot → Binance PAXG (≈spot) → Yahoo GC=F (futures COMEX).
    Lưu cả spot & futures vào PRICE_INFO để báo cáo chênh lệch."""
    spot = futures = None
    spot_src = futures_src = None
    # 1) Spot: gold-api.com (XAUUSD spot)
    try:
        d = http_json("https://api.gold-api.com/price/XAU", timeout=6)
        spot = float(d["price"]); spot_src = "gold-api.com · XAU spot"
    except Exception:
        pass
    # 2) Spot: Binance PAXG (≈ 1 oz vàng)
    if spot is None:
        try:
            d = http_json("https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT", timeout=6)
            spot = float(d["price"]); spot_src = "Binance · PAXG (≈spot)"
        except Exception:
            pass
    # 3) Futures: Yahoo GC=F (COMEX) — để so sánh
    try:
        d = http_json("https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=1d", timeout=6)
        futures = float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
        futures_src = "Yahoo Finance · GC=F (COMEX futures)"
    except Exception:
        pass
    PRICE_INFO["spot"] = spot; PRICE_INFO["spot_src"] = spot_src
    PRICE_INFO["futures"] = futures; PRICE_INFO["futures_src"] = futures_src
    if spot:
        return spot, spot_src
    if futures:
        return futures, futures_src
    return 4050.0, "Giá mặc định (offline)"


def fetch_klines(tf_key):
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
    momentum = clamp(last_ret * 50, -1, 1)
    return {"rsi": rsi[-1], "ema20": ema20, "ema50": ema50, "macd_hist": hist[-1], "atr": atr,
            "res": res, "sup": sup, "vol": vol, "momentum": momentum,
            "last_ret_pct": last_ret * 100, "trend": "TĂNG" if ema20 > ema50 else "GIẢM", "n": n}


# =====================================================================
# PROMPT
# =====================================================================
def market_snapshot(cfg, price, price_src, klines, ind, tf_key, context="", history=""):
    tf = TIMEFRAMES[tf_key]
    L = [f"Giá hiện tại: ${price:,.2f}/oz (nguồn: {price_src}).",
         f"Khung {tf['label']}: dự báo {tf['steps']} nến ≈ {tf['label']} tới."]
    if PRICE_INFO.get("futures") and PRICE_INFO.get("spot") and abs(PRICE_INFO["spot"] - PRICE_INFO["futures"]) > 1:
        L.append(f"Lưu ý giá: spot XAUUSD ${PRICE_INFO['spot']:,.2f} vs futures GC=F ${PRICE_INFO['futures']:,.2f} "
                 f"(chênh ${PRICE_INFO['spot']-PRICE_INFO['futures']:+,.2f} — futures thường lệch spot).")
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


# =====================================================================
# LLM — ĐỊNH TUYẾN 4 NHÀ CUNG CẤP
# =====================================================================
def call_llm(role, prompt, cfg, provider=None, model=None):
    """Gọi LLM theo provider của role → trả về cấu trúc thống nhất."""
    if role == "trader":
        conf = cfg.get("trader", {})
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
        url = f"{meta['base']}/{model}:generateContent?key={key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0.4, "maxOutputTokens": 700}}
        data = _http_post(url, payload, {"Content-Type": "application/json", **meta["extra_headers"]})
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"Gemini trả lỗi: {json.dumps(data, ensure_ascii=False)[:200]}")
        usage = data.get("usageMetadata", {})
        ptok = int(usage.get("promptTokenCount", 0) or 0)
        ctok = int(usage.get("candidatesTokenCount", 0) or 0)
    elif meta["style"] == "cohere":
        url = meta["url"]
        payload = {"model": model,
                   "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                   "temperature": 0.4, "max_tokens": 700}
        data = _http_post(url, payload, {"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                         **meta["extra_headers"]})
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
    else:  # openai-style (groq / openrouter)
        url = meta["url"]
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "temperature": 0.4, "max_tokens": 700}
        data = _http_post(url, payload, {"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                         **meta["extra_headers"]})
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
    return {"text": text, "prompt_tokens": ptok, "completion_tokens": ctok, "model": model, "provider": provider}


def _build_chain(cfg, provider, model):
    chain = [(provider, model)]
    for p, m in FALLBACK_CHAIN.get(provider, []):
        if (p, m) not in chain:
            chain.append((p, m))
    # Fallback chéo: nếu provider chính lỗi hết → thử OpenRouter rẻ (nếu có key)
    if provider != "openrouter" and (cfg["openrouter_api_key"] or os.getenv("OPENROUTER_API_KEY")):
        for m in OR_FALLBACK_MODELS:
            if ("openrouter", m) not in chain:
                chain.append(("openrouter", m))
    return chain


def _normalize_json(j, round_no):
    j.setdefault("confidence", 0.7)
    if round_no == 2:
        if not j.get("revised_sentiment") and j.get("sentiment_score") is not None:
            j["revised_sentiment"] = j["sentiment_score"]
    return j


def call_agent_json(agent, round_no, prev, snap, cfg):
    """Gọi LLM cho 1 agent theo chuỗi dự phòng (retry 429) → dict đã parse."""
    role = agent["key"]
    chosen = agent_conf(cfg, role)
    chain = _build_chain(cfg, chosen["provider"], chosen["model"])
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
            return {"json": _normalize_json(j, round_no), "model": f"{provider}/{model}", "provider": provider,
                    "prompt_tokens": res["prompt_tokens"], "completion_tokens": res["completion_tokens"]}
        except Exception as e:
            last_err = str(e)
            status = getattr(e, "status", 0)
            is429 = status == 429 or "429" in last_err
            if status == 403:
                # Key provider bị từ chối -> bỏ qua mọi model còn lại CÙNG provider, chuyển thẳng sang nguồn khác
                while ci + 1 < len(chain) and chain[ci + 1][0] == provider:
                    ci += 1
                if ci + 1 < len(chain):
                    print(f"  🔄 {agent['title']} ({provider}) bị từ chối (403) — chuyển thẳng {chain[ci+1][0]}/{chain[ci+1][1]}")
                else:
                    print(f"  ⚠️ {agent['title']} hết chuỗi dự phòng — dùng dữ liệu mẫu. ({last_err[:80]})")
                    break
                ci += 1
                continue
            if is429:
                print(f"  ⏳ {agent['title']} ({provider}/{model}) bị 429 (rate limit) — chờ 5s thử lại...")
                time.sleep(5)
                try:
                    res = call_llm(role, prompt, cfg, provider=provider, model=model)
                    j = extract_json(res["text"])
                    if j:
                        return {"json": _normalize_json(j, round_no), "model": f"{provider}/{model}",
                                "provider": provider, "prompt_tokens": res["prompt_tokens"],
                                "completion_tokens": res["completion_tokens"]}
                    last_err = "JSON không hợp lệ (sau retry)"
                except Exception as e2:
                    last_err = str(e2)
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


# =====================================================================
# TRANH LUẬN
# =====================================================================
def run_debate(cfg, snap, rounds):
    timeline, prev = [], None
    live = any_api_key(cfg)
    for r in range(1, rounds + 1):
        entries = []
        for agent in AGENTS:
            conf = agent_conf(cfg, agent["key"])
            print(f"  🤖 [Vòng {r}] {agent['title']}... ({conf['provider']}/{conf['model']})")
            time.sleep(1.2)  # giãn cách giữa các agent — tránh rate limit, tôn trọng giới hạn
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
                                    "stance": clamp(gfloat(j, ["sentiment_score", "sentiment"]), -1, 1),
                                    "conf": clamp(gfloat(j, ["confidence"], 0.7), 0, 1),
                                    "reason": str(j.get("reasoning", j.get("reason", "")))[:600],
                                    "critique": None, "model": out["model"], "fallback": False})
                else:
                    entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"],
                                    "stance": clamp(gfloat(j, ["revised_sentiment", "sentiment_score", "sentiment"]), -1, 1),
                                    "conf": clamp(gfloat(j, ["confidence"], 0.7), 0, 1),
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
    mb = clamp(momentum * 0.35 + rng.normal(0, 0.05), -0.4, 0.4)
    stances = np.array([f["stance"] for f in finals])
    confs = np.array([f["conf"] for f in finals])
    biases = np.clip(mb + rng.normal(0, 0.35, n_voters), -1, 1)
    pers = confs[None, :] * (1 - 0.85 * np.abs(stances[None, :] - biases[:, None])) + rng.normal(0, 0.12, (n_voters, len(finals)))
    pers = np.clip(pers, 0, 1.5)
    total = pers.sum(axis=1)
    pos = np.where(total > 0, (pers * stances[None, :]).sum(axis=1) / total, 0)
    bull = int((pos > 0.12).sum())
    bear = int((pos < -0.12).sum())
    winners = pers.argmax(axis=1)
    votes = {f["key"]: int((winners == i).sum()) for i, f in enumerate(finals)}
    return {"n_voters": n_voters, "bull": bull, "neu": n_voters - bull - bear, "bear": bear,
            "crowd_mean": float(pos.mean()), "votes": votes, "momentum": mb}


def run_monte_carlo(start, steps, vol, consensus, momentum, paths, seed=None):
    rng = np.random.default_rng(seed)
    drift = consensus * vol * 0.45 + momentum * vol * 0.10
    W = rng.normal(0, 1, (paths, steps))
    increments = (drift - 0.5 * vol * vol) + vol * W
    log_paths = np.zeros((paths, steps + 1))
    log_paths[:, 1:] = math.log(start) + np.cumsum(increments, axis=1)
    mat = np.exp(log_paths)
    mean = mat.mean(axis=0)
    p10 = np.percentile(mat, 10, axis=0)
    p50 = np.percentile(mat, 50, axis=0)
    p90 = np.percentile(mat, 90, axis=0)
    return {"target": float(mean[-1]), "p10": float(p10[-1]), "p50": float(p50[-1]), "p90": float(p90[-1]),
            "prob_up": float((mat[:, -1] > start).mean()), "drift": drift,
            "rows": [{"mean": float(mean[i]), "p10": float(p10[i]), "p50": float(p50[i]), "p90": float(p90[i])}
                     for i in range(steps + 1)]}


# =====================================================================
# BACKTEST
# =====================================================================
def run_backtest(klines, tf_key):
    """Backtest heuristic — có bộ lọc RSI quá mua/quá bán + lọc xu hướng (bài học AI đã rút ra):
    - RSI > 72: không MUA (tránh đuổi đỉnh) · RSI < 28: không BÁN (tránh bán đáy)
    - Chỉ trade cùng chiều xu hướng EMA20/EMA50
    """
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
    skipped = 0
    for i in range(min_n, n - steps):
        sl = closes[:i + 1]
        ks = klines[:i + 1]
        rsi = calc_rsi(sl)[-1]
        e20, e50 = calc_ema(sl, 20)[-1], calc_ema(sl, 50)[-1]
        _, _, hist = calc_macd(sl)
        hist = hist[-1]
        atr = calc_atr(ks)
        last_ret = sl[-1] / sl[-2] - 1 if len(sl) > 1 else 0
        score = (0.30 if e20 > e50 else -0.30) + clamp((50 - rsi) / 30 * 0.2, -0.2, 0.2) \
                + clamp(hist / (atr if atr else 1e-9) * 0.25, -0.25, 0.25) + clamp(last_ret * 20, -0.15, 0.15)
        if abs(score) < 0.15:
            skipped += 1
            continue
        # 🔒 Bộ lọc: không đuổi đỉnh/đáy khi RSI cực đoan + chỉ trade cùng trend
        if score > 0 and rsi > 72:
            skipped += 1
            continue
        if score < 0 and rsi < 28:
            skipped += 1
            continue
        if score > 0 and e20 <= e50:
            skipped += 1
            continue
        if score < 0 and e20 >= e50:
            skipped += 1
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
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "tf": tf_key, "trades": trades,
            "win_rate": round(wins / trades * 100, 1), "total_return_pct": round((equity - 1) * 100, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "max_drawdown_pct": round(max_dd * 100, 2), "points": (n - min_n - steps) // steps + 1,
            "skipped": skipped, "filtered": True}


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
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "timeframe": args.timeframe,
            "timeframe_label": tf["label"], "horizon": f"{tf['steps']} nến", "rounds": args.rounds,
            "price": price, "price_source": price_src, "kline_source": kline_src, "n_candles": len(klines),
            "indicators": ind, "consensus": consensus, "verdict": verdict,
            "target": mc["target"], "p10": mc["p10"], "p50": mc["p50"], "p90": mc["p90"],
            "prob_up": mc["prob_up"], "drift": mc["drift"], "n_paths": args.paths, "crowd": crowd,
            "agents": [{"key": f["key"], "title": f["title"], "icon": f["icon"], "stance": f["stance"],
                        "conf": f["conf"], "reasoning": f["reason"], "model": f["model"],
                        "fallback": f.get("fallback", False)} for f in finals],
            "timeline": timeline, "monte_carlo_rows": mc["rows"], "klines": klines[-90:]}


def generate_dashboard_html(data, out_path):
    agents_html = ""
    for a in data["agents"]:
        col = "#10b981" if a["stance"] > 0 else "#ef4444" if a["stance"] < 0 else "#94a3b8"
        lbl = ("MUA MẠNH" if a["stance"] >= 0.3 else "MUA" if a["stance"] >= 0.1 else
               "TRUNG LẬP" if a["stance"] > -0.1 else "BÁN" if a["stance"] > -0.3 else "BÁN MẠNH")
        pct = (a["stance"] + 1) / 2 * 100
        fb = " · ⚠️ dự phòng" if a.get("fallback") else ""
        agents_html += (f'<div style="background:#16223a;border:1px solid #1e2c47;border-radius:10px;padding:12px;border-left:4px solid {col};">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;"><b style="font-size:13px;">{a["icon"]} {a["title"]}</b>'
                        f'<span style="background:{col};color:#04121a;padding:2px 8px;border-radius:99px;font-size:10.5px;font-weight:800;">{lbl}</span></div>'
                        f'<div style="font-size:10.5px;color:#8ea3c0;font-family:monospace;margin-bottom:6px;">{a["model"]}{fb}</div>'
                        f'<div style="height:7px;background:#0b1220;border-radius:99px;overflow:hidden;margin-bottom:4px;"><div style="height:100%;width:{pct:.1f}%;background:{col};"></div></div>'
                        f'<div style="font-size:11px;color:#8ea3c0;display:flex;justify-content:space-between;"><span>Tâm lý: {a["stance"]:+.2f}</span><span>Tự tin: {a["conf"]*100:.0f}%</span></div>'
                        f'<div style="font-size:12px;color:#c3d2e8;background:#0b1220;border-radius:8px;padding:8px 10px;margin-top:8px;border-left:3px solid #1e2c47;">{a["reasoning"]}</div></div>')
    d = data
    html = DASHBOARD_TEMPLATE
    for k, v in [("@@TITLE@@", f"XAU/USD — Mô phỏng {d['timeframe_label']} · {d['generated_at']}"),
                 ("@@PRICE@@", f"${d['price']:,.2f}"), ("@@PRICE_SRC@@", d["price_source"]),
                 ("@@KLINES@@", d["kline_source"]), ("@@CONSENSUS@@", f"{d['consensus']:+.3f}"),
                 ("@@CONSENSUS_COLOR@@", "#10b981" if d["consensus"] > 0 else "#ef4444"),
                 ("@@VERDICT@@", d["verdict"]), ("@@TARGET@@", f"${d['target']:,.2f}"),
                 ("@@RANGE@@", f"${d['p10']:,.2f} – ${d['p90']:,.2f}"), ("@@PROB@@", f"{d['prob_up']*100:.1f}%"),
                 ("@@HORIZON@@", f"{d['timeframe_label']} · {d['horizon']} · {d['n_paths']} kịch bản"),
                 ("@@CROWD@@", f"Mua {d['crowd']['bull']} · Trung lập {d['crowd']['neu']} · Bán {d['crowd']['bear']} ({d['crowd']['n_voters']} cử tri)"),
                 ("@@AGENTS@@", agents_html),
                 ("@@DATA@@", json.dumps({"klines": d["klines"], "mc_rows": d["monte_carlo_rows"],
                                          "steps": len(d["monte_carlo_rows"]) - 1, "crowd": d["crowd"],
                                          "verdict": d["verdict"], "consensus": d["consensus"],
                                          "ind": d["indicators"], "timeframe": d["timeframe"]},
                                         ensure_ascii=False).replace("</", "<\\/"))]:
        html = html.replace(k, v)
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
<div class="foot">⚠️ Công cụ mô phỏng AI phục vụ nghiên cứu & giáo dục — KHÔNG phải lời khuyên tài chính.</div>
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
const c = D.crowd;
Plotly.newPlot('vote',[{values:[c.bull,c.neu,c.bear],labels:['Mua ('+c.bull+')','Trung lập ('+c.neu+')','Bán ('+c.bear+')'],type:'pie',hole:.55,marker:{colors:['#10b981','#64748b','#ef4444']},textinfo:'label+percent',textfont:{color:'#e2e8f0'}}],{paper_bgcolor:'#111c30',font:{color:'#8ea3c0'},showlegend:false,margin:{t:10,b:10,l:10,r:10}});
</script></body></html>
"""


# =====================================================================
# 💼 AI TRADER
# =====================================================================
TRADER_DEFAULT_STATE = {
    "balance": 1000.0, "start_balance": 1000.0, "peak": 1000.0, "max_dd": 0.0,
    "position": None, "history": [], "trades": 0, "wins": 0,
    "total_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
    "sessions": 0, "equity_points": [],
}


MAX_POSITIONS = 3  # tối đa 3 lệnh mở cùng lúc — đủ 3 phải chờ 1 lệnh chạm SL/TP


def new_trader_state():
    """State mới — các container (list/dict) phải là bản mới, KHÔNG dùng chung với hằng số."""
    return {
        "balance": 1000.0, "start_balance": 1000.0, "peak": 1000.0, "max_dd": 0.0,
        "positions": [], "position": None, "pending_orders": [], "history": [], "trades": 0, "wins": 0,
        "total_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
        "trader_score": 1000.0, "lessons": [],
        "sessions": 0, "trade_tf": "", "equity_points": [{"t": int(time.time() * 1000), "e": 1000.0}],
    }


def load_trader_state(out_dir):
    p = os.path.join(out_dir, "trader_state.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
            if isinstance(st, dict) and "balance" in st:
                merged = new_trader_state()
                merged.update(st)
                # migrate: state cũ dùng "position" đơn → chuyển sang danh sách "positions"
                if merged.get("position"):
                    if not isinstance(merged.get("positions"), list):
                        merged["positions"] = []
                    if not any(p.get("id") == merged["position"].get("id") for p in merged["positions"]):
                        merged["positions"].append(merged["position"])
                merged["position"] = None
                if not isinstance(merged.get("positions"), list):
                    merged["positions"] = []
                if not isinstance(merged.get("pending_orders"), list):
                    merged["pending_orders"] = []
                merged.setdefault("trader_score", 1000.0)
                if not isinstance(merged.get("lessons"), list):
                    merged["lessons"] = []
                merged.setdefault("trade_tf", "")
                return merged
        except Exception:
            pass
    return new_trader_state()


def save_trader_state(st, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "trader_state.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def trader_heuristic(consensus, prob_up, price, atr, target, sup=None, res=None):
    """Fallback khi không có LLM — có thể đặt lệnh chờ (limit tại hỗ trợ/kháng cự) như trader chuyên nghiệp."""
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
        # nếu có hỗ trợ gần & đủ xa → đặt BUY_LIMIT chờ giá về hỗ trợ (chuyên nghiệp, giá tốt hơn)
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


def trader_llm_prompt(consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, st):
    """Hội đồng CHỈ GÓP Ý — AI Trader là trader độc lập, tự quyết, nhớ lịch sử + bài học + điểm."""
    panel = "\n".join(f"• {f['title']}: {f['stance']:+.2f} (tự tin {f['conf']*100:.0f}%) — {f.get('reasoning') or f.get('reason') or ''}"
                      for f in finals)
    pos = st.get("positions") or []
    pos_line = ""
    if pos:
        pos_line = "ĐANG MỞ: " + " · ".join(f"{p['dir'].upper()} {p['tf']}@{p['entry']:,.0f}" for p in pos)
    pend = st.get("pending_orders") or []
    pend_line = ""
    if pend:
        pend_line = "LỆNH CHỜ: " + " · ".join(f"{p['type'].upper()} {p['trigger']:,.0f}" for p in pend)
    # Lịch sử + bài học để AI Trader nhớ & rút kinh nghiệm (nối tiếp, không phải phiên mới)
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
    return (f"Bạn là AI TRADER chuyên nghiệp độc lập, quản lý quỹ mô phỏng ${balance:,.2f} giao dịch vàng (XAU/USD). "
            f"HỘI ĐỒNG CHỈ GÓP Ý — bạn có quyền nghe hoặc giữ lập trường riêng. Ưu tiên bảo toàn vốn.\n\n"
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
            f"- Đặt LỆNH CHỜ (chuyên nghiệp) → order_type \"buy_limit\" (chờ giá hạ về mốc) | \"buy_stop\" (chờ phá mốc) "
            f"| \"sell_limit\" (chờ giá tăng lên mốc) | \"sell_stop\" (chờ thủng mốc), kèm \"trigger\" = MỨC GIÁ chờ.\n"
            f"- SL/TP là MỨC GIÁ cụ thể, risk_pct 0.5-3, khung thời gian 15m/1h/4h/1D.\n"
            f'Trả về DUY NHẤT JSON: {{"action": "long|short|hold", "order_type": "market|buy_limit|buy_stop|sell_limit|sell_stop", '
            f'"trigger": <giá chờ, bỏ 0 nếu market>, "timeframe": "15m|1h|4h|1D", "sl": <số>, "tp": <số>, '
            f'"risk_pct": <số>, "reason": "<lý do 2-3 câu tiếng Việt, nêu bạn có nghe hội đồng hay giữ lập trường riêng>"}}')


def trader_llm_decision(cfg, consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, st):
    if not any_api_key(cfg):
        return None
    chosen = cfg.get("trader", {})
    if isinstance(chosen, str):
        chosen = {"provider": "openrouter", "model": chosen}
    chain = _build_chain(cfg, chosen.get("provider", "openrouter"), chosen.get("model", ""))
    prompt = trader_llm_prompt(consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, st)
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
                # lệnh ngay: SL/TP phải đúng hướng quanh giá hiện tại
                if action == "long" and not (sl < price < tp):
                    continue
                if action == "short" and not (tp < price < sl):
                    continue
                rr = (tp - price) / (price - sl) if action == "long" else (price - tp) / (sl - price)
                return {"action": action, "tf": tf, "sl": sl, "tp": tp, "risk": risk,
                        "rr": max(0.1, rr), "reason": reason, "llm": True,
                        "order_type": "market", "trigger": 0}
            # lệnh chờ: trigger phải đúng hướng so với giá
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


MAX_PENDING = 3  # tối đa 3 lệnh chờ cùng lúc
PENDING_EXPIRY_SESSIONS = 8  # lệnh chờ tự hủy sau 8 phiên chưa chạm giá


def trader_create_pending(st, decision, price, out_dir):
    """Đặt LỆNH CHỜ (pending): BUY/SELL LIMIT/STOP — chỉ kích hoạt khi giá chạm mốc."""
    if decision["action"] == "hold":
        return False
    if not isinstance(st.get("pending_orders"), list):
        st["pending_orders"] = []
    if len(st["pending_orders"]) >= MAX_PENDING:
        print(f"🚫 Đã đủ {MAX_PENDING} lệnh chờ — hủy lệnh chờ cũ trước khi đặt mới.")
        return False
    otype = decision.get("order_type", "market")
    if otype == "market":
        return False
    trigger = decision.get("trigger", 0)
    if not trigger or trigger <= 0:
        return False
    # validate: LIMIT = chờ giá quay về (ngược hướng hiện tại), STOP = chờ giá vượt (cùng hướng)
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


def trader_process_pending(st, klines, out_dir):
    """Mỗi phiên: kiểm tra lệnh chờ — giá chạm mốc → kích hoạt thành lệnh mở; quá hạn → hủy.
    Trả về list sự kiện: [{"event": "activated"|"cancelled", "order": {...}}]"""
    if not isinstance(st.get("pending_orders"), list) or not st["pending_orders"] or not klines:
        return []
    last = klines[-1]
    events = []
    remaining = []
    for po in st["pending_orders"]:
        po["sessions_alive"] = po.get("sessions_alive", 0) + 1
        triggered = False
        # kích hoạt theo loại lệnh chờ
        if po["type"] == "buy_limit" and last["l"] <= po["trigger"]:
            triggered = True
        elif po["type"] == "buy_stop" and last["h"] >= po["trigger"]:
            triggered = True
        elif po["type"] == "sell_limit" and last["h"] >= po["trigger"]:
            triggered = True
        elif po["type"] == "sell_stop" and last["l"] <= po["trigger"]:
            triggered = True
        if triggered:
            # đủ slot lệnh mở thì kích hoạt, không thì hủy
            if len(st.get("positions") or []) < MAX_POSITIONS:
                st["positions"].append({
                    "id": int(time.time() * 1000), "dir": po["dir"], "tf": po.get("tf", "1h"),
                    "entry": po["trigger"], "sl": po["sl"], "tp": po["tp"], "rr": po["rr"],
                    "qty": 0, "risk_pct": po["risk_pct"], "pending": True,
                    "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": po["reason"], "llm": po.get("llm", False),
                })
                # tính qty theo risk
                sl_dist = abs(po["sl"] - po["trigger"])
                if sl_dist > 1e-9:
                    risk_amt = st["balance"] * po["risk_pct"]
                    st["positions"][-1]["qty"] = risk_amt / sl_dist
                events.append({"event": "activated", "order": po})
                print(f"⚡ LỆNH CHỜ KÍCH HOẠT: {po['dir'].upper()} {po['type'].upper()} @ ${po['trigger']:,.2f} "
                      f"→ thành lệnh mở (SL ${po['sl']:,.2f} · TP ${po['tp']:,.2f})")
            else:
                events.append({"event": "cancelled_full", "order": po})
                print(f"🚫 LỆNH CHỜ {po['type'].upper()} bị hủy — đã đủ {MAX_POSITIONS} lệnh mở.")
            continue
        # hết hạn
        if po["sessions_alive"] >= PENDING_EXPIRY_SESSIONS:
            events.append({"event": "expired", "order": po})
            print(f"🗑️ LỆNH CHỜ HẾT HẠN: {po['type'].upper()} @ ${po['trigger']:,.2f} (sau {po['sessions_alive']} phiên chưa chạm)")
            continue
        remaining.append(po)
    st["pending_orders"] = remaining
    if events:
        save_trader_state(st, out_dir)
    return events


def trader_execute(st, decision, price, out_dir):
    """Mở lệnh mới — tối đa MAX_POSITIONS (3) lệnh mở cùng lúc."""
    if decision["action"] == "hold":
        return False
    if not isinstance(st.get("positions"), list):
        st["positions"] = []
    if len(st["positions"]) >= MAX_POSITIONS:
        print(f"🚫 Đã đủ {MAX_POSITIONS} lệnh mở — chờ 1 lệnh chạm SL/TP mới được mở tiếp. (giữ lệnh hiện tại)")
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


def trader_check_position(st, klines, out_dir):
    """Kiểm tra TẤT CẢ lệnh mở — đóng lệnh nào chạm SL/TP. Trả danh sách lệnh đã đóng."""
    if not klines or not isinstance(st.get("positions"), list) or not st["positions"]:
        return []
    last = klines[-1]
    closed_all = []
    remaining = []
    for p in st["positions"]:
        exit_p, reason = None, ""
        if p["dir"] == "long":
            if last["l"] <= p["sl"]:
                exit_p, reason = p["sl"], "chạm CẮT LỖ (SL)"
            elif last["h"] >= p["tp"]:
                exit_p, reason = p["tp"], "chạm CHỐT LỜI (TP)"
        else:
            if last["h"] >= p["sl"]:
                exit_p, reason = p["sl"], "chạm CẮT LỖ (SL)"
            elif last["l"] <= p["tp"]:
                exit_p, reason = p["tp"], "chạm CHỐT LỜI (TP)"
        if exit_p is None:
            remaining.append(p)
            continue
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
        closed_all.append(closed_rec)
        # 🏆 CHẤM ĐIỂM thưởng/phạt cho AI Trader + bài học rút kinh nghiệm
        delta = max(-100.0, min(100.0, pnl_pct * 20.0))  # thắng 1% → +20, thua 1% → -20
        st["trader_score"] = st.get("trader_score", 1000.0) + delta
        lesson = (f"{'✅' if pnl > 0 else '❌'} {p['dir'].upper()} {p['tf']} {pnl_pct:+.1f}% ({reason}) "
                  f"-> {'giữ chiến lược' if pnl > 0 else 'cần chờ mốc tốt hơn/tránh vào sớm'}")
        st.setdefault("lessons", []).append(lesson)
        st["lessons"] = st["lessons"][-50:]
        print(f"💼 ĐÓNG LỆNH {p['dir'].upper()} — {reason} · entry ${p['entry']:,.2f} → exit ${exit_p:,.2f} · "
              f"P&L {pnl:+,.2f}$ ({pnl_pct:+.2f}%) · vốn ${st['balance']:,.2f} · 🏆 điểm {st['trader_score']:.0f}")
    st["positions"] = remaining
    if closed_all:
        save_trader_state(st, out_dir)
    return closed_all


def trader_perf_line(st):
    """Hiệu suất THẬT của AI Trader (phân biệt với backtest heuristic)."""
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
        parts.append(f"{len(positions)}/{MAX_POSITIONS} lệnh mở: " + " · ".join(f"{p['dir'].upper()} {p['tf']}@{p['entry']:,.0f}" for p in positions))
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


# =====================================================================
# ☁️ METAAPI — Cloud MT5 (AUTO-TRADE EXNESS DEMO qua REST API, không cần MT5)
# ---------------------------------------------------------------
# - Dịch vụ host sẵn MT5 trên cloud (metaapi.cloud) — free 1 tài khoản
# - Từ GitHub Actions (Linux) chỉ cần gọi REST API là đặt lệnh được
# - Cần: METAAPI_TOKEN (từ app.metaapi.cloud) + METAAPI_ACCOUNT_ID
#   (tài khoản MetaAPI đã được tạo + nạp thông tin Exness demo trong dashboard)
# =====================================================================
METAAPI_REGIONS = [
    "mt-client-api-v1.agiliumtrade.agiliumtrade.ai",
    "mt-client-api-v1.new-york.agiliumtrade.ai",
    "mt-client-api-v1.london.agiliumtrade.ai",
    "mt-client-api-v1.manila.agiliumtrade.ai",
    "mt-client-api-v1.cyprus.agiliumtrade.ai",
    "mt-client-api-v1.moscow.agiliumtrade.ai",
    "mt-client-api-v1.seoul.agiliumtrade.ai",
    "mt-client-api-v1.singapore.agiliumtrade.ai",
]


def load_metaapi_cfg(cfg):
    m = cfg.get("metaapi", {}) or {}
    return {
        "enabled": bool(m.get("enabled", False)),
        "token": str(m.get("token", "") or os.getenv("METAAPI_TOKEN", "")),
        "account_id": str(m.get("account_id", "") or os.getenv("METAAPI_ACCOUNT_ID", "")),
        "symbol": str(m.get("symbol", "XAUUSD")),
        "magic": int(m.get("magic", 20260804) or 20260804),
        "risk_pct": float(m.get("risk_pct", 1.0)),
        "region": str(m.get("region", "") or ""),
    }


def _metaapi_req(cfg, method, path, body=None, timeout=30):
    """Gọi REST API MetaAPI — tự dò lần lượt các region cho tới khi tìm thấy account."""
    mc = load_metaapi_cfg(cfg)
    if not mc["token"]:
        raise ValueError("Thiếu METAAPI_TOKEN (tạo tại app.metaapi.cloud → API access → token)")
    # region tùy chọn (user điền trong config, hoặc auto-dò)
    regions = [f"mt-client-api-v1.{mc['region']}.agiliumtrade.ai"] if mc.get("region") else METAAPI_REGIONS
    headers = {"auth-token": mc["token"], "User-Agent": UA["User-Agent"]}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    last_err = None
    for domain in regions:
        try:
            url = f"https://{domain}{path}"
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ValueError("METAAPI_TOKEN không hợp lệ (401) — kiểm tra lại token")
            last_err = f"HTTP {e.code} @ {domain}"
        except Exception as e:
            last_err = f"{str(e)[:80]} @ {domain}"
    raise RuntimeError(f"Không tìm thấy account MetaAPI ở mọi region. {last_err} — "
                       f"kiểm tra: account đã deploy chưa? (app.metaapi.cloud → Accounts → state=Deployed)")


def metaapi_ensure_deployed(cfg, timeout=180):
    """Chờ tài khoản MetaAPI deployed (MT5 cloud sẵn sàng)."""
    mc = load_metaapi_cfg(cfg)
    if not mc["account_id"]:
        raise ValueError("Thiếu METAAPI_ACCOUNT_ID (id tài khoản đã tạo trong dashboard MetaAPI)")
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st = _metaapi_req(cfg, "GET", f"/v1/accounts/{mc['account_id']}/state")
            if st.get("deployed"):
                return True
            if st.get("error"):
                raise ValueError(f"Tài khoản MetaAPI lỗi: {st['error']}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        print("  ⏳ Đang chờ MetaAPI deploy MT5 cloud...")
        time.sleep(5)
    raise ValueError("Quá thời gian chờ MetaAPI deploy (kiểm tra tài khoản trong dashboard)")


def metaapi_account_info(cfg):
    mc = load_metaapi_cfg(cfg)
    d = _metaapi_req(cfg, "GET", f"/v1/accounts/{mc['account_id']}/account-information")
    return d


def metaapi_positions(cfg):
    mc = load_metaapi_cfg(cfg)
    d = _metaapi_req(cfg, "GET", f"/v1/accounts/{mc['account_id']}/positions")
    return d.get("positions", []) if isinstance(d, dict) else []


def metaapi_place_order(cfg, decision, price):
    """Đặt lệnh THẬT trên Exness demo qua MetaAPI. Trả (result, error)."""
    mc = load_metaapi_cfg(cfg)
    try:
        metaapi_ensure_deployed(cfg)
    except Exception as e:
        return None, str(e)
    sl_dist = abs(decision["sl"] - price)
    if sl_dist < 1e-9:
        return None, "Khoảng cách SL quá nhỏ"
    risk_amt = decision["risk"] * 1000
    # ~0.01 lot/1000$ cho XAUUSD (contract 100oz, $1 pip ≈ $1/lot... tính gần đúng)
    lot = round(max(0.01, min(risk_amt / (sl_dist * 100), 5.0)), 2)
    body = {
        "actionType": "ORDER_TYPE_BUY" if decision["action"] == "long" else "ORDER_TYPE_SELL",
        "symbol": mc["symbol"],
        "volume": lot,
        "stopLoss": decision["sl"],
        "takeProfit": decision["tp"],
        "comment": "AI-ARENA",
        "magic": mc["magic"],
        "typeTime": "GTC",
        "typeFilling": "ORDER_FILLING_IOC",
        "deviation": 20,
    }
    try:
        d = _metaapi_req(cfg, "POST", f"/v1/accounts/{mc['account_id']}/trade", body)
        if "code" in d:
            return None, f"MetaAPI từ chối: {d.get('message', d)}"
        return {"ticket": d.get("orderId", d.get("id", "?")), "lot": lot, "response": d}, None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')[:200]}"
    except Exception as e:
        return None, f"Lỗi MetaAPI: {str(e)[:150]}"


def metaapi_sync(cfg, st):
    """Đồng bộ balance + lệnh mở từ tài khoản MetaAPI."""
    try:
        info = metaapi_account_info(cfg)
        if info.get("balance") is not None:
            st["balance"] = float(info["balance"])
            st["start_balance"] = float(info.get("balance", st.get("start_balance", 1000)))
            st["equity_points"].append({"t": int(time.time() * 1000), "e": float(info.get("equity", info["balance"]))})
        positions = metaapi_positions(cfg)
        my_pos = [p for p in positions if p.get("magic") == load_metaapi_cfg(cfg)["magic"]]
        if my_pos:
            p = my_pos[0]
            st["position"] = {"id": p.get("id", 0), "dir": "long" if p.get("type") == "POSITION_TYPE_BUY" else "short",
                              "tf": "metaapi", "entry": float(p.get("openPrice", 0)),
                              "sl": float(p.get("stopLoss", 0) or 0), "tp": float(p.get("takeProfit", 0) or 0),
                              "rr": 0, "qty": float(p.get("volume", 0)), "risk_pct": 0.01,
                              "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": "Exness demo (MetaAPI)",
                              "llm": False, "exness": True}
        else:
            st["position"] = None
        return st, None
    except Exception as e:
        return st, str(e)


# =====================================================================
# 🏦 EXNESS — MetaTrader 5 (AUTO-TRADE, chỉ tài khoản DEMO)
# ---------------------------------------------------------------
# - Chỉ chạy trên WINDOWS (pip install MetaTrader5) — MT5 terminal phải cài
# - Lấy giá XAUUSD realtime từ Exness + AI Trader đặt lệnh THẬT trên demo
# - AN TOÀN: tự từ chối nếu phát hiện tài khoản REAL (trade_mode != DEMO)
# =====================================================================
def load_exness_cfg(cfg):
    e = cfg.get("exness", {}) or {}
    return {
        "enabled": bool(e.get("enabled", False)),
        "mode": str(e.get("mode", "demo")),
        "server": str(e.get("server", "")),
        "account": int(e.get("account", 0) or 0),
        "password": str(e.get("password", "") or os.getenv("EXNESS_PASSWORD", "")),
        "symbol": str(e.get("symbol", "XAUUSD")),
        "magic": int(e.get("magic", 20260804) or 20260804),
        "risk_pct": float(e.get("risk_pct", 1.0)),
        "mt5_path": str(e.get("mt5_path", "")),
    }


_mt5 = None
def _get_mt5():
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5 as mt5
            _mt5 = mt5
        except Exception:
            _mt5 = False
    return _mt5 or None


def mt5_connect(cfg):
    """Kết nối MT5 + đăng nhập Exness. Trả (mt5, error)."""
    mt5 = _get_mt5()
    if not mt5:
        return None, "MetaTrader5 chưa cài (chỉ Windows): pip install MetaTrader5"
    ecfg = load_exness_cfg(cfg)
    if not ecfg["enabled"]:
        return None, None
    try:
        if not mt5.initialize(path=ecfg["mt5_path"] or None):
            return None, f"Không khởi động MT5: {mt5.last_error()}"
        if ecfg["account"] and ecfg["password"]:
            if not mt5.login(ecfg["account"], ecfg["password"], server=ecfg["server"]):
                err = mt5.last_error()
                mt5.shutdown()
                return None, f"Đăng nhập Exness thất bại: {err}"
        acc = mt5.account_info()
        if acc is None:
            return None, "Không lấy được account_info"
        mode = int(getattr(acc, "trade_mode", 1))  # 0=DEMO, 1=CONTEST, 2=REAL
        if mode == 2:
            mt5.shutdown()
            return None, "🚫 TÀI KHOẢN REAL — từ chối giao dịch! Chỉ cho phép DEMO."
        return mt5, None
    except Exception as e:
        try:
            mt5.shutdown()
        except Exception:
            pass
        return None, f"Lỗi MT5: {str(e)[:120]}"


def mt5_symbol(mt5, cfg):
    """Tìm symbol vàng hợp lệ trên tài khoản. Trả (symbol, info)."""
    ecfg = load_exness_cfg(cfg)
    cands = [ecfg["symbol"], "XAUUSD", "XAUUSDm", "XAUUSD.a"]
    for s in cands:
        info = mt5.symbol_info(s)
        if info is not None:
            return s, info
    return None, None


def mt5_price(cfg):
    """Giá vàng XAUUSD realtime từ Exness (bid/ask). Trả (price, 'Exness MT5')."""
    mt5 = _get_mt5()
    if not mt5:
        return None, None
    try:
        if not mt5.initialize():
            return None, None
        sym, _ = mt5_symbol(mt5, cfg)
        if not sym:
            mt5.shutdown()
            return None, None
        tick = mt5.symbol_info_tick(sym)
        mt5.shutdown()
        if tick and tick.bid:
            return float(tick.bid), f"Exness MT5 · {sym}"
    except Exception:
        try:
            mt5.shutdown()
        except Exception:
            pass
    return None, None


def mt5_place_order(mt5, cfg, decision, price):
    """Đặt lệnh THẬT trên Exness demo. Trả (result_dict, error)."""
    ecfg = load_exness_cfg(cfg)
    sym, info = mt5_symbol(mt5, cfg)
    if not sym:
        return None, "Không tìm thấy symbol vàng"
    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        return None, "Không có tick giá"
    cs = float(getattr(info, "trade_contract_size", 100) or 100)
    sl_dist = abs(decision["sl"] - price)
    if sl_dist < 1e-9:
        return None, "Khoảng cách SL quá nhỏ"
    risk_amt = decision["risk"] * 1000  # vốn mô phỏng ~$1000 → lot theo risk%
    lot = risk_amt / (sl_dist * cs)
    lot = round(max(0.01, min(lot, 5.0)), 2)
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if decision["action"] == "long" else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if decision["action"] == "long" else tick.bid,
        "sl": decision["sl"],
        "tp": decision["tp"],
        "deviation": 20,
        "magic": ecfg["magic"],
        "comment": "AI-ARENA",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res is None:
        return None, f"order_send lỗi: {mt5.last_error()}"
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        return None, f"Lệnh bị từ chối: {res.retcode} {res.comment}"
    return {"ticket": int(res.order or res.deal or 0), "lot": lot, "price": float(res.price), "retcode": res.retcode}, None


def mt5_sync(cfg, mt5, st):
    """Đồng bộ trạng thái AI Trader với tài khoản Exness demo (balance + lệnh mở)."""
    ecfg = load_exness_cfg(cfg)
    acc = mt5.account_info()
    if acc is None:
        return st, "Không lấy được account_info"
    new_balance = float(acc.balance)
    # đồng bộ vốn
    st["balance"] = new_balance
    st["start_balance"] = float(getattr(acc, "balance", st.get("start_balance", 1000)))
    st["equity_points"].append({"t": int(time.time() * 1000), "e": float(acc.equity)})
    # lệnh đang mở (của magic này)
    positions = mt5.positions_get(symbol=ecfg["symbol"], magic=ecfg["magic"])
    if positions and len(positions) > 0:
        p = positions[0]
        st["position"] = {
            "id": int(p.ticket), "dir": "long" if p.type == 0 else "short",
            "tf": "exness", "entry": float(p.price_open),
            "sl": float(p.sl) if p.sl else 0, "tp": float(p.tp) if p.tp else 0,
            "rr": 0, "qty": float(p.volume), "risk_pct": ecfg["risk_pct"] / 100,
            "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": "Exness demo",
            "llm": False, "exness": True,
        }
    else:
        st["position"] = None
    return st, None


# =====================================================================
# 🔑 KIỂM TRA API KEY
# =====================================================================
def check_api_keys(cfg):
    """Gọi 1 request nhỏ tới từng provider để kiểm tra key còn hiệu lực không."""
    print("🔑 Kiểm tra 4 API key (không lộ key):")
    for name, env in (("OpenRouter", "OPENROUTER_API_KEY"), ("Groq", "GROQ_API_KEY"),
                      ("Cohere", "COHERE_API_KEY"), ("Gemini", "GEMINI_API_KEY")):
        key = cfg.get(env.lower(), "")
        if not key:
            print(f"  ❌ {name:<11} CHƯA đặt key ({env})")
            continue
        try:
            if env == "OPENROUTER_API_KEY":
                url = "https://openrouter.ai/api/v1/auth/key"
            elif env == "GROQ_API_KEY":
                url = "https://api.groq.com/openai/v1/models"
            elif env == "COHERE_API_KEY":
                url = "https://api.cohere.com/v1/models"
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key,
                                                   "User-Agent": UA["User-Agent"]})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
                n = len(d.get("data", [])) if isinstance(d.get("data"), list) else "OK"
                print(f"  ✅ {name:<11} key HOẠT ĐỘNG ({n} models)")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:150]
            print(f"  ❌ {name:<11} key LỖI: HTTP {e.code} — {body}")
        except Exception as e:
            print(f"  ❌ {name:<11} lỗi: {str(e)[:100]}")
    print("💡 Nếu Groq báo 403/1010 → key sai hoặc bị chặn: tạo key mới tại console.groq.com/keys")


# =====================================================================
# 📨 TELEGRAM
# =====================================================================
def send_telegram(text, cfg):
    """Gửi tin nhắn Telegram. Kiểm tra response thật (ok=true)."""
    token = cfg.get("trader", {}).get("telegram_token", "")
    chat = cfg.get("trader", {}).get("telegram_chat_id", "")
    if not token or not chat:
        print("⚠️ Telegram CHƯA cấu hình — thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
              "(thêm vào GitHub Secrets hoặc config.json).")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("ok"):
            return True
        print(f"⚠️ Telegram trả lỗi: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return False
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='ignore')[:200]
        if e.code == 401:
            print(f"⚠️ Telegram 401 — TOKEN BOT SAI trên GitHub Secret! (token hiện dài {len(token)} ký tự, "
                  f"bot token chuẩn ~46). Sửa: Settings → Secrets → TELEGRAM_BOT_TOKEN → dán lại token đúng từ BotFather.")
        else:
            print(f"⚠️ Telegram HTTP {e.code}: {detail}")
        return False
    except Exception as e:
        print(f"⚠️ Telegram lỗi: {e}")
        return False


def trader_summary(st, cfg, out_dir, force=False):
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
             f"⏱️ Số phiên: {st['sessions']}" + (" (tổng kết thủ công)" if force else ""),
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
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary_latest.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print("📊 TỔNG KẾT " + str(st["sessions"]) + " phiên — vốn $" + f"{st['balance']:,.2f}" + " · " +
          str(st["trades"]) + " lệnh · win " + f"{win_rate:.1f}%" + " · PF " +
          ("∞" if pf == float('inf') else f"{pf:.2f}"))
    sent = send_telegram(text, cfg)
    if sent:
        print("📨 Đã gửi tổng kết qua Telegram.")
    if not force:
        st["sessions"] = 0
    save_trader_state(st, out_dir)


def send_session_report(cfg, price, price_src, consensus, verdict, finals, mc, bt, trader_info=None, out_dir=None):
    """📊 Báo cáo MỖI PHIÊN — LUÔN ghi file (xem trên GitHub Pages), gửi Telegram nếu có token."""
    if not cfg.get("trader", {}).get("report_every_session", True):
        return False
    lines = ["📊 *XAU/USD AI DEBATE — BÁO CÁO PHIÊN*", "━━━━━━━━━━━━━━━━━",
             f"⏱️ {time.strftime('%d/%m/%Y %H:%M')} · {price_src}",
             f"💰 Giá: ${price:,.2f}", f"🧠 Đồng thuận: {consensus:+.3f} → {verdict}"]
    # chênh lệch spot vs futures (giải thích vì sao giá lệch sàn)
    if PRICE_INFO.get("spot") and PRICE_INFO.get("futures") and abs(PRICE_INFO["spot"] - PRICE_INFO["futures"]) > 1:
        lines.append(f"💱 Spot XAUUSD: ${PRICE_INFO['spot']:,.2f} · Futures GC=F: ${PRICE_INFO['futures']:,.2f} "
                     f"(chênh {PRICE_INFO['spot']-PRICE_INFO['futures']:+,.2f})")
    for f in finals:
        lines.append(f"  {f.get('icon', '🤖')} {f['title']}: {f['stance']:+.2f} (tự tin {f['conf']*100:.0f}%)")
    if mc:
        lines.append(f"🎯 Mục tiêu ${mc['target']:,.2f} · P10-P90 ${mc['p10']:,.2f} – ${mc['p90']:,.2f} · P(tăng) {mc['prob_up']*100:.0f}%")
    if trader_info:
        lines.append(f"💼 AI Trader: {trader_info}")
    # phân biệt rõ: hiệu suất AI Trader THẬT vs backtest heuristic (chiến lược mẫu)
    try:
        st_perf = load_trader_state(out_dir) if out_dir else None
        if st_perf:
            lines.append(trader_perf_line(st_perf))
    except Exception:
        pass
    if bt:
        lines.append(f"📈 Backtest heuristic {bt.get('tf', '')} (chiến lược mẫu): win {bt['win_rate']}% "
                     f"({bt['trades']} lệnh) · {bt['total_return_pct']:+.2f}%"
                     + (f" · bỏ qua {bt.get('skipped', 0)} tín hiệu bởi bộ lọc" if bt.get('filtered') else ""))
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append("_Bạn là người ra quyết định cuối cùng._")
    text = "\n".join(lines)
    # 1) LUÔN ghi file báo cáo phiên (dự phòng — xem được trên GitHub Pages)
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "latest_report.txt"), "w", encoding="utf-8") as f:
                f.write(text)
            print(f"📄 Đã lưu báo cáo phiên: {out_dir}/latest_report.txt (xem trên GitHub Pages)")
        except Exception as e:
            print(f"⚠️ Không lưu được file báo cáo: {e}")
    # 2) Gửi Telegram nếu có token
    token = cfg.get("trader", {}).get("telegram_token", "")
    chat = cfg.get("trader", {}).get("telegram_chat_id", "")
    if not token or not chat:
        print("⚠️ Telegram CHƯA cấu hình — thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (thêm vào GitHub Secrets).")
        return False
    ok = send_telegram(text, cfg)
    if ok:
        print("📨 Đã gửi báo cáo phiên qua Telegram.")
    return ok


# =====================================================================
# 🧠 BỘ NHỚ PHIÊN (sessions log) — AI nhớ các phiên trước
# =====================================================================
def load_sessions(out_dir, max_n=200):
    p = os.path.join(out_dir, "sessions_log.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            return s[-max_n:] if isinstance(s, list) else []
        except Exception:
            return []
    return []


def save_session(out_dir, record):
    os.makedirs(out_dir, exist_ok=True)
    sess = load_sessions(out_dir)
    sess.append(record)
    with open(os.path.join(out_dir, "sessions_log.json"), "w", encoding="utf-8") as f:
        json.dump(sess[-500:], f, ensure_ascii=False, indent=2)


def history_block(out_dir, n=6):
    """Tóm tắt các phiên gần đây + hiệu suất trader → đưa vào prompt cho AI."""
    lines = []
    sess = load_sessions(out_dir)[-n:]
    for s in sess:
        try:
            lines.append(f"• {s['time']} [{s['tf']}] giá ${s['price']:,.2f} · đồng thuận {s['consensus']:+.2f} ({s['verdict']})"
                         + (f" · trader {s['trader_action'].upper()}" if s.get('trader_action') else ""))
        except Exception:
            continue
    # hiệu suất trader
    try:
        st = load_trader_state(out_dir)
        if st.get("trades"):
            wr = st["wins"] / st["trades"] * 100
            lines.append(f"📈 Hiệu suất AI Trader: {st['trades']} lệnh · win {wr:.0f}% · "
                         f"P&L {st['total_pnl']:+,.2f}$ · vốn ${st['balance']:,.2f}")
    except Exception:
        pass
    return "\n".join(lines) if lines else ""


# =====================================================================
# 🎛️ LỆNH TELEGRAM — boss ra lệnh khung giao dịch, xem status...
#   VD: "trade 1h" · "trade 15p" · "trade 4h" · "trade 1d" · "status"
#   Poll mỗi lần chạy phiên (GitHub Actions) → áp dụng cho phiên đó
# =====================================================================
def telegram_poll_commands(cfg, out_dir):
    """Đọc tin nhắn Telegram (getUpdates) → xử lý lệnh → lưu trade_tf. Trả trade_tf mới (hoặc '')."""
    token = cfg.get("trader", {}).get("telegram_token", "")
    chat = cfg.get("trader", {}).get("telegram_chat_id", "")
    if not token or not chat:
        return ""
    tg_state = {"offset": 0, "trade_tf": ""}
    tg_path = os.path.join(out_dir, "tg_state.json")
    if os.path.exists(tg_path):
        try:
            with open(tg_path, encoding="utf-8") as f:
                tg_state = json.load(f)
        except Exception:
            pass
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates?offset={tg_state.get('offset', 0) + 1}&timeout=1"
        req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        updates = data.get("result", []) if data.get("ok") else []
        changed = False
        for u in updates:
            up_id = u.get("update_id")
            msg = (u.get("message") or {}).get("text", "") or ""
            msg_l = msg.lower().strip()
            tf = None
            if "trade" in msg_l or "khung" in msg_l or "giao dịch" in msg_l:
                if "15" in msg_l: tf = "15m"
                elif "4h" in msg_l or "4 giờ" in msg_l: tf = "4h"
                elif "1d" in msg_l or "1 ngày" in msg_l or "ngày" in msg_l: tf = "1D"
                elif "1h" in msg_l or "1 giờ" in msg_l or "giờ" in msg_l: tf = "1h"
            if tf:
                tg_state["trade_tf"] = tf
                changed = True
                send_telegram(f"✅ Đã đổi khung giao dịch sang **{TIMEFRAMES[tf]['label']}** — từ phiên tới AI Trader chỉ trade khung này.", cfg)
            elif msg_l in ("status", "trạng thái", "tình hình"):
                st = load_trader_state(out_dir)
                info = trader_status_line(st, st.get("balance", 0))
                tf_now = tg_state.get("trade_tf", "") or "1h"
                send_telegram(f"📊 *TRẠNG THÁI*\nKhung giao dịch: {tf_now}\n{info}\n"
                              f"Phiên đã chạy: {st.get('sessions', 0)}", cfg)
            elif "reset trader" in msg_l or "reset vốn" in msg_l:
                st = new_trader_state()
                save_trader_state(st, out_dir)
                send_telegram("↺ Đã reset vốn AI Trader về $1.000.", cfg)
            elif "stop" in msg_l or "dừng" in msg_l:
                tg_state["trade_tf"] = ""
                changed = True
                send_telegram("⏸️ Đã quay lại khung mặc định (1h).", cfg)
            tg_state["offset"] = max(tg_state.get("offset", 0), up_id)
        if changed or updates:
            with open(tg_path, "w", encoding="utf-8") as f:
                json.dump(tg_state, f, ensure_ascii=False, indent=2)
        return tg_state.get("trade_tf", "")
    except Exception as e:
        print(f"⚠️ Poll Telegram lỗi: {str(e)[:100]}")
        return tg_state.get("trade_tf", "")


# =====================================================================
# ĐIỀU PHỐI
# =====================================================================
def trader_step(cfg, args, ind, mc, consensus, verdict, finals, price, klines, out_dir):
    """AI TRADER CHUYÊN NGHIỆP — vòng lặp nối tiếp mỗi phiên:
    1) Đóng lệnh chạm SL/TP (chấm điểm + bài học)
    2) Kích hoạt lệnh chờ nếu giá chạm mốc / hủy nếu hết hạn
    3) Quyết định mới: vào ngay (market) hoặc đặt lệnh chờ (limit/stop) — độc lập với hội đồng
    4) Gửi Telegram mọi sự kiện
    """
    try:
        st = load_trader_state(out_dir)
        st["sessions"] = st.get("sessions", 0) + 1

        # 1) Đóng lệnh chạm SL/TP (tất cả lệnh mở) — cập nhật điểm + bài học
        closed_all = trader_check_position(st, klines, out_dir)
        trader_summary(st, cfg, out_dir)

        # 2) Xử lý lệnh chờ: kích hoạt / hết hạn
        pend_events = trader_process_pending(st, klines, out_dir)

        # 3) Khung theo lệnh Telegram
        cmd_tf = st.get("trade_tf", "")
        if cmd_tf and cmd_tf in TIMEFRAMES:
            print(f"🎛️ Khung giao dịch theo lệnh Telegram: {TIMEFRAMES[cmd_tf]['label']}")

        # 4) Quyết định mới nếu còn slot lệnh mở (tối đa 3)
        decided = None
        if len(st.get("positions") or []) < MAX_POSITIONS:
            decided = trader_llm_decision(cfg, consensus, verdict, finals, price, ind["atr"],
                                          ind["sup"], ind["res"], mc["target"], mc["p10"], mc["p90"],
                                          mc["prob_up"], st["balance"], st)
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
        for rep in closed_all:
            send_telegram(
                f"💼 *AI TRADER ĐÓNG LỆNH* {rep['dir'].upper()} {rep['tf']}\nKết quả: {rep['exit_reason']}\n"
                f"Entry ${rep['entry']:,.2f} → Exit ${rep['exit']:,.2f}\nP&L: {rep['pnl']:+,.2f}$ ({rep['pnl_pct']:+.2f}%)\n"
                f"Vốn: ${st['balance']:,.2f} · 🏆 Điểm: {st.get('trader_score', 1000):.0f}", cfg)
        for ev in pend_events:
            po = ev["order"]
            if ev["event"] == "activated":
                send_telegram(
                    f"⚡ *LỆNH CHỜ KÍCH HOẠT*: {po['dir'].upper()} {po['type'].upper()} @ ${po['trigger']:,.2f}\n"
                    f"SL ${po['sl']:,.2f} · TP ${po['tp']:,.2f} · RR 1:{po['rr']:.1f}", cfg)
            elif ev["event"] == "expired":
                send_telegram(f"🗑️ *LỆNH CHỜ HẾT HẠN*: {po['type'].upper()} @ ${po['trigger']:,.2f} (không chạm mốc)", cfg)
            elif ev["event"] == "cancelled_full":
                send_telegram(f"🚫 *LỆNH CHỜ BỊ HỦY* (đủ 3 lệnh mở): {po['type'].upper()} @ ${po['trigger']:,.2f}", cfg)
        if decided and decided.get("order_type", "market") != "market" and decided["action"] != "hold":
            send_telegram(
                f"📌 *AI TRADER ĐẶT LỆNH CHỜ*: {decided['action'].upper()} {decided['order_type'].upper()}\n"
                f"Chờ giá ${decided.get('trigger', 0):,.2f} · SL ${decided['sl']:,.2f} · TP ${decided['tp']:,.2f} · RR 1:{decided['rr']:.1f}", cfg)
    except Exception as e:
        print(f"⚠️ AI Trader lỗi (không làm hỏng phiên): {e}")


def run_once(cfg, args):
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    # 🎛️ Nhận lệnh Telegram (trade 1h / 15p / 4h / 1d / status...) → áp dụng khung cho phiên này
    cmd_tf = telegram_poll_commands(cfg, out_dir)
    tf_key = args.timeframe
    if cmd_tf and cmd_tf in TIMEFRAMES:
        tf_key = cmd_tf
        args.timeframe = cmd_tf
        print(f"🎛️ Nhận lệnh Telegram: chuyển khung phiên này sang {TIMEFRAMES[tf_key]['label']}")

    print("=" * 64)
    print(f"🥇 XAU/USD AI DEBATE ARENA — phiên {time.strftime('%H:%M:%S')}")
    print(f"Khung: {TIMEFRAMES[tf_key]['label']} · {args.rounds} vòng · {args.voters} cử tri · {args.paths} kịch bản")
    keys = []
    for name, k in (("OpenRouter", cfg["openrouter_api_key"]), ("Gemini", cfg["gemini_api_key"]),
                    ("Groq", cfg["groq_api_key"]), ("Cohere", cfg["cohere_api_key"])):
        if k:
            keys.append(name)
    print(f"API: {' + '.join(keys) if keys else 'CHẾ ĐỘ MẪU (chưa có key)'}")
    print("Định tuyến: " + " · ".join(f"{a['title']}→{agent_conf(cfg, a['key'])['provider']}/{agent_conf(cfg, a['key'])['model']}" for a in AGENTS))
    print("-" * 64)

    price, price_src = fetch_price(cfg)
    print(f"⚡ Giá realtime: ${price:,.2f} ({price_src})")
    klines, kline_src = fetch_klines(tf_key)
    ind = compute_indicators(klines, tf_key)
    print(f"📊 {len(klines)} nến ({kline_src}) — RSI {ind['rsi']:.1f} · vol {ind['vol']*100:.2f}%/nến · {ind['trend']}")

    hist = history_block(out_dir)  # 🧠 AI nhớ các phiên trước
    snap = market_snapshot(cfg, price, price_src, klines, ind, tf_key, args.context, history=hist)
    if hist:
        print("🧠 Đã đưa lịch sử các phiên gần đây vào bối cảnh cho hội đồng AI.")
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

    data = build_result(cfg, args, price, price_src, klines, kline_src, ind,
                        timeline, finals, consensus, verdict, crowd, mc)
    with open(os.path.join(out_dir, "simulation_latest.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    generate_dashboard_html(data, os.path.join(out_dir, "dashboard.html"))
    if bt:
        save_backtest_history({**bt, "consensus": consensus, "verdict": verdict, "target": mc["target"]}, out_dir)

    # 🧠 Ghi nhớ phiên vào lịch sử (AI nhớ các phiên sau)
    try:
        st_mem = load_trader_state(out_dir)
        save_session(out_dir, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "tf": tf_key,
            "price": price, "price_src": price_src,
            "consensus": consensus, "verdict": verdict,
            "target": mc["target"], "p10": mc["p10"], "p90": mc["p90"],
            "prob_up": mc["prob_up"],
            "trader_action": (st_mem.get("positions") or [])[0]["dir"] if st_mem.get("positions") else
                             ("hold" if not st_mem.get("history") else "closed"),
        })
    except Exception:
        pass

    if not args.no_trader:
        trader_step(cfg, args, ind, mc, consensus, verdict, finals, price, klines, out_dir)

    # 📨 Báo cáo phiên qua Telegram (không làm hỏng phiên nếu lỗi)
    try:
        st = load_trader_state(out_dir)
        send_session_report(cfg, price, price_src, consensus, verdict, finals, mc, bt,
                            trader_status_line(st, price), out_dir)
    except Exception as e:
        print(f"⚠️ Gửi báo cáo Telegram lỗi: {e}")
    return data


def main():
    ap = argparse.ArgumentParser(description="XAU/USD AI Debate Arena — backend kiểm tra toàn diện")
    ap.add_argument("--timeframe", choices=list(TIMEFRAMES), default="1h")
    ap.add_argument("--rounds", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--voters", type=int, default=80)
    ap.add_argument("--paths", type=int, default=300)
    ap.add_argument("--context", default="")
    ap.add_argument("--watch", type=int, default=0, help="Tự chạy lại mỗi N phút (0 = tắt)")
    ap.add_argument("--serve", type=int, default=0, help="Web server xem dashboard")
    ap.add_argument("--out", default="output")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-trader", action="store_true")
    ap.add_argument("--force-summary", action="store_true")
    ap.add_argument("--test-telegram", action="store_true", help="Gửi tin test qua Telegram rồi thoát")
    ap.add_argument("--test-keys", action="store_true", help="Kiểm tra nhanh 4 API key rồi thoát")
    args = ap.parse_args()
    cfg = load_config()

    if args.test_keys:
        check_api_keys(cfg)
        return

    if args.test_telegram:
        ok = send_telegram("✅ *Kết nối Telegram thành công!*\nXAU/USD AI Debate Arena sẽ gửi báo cáo mỗi phiên, "
                           "báo cáo lệnh và tổng kết định kỳ cho bạn.", cfg)
        print("✅ Đã gửi tin test thành công." if ok else "❌ Gửi thất bại — kiểm tra TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
        return

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
