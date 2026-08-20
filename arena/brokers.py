# -*- coding: utf-8 -*-
"""Kết nối sàn giao dịch — MetaAPI cloud (Exness/MT5 qua REST) + MT5 trực tiếp.

MetaAPI = cách chuẩn để lấy giá Exness THẬT (bid/ask) + nến lịch sử từ tài khoản
Exness demo mà không cần cài MT5 (chạy được cả trên GitHub Actions lẫn VPS Linux).
Endpoint theo tài liệu chính thức metaapi.cloud (REST API v1):
    - Giá hiện tại : GET /users/current/accounts/{id}/symbols/{symbol}/current-price
                     host mt-client-api-v1.<region>.agiliumtrade.ai
    - Nến lịch sử  : GET /users/current/accounts/{id}/historical-market-data/symbols/{symbol}/timeframes/{tf}/candles
                     host mt-market-data-client-api-v1.<region>.agiliumtrade.ai
    - Đặt lệnh     : POST /users/current/accounts/{id}/trade
    - Vị thế       : GET /users/current/accounts/{id}/positions
    - Thông tin    : GET /users/current/accounts/{id}/accountInformation

AN TOÀN: MT5 trực tiếp chỉ cho phép tài khoản DEMO — tự từ chối nếu REAL.
"""
import json
import time
import urllib.error
import urllib.request

from .config import UA

METAAPI_REGIONS = [
    "agiliumtrade", "new-york", "london", "manila",
    "cyprus", "moscow", "seoul", "singapore",
]

# MetaAPI timeframe cho nến lịch sử (hỗ trợ cả 1m..1mn)
METAAPI_TF_MAP = {"15m": "15m", "1h": "1h", "4h": "4h", "1D": "1d"}


def load_metaapi_cfg(cfg):
    m = cfg.get("metaapi", {}) or {}
    import os
    return {
        "enabled": bool(m.get("enabled", False)),
        "token": str(m.get("token", "") or os.getenv("METAAPI_TOKEN", "")),
        "account_id": str(m.get("account_id", "") or os.getenv("METAAPI_ACCOUNT_ID", "")),
        "symbol": str(m.get("symbol", "XAUUSD")),
        "magic": int(m.get("magic", 20260804) or 20260804),
        "risk_pct": float(m.get("risk_pct", 1.0)),
        "region": str(m.get("region", "") or ""),
    }


def _metaapi_hosts(kind, region=""):
    """kind: 'client' (lệnh/giá) hoặc 'marketdata' (nến lịch sử)."""
    prefix = "mt-client-api-v1" if kind == "client" else "mt-market-data-client-api-v1"
    regions = [region] if region else METAAPI_REGIONS
    return [f"{prefix}.{r}.agiliumtrade.ai" for r in regions]


def metaapi_req(cfg, kind, method, path, body=None, timeout=30):
    """Gọi REST MetaAPI — tự dò lần lượt các region cho tới khi tìm thấy account."""
    mc = load_metaapi_cfg(cfg)
    if not mc["token"]:
        raise ValueError("Thiếu METAAPI_TOKEN (tạo tại app.metaapi.cloud → API access → token)")
    headers = {"auth-token": mc["token"], "User-Agent": UA["User-Agent"]}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    last_err = None
    for domain in _metaapi_hosts(kind, mc["region"]):
        try:
            url = f"https://{domain}{path}"
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ValueError("METAAPI_TOKEN không hợp lệ (401) — kiểm tra lại token")
            if e.code in (404, 400, 405):
                last_err = f"HTTP {e.code} @ {domain}"
                continue
            last_err = f"HTTP {e.code} @ {domain}"
        except Exception as e:
            last_err = f"{str(e)[:80]} @ {domain}"
    raise RuntimeError(f"Không gọi được MetaAPI ở mọi region. {last_err}")


# ----------------------------------------------------------------------
# DỮ LIỆU GIÁ — Exness thật qua MetaAPI
# ----------------------------------------------------------------------
def metaapi_current_price(cfg):
    """Giá hiện tại (bid/ask) từ tài khoản MetaAPI. Trả (mid, {'bid','ask','ts'}) hoặc raise."""
    mc = load_metaapi_cfg(cfg)
    d = metaapi_req(cfg, "client", "GET", f"/users/current/accounts/{mc['account_id']}/symbols/{mc['symbol']}/current-price")
    bid = d.get("bid")
    ask = d.get("ask")
    if not bid and not ask:
        raise ValueError(f"MetaAPI current-price không có bid/ask: {json.dumps(d, ensure_ascii=False)[:150]}")
    bid = float(bid or ask)
    ask = float(ask or bid)
    return (bid + ask) / 2.0, {"bid": bid, "ask": ask, "ts": d.get("time", "")}


