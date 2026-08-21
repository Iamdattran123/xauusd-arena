#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XAU/USD AI DEBATE ARENA — Backend v2 (module hoá, sẵn sàng GitHub Actions + VPS)
===============================================================================
Multi-agent debate + crowd voting + Monte Carlo + AI Trader ($1.000 mô phỏng)
+ Telegram báo cáo mỗi phiên + nhận biết phiên nghỉ (cuối tuần/lễ).

Nguồn giá realtime (ưu tiên chuẩn sàn — xem arena/datasources.py):
    Exness qua MetaAPI (bid/ask thật) → gold-api spot → Binance PAXG → Yahoo GC=F
    Mọi nến được CĂN CHỈNH về đúng mức giá spot để SL/TP không bị lệch nguồn.

Định tuyến 4 nhà cung cấp LLM (key từ biến môi trường / GitHub Secrets):
    OPENROUTER_API_KEY · GROQ_API_KEY · GEMINI_API_KEY · COHERE_API_KEY
    TELEGRAM_BOT_TOKEN · TELEGRAM_CHAT_ID  (để nhận báo cáo mỗi phiên)

Cách dùng:
    python app.py                          # chạy 1 phiên đầy đủ + báo cáo
    python app.py --timeframe 4h           # đổi khung giao dịch
    python app.py --watch 60               # VPS: tự chạy mỗi 60 phút, nghỉ cuối tuần
    python app.py --watch 60 --run-closed  # VPS: chạy cả khi thị trường đóng
    python app.py --market-status          # xem trạng thái thị trường rồi thoát
    python app.py --test-telegram          # gửi tin test Telegram
    python app.py --test-keys              # kiểm tra 4 API key
    python app.py --force-summary          # ép AI Trader tổng kết ngay
    python app.py --serve 8000             # web server xem dashboard
    python app.py --allow-mock-trading     # (dev) cho phép trade trên dữ liệu mẫu
