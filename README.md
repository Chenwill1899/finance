# 📈 美股智能分析 App / Stock Analysis Toolkit

一个跑在本地的美股技术分析看板：输入美股 ticker → 实时拉取行情 → 自动抓取相关新闻 → 精算技术指标 → 给出规则化的买卖参考与当日 K 线分析。网页主流程仅支持美股。

> ⚠️ 仅供学习与决策参考，**非投资建议**。投资有风险，请独立决策并严格止损。

## ✨ 功能

- **实时分析**：输入代码即自动拉数据、算指标、出决策看板（评分 100 分制 + 买/持/卖信号）。
- **技术指标**：MA(5/10/20/60)、MACD、RSI、量能、乖离率、**ATR 波动率**、**周线多周期共振**、**SPY/QQQ 相对强弱**。
- **当日 K 线**：点卡片看分时蜡烛图，叠加 VWAP / 昨收 / 开盘 / 日内高低 + **买点/止损/目标**策略线。
- **持仓管理**：代码后加 `@成本价`（如 `NVDA@200`）显示浮动盈亏并给持仓建议。
- **财报 & 新闻**：自动标注财报日（临近提醒）+ 拉取相关新闻（Google News RSS / yfinance，免 Key）并纳入建议。
- **自动刷新**：可选 15s–2min 间隔实时更新。
- **点位提醒**：浏览器通知，触及止损/目标/回踩买点时弹窗。
- **历史记录**：保存最近查询，一键重跑。
- **浅色「液态玻璃」UI**。

## 🚀 快速开始

所有功能都从统一入口 `run.py` 启动：

```bash
pip3 install -r requirements.txt        # 安装依赖（首次）
python3 run.py                           # 启动网页看板，自动打开 http://localhost:8765
```

macOS 用户可直接双击 `start.command`。

> 若你的环境需要代理访问网络，先运行 `proxy_on` 再启动。

### 一个入口，所有命令

```bash
python3 run.py                  # 启动网页看板（默认）
python3 run.py backtest --stocks "NVDA,MU" --days 400   # 回测
python3 run.py backtest --selftest                      # 回测自测（不联网）
python3 run.py fetch --stocks "NVDA,RDW" --extras       # 命令行拉数据(JSON)
python3 run.py test             # 跑全部离线测试
python3 run.py help             # 帮助
```

## 📁 文件结构

| 文件 | 说明 |
|---|---|
| `run.py` | **统一入口**：一个命令启动网页/回测/测试/CLI |
| `analyzer_app.py` | 本地 Web App（UI + 进程内数据引擎 + 缓存/并发 + 提醒）|
| `stock_data_fetcher.py` | 数据获取 + 技术指标计算引擎（可单独 CLI 使用）|
| `backtest_engine.py` | 回测引擎：用历史数据验证评分规则（含费用/ATR止损/对比基准）|
| `test_indicators.py` | 指标算法单元测试（`python3 test_indicators.py`）|
| `requirements.txt` | 依赖清单 |
| `start.command` | macOS 一键启动 |
| `量化工具路线图.md` | 如何升级为真正量化系统的路线图 |
| `使用说明.md` | 中文使用说明 |

## 🔧 CLI 用法（脚本单独跑）

```bash
python3 stock_data_fetcher.py --stocks "NVDA,RDW,RKLB" --days 120 --extras
```

输出结构化 JSON（行情 + 指标 + 评分 + 财报/新闻 + 新闻面解读 + SPY/QQQ 相对强弱）。

## 🧪 测试

```bash
python3 run.py test
```

## 📉 回测（验证规则是否赚钱）

```bash
python3 backtest_engine.py --stocks "NVDA,MU,RKLB" --days 400   # 真实历史回测
python3 backtest_engine.py --selftest                           # 合成数据自测（不联网）
```

无前视偏差（当日出信号、次日执行）、含手续费/滑点与 ATR 止损，输出年化、最大回撤、夏普、胜率、盈亏比，并与买入持有对比。

### Walk-Forward 走动验证（防过拟合）

```bash
python3 run.py backtest --stocks "NVDA,MU" --walkforward --train 180 --test 60
```

滚动窗口「训练区网格调参 → 测试区检验」，只统计**样本外(OOS)**结果，并给出「过拟合落差」（样本内 vs 样本外差距）。网页端在回测弹窗里点「🔬 走动验证」即可。这是判断规则是否真有效的关键一步。

## 🗺️ 路线图

当前为「决策支持工具」。要升级为系统化量化工具（回测、walk-forward、风险/组合模型、执行），见 [`量化工具路线图.md`](./量化工具路线图.md)。

## 数据来源

行情：Yahoo Finance（yfinance，可能有约 15 分钟延迟）。计划支持 Finnhub / Tiingo 等更专业的数据源。
