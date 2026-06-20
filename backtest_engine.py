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


def scores_per_day(bars):
    """逐日计算 (score_total, atr)，只用当日及之前的数据（指标只算一次，供任意窗口重放）。"""
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
        out[t] = (score["total"], atr)
    return out


# 默认参数（评分阈值化，便于优化）
DEFAULT_PARAMS = {"buy_th": 60, "sell_th": 35, "stop_atr": 2.0}


def simulate(bars, scores, lo, hi, params, fee_bps=5.0):
    """在 [lo, hi) 区间用预计算的评分重放策略；返回指标 + 资金曲线 + 每笔收益。
    入场：score >= buy_th；出场：score <= sell_th 或触发 ATR 止损。"""
    closes = [b["close"] for b in bars]
    opens = [b.get("open") or b["close"] for b in bars]
    lows = [b["low"] for b in bars]
    buy_th, sell_th, stop_atr = params["buy_th"], params["sell_th"], params["stop_atr"]
    fee = fee_bps / 10000.0
    lo = max(lo, WARMUP)
    hi = min(hi, len(bars) - 1)

    equity = 1.0; pos = 0; entry = None; stop = None
    curve = []; dates = []; trade_rets = []; n_trades = 0
    for t in range(lo, hi):
        sc, atr = scores[t]
        if sc is None:
            curve.append(equity); dates.append(bars[t + 1].get("date")); continue
        c2, l2 = closes[t + 1], lows[t + 1]
        if pos == 0:
            if sc >= buy_th:
                pos = 1; entry = opens[t + 1]
                stop = entry - stop_atr * atr if atr else entry * 0.92
                equity *= (1 - fee); n_trades += 1
                if stop and l2 <= stop:
                    equity *= stop / entry; trade_rets.append(stop / entry - 1); pos = 0; stop = None
                else:
                    equity *= c2 / entry
            curve.append(equity); dates.append(bars[t + 1].get("date")); continue
        prev = closes[t]
        if stop and l2 <= stop:
            equity *= stop / prev; equity *= (1 - fee)
            trade_rets.append(stop / entry - 1); pos = 0; stop = None
        else:
            equity *= c2 / prev
            if sc <= sell_th:
                equity *= (1 - fee); trade_rets.append(c2 / entry - 1); pos = 0; stop = None
        curve.append(equity); dates.append(bars[t + 1].get("date"))
    if pos == 1:
        equity *= (1 - fee); trade_rets.append(closes[hi] / entry - 1)

    days = max(hi - lo, 1)
    daily = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))] if len(curve) > 1 else [0]
    sharpe = 0.0
    if len(daily) > 2 and statistics.pstdev(daily) > 0:
        sharpe = statistics.mean(daily) / statistics.pstdev(daily) * math.sqrt(TRADING_DAYS)
    peak = -1e9; maxdd = 0.0
    for e in curve:
        peak = max(peak, e); maxdd = max(maxdd, (peak - e) / peak)
    cagr = equity ** (TRADING_DAYS / days) - 1 if days > 0 else 0
    wins = [r for r in trade_rets if r > 0]; losses = [r for r in trade_rets if r <= 0]
    win_rate = len(wins) / len(trade_rets) if trade_rets else 0
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0)
    bh = closes[hi] / closes[lo] - 1
    return {
        "total_return": round(equity - 1, 4), "cagr": round(cagr, 4), "sharpe": round(sharpe, 2),
        "max_drawdown": round(maxdd, 4), "n_trades": n_trades, "win_rate": round(win_rate, 3),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "buy_hold_return": round(bh, 4), "excess_vs_bh": round((equity - 1) - bh, 4),
        "_equity": equity, "_curve": curve, "_dates": dates,
    }


