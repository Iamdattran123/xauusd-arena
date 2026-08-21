"""XAU/USD AI Debate Arena — package backend.

Kiến trúc module hoá để dễ mở rộng & chạy đa môi trường (GitHub Actions / VPS):
    config        — cấu hình, hằng số, định tuyến LLM
    state         — đọc/ghi file JSON an toàn (atomic write) + khoá chống chạy trùng
    market_hours  — lịch phiên thị trường vàng (cuối tuần, ngày lễ CME)
    datasources   — giá realtime đa nguồn (Exness/MetaAPI → spot → crypto → futures) + nến + căn chỉnh giá
    indicators    — chỉ báo kỹ thuật
    llm           — gọi LLM 4 nhà cung cấp + chuỗi dự phòng
    debate        — hội đồng 4 chuyên gia tranh luận + đồng thuận
    simulation    — đám đông, Monte Carlo, backtest
    trader        — AI Trader mô phỏng (lệnh, SL/TP, pending, phát lại nến)
    brokers       — MetaAPI (Exness cloud) + MT5 trực tiếp (demo only)
    reports       — Telegram, báo cáo phiên/tổng kết, dashboard HTML
    telegram_bot  — lệnh điều khiển qua Telegram
    scheduler     — vòng lặp watch cho VPS (nhận biết phiên nghỉ)
"""

__version__ = "2.0.0"
APP_NAME = "XAU/USD AI Debate Arena"
