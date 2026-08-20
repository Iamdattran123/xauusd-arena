# -*- coding: utf-8 -*-
"""Báo cáo: Telegram + file text + dashboard HTML."""
import json
import os
import time
import urllib.error
import urllib.request

from .config import UA
from . import datasources
from .trader import load_trader_state, trader_perf_line


def send_telegram(text, cfg):
    """Gửi tin Telegram — kiểm tra response thật (ok=true)."""
    token = cfg.get("trader", {}).get("telegram_token", "")
    chat = cfg.get("trader", {}).get("telegram_chat_id", "")
    if not token or not chat:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("ok"):
            return True
        print(f"⚠️ Telegram trả lỗi: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return False
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='ignore')[:200]
        if e.code == 401:
            print(f"⚠️ Telegram 401 — TOKEN BOT SAI trên GitHub Secret! (token hiện dài {len(token)} ký tự, "
                  f"bot token chuẩn ~46). Sửa: Settings → Secrets → TELEGRAM_BOT_TOKEN.")
        else:
            print(f"⚠️ Telegram HTTP {e.code}: {detail}")
        return False
    except Exception as e:
        print(f"⚠️ Telegram lỗi: {e}")
        return False


def _telegram_ok(cfg):
    return bool(cfg.get("trader", {}).get("telegram_token") and cfg.get("trader", {}).get("telegram_chat_id"))


