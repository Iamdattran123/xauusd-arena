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
    COHERE_API_KEY       (Mới thêm)

Cấu hình mặc định (hardcode, có thể ghi đè bằng config.json):
    Macro_Analyst      -> openrouter / qwen/qwen3-32b
    Technical_Analyst  -> openrouter / deepseek/deepseek-v4-flash-0731
    Institutional_Whale-> cohere     / command-r-plus-08-2024
    Retail_Crowd       -> groq       / llama-3.3-70b-versatile
    trader             -> openrouter / deepseek/deepseek-v4-flash-0731
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
# CẤU HÌNH MẶC ĐỊNH
# ---------------------------------------------------------------------
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
    "telegram_token": "",
    "telegram_chat_id": "",
    "report_every_session": True,
}

# ---------------------------------------------------------------------
# NHÀ CUNG CẤP — endpoint + tên biến môi trường lấy key
# ---------------------------------------------------------------------
PROVIDER_META = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "env": "OPENROUTER_API_KEY",
        "style": "openai",
        "extra_headers": {"HTTP-Referer": "https://localhost", "X-Title": "XAUUSD AI Debate Arena"},
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "env": "GROQ_API_KEY",
        "style": "openai",
        "extra_headers": {},
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/models",
        "env": "GEMINI_API_KEY",
        "style": "google",
        "extra_headers": {},
    },
    "cohere": {
        "url": "https://api.cohere.com/v2/chat",
        "env": "COHERE_API_KEY",
        "style": "cohere",
        "extra_headers": {"X-Client-Name": "xauusd-arena"},
    },
}

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
        1: (0.55, 0.80, "DXY suy yếu và kỳ vọng Fed hạ lãi suất củng cố vai trò trú ẩn của vàng."),
        2: ("Quan điểm kỹ thuật quá nhấn mạnh RSI ngắn hạn.", 0.52, 0.82, "Giữ quan điểm vĩ mô tích cực."),
        3: (0.50, 0.82, "Giữ quan điểm vĩ mô tích cực."),
    },
    "Technical_Analyst": {
        1: (-0.20, 0.75, "RSI quá mua, MACD phân kỳ âm, xác suất điều chỉnh cao."),
        2: ("Lập luận vĩ mô đúng nhưng thiếu định lượng ngắn hạn.", -0.10, 0.72, "Thận trọng ngắn hạn chờ phá kháng cự."),
        3: (-0.10, 0.72, "Thận trọng ngắn hạn chờ phá kháng cự."),
    },
    "Institutional_Whale": {
        1: (0.40, 0.90, "Dòng vốn ETF chuyển ròng dương tạo hỗ trợ mua mạnh."),
        2: ("Kỹ thuật chỉ cho lướt sóng, dòng tiền tổ chức cầm dài hạn.", 0.45, 0.88, "Điều chỉnh là cơ hội mua thêm."),
        3: (0.45, 0.88, "Điều chỉnh là cơ hội mua thêm."),
    },
    "Retail_Crowd": {
        1: (0.10, 0.60, "Nhỏ lẻ phân hóa, một bộ phận FOMO, một bộ phận chờ bắt đáy."),
        2: ("Cá mập bỏ qua rủi ro thanh khoản.", 0.05, 0.58, "Giữ tâm lý trung lập chờ tín hiệu."),
        3: (0.05, 0.58, "Giữ tâm lý trung lập chờ tín hiệu."),
    },
}

def load_config():
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
                if user.get(k): cfg[k] = user[k]
            if isinstance(user.get("models"), dict):
                for key, val in user["models"].items():
                    if isinstance(val, str):
                        cfg["models"][key] = {"provider": "openrouter", "model": val}
                    elif isinstance(val, dict) and val.get("model"):
                        cfg["models"][key] = {"provider": val.get("provider", "openrouter"), "model": val["model"]}
            if isinstance(user.get("trader"), dict):
                cfg["trader"].update(user["trader"])
        except: pass
    if os.getenv("TELEGRAM_BOT_TOKEN"): cfg["trader"]["telegram_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"): cfg["trader"]["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
    cfg["trader"].setdefault("report_every_session", True)
    return cfg

def agent_conf(cfg, key):
    c = cfg["models"].get(key, {})
    if isinstance(c, str): return {"provider": "openrouter", "model": c}
    return {"provider": c.get("provider", "openrouter"), "model": c.get("model", "")}

# =====================================================================
# DỮ LIỆU REALTIME
# =====================================================================
def http_json(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))

