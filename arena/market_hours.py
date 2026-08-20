# -*- coding: utf-8 -*-
"""Lịch phiên thị trường vàng (XAU/USD) — AI & bot biết khi nào thị trường nghỉ.

Quy ước giờ (UTC, mặc định, có thể ghi đè bằng config.json → market_hours):
    - Mở cửa: Chủ nhật 22:00 UTC
    - Đóng cửa: Thứ sáu 21:00 UTC
    - Nghỉ bảo trì hằng ngày: 21:00–22:00 UTC (thứ 2 → thứ 5)
    - Ngày lễ CME (vàng): đóng cửa cả ngày / đóng sớm 18:00 UTC
  Khớp gần đúng lịch giao dịch vàng của các sàn lớn (Exness/CME). Lịch lễ là
  xấp xỉ cho 2025–2027 — muốn chuẩn tuyệt đối thì thêm vào config:
      market_hours.extra_holidays / extra_early_closes  (định dạng "YYYY-MM-DD")
"""
import datetime as _dt
import time as _time

# Ngày lễ đóng cửa hoàn toàn (CME — kim loại quý). Approx 2025–2027.
_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

# Ngày đóng sớm (18:00 UTC): trước lễ Tạ ơn/Giáng sinh/Năm mới, trước Good Friday
_EARLY_CLOSES = {
    "2025-04-17", "2025-07-03", "2025-11-28", "2025-12-24", "2025-12-31",
    "2026-04-02", "2026-11-27", "2026-12-24", "2026-12-31",
    "2027-03-25", "2027-11-26", "2027-12-23", "2027-12-31",
}


def _hm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _settings(cfg=None):
    mh = (cfg or {}).get("market_hours") or {}
    return {
        "sun_open": _hm(str(mh.get("sun_open", "22:00"))),
        "fri_close": _hm(str(mh.get("fri_close", "21:00"))),
        "break_start": _hm(str(mh.get("daily_break_start", "21:00"))),
        "break_end": _hm(str(mh.get("daily_break_end", "22:00"))),
        "early_close": _hm(str(mh.get("early_close", "18:00"))),
        "holidays": _HOLIDAYS | set(mh.get("extra_holidays") or []),
        "early": _EARLY_CLOSES | set(mh.get("extra_early_closes") or []),
    }


def is_market_open(dt=None, cfg=None):
    """True nếu thị trường vàng đang MỞ tại thời điểm dt (aware UTC)."""
    dt = dt or _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    s = _settings(cfg)
    wd = dt.weekday()          # 0 = Thứ 2 ... 6 = Chủ nhật
    now = dt.hour * 60 + dt.minute
    day = dt.strftime("%Y-%m-%d")

    if day in s["holidays"]:
        return False
    if wd == 5:                                    # Thứ 7: đóng cả ngày
        return False
    if wd == 6:                                    # Chủ nhật: mở từ 22:00
        return now >= s["sun_open"]
    if wd == 4:                                    # Thứ 6: đóng từ 21:00
        return now < s["fri_close"]
    # Thứ 2 → thứ 5: nghỉ bảo trì 21:00–22:00
    if day in s["early"]:
        return now < s["early_close"]
    return not (s["break_start"] <= now < s["break_end"])


def _step(dt, minutes=5):
    return dt + _dt.timedelta(minutes=minutes)


def next_market_open(dt=None, cfg=None):
    """Thời điểm MỞ CỬA tiếp theo (aware UTC). Tìm tối đa 7 ngày tới."""
    dt = dt or _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    t = _step(dt)
    for _ in range(7 * 24 * 12):     # 7 ngày, bước 5 phút
        if is_market_open(t, cfg):
            return t
        t = _step(t)
    return None


def next_market_close(dt=None, cfg=None):
    """Thời điểm ĐÓNG CỬA tiếp theo nếu đang mở (aware UTC)."""
    dt = dt or _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    if not is_market_open(dt, cfg):
        return None
    t = _step(dt)
    for _ in range(7 * 24 * 12):
        if not is_market_open(t, cfg):
            return t
        t = _step(t)
    return None


def status_info(dt=None, cfg=None):
    """Trả dict: open, text (tiếng Việt), next_open, next_close — dùng cho báo cáo/prompt."""
    dt = dt or _dt.datetime.now(_dt.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    if is_market_open(dt, cfg):
        nxt = next_market_close(dt, cfg)
        return {"open": True,
                "text": "🟢 THỊ TRƯỜNG MỞ — phiên giao dịch bình thường",
                "next_close": nxt.strftime("%a %d/%m %H:%M UTC") if nxt else "—"}
    nxt = next_market_open(dt, cfg)
    day = dt.strftime("%Y-%m-%d")
    reason = "cuối tuần" if dt.weekday() >= 5 else "ngày lễ" if day in _settings(cfg)["holidays"] else "nghỉ bảo trì hằng ngày"
    return {"open": False,
            "text": f"🔴 THỊ TRƯỜNG ĐÓNG ({reason}) — mở lại {nxt.strftime('%a %d/%m %H:%M UTC') if nxt else '—'}",
            "next_open": nxt.strftime("%a %d/%m %H:%M UTC") if nxt else "—"}


def now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def ts_ms():
    return int(_time.time() * 1000)
