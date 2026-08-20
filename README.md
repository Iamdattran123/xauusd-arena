# 🥇 XAU/USD AI DEBATE ARENA

Bot mô phỏng giao dịch vàng **XAU/USD** chạy 24/7 — hội đồng 4 chuyên gia AI tranh luận → đám đông ảo bỏ phiếu → Monte Carlo → **AI Trader** quản lý vốn mô phỏng $1.000 → báo cáo Telegram mỗi phiên.

Chạy được trên **GitHub Actions (miễn phí)** hoặc **VPS** (systemd), tự nhận biết **phiên nghỉ cuối tuần/lễ**, lấy giá **realtime chuẩn sàn** (Exness qua MetaAPI → spot → PAXG → futures, tự căn chỉnh về cùng một mức giá).

> ⚠️ **Dự án phục vụ nghiên cứu & giáo dục — KHÔNG phải lời khuyên tài chính.** Kết quả mô phỏng không đại diện hiệu suất giao dịch thật.

---

## ✨ Tính năng nổi bật (v2)

| Tính năng | Mô tả |
|---|---|
| 🧠 Hội đồng tranh luận | 4 chuyên gia (Vĩ mô · Kỹ thuật · Quỹ/TW · Đám đông nhỏ lẻ) qua 4 nhà cung cấp LLM (OpenRouter/Groq/Cohere/Gemini), 3 vòng: lập trường → phản biện → chốt |
| 💲 Giá realtime chuẩn sàn | Exness bid/ask thật (MetaAPI cloud) → gold-api spot → Binance PAXG → Yahoo GC=F; mọi nến **căn chỉnh về đúng giá spot** (hết lệch spot/futures ~$65 như bản cũ) |
| 🗓️ Nhận biết phiên nghỉ | Cuối tuần (T7 → CN 22:00 UTC), nghỉ bảo trì 21:00–22:00 UTC, ngày lễ CME 2025–2027 + đóng sớm. AI được báo trạng thái thị trường trong prompt; **trader không mở lệnh khi đóng cửa** |
| 💼 AI Trader | Lệnh market/limit/stop, SL/TP, RR, tối đa 3 lệnh mở + 3 lệnh chờ, **phát lại từng nến** (không sót SL/TP khi cron trễ), **xử lý gap cuối tuần** (khớp tại giá mở), chấm điểm + rút bài học |
| 🛡️ An toàn | Chặn giao dịch khi dữ liệu mẫu/offline; MT5 trực tiếp **từ chối tài khoản REAL** (chỉ demo); khoá chống chạy trùng; ghi file JSON nguyên tử |
| 📡 Vận hành | Báo cáo Telegram mỗi phiên (có đánh dấu ⚠️ agent dùng dữ liệu mẫu), dashboard Plotly tự sinh, tổng kết định kỳ, lệnh điều khiển từ xa |
| 🧩 Dễ mở rộng | Code module hoá trong `arena/` (data, market_hours, trader, llm, debate, simulation, reports...) + 38 unit test |

---

## 🚀 Chạy nhanh

```bash
git clone https://github.com/<your-account>/xauusd-arena.git
cd xauusd-arena
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt        # chỉ cần numpy
cp config.json.example config.json     # điền API key / Telegram (tùy chọn)

python app.py                          # chạy 1 phiên đầy đủ
python app.py --market-status          # xem giờ mở/đóng cửa thị trường
python app.py --test-keys              # kiểm tra 4 API key
python app.py --test-telegram          # gửi tin test Telegram
python app.py --watch 60               # VPS: tự chạy mỗi 60 phút, nghỉ cuối tuần
python app.py --serve 8000             # mở dashboard tại http://localhost:8000/dashboard.html
```

Không có API key → chế độ **dữ liệu mẫu** (mock) vẫn chạy để demo; AI Trader sẽ **không giao dịch** trên dữ liệu mẫu (dùng `--allow-mock-trading` nếu muốn ép khi phát triển).

---

## ⚙️ Chạy trên GitHub Actions (như hiện tại)

1. Vào repo → **Settings → Secrets and variables → Actions** → thêm:
   - `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `COHERE_API_KEY` (≥1 key là chạy được; thiếu provider nào hệ thống tự chuyển sang nguồn khác)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (tạo bot bằng @BotFather; lấy chat id bằng @userinfobot)
2. Workflow `.github/workflows/arena.yml` chạy **đầu mỗi giờ** (cron `0 * * * *`), chạy test trước, rồi chạy phiên, commit kết quả vào `output/`.
3. Dashboard xem qua **GitHub Pages**: Settings → Pages → Deploy from branch (thư mục `/output`). Link mẫu: `https://<account>.github.io/xauusd-arena/dashboard.html`

Phiên cuối tuần vẫn chạy để AI **biết thị trường đóng** (báo cáo ghi rõ + trader đứng ngoài); muốn bỏ hẳn phiên nghỉ thì thêm `--skip-closed` vào lệnh trong workflow.

---

## 🖥️ Chạy trên VPS

