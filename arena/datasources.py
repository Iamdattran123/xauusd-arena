# -*- coding: utf-8 -*-
"""DỮ LIỆU THỊ TRƯỜNG — giá realtime chuẩn sàn + nến lịch sử nhất quán.

Chuỗi ưu tiên giá (cấu hình được qua config.json → data.price_providers):
    1. metaapi   — giá bid/ask Exness THẬT qua MetaAPI cloud (chuẩn nhất, cần token)
    2. gold_api  — XAU/USD spot realtime (gold-api.com, không cần key)
    3. binance   — PAXGUSDT (≈ spot 1 oz vàng, 24/7)
    4. yahoo     — GC=F (COMEX futures — chỉ dùng khi các nguồn trên chết)

Chuỗi ưu tiên nến:
    1. metaapi (nến Exness thật) → 2. binance PAXG → 3. yahoo GC=F → 4. dữ liệu mẫu

🔑 NGUYÊN TẮC NHẤT QUÁN: mọi chuỗi nến được CĂN CHỈNH (align) theo giá spot realtime
   để SL/TP, chỉ báo và backtest dùng CHUNG một mức giá — loại bỏ lệch spot/futures
   (~65 USD ở phiên cũ) từng làm sai lệch việc kiểm tra SL/TP.
"""
import json
import math
import random
import time
import urllib.request

from . import brokers
from .config import PRICE_SANITY_RANGE, MAX_ALIGN_RATIO, TIMEFRAMES, UA

PRICE_INFO = {"spot": None, "spot_src": None, "futures": None, "futures_src": None}

# Nhãn chất lượng (dùng trong báo cáo)
QUALITY_LABEL = {
    "broker": "⭐ sàn (Exness/MetaAPI bid-ask thật)",
    "spot": "✅ spot realtime",
    "crypto_spot": "🟡 PAXG crypto ≈ spot",
    "futures": "🟠 futures COMEX (đã căn chỉnh về spot)",
    "default": "⛔ dữ liệu mẫu (offline) — KHÔNG giao dịch",
}


def _http_json(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def sanity_price(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return False
    lo, hi = PRICE_SANITY_RANGE
    return math.isfinite(p) and lo < p < hi


# ----------------------------------------------------------------------
# GIÁ REALTIME
# ----------------------------------------------------------------------
def _price_metaapi(cfg):
    if not brokers.load_metaapi_cfg(cfg)["enabled"]:
        return None
    mid, info = brokers.metaapi_current_price(cfg)
    if not sanity_price(mid):
        return None
    spread = abs(info["bid"] - info["ask"])
    return {"price": mid, "source": f"Exness (MetaAPI) · XAUUSD · spread ${spread:.2f}",
            "quality": "broker", "ts": info.get("ts", "")}


def _price_gold_api(cfg):
    try:
        d = _http_json("https://api.gold-api.com/price/XAU", timeout=6)
        p = float(d["price"])
        if sanity_price(p):
            return {"price": p, "source": "gold-api.com · XAU spot", "quality": "spot",
                    "ts": d.get("updatedAt", "")}
    except Exception:
        pass
    return None


def _price_binance(cfg):
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            d = _http_json(f"{host}/api/v3/ticker/price?symbol=PAXGUSDT", timeout=6)
            p = float(d["price"])
            if sanity_price(p):
                return {"price": p, "source": f"Binance ({host.split('//')[1].split('/')[0]}) · PAXG (≈spot)",
                        "quality": "crypto_spot", "ts": ""}
        except Exception:
            continue
    return None


def _price_yahoo(cfg):
    try:
        d = _http_json("https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=1d", timeout=6)
        p = float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
        if sanity_price(p):
            return {"price": p, "source": "Yahoo Finance · GC=F (COMEX futures)", "quality": "futures", "ts": ""}
    except Exception:
        pass
    return None


_PRICE_PROVIDERS = {
    "metaapi": _price_metaapi,
    "gold_api": _price_gold_api,
    "binance": _price_binance,
    "yahoo": _price_yahoo,
}


def fetch_price(cfg):
    """Giá realtime theo chuỗi ưu tiên. Trả dict {price, source, quality, synthetic}."""
    providers = cfg.get("data", {}).get("price_providers") or list(_PRICE_PROVIDERS)
    # luôn thử Yahoo để điền PRICE_INFO.futures (phục vụ dòng chênh lệch báo cáo)
    fy = _price_yahoo(cfg)
    if fy:
        PRICE_INFO["futures"] = fy["price"]
        PRICE_INFO["futures_src"] = fy["source"]
    for name in providers:
        fn = _PRICE_PROVIDERS.get(name)
        if not fn:
            continue
        try:
            q = fn(cfg)
        except Exception:
            q = None
        if q:
            if q["quality"] != "futures":
                PRICE_INFO["spot"] = q["price"]
                PRICE_INFO["spot_src"] = q["source"]
            q["synthetic"] = False
            q["ts"] = q.get("ts", "") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return q
    return {"price": 4050.0, "source": "Giá mặc định (offline)", "quality": "default",
            "synthetic": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ----------------------------------------------------------------------
# NẾN LỊCH SỬ
# ----------------------------------------------------------------------
def _klines_metaapi(cfg, tf_key):
    try:
        if not brokers.load_metaapi_cfg(cfg)["enabled"]:
            return None
        rows = brokers.metaapi_candles(cfg, tf_key, limit=300)
        if len(rows) > 10:
            return rows, f"Exness (MetaAPI) · XAUUSD {TIMEFRAMES[tf_key]['label']}"
    except Exception:
        pass
    return None


def _klines_binance(cfg, tf_key):
    interval = TIMEFRAMES[tf_key]["binance"]
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            d = _http_json(f"{host}/api/v3/klines?symbol=PAXGUSDT&interval={interval}&limit=300", timeout=8)
            raw = [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4])}
                   for k in d]
            if len(raw) > 10:
                return raw, f"Binance · PAXGUSDT {TIMEFRAMES[tf_key]['label']}"
        except Exception:
            continue
    return None