def metaapi_candles(cfg, timeframe, limit=260):
    """Nến lịch sử từ MetaAPI (Exness). Trả list [{t,o,h,l,c}] (t = ms)."""
    import datetime
    mc = load_metaapi_cfg(cfg)
    tf = METAAPI_TF_MAP.get(timeframe, "1h")
    end = datetime.datetime.now(datetime.timezone.utc)
    step = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}[tf]
    start = end - datetime.timedelta(minutes=step * (limit + 2))
    qs = f"?startTime={start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}&limit={limit}"
    path = (f"/users/current/accounts/{mc['account_id']}/historical-market-data/symbols/{mc['symbol']}"
            f"/timeframes/{tf}/candles{qs}")
    rows = metaapi_req(cfg, "marketdata", "GET", path, timeout=60)
    out = []
    for r in rows:
        try:
            t = r.get("time", "")
            ts = datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000
            out.append({"t": int(ts), "o": float(r["open"]), "h": float(r["high"]),
                        "l": float(r["low"]), "c": float(r["close"])})
        except Exception:
            continue
    return out


# ----------------------------------------------------------------------
# GIAO DỊCH — Exness DEMO qua MetaAPI REST
# ----------------------------------------------------------------------
def metaapi_ensure_deployed(cfg, timeout=180):
    """Chờ tài khoản MetaAPI sẵn sàng (deployed)."""
    mc = load_metaapi_cfg(cfg)
    if not mc["account_id"]:
        raise ValueError("Thiếu METAAPI_ACCOUNT_ID (id tài khoản đã tạo trong dashboard MetaAPI)")
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            info = metaapi_req(cfg, "client", "GET", f"/users/current/accounts/{mc['account_id']}/accountInformation")
            if info.get("accountId") or info.get("balance") is not None or info.get("login") is not None:
                return True
        except Exception as e:
            if "401" in str(e):
                raise
        print("  ⏳ Đang chờ MetaAPI deploy MT5 cloud...")
        time.sleep(5)
    raise ValueError("Quá thời gian chờ MetaAPI deploy (kiểm tra tài khoản trong dashboard)")


def metaapi_account_info(cfg):
    mc = load_metaapi_cfg(cfg)
    return metaapi_req(cfg, "client", "GET", f"/users/current/accounts/{mc['account_id']}/accountInformation")


def metaapi_positions(cfg):
    mc = load_metaapi_cfg(cfg)
    d = metaapi_req(cfg, "client", "GET", f"/users/current/accounts/{mc['account_id']}/positions")
    return d.get("positions", []) if isinstance(d, dict) else []


def metaapi_place_order(cfg, decision, price, balance=1000.0):
    """Đặt lệnh THẬT trên Exness demo qua MetaAPI. Trả (result, error)."""
    mc = load_metaapi_cfg(cfg)
    try:
        metaapi_ensure_deployed(cfg)
    except Exception as e:
        return None, str(e)
    sl_dist = abs(decision["sl"] - price)
    if sl_dist < 1e-9:
        return None, "Khoảng cách SL quá nhỏ"
    risk_amt = decision["risk"] * max(balance, 1.0)
    # ~0.01 lot/1000$ cho XAUUSD (contract 100oz — tính gần đúng, cân chỉnh theo tài khoản)
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
        d = metaapi_req(cfg, "client", "POST", f"/users/current/accounts/{mc['account_id']}/trade", body)
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


# ----------------------------------------------------------------------
# EXNESS — MetaTrader 5 trực tiếp (chỉ Windows, chỉ DEMO)
# ----------------------------------------------------------------------
def load_exness_cfg(cfg):
    import os
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
    for s in [ecfg["symbol"], "XAUUSD", "XAUUSDm", "XAUUSD.a"]:
        info = mt5.symbol_info(s)
        if info is not None:
            return s, info
    return None, None


def mt5_price(cfg):
    """Giá vàng XAUUSD realtime từ Exness (bid/ask). Trả (mid, 'Exness MT5 · <symbol>')."""
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
        if tick and (tick.bid or tick.ask):
            mid = (float(tick.bid or tick.ask) + float(tick.ask or tick.bid)) / 2.0
            return mid, f"Exness MT5 · {sym}"
    except Exception:
        try:
            mt5.shutdown()
        except Exception:
            pass
    return None, None


def mt5_place_order(mt5, cfg, decision, price, balance=1000.0):
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
    risk_amt = decision["risk"] * max(balance, 1.0)
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
    st["balance"] = new_balance
    st["start_balance"] = float(getattr(acc, "balance", st.get("start_balance", 1000)))
    st["equity_points"].append({"t": int(time.time() * 1000), "e": float(acc.equity)})
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
