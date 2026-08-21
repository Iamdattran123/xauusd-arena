# -*- coding: utf-8 -*-
"""📡 Vòng lặp watch — cho VPS (python app.py --watch 60).

Khác biệt với chạy 1 phiên (GitHub Actions):
  - Tự nhận biết phiên nghỉ: cuối tuần/lễ → KHÔNG chạy (tiết kiệm token) trừ khi --run-closed
  - Căn giờ theo đúng khung thời gian (ví dụ 1h → chạy đầu mỗi giờ)
  - Lỗi phiên không làm chết vòng lặp — log, chờ, chạy lại phiên sau
  - Khoá chống 2 tiến trình watch cùng lúc
"""
import time
import traceback

from .config import TIMEFRAMES
from .market_hours import is_market_open, next_market_open, now_utc
from .state import InstanceLock


def _slot_delay(tf_key):
    """Số giây tới mốc thời gian tiếp theo của khung (căn theo đồng hồ)."""
    step = TIMEFRAMES[tf_key]["step_min"] * 60
    now = time.time()
    return step - (now % step)


def watch_loop(cfg, args, run_once_fn, lock_name="arena_watch"):
    lock = InstanceLock(lock_name, timeout=3600)
    if not lock.acquire():
        print("⚠️ Đã có 1 tiến trình watch khác đang chạy (file lock). Thoát.")
        return 1

    print(f"📡 Chế độ watch: khung {TIMEFRAMES[args.timeframe]['label']} · chạy mỗi "
          f"{TIMEFRAMES[args.timeframe]['step_min']} phút"
          + (" · BỎ QUA khi thị trường đóng" if not args.run_closed else " · chạy cả khi thị trường đóng"))
    try:
        while True:
            now = now_utc()
            if not args.run_closed and not is_market_open(now, cfg):
                nxt = next_market_open(now, cfg)
                wait = max(60, min(3600, (nxt - now).total_seconds() + 120)) if nxt else 3600
                print(f"🔴 {now.strftime('%d/%m %H:%M')} thị trường ĐÓNG — ngủ {wait/60:.0f} phút"
                      + (f" (mở lại {nxt.strftime('%a %H:%M UTC')})" if nxt else ""))
                time.sleep(wait)
                continue
            try:
                run_once_fn(cfg, args)
            except Exception:
                print("💥 Phiên lỗi — log bên dưới, sẽ chạy lại phiên sau:")
                traceback.print_exc()
                time.sleep(60)
                continue
            # ngủ tới mốc thời gian tiếp theo (kiểm tra lại thị trường mỗi phút)
            deadline = time.time() + max(10, _slot_delay(args.timeframe))
            while time.time() < deadline:
                time.sleep(min(60, deadline - time.time()))
    except KeyboardInterrupt:
        print("\n👋 Đã dừng watch.")
    finally:
        lock.release()
    return 0