```bash
git clone <repo-url> ~/xauusd-arena
cd ~/xauusd-arena
bash deploy/install.sh          # tạo venv, cài systemd, tự start
```

Sau khi cài: mở `~/xauusd-arena/config.json` điền key, rồi:

```bash
systemctl --user restart xauusd-arena
systemctl --user status xauusd-arena
journalctl --user -u xauusd-arena -f        # xem log live
```

- Watch mode **tự bỏ qua phiên nghỉ** (cuối tuần/lễ) để tiết kiệm token — thêm `--run-closed` nếu muốn phân tích cả khi đóng cửa.
- Muốn tự đặt lệnh **thật trên Exness demo**: bật `metaapi.enabled` trong config.json (token + account_id từ app.metaapi.cloud, account Exness demo đã liên kết) — bot sẽ lấy giá bid/ask Exness thật và có thể đặt lệnh demo qua REST (chỉ DEMO).

---

## 🎛️ Lệnh Telegram

| Lệnh | Ý nghĩa |
|---|---|
| `trade 1h` / `trade 15p` / `trade 4h` / `trade 1d` | Đổi khung giao dịch của AI Trader |
| `status` | Trạng thái vốn, lệnh mở, lệnh chờ |
| `market` | Giờ mở/đóng cửa thị trường |
| `tổng kết ngày` / `tổng kết` | Thống kê hôm nay / toàn bộ |
| `reset trader` | Reset vốn mô phỏng về $1.000 |
| `stop` | Quay lại khung mặc định |

🔐 Đặt `trader.allowed_user_ids` trong config.json để **chỉ chủ nhân** điều khiển được bot.

---

## 🧱 Kiến trúc

```
app.py                    # CLI entry (GitHub Actions / VPS)
arena/
  config.py               # hằng số, TIMEFRAMES, định tuyến LLM, load config.json
  market_hours.py         # lịch phiên: cuối tuần, lễ CME, giờ nghỉ, next open/close
  datasources.py          # giá realtime đa nguồn + nến + căn chỉnh về spot + sanity check
  indicators.py           # RSI/MACD/EMA/ATR/vol...
  llm.py                  # gọi 4 provider + fallback chain + retry 429/403
  debate.py               # prompt hội đồng (có trạng thái thị trường) + đồng thuận trọng số
  simulation.py           # đám đông ảo + Monte Carlo + backtest (không lookahead)
  trader.py               # AI Trader: phát lại nến, gap, pending, chấm điểm, bài học
  brokers.py              # MetaAPI (Exness cloud) + MT5 trực tiếp (demo only)
  reports.py              # Telegram + báo cáo phiên/tổng kết + dashboard HTML
  telegram_bot.py         # lệnh điều khiển từ xa (có whitelist)
  scheduler.py            # watch loop cho VPS (căn giờ, bỏ qua phiên nghỉ)
  state.py                # JSON atomic write + file lock chống chạy trùng
tests/                    # 38 unit test (unittest chuẩn, không cần pip thêm)
deploy/                   # script + systemd unit cho VPS
output/                   # kết quả mỗi phiên (dashboard, state, backtest history...)
```

**Chuỗi giá:** Exness/MetaAPI (bid-ask thật) → gold-api spot → Binance PAXG → Yahoo GC=F. Nến ưu tiên Exness → PAXG → GC=F, sau đó **co giãn toàn bộ chuỗi nến cho close cuối == giá spot** (chỉ khi lệch ≤3%, nếu không thì cảnh báo dữ liệu nghi ngờ).

**Lịch thị trường (UTC):** mở CN 22:00 → đóng T6 21:00; nghỉ bảo trì 21:00–22:00 (T2–T5); lễ CME đóng cả ngày/đóng sớm 18:00 (2025–2027, xấp xỉ — thêm ngày lẻ qua `market_hours.extra_holidays`).

---

## 🧪 Test

```bash
python -m unittest discover -s tests    # 38 test: market hours, trader (gap/replay/chặn),
                                        # backtest no-lookahead, alignment, lock, state
```

---

## 📝 Ghi chú kỹ thuật đáng lưu ý

- **Phát lại nến**: mỗi phiên chỉ duyệt các nến **mới từ lần chạy trước** (lưu `last_candle_ts`) → cron trễ vài giờ vẫn xét đủ SL/TP. Nến gap qua SL/TP → khớp tại **giá mở** (trượt giá). SL và TP cùng chạm trong 1 nến → tính **SL trước** (thận trọng).
- **State cũ tương thích**: lệnh mở dạng `position` cũ tự migrate sang `positions`; lệnh đang mở khi nâng cấp không bị phát lại lịch sử (mốc phát lại lấy từ thời điểm mở lệnh).
- **Chống chạy trùng**: file lock trong `output/` — 2 cron hoặc watch + chạy tay không ghi đè state của nhau.
- **Backtest** chỉ dùng dữ liệu tới nến hiện tại (đã kiểm chứng: win rate ~50% trên random walk — không có lookahead bias).

## 📄 License

MIT — xem [LICENSE](LICENSE).