def fetch_price():
    try:
        d = http_json("https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=1d", timeout=6)
        return float(d["chart"]["result"][0]["meta"]["regularMarketPrice"]), "Yahoo Finance · GC=F (COMEX)"
    except: pass
    try:
        d = http_json("https://api.gold-api.com/price/XAU", timeout=6)
        return float(d["price"]), "gold-api.com · XAU spot"
    except: pass
    try:
        d = http_json("https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT", timeout=6)
        return float(d["price"]), "Binance · PAXG"
    except:
        return 4050.0, "Giá mặc định (offline)"

def fetch_klines(tf_key):
    cfg = TIMEFRAMES[tf_key]
    try:
        d = http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval={cfg['yahoo_interval']}&range={cfg['yahoo_range']}", timeout=10)
        r = d["chart"]["result"][0]
        ts, q = r["timestamp"], r["indicators"]["quote"][0]
        raw = [{"t": t * 1000, "o": o, "h": h, "l": lo, "c": c} for t, o, h, lo, c in zip(ts, q["open"], q["high"], q["low"], q["close"]) if o is not None and c is not None]
        if cfg["resample"] > 1: raw = resample(raw, cfg["step_min"] * 60000)
        raw = raw[:-1]
        if len(raw) > 10: return raw, f"Yahoo · GC=F {cfg['label']}"
    except: pass
    try:
        d = http_json(f"https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval={cfg['binance']}&limit=260", timeout=8)
        raw = [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4])} for k in d]
        if len(raw) > 10: return raw, f"Binance · PAXGUSDT {cfg['label']}"
    except: pass
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
        h, l = max(o, c) * (1 + abs(random.gauss(0, vol * 0.5))), min(o, c) * (1 - abs(random.gauss(0, vol * 0.5)))
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
    g = l = 0.0; out = [50.0]
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        if i <= p:
            g += max(d, 0) / p; l += max(-d, 0) / p
            if i == p: out.append(100 - 100 / (1 + g / (l if l else 1e-9)))
        else:
            g = (g * (p - 1) + max(d, 0)) / p; l = (l * (p - 1) + max(-d, 0)) / p
            out.append(100 - 100 / (1 + g / (l if l else 1e-9)))
    return out

