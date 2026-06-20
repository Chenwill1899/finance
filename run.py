#!/usr/bin/env python3
"""
统一入口 —— 一个脚本启动所有功能。

用法：
    python3 run.py                      启动网页看板（默认）
    python3 run.py app                  同上
    python3 run.py backtest --stocks "NVDA,MU" --days 400   跑回测
    python3 run.py backtest --selftest  回测引擎自测（不联网）
    python3 run.py fetch --stocks "NVDA,MU" --extras        命令行拉数据(JSON)
    python3 run.py test                 跑指标单元测试
    python3 run.py help                 显示本帮助
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

USAGE = __doc__


def main():
    args = sys.argv[1:]
    cmd = (args[0].lower() if args else "app")
    rest = args[1:]

    if cmd in ("app", "start", "web", "run"):
        import analyzer_app
        analyzer_app.main()

    elif cmd in ("backtest", "bt"):
        import backtest_engine
        sys.argv = ["backtest_engine.py"] + rest
        backtest_engine.main()

    elif cmd in ("fetch", "cli", "data"):
        import stock_data_fetcher
        sys.argv = ["stock_data_fetcher.py"] + rest
        stock_data_fetcher.main()

    elif cmd in ("test", "tests"):
        import test_indicators as ti
        tests = [v for k, v in sorted(vars(ti).items()) if k.startswith("test_") and callable(v)]
        for t in tests:
            t()
        print(f"\n全部 {len(tests)} 项测试通过 ✅")

    elif cmd in ("help", "-h", "--help"):
        print(USAGE)

    else:
        print(f"未知命令：{cmd}\n")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
