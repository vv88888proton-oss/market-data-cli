"""OKX 公开行情 K 线命令行工具:拉取 K 线 -> 计算指标 -> 输出 CSV 汇总和 PNG 图表。

只使用 OKX 公开、免登录的行情接口 GET /api/v5/market/candles 和
/api/v5/market/history-candles,不需要 API Key,不涉及任何下单或账户操作。

用法:
    python okx_candles.py BTC-USDT ETH-USDT --bar 1D --limit 180
    python okx_candles.py BTC-USDT --offline sample_data/btc_usdt_1d_sample.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境也能出图
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

DEFAULT_BASE_URL = "https://www.okx.com"
COLUMNS = ["ts", "open", "high", "low", "close", "vol", "vol_ccy", "vol_ccy_quote", "confirm"]
BARS_PER_YEAR = {"1H": 24 * 365, "4H": 6 * 365, "1D": 365, "1W": 52}
MAX_PER_REQUEST = 100  # history-candles 单次上限 100 条

log = logging.getLogger("okx_candles")


class OKXError(RuntimeError):
    pass


def _get(session: requests.Session, url: str, params: dict, retries: int = 3) -> list:
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 429:  # 限频:退避后重试
                time.sleep(2 * attempt)
                continue
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != "0":
                raise OKXError(f"OKX 返回错误 code={body.get('code')} msg={body.get('msg')}")
            return body["data"]
        except requests.RequestException as exc:
            log.warning("请求失败(第 %d 次):%s", attempt, exc)
            time.sleep(2 * attempt)
    raise OKXError(f"多次请求失败:{url} {params}")


def fetch_candles(inst_id: str, bar: str = "1D", limit: int = 180, base_url: str = DEFAULT_BASE_URL,
                  pause: float = 0.25) -> list[list]:
    """分页拉取最近 limit 根 K 线(新 -> 旧),用 after 参数向更早翻页。"""
    session = requests.Session()
    session.headers["User-Agent"] = "PortfolioDemo/1.0"
    rows: list[list] = []
    after: str | None = None
    while len(rows) < limit:
        params = {"instId": inst_id, "bar": bar, "limit": str(min(MAX_PER_REQUEST, limit - len(rows)))}
        if after:
            params["after"] = after
        data = _get(session, f"{base_url}/api/v5/market/history-candles", params)
        if not data:
            break
        rows.extend(data)
        after = data[-1][0]  # 本页最早一根的时间戳
        time.sleep(pause)  # 公开接口限频 20 次/2 秒,这里留足余量
    return rows[:limit]


def to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS[: len(rows[0])] if rows else COLUMNS)
    if df.empty:
        raise OKXError("没有拿到任何 K 线数据,请检查交易对和周期")
    for c in ["open", "high", "low", "close", "vol", "vol_ccy", "vol_ccy_quote"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    df = df.drop_duplicates("ts").sort_values("time").reset_index(drop=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["return"] = df["close"].pct_change()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["drawdown"] = df["close"] / df["close"].cummax() - 1
    return df


def summarize(df: pd.DataFrame, inst_id: str, bar: str) -> dict:
    rets = df["return"].dropna()
    periods = BARS_PER_YEAR.get(bar, 365)
    first, last = df["close"].iloc[0], df["close"].iloc[-1]
    return {
        "instId": inst_id,
        "bar": bar,
        "start": df["time"].iloc[0].strftime("%Y-%m-%d %H:%M"),
        "end": df["time"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
        "candles": len(df),
        "first_close": first,
        "last_close": last,
        "period_return_pct": round((last / first - 1) * 100, 2),
        "high": df["high"].max(),
        "low": df["low"].min(),
        "annualized_vol_pct": round(rets.std() * math.sqrt(periods) * 100, 2) if len(rets) > 1 else None,
        "max_drawdown_pct": round(df["drawdown"].min() * 100, 2),
        "up_bars_pct": round((rets > 0).mean() * 100, 1) if len(rets) else None,
        "last_vs_sma20_pct": round((last / df["sma20"].iloc[-1] - 1) * 100, 2) if pd.notna(df["sma20"].iloc[-1]) else None,
        "avg_quote_volume": round(df["vol_ccy_quote"].mean(), 0) if "vol_ccy_quote" in df else None,
    }


def plot(df: pd.DataFrame, inst_id: str, bar: str, path: Path) -> Path:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(df["time"], df["close"], label="Close", color="#1f4e78", linewidth=1.4)
    ax1.plot(df["time"], df["sma20"], label="SMA20", color="#f39c12", linewidth=1)
    ax1.plot(df["time"], df["sma50"], label="SMA50", color="#8e44ad", linewidth=1)
    ax1.set_title(f"OKX {inst_id} {bar} close & moving averages (time: UTC+8)")
    ax1.grid(alpha=0.3)
    ax1.legend(loc="upper left")
    ax2.fill_between(df["time"], df["drawdown"] * 100, 0, color="#c0392b", alpha=0.4)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OKX 公开 K 线数据汇总工具(只读,无需 API Key)")
    ap.add_argument("inst_ids", nargs="+", help="交易对,如 BTC-USDT、ETH-USDT、BTC-USDT-SWAP")
    ap.add_argument("--bar", default="1D", help="K 线周期:1H / 4H / 1D / 1W 等")
    ap.add_argument("--limit", type=int, default=180, help="拉取最近多少根 K 线")
    ap.add_argument("--out", default="output", help="输出目录")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="接口域名,网络不通时可替换")
    ap.add_argument("--offline", help="离线模式:读取本地 JSON(OKX 原始返回格式),只支持单个交易对")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    summaries = []
    for inst in args.inst_ids:
        if args.offline:
            rows = json.loads(Path(args.offline).read_text(encoding="utf-8"))["data"]
        else:
            log.info("拉取 %s %s 最近 %d 根 K 线", inst, args.bar, args.limit)
            rows = fetch_candles(inst, args.bar, args.limit, args.base_url)
        df = add_indicators(to_frame(rows))
        tag = f"{inst.lower().replace('-', '_')}_{args.bar.lower()}"
        export = df.drop(columns=["ts"]).copy()
        export["time"] = export["time"].dt.strftime("%Y-%m-%d %H:%M")
        export[["return", "drawdown"]] = export[["return", "drawdown"]].round(6)
        export[["sma20", "sma50"]] = export[["sma20", "sma50"]].round(4)
        export = export[["time"] + [c for c in export.columns if c != "time"]]
        export.to_csv(out / f"{tag}_candles.csv", index=False, encoding="utf-8-sig")
        plot(df, inst, args.bar, out / f"{tag}_chart.png")
        summaries.append(summarize(df, inst, args.bar))

    summary = pd.DataFrame(summaries)
    summary_path = out / f"summary_{args.bar.lower()}.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(summary.T.to_string(header=False))
    print(f"已输出:{summary_path} 以及各交易对的 *_candles.csv / *_chart.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
