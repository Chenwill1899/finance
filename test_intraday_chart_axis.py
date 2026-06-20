#!/usr/bin/env python3
"""
Intraday chart axis behavior tests.
These tests inspect the embedded frontend code without launching a browser.
"""
import analyzer_app as app


def test_intraday_chart_does_not_force_far_strategy_lines_into_axis():
    page = app.PAGE

    assert "[d.prev_close,lv.buy,lv.stop,lv.cost].forEach" not in page
    assert "function lineInView" in page
    assert "lineInView(lv.stop" in page
    print("✓ 日内K线远离策略线不压缩坐标")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n全部 {len(tests)} 项日内图表测试通过 ✅")
