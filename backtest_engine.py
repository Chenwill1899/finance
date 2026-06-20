#!/usr/bin/env python3
"""
回测引擎 —— 把现有「技术评分规则」用历史数据验证（路线图阶段1）。

核心原则（避免自欺）：
  * 无前视偏差：第 t 日收盘用 [..t] 的数据算信号，第 t+1 日才执行。
  * 计交易成本：每次买/卖扣 fee_bps（手续费+滑点，单边）。
  * 含风控：ATR 止损（现价 - n×ATR），盘中触及即出。
  * 给基准：对比同期「买入持有」。

策略（long-only，复用 stock_data_fetcher 的打分）：
  * 入场：评分信号为 buy / strong_buy。
  * 出场：信号转 sell / strong_sell，或触发 ATR 止损。

用法：
  python3 backtest_engine.py --stocks "NVDA,MU,RKLB" --days 400
  python3 backtest_engine.py --selftest          # 合成数据自测（不联网）
"""
import argparse
import math
import statistics
import stock_data_fetcher as f

WARMUP = 60            # 预热：保证 MA60 等指标有足够数据
TRADING_DAYS = 252


def _signals_per_day(bars, stop_atr):
    """逐日计算 (signal, atr)，只用当日及之前的数据。"""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    out = [(None, None)] * len(bars)
    for t in range(WARMUP, len(bars)):
        c = closes[: t + 1]
        ma = f.calc_ma(c, [5, 10, 20, 60])
        macd = f.calc_macd(c)
        rsi = f.calc_rsi(c, [6, 12, 24])
        vol = f.calc_volume_analysis(vols[: t + 1], c)
        bias = f.calc_bias(c, ma)
        sup = f.calc_support(c, ma)
        score = f.calc_trend_score(ma, macd, rsi, vol, bias, sup)
        atr = f.calc_atr(highs[: t + 1], lows[: t + 1], c)["atr"]
        out[t] = (score["signal"], atr)
    return out


def run_backtest(bars, fee_bps=5.0, stop_atr=2.0):
    """事件驱动回测，返回指标 dict。"""
    n = len(bars)
    if n < WARMUP + 20:
        return {"error": f"数据不足（{n} 根，至少需要 {WARMUP+20}）"}
    closes = [b["close"] for b in bars]
    opens = [b.get("open") or b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    sig = _signals_per_day(bars, stop_atr)
    fee = fee_bps / 10000.0

    equity = 1.0
    pos = 0                # 0=空仓 1=持有
    entry = None
    stop = None
    curve = []             # 每日权益（用于夏普/回撤）
    trade_rets = []        # 每笔交易收益率
    n_trades = 0

    for t in range(WARMUP, n - 1):
        s = sig[t][0]
        atr = sig[t][1]
        # --- 决策：依据第 t 日信号，第 t+1 日执行 ---
        if pos == 0:
            if s in ("buy", "strong_buy"):
                pos = 1
                entry = opens[t + 1]                      # 次日开盘买入
                stop = entry - stop_atr * atr if atr else entry * 0.92
                equity *= (1 - fee)
                n_trades += 1
                base = entry
                # 当日(t+1)从开盘到收盘的盈亏
                o2, h2, l2, c2 = opens[t + 1], highs[t + 1], lows[t + 1], closes[t + 1]
                if stop and l2 <= stop:                   # 当天就触发止损
                    equity *= stop / base
                    trade_rets.append(stop / entry - 1); pos = 0; stop = None
                else:
                    equity *= c2 / base
            curve.append(equity)
            continue

        # 已持仓：处理第 t+1 日
        o2, h2, l2, c2 = opens[t + 1], highs[t + 1], lows[t + 1], closes[t + 1]
        prev_close = closes[t]
        if stop and l2 <= stop:                           # 盘中触发止损
            equity *= stop / prev_close
            equity *= (1 - fee)
            trade_rets.append(stop / entry - 1); pos = 0; stop = None
        else:
            equity *= c2 / prev_close                     # 持有到收盘
            if s in ("sell", "strong_sell"):              # 收盘信号出场（次日已体现为持有到收盘后了结）
                equity *= (1 - fee)
                trade_rets.append(c2 / entry - 1); pos = 0; stop = None
        curve.append(equity)

    # 收尾：仍持仓则按最后收盘了结
    if pos == 1:
        equity *= (1 - fee)
        trade_rets.append(closes[-1] / entry - 1)

    # ---- 指标 ----
    days = n - WARMUP
    daily = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))] if len(curve) > 1 else [0]
    sharpe = 0.0
    if len(daily) > 2 and statistics.pstdev(daily) > 0:
        sharpe = statistics.mean(daily) / statistics.pstdev(daily) * math.sqrt(TRADING_DAYS)
    peak = -1e9; maxdd = 0.0
    for e in curve:
        peak = max(peak, e); maxdd = max(maxdd, (peak - e) / peak)
    cagr = equity ** (TRADING_DAYS / days) - 1 if days > 0 else 0
    wins = [r for r in trade_rets if r > 0]
    losses = [r for r in trade_rets if r <= 0]
    win_rate = len(wins) / len(trade_rets) if trade_rets else 0
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0)
    buy_hold = closes[-1] / closes[WARMUP] - 1

    return {
        "total_return": round(equity - 1, 4),
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(maxdd, 4),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 3),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "buy_hold_return": round(buy_hold, 4),
        "excess_vs_bh": round((equity - 1) - buy_hold, 4),
        "bars": n, "days_tested": days,
    }


