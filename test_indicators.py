#!/usr/bin/env python3
"""
指标算法单元测试（纯数学，不联网、不需要 yfinance）。
运行： python3 test_indicators.py
"""
import math
import stock_data_fetcher as f


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def test_ema():
    # EMA 第一个值是前 period 个数的简单平均
    data = [1, 2, 3, 4, 5]
    ema = f.calc_ema(data, 3)
    assert ema[:2] == [None, None], ema
    assert approx(ema[2], 2.0), ema  # (1+2+3)/3
    print("✓ calc_ema")


def test_ma_alignment():
    closes = list(range(1, 61))  # 单调上升 -> 多头排列
    ma = f.calc_ma(closes, [5, 10, 20, 60])
    assert ma["MA5"] > ma["MA10"] > ma["MA20"], ma
    assert ma["alignment"] == "bullish", ma["alignment"]
    closes_dn = list(range(60, 0, -1))  # 下降 -> 空头
    assert f.calc_ma(closes_dn, [5, 10, 20])["alignment"] == "bearish"
    print("✓ calc_ma + 排列判断")


def test_rsi_bounds():
    up = list(range(1, 40))            # 一路上涨 -> RSI 接近 100
    r = f.calc_rsi(up, [12])
    assert 95 <= r["RSI12"] <= 100, r
    assert r["zone"] == "overbought", r
    dn = list(range(40, 1, -1))        # 一路下跌 -> RSI 接近 0
    r2 = f.calc_rsi(dn, [12])
    assert 0 <= r2["RSI12"] <= 5, r2
    print("✓ calc_rsi 边界")


def test_macd_shape():
    closes = [10 + 2 * i for i in range(40)]  # 稳定上涨
    m = f.calc_macd(closes)
    assert m["DIF"] is not None and m["DEA"] is not None, m
    assert m["signal"] in ("bullish", "golden_cross_above_zero", "crossing_above_zero", "golden_cross"), m["signal"]
    print("✓ calc_macd")


def test_atr():
    n = 30
    highs = [10 + i for i in range(n)]
    lows = [9 + i for i in range(n)]
    closes = [9.5 + i for i in range(n)]
    a = f.calc_atr(highs, lows, closes, period=14)
    assert a["atr"] is not None and a["atr"] > 0, a
    assert a["atr_pct"] is not None, a
    # 数据不足时返回 None
    assert f.calc_atr([1, 2], [0, 1], [0.5, 1.5])["atr"] is None
    print("✓ calc_atr")


def test_weekly():
    # 造 40 个连续交易日（上升）
    import datetime as dt
    ohlcv = []
    d = dt.date(2026, 1, 5)
    px = 100.0
    for i in range(60):
        ohlcv.append({"date": d.isoformat(), "open": px, "high": px + 1, "low": px - 1, "close": px})
        px += 1
        d += dt.timedelta(days=1)
    w = f.calc_weekly(ohlcv)
    assert w["weeks"] >= 8, w
    assert "trend" in w
    print("✓ calc_weekly")


def test_score_monotonic():
    # 强多头场景评分应高于强空头场景
    bull = f.calc_trend_score(
        {"alignment": "bullish", "alignment_detail": "strong_bullish"},
        {"signal": "golden_cross_above_zero"}, {"zone": "strong"},
        {"trend": "shrink_pullback"}, {"bias_ma5": -1.0},
        {"support_ma5": True, "support_ma10": True})
    bear = f.calc_trend_score(
        {"alignment": "bearish", "alignment_detail": "strong_bearish"},
        {"signal": "death_cross"}, {"zone": "overbought"},
        {"trend": "heavy_volume_down"}, {"bias_ma5": 8.0},
        {"support_ma5": False, "support_ma10": False})
    assert bull["total"] > bear["total"], (bull["total"], bear["total"])
    assert bull["signal"] in ("buy", "strong_buy"), bull["signal"]
    print("✓ calc_trend_score 单调性")


def test_classify():
    assert f.classify_stock("NVDA")[0] == "us"
    assert f.classify_stock("600519")[0] == "cn_a"
    assert f.classify_stock("HK00700")[0] == "cn_hk"
    print("✓ classify_stock")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n全部 {len(tests)} 项测试通过 ✅")
