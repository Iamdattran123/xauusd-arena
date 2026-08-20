#!/usr/bin/env bash
# ============================================================
#  Cài đặt XAU/USD AI Debate Arena trên VPS Linux (Ubuntu/Debian)
#  Chạy:  bash deploy/install.sh
#  Dự án được cài tại ~/xauusd-arena, chạy 24/7 qua systemd.
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/xauusd-arena}"
WATCH_MINUTES="${WATCH_MINUTES:-60}"          # chạy mỗi 60 phút (khung 1h)
SERVICE_NAME="xauusd-arena"

echo "📦 1/5 — Kiểm tra Python"
command -v python3 >/dev/null || { echo "❌ Cần cài python3 trước (sudo apt install python3 python3-venv)"; exit 1; }
python3 -c "import venv" 2>/dev/null || { echo "❌ Cần python3-venv (sudo apt install python3-venv)"; exit 1; }

echo "📂 2/5 — Thư mục dự án: $APP_DIR"
if [ ! -f "$APP_DIR/app.py" ]; then
  echo "❌ Chưa có mã nguồn — clone trước: git clone <repo-url> $APP_DIR"
  exit 1
fi

echo "🐍 3/5 — Tạo virtualenv + cài numpy"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/config.json" ]; then
  cp "$APP_DIR/config.json.example" "$APP_DIR/config.json"
  echo "⚠️ Đã tạo config.json từ mẫu — MỞ FILE ĐIỀN API KEY + Telegram trước khi start!"
fi

echo "🛠️ 4/5 — Cài systemd service"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/$SERVICE_NAME.service" <<EOF
[Unit]
Description=XAU/USD AI Debate Arena
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/app.py --watch $WATCH_MINUTES
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

echo "🚀 5/5 — Kích hoạt service"
systemctl --user daemon-reload
loginctl enable-linger "$USER" 2>/dev/null || true   # chạy cả khi không đăng nhập
systemctl --user enable --now "$SERVICE_NAME"

echo ""
echo "✅ XONG! Trạng thái:  systemctl --user status $SERVICE_NAME"
echo "   Xem log:          journalctl --user -u $SERVICE_NAME -f"
echo "   Dashboard:        $APP_DIR/output/dashboard.html  (hoặc python app.py --serve 8000)"
echo "   Bot tự bỏ qua phiên nghỉ cuối tuần/lễ; lệnh Telegram: trade 1h · status · market · tổng kết"