def _fmt_pct(x):
    return f"{x*100:+.1f}%" if isinstance(x, (int, float)) else str(x)


def _pct(x):
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else str(x)


def print_report(code, m):
    if m.get("error"):
        print(f"\n[{code}] ⚠️ {m['error']}"); return
    verdict = "✅ 跑赢" if m["excess_vs_bh"] > 0 else "❌ 跑输"
    print(f"""
━━━ {code} 回测（{m['days_tested']} 个交易日）━━━
  策略总收益 : {_fmt_pct(m['total_return'])}     年化 : {_fmt_pct(m['cagr'])}
  买入持有   : {_fmt_pct(m['buy_hold_return'])}     超额 : {_fmt_pct(m['excess_vs_bh'])}  {verdict}
  夏普比率   : {m['sharpe']}        最大回撤 : {_pct(m['max_drawdown'])}
  交易次数   : {m['n_trades']}        胜率 : {_pct(m['win_rate'])}   盈亏比 : {m['profit_factor']}""")


def selftest():
    """用合成数据验证引擎逻辑（不联网）。"""
    import random
    random.seed(7)
    bars = []
    px = 100.0
    for i in range(400):
        drift = 0.0008 if i < 250 else -0.0010      # 先涨后跌，制造趋势
        px *= (1 + drift + random.gauss(0, 0.018))
        o = px * (1 + random.gauss(0, 0.004))
        h = max(o, px) * (1 + abs(random.gauss(0, 0.006)))
        l = min(o, px) * (1 - abs(random.gauss(0, 0.006)))
        bars.append({"date": f"d{i}", "open": round(o, 2), "high": round(h, 2),
                     "low": round(l, 2), "close": round(px, 2), "volume": random.randint(1e6, 5e6)})
    m = run_backtest(bars, fee_bps=5, stop_atr=2.0)
    print_report("SELFTEST", m)
    assert "error" not in m, m
    assert m["n_trades"] >= 1, "应至少有一笔交易"
    assert -1 < m["total_return"] < 50, m["total_return"]
    assert 0 <= m["win_rate"] <= 1
    print("\n自测通过 ✅ 引擎逻辑正常。")


def main():
    ap = argparse.ArgumentParser(description="技术评分规则回测")
    ap.add_argument("--stocks", help="逗号分隔代码，如 NVDA,MU,RKLB")
    ap.add_argument("--days", type=int, default=400, help="回测历史交易日数（默认400）")
    ap.add_argument("--fee_bps", type=float, default=5.0, help="单边费用(基点)，默认5")
    ap.add_argument("--stop_atr", type=float, default=2.0, help="ATR止损倍数，默认2")
    ap.add_argument("--selftest", action="store_true", help="合成数据自测（不联网）")
    args = ap.parse_args()

    if args.selftest:
        selftest(); return
    if not args.stocks:
        ap.error("请提供 --stocks 或用 --selftest")

    codes = [c.strip().upper() for c in args.stocks.split(",") if c.strip()]
    for code in codes:
        try:
            market, norm, disp = f.classify_stock(code)
            raw = (f.fetch_us(norm, args.days) if market == "us"
                   else f.fetch_cn_a(norm, args.days) if market == "cn_a"
                   else f.fetch_hk(norm, args.days))
            m = run_backtest(raw["ohlcv"], fee_bps=args.fee_bps, stop_atr=args.stop_atr)
            print_report(code, m)
        except Exception as e:
            print(f"\n[{code}] ⚠️ 失败：{e}")
    print("\n⚠️ 回测基于历史，不代表未来；样本内表现不等于实盘。建议进一步做 walk-forward 验证。")


if __name__ == "__main__":
    main()
