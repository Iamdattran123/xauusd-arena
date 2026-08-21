# -*- coding: utf-8 -*-
"""Chỉ báo kỹ thuật — thuần toán, không phụ thuộc nguồn dữ liệu."""
import math
import statistics

from .config import TIMEFRAMES


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


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
    return sum(s) / len(s) if s else 0.0


def compute_indicators(klines, tf_key):
    closes = [k["c"] for k in klines]
    n = len(closes)
    if n < 2:
        return {"rsi": 50.0, "ema20": 0.0, "ema50": 0.0, "macd_hist": 0.0, "atr": 0.0,
                "res": 0.0, "sup": 0.0, "vol": TIMEFRAMES[tf_key]["vol"], "momentum": 0.0,
                "last_ret_pct": 0.0, "trend": "KHÔNG XÁC ĐỊNH", "n": n}
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