def run_backtest(bars, fee_bps=5.0, stop_atr=2.0, with_curve=False, params=None):
    """全样本回测（兼容旧接口）。"""
    n = len(bars)
    if n < WARMUP + 20:
        return {"error": f"数据不足（{n} 根，至少需要 {WARMUP+20}）"}
    p = dict(DEFAULT_PARAMS); p["stop_atr"] = stop_atr
    if params:
        p.update(params)
    scores = scores_per_day(bars)
    m = simulate(bars, scores, WARMUP, n - 1, p, fee_bps)
    closes = [b["close"] for b in bars]
    res = {k: v for k, v in m.items() if not k.startswith("_")}
    res["bars"] = n; res["days_tested"] = n - WARMUP; res["params"] = p
    if with_curve:
        curve = m["_curve"]
        bh_curve = [round(closes[WARMUP + 1 + i] / closes[WARMUP], 4) for i in range(len(curve))]
        res["curve"] = [round(x, 4) for x in curve]
        res["bh_curve"] = bh_curve
        res["dates"] = m["_dates"]
    return res


# 参数网格（walk-forward 优化用）
PARAM_GRID = [
    {"buy_th": b, "sell_th": s, "stop_atr": a}
    for b in (55, 60, 65, 70) for s in (30, 35, 40) for a in (1.5, 2.0, 2.5, 3.0)
]


def walk_forward(bars, train=180, test=60, fee_bps=5.0, optimize_by="sharpe", with_curve=False):
    """走动验证：滚动窗口「训练区调参→测试区检验」，只统计样本外(OOS)结果。"""
    n = len(bars)
    need = WARMUP + train + test
    if n < need:
        return {"error": f"数据不足：需 ≥{need} 根（当前 {n}）。请加大 --days 或减小 train/test。"}
    scores = scores_per_day(bars)
    closes = [b["close"] for b in bars]

    oos_rets = []          # 拼接样本外日收益
    oos_dates = []
    folds = []
    is_returns = []        # 各段样本内最优收益（用于过拟合对比）

    start = WARMUP
    while start + train + test <= n - 1:
        tr_lo, tr_hi = start, start + train
        te_lo, te_hi = tr_hi, tr_hi + test
        # 训练区网格搜索
        best, best_p, best_is = None, None, None
        for p in PARAM_GRID:
            mm = simulate(bars, scores, tr_lo, tr_hi, p, fee_bps)
            key = mm["sharpe"] if optimize_by == "sharpe" else mm["total_return"]
            if best is None or key > best:
                best, best_p, best_is = key, p, mm["total_return"]
        # 应用到测试区（样本外）
        te = simulate(bars, scores, te_lo, te_hi, best_p, fee_bps)
        cur = te["_curve"]
        if cur:
            fold_rets = [cur[0] - 1] + [cur[i] / cur[i - 1] - 1 for i in range(1, len(cur))]
            oos_rets += fold_rets
            oos_dates += te["_dates"]
        is_returns.append(best_is)
        folds.append({"train": [tr_lo, tr_hi], "test": [te_lo, te_hi],
                      "params": best_p, "is_return": round(best_is, 4),
                      "oos_return": te["total_return"], "oos_bh": te["buy_hold_return"]})
        start += test

    if not folds:
        return {"error": "窗口不足，无法走动验证。"}

    # 拼接 OOS 资金曲线与指标
    eq = 1.0; oos_curve = []
    for r in oos_rets:
        eq *= (1 + r); oos_curve.append(eq)
    days = len(oos_rets) or 1
    sharpe = 0.0
    if len(oos_rets) > 2 and statistics.pstdev(oos_rets) > 0:
        sharpe = statistics.mean(oos_rets) / statistics.pstdev(oos_rets) * math.sqrt(TRADING_DAYS)
    peak = -1e9; maxdd = 0.0
    for e in oos_curve:
        peak = max(peak, e); maxdd = max(maxdd, (peak - e) / peak)
    oos_total = eq - 1
    oos_cagr = eq ** (TRADING_DAYS / days) - 1 if days > 0 else 0
    is_avg = statistics.mean(is_returns) if is_returns else 0
    # 样本外覆盖区间的买入持有
    te_lo0 = folds[0]["test"][0]; te_hiN = folds[-1]["test"][1]
    bh = closes[te_hiN] / closes[te_lo0] - 1

    res = {
        "oos_total_return": round(oos_total, 4), "oos_cagr": round(oos_cagr, 4),
        "oos_sharpe": round(sharpe, 2), "oos_max_drawdown": round(maxdd, 4),
        "oos_buy_hold": round(bh, 4), "oos_excess_vs_bh": round(oos_total - bh, 4),
        "is_avg_return_per_fold": round(is_avg, 4),
        "n_folds": len(folds), "train": train, "test": test,
        "overfit_gap": round(is_avg - (oos_total / max(len(folds), 1)), 4),
        "folds": folds,
    }
    if with_curve:
        res["curve"] = [round(x, 4) for x in oos_curve]
        res["dates"] = oos_dates
    return res


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


