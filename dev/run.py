"""本地开发调试：数据获取 -> LLM -> 输出 txt"""
import os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv('.env.local')

import efinance as ef
from openai import OpenAI

STOCKS = ["000002", "002607", "002373"]
OUTDIR = "local_reports"

client = OpenAI(
    api_key=os.environ.get("LLM_OPENAI_API_KEY"),
    base_url=os.environ.get("LLM_OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
)

def fetch_kline(code):
    """获取日K线"""
    df = ef.stock.get_quote_history(code, klt=101, fqt=1)
    return df

def fetch_quote(code):
    """获取实时行情"""
    for suffix in [".SZ", ".SH"]:
        try:
            q = ef.stock.get_realtime_quotes([code + suffix])
            if q is not None and len(q):
                return q
        except Exception:
            continue
    return None

def code_to_name(code):
    """尝试从K线数据获取股票名称"""
    df = fetch_kline(code)
    if df is not None and len(df):
        cols = df.columns.tolist()
        if "股票名称" in cols:
            return df.iloc[-1]["股票名称"]
    return code

def format_prompt(name, code, df, today=None):
    """构建LLM输入，返回 (prompt_str, signals_dict)"""
    if df is None or len(df) < 2:
        return None, {}
    last = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(last["收盘"])
    prev_close = float(prev["收盘"])
    chg_pct = round((close - prev_close) / prev_close * 100, 2)

    # 均线
    closes = [float(x) for x in df["收盘"]]
    ma5 = round(sum(closes[-5:]) / min(5, len(closes)), 3) if len(closes) >= 5 else "N/A"
    ma10 = round(sum(closes[-10:]) / min(10, len(closes)), 3) if len(closes) >= 10 else "N/A"
    ma20 = round(sum(closes[-20:]) / min(20, len(closes)), 3) if len(closes) >= 20 else "N/A"

    bias = round((close - ma5) / ma5 * 100, 2) if isinstance(ma5, float) else "N/A"

    trend = "上升" if (isinstance(ma5, float) and isinstance(ma20, float) and ma5 > ma20) else \
            "下降" if (isinstance(ma5, float) and isinstance(ma20, float) and ma5 < ma20) else "震荡"
    ma_align = f"MA5={ma5} MA10={ma10} MA20={ma20}"
    if isinstance(ma5, float) and isinstance(ma10, float) and isinstance(ma20, float):
        if ma5 > ma10 > ma20:
            ma_align += " 多头排列"
        elif ma5 < ma10 < ma20:
            ma_align += " 空头排列"
        else:
            ma_align += " 均线缠绕"

    signals = {"trend": trend, "ma_align": ma_align, "bias": bias, "chg_pct": chg_pct, "close": close}

    prompt = f"""你是一个A股市场信息摘要助手。

## 预计算的技术信号
- 趋势方向: {trend}
- 均线排列: {ma_align}
- 乖离率(MA5): {bias}%
- 动量: {"偏强" if chg_pct > 2 else "偏弱" if chg_pct < -2 else "中性"}

## 当日行情数据
- 股票: {name}({code})
- 日期: {last.get("日期","N/A")}
- 开盘: {last.get("开盘","N/A")}
- 收盘: {close}
- 涨跌幅: {chg_pct}%
- 最高: {last.get("最高","N/A")}
- 最低: {last.get("最低","N/A")}
- 成交量: {last.get("成交量","N/A")}
- 成交额: {last.get("成交额","N/A")}
- 换手率: {last.get("换手率","N/A")}
- 振幅: {last.get("振幅","N/A")}
- 量比: {today.get("量比","N/A") if today is not None else "N/A"}

请输出以下JSON（纯文本，不用markdown）：
{{"analysis_summary":"80字以内综合状态摘要","key_observations":["观察点1","观察点2"],"risk_observations":["风险点1","风险点2"]}}"""

    return prompt, signals

def call_llm(prompt):
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1024
    )
    return resp.choices[0].message.content

def main():
    os.makedirs(OUTDIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for code in STOCKS:
        print(f"\n{'='*40}")
        print(f"[{code}] 获取数据...")
        df = fetch_kline(code)
        if df is None or len(df) == 0:
            print(f"  WARNING: No data for {code}, skipping")
            continue

        name = code_to_name(code) if isinstance(code_to_name(code), str) else code
        q = fetch_quote(code)

        print(f"[{code}] {name}")
        print(f"[{code}] 构建 prompt...")
        prompt, signals = format_prompt(name, code, df, q)
        if prompt is None:
            print(f"  WARNING: Insufficient data for {code}")
            continue

        print(f"[{code}] 调用 LLM...")
        llm_out = call_llm(prompt)

        # 写入报告
        outfile = os.path.join(OUTDIR, f"{code}_{ts}.txt")
        with open(outfile, "w") as f:
            f.write(f"# {name}({code})\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 40 + "\n\n")
            f.write("## Python 预计算信号\n\n")
            f.write(f"趋势: {signals['trend']}\n")
            f.write(f"均线: {signals['ma_align']}\n")
            f.write(f"涨跌幅: {signals['chg_pct']}%\n")
            f.write(f"乖离率: {signals['bias']}%\n")
            f.write("\n## LLM 文本摘要\n\n")
            f.write(llm_out + "\n")
        print(f"[{code}] -> {outfile}")

    print(f"\nDone. Reports in {OUTDIR}/")

if __name__ == "__main__":
    main()
