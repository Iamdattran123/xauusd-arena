# -*- coding: utf-8 -*-
"""LLM — định tuyến 4 nhà cung cấp + chuỗi dự phòng + kiểm tra key."""
import json
import os
import time
import urllib.error
import urllib.request

from .config import FALLBACK_CHAIN, OR_FALLBACK_MODELS, PROVIDER_META, UA, agent_conf


def _http_post(url, payload, headers, timeout=90):
    """POST JSON → dict. Ném lỗi kèm .status khi HTTP lỗi."""
    hdrs = dict(headers)
    hdrs.setdefault("User-Agent", UA["User-Agent"])  # bắt buộc UA trình duyệt (Groq chặn UA bot)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", errors="ignore"))
        except Exception:
            body = {}
        err = RuntimeError(f"HTTP {e.code}: {json.dumps(body, ensure_ascii=False)[:200]}")
        err.status = e.code
        raise err


def extract_json(text):
    """Bóc JSON từ văn bản LLM (bỏ <think>, markdown, chữ thừa)."""
    s = str(text or "")
    for tag in ("<think>", "</think>"):
        s = s.replace(tag, "")
    s = s.replace("```json", "").replace("```", "")
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return o
    except Exception:
        pass
    for i in range(len(s)):
        if s[i] != "{":
            continue
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(s[i:j + 1])
                        if isinstance(o, dict):
                            return o
                    except Exception:
                        pass
                    break
    return None


def _api_key(cfg, provider):
    meta = PROVIDER_META.get(provider)
    if not meta:
        return ""
    return cfg.get(meta["env"].lower()) or os.getenv(meta["env"], "")