def print_wf(code, m):
    if m.get("error"):
        print(f"\n[{code}] ⚠️ {m['error']}"); return
    verdict = "✅ 样本外跑赢" if m["oos_excess_vs_bh"] > 0 else "❌ 样本外跑输"
    overfit = "（落差大，疑过拟合）" if m["overfit_gap"] > 0.15 else "（落差可接受）"
    print(f"""
━━━ {code} Walk-Forward 走动验证（{m['n_folds']} 段，训练{m['train']}/测试{m['test']}）━━━
  样本外总收益 : {_fmt_pct(m['oos_total_return'])}    年化 : {_fmt_pct(m['oos_cagr'])}
  同期买入持有 : {_fmt_pct(m['oos_buy_hold'])}    超额 : {_fmt_pct(m['oos_excess_vs_bh'])}  {verdict}
  样本外夏普   : {m['oos_sharpe']}       最大回撤 : {_pct(m['oos_max_drawdown'])}
  样本内均收益 : {_fmt_pct(m['is_avg_return_per_fold'])}/段  过拟合落差 : {_fmt_pct(m['overfit_gap'])} {overfit}""")


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
    # walk-forward 自测
    wf = walk_forward(bars, train=120, test=40, with_curve=True)
    print_wf("SELFTEST", wf)
    assert "error" not in wf, wf
    assert wf["n_folds"] >= 2, wf
    assert len(wf["curve"]) == len(wf["dates"]), (len(wf["curve"]), len(wf["dates"]))
    print("\n自测通过 ✅ 引擎 + 走动验证逻辑正常。")


def main():
    ap = argparse.ArgumentParser(description="技术评分规则回测")
    ap.add_argument("--stocks", help="逗号分隔代码，如 NVDA,MU,RKLB")
    ap.add_argument("--days", type=int, default=400, help="回测历史交易日数（默认400）")
    ap.add_argument("--fee_bps", type=float, default=5.0, help="单边费用(基点)，默认5")
    ap.add_argument("--stop_atr", type=float, default=2.0, help="ATR止损倍数，默认2")
    ap.add_argument("--walkforward", action="store_true", help="走动验证(防过拟合)")
    ap.add_argument("--train", type=int, default=180, help="WF训练窗口(交易日)")
    ap.add_argument("--test", type=int, default=60, help="WF测试窗口(交易日)")
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
            if args.walkforward:
                wf = walk_forward(raw["ohlcv"], train=args.train, test=args.test, fee_bps=args.fee_bps)
                print_wf(code, wf)
            else:
                m = run_backtest(raw["ohlcv"], fee_bps=args.fee_bps, stop_atr=args.stop_atr)
                print_report(code, m)
        except Exception as e:
            print(f"\n[{code}] ⚠️ 失败：{e}")
    print("\n⚠️ 回测基于历史，不代表未来；样本内表现不等于实盘。建议进一步做 walk-forward 验证。")


if __name__ == "__main__":
    main()
