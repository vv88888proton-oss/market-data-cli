"""生成离线演示用的 K 线 JSON(随机游走模拟数据,格式与 OKX 接口返回一致,不是真实行情)。"""
import json
import random
from pathlib import Path

rng = random.Random(7)
start_ms = 1767225600000  # 2026-01-01 00:00 UTC
price, rows = 90000.0, []
for i in range(200):
    o = price
    c = o * (1 + rng.gauss(0, 0.025))
    h, l = max(o, c) * (1 + abs(rng.gauss(0, 0.008))), min(o, c) * (1 - abs(rng.gauss(0, 0.008)))
    vol = rng.uniform(5000, 15000)
    rows.append([str(start_ms + i * 86400000), f"{o:.1f}", f"{h:.1f}", f"{l:.1f}", f"{c:.1f}",
                 f"{vol:.4f}", f"{vol * c:.2f}", f"{vol * c:.2f}", "1"])
    price = c
rows.reverse()  # OKX 返回顺序是新 -> 旧
Path("sample_data").mkdir(exist_ok=True)
Path("sample_data/btc_usdt_1d_sample.json").write_text(json.dumps({"code": "0", "msg": "", "data": rows}), encoding="utf-8")
print("已生成 sample_data/btc_usdt_1d_sample.json(模拟数据)")