def calc_macd(c):
    e12, e26 = calc_ema(c, 12), calc_ema(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = calc_ema(macd, 9)
    return macd, sig, [m - s for m, s in zip(macd, sig)]

def calc_atr(k, p=14):
    trs = [max(k[i]["h"] - k[i]["l"], abs(k[i]["h"] - k[i - 1]["c"]), abs(k[i]["l"] - k[i - 1]["c"])) for i in range(1, len(k))]
    s = trs[-p:]
    return sum(s) / len(s) if s else 0

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
    return {"rsi": rsi[-1], "ema20": ema20, "ema50": ema50, "macd_hist": hist[-1], "atr": atr, "res": res, "sup": sup, "vol": vol, "momentum": momentum, "last_ret_pct": last_ret * 100, "trend": "TĂNG" if ema20 > ema50 else "GIẢM", "n": n}

# =====================================================================
# TÁC NHÂN AI & CALL_LLM
# =====================================================================
def market_snapshot(cfg, price, price_src, klines, ind, tf_key, context=""):
    tf = TIMEFRAMES[tf_key]
    L = [f"Giá hiện tại: ${price:,.2f}/oz (nguồn: {price_src}).", f"Khung {tf['label']}: dự báo {tf['steps']} nến ≈ {tf['label']} tới."]
    if ind:
        L.append(f"Dữ liệu {ind['n']} nến gần nhất — RSI(14): {ind['rsi']:.1f} | EMA20: ${ind['ema20']:,.2f} | EMA50: ${ind['ema50']:,.2f} | MACD hist: {ind['macd_hist']:+.2f} | ATR: ${ind['atr']:,.2f} | Xu hướng EMA20/50: {ind['trend']}.")
        L.append(f"Hỗ trợ gần nhất: ${ind['sup']:,.2f} | Kháng cự gần nhất: ${ind['res']:,.2f} | Biến động thực tế: {ind['vol']*100:.2f}%/nến | Nến gần nhất: {ind['last_ret_pct']:+.2f}%.")
    if context: L.append(f"Bối cảnh bổ sung từ người điều hành: {context}")
    return "\n".join(L)

def build_prompt(agent, round_no, prev, snap):
    persona = agent["persona"]
    NO_FAB = '\nTUYỆT ĐỐI chỉ dùng các con số, mức giá, vùng hỗ trợ/kháng cự CÓ TRONG dữ liệu được cung cấp ở trên. KHÔNG bịa ra mức giá, chỉ báo hay sự kiện không có trong dữ liệu.\n'
    if round_no == 1:
        return f"""{persona}\nĐọc kỹ dữ liệu thị trường sau và đưa ra LẬP TRƯỜNG BAN ĐẦU của bạn về giá vàng trong thời gian tới:\n---\n{snap}\n---\n{NO_FAB}\nTrả về DUY NHẤT một JSON hợp lệ (không markdown, không giải thích thêm):\n{{"sentiment_score": <số từ -1.0 đến +1.0>, "confidence": <số 0.0-1.0>, "reasoning": "<luận điểm chính bằng tiếng Việt, 2-3 câu>"}}"""
    if round_no == 2:
        others = "\n".join(f"• {x['title']} (tâm lý {x['stance']:+.2f}): {x.get('reason','')}" for x in prev if x["key"] != agent["key"])
        mine = next((x.get("reason","") for x in prev if x["key"] == agent["key"]), "")
        return f"""{persona}\nLập trường ban đầu của bạn: {mine}\nĐây là lập trường của các chuyên gia khác trong HỘI ĐỒNG:\n{others}\nNhiệm vụ PHẢN BIỆN: chỉ ra lỗ hổng logic/luận điểm yếu trong các quan điểm trên, rồi CẬP NHẬT tâm lý của bạn.\n{NO_FAB}\nTrả về DUY NHẤT một JSON hợp lệ:\n{{"critique": "<phản biện ngắn gọn>", "revised_sentiment": <số -1.0 đến +1.0>, "confidence": <số 0.0-1.0>, "reasoning": "<lập trường sau phản biện, 2 câu>"}}"""
    
    myEntry = next((x for x in prev if x["key"] == agent["key"]), None)
    mine = myEntry.get("reason", "") if myEntry else ""
    my_stance = myEntry.get("stance", 0) if myEntry else 0
    critiques = "\n".join(f"• Phản biện của {x['title']}: {x.get('critique') or '(không có)'}" for x in prev if x["key"] != agent["key"])
    return f"""{persona}\nLập trường hiện tại của bạn: {mine} (tâm lý {my_stance:+.2f})\nCác phản biện dành cho bạn:\n{critiques}\nNhiệm vụ ĐIỀU CHỈNH CUỐI CÙNG: cân nhắc các phản biện, đưa ra LẬP TRƯỜNG CUỐI CÙNG.\n{NO_FAB}\nTrả về DUY NHẤT một JSON hợp lệ:\n{{"sentiment_score": <số -1.0 đến +1.0>, "confidence": <số 0.0-1.0>, "reasoning": "<lập trường cuối cùng, 2-3 câu>"}}"""

def extract_json(text):
    s = str(text or "")
    for tag in ("<think>", "</think>"): s = s.replace(tag, "")
    s = s.replace("```json", "").replace("```", "")
    try: return json.loads(s)
    except: pass
    for i in range(len(s)):
        if s[i] == "{":
            depth = 0
            for j in range(i, len(s)):
                if s[j] == "{": depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        try: return json.loads(s[i:j + 1])
                        except: pass
                        break
    return None

def gfloat(obj, keys, default=0.0):
    for k in keys:
        v = obj.get(k)
        if isinstance(v, (int, float)) and math.isfinite(v): return float(v)
    return default

def _http_post(url, payload, headers, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {body[:200]}")

def call_llm(role, prompt, cfg, provider=None, model=None):
    if role == "trader":
        conf = cfg["trader"]
        if isinstance(conf, str): conf = {"provider": "openrouter", "model": conf}
        provider = provider or conf.get("provider", "openrouter")
        model = model or conf.get("model", "")
    else:
        conf = agent_conf(cfg, role)
        provider = provider or conf["provider"]
        model = model or conf["model"]

    meta = PROVIDER_META.get(provider)
    if not meta: raise ValueError(f"Provider không hợp lệ: {provider}")
    key = cfg.get(meta["env"].lower()) or os.getenv(meta["env"], "")
    if not key: raise ValueError(f"Thiếu API key cho {provider}")

    if meta["style"] == "google":
        url = f"{meta['base']}/{model}:generateContent?key={key}"
        data = _http_post(url, {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": 700}}, {"Content-Type": "application/json", **meta["extra_headers"]})
        text = "".join(p.get("text", "") for p in data.get("candidates", [{}])[0].get("content", {}).get("parts", []))
    elif meta["style"] == "cohere":
        url = meta["url"]
        data = _http_post(url, {"model": model, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}], "temperature": 0.4, "max_tokens": 700}, {"Content-Type": "application/json", "Authorization": "Bearer " + key, **meta["extra_headers"]})
        text = "".join(c.get("text", "") for c in data.get("message", {}).get("content", []) if isinstance(c, dict))
    else:
        url = meta["url"]
        data = _http_post(url, {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.4, "max_tokens": 700}, {"Content-Type": "application/json", "Authorization": "Bearer " + key, **meta["extra_headers"]})
        msg = data.get("choices", [{}])[0].get("message", {})
        text = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""

    if not text: raise ValueError(f"{provider} trả về rỗng")
    return {"text": text, "prompt_tokens": 0, "completion_tokens": 0, "model": model, "provider": provider}

def call_agent_json(agent, round_no, prev, snap, cfg):
    role = agent["key"]
    chosen = agent_conf(cfg, role)
    chain = [(chosen["provider"], chosen["model"])]
    for p, m in FALLBACK_CHAIN.get(chosen["provider"], []):
        if (p, m) not in chain: chain.append((p, m))
    prompt = build_prompt(agent, round_no, prev, snap)
    last_err = ""
    for ci, (provider, model) in enumerate(chain):
        try:
            res = call_llm(role, prompt, cfg, provider=provider, model=model)
            j = extract_json(res["text"])
            if not j:
                last_err = "JSON không hợp lệ"
                continue
            if round_no in (1, 3): j.setdefault("confidence", 0.7)
            else:
                j.setdefault("confidence", 0.7)
                if not j.get("revised_sentiment") and j.get("sentiment_score") is not None: j["revised_sentiment"] = j["sentiment_score"]
            return {"json": j, "model": f"{provider}/{model}", "provider": provider}
        except Exception as e:
            last_err = str(e)
            if ci < len(chain) - 1: print(f"  🔄 {agent['title']} lỗi {last_err[:50]} → thử {chain[ci+1][1]}...")
    raise RuntimeError(last_err or "Lỗi LLM")

def run_debate(cfg, snap, rounds):
    timeline, prev = [], None
    any_key = cfg["openrouter_api_key"] or cfg["gemini_api_key"] or cfg["groq_api_key"] or cfg["cohere_api_key"]
    for r in range(1, rounds + 1):
        entries = []
        for agent in AGENTS:
            conf = agent_conf(cfg, agent["key"])
            print(f"  🤖 [Vòng {r}] {agent['title']}... ({conf['provider']}/{conf['model']})")
            if not any_key:
                time.sleep(0.1)
                m = MOCK[agent["key"]][r]
                entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"], "stance": m[1] if r==2 else m[0], "conf": m[2] if r==2 else m[1], "reason": m[3] if r==2 else m[2], "critique": m[0] if r==2 else None, "model": "(mẫu)", "fallback": True})
                continue
            try:
                out = call_agent_json(agent, r, prev, snap, cfg)
                j = out["json"]
                stance = max(-1.0, min(1.0, gfloat(j, ["revised_sentiment", "sentiment_score", "sentiment"])))
                conf = max(0.0, min(1.0, gfloat(j, ["confidence"], 0.7)))
                reason = str(j.get("reasoning", j.get("reason", "")))[:600]
                critique = str(j.get("critique", ""))[:500] if r==2 else None
                entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"], "stance": stance, "conf": conf, "reason": reason, "critique": critique, "model": out["model"], "fallback": False})
            except Exception as e:
                print(f"  ⚠️ {agent['title']} dùng dữ liệu dự phòng: {str(e)[:100]}")
                m = MOCK[agent["key"]][r]
                entries.append({"key": agent["key"], "title": agent["title"], "icon": agent["icon"], "stance": m[1] if r==2 else m[0], "conf": m[2] if r==2 else m[1], "reason": m[3] if r==2 else m[2], "critique": m[0] if r==2 else None, "model": "(mẫu)", "fallback": True})
        ordered = [next(x for x in entries if x["key"] == a["key"]) for a in AGENTS]
        timeline.append({"round": r, "entries": ordered})
        prev = [{k: x.get(k) for k in ("key", "title", "stance", "conf", "reason", "critique")} for x in ordered]
    return timeline, prev

# =====================================================================
# TÍNH TOÁN & BACKTEST
# =====================================================================
def compute_consensus(finals, tf_key):
    w = TIMEFRAMES[tf_key]["weights"]
    num = den = 0.0
    for f in finals:
        eff = w[f["key"]] * (0.5 + f["conf"] / 2)
        num += eff * f["stance"]
        den += eff
    c = num / den if den else 0
    v = "MUA MẠNH 📈" if c >= 0.3 else "MUA NHẸ 📈" if c >= 0.1 else "TRUNG LẬP ↔️" if c > -0.1 else "BÁN NHẸ 📉" if c > -0.3 else "BÁN MẠNH 📉"
    return c, v

def simulate_crowd(finals, n_voters, momentum, seed=None):
    rng = np.random.default_rng(seed)
    mb = max(-0.4, min(0.4, momentum * 0.35 + rng.normal(0, 0.05)))
    stances = np.array([f["stance"] for f in finals])
    confs = np.array([f["conf"] for f in finals])
    biases = np.clip(mb + rng.normal(0, 0.35, n_voters), -1, 1)
    pers = np.clip(confs[None, :] * (1 - 0.85 * np.abs(stances[None, :] - biases[:, None])) + rng.normal(0, 0.12, (n_voters, len(finals))), 0, 1.5)
    total = pers.sum(axis=1)
    pos = np.where(total > 0, (pers * stances[None, :]).sum(axis=1) / total, 0)
    bull, bear = int((pos > 0.12).sum()), int((pos < -0.12).sum())
    winners = pers.argmax(axis=1)
    return {"n_voters": n_voters, "bull": bull, "neu": n_voters - bull - bear, "bear": bear, "crowd_mean": float(pos.mean()), "votes": {f["key"]: int((winners == i).sum()) for i, f in enumerate(finals)}, "momentum": mb}

def run_monte_carlo(start, steps, vol, consensus, momentum, paths, seed=None):
    rng = np.random.default_rng(seed)
    drift = consensus * vol * 0.45 + momentum * vol * 0.10
    W = rng.normal(0, 1, (paths, steps))
    log_paths = np.zeros((paths, steps + 1))
    log_paths[:, 1:] = math.log(start) + np.cumsum((drift - 0.5 * vol * vol) + vol * W, axis=1)
    paths_mat = np.exp(log_paths)
    mean = paths_mat.mean(axis=0)
    return {"target": float(mean[-1]), "p10": float(np.percentile(paths_mat, 10, axis=0)[-1]), "p50": float(np.percentile(paths_mat, 50, axis=0)[-1]), "p90": float(np.percentile(paths_mat, 90, axis=0)[-1]), "prob_up": float((paths_mat[:, -1] > start).mean()), "drift": drift, "rows": [{"mean": float(mean[i]), "p10": float(np.percentile(paths_mat, 10, axis=0)[i]), "p50": float(np.percentile(paths_mat, 50, axis=0)[i]), "p90": float(np.percentile(paths_mat, 90, axis=0)[i])} for i in range(steps + 1)]}

def run_backtest(klines, tf_key):
    closes = [k["c"] for k in klines]
    n, steps = len(closes), TIMEFRAMES[tf_key]["steps"]
    if n < 40 + steps: return None
    equity, trades, wins, gross_win, gross_loss, peak, max_dd = 1.0, 0, 0, 0.0, 0.0, 1.0, 0.0
    for i in range(40, n - steps):
        sl, ks = closes[:i + 1], klines[:i + 1]
        score = 0.30 if calc_ema(sl, 20)[-1] > calc_ema(sl, 50)[-1] else -0.30
        score += max(-0.2, min(0.2, (50 - calc_rsi(sl)[-1]) / 30 * 0.2))
        score += max(-0.25, min(0.25, calc_macd(sl)[2][-1] / (calc_atr(ks) or 1e-9) * 0.25))
        score += max(-0.15, min(0.15, (sl[-1] / sl[-2] - 1 if len(sl) > 1 else 0) * 20))
        if abs(score) < 0.15: continue
        d = 1 if score > 0 else -1
        ret = (closes[i + steps] / closes[i] - 1) * d
        trades += 1
        if ret > 0: wins += 1; gross_win += ret
        else: gross_loss += -ret
        equity *= (1 + ret); peak = max(peak, equity); max_dd = max(max_dd, (peak - equity) / peak)
    if trades == 0: return None
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "tf": tf_key, "trades": trades, "win_rate": round(wins / trades * 100, 1), "total_return_pct": round((equity - 1) * 100, 2), "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None, "max_drawdown_pct": round(max_dd * 100, 2)}

# =====================================================================
# AI TRADER & TELEGRAM
# =====================================================================
def load_trader_state(out_dir):
    p = os.path.join(out_dir, "trader_state.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f: st = json.load(f)
            if "balance" in st: return st
        except: pass
    return {"balance": 1000.0, "start_balance": 1000.0, "peak": 1000.0, "max_dd": 0.0, "position": None, "history": [], "trades": 0, "wins": 0, "total_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0, "sessions": 0, "equity_points": [{"t": int(time.time() * 1000), "e": 1000.0}]}

def save_trader_state(st, out_dir):
    with open(os.path.join(out_dir, "trader_state.json"), "w", encoding="utf-8") as f: json.dump(st, f, ensure_ascii=False, indent=2)

def trader_heuristic(consensus, prob_up, price, atr, target):
    action, sl, tp, tf = "hold", None, None, "1h"
    if consensus >= 0.15 and prob_up > 0.52:
        action, sl, tp = "long", price - max(1.5 * atr, price * 0.003), target if target > price else price + 2 * max(1.5 * atr, price * 0.003)
        if (tp - price) / (price - sl) < 1.2: tp = price + 2 * (price - sl)
    elif consensus <= -0.15 and prob_up < 0.48:
        action, sl, tp = "short", price + max(1.5 * atr, price * 0.003), target if target < price else price - 2 * max(1.5 * atr, price * 0.003)
        if (price - tp) / (sl - price) < 1.2: tp = price - 2 * (sl - price)
    rr = (tp - price) / (price - sl) if action == "long" else (price - tp) / (sl - price) if action == "short" else 0
    return {"action": action, "tf": "4h" if abs(consensus)>=0.25 else "1h", "sl": sl, "tp": tp, "risk": 0.01, "rr": rr, "reason": "Đứng ngoài." if action == "hold" else f"Tự quyết {action.upper()}.", "llm": False}

def trader_llm_decision(cfg, consensus, verdict, finals, price, atr, sup, res, target, p10, p90, prob_up, balance, position):
    chosen = cfg["trader"]
    if isinstance(chosen, str): chosen = {"provider": "openrouter", "model": chosen}
    chain = [(chosen.get("provider", "openrouter"), chosen.get("model", ""))]
    for p, m in FALLBACK_CHAIN.get(chosen.get("provider", "openrouter"), []):
        if (p, m) not in chain: chain.append((p, m))
    if not (cfg["openrouter_api_key"] or cfg["gemini_api_key"] or cfg["groq_api_key"]): return None
    
    # LỖI CHÍNH NẰM Ở ĐÂY: DÙNG .GET() ĐỂ TRÁNH SẬP CODE
    panel = "\n".join(
        f"• {f.get('title', 'Unknown')}: {f.get('stance', 0):+.2f} (tự tin {f.get('conf', 0)*100:.0f}%) — {f.get('reason', f.get('reasoning', 'API lỗi, không có dữ liệu'))}" 
        if isinstance(f, dict) else "• Lỗi lấy dữ liệu từ Agent"
        for f in finals
    )
    pos = f" · ĐANG CÓ LỆNH: {position['dir']} entry ${position['entry']:,.2f}" if position else ""
    prompt = f"""Bạn là AI TRADER. Đọc phán quyết:\n{panel}\nĐồng thuận: {consensus:+.3f}\nThị trường: Giá ${price:,.2f} | P(tăng) {prob_up*100:.0f}%\nVốn: ${balance:,.2f}{pos}\nRa quyết định giao dịch (Ưu tiên bảo toàn vốn). Trả JSON: {{"action": "long|short|hold", "timeframe": "1h", "sl": <số>, "tp": <số>, "risk_pct": 1.0, "reason": "lý do"}}"""
    
    for provider, model in chain:
        try:
            res = call_llm("trader", prompt, cfg, provider=provider, model=model)
            j = extract_json(res["text"])
            if not j or str(j.get("action", "")).lower() not in ("long", "short", "hold"): continue
            if j["action"] == "hold": return {"action": "hold", "tf": "1h", "sl": None, "tp": None, "risk": 0, "rr": 0, "reason": str(j.get("reason", ""))[:500], "llm": True}
            sl, tp, risk = float(j.get("sl", 0)), float(j.get("tp", 0)), max(0.5, min(3.0, float(j.get("risk_pct", 1.0)))) / 100
            if (j["action"] == "long" and sl < price < tp) or (j["action"] == "short" and tp < price < sl):
                rr = (tp - price) / (price - sl) if j["action"] == "long" else (price - tp) / (sl - price)
                return {"action": j["action"], "tf": str(j.get("timeframe", "1h")), "sl": sl, "tp": tp, "risk": risk, "rr": max(0.1, rr), "reason": str(j.get("reason", ""))[:500], "llm": True}
        except: pass
    return None

def send_telegram(text, cfg):
    token = cfg["trader"].get("telegram_token", "")
    chat = cfg["trader"].get("telegram_chat_id", "")
    if not token or not chat: return False
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode("utf-8"), headers={"Content-Type": "application/json"}), timeout=10)
        return True
    except Exception as e:
        print(f"⚠️ Telegram lỗi: {e}")
        return False

def trader_step(cfg, args, ind, mc, consensus, verdict, finals, price, klines, out_dir):
    st = load_trader_state(out_dir)
    st["sessions"] += 1
    rep = None
    if st.get("position"):
        p, last = st["position"], klines[-1]
        exit_p = p["sl"] if (p["dir"]=="long" and last["l"]<=p["sl"]) or (p["dir"]=="short" and last["h"]>=p["sl"]) else p["tp"] if (p["dir"]=="long" and last["h"]>=p["tp"]) or (p["dir"]=="short" and last["l"]<=p["tp"]) else None
        if exit_p:
            pnl = (exit_p - p["entry"]) * (1 if p["dir"]=="long" else -1) * p["qty"]
            st["balance"] += pnl; st["peak"] = max(st["peak"], st["balance"]); st["max_dd"] = max(st["max_dd"], (st["peak"] - st["balance"]) / st["peak"] * 100); st["trades"] += 1
            if pnl > 0: st["wins"] += 1; st["gross_win"] += pnl
            else: st["gross_loss"] += -pnl
            st["total_pnl"] += pnl; st["position"] = None
            rep = {**p, "exit": exit_p, "pnl": pnl, "pnl_pct": (exit_p/p["entry"]-1)*(1 if p["dir"]=="long" else -1)*100, "exit_reason": "chạm SL/TP"}
            st["history"].insert(0, rep)
    
    if not st.get("position"):
        decision = trader_llm_decision(cfg, consensus, verdict, finals, price, ind["atr"], ind["sup"], ind["res"], mc["target"], mc["p10"], mc["p90"], mc["prob_up"], st["balance"], st.get("position")) or trader_heuristic(consensus, mc["prob_up"], price, ind["atr"], mc["target"])
        if decision["action"] != "hold" and abs(decision["sl"] - price) > 1e-9:
            qty = (st["balance"] * decision["risk"]) / abs(decision["sl"] - price)
            if qty * price <= st["balance"] * 20:
                st["position"] = {"id": int(time.time()*1000), "dir": decision["action"], "tf": decision["tf"], "entry": price, "sl": decision["sl"], "tp": decision["tp"], "rr": decision["rr"], "qty": qty, "risk_pct": decision["risk"], "reason": decision["reason"]}
    save_trader_state(st, out_dir)
    
    if cfg["trader"].get("report_every_session", True):
        lines = [f"📊 *XAU/USD BÁO CÁO PHIÊN*\n💰 Giá: ${price:,.2f}\n🧠 Đồng thuận: {consensus:+.3f} → {verdict}"]
        for f in finals: lines.append(f"  {f.get('icon','🤖')} {f['title']}: {f['stance']:+.2f}")
        if rep: lines.append(f"\n💼 ĐÓNG LỆNH {rep['dir'].upper()} P&L: {rep['pnl']:+,.2f}$")
        if st.get("position"): lines.append(f"\n💼 MỞ LỆNH {st['position']['dir'].upper()} Entry ${st['position']['entry']:,.2f}")
        send_telegram("\n".join(lines), cfg)

# =====================================================================
# OUTPUT DASHBOARD
# =====================================================================
def run_once(cfg, args):
    tf_key = args.timeframe
    price, price_src = fetch_price()
    klines, kline_src = fetch_klines(tf_key)
    ind = compute_indicators(klines, tf_key)
    snap = market_snapshot(cfg, price, price_src, klines, ind, tf_key, args.context)
    timeline, _ = run_debate(cfg, snap, args.rounds)
    finals = timeline[-1]["entries"]
    consensus, verdict = compute_consensus(finals, tf_key)
    crowd = simulate_crowd(finals, args.voters, ind["momentum"], seed=args.seed)
    mc = run_monte_carlo(price, TIMEFRAMES[tf_key]["steps"], ind["vol"], consensus, ind["momentum"], args.paths, seed=args.seed)
    
    os.makedirs(args.out, exist_ok=True)
    if not args.no_trader: trader_step(cfg, args, ind, mc, consensus, verdict, finals, price, klines, args.out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--voters", type=int, default=80)
    ap.add_argument("--paths", type=int, default=300)
    ap.add_argument("--context", default="")
    ap.add_argument("--no-trader", action="store_true")
    ap.add_argument("--out", default="output")
    ap.add_argument("--seed", type=int, default=None)
    run_once(load_config(), ap.parse_args())

if __name__ == "__main__":
    main()
