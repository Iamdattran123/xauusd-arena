# -*- coding: utf-8 -*-
"""Đọc/ghi file JSON an toàn (atomic write) + khoá phiên bản chống chạy trùng.

- atomic: ghi vào file tạm rồi os.replace → không bao giờ hỏng file giữa chừng
- InstanceLock: chặn 2 tiến trình (cron chồng nhau / watch + chạy tay) ghi đè state
"""
import json
import os
import time


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    """Ghi JSON nguyên tử (temp + rename). Trả True nếu ghi xong."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return True


class InstanceLock:
    """Khoá file đơn giản, đa nền tảng (Linux/macOS/Windows), tự hết hạn.

    Dùng để đảm bảo chỉ 1 tiến trình chạy phiên mỗi lúc — tránh 2 cron/run tay
    cùng ghi trader_state.json dẫn tới mất dữ liệu.
    """

    def __init__(self, name="arena_run", timeout=900, lock_dir=None):
        self.path = os.path.join(lock_dir or os.path.dirname(os.path.abspath(__file__)),
                                 f".{name}.lock")
        self.timeout = timeout  # lock tự hết hạn sau N giây (chống kẹt vĩnh viễn)
        self._held = False

    def acquire(self):
        try:
            if os.path.exists(self.path):
                try:
                    age = time.time() - os.path.getmtime(self.path)
                    if age < self.timeout:
                        return False
                    # lock quá hạn → dọn và chiếm lại
                    os.remove(self.path)
                except OSError:
                    return False
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"pid={os.getpid()} at={time.strftime('%Y-%m-%d %H:%M:%S')}")
            self._held = True
            return True
        except OSError:
            return False

    def release(self):
        if self._held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self._held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
