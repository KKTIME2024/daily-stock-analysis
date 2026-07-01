"""轻量级每日A股分析：efinance数据 + DDG新闻 + DeepSeek LLM → 7章报告 → Bark推送

数据源：efinance（东方财富）
新闻：DuckDuckGo 免费搜索（事件触发规则）
LLM：DeepSeek V3（兼容 OpenAI API）
推送：Bark POST（CUSTOM_WEBHOOK_URLS，逗号分隔，支持多人）
部署：Linux cron 双时段（09:25 盘前新闻 / 18:00 收盘分析）

用法:
    python dev/run.py                  # 收盘完整分析
    python dev/run.py --mode pre       # 盘前新闻推送
    python dev/run.py --stocks 000002,002607,002373
"""
import os, sys, time, random
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv('.env.local')

import efinance as ef
from openai import OpenAI
from ddgs import DDGS

STOCKS = ["000002", "002607", "002373"]
OUTDIR = "local_reports"
LLM_MODEL = "deepseek-chat"
INDEX_KEYWORDS = ["上证指数", "深证成指", "创业板指", "科创50"]
LLM_PRICE_IN = 2.0
LLM_PRICE_OUT = 8.0

client = OpenAI(
    api_key=os.environ.get("LLM_OPENAI_API_KEY"),
    base_url=os.environ.get("LLM_OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
)

def _sf(val, default=0.0):
    try: return float(val)
    except: return default

def _si(val, default=0):
    try: return int(float(val))
    except: return default

def _arrow(pct):
    return "\u25b2" if pct > 0 else "\u25bc" if pct < 0 else "\u2014"

def _money(v):
    if abs(v) >= 1e8: return f"{v/1e8:+.2f}\u4ebf"
    if abs(v) >= 1e4: return f"{v/1e4:+.0f}\u4e07"
    return f"{v:+.0f}"

def _mcap(v):
    try: return f"{float(v)/1e8:.0f}\u4ebf"
    except: return str(v)

def _retry(func, *args, tries=3, **kwargs):
    for i in range(tries):
        try:
            time.sleep(random.uniform(0.5, 2.0))
            return func(*args, **kwargs)
        except Exception as e:
            if i == tries - 1: raise
            print(f"  [RETRY] {func.__name__} attempt {i+1} failed: {str(e)[:80]}")

def fetch_index_quotes():
    result = {}
    try:
        df = _retry(ef.stock.get_realtime_quotes, ["\u6caa\u6df1\u7cfb\u5217\u6307\u6570"])
        if df is None or df.empty: return result
        for _, row in df.iterrows():
            name = str(row.get("\u80a1\u7968\u540d\u79f0", ""))
            for kw in INDEX_KEYWORDS:
                if kw in name:
                    result[kw] = {"close": _sf(row.get("\u6700\u65b0\u4ef7")), "chg_pct": _sf(row.get("\u6da8\u8dcc\u5e45"))}
                    break
    except Exception as e: print(f"  [WARN] \u6307\u6570: {e}")
    return result

def fetch_sector_rankings(n=5):
    result = {"top": [], "bottom": []}
    try:
        df = _retry(ef.stock.get_realtime_quotes, ["\u884c\u4e1a\u677f\u5757"])
        if df is None or df.empty: return result
        chg_col = "\u6da8\u8dcc\u5e45" if "\u6da8\u8dcc\u5e45" in df.columns else None
        name_col = "\u80a1\u7968\u540d\u79f0" if "\u80a1\u7968\u540d\u79f0" in df.columns else None
        if not chg_col or not name_col: return result
        df[chg_col] = df[chg_col].apply(_sf)
        df = df.dropna(subset=[chg_col])
        for _, row in df.nlargest(n, chg_col).iterrows():
            result["top"].append({"name": str(row[name_col]), "chg_pct": _sf(row[chg_col])})
        for _, row in df.nsmallest(n, chg_col).iterrows():
            result["bottom"].append({"name": str(row[name_col]), "chg_pct": _sf(row[chg_col])})
    except Exception as e: print(f"  [WARN] \u677f\u5757: {e}")
    return result

def fetch_stock_kline(code):
    return _retry(ef.stock.get_quote_history, code, klt=101, fqt=1)

def fetch_stock_fund_flow(code, days=5):
    try:
        df = _retry(ef.stock.get_history_bill, code)
        if df is None or df.empty: return None
        result = []
        for i in range(min(days, len(df))):
            row = df.iloc[-(i+1)]
            result.append({
                "main_net": _sf(row.get("\u4e3b\u529b\u51c0\u6d41\u5165")),
                "small_net": _sf(row.get("\u5c0f\u5355\u51c0\u6d41\u5165")),
                "mid_net": _sf(row.get("\u4e2d\u5355\u51c0\u6d41\u5165")),
                "big_net": _sf(row.get("\u5927\u5355\u51c0\u6d41\u5165")),
                "super_big_net": _sf(row.get("\u8d85\u5927\u5355\u51c0\u6d41\u5165")),
                "close": _sf(row.get("\u6536\u76d8\u4ef7")),
                "chg_pct": _sf(row.get("\u6da8\u8dcc\u5e45")),
            })
        return result
    except Exception as e:
        print(f"  [WARN] \u8d44\u91d1\u6d41({code}): {e}")
        return None

def fetch_stock_base_info(code):
    try:
        info = _retry(ef.stock.get_base_info, code)
        if info is None or info.empty: return {}
        return info.to_dict()
    except: return {}

def fetch_billboard():
    try:
        df = _retry(ef.stock.get_daily_billboard)
        if df is None or df.empty: return []
        result = []
        for _, row in df.head(20).iterrows():
            result.append({
                "name": str(row.get("\u80a1\u7968\u540d\u79f0", "")),
                "code": str(row.get("\u80a1\u7968\u4ee3\u7801", "")),
                "chg_pct": _sf(row.get("\u6da8\u8dcc\u5e45")),
                "net_buy": _sf(row.get("\u9f99\u864e\u699c\u51c0\u4e70\u989d")),
                "turnover": _sf(row.get("\u6362\u624b\u7387")),
                "reason": str(row.get("\u4e0a\u699c\u539f\u56e0", "")),
            })
        return result
    except Exception as e:
        print(f"  [WARN] \u9f99\u864e\u699c: {e}")
        return []

def compute_signals(df):
    if df is None or len(df) < 2: return {}
    last = df.iloc[-1]; prev = df.iloc[-2]
    close = _sf(last["\u6536\u76d8"]); prev_close = _sf(prev["\u6536\u76d8"])
    chg_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0

    closes = [_sf(x) for x in df["\u6536\u76d8"]]; n = len(closes)
    ma5 = round(sum(closes[-5:]) / min(5, n), 3) if n >= 5 else None
    ma10 = round(sum(closes[-10:]) / min(10, n), 3) if n >= 10 else None
    ma20 = round(sum(closes[-20:]) / min(20, n), 3) if n >= 20 else None
    bias = round((close - ma5) / ma5 * 100, 2) if ma5 and ma5 != 0 else None

    if ma5 and ma20:
        trend = "\u4e0a\u5347" if ma5 > ma20 else "\u4e0b\u964d" if ma5 < ma20 else "\u9707\u8361"
    else: trend = "\u6570\u636e\u4e0d\u8db3"

    ma_align = ""
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20: ma_align = "\u591a\u5934\u6392\u5217"
        elif ma5 < ma10 < ma20: ma_align = "\u7a7a\u5934\u6392\u5217"
        else: ma_align = "\u5747\u7ebf\u7f20\u7ed5"

    week5_chg = round((close - closes[max(0,n-5)]) / closes[max(0,n-5)] * 100, 2) if n >= 5 else None
    high52 = round(max(closes[-250:]), 2) if n >= 10 else None
    low52 = round(min(closes[-250:]), 2) if n >= 10 else None

    volumes = [_sf(x) for x in df["\u6210\u4ea4\u91cf"]] if "\u6210\u4ea4\u91cf" in df.columns else []
    vol_today = _si(last.get("\u6210\u4ea4\u91cf"))
    if len(volumes) >= 5:
        avg_vol_5 = sum(volumes[-6:-1]) / 5
        vol_ratio = round(vol_today / avg_vol_5, 2) if avg_vol_5 > 0 else None
    else:
        vol_ratio = None

    return {
        "close": close, "chg_pct": chg_pct, "week5_chg": week5_chg,
        "open": _sf(last.get("\u5f00\u76d8")), "high": _sf(last.get("\u6700\u9ad8")), "low": _sf(last.get("\u6700\u4f4e")),
        "amount": _sf(last.get("\u6210\u4ea4\u989d")), "turnover": _sf(last.get("\u6362\u624b\u7387")),
        "amplitude": _sf(last.get("\u632f\u5e45")), "volume": vol_today, "vol_ratio": vol_ratio,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "bias": bias,
        "trend": trend, "ma_align": ma_align, "high52": high52, "low52": low52,
    }

def search_news(query, max_results=3):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return [{"title": r["title"], "body": r["body"][:200], "href": r["href"]} for r in results]
    except Exception as e:
        print(f"  [WARN] \u65b0\u95fb\u641c\u7d22({query[:20]}): {e}")
        return []

def generate_news_queries(market_data):
    queries = []
    queries.append("\u4eca\u65e5A\u80a1 \u91cd\u5927\u65b0\u95fb \u653f\u7b56")
    queries.append("\u9694\u591c\u7f8e\u80a1 \u9053\u7434\u65af \u7eb3\u65af\u8fbe\u514b \u6536\u76d8")

    tops = [s["name"] for s in market_data.get("sectors", {}).get("top", [])[:2]]
    bots = [s["name"] for s in market_data.get("sectors", {}).get("bottom", [])[:2]]
    for s in tops: queries.append(f"{s} \u677f\u5757 \u5927\u6da8 \u539f\u56e0")
    for s in bots: queries.append(f"{s} \u677f\u5757 \u4e0b\u8dcc \u539f\u56e0")

    for code, sd in market_data.get("stocks", {}).items():
        sig = sd.get("signals", {})
        base = sd.get("base_info") or {}
        fund = sd.get("fund_flow")
        name = sd["name"]

        queries.append(f"{name} {code} \u516c\u544a")

        if abs(sig.get("chg_pct", 0)) > 4:
            queries.append(f"{name} {code} \u6da8\u8dcc \u539f\u56e0")

        vr = sig.get("vol_ratio")
        if vr and vr > 1.5:
            queries.append(f"{name} {code} \u653e\u91cf \u539f\u56e0")

        try:
            if float(base.get("\u5e02\u51c0\u7387", 1)) < 0.5:
                queries.append(f"{name} {code} \u7834\u51c0 \u56f0\u5883")
        except: pass

        try:
            pe = float(base.get("\u5e02\u76c8\u7387(\u52a8)", 20))
            if pe < 0: queries.append(f"{name} {code} \u4e8f\u635f \u4e1a\u7ee9")
        except: pass

        if fund and abs(fund[0].get("main_net", 0)) > 1e8:
            queries.append(f"{name} {code} \u4e3b\u529b\u8d44\u91d1")

    bb = market_data.get("billboard") or []
    for b in sorted(bb, key=lambda x: abs(x.get("net_buy", 0)), reverse=True)[:2]:
        if abs(b.get("net_buy", 0)) > 5e8:
            queries.append(f"{b['name']} \u9f99\u864e\u699c \u673a\u6784")

    seen = set()
    unique = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique[:15]

def _stock_section(name, code, sig, fund_flow, base):
    lines = []
    lines.append(f"\u2501\u2501\u2501 {name} ({code}) \u2501\u2501\u2501")

    c = sig.get("close", "?"); pct = sig.get("chg_pct", 0)
    o = sig.get("open", "?"); h = sig.get("high", "?"); l = sig.get("low", "?")
    amp = sig.get("amplitude", 0); to = sig.get("turnover", 0)
    amt = _money(sig.get("amount", 0)).replace("+", "")
    w5 = sig.get("week5_chg")
    h52 = sig.get("high52"); l52 = sig.get("low52")

    lines.append(f"  \u6536\u76d8  {c}  {_arrow(pct)}{abs(pct):.2f}%  "
                 f"\u5f00{o} \u9ad8{h} \u4f4e{l}  \u632f\u5e45{amp:.1f}%")
    lines.append(f"  \u6210\u4ea4\u989d {amt}  \u6362\u624b{to:.1f}%"
                 + (f"  \u91cf\u6bd4{sig.get('vol_ratio','?'):.1f}" if sig.get('vol_ratio') else "")
                 + (f"  5\u65e5\u6da8\u8dcc {w5:+.1f}%" if w5 is not None else ""))
    if h52 and l52:
        pct_52 = (c - l52) / (h52 - l52) * 100 if h52 != l52 else 0
        lines.append(f"  52\u5468 \u9ad8{h52} \u4f4e{l52}  \u5f53\u524d\u4f4d\u7f6e {pct_52:.0f}%\u5206\u4f4d")

    lines.append(f"  \u6280\u672f: {sig.get('trend','?')}\u8d8b\u52bf  {sig.get('ma_align','?')}  "
                 f"MA5={sig.get('ma5','?')} MA10={sig.get('ma10','?')} MA20={sig.get('ma20','?')}  "
                 f"\u4e56\u79bbMA5={sig.get('bias','?')}%")

    if fund_flow:
        latest = fund_flow[0]
        lines.append(f"  \u8d44\u91d1: \u4e3b\u529b\u51c0\u6d41\u5165{_money(latest['main_net'])} | "
                     f"\u8d85\u5927\u5355{_money(latest['super_big_net'])}  "
                     f"\u5927\u5355{_money(latest['big_net'])} | "
                     f"\u4e2d\u5355{_money(latest['mid_net'])}  "
                     f"\u6563\u6237{_money(latest['small_net'])}")
        if len(fund_flow) >= 3:
            flows = [f["main_net"]/1e8 for f in fund_flow[:3]]
            flows.reverse()
            flow_str = " \u2192 ".join(f"{v:+.2f}\u4ebf" for v in flows)
            lines.append(f"  \u8fd13\u65e5\u4e3b\u529b: {flow_str}")
        if len(fund_flow) >= 5:
            total5 = sum(f["main_net"] for f in fund_flow[:5])
            lines.append(f"  \u8fd15\u65e5\u4e3b\u529b\u7d2f\u8ba1: {_money(total5)}")

    if base:
        pe = base.get("\u5e02\u76c8\u7387(\u52a8)", "?"); pb = base.get("\u5e02\u51c0\u7387", "?")
        roe = base.get("ROE", "?"); ind = base.get("\u6240\u5904\u884c\u4e1a", "?")
        mc = _mcap(base.get("\u603b\u5e02\u503c")); pro = base.get("\u51c0\u5229\u7387", "?")
        ni = base.get("\u51c0\u5229\u6da6", "?")
        try: ni = _money(float(ni))
        except: pass

        lines.append(f"  \u57fa\u672c\u9762: PE{pe} PB{pb} ROE{roe}%  \u884c\u4e1a: {ind}  \u5e02\u503c{mc}")
        extra = []
        if pro and pro != "?":
            try: extra.append(f"\u51c0\u5229\u7387{float(pro):.1f}%")
            except: pass
        if ni and ni != "?" and ni != "+0.00\u4ebf": extra.append(f"\u51c0\u5229\u6da6{ni}")
        if extra: lines.append(f"          {'  '.join(extra)}")
        try:
            if float(pb) < 0.5:
                lines.append(f"          \u26a0 PB\u4ec5{pb}\u500d\uff08\u7834\u51c0\uff0c\u6bcf\u80a1\u51c0\u8d44\u4ea7\u5927\u5e45\u6298\u4ef7\uff09")
        except: pass

    lines.append("")
    return "\n".join(lines)

def format_report(market_data, llm_output, usage):
    dt = datetime.now().strftime("%m-%d %H:%M")
    names = "\u3001".join(sd["name"] for sd in market_data["stocks"].values())
    cost_str = ""
    if usage:
        in_tok = usage.prompt_tokens; out_tok = usage.completion_tokens
        cost = in_tok / 1e6 * LLM_PRICE_IN + out_tok / 1e6 * LLM_PRICE_OUT
        cost_str = f"  \u26a1{in_tok}\u2192{out_tok}tok \u00a5{cost:.4f}"
    lines = [f"\U0001f4c8 A\u80a1\u5206\u6790  {dt}{cost_str}", f"\u6807\u7684: {names}", ""]

    indices = market_data["indices"]
    if indices:
        lines.append("\u2501\u2501\u2501 \u4e00\u3001\u6307\u6570\u6536\u76d8 \u2501\u2501\u2501")
        for name in INDEX_KEYWORDS:
            d = indices.get(name)
            if d:
                lines.append(f"  {name}  {d['close']}  {_arrow(d['chg_pct'])}{d['chg_pct']:+.2f}%")
        lines.append("  > \u6570\u636e\u6e90: efinance(\u4e1c\u65b9\u8d22\u5bcc) \u5355\u6e90")
        lines.append("")

    sectors = market_data.get("sectors", {})
    if sectors.get("top"):
        top_s = "  ".join(f"{s['name']}+{s['chg_pct']:.1f}%" for s in sectors["top"])
        bot_s = "  ".join(f"{s['name']}{s['chg_pct']:.1f}%" for s in sectors["bottom"])
        lines.append("\u2501\u2501\u2501 \u4e8c\u3001\u677f\u5757 \u2501\u2501\u2501")
        lines.append(f"  \U0001f53a \u9886\u6da8: {top_s}")
        lines.append(f"  \U0001f53b \u9886\u8dcc: {bot_s}")
        lines.append("  > \u6570\u636e\u6e90: efinance \u5355\u6e90\uff0c\u4ec5\u6da8\u8dcc\u5e45\u6392\u884c\uff0c\u65e0\u677f\u5757\u7ea7\u522b\u8d44\u91d1\u6d41\u6570\u636e")
        lines.append("")

    bb = market_data.get("billboard") or []
    if bb:
        top5 = sorted(bb, key=lambda x: abs(x["net_buy"]), reverse=True)[:5]
        lines.append(f"\u2501\u2501\u2501 \u4e09\u3001\u9f99\u864e\u699c\uff08{len(bb)}\u53ea\u4e0a\u699c\uff09\u2501\u2501\u2501")
        for b in top5:
            lines.append(f"  {b['name']}({b['code']}) {b['chg_pct']:+.1f}% "
                         f"\u51c0\u4e70{_money(b['net_buy'])}  \u6362\u624b{b['turnover']:.1f}%  {b['reason'][:20]}")
        lines.append("")

    stocks_data = market_data["stocks"]
    if stocks_data:
        lines.append("\u2501\u2501\u2501 \u56db\u3001\u4e2a\u80a1\u8be6\u7ec6\u89e3\u8bfb \u2501\u2501\u2501")
        lines.append("")
        for code, sd in stocks_data.items():
            lines.append(_stock_section(sd["name"], code, sd.get("signals", {}),
                                        sd.get("fund_flow"), sd.get("base_info", {})))

    if llm_output:
        lines.append("\u2501\u2501\u2501 \u4e94\u3001\u8d70\u52bf\u7814\u5224 \u2501\u2501\u2501")
        for line in llm_output.strip().split("\n"):
            line = line.strip()
            if line: lines.append(f"  {line}")
        lines.append("")

    news = market_data.get("news") or {}
    if news:
        lines.append("\u2501\u2501\u2501 \u516d\u3001\u76f8\u5173\u65b0\u95fb \u2501\u2501\u2501")
        stock_news = {}
        for query, results in news.items():
            if not results: continue
            for code in market_data["stocks"]:
                if code in query or market_data["stocks"][code]["name"] in query:
                    stock_news.setdefault(code, []).append((query, results[0]))
        for code, items in stock_news.items():
            lines.append(f"  \U0001f4f0 {market_data['stocks'][code]['name']}({code}):")
            for q, r in items[:3]:
                lines.append(f"    {r['title']}")
        other = [(q, r[0]) for q, r in news.items() if r and not any(
            code in q or market_data["stocks"][code]["name"] in q for code in market_data["stocks"]
        )]
        if other:
            lines.append(f"  \U0001f4f0 \u5176\u4ed6:")
            for q, r in other[:3]:
                lines.append(f"    {r['title'][:80]}")
    lines.append("")

    cost_str = ""
    lines.append(f"\u751f\u6210\u65f6\u95f4: {dt}")

    return "\n".join(lines)

def build_llm_prompt(market_data):
    ctx = []

    indices = market_data.get("indices", {})
    if indices:
        ctx.append("## \u6307\u6570")
        for name in INDEX_KEYWORDS:
            d = indices.get(name)
            if d: ctx.append(f"- {name}: {d['close']} ({d['chg_pct']:+.2f}%)")

    sectors = market_data.get("sectors", {})
    if sectors.get("top"):
        ctx.append("\n## \u677f\u5757\u8f6e\u52a8")
        ctx.append(f"\u9886\u6da8: {', '.join(f'{s['name']}+{s['chg_pct']:.1f}%' for s in sectors['top'])}")
        ctx.append(f"\u9886\u8dcc: {', '.join(f'{s['name']}{s['chg_pct']:.1f}%' for s in sectors['bottom'])}")

    bb = market_data.get("billboard") or []
    if bb:
        top3 = sorted(bb, key=lambda x: abs(x["net_buy"]), reverse=True)[:3]
        ctx.append(f"\n## \u9f99\u864e\u699c ({len(bb)}\u53ea)")
        for b in top3:
            ctx.append(f"- {b['name']} {b['chg_pct']:+.1f}% \u51c0\u4e70{_money(b['net_buy'])} \u6362\u624b{b['turnover']:.1f}%")

    ctx.append("\n## \u4e2a\u80a1\u8be6\u7ec6\u6570\u636e")

    news = market_data.get("news") or {}
    if news:
        ctx.append("\n## \u76f8\u5173\u65b0\u95fb")
        for query, results in news.items():
            for r in results[:1]:
                ctx.append(f"- [{query}] {r['title']}: {r['body'][:150]}")

    for code, sd in market_data.get("stocks", {}).items():
        sig = sd.get("signals", {})
        fund = sd.get("fund_flow")
        base = sd.get("base_info") or {}
        ctx.append(f"\n### {sd['name']}({code})")
        ctx.append(f"\u6536\u76d8{sig.get('close')} {_arrow(sig.get('chg_pct',0))}{sig.get('chg_pct',0):+.2f}% "
                   f"\u5f00{sig.get('open')} \u9ad8{sig.get('high')} \u4f4e{sig.get('low')} "
                   f"\u632f\u5e45{sig.get('amplitude',0):.1f}% \u6362\u624b{sig.get('turnover',0):.1f}%")
        ctx.append(f"\u8d8b\u52bf: {sig.get('trend')}  {sig.get('ma_align')}  "
                   f"MA5={sig.get('ma5')} MA20={sig.get('ma20')}  \u4e56\u79bb={sig.get('bias')}%")
        if fund:
            f0 = fund[0]
            ctx.append(f"\u8d44\u91d1: \u4e3b\u529b{_money(f0['main_net'])} "
                       f"\u8d85\u5927\u5355{_money(f0['super_big_net'])} \u5927\u5355{_money(f0['big_net'])} "
                       f"\u4e2d\u5355{_money(f0['mid_net'])} \u6563\u6237{_money(f0['small_net'])}")
            if len(fund) >= 3:
                flows = [f"\u7b2c{i+1}\u65e5{_money(fund[i]['main_net'])}" for i in range(min(3, len(fund)))]
                ctx.append(f"\u8fd13\u65e5\u4e3b\u529b: {' \u2192 '.join(reversed(flows))}")
        if base:
            ctx.append(f"PE={base.get('\u5e02\u76c8\u7387(\u52a8)','?')} PB={base.get('\u5e02\u51c0\u7387','?')} "
                       f"ROE={base.get('ROE','?')}% \u884c\u4e1a:{base.get('\u6240\u5904\u884c\u4e1a','?')} "
                       f"\u5e02\u503c{_mcap(base.get('\u603b\u5e02\u503c'))} \u51c0\u5229\u7387{base.get('\u51c0\u5229\u7387','?')}%")

    prompt = f"""\u4f60\u662f\u4e00\u4f4d\u4e13\u4e1aA\u80a1\u5206\u6790\u5e08\u3002\u57fa\u4e8e\u4ee5\u4e0b\u6570\u636e\uff0c\u64b0\u5199\uff1a

**\u8d70\u52bf\u7814\u5224**\uff08200-300\u5b57\uff09
\u7efc\u5408\u6307\u6570\u8868\u73b0\u3001\u677f\u5757\u8f6e\u52a8\u65b9\u5411\u3001\u8d44\u91d1\u6d41\u5411\u7279\u5f81\u3001\u9f99\u864e\u699c\u4fe1\u53f7\u3002\u63cf\u8ff0\u5f53\u524d\u5e02\u573a\u6838\u5fc3\u77db\u76fe\uff0c\u6307\u51fa\u503c\u5f97\u5173\u6ce8\u7684\u7ed3\u6784\u6027\u53d8\u5316\u3002\u7eaf\u63cf\u8ff0\uff0c\u4e0d\u63a8\u8350\u64cd\u4f5c\u3002

**\u4e2a\u80a1\u89e3\u8bfb**\uff08\u6bcf\u53ea100-150\u5b57\uff09
\u7ed3\u5408\u884c\u60c5\u6570\u636e+\u6280\u672f\u9762+\u8d44\u91d1\u9762+\u57fa\u672c\u9762\u7efc\u5408\u5206\u6790\u3002\u8981\u70b9\uff1a
- \u5f53\u65e5\u8d70\u52bf\u7279\u5f81\uff08\u6da8\u8dcc\u3001\u632f\u5e45\u3001\u91cf\u80fd\u3001\u5728\u677f\u5757\u4e2d\u7684\u4f4d\u7f6e\uff09
- \u6280\u672f\u9762\u72b6\u6001\uff08\u5747\u7ebf\u3001\u8d8b\u52bf\u3001\u4e56\u79bb\u7387\u542b\u4e49\uff09
- \u8d44\u91d1\u9762\u4fe1\u53f7\uff08\u4e3b\u529b\u6d41\u5411\u3001\u591a\u65e5\u8d8b\u52bf\u3001\u6563\u6237\u884c\u4e3a\uff09
- \u57fa\u672c\u9762\u4f30\u503c\uff08PE/PB/ROE \u5728\u884c\u4e1a\u4e2d\u7684\u542b\u4e49\uff0c\u662f\u5426\u5f02\u5e38\uff09
- \u4e00\u53e5\u8bdd\u603b\u7ed3\u5f53\u524d\u9636\u6bb5\u7279\u5f81\uff08\u5982\uff1a\u5b58\u91cf\u535a\u5f08\u3001\u4f30\u503c\u4fee\u590d\u3001\u8d8b\u52bf\u5f3a\u5316\u7b49\uff09

{chr(10).join(ctx)}

\u8bf7\u8f93\u51fa\uff08\u4e0d\u8981markdown\uff0c\u7eaf\u6587\u672c\uff09\uff1a

\u8d70\u52bf\u7814\u5224
[\u4f60\u7684\u7814\u5224]

\u4e2a\u80a1\u89e3\u8bfb
- {list(market_data['stocks'].values())[0]['name']}: 
[\u89e3\u8bfb]

- {list(market_data['stocks'].values())[1]['name']}: 
[\u89e3\u8bfb]

- {list(market_data['stocks'].values())[2]['name']}: 
[\u89e3\u8bfb]

\u6838\u5fc3\u6458\u8981
[50\u5b57\u4ee5\u5185\u4e00\u53e5\u8bdd]"""
    return prompt

def send_bark(title, body):
    urls = os.environ.get("CUSTOM_WEBHOOK_URLS", "")
    if not urls:
        print("  [SKIP] CUSTOM_WEBHOOK_URLS \u672a\u914d\u7f6e"); return
    import urllib.request, json
    parts = _split_bark_body(body)
    ok = 0
    for i, part in enumerate(parts):
        t = title if i == 0 else f"{title} ({i+1}/{len(parts)})"
        for raw in urls.split(","):
            raw = raw.strip()
            if not raw: continue
            key = raw.rstrip("/").split("/")[-1]
            try:
                data = json.dumps({"device_key": key, "title": t, "body": part}).encode()
                req = urllib.request.Request("https://api.day.app/push", data=data,
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
                ok += 1
            except Exception as e:
                print(f"  [WARN] Bark ch{i}({key[:12]}): {e}")
    print(f"  [OK] Bark \u63a8\u9001 {ok}/{len(parts)*len(urls.split(','))}")

def _split_bark_body(body):
    lines, chunks, cur = body.split("\n"), [], []
    limit = 4000
    for line in lines:
        s = line.strip()
        if not s: continue
        if s.startswith("\u2501\u2501\u2501 \u516d"): break
        test = "\n".join(cur + [s])
        if len(test.encode()) > limit:
            chunks.append("\n".join(cur))
            cur = [s]
        else:
            cur.append(s)
    if cur: chunks.append("\n".join(cur))
    return chunks[:4]

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--stocks", type=str)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--mode", choices=["full", "pre"], default="full",
                   help="full=\u6536\u76d8\u5206\u6790, pre=\u76d8\u524d\u65b0\u95fb")
    args = p.parse_args()

    stock_list = STOCKS
    if args.stocks: stock_list = [s.strip() for s in args.stocks.split(",")]

    os.makedirs(OUTDIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 50)
    print(f"Phase 1: \u6570\u636e\u83b7\u53d6 (mode={args.mode})"); print("=" * 50)
    market_data = {"indices": {}, "sectors": {}, "billboard": [], "stocks": {}}

    if args.mode == "pre":
        for code in stock_list:
            sd = {"name": code, "signals": {}, "fund_flow": None, "base_info": {}}
            try:
                time.sleep(0.5)
                df = fetch_stock_kline(code)
                if df is not None and len(df) > 0:
                    sd["name"] = str(df.iloc[-1].get("\u80a1\u7968\u540d\u79f0", code))
            except Exception as e:
                print(f"  [WARN] {code}: {e}")
            market_data["stocks"][code] = sd
    else:
        print("\n[\u6307\u6570]"); time.sleep(0.5)
        market_data["indices"] = fetch_index_quotes()
        for n, d in market_data["indices"].items(): print(f"  {n}: {d['close']} ({d['chg_pct']:+.2f}%)")

        print("\n[\u677f\u5757]"); time.sleep(0.5)
        market_data["sectors"] = fetch_sector_rankings(5)
        print(f"  \u9886\u6da8: {', '.join(s['name'] for s in market_data['sectors']['top']) or '\u65e0'}")
        print(f"  \u9886\u8dcc: {', '.join(s['name'] for s in market_data['sectors']['bottom']) or '\u65e0'}")

        print("\n[\u9f99\u864e\u699c]"); time.sleep(0.5)
        market_data["billboard"] = fetch_billboard()
        print(f"  \u4e0a\u699c: {len(market_data['billboard'])}\u53ea")

        for code in stock_list:
            print(f"\n[{code}]")
            sd = {"name": code, "signals": {}, "fund_flow": None, "base_info": {}}
            try:
                time.sleep(0.5)
                df = fetch_stock_kline(code)
                if df is not None and len(df) > 0:
                    sd["name"] = str(df.iloc[-1].get("\u80a1\u7968\u540d\u79f0", code))
                    sd["signals"] = compute_signals(df)
                    print(f"  {sd['name']}: {sd['signals'].get('close','?')} "
                          f"{_arrow(sd['signals'].get('chg_pct',0))}{sd['signals'].get('chg_pct',0):.2f}%")
                else: print(f"  [WARN] K\u7ebf\u4e3a\u7a7a")

                time.sleep(0.5)
                fund = fetch_stock_fund_flow(code, days=5)
                if fund:
                    sd["fund_flow"] = fund
                    print(f"  \u4e3b\u529b: {_money(fund[0]['main_net'])} (\u8fd15\u65e5)")
                else: print(f"  \u8d44\u91d1\u6d41: \u672a\u83b7\u53d6")

                time.sleep(0.5)
                base = fetch_stock_base_info(code)
                if base:
                    sd["base_info"] = base
                    print(f"  PE:{base.get('\u5e02\u76c8\u7387(\u52a8)','?')} PB:{base.get('\u5e02\u51c0\u7387','?')} {base.get('\u6240\u5904\u884c\u4e1a','?')}")
            except Exception as e:
                print(f"  [ERROR] \u83b7\u53d6\u5931\u8d25({code}): {e}")

            market_data["stocks"][code] = sd

    print("\n[\u65b0\u95fb] \u89e6\u53d1\u5668\u751f\u6210\u641c\u7d22\u8bcd...")
    queries = generate_news_queries(market_data)
    print(f"  \u67e5\u8be2\u6570: {len(queries)}")
    news_results = {}
    for q in queries:
        results = search_news(q, max_results=2)
        if results:
            news_results[q] = results
            print(f"  \u2705 {q[:40]}... ({len(results)}\u6761)")
        else:
            print(f"  \u274c {q[:40]}...")
        time.sleep(0.3)
    market_data["news"] = news_results
    print(f"  \u6709\u6548\u7ed3\u679c: {sum(1 for r in news_results.values() if r)}/{len(queries)} \u7ec4")

    llm_output = None; usage = None
    if args.mode != "pre" and not args.no_llm:
        print("\n\u8c03\u7528 LLM...")
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": build_llm_prompt(market_data)}],
                temperature=0.3, max_tokens=4096,
            )
            llm_output = resp.choices[0].message.content
            usage = resp.usage
            print(f"  {usage.prompt_tokens}\u2192{usage.completion_tokens} tokens")
        except Exception as e: print(f"  [ERROR] LLM: {e}")

    report = format_report(market_data, llm_output, usage)
    outfile = os.path.join(OUTDIR, f"report_{ts}.txt")
    with open(outfile, "w") as f: f.write(report)
    print(f"\n\u62a5\u544a: {outfile}")

    send_bark(f"A\u80a1{datetime.now().strftime('%m-%d')}", report)
    print("Done.")

if __name__ == "__main__":
    main()
