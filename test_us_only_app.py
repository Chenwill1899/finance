#!/usr/bin/env python3
"""
US-only app behavior tests.
These tests do not start the web server or access the network.
"""
import analyzer_app as app


def test_split_us_tickers_accepts_simple_us_symbols():
    valid, errors = app.split_us_tickers("nvda, rdw\nrklb")

    assert valid == ["NVDA", "RDW", "RKLB"], valid
    assert errors == [], errors
    print("✓ 美股代码解析")


def test_split_us_tickers_rejects_a_share_and_hk_codes():
    valid, errors = app.split_us_tickers("NVDA, 600519, HK00700")

    assert valid == ["NVDA"], valid
    assert errors == [
        {"code": "600519", "error": "目前仅支持美股 ticker，例如 NVDA、RDW、RKLB。"},
        {"code": "HK00700", "error": "目前仅支持美股 ticker，例如 NVDA、RDW、RKLB。"},
    ], errors
    print("✓ 非美股代码拒绝")


def test_split_us_tickers_rejects_market_suffixes():
    valid, errors = app.split_us_tickers("TSLA, 600519.SH")

    assert valid == ["TSLA"], valid
    assert errors == [
        {"code": "600519.SH", "error": "目前仅支持美股 ticker，例如 NVDA、RDW、RKLB。"}
    ], errors
    print("✓ 非美股后缀拒绝")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n全部 {len(tests)} 项美股专用测试通过 ✅")