"""
import argparse
import json
import os
import sys
import time

from arena import __version__
from arena.config import AGENTS, TIMEFRAMES, agent_conf, any_api_key, load_config
from arena.datasources import build_market_data
from arena.debate import market_snapshot, run_debate, compute_consensus
from arena.indicators import compute_indicators
from arena.market_hours import status_info, now_utc
from arena.reports import generate_dashboard_html, send_session_report, send_telegram
from arena.simulation import run_backtest, run_monte_carlo, save_backtest_history, simulate_crowd
from arena.state import InstanceLock, read_json, write_json
from arena.telegram_bot import telegram_poll_commands
from arena.trader import load_trader_state, trader_status_line, trader_step, trader_summary

# ----------------------------------------------------------------------
# BỘ NHỚ PHIÊN — AI nhớ các phiên trước
# ----------------------------------------------------------------------
def load_sessions(out_dir, max_n=200):
    s = read_json(os.path.join(out_dir, "sessions_log.json"), [])
    return s[-max_n:] if isinstance(s, list) else []


def save_session(out_dir, record):
    sess = load_sessions(out_dir)
    sess.append(record)
    write_json(os.path.join(out_dir, "sessions_log.json"), sess[-500:])


def history_block(out_dir, n=6):
    """Tóm tắt các phiên gần đây + hiệu suất trader → đưa vào prompt cho AI."""
    lines = []
    for s in load_sessions(out_dir)[-n:]:
        try:
            m = "🔴 đóng" if s.get("market_open") is False else "🟢 mở"
            lines.append(f"• {s['time']} [{s['tf']}] giá ${s['price']:,.2f} · đồng thuận {s['consensus']:+.2f} ({s['verdict']})"
                         + f" · {m}" + (f" · trader {s['trader_action'].upper()}" if s.get('trader_action') else ""))
        except Exception:
            continue
    try:
        st = load_trader_state(out_dir)
        if st.get("trades"):
            wr = st["wins"] / st["trades"] * 100
            lines.append(f"📈 Hiệu suất AI Trader: {st['trades']} lệnh · win {wr:.0f}% · "
                         f"P&L {st['total_pnl']:+,.2f}$ · vốn ${st['balance']:,.2f}")
    except Exception:
        pass
    return "\n".join(lines) if lines else ""


# ----------------------------------------------------------------------
# KẾT QUẢ PHIÊN
# ----------------------------------------------------------------------
def build_result(args, market_status, md, klines, ind, timeline, finals,
                 consensus, verdict, crowd, mc):
    tf = TIMEFRAMES[args.timeframe]
    return {"version": __version__, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeframe": args.timeframe, "timeframe_label": tf["label"],
            "horizon": f"{tf['steps']} nến", "rounds": args.rounds,
            "price": md["price"], "price_source": md["price_src"],
            "price_quality": md["price_quality"], "quality_label": md["quality_label"],
            "kline_source": md["kline_src"], "n_candles": len(klines),
            "synthetic": md["synthetic"], "stale": md["stale"],
            "aligned_ratio": md["aligned_ratio"], "basis": md["basis"],
            "market_open": market_status["open"], "market_status_text": market_status["text"],
            "market_status_html": (f"<b style='color:#10b981'>🟢 THỊ TRƯỜNG MỞ</b>"
                                   if market_status["open"] else
                                   f"<b style='color:#ef4444'>🔴 THỊ TRƯỜNG ĐÓNG</b> — {market_status['text'].split('—', 1)[-1].strip()}"),
            "indicators": ind, "consensus": consensus, "verdict": verdict,
            "target": mc["target"], "p10": mc["p10"], "p50": mc["p50"], "p90": mc["p90"],
            "prob_up": mc["prob_up"], "drift": mc["drift"], "n_paths": args.paths, "crowd": crowd,
            "agents": [{"key": f["key"], "title": f["title"], "icon": f["icon"], "stance": f["stance"],
                        "conf": f["conf"], "reasoning": f["reason"], "model": f["model"],
                        "fallback": f.get("fallback", False)} for f in finals],
            "timeline": timeline, "monte_carlo_rows": mc["rows"], "klines": klines[-90:]}


# ----------------------------------------------------------------------
# MỘT PHIÊN ĐẦY ĐỦ
# ----------------------------------------------------------------------
def run_once(cfg, args):
    """Chạy 1 phiên. Trả data dict hoặc None (bỏ qua phiên)."""
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    # Khoá chống 2 tiến trình ghi state cùng lúc
    lock = InstanceLock("arena_run", timeout=1800, lock_dir=out_dir)
    if not lock.acquire():
        print("⚠️ Đã có phiên khác đang chạy (file lock trong output/) — bỏ qua phiên này.")
        return None
    try:
        return _run_once_inner(cfg, args, out_dir)
    finally:
        lock.release()


def _run_once_inner(cfg, args, out_dir):
    # 🎛️ Nhận lệnh Telegram (trade 1h / 15p / 4h / 1d / status...)
    cmd_tf = telegram_poll_commands(cfg, out_dir, send_fn=send_telegram)
    tf_key = args.timeframe
    if cmd_tf and cmd_tf in TIMEFRAMES:
        tf_key = cmd_tf
        args.timeframe = cmd_tf
        print(f"🎛️ Nhận lệnh Telegram: chuyển khung phiên này sang {TIMEFRAMES[tf_key]['label']}")

    market_status = status_info(now_utc(), cfg)

    if args.skip_closed and not market_status["open"]:
        print(f"⏭️ {market_status['text']} — bỏ qua phiên (--skip-closed).")
        return None

    print("=" * 64)
    print(f"🥇 XAU/USD AI DEBATE ARENA — phiên {time.strftime('%H:%M:%S')} (v{__version__})")
    print(f"Khung: {TIMEFRAMES[tf_key]['label']} · {args.rounds} vòng · {args.voters} cử tri · {args.paths} kịch bản")
    keys = [n for n, k in (("OpenRouter", cfg["openrouter_api_key"]), ("Gemini", cfg["gemini_api_key"]),
                           ("Groq", cfg["groq_api_key"]), ("Cohere", cfg["cohere_api_key"])) if k]
    print(f"API: {' + '.join(keys) if keys else 'CHẾ ĐỘ MẪU (chưa có key)'}")
    print("Định tuyến: " + " · ".join(f"{a['title']}→{agent_conf(cfg, a['key'])['provider']}/{agent_conf(cfg, a['key'])['model']}" for a in AGENTS))
    print(f"⏰ {market_status['text']}")
    print("-" * 64)

    # 📡 DỮ LIỆU THỊ TRƯỜNG (giá realtime chuẩn sàn + nến căn chỉnh về spot)
    md = build_market_data(cfg, tf_key)
    price, klines = md["price"], md["klines"]
    ind = compute_indicators(klines, tf_key)
    data_note = md["quality_label"]
    if md.get("stale"):
        data_note += f" · ⏳ nến cũ ({md['stale_text']})"
    if md.get("synthetic"):
        data_note += " · KHÔNG giao dịch trên dữ liệu mẫu"
    print(f"⚡ Giá realtime: ${price:,.2f} ({md['price_src']}) — {md['quality_label']}")
    print(f"📊 {len(klines)} nến ({md['kline_src']}) — RSI {ind['rsi']:.1f} · vol {ind['vol']*100:.2f}%/nến · {ind['trend']}")
    if md.get("synthetic"):
        print("⛔ CẢNH BÁO: dữ liệu là GIẢ LẬP (offline) — AI Trader sẽ không giao dịch phiên này.")

    # 🧠 Snapshot cho hội đồng — AI biết thị trường nghỉ hay mở
    hist = history_block(out_dir)
    snap = market_snapshot(price, md["price_src"], klines, ind, tf_key, args.context,
                           history=hist, market_status=market_status, data_note=data_note)
    if hist:
        print("🧠 Đã đưa lịch sử các phiên gần đây vào bối cảnh cho hội đồng AI.")

    # Vòng tranh luận — phiên nghỉ giảm còn 1 vòng để tiết kiệm token (config được)
    rounds = args.rounds
    if not market_status["open"] and rounds > 1:
        rounds = int(cfg.get("data", {}).get("closed_rounds", 1))
        print(f"🌙 Thị trường đóng → rút gọn còn {rounds} vòng tranh luận.")
    timeline, _ = run_debate(cfg, snap, rounds)
    finals = timeline[-1]["entries"]
    consensus, verdict = compute_consensus(finals, tf_key)
    crowd = simulate_crowd(finals, args.voters, ind["momentum"], seed=args.seed)
    mc = run_monte_carlo(price, TIMEFRAMES[tf_key]["steps"], ind["vol"], consensus, ind["momentum"], args.paths, seed=args.seed)

    print("-" * 64)
    for f in finals:
        fb = " · ⚠️MẪU" if f.get("fallback") else ""
        print(f"  {f['icon']} {f['title']:<28} tâm lý {f['stance']:+.2f} (tự tin {f['conf']*100:.0f}%) — {f['reason'][:80]}{fb}")
    print(f"🧠 Đồng thuận ròng: {consensus:+.3f} → {verdict}")
    print(f"🗳️ Đám đông {args.voters} cử tri: Mua {crowd['bull']} · Trung lập {crowd['neu']} · Bán {crowd['bear']}")
    print(f"📈 Mục tiêu ${mc['target']:,.2f} · P10-P90: ${mc['p10']:,.2f} – ${mc['p90']:,.2f} · P(tăng) {mc['prob_up']*100:.1f}%")

    bt = run_backtest(klines, tf_key)
    if bt:
        print(f"📈 Backtest {tf_key}: win rate {bt['win_rate']}% ({bt['trades']} lệnh) · "
              f"lãi/lỗ {bt['total_return_pct']:+.2f}% · PF {bt['profit_factor'] if bt['profit_factor'] is not None else '∞'} · "
              f"DD tối đa {bt['max_drawdown_pct']}%")
    else:
        print(f"📈 Backtest: chưa đủ dữ liệu lịch sử cho khung {tf_key}.")
    print("=" * 64)

    data = build_result(args, market_status, md, klines, ind, timeline, finals, consensus, verdict, crowd, mc)
    write_json(os.path.join(out_dir, "simulation_latest.json"), data)
    generate_dashboard_html(data, os.path.join(out_dir, "dashboard.html"))
    if bt:
        save_backtest_history({**bt, "consensus": consensus, "verdict": verdict, "target": mc["target"],
                               "market_open": market_status["open"], "price_quality": md["price_quality"]}, out_dir)

    # 🧠 Ghi nhớ phiên
    try:
        st_mem = load_trader_state(out_dir)
        save_session(out_dir, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "tf": tf_key,
            "price": price, "price_src": md["price_src"], "price_quality": md["price_quality"],
            "consensus": consensus, "verdict": verdict,
            "target": mc["target"], "p10": mc["p10"], "p90": mc["p90"], "prob_up": mc["prob_up"],
            "market_open": market_status["open"],
            "trader_action": (st_mem.get("positions") or [{}])[0].get("dir")
                             if st_mem.get("positions") else ("hold" if not st_mem.get("history") else "closed"),
        })
    except Exception:
        pass

    # 💼 AI Trader
    trader_info = None
    if not args.no_trader:
        try:
            st, events, decided, block_reason = trader_step(
                cfg, args, ind, mc, consensus, verdict, finals, price, klines, out_dir,
                market_status, md, send_fn=send_telegram)
            trader_summary(st, cfg, out_dir, send_fn=send_telegram)
            trader_info = trader_status_line(st, price)
            if block_reason:
                trader_info = (trader_info or "") + f" · {block_reason}"
        except Exception as e:
            print(f"⚠️ AI Trader lỗi (không làm hỏng phiên): {e}")

    # 📨 Báo cáo phiên
    try:
        send_session_report(cfg, price, md["price_src"], md["quality_label"], consensus, verdict,
                            finals, mc, bt, trader_info, out_dir, market_status=market_status,
                            market_data=md, send_fn=send_telegram)
    except Exception as e:
        print(f"⚠️ Gửi báo cáo Telegram lỗi: {e}")
    return data


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="XAU/USD AI Debate Arena — backend v2 (GitHub Actions / VPS)")
    ap.add_argument("--timeframe", choices=list(TIMEFRAMES), default="1h")
    ap.add_argument("--rounds", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--voters", type=int, default=80)
    ap.add_argument("--paths", type=int, default=300)
    ap.add_argument("--context", default="")
    ap.add_argument("--watch", type=int, default=0, help="Tự chạy lại mỗi N phút (0 = tắt) — tự bỏ qua phiên nghỉ")
    ap.add_argument("--run-closed", action="store_true", help="(watch) chạy cả khi thị trường đóng")
    ap.add_argument("--skip-closed", action="store_true", help="Bỏ qua phiên nếu thị trường đang đóng")
    ap.add_argument("--serve", type=int, default=0, help="Web server xem dashboard")
    ap.add_argument("--out", default="output")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-trader", action="store_true")
    ap.add_argument("--allow-mock-trading", action="store_true", help="(dev) cho phép trade trên dữ liệu mẫu")
    ap.add_argument("--force-summary", action="store_true")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--test-keys", action="store_true")
    ap.add_argument("--market-status", action="store_true", help="In trạng thái thị trường rồi thoát")
    args = ap.parse_args()
    cfg = load_config()

    if args.test_keys:
        from arena.llm import check_api_keys
        check_api_keys(cfg)
        return

    if args.test_telegram:
        ok = send_telegram("✅ *Kết nối Telegram thành công!*\nXAU/USD AI Debate Arena sẽ gửi báo cáo mỗi phiên, "
                           "báo cáo lệnh và tổng kết định kỳ cho bạn.", cfg)
        print("✅ Đã gửi tin test thành công." if ok else "❌ Gửi thất bại — kiểm tra TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
        return

    if args.market_status:
        s = status_info(now_utc(), cfg)
        print(s["text"])
        if s.get("open") and s.get("next_close"):
            print(f"Đóng cửa phiên tiếp theo: {s['next_close']}")
        return

    if args.force_summary:
        st = load_trader_state(args.out)
        trader_summary(st, cfg, args.out, force=True, send_fn=send_telegram)
        print("✅ Đã buộc tổng kết. Xem output/summary_latest.txt (hoặc Telegram).")
        return

    if args.watch > 0:
        from arena.scheduler import watch_loop
        sys.exit(watch_loop(cfg, args, run_once))

    try:
        run_once(cfg, args)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)

    if args.serve:
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        os.chdir(args.out)
        handler = SimpleHTTPRequestHandler
        handler.extensions_map = {**handler.extensions_map, ".html": "text/html; charset=utf-8"}
        print(f"🌐 Dashboard tại: http://localhost:{args.serve}/dashboard.html  (Ctrl+C để dừng)")
        ThreadingHTTPServer(("0.0.0.0", args.serve), handler).serve_forever()


if __name__ == "__main__":
    main()