def call_llm(role, prompt, cfg, provider=None, model=None):
    """Gọi LLM theo provider của role → trả cấu trúc thống nhất."""
    if role == "trader":
        conf = cfg.get("trader", {})
        if isinstance(conf, str):
            conf = {"provider": "openrouter", "model": conf}
        provider = provider or conf.get("provider", "openrouter")
        model = model or conf.get("model", "")
    else:
        conf = agent_conf(cfg, role)
        provider = provider or conf["provider"]
        model = model or conf["model"]

    meta = PROVIDER_META.get(provider)
    if not meta:
        raise ValueError(f"Nhà cung cấp không hợp lệ: {provider}")
    key = _api_key(cfg, provider)
    if not key:
        raise ValueError(f"Thiếu API key {meta['env']} cho nhà cung cấp {provider}")

    if meta["style"] == "google":
        url = f"{meta['base']}/{model}:generateContent?key={key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0.4, "maxOutputTokens": 700}}
        data = _http_post(url, payload, {"Content-Type": "application/json", **meta["extra_headers"]})
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"Gemini trả lỗi: {json.dumps(data, ensure_ascii=False)[:200]}")
        usage = data.get("usageMetadata", {})
        ptok = int(usage.get("promptTokenCount", 0) or 0)
        ctok = int(usage.get("candidatesTokenCount", 0) or 0)
    elif meta["style"] == "cohere":
        url = meta["url"]
        payload = {"model": model,
                   "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                   "temperature": 0.4, "max_tokens": 700}
        data = _http_post(url, payload, {"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                         **meta["extra_headers"]})
        try:
            parts = data["message"]["content"]
            text = "".join(c.get("text", "") for c in parts if isinstance(c, dict))
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"Cohere trả lỗi: {json.dumps(data, ensure_ascii=False)[:200]}")
        usage = data.get("usage", {})
        tk = usage.get("tokens", {}) or {}
        billed = usage.get("billed_units", {}) or {}
        ptok = int(tk.get("input_tokens", 0) or billed.get("input_tokens", 0) or 0)
        ctok = int(tk.get("output_tokens", 0) or billed.get("output_tokens", 0) or 0)
    else:  # openai-style (groq / openrouter)
        url = meta["url"]
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   "temperature": 0.4, "max_tokens": 700}
        data = _http_post(url, payload, {"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                         **meta["extra_headers"]})
        try:
            msg = data["choices"][0]["message"]
            text = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"{provider} trả lỗi: {json.dumps(data, ensure_ascii=False)[:200]}")
        usage = data.get("usage", {})
        ptok = int(usage.get("prompt_tokens", 0) or 0)
        ctok = int(usage.get("completion_tokens", 0) or 0)

    if not text:
        raise ValueError(f"{provider} trả về nội dung rỗng")
    return {"text": text, "prompt_tokens": ptok, "completion_tokens": ctok, "model": model, "provider": provider}


def _build_chain(cfg, provider, model):
    chain = [(provider, model)]
    for p, m in FALLBACK_CHAIN.get(provider, []):
        if (p, m) not in chain:
            chain.append((p, m))
    if provider != "openrouter" and _api_key(cfg, "openrouter"):
        for m in OR_FALLBACK_MODELS:
            if ("openrouter", m) not in chain:
                chain.append(("openrouter", m))
    return chain


def _normalize_json(j, round_no):
    j.setdefault("confidence", 0.7)
    if round_no == 2:
        if not j.get("revised_sentiment") and j.get("sentiment_score") is not None:
            j["revised_sentiment"] = j["sentiment_score"]
    return j


def call_agent_json(agent, round_no, prev, snap, cfg):
    """Gọi LLM cho 1 agent theo chuỗi dự phòng (retry 429) → dict đã parse."""
    from .debate import build_prompt
    role = agent["key"]
    chosen = agent_conf(cfg, role)
    chain = _build_chain(cfg, chosen["provider"], chosen["model"])
    prompt = build_prompt(agent, round_no, prev, snap)
    last_err = ""
    for ci, (provider, model) in enumerate(chain):
        try:
            res = call_llm(role, prompt, cfg, provider=provider, model=model)
            j = extract_json(res["text"])
            if not j:
                last_err = "JSON không hợp lệ"
                if ci == 0:
                    print(f"  🔁 {agent['title']} ({provider}/{model}) trả lời sai định dạng — thử lại...")
                continue
            return {"json": _normalize_json(j, round_no), "model": f"{provider}/{model}", "provider": provider,
                    "prompt_tokens": res["prompt_tokens"], "completion_tokens": res["completion_tokens"]}
        except Exception as e:
            last_err = str(e)
            status = getattr(e, "status", 0)
            is429 = status == 429 or "429" in last_err
            if status == 403:
                while ci + 1 < len(chain) and chain[ci + 1][0] == provider:
                    ci += 1
                if ci + 1 < len(chain):
                    print(f"  🔄 {agent['title']} ({provider}) bị từ chối (403) — chuyển thẳng {chain[ci+1][0]}/{chain[ci+1][1]}")
                else:
                    print(f"  ⚠️ {agent['title']} hết chuỗi dự phòng — dùng dữ liệu mẫu. ({last_err[:80]})")
                    break
                ci += 1
                continue
            if is429:
                print(f"  ⏳ {agent['title']} ({provider}/{model}) bị 429 (rate limit) — chờ 5s thử lại...")
                time.sleep(5)
                try:
                    res = call_llm(role, prompt, cfg, provider=provider, model=model)
                    j = extract_json(res["text"])
                    if j:
                        return {"json": _normalize_json(j, round_no), "model": f"{provider}/{model}",
                                "provider": provider, "prompt_tokens": res["prompt_tokens"],
                                "completion_tokens": res["completion_tokens"]}
                    last_err = "JSON không hợp lệ (sau retry)"
                except Exception as e2:
                    last_err = str(e2)
            if ci < len(chain) - 1:
                print(f"  🔄 {agent['title']} ({provider}/{model}) lỗi: {last_err[:100]} → thử {chain[ci+1][0]}/{chain[ci+1][1]}...")
            else:
                print(f"  ⚠️ {agent['title']} hết chuỗi dự phòng — dùng dữ liệu mẫu. ({last_err[:100]})")
    raise RuntimeError(last_err or "không gọi được LLM")


def mock_agent(agent, round_no):
    from .config import MOCK
    m = MOCK[agent["key"]][round_no]
    if round_no == 1:
        return {"stance": m[0], "conf": m[1], "reason": m[2], "critique": None, "model": "(mẫu)", "fallback": True}
    if round_no == 2:
        return {"critique": m[0], "stance": m[1], "conf": m[2], "reason": m[3], "model": "(mẫu)", "fallback": True}
    return {"stance": m[0], "conf": m[1], "reason": m[2], "critique": None, "model": "(mẫu)", "fallback": True}


def check_api_keys(cfg):
    """Gọi 1 request nhỏ tới từng provider để kiểm tra key còn hiệu lực không."""
    print("🔑 Kiểm tra 4 API key (không lộ key):")
    for name, env in (("OpenRouter", "OPENROUTER_API_KEY"), ("Groq", "GROQ_API_KEY"),
                      ("Cohere", "COHERE_API_KEY"), ("Gemini", "GEMINI_API_KEY")):
        key = cfg.get(env.lower(), "")
        if not key:
            print(f"  ❌ {name:<11} CHƯA đặt key ({env})")
            continue
        try:
            if env == "OPENROUTER_API_KEY":
                url = "https://openrouter.ai/api/v1/auth/key"
            elif env == "GROQ_API_KEY":
                url = "https://api.groq.com/openai/v1/models"
            elif env == "COHERE_API_KEY":
                url = "https://api.cohere.com/v1/models"
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key,
                                                       "User-Agent": UA["User-Agent"]})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
                n = len(d.get("data", [])) if isinstance(d.get("data"), list) else "OK"
                print(f"  ✅ {name:<11} key HOẠT ĐỘNG ({n} models)")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:150]
            print(f"  ❌ {name:<11} key LỖI: HTTP {e.code} — {body}")
        except Exception as e:
            print(f"  ❌ {name:<11} lỗi: {str(e)[:100]}")
    print("💡 Nếu Groq báo 403/1010 → key sai hoặc bị chặn: tạo key mới tại console.groq.com/keys")
