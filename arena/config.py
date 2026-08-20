# -*- coding: utf-8 -*-
"""Cấu hình + hằng số dùng chung toàn hệ thống.

Key API đọc từ biến môi trường (GitHub Secrets / systemd Environment) và có thể
ghi đè bằng `config.json` đặt cạnh thư mục gốc dự án (xem config.json.example).
"""
import json
import os

# ----------------------------------------------------------------------
# TIMEFRAMES — định nghĩa khung giao dịch, trọng số hội đồng, biến động mẫu
# ----------------------------------------------------------------------
TIMEFRAMES = {
    "15m": {"label": "15 phút", "step_min": 15,  "yahoo_interval": "15m", "yahoo_range": "2d",  "binance": "15m", "vol": 0.0009, "steps": 16, "resample": 1, "weights": {"Macro_Analyst": 0.15, "Technical_Analyst": 0.45, "Institutional_Whale": 0.25, "Retail_Crowd": 0.15}},
    "1h":  {"label": "1 giờ",   "step_min": 60,  "yahoo_interval": "60m", "yahoo_range": "5d",  "binance": "1h",  "vol": 0.0018, "steps": 24, "resample": 1, "weights": {"Macro_Analyst": 0.25, "Technical_Analyst": 0.35, "Institutional_Whale": 0.25, "Retail_Crowd": 0.15}},
    "4h":  {"label": "4 giờ",   "step_min": 240, "yahoo_interval": "60m", "yahoo_range": "12d", "binance": "4h",  "vol": 0.0035, "steps": 18, "resample": 4, "weights": {"Macro_Analyst": 0.35, "Technical_Analyst": 0.25, "Institutional_Whale": 0.30, "Retail_Crowd": 0.10}},
    "1D":  {"label": "1 ngày",  "step_min": 1440,"yahoo_interval": "1d",  "yahoo_range": "6mo", "binance": "1d",  "vol": 0.0075, "steps": 30, "resample": 1, "weights": {"Macro_Analyst": 0.40, "Technical_Analyst": 0.15, "Institutional_Whale": 0.35, "Retail_Crowd": 0.10}},
}

AGENTS = [
    {"key": "Macro_Analyst", "title": "Chuyên gia Vĩ mô", "icon": "🏛️",
     "persona": "Bạn là chuyên gia kinh tế vĩ mô cấp cao tại một quỹ đầu tư toàn cầu. Trọng tâm: chính sách lãi suất Fed, chỉ số DXY, lạm phát CPI/PCE, lợi suất trái phiếu Mỹ, rủi ro địa chính trị và vai trò trú ẩn của vàng."},
    {"key": "Technical_Analyst", "title": "Chuyên gia Kỹ thuật", "icon": "📐",
     "persona": "Bạn là chuyên gia phân tích kỹ thuật với 15 năm kinh nghiệm giao dịch vàng. Trọng tâm: RSI, MACD, EMA, ATR, vùng hỗ trợ/kháng cự, mô hình nến và cấu trúc xu hướng."},
    {"key": "Institutional_Whale", "title": "Quỹ & Ngân hàng TW", "icon": "🐋",
     "persona": "Bạn đại diện cho ngân hàng trung ương và quỹ ETF vàng lớn (SPDR Gold, iShares). Trọng tâm: dòng tiền tổ chức, nhu cầu tích trữ, giao dịch OTC, thanh khoản và chi phí cơ hội."},
    {"key": "Retail_Crowd", "title": "Đám đông Nhỏ lẻ", "icon": "👥",
     "persona": "Bạn đại diện cho tâm lý nhà đầu tư cá nhân trên mạng xã hội. Trọng tâm: FOMO, bắt đáy, chốt lời, đòn bẩy, dòng tiền nhỏ lẻ và tâm lý theo đám đông."},
]

