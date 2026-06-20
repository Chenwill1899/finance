#!/usr/bin/env python3
"""
Backtest engine behavior tests.
These are deterministic and do not require network access.
"""
import copy

import backtest_engine as bt


def _bars(n, close=100.0):
    bars = []
    for i in range(n):
        px = float(close)
        bars.append({
            "date": f"d{i}",
            "open": px,
            "high": px + 1,
            "low": px - 1,
            "close": px,
            "volume": 1_000_000,
        })
    return bars


def test_scores_per_day_does_not_use_future_bars():
    bars = _bars(90)
    scores = bt.scores_per_day(bars)

    changed = copy.deepcopy(bars)
    for i in range(76, len(changed)):
        changed[i] = {
            **changed[i],
            "open": 1_000.0,
            "high": 1_010.0,
            "low": 990.0,
            "close": 1_005.0,
        }
    changed_scores = bt.scores_per_day(changed)

    assert scores[75] == changed_scores[75], (scores[75], changed_scores[75])
    print("✓ scores_per_day 无未来函数")


def test_simulate_enters_on_next_open_after_signal():
    bars = _bars(63)
    bars[60] = {**bars[60], "close": 10.0}
    bars[61] = {**bars[61], "open": 100.0, "high": 112.0, "low": 99.0, "close": 110.0}
    bars[62] = {**bars[62], "open": 110.0, "high": 122.0, "low": 109.0, "close": 121.0}
    scores = [(None, None)] * len(bars)
    scores[60] = (70, 1.0)
    scores[61] = (70, 1.0)

    result = bt.simulate(
        bars,
        scores,
        lo=60,
        hi=62,
        params={"buy_th": 60, "sell_th": 35, "stop_atr": 2.0},
        fee_bps=0,
    )

    assert result["n_trades"] == 1, result
    assert result["total_return"] == 0.21, result
    print("✓ simulate 信号次日开盘执行")


def test_simulate_applies_atr_stop_from_entry_day_low():
    bars = _bars(63)
    bars[61] = {**bars[61], "open": 100.0, "high": 101.0, "low": 97.0, "close": 100.0}
    scores = [(None, None)] * len(bars)
    scores[60] = (70, 1.0)

    result = bt.simulate(
        bars,
        scores,
        lo=60,
        hi=62,
        params={"buy_th": 60, "sell_th": 35, "stop_atr": 2.0},
        fee_bps=0,
    )

    assert result["n_trades"] == 1, result
    assert result["total_return"] == -0.02, result
    print("✓ simulate ATR止损")


def test_walk_forward_rejects_short_history():
    result = bt.walk_forward(_bars(100), train=80, test=40)

    assert "error" in result, result
    assert "数据不足" in result["error"], result
    print("✓ walk_forward 短历史保护")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n全部 {len(tests)} 项回测测试通过 ✅")
