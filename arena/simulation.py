# -*- coding: utf-8 -*-
"""Mô phỏng: đám đông cử tri + Monte Carlo + backtest chiến lược mẫu."""
import math
import os
import time

import numpy as np

from .config import TIMEFRAMES
from .indicators import calc_atr, calc_ema, calc_macd, calc_rsi, clamp
from .state import read_json, write_json


def simulate_crowd(finals, n_voters, momentum, seed=None):
    rng = np.random.default_rng(seed)
    mb = clamp(momentum * 0.35 + rng.normal(0, 0.05), -0.4, 0.4)
    stances = np.array([f["stance"] for f in finals])
    confs = np.array([f["conf"] for f in finals])
    biases = np.clip(mb + rng.normal(0, 0.35, n_voters), -1, 1)
    pers = confs[None, :] * (1 - 0.85 * np.abs(stances[None, :] - biases[:, None])) + rng.normal(0, 0.12, (n_voters, len(finals)))
    pers = np.clip(pers, 0, 1.5)
    total = pers.sum(axis=1)
    pos = np.divide((pers * stances[None, :]).sum(axis=1), total,
                    out=np.zeros(n_voters, dtype=float), where=total > 0)
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


# ----------------------------------------------------------------------
# BACKTEST — chiến lược mẫu có bộ lọc (không lookahead: chỉ dùng dữ liệu tới nến i)
# ----------------------------------------------------------------------
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
    hist = read_json(p, []) or []
    hist.append(entry)
    write_json(p, hist[-500:])
