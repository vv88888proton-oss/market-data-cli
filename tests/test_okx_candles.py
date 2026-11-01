import json
from pathlib import Path
from unittest import mock

import pytest

import okx_candles as oc


def fake_rows(n=60, start=1767225600000, step=86400000):
    rows = []
    for i in range(n):
        c = 100 + i  # 单调上涨
        rows.append([str(start + i * step), str(c - 0.5), str(c + 1), str(c - 1), str(c), "10", "1000", "1000", "1"])
    return list(reversed(rows))


def test_to_frame_sorts_and_types():
    df = oc.to_frame(fake_rows(5))
    assert df["close"].tolist() == [100, 101, 102, 103, 104]
    assert str(df["time"].dt.tz) == "Asia/Shanghai"


def test_summary_metrics():
    df = oc.add_indicators(oc.to_frame(fake_rows(60)))
    s = oc.summarize(df, "TEST-USDT", "1D")
    assert s["candles"] == 60
    assert s["period_return_pct"] == pytest.approx(59.0)
    assert s["max_drawdown_pct"] == 0
    assert s["up_bars_pct"] == 100.0


def test_drawdown():
    rows = fake_rows(3)
    rows[0][4] = "50"  # 最新一根收盘价从 102 跌到 50
    df = oc.add_indicators(oc.to_frame(rows))
    assert df["drawdown"].iloc[-1] == pytest.approx(50 / 101 - 1)


def test_fetch_candles_paginates():
    all_rows = fake_rows(250)
    calls = []

    def fake_get(session, url, params, retries=3):
        calls.append(params)
        after = params.get("after")
        start = 0 if after is None else next(i for i, r in enumerate(all_rows) if r[0] == after) + 1
        return all_rows[start : start + int(params["limit"])]

    with mock.patch.object(oc, "_get", side_effect=fake_get):
        rows = oc.fetch_candles("TEST-USDT", limit=250, pause=0)
    assert len(rows) == 250 and len(calls) == 3
    assert rows == all_rows


def test_api_error_raises():
    resp = mock.Mock(status_code=200)
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"code": "51001", "msg": "Instrument ID does not exist", "data": []}
    session = mock.Mock()
    session.get.return_value = resp
    with pytest.raises(oc.OKXError):
        oc._get(session, "http://x", {})


def test_cli_offline(tmp_path: Path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"code": "0", "data": fake_rows(60)}))
    assert oc.main(["TEST-USDT", "--offline", str(p), "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "summary_1d.csv").exists()
    assert (tmp_path / "o" / "test_usdt_1d_chart.png").stat().st_size > 10_000