# Cấu hình mặc định (ghi đè bằng config.json → models.<AgentKey>)
DEFAULT_AGENT_CONFIG = {
    "Macro_Analyst":        {"provider": "openrouter", "model": "qwen/qwen3-32b"},
    "Technical_Analyst":    {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash-0731"},
    "Institutional_Whale":  {"provider": "cohere",     "model": "command-r-plus-08-2024"},
    "Retail_Crowd":         {"provider": "groq",       "model": "llama-3.3-70b-versatile"},
}

DEFAULT_TRADER_CONFIG = {
    "provider": "openrouter",
    "model": "deepseek/deepseek-v4-flash-0731",
    "risk_pct": 1.0,
    "summary_every": 300,
    "report_every_session": True,
    "telegram_token": "",
    "telegram_chat_id": "",
    # Chỉ ai trong danh sách này mới điều khiển được bot qua Telegram (rỗng = ai cũng được)
    "allowed_user_ids": [],
}

# Endpoint + cách gọi từng nhà cung cấp LLM
PROVIDER_META = {
    "openrouter": {"url": "https://openrouter.ai/api/v1/chat/completions", "env": "OPENROUTER_API_KEY", "style": "openai",
                   "extra_headers": {"HTTP-Referer": "https://localhost", "X-Title": "XAUUSD AI Debate Arena"}},
    "groq":       {"url": "https://api.groq.com/openai/v1/chat/completions", "env": "GROQ_API_KEY", "style": "openai",
                   "extra_headers": {}},
    "cohere":     {"url": "https://api.cohere.com/v2/chat", "env": "COHERE_API_KEY", "style": "cohere",
                   "extra_headers": {"X-Client-Name": "xauusd-arena"}},
    "gemini":     {"base": "https://generativelanguage.googleapis.com/v1beta/models", "env": "GEMINI_API_KEY",
                   "style": "google", "extra_headers": {}},
}

# Chuỗi dự phòng theo từng nhà cung cấp (khi model chính lỗi/429)
FALLBACK_CHAIN = {
    "gemini":     [("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash-lite")],
    "groq":       [("groq", "llama-3.3-70b-versatile"), ("groq", "llama-3.1-8b-instant")],
    "cohere":     [("cohere", "command-r-plus-08-2024"), ("cohere", "command-r-08-2024")],
    "openrouter": [("openrouter", "qwen/qwen3-32b"),
                   ("openrouter", "deepseek/deepseek-v4-flash-0731"),
                   ("openrouter", "deepseek/deepseek-chat"),
                   ("openrouter", "openai/gpt-4o-mini"),
                   ("openrouter", "google/gemma-4-31b-it:free")],
}
# Model OpenRouter dùng làm fallback CHÉO (khi provider khác lỗi hết)
OR_FALLBACK_MODELS = ["qwen/qwen3-32b", "deepseek/deepseek-v4-flash-0731", "openai/gpt-4o-mini", "google/gemma-4-31b-it:free"]

# Dữ liệu mẫu khi chưa có API key (để demo/kiểm thử không cần key)
MOCK = {
    "Macro_Analyst": {
        1: (0.55, 0.80, "DXY suy yếu và kỳ vọng Fed hạ lãi suất từ quý IV củng cố vai trò trú ẩn của vàng; rủi ro địa chính trị duy trì dòng mua phòng thủ của các NHTW."),
        2: ("Quan điểm kỹ thuật quá nhấn mạnh RSI ngắn hạn — vàng trong chu kỳ vĩ mô nới lỏng thường vượt vùng quá mua trong thời gian dài; bỏ qua sức ép DXY.", 0.52, 0.82, "Sau khi cân nhắc phản biện: vẫn duy trì xu hướng tăng vĩ mô nhưng thừa nhận khả năng điều chỉnh kỹ thuật ngắn hạn trước vùng kháng cự; hạ nhẹ mức độ tích cực."),
        3: (0.50, 0.82, "Sau khi cân nhắc phản biện: vẫn duy trì xu hướng tăng vĩ mô nhưng thừa nhận khả năng điều chỉnh kỹ thuật ngắn hạn trước vùng kháng cự; hạ nhẹ mức độ tích cực."),
    },
    "Technical_Analyst": {
        1: (-0.20, 0.75, "RSI tiệm cận vùng quá mua 68-70, MACD có dấu hiệu phân kỳ âm nhẹ; giá chạm vùng kháng cự nhiều lần chưa phá — xác suất điều chỉnh ngắn hạn cao."),
        2: ("Lập luận vĩ mô đúng hướng nhưng chưa định lượng: nếu Fed hạ lãi suất, mô hình nến tăng vẫn chiếm ưu thế; kháng cự có thể bị phá trong 1-2 phiên.", -0.10, 0.72, "Giữ quan điểm thận trọng ngắn hạn: chờ xác nhận phá kháng cự đi kèm khối lượng trước khi đuổi mua; nâng nhẹ điểm do lực vĩ mô hỗ trợ."),
        3: (-0.10, 0.72, "Giữ quan điểm thận trọng ngắn hạn: chờ xác nhận phá kháng cự đi kèm khối lượng trước khi đuổi mua; nâng nhẹ điểm do lực vĩ mô hỗ trợ."),
    },
    "Institutional_Whale": {
        1: (0.40, 0.90, "Lượng vàng do các NHTW nắm giữ tăng tháng thứ 9 liên tiếp; dòng vốn ETF vàng toàn cầu chuyển sang ròng dương, tạo lớp hỗ trợ mua tại vùng giá hiện tại."),
        2: ("Phản biện kỹ thuật chỉ đúng cho nhà giao dịch lướt sóng; dòng tiền tổ chức quan tâm tích trữ 6-12 tháng, đợt điều chỉnh 1-2% không thay đổi chiến lược.", 0.45, 0.88, "Giữ lập trường tích cực: đợt điều chỉnh kỹ thuật nếu xảy ra sẽ là cơ hội gia tăng vị thế của dòng tiền lớn tại vùng hỗ trợ EMA50."),
        3: (0.45, 0.88, "Giữ lập trường tích cực: đợt điều chỉnh kỹ thuật nếu xảy ra sẽ là cơ hội gia tăng vị thế của dòng tiền lớn tại vùng hỗ trợ EMA50."),
    },
    "Retail_Crowd": {
        1: (0.10, 0.60, "Tâm lý mạng xã hội phân hoá: một bộ phận FOMO đuổi mua phá đỉnh, phần còn lại chờ điều chỉnh để bắt đáy; chưa có dòng tiền nhỏ lẻ áp đảo."),
        2: ("Quan điểm quỹ bỏ qua rủi ro thanh khoản: nếu thanh khoản toàn cầu thắt chặt bất ngờ, dòng tiền tổ chức cũng có thể đảo chiều nhanh; nhỏ lẻ thường bị cuốn theo.", 0.05, 0.58, "Sau phản biện, giữ tâm lý trung lập nhẹ: phần lớn nhỏ lẻ đang đứng ngoài quan sát, chỉ tham gia khi có tín hiệu phá đỉnh rõ ràng kèm khối lượng."),
        3: (0.05, 0.58, "Sau phản biện, giữ tâm lý trung lập nhẹ: phần lớn nhỏ lẻ đang đứng ngoài quan sát, chỉ tham gia khi có tín hiệu phá đỉnh rõ ràng kèm khối lượng."),
    },
}

# ----------------------------------------------------------------------
# Giới hạn vận hành
# ----------------------------------------------------------------------
MAX_POSITIONS = 3          # tối đa 3 lệnh mở cùng lúc
MAX_PENDING = 3            # tối đa 3 lệnh chờ cùng lúc
PENDING_EXPIRY_SESSIONS = 8  # lệnh chờ tự hủy sau 8 phiên (chỉ đếm khi thị trường MỞ)
PRICE_SANITY_RANGE = (500.0, 20000.0)   # giá vàng hợp lệ để loại dữ liệu rác
MAX_ALIGN_RATIO = 0.03                   # chênh lệch tối đa 3% khi căn chỉnh nến theo giá spot

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


# ----------------------------------------------------------------------
# Đọc cấu hình
# ----------------------------------------------------------------------
def _config_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def load_config(path=None):
    """Key từ biến môi trường (GitHub Secrets) + ghi đè tùy chọn bằng config.json."""
    cfg = {
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "gemini_api_key":     os.getenv("GEMINI_API_KEY", ""),
        "groq_api_key":       os.getenv("GROQ_API_KEY", ""),
        "cohere_api_key":     os.getenv("COHERE_API_KEY", ""),
        "models": {k: dict(v) for k, v in DEFAULT_AGENT_CONFIG.items()},
        "trader": dict(DEFAULT_TRADER_CONFIG),
        "data": {"price_providers": ["metaapi", "gold_api", "binance", "yahoo"],
                 "kline_providers": ["metaapi", "binance", "yahoo"]},
        "market_hours": {},
        "exness": {}, "metaapi": {},
    }
    p = path or _config_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                user = json.load(f)
            for k in ("openrouter_api_key", "gemini_api_key", "groq_api_key", "cohere_api_key"):
                if user.get(k):
                    cfg[k] = user[k]
            if isinstance(user.get("models"), dict):
                for key, val in user["models"].items():
                    if isinstance(val, str):
                        cfg["models"][key] = {"provider": "openrouter", "model": val}
                    elif isinstance(val, dict) and val.get("model"):
                        cfg["models"][key] = {"provider": val.get("provider", "openrouter"), "model": val["model"]}
            for section in ("trader", "data", "market_hours", "exness", "metaapi"):
                if isinstance(user.get(section), dict):
                    cfg.get(section, {}).update(user[section])
        except Exception as e:
            print(f"⚠️ Lỗi đọc config.json: {e}")

    # Môi trường luôn thắng config.json với các secret nhạy cảm
    if os.getenv("EXNESS_PASSWORD"):
        cfg["exness"]["password"] = os.getenv("EXNESS_PASSWORD")
    if os.getenv("METAAPI_TOKEN"):
        cfg["metaapi"]["token"] = os.getenv("METAAPI_TOKEN")
    if os.getenv("METAAPI_ACCOUNT_ID"):
        cfg["metaapi"]["account_id"] = os.getenv("METAAPI_ACCOUNT_ID")
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        cfg.get("trader", {})["telegram_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"):
        cfg.get("trader", {})["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
    return cfg


def agent_conf(cfg, key):
    c = cfg["models"].get(key, {})
    if isinstance(c, str):
        return {"provider": "openrouter", "model": c}
    return {"provider": c.get("provider", "openrouter"), "model": c.get("model", "")}


def any_api_key(cfg):
    return bool(cfg.get("openrouter_api_key") or cfg.get("gemini_api_key")
                or cfg.get("groq_api_key") or cfg.get("cohere_api_key"))