def _klines_yahoo(cfg, tf_key):
    tfc = TIMEFRAMES[tf_key]
    try:
        d = _http_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval={tfc['yahoo_interval']}&range={tfc['yahoo_range']}",
            timeout=10)
        r = d["chart"]["result"][0]
        ts, q = r["timestamp"], r["indicators"]["quote"][0]
        raw = [{"t": t * 1000, "o": o, "h": h, "l": lo, "c": c}
               for t, o, h, lo, c in zip(ts, q["open"], q["high"], q["low"], q["close"])
               if o is not None and c is not None]
        if tfc["resample"] > 1:
            raw = resample(raw, tfc["step_min"] * 60000)
        if len(raw) > 10:
            return raw, f"Yahoo · GC=F {tfc['label']}"
    except Exception:
        pass
    return None


_KLINE_PROVIDERS = {
    "metaapi": _klines_metaapi,
    "binance": _klines_binance,
    "yahoo": _klines_yahoo,
}


def fetch_klines(cfg, tf_key):
    """Nến theo chuỗi ưu tiên — bỏ nến cuối (đang hình thành). Trả (klines, source, synthetic)."""
    providers = cfg.get("data", {}).get("kline_providers") or list(_KLINE_PROVIDERS)
    for name in providers:
        fn = _KLINE_PROVIDERS.get(name)
        if not fn:
            continue
        try:
            res = fn(cfg, tf_key)
        except Exception:
            res = None
        if res:
            klines, src = res
            klines = klines[:-1]  # nến cuối chưa đóng
            if len(klines) > 10:
                return klines, src, False
    print("⚠️ Không lấy được nến lịch sử — sinh dữ liệu mẫu.")
    return mock_klines(4050.0, TIMEFRAMES[tf_key]["step_min"], 120, TIMEFRAMES[tf_key]["vol"]), \
        "Dữ liệu mẫu (offline)", True


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


# ----------------------------------------------------------------------
# CĂN CHỈNH — mọi nến về đúng mức giá spot hiện tại
# ----------------------------------------------------------------------
def align_klines_to_price(klines, price):
    """Co/giãn chuỗi nến sao cho close cuối == giá spot realtime.

    Trả (klines_mới, ratio). ratio ≈ 1 khi nguồn nến cùng mức giá với spot;
    nếu lệch quá MAX_ALIGN_RATIO (3%) → dữ liệu nghi ngờ, KHÔNG căn chỉnh.
    """
    if not klines or not price:
        return klines, 1.0
    last_c = klines[-1]["c"]
    if not last_c or not math.isfinite(last_c):
        return klines, 1.0
    ratio = price / last_c
    if not (1 - MAX_ALIGN_RATIO < ratio < 1 + MAX_ALIGN_RATIO):
        return klines, 1.0
    return [{**k, "o": k["o"] * ratio, "h": k["h"] * ratio, "l": k["l"] * ratio, "c": k["c"] * ratio}
            for k in klines], ratio


def build_market_data(cfg, tf_key):
    """Tổng hợp giá + nến + trạng thái dữ liệu cho 1 phiên.

    Trả dict: price, price_src, price_quality, synthetic, klines, kline_src,
    kline_quality, aligned_ratio, basis, stale (bool), stale_text.
    """
    q = fetch_price(cfg)
    price, price_src, quality = q["price"], q["source"], q["quality"]
    klines, kline_src, k_synthetic = fetch_klines(cfg, tf_key)
    raw_last_close = klines[-1]["c"] if klines else price
    klines, ratio = align_klines_to_price(klines, price)
    basis = price - raw_last_close

    synthetic = q["synthetic"] or k_synthetic

    # Độ tươi của dữ liệu: nến cuối cách hiện tại bao lâu (phút)
    step_min = TIMEFRAMES[tf_key]["step_min"]
    age_min = (time.time() * 1000 - klines[-1]["t"]) / 60000 if klines else 0
    stale = age_min > 2.5 * step_min

    kline_quality = quality if not k_synthetic else "default"
    return {
        "price": price, "price_src": price_src, "price_quality": quality,
        "synthetic": synthetic, "klines": klines, "kline_src": kline_src,
        "kline_quality": kline_quality, "aligned_ratio": ratio, "basis": basis,
        "stale": stale, "stale_text": f"nến cuối cách {age_min:.0f} phút" if stale else "",
        "last_candle_ts": klines[-1]["t"] if klines else 0,
        "quality_label": QUALITY_LABEL.get(quality, quality),
    }
