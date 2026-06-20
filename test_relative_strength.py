#!/usr/bin/env python3
"""
Relative strength tests.
These tests are deterministic and do not access the network.
"""
import stock_data_fetcher as f


def _bars(closes):
    return [
        {
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1_000_000,
        }
        for i, c in enumerate(closes)
    ]


def test_calc_relative_strength_marks_stock_outperforming_benchmarks():
    stock = _bars([100.0] * 61 + [112.0])
    spy = _bars([100.0] * 61 + [103.0])
    qqq = _bars([100.0] * 61 + [106.0])

    result = f.calc_relative_strength(stock, {"SPY": spy, "QQQ": qqq}, periods=(5, 20, 60))

    assert result["periods"]["5d"]["stock_return"] == 12.0, result
    assert result["periods"]["5d"]["vs_spy"] == 9.0, result
    assert result["periods"]["5d"]["vs_qqq"] == 6.0, result
    assert result["summary"]["market"] == "outperform", result
    assert result["summary"]["tech"] == "outperform", result
    assert result["summary"]["label_cn"] == "强于大盘和科技指数", result
    print("✓ 相对强弱跑赢识别")


def test_calc_relative_strength_marks_stock_lagging_benchmarks():
    stock = _bars([100.0] * 61 + [98.0])
    spy = _bars([100.0] * 61 + [102.0])
    qqq = _bars([100.0] * 61 + [104.0])

    result = f.calc_relative_strength(stock, {"SPY": spy, "QQQ": qqq}, periods=(5, 20, 60))

    assert result["periods"]["20d"]["stock_return"] == -2.0, result
    assert result["periods"]["20d"]["vs_spy"] == -4.0, result
    assert result["periods"]["20d"]["vs_qqq"] == -6.0, result
    assert result["summary"]["market"] == "underperform", result
    assert result["summary"]["tech"] == "underperform", result
    assert result["summary"]["label_cn"] == "弱于大盘和科技指数", result
    print("✓ 相对强弱跑输识别")


def test_calc_relative_strength_handles_insufficient_data():
    result = f.calc_relative_strength(_bars([100.0, 101.0]), {"SPY": _bars([100.0, 101.0])})

    assert result["summary"]["market"] == "unknown", result
    assert result["summary"]["label_cn"] == "相对强弱数据不足", result
    print("✓ 相对强弱数据不足处理")


def test_calc_relative_strength_falls_back_to_shorter_available_period():
    stock = _bars([100.0, 100.0, 100.0, 100.0, 100.0, 110.0])
    spy = _bars([100.0, 100.0, 100.0, 100.0, 100.0, 102.0])

    result = f.calc_relative_strength(stock, {"SPY": spy}, periods=(5, 20))

    assert result["summary"]["market"] == "outperform", result
    assert result["summary"]["tech"] == "unknown", result
    assert result["summary"]["label_cn"] == "强于大盘", result
    print("✓ 相对强弱短周期回退")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n全部 {len(tests)} 项相对强弱测试通过 ✅")