def send_session_report(cfg, price, price_src, quality_label, consensus, verdict, finals, mc, bt,
                        trader_info=None, out_dir=None, market_status=None, market_data=None, send_fn=None):
    """📊 Báo cáo MỖI PHIÊN — LUÔN ghi file; gửi Telegram nếu có token."""
    if not cfg.get("trader", {}).get("report_every_session", True):
        return False
    lines = ["📊 *XAU/USD AI DEBATE — BÁO CÁO PHIÊN*", "━━━━━━━━━━━━━━━━━",
             f"⏱️ {time.strftime('%d/%m/%Y %H:%M')} · {price_src}",
             f"💰 Giá: ${price:,.2f} · {quality_label}"]
    if market_status:
        lines.append(market_status["text"])
    if market_data and market_data.get("basis") and abs(market_data["basis"]) > 1 and not market_data.get("synthetic"):
        lines.append(f"🔀 Căn chỉnh nến về spot: {market_data['aligned_ratio']*100:.2f}% "
                     f"(chênh gốc {market_data['basis']:+,.2f}$)")
    if market_data and market_data.get("stale"):
        lines.append(f"⏳ Dữ liệu cũ: {market_data.get('stale_text', 'nến cuối đã lâu')}")
    if datasources.PRICE_INFO.get("spot") and datasources.PRICE_INFO.get("futures") \
            and abs(datasources.PRICE_INFO["spot"] - datasources.PRICE_INFO["futures"]) > 1:
        lines.append(f"💱 Spot XAUUSD: ${datasources.PRICE_INFO['spot']:,.2f} · Futures GC=F: "
                     f"${datasources.PRICE_INFO['futures']:,.2f} (chênh {datasources.PRICE_INFO['spot']-datasources.PRICE_INFO['futures']:+,.2f})")
    lines.append(f"🧠 Đồng thuận: {consensus:+.3f} → {verdict}")
    for f in finals:
        fb = " ⚠️dữ liệu mẫu" if f.get("fallback") else ""
        lines.append(f"  {f.get('icon', '🤖')} {f['title']}: {f['stance']:+.2f} (tự tin {f['conf']*100:.0f}%){fb}")
    n_fb = sum(1 for f in finals if f.get("fallback"))
    if n_fb:
        lines.append(f"⚠️ {n_fb}/4 chuyên gia dùng DỮ LIỆU MẪU — kết quả phiên chỉ mang tính tham khảo!")
    if mc:
        lines.append(f"🎯 Mục tiêu ${mc['target']:,.2f} · P10-P90 ${mc['p10']:,.2f} – ${mc['p90']:,.2f} · P(tăng) {mc['prob_up']*100:.0f}%")
    if trader_info:
        lines.append(f"💼 AI Trader: {trader_info}")
    try:
        st_perf = load_trader_state(out_dir) if out_dir else None
        if st_perf:
            lines.append(trader_perf_line(st_perf))
    except Exception:
        pass
    if bt:
        lines.append(f"📈 Backtest heuristic {bt.get('tf', '')} (chiến lược mẫu): win {bt['win_rate']}% "
                     f"({bt['trades']} lệnh) · {bt['total_return_pct']:+.2f}%"
                     + (f" · bỏ qua {bt.get('skipped', 0)} tín hiệu bởi bộ lọc" if bt.get('filtered') else ""))
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append("_Bạn là người ra quyết định cuối cùng._")
    text = "\n".join(lines)

    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "latest_report.txt"), "w", encoding="utf-8") as f:
                f.write(text)
            print(f"📄 Đã lưu báo cáo phiên: {out_dir}/latest_report.txt")
        except Exception as e:
            print(f"⚠️ Không lưu được file báo cáo: {e}")

    if not _telegram_ok(cfg):
        print("⚠️ Telegram CHƯA cấu hình — thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
        return False
    ok = (send_fn or send_telegram)(text, cfg)
    if ok:
        print("📨 Đã gửi báo cáo phiên qua Telegram.")
    return ok


# ----------------------------------------------------------------------
# DASHBOARD HTML
# ----------------------------------------------------------------------
def generate_dashboard_html(data, out_path):
    agents_html = ""
    for a in data["agents"]:
        col = "#10b981" if a["stance"] > 0 else "#ef4444" if a["stance"] < 0 else "#94a3b8"
        lbl = ("MUA MẠNH" if a["stance"] >= 0.3 else "MUA" if a["stance"] >= 0.1 else
               "TRUNG LẬP" if a["stance"] > -0.1 else "BÁN" if a["stance"] > -0.3 else "BÁN MẠNH")
        pct = (a["stance"] + 1) / 2 * 100
        fb = " · ⚠️ dự phòng" if a.get("fallback") else ""
        agents_html += (f'<div style="background:#16223a;border:1px solid #1e2c47;border-radius:10px;padding:12px;border-left:4px solid {col};">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;"><b style="font-size:13px;">{a["icon"]} {a["title"]}</b>'
                        f'<span style="background:{col};color:#04121a;padding:2px 8px;border-radius:99px;font-size:10.5px;font-weight:800;">{lbl}</span></div>'
                        f'<div style="font-size:10.5px;color:#8ea3c0;font-family:monospace;margin-bottom:6px;">{a["model"]}{fb}</div>'
                        f'<div style="height:7px;background:#0b1220;border-radius:99px;overflow:hidden;margin-bottom:4px;"><div style="height:100%;width:{pct:.1f}%;background:{col};"></div></div>'
                        f'<div style="font-size:11px;color:#8ea3c0;display:flex;justify-content:space-between;"><span>Tâm lý: {a["stance"]:+.2f}</span><span>Tự tin: {a["conf"]*100:.0f}%</span></div>'
                        f'<div style="font-size:12px;color:#c3d2e8;background:#0b1220;border-radius:8px;padding:8px 10px;margin-top:8px;border-left:3px solid #1e2c47;">{a["reasoning"]}</div></div>')
    d = data
    market_html = d.get("market_status_html", "")
    html = DASHBOARD_TEMPLATE
    for k, v in [("@@TITLE@@", f"XAU/USD — Mô phỏng {d['timeframe_label']} · {d['generated_at']}"),
                 ("@@PRICE@@", f"${d['price']:,.2f}"), ("@@PRICE_SRC@@", d["price_source"]),
                 ("@@MARKET@@", market_html),
                 ("@@KLINES@@", d["kline_source"]), ("@@CONSENSUS@@", f"{d['consensus']:+.3f}"),
                 ("@@CONSENSUS_COLOR@@", "#10b981" if d["consensus"] > 0 else "#ef4444"),
                 ("@@VERDICT@@", d["verdict"]), ("@@TARGET@@", f"${d['target']:,.2f}"),
                 ("@@RANGE@@", f"${d['p10']:,.2f} – ${d['p90']:,.2f}"), ("@@PROB@@", f"{d['prob_up']*100:.1f}%"),
                 ("@@HORIZON@@", f"{d['timeframe_label']} · {d['horizon']} · {d['n_paths']} kịch bản"),
                 ("@@CROWD@@", f"Mua {d['crowd']['bull']} · Trung lập {d['crowd']['neu']} · Bán {d['crowd']['bear']} ({d['crowd']['n_voters']} cử tri)"),
                 ("@@AGENTS@@", agents_html),
                 ("@@DATA@@", json.dumps({"klines": d["klines"], "mc_rows": d["monte_carlo_rows"],
                                          "steps": len(d["monte_carlo_rows"]) - 1, "crowd": d["crowd"],
                                          "verdict": d["verdict"], "consensus": d["consensus"],
                                          "ind": d["indicators"], "timeframe": d["timeframe"]},
                                         ensure_ascii=False).replace("</", "<\\/"))]:
        html = html.replace(k, v)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✨ Đã xuất dashboard: {out_path}")


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<style>
body{background:#0b1220;color:#e2e8f0;font-family:'Segoe UI',sans-serif;margin:0;padding:20px;}
.wrap{max-width:1200px;margin:0 auto;}
.hd{background:linear-gradient(135deg,#0f2239,#0e1a2e);border:1px solid #1e2c47;border-radius:14px;padding:18px 22px;margin-bottom:14px;display:flex;flex-wrap:wrap;gap:12px;align-items:center;}
.hd h1{margin:0;font-size:18px;} .hd p{margin:3px 0 0;color:#8ea3c0;font-size:12px;}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:14px;}
.mc{background:#111c30;border:1px solid #1e2c47;border-radius:12px;padding:12px 14px;}
.mc .l{font-size:10.5px;text-transform:uppercase;color:#8ea3c0;letter-spacing:.6px;font-weight:700;}
.mc .v{font-size:20px;font-weight:800;margin-top:3px;font-family:Consolas,monospace;}
.panel{background:#111c30;border:1px solid #1e2c47;border-radius:14px;padding:16px 18px;margin-bottom:14px;}
.panel h2{font-size:13px;text-transform:uppercase;letter-spacing:.8px;color:#8ea3c0;margin:0 0 12px;}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:14px;}
@media(max-width:900px){.grid2{grid-template-columns:1fr;}}
.foot{margin-top:20px;text-align:center;font-size:11px;color:#8ea3c0;}
</style></head><body><div class="wrap">
<div class="hd">
  <div><h1>🥇 XAU/USD AI Debate Arena — Dashboard</h1>
  <p>Mô phỏng đa tác nhân + đám đông bỏ phiếu + Monte Carlo</p>
  <p>@@MARKET@@</p></div>
  <div style="margin-left:auto;text-align:right;font-size:12px;color:#8ea3c0;">
    Giá: <b style="color:#f5c518;font-size:16px;">@@PRICE@@</b><br>@@PRICE_SRC@@ · @@KLINES@@
  </div>
</div>
<div class="metrics">
  <div class="mc"><div class="l">🧠 Đồng thuận ròng</div><div class="v" style="color:@@CONSENSUS_COLOR@@">@@CONSENSUS@@</div><div style="font-size:11px;color:#8ea3c0;">@@VERDICT@@</div></div>
  <div class="mc"><div class="l">🎯 Giá mục tiêu</div><div class="v" style="color:#38bdf8;">@@TARGET@@</div><div style="font-size:11px;color:#8ea3c0;">@@HORIZON@@</div></div>
  <div class="mc"><div class="l">📊 Biên độ 80% (P10-P90)</div><div class="v" style="font-size:15px;padding-top:5px;">@@RANGE@@</div></div>
  <div class="mc"><div class="l">📈 Xác suất tăng</div><div class="v" style="color:#10b981;">@@PROB@@</div><div style="font-size:11px;color:#8ea3c0;">@@CROWD@@</div></div>
</div>
<div class="grid2">
  <div class="panel"><h2>🕯️ Nến thực tế + Dự báo Monte Carlo</h2><div id="chart" style="height:480px;"></div></div>
  <div class="panel"><h2>🤖 Lập trường các chuyên gia</h2>@@AGENTS@@</div>
</div>
<div class="panel"><h2>🗳️ Đám đông bỏ phiếu</h2><div id="vote" style="height:260px;"></div></div>
<div class="foot">⚠️ Công cụ mô phỏng AI phục vụ nghiên cứu & giáo dục — KHÔNG phải lời khuyên tài chính.</div>
</div>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.27.0/plotly.min.js"></script>
<script>
const D = @@DATA@@;
const k = D.klines.map(x=>({t:new Date(x.t),o:x.o,h:x.h,l:x.l,c:x.c}));
const mc = D.mc_rows.map((r,i)=>({t:new Date(k[k.length-1].t.getTime()+(i+1)*(D.timeframe==='1D'?86400000:D.timeframe==='4h'?14400000:D.timeframe==='1h'?3600000:900000)),mean:r.mean,p10:r.p10,p90:r.p90}));
Plotly.newPlot('chart',[
 {x:k.map(x=>x.t),open:k.map(x=>x.o),high:k.map(x=>x.h),low:k.map(x=>x.l),close:k.map(x=>x.c),type:'candlestick',name:'Thực tế',increasing:{line:{color:'#10b981'}},decreasing:{line:{color:'#ef4444'}}},
 {x:mc.map(x=>x.t),y:mc.map(x=>x.p90),type:'scatter',mode:'lines',name:'P90',line:{color:'rgba(56,189,248,.4)',dash:'dot'}},
 {x:mc.map(x=>x.t),y:mc.map(x=>x.p10),type:'scatter',mode:'lines',name:'P10-P90',fill:'tonexty',fillcolor:'rgba(56,189,248,.08)',line:{color:'rgba(56,189,248,.4)',dash:'dot'}},
 {x:mc.map(x=>x.t),y:mc.map(x=>x.mean),type:'scatter',mode:'lines',name:'Kỳ vọng',line:{color:'#f5c518',width:2}}
],{paper_bgcolor:'#111c30',plot_bgcolor:'#0b1220',font:{color:'#8ea3c0'},xaxis:{gridcolor:'#1e2c47',type:'date'},yaxis:{gridcolor:'#1e2c47',title:'USD/oz'},margin:{t:20,l:60,r:20,b:40},legend:{orientation:'h'}});
const c = D.crowd;
Plotly.newPlot('vote',[{values:[c.bull,c.neu,c.bear],labels:['Mua ('+c.bull+')','Trung lập ('+c.neu+')','Bán ('+c.bear+')'],type:'pie',hole:.55,marker:{colors:['#10b981','#64748b','#ef4444']},textinfo:'label+percent',textfont:{color:'#e2e8f0'}}],{paper_bgcolor:'#111c30',font:{color:'#8ea3c0'},showlegend:false,margin:{t:10,b:10,l:10,r:10}});
</script></body></html>
"""
