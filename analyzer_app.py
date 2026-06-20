#!/usr/bin/env python3
"""
美股智能分析 App（本地网页版）
=================================
在你自己的电脑上运行：页面里输入股票代码 -> 自动后台跑 stock_data_fetcher.py -> 出决策看板。

用法：
    proxy_on                      # 先开代理（你电脑的命令）
    python3 analyzer_app.py       # 启动，然后浏览器会自动打开 http://localhost:8765

依赖：只需 yfinance（pip3 install yfinance --break-system-packages）。
本文件依赖同目录下的 stock_data_fetcher.py（我已经放在同一个文件夹里）。
"""
import http.server, socketserver, subprocess, sys, os, json, urllib.parse, webbrowser, threading, time
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
FETCHER = os.path.join(HERE, "stock_data_fetcher.py")
CACHE_TTL = 45          # 秒：同一只票多次刷新走缓存，避免反复拉数据
MAX_WORKERS = 6         # 并发拉取的线程数

# 把 fetcher 作为模块直接 import 进本进程（省掉每次起子进程 + 重新 import yfinance 的开销）
FETCHER_MOD = None
try:
    _spec = importlib.util.spec_from_file_location("stock_data_fetcher", FETCHER)
    FETCHER_MOD = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(FETCHER_MOD)
except Exception as _e:
    print(f"⚠️  无法导入 fetcher 模块（将回退到子进程模式）：{_e}")

# 回测引擎（网页内回测用）
BT_MOD = None
try:
    _bspec = importlib.util.spec_from_file_location("backtest_engine", os.path.join(HERE, "backtest_engine.py"))
    BT_MOD = importlib.util.module_from_spec(_bspec)
    _bspec.loader.exec_module(BT_MOD)
except Exception as _e:
    print(f"⚠️  无法导入回测引擎：{_e}")


def _fetch_ohlcv(code, days):
    """按市场拉取日线（给回测用）。"""
    market, norm, _ = FETCHER_MOD.classify_stock(code)
    if market == "us":
        return FETCHER_MOD.fetch_us(norm, days)["ohlcv"]
    if market == "cn_a":
        return FETCHER_MOD.fetch_cn_a(norm, days)["ohlcv"]
    if market == "cn_hk":
        return FETCHER_MOD.fetch_hk(norm, days)["ohlcv"]
    raise ValueError(f"无法识别代码：{code}")

_CACHE = {}             # code -> (timestamp, result_dict)
_CACHE_LOCK = threading.Lock()


def _analyze_one(code, days=120):
    """单只：带 TTL 缓存。"""
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(code)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    res = FETCHER_MOD.analyze_stock(code, days, fetch_news=False, extras=True)
    with _CACHE_LOCK:
        _CACHE[code] = (time.time(), res)
    return res


def analyze_codes(codes, days=120):
    """多只并发，返回与 CLI 相同结构的 JSON dict。"""
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_analyze_one, c, days): c for c in codes}
        done = {}
        for f in as_completed(futs):
            c = futs[f]
            try:
                done[c] = f.result()
            except Exception as e:
                errors.append({"code": c, "error": str(e), "type": type(e).__name__})
    for c in codes:               # 保持输入顺序
        if c in done:
            results.append(done[c])
    return {
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "analysis_time": datetime.now().strftime("%H:%M:%S"),
        "stocks": results, "errors": errors,
        "total_requested": len(codes), "total_success": len(results),
        "engine": "in-process",
    }

PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>美股智能分析看板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js"></script>
<style>
:root{color-scheme:light;--bg:#f6f8fb;--card:#fff;--line:#e6eaf0;--ink:#1a2230;--sub:#6b7687;
--green:#0ca678;--green-bg:#e6f7f1;--red:#e03131;--red-bg:#fdecec;--amber:#f08c00;--amber-bg:#fff4e2;
--gray:#868e96;--gray-bg:#eef1f5;--blue:#1c7ed6;--shadow:0 1px 3px rgba(20,30,50,.06),0 4px 16px rgba(20,30,50,.05);}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.5}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 56px}h1{font-size:21px;margin:0 0 4px}
.lede{color:var(--sub);margin:0 0 18px;font-size:13px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:16px;margin-bottom:18px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input[type=text]{flex:1 1 240px;border:1px solid var(--line);border-radius:9px;padding:11px 13px;font:inherit;background:#fcfdff;color:var(--ink)}
button{cursor:pointer;border:0;border-radius:9px;padding:11px 18px;font:inherit;font-weight:600;background:var(--blue);color:#fff}
button:hover{filter:brightness(1.05)}button:disabled{opacity:.5;cursor:wait}
button.ghost{background:var(--gray-bg);color:var(--ink);padding:8px 12px;font-size:12px}
.hint{font-size:12px;color:var(--sub);margin-top:8px}.err{color:var(--red);font-size:13px;margin-top:8px;display:none}
.status{color:var(--blue);font-size:13px;margin-left:6px}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin:4px 0 18px}
.pill{border-radius:999px;padding:6px 13px;font-size:12.5px;font-weight:600;border:1px solid var(--line);background:var(--card)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:16px;display:flex;flex-direction:column;gap:12px}
.chead{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}.tk{font-size:17px;font-weight:700}
.nm{font-size:11.5px;color:var(--sub);margin-top:1px}.px{text-align:right}.px .p{font-size:18px;font-weight:700}.px .c{font-size:12.5px;font-weight:600}
.sig{display:inline-flex;align-items:center;gap:6px;border-radius:9px;padding:7px 11px;font-weight:700;font-size:13px}
.score{font-variant-numeric:tabular-nums}.gauge{height:8px;border-radius:6px;background:var(--gray-bg);overflow:hidden}.gauge>div{height:100%;border-radius:6px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.kv{background:#fbfcfe;border:1px solid var(--line);border-radius:9px;padding:7px 9px}
.kv .k{font-size:10.5px;color:var(--sub);text-transform:uppercase;letter-spacing:.4px}.kv .v{font-size:13px;font-weight:600;margin-top:2px}
.chartbox{height:140px;position:relative}.op{border-top:1px dashed var(--line);padding-top:11px}
.op h4{margin:0 0 6px;font-size:12px;color:var(--sub);text-transform:uppercase;letter-spacing:.5px}
.levels{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}.lv{flex:1;min-width:84px;border-radius:9px;padding:7px 8px;text-align:center;border:1px solid var(--line)}
.lv .lk{font-size:10.5px;color:var(--sub)}.lv .lvv{font-size:13.5px;font-weight:700;margin-top:1px}
.op .txt{font-size:13px;line-height:1.55}.tag{display:inline-block;font-size:11px;font-weight:600;border-radius:6px;padding:2px 7px;margin:0 4px 4px 0}
.disc{font-size:11.5px;color:var(--sub);margin-top:22px;line-height:1.6;border-top:1px solid var(--line);padding-top:14px}
.empty{text-align:center;color:var(--sub);padding:30px 10px;font-size:13px}
.card{cursor:pointer;transition:box-shadow .15s,transform .12s}
.card:hover{box-shadow:0 4px 10px rgba(20,30,50,.1),0 12px 30px rgba(20,30,50,.09);transform:translateY(-2px)}
.card .more{font-size:11px;color:var(--blue);text-align:center;margin-top:-2px}
/* modal */
.mask{position:fixed;inset:0;background:rgba(18,26,40,.45);display:none;align-items:flex-start;justify-content:center;z-index:50;padding:32px 14px;overflow:auto}
.mask.on{display:flex}
.modal{background:var(--card);border-radius:16px;box-shadow:0 20px 60px rgba(10,20,40,.3);width:100%;max-width:760px;padding:20px}
.mhead{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:6px}
.mhead .tk{font-size:22px;font-weight:700}.mhead .nm{font-size:12px;color:var(--sub)}
.mhead .px{text-align:right}.mhead .px .p{font-size:22px;font-weight:700}.mhead .px .c{font-size:13px;font-weight:600}
.mdate{font-size:12px;color:var(--sub);margin-bottom:10px}
.close{cursor:pointer;border:0;background:var(--gray-bg);color:var(--ink);border-radius:8px;padding:6px 10px;font-weight:700}
.bigchart{height:300px;position:relative;margin:6px 0 14px}
.stat6{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
.stat6 .kv{background:#fbfcfe;border:1px solid var(--line);border-radius:9px;padding:8px 9px}
.mverdict{background:#f7f9fc;border:1px solid var(--line);border-radius:10px;padding:12px 14px;font-size:13.5px;line-height:1.6}
.mverdict b{color:var(--ink)}
.loading{text-align:center;color:var(--blue);padding:40px;font-size:14px}
.histbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px}
.histbar .hl{font-size:12px;color:var(--sub);margin-right:2px}
.chip{cursor:pointer;border:1px solid var(--line);background:#fcfdff;border-radius:999px;padding:5px 12px;font-size:12.5px;color:var(--ink);display:inline-flex;align-items:center;gap:6px}
.chip:hover{border-color:var(--blue);color:var(--blue)}
.chip .x{color:var(--sub);font-size:13px;line-height:1}.chip .x:hover{color:var(--red)}
.histclear{cursor:pointer;font-size:12px;color:var(--sub);background:none;border:0;padding:4px 6px}
.histclear:hover{color:var(--red)}
@media(max-width:560px){.stat6{grid-template-columns:repeat(2,1fr)}}
/* ===== Liquid Glass（浅色） ===== */
:root{--glass:rgba(255,255,255,.55);--glass-strong:rgba(255,255,255,.74);--glass-brd:rgba(255,255,255,.65);
 --glass-sh:0 8px 32px rgba(80,110,160,.16),inset 0 1px 0 rgba(255,255,255,.75);
 --ink:#1d2433;--sub:#5b6985;--blue:#2f7ad6;}
html{background:#eaf0f8}
body{background:linear-gradient(160deg,#eef3fb 0%,#f4eefb 48%,#eaf6f4 100%);min-height:100vh;position:relative;overflow-x:hidden;
 -webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:-25% -12% auto -12%;height:80vh;z-index:-1;pointer-events:none;
 background:radial-gradient(42% 52% at 16% 18%,rgba(120,160,255,.42),transparent 70%),
  radial-gradient(40% 50% at 84% 10%,rgba(192,140,255,.34),transparent 70%),
  radial-gradient(46% 54% at 62% 86%,rgba(110,225,205,.30),transparent 70%);filter:blur(18px)}
.wrap{position:relative;z-index:1}
h1{letter-spacing:.3px;background:linear-gradient(90deg,#2b3a5e,#5b3d8f);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.panel,.card{background:var(--glass)!important;-webkit-backdrop-filter:blur(22px) saturate(180%);backdrop-filter:blur(22px) saturate(180%);
 border:1px solid var(--glass-brd)!important;box-shadow:var(--glass-sh)!important;border-radius:20px!important}
.card:hover{box-shadow:0 16px 44px rgba(80,110,160,.24),inset 0 1px 0 rgba(255,255,255,.85)!important;transform:translateY(-3px)}
input[type=text]{background:rgba(255,255,255,.58)!important;border:1px solid rgba(255,255,255,.72)!important;
 -webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);box-shadow:inset 0 1px 3px rgba(80,110,160,.10);border-radius:13px!important;padding:12px 14px!important}
input[type=text]:focus{outline:none;border-color:rgba(80,140,230,.55)!important;box-shadow:0 0 0 3px rgba(80,140,230,.16)}
button{background:linear-gradient(180deg,#4f96ea,#2f7ad6)!important;box-shadow:0 6px 16px rgba(47,122,214,.34),inset 0 1px 0 rgba(255,255,255,.45)!important;border-radius:13px!important}
button.ghost,.close,.histclear{background:rgba(255,255,255,.62)!important;color:var(--ink)!important;
 box-shadow:inset 0 0 0 1px rgba(255,255,255,.7),0 2px 10px rgba(80,110,160,.12)!important;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
.histclear{box-shadow:none!important;background:transparent!important}
.pill,.chip{background:rgba(255,255,255,.55)!important;border:1px solid var(--glass-brd)!important;-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);
 box-shadow:0 2px 10px rgba(80,110,160,.10)!important;border-radius:999px!important}
.kv,.lv{background:rgba(255,255,255,.45)!important;border:1px solid rgba(255,255,255,.62)!important;border-radius:13px!important;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.sig{-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);box-shadow:inset 0 0 0 1px rgba(255,255,255,.55)}
.gauge{background:rgba(120,140,180,.18)!important}
.op,.disc{border-top:1px solid rgba(120,140,180,.25)!important}
.mask{background:rgba(40,55,90,.30)!important;-webkit-backdrop-filter:blur(7px);backdrop-filter:blur(7px)}
.modal{background:var(--glass-strong)!important;-webkit-backdrop-filter:blur(30px) saturate(180%);backdrop-filter:blur(30px) saturate(180%);
 border:1px solid var(--glass-brd)!important;border-radius:26px!important;box-shadow:0 30px 90px rgba(40,60,110,.32),inset 0 1px 0 rgba(255,255,255,.85)!important}
.mverdict{background:rgba(255,255,255,.5)!important;border:1px solid rgba(255,255,255,.62)!important;border-radius:15px!important;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
.stat6 .kv{background:rgba(255,255,255,.45)!important}
/* auto-refresh switch */
.arbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px;font-size:13px;color:var(--sub)}
.switch{position:relative;display:inline-block;width:44px;height:25px}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;inset:0;background:rgba(120,140,180,.32);border-radius:999px;transition:.25s;box-shadow:inset 0 1px 3px rgba(0,0,0,.12)}
.slider:before{content:"";position:absolute;height:19px;width:19px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.25s;box-shadow:0 1px 4px rgba(0,0,0,.28)}
.switch input:checked+.slider{background:linear-gradient(180deg,#4f96ea,#2f7ad6)}
.switch input:checked+.slider:before{transform:translateX(19px)}
.arbar select{border:1px solid var(--glass-brd);background:rgba(255,255,255,.62);border-radius:10px;padding:6px 9px;font:inherit;font-size:12.5px;color:var(--ink);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.arbar .upd{margin-left:auto;font-size:12px;color:var(--sub)}
.live{display:inline-flex;align-items:center;gap:5px;color:var(--green);font-weight:600}
.live .dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 1.4s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.more{display:flex;gap:10px;align-items:center;justify-content:center}
.btbtn{cursor:pointer;border:1px solid var(--glass-brd);background:rgba(255,255,255,.6);color:var(--blue);border-radius:8px;padding:3px 11px;font-size:11px;font-weight:600;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.btbtn:hover{border-color:var(--blue)}
.btctrl{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px}
.btctrl .f{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--sub)}
.btctrl input{width:90px;border:1px solid var(--glass-brd);background:rgba(255,255,255,.6);border-radius:9px;padding:7px 9px;font:inherit;font-size:13px;color:var(--ink)}
.bigchart2{height:240px;position:relative;margin:4px 0 14px}
</style></head><body><div class="wrap">
<h1>📈 美股智能分析看板</h1>
<p class="lede">输入股票代码，点「分析」——自动跑脚本、画图、算指标、给买卖意见。数据来自 Yahoo Finance 实时拉取。</p>
<div class="panel">
  <div class="row">
    <input id="tickers" type="text" placeholder="NVDA@200, MU, RKLB" value=""
      onkeydown="if(event.key==='Enter')analyze()">
    <button id="go" onclick="analyze()">分析</button>
    <span id="status" class="status"></span>
  </div>
  <div class="err" id="err"></div>
  <div class="hint">美股用字母（NVDA），A股用6位数字（600519），港股加HK（HK00700）。多只用逗号分隔。<b>可选：在代码后加 <code>@成本价</code> 标注当前持仓</b>，例如 <code>NVDA@200</code>，会显示浮动盈亏并给持仓建议。</div>
  <div id="history" class="histbar"></div>
  <div class="arbar">
    <label class="switch"><input type="checkbox" id="arToggle" onchange="toggleAuto()"><span class="slider"></span></label>
    <span>自动刷新</span>
    <select id="arInt" onchange="if(document.getElementById('arToggle').checked)toggleAuto()">
      <option value="15">每 15 秒</option>
      <option value="30" selected>每 30 秒</option>
      <option value="60">每 60 秒</option>
      <option value="120">每 2 分钟</option>
    </select>
    <span style="width:1px;height:18px;background:rgba(120,140,180,.3)"></span>
    <label class="switch"><input type="checkbox" id="alToggle" onchange="toggleAlerts()"><span class="slider"></span></label>
    <span>点位提醒</span>
    <span class="upd" id="lastUpd"></span>
  </div>
</div>
<div id="summary" class="summary"></div>
<div id="cards" class="cards"><div class="empty">输入代码后点「分析」。首次拉取约 5–15 秒。</div></div>
<div class="disc">⚠️ 本看板基于脚本精算的技术指标 + 规则化判断，<b>仅供参考，非投资建议</b>。技术信号会滞后，重大消息面（财报、指数调整等）需自行核对。投资有风险，请独立决策并严格止损。</div>
</div>

<div class="mask" id="mask" onclick="if(event.target===this)closeDetail()">
  <div class="modal">
    <div class="mhead">
      <div><div class="tk" id="mtk"></div><div class="nm" id="mnm"></div></div>
      <div style="display:flex;gap:10px;align-items:flex-start">
        <div class="px"><div class="p" id="mpx"></div><div class="c" id="mchg"></div></div>
        <button class="close" onclick="closeDetail()">✕</button>
      </div>
    </div>
    <div class="mdate" id="mdate"></div>
    <div id="mbody"><div class="loading">正在拉取当日分时数据…</div></div>
  </div>
</div>

<div class="mask" id="btmask" onclick="if(event.target===this)closeBT()">
  <div class="modal">
    <div class="mhead">
      <div><div class="tk" id="bttk"></div><div class="nm">📉 策略回测 · 技术评分规则（无前视偏差·含费用·ATR止损）</div></div>
      <button class="close" onclick="closeBT()">✕</button>
    </div>
    <div id="btbody"><div class="loading">回测计算中…</div></div>
  </div>
</div>

<script>
const charts={};
const fmt=(n,d=2)=>{if(n===null||n===undefined||isNaN(n))return '—';const a=Math.abs(n);
 if(a>=1000)return Number(n).toLocaleString('en-US',{maximumFractionDigits:0});return Number(n).toFixed(d);};
const pct=n=>(n>0?'+':'')+fmt(n,2)+'%';const up=n=>n>=0;
const SIG={strong_buy:{t:'强烈买入',c:'green'},buy:{t:'买入',c:'green'},hold:{t:'持有',c:'amber'},
 wait:{t:'观望',c:'gray'},sell:{t:'卖出',c:'red'},strong_sell:{t:'强烈卖出',c:'red'}};
const COL={green:['var(--green)','var(--green-bg)'],red:['var(--red)','var(--red-bg)'],
 amber:['var(--amber)','var(--amber-bg)'],gray:['var(--gray)','var(--gray-bg)'],blue:['var(--blue)','#e7f1fb']};
function buildOpinion(s){
 const r=s.realtime||{},ind=s.indicators||{},ma=ind.ma||{},rsi=ind.rsi||{},bias=ind.bias||{},vol=ind.volume||{};
 const sc=s.trend_score||{};const price=r.price ?? (s.recent_bars&&s.recent_bars.length?s.recent_bars[s.recent_bars.length-1].close:null);
 let sig=sc.signal||'hold';const flags=[];const rsi12=rsi.RSI12,b5=bias.bias_ma5;
 if(rsi12>=80&&(sig==='strong_buy'||sig==='buy')){sig='wait';flags.push(['RSI 超买，勿追','red']);}
 if(b5>=5){if(sig==='strong_buy'||sig==='buy')sig='hold';flags.push(['乖离 +'+fmt(b5,1)+'%，勿追高','amber']);}
 if(vol.trend==='heavy_volume_down')flags.push(['放量下跌','red']);
 if(vol.trend==='shrink_pullback')flags.push(['缩量回调','green']);
 if(vol.trend==='heavy_volume_up')flags.push(['放量上涨','green']);
 if((ma.alignment||'').includes('bull'))flags.push(['均线多头','green']);
 if((ma.alignment||'').includes('bear'))flags.push(['均线空头','red']);
 // 周线多周期共振
 const wk=s.weekly||{};
 if((wk.trend||'').includes('bull'))flags.push(['周线多头','green']);
 else if((wk.trend||'').includes('bear'))flags.push(['周线空头','red']);
 // 财报临近提醒（硬规则：5天内不给买入）
 const ed=s.earnings; let earnSoon=false;
 if(ed&&ed.days!=null&&ed.days>=0&&ed.days<=10){
   flags.push(['财报 '+ed.days+'天后','amber']);
   if(ed.days<=5){earnSoon=true; if(sig==='strong_buy'||sig==='buy')sig='hold';}
 }
 const atr=(ind.atr||{}).atr;
 const lows=(s.recent_bars||[]).map(b=>b.low).filter(x=>x);const recentLow=lows.length?Math.min.apply(null,lows):null;
 const supports=[ma.MA10,ma.MA60,ma.MA20].filter(x=>x&&x<price).sort((a,b)=>b-a);
 const buyZone=supports[0]||ma.MA5;
 // ATR 止损：现价 - 2×ATR（更贴合波动率）；无ATR时回退到MA60/近期低点
 const stop=atr?+(price-2*atr).toFixed(2):Math.min.apply(null,[ma.MA60,recentLow].filter(x=>x));
 const bullish=(ma.alignment||'').includes('bull');const target=bullish?(r.week_52_high||ma.MA5*1.08):(ma.MA20||price*1.05);
 const meta=SIG[sig]||SIG.hold;let verb;
 if(sig==='strong_buy'||sig==='buy')verb='技术面偏多，可逢回踩 '+fmt(buyZone)+' 附近分批介入';
 else if(sig==='hold')verb='持仓为主，方向未明，破位则减';
 else if(sig==='wait')verb='暂时观望，等企稳信号（缩量+站回均线）再说';
 else verb='趋势走弱，不宜接刀，反弹看作减仓机会';
 if(earnSoon)verb+='；⚠️ 财报临近（'+ed.days+'天），波动大，建议轻仓或等财报后';
 return {sig,meta,flags,price,buyZone,stop,target,verb,atr,wk,ed};
}
function posNote(o,cost){
 if(!cost)return '';
 if(o.price<=o.stop)return '<b style="color:var(--red)">⚠ 已跌破止损位 $'+fmt(o.stop)+'，建议止损离场。</b>';
 if(o.price>=o.target)return '<b style="color:var(--blue)">🎯 已达目标位 $'+fmt(o.target)+'，可考虑分批止盈。</b>';
 const pnl=(o.price-cost)/cost*100;
 if(pnl<0)return '当前浮亏 '+fmt(pnl,1)+'%，止损守 $'+fmt(o.stop)+'，跌破离场。';
 return '当前浮盈 +'+fmt(pnl,1)+'%，可持有，目标看 $'+fmt(o.target)+'。';
}
function render(data){
 const stocks=data.stocks||[];const sumEl=document.getElementById('summary'),cardsEl=document.getElementById('cards');
 const POS=window._pos||{};
 Object.values(charts).forEach(c=>c.destroy());for(const k in charts)delete charts[k];
 const errs=data.errors||[];
 if(!stocks.length){cardsEl.innerHTML='<div class="empty">没有成功获取数据。'+(errs.length?errs.map(e=>e.code+': '+e.error).join('；'):'')+'</div>';sumEl.innerHTML='';return;}
 const ops=stocks.map(buildOpinion);window._ANA={stocks,ops,pos:POS};checkAlerts(stocks,ops,POS);const cnt={buy:0,hold:0,sell:0};
 ops.forEach(o=>{const c=o.meta.c;if(c==='green')cnt.buy++;else if(c==='amber'||c==='gray')cnt.hold++;else cnt.sell++;});
 sumEl.innerHTML='<span class="pill">📅 '+(data.analysis_date||'')+' '+(data.analysis_time||'')+'</span>'
  +'<span class="pill">共 '+stocks.length+' 只</span>'
  +'<span class="pill" style="border-color:var(--green);color:var(--green)">🟢 偏多 '+cnt.buy+'</span>'
  +'<span class="pill" style="border-color:var(--amber);color:var(--amber)">🟡 中性 '+cnt.hold+'</span>'
  +'<span class="pill" style="border-color:var(--red);color:var(--red)">🔴 偏空 '+cnt.sell+'</span>'
  +(errs.length?'<span class="pill" style="border-color:var(--red);color:var(--red)">失败 '+errs.length+'</span>':'');
 cardsEl.innerHTML='';
 stocks.forEach((s,i)=>{
  const o=ops[i],r=s.realtime||{},ind=s.indicators||{},ma=ind.ma||{},rsi=ind.rsi||{},macd=ind.macd||{},vol=ind.volume||{},bias=ind.bias||{};
  const sc=s.trend_score||{};const c0=COL[o.meta.c];const fg=c0[0],bg=c0[1];
  const chg=r.change_pct;const cc=up(chg)?'var(--green)':'var(--red)';
  const cost=POS[s.code];const pnl=cost?((o.price-cost)/cost*100):null;
  const pnc=pnl>=0?'var(--green)':'var(--red)';
  const holdHtml=cost?('<div style="font-size:11.5px;color:var(--sub);margin-top:-4px">📍 持仓成本 $'+fmt(cost)+' · 浮动 <b style="color:'+pnc+'">'+pct(pnl)+'</b></div>'):'';
  const pNote=posNote(o,cost);
  const macdMap={golden_cross_above_zero:'零上金叉',golden_cross:'金叉',crossing_above_zero:'上穿零轴',bullish:'多头',neutral:'中性',bearish:'空头',death_cross:'死叉',crossing_below_zero:'下穿零轴'};
  const volMap={heavy_volume_up:'放量上涨',heavy_volume_down:'放量下跌',shrink_pullback:'缩量回调',shrink_up:'缩量上涨',normal:'平量'};
  const zoneMap={overbought:'超买',oversold:'超卖',strong:'偏强',weak:'偏弱',neutral:'中性'};
  const card=document.createElement('div');card.className='card';
  card.innerHTML='<div class="chead"><div><div class="tk">'+s.code+'</div><div class="nm">'+(s.name||'')+'</div></div>'
   +'<div class="px"><div class="p">$'+fmt(o.price)+'</div><div class="c" style="color:'+cc+'">'+pct(chg)+'</div></div></div>'
   +holdHtml
   +'<div class="row" style="justify-content:space-between"><span class="sig" style="color:'+fg+';background:'+bg+'">'+o.meta.t+'</span>'
   +'<span class="score" style="color:var(--sub);font-size:12.5px">评分 <b style="color:var(--ink);font-size:15px">'+(sc.total!=null?sc.total:'—')+'</b>/100</span></div>'
   +'<div class="gauge"><div style="width:'+Math.max(2,Math.min(100,sc.total||0))+'%;background:'+fg+'"></div></div>'
   +'<div class="chartbox"><canvas id="ch'+i+'"></canvas></div>'
   +'<div class="grid">'
   +'<div class="kv"><div class="k">趋势/均线</div><div class="v">'+(ma.alignment_detail?(ma.alignment_detail.includes('bull')?'多头排列':ma.alignment_detail.includes('bear')?'空头排列':'震荡'):'—')+'</div></div>'
   +'<div class="kv"><div class="k">MACD</div><div class="v">'+(macdMap[macd.signal]||'—')+'</div></div>'
   +'<div class="kv"><div class="k">RSI(12)</div><div class="v">'+fmt(rsi.RSI12,1)+' <span style="font-weight:400;color:var(--sub)">'+(zoneMap[rsi.zone]||'')+'</span></div></div>'
   +'<div class="kv"><div class="k">量能</div><div class="v">'+(volMap[vol.trend]||'—')+' '+(vol.vol_ratio?'('+fmt(vol.vol_ratio,2)+'x)':'')+'</div></div>'
   +'<div class="kv"><div class="k">乖离(MA5)</div><div class="v" style="color:'+(bias.bias_ma5>=5?'var(--red)':'inherit')+'">'+fmt(bias.bias_ma5,2)+'%</div></div>'
   +'<div class="kv"><div class="k">52周区间</div><div class="v" style="font-size:11.5px">'+fmt(r.week_52_low)+' – '+fmt(r.week_52_high)+'</div></div>'
   +'</div>'
   +'<div class="op"><h4>操作参考</h4><div class="levels">'
   +'<div class="lv"><div class="lk">买点(回踩)</div><div class="lvv" style="color:var(--green)">$'+fmt(o.buyZone)+'</div></div>'
   +'<div class="lv"><div class="lk">止损</div><div class="lvv" style="color:var(--red)">$'+fmt(o.stop)+'</div></div>'
   +'<div class="lv"><div class="lk">目标</div><div class="lvv" style="color:var(--blue)">$'+fmt(o.target)+'</div></div></div>'
   +'<div>'+o.flags.map(f=>'<span class="tag" style="color:'+COL[f[1]][0]+';background:'+COL[f[1]][1]+'">'+f[0]+'</span>').join('')+'</div>'
   +'<div class="txt">'+o.verb+'。'+(pNote?' '+pNote:'')+'</div></div>'
   +'<div class="more"><span>🔍 当日K线</span>'
   +'<button class="btbtn" onclick="event.stopPropagation();openBacktest(\''+s.code+'\',\''+(s.name||'').replace(/\x27/g,"")+'\')">📉 回测</button></div>';
  card.onclick=()=>openDetail(s.code,s.name||'');
  cardsEl.appendChild(card);
  const bars=s.recent_bars||[];const labels=bars.map(b=>(b.date||'').slice(5));const closes=bars.map(b=>b.close);
  const mk=(v,col)=>({label:'',data:labels.map(()=>v),borderColor:col,borderWidth:1,borderDash:[4,4],pointRadius:0,fill:false});
  charts['ch'+i]=new Chart(document.getElementById('ch'+i),{type:'line',
   data:{labels,datasets:[{label:'收盘',data:closes,borderColor:fg,backgroundColor:bg,borderWidth:2,pointRadius:0,tension:.25,fill:true},
    ma.MA5?mk(ma.MA5,'#adb5bd'):null,ma.MA20?mk(ma.MA20,'#74c0fc'):null].filter(Boolean)},
   options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>'$'+fmt(c.parsed.y)}}},
    scales:{x:{ticks:{font:{size:9},maxTicksLimit:5},grid:{display:false}},y:{ticks:{font:{size:9},callback:v=>'$'+fmt(v,0)},grid:{color:'#f0f2f6'}}}}});
 });
}
async function analyze(silent){
 const err=document.getElementById('err');if(!silent)err.style.display='none';
 const go=document.getElementById('go'),st=document.getElementById('status');
 const items=document.getElementById('tickers').value.split(/[,\n]+/).map(s=>s.trim()).filter(Boolean);
 const posMap={}, codes=[];
 items.forEach(it=>{const p=it.split('@');const code=(p[0]||'').trim().toUpperCase();if(!code)return;codes.push(code);
   const c=parseFloat((p[1]||'').trim());if(!isNaN(c))posMap[code]=c;});
 window._pos=posMap;
 if(!codes.length){ if(!silent){err.textContent='请输入至少一个代码。';err.style.display='block';} return; }
 if(!silent){go.disabled=true;st.textContent='正在拉取数据并计算指标…';}
 try{
  const res=await fetch('/api/analyze?stocks='+encodeURIComponent(codes.join(',')));
  const data=await res.json();
  if(data.fatal){throw new Error(data.fatal);}
  render(data);markUpdated();
  if(!silent){saveHistory(items.join(', '));st.textContent='完成 ✓';setTimeout(()=>st.textContent='',2000);}
 }catch(e){ if(!silent){err.textContent='出错了：'+e.message+'（确认已 proxy_on 且装了 yfinance）';err.style.display='block';st.textContent='';} }
 if(!silent)go.disabled=false;
}
function markUpdated(){
 const el=document.getElementById('lastUpd');if(!el)return;
 const t=new Date().toLocaleTimeString('zh-CN',{hour12:false});
 const on=document.getElementById('arToggle')&&document.getElementById('arToggle').checked;
 el.innerHTML=(on?'<span class="live"><span class="dot"></span>实时</span> · ':'')+'最后更新 '+t;
}
// ---------- 自动刷新 ----------
let autoTimer=null;
function toggleAuto(){
 const on=document.getElementById('arToggle').checked;
 if(autoTimer){clearInterval(autoTimer);autoTimer=null;}
 if(on){
  const sec=parseInt(document.getElementById('arInt').value,10)||30;
  autoTimer=setInterval(autoTick,sec*1000);
  autoTick();
 }
 markUpdated();
}
async function autoTick(){
 const has=document.getElementById('tickers').value.trim();
 if(!has)return;
 await analyze(true);
 if(document.getElementById('mask').classList.contains('on')&&window._curDetail){
  openDetail(window._curDetail.code,window._curDetail.name,true);
 }
}
// ---------- 点位提醒 ----------
function toggleAlerts(){
 const on=document.getElementById('alToggle').checked;
 if(on&&'Notification'in window&&Notification.permission==='default')Notification.requestPermission();
}
function notify(title,body){
 try{ if('Notification'in window&&Notification.permission==='granted')new Notification(title,{body});
   else console.log('[提醒]',title,body); }catch(e){}
}
const _alertState={};
function checkAlerts(stocks,ops,POS){
 if(!document.getElementById('alToggle')||!document.getElementById('alToggle').checked)return;
 stocks.forEach((s,i)=>{
  const o=ops[i],code=s.code,price=o.price,st=_alertState[code]||{};
  const fire=(k,title,body)=>{if(!st[k]){notify(title,body);st[k]=true;}};
  if(price<=o.stop){fire('stop','⚠️ '+code+' 触及止损',code+' 现价 $'+fmt(price)+' ≤ 止损 $'+fmt(o.stop));}else st.stop=false;
  if(price>=o.target){fire('target','🎯 '+code+' 到达目标',code+' 现价 $'+fmt(price)+' ≥ 目标 $'+fmt(o.target));}else st.target=false;
  if(o.buyZone&&price<=o.buyZone&&(o.sig==='buy'||o.sig==='strong_buy'||o.sig==='hold')){fire('buy','📥 '+code+' 回踩买点',code+' 现价 $'+fmt(price)+' 已回踩买点 $'+fmt(o.buyZone));}else if(o.buyZone&&price>o.buyZone*1.005)st.buy=false;
  const cost=POS[code];
  if(cost&&price<=cost*0.92){fire('dd','🔻 '+code+' 浮亏超8%',code+' 跌破成本8%（成本 $'+fmt(cost)+' / 现价 $'+fmt(price)+'）');}else if(cost&&price>cost*0.93)st.dd=false;
  _alertState[code]=st;
 });
}

// ---------- 历史查询记录 ----------
const HKEY='stockAnalysisHistory';
function getHistory(){ try{return JSON.parse(localStorage.getItem(HKEY)||'[]');}catch(e){return [];} }
function saveHistory(text){
 text=(text||'').trim(); if(!text)return;
 let h=getHistory().filter(x=>x.toLowerCase()!==text.toLowerCase());
 h.unshift(text); h=h.slice(0,12);
 localStorage.setItem(HKEY,JSON.stringify(h)); renderHistory();
}
function removeHistory(text,ev){ ev.stopPropagation();
 localStorage.setItem(HKEY,JSON.stringify(getHistory().filter(x=>x!==text))); renderHistory();
}
function clearHistory(){ localStorage.removeItem(HKEY); renderHistory(); }
function runHistory(text){ document.getElementById('tickers').value=text; analyze(); }
function renderHistory(){
 const el=document.getElementById('history'); const h=getHistory();
 if(!h.length){ el.innerHTML=''; return; }
 el.innerHTML='<span class="hl">🕘 历史：</span>'
  + h.map(t=>'<span class="chip" onclick="runHistory(\''+t.replace(/'/g,"\\'")+'\')">'+t
    +'<span class="x" onclick="removeHistory(\''+t.replace(/'/g,"\\'")+'\',event)">✕</span></span>').join('')
  + '<button class="histclear" onclick="clearHistory()">清空</button>';
}
renderHistory();

// ---------- 网页内回测 ----------
let btChart=null;
function closeBT(){ document.getElementById('btmask').classList.remove('on'); if(btChart){btChart.destroy();btChart=null;} }
async function openBacktest(code,name,params){
 const mask=document.getElementById('btmask'); mask.classList.add('on');
 window._btCode=code;
 document.getElementById('bttk').textContent=code+(name?' · '+name:'');
 document.getElementById('btbody').innerHTML='<div class="loading">回测计算中…（拉取历史+逐日模拟，约 5–15 秒）</div>';
 const p=params||window._btParams||{days:400,fee_bps:5,stop_atr:2};
 window._btParams=p;
 try{
  const q='/api/backtest?stock='+encodeURIComponent(code)+'&days='+p.days+'&fee_bps='+p.fee_bps+'&stop_atr='+p.stop_atr;
  const m=await (await fetch(q)).json();
  if(m.error){ document.getElementById('btbody').innerHTML='<div class="loading" style="color:var(--red)">'+m.error+'</div>'; return; }
  renderBacktest(m,p);
 }catch(e){ document.getElementById('btbody').innerHTML='<div class="loading" style="color:var(--red)">回测失败：'+e.message+'</div>'; }
}
function rerunBT(){
 const p={days:parseInt(document.getElementById('btDays').value,10)||400,
   fee_bps:parseFloat(document.getElementById('btFee').value)||5,
   stop_atr:parseFloat(document.getElementById('btStop').value)||2};
 openBacktest(window._btCode,'',p);
}
function renderBacktest(m,p){
 const pc=x=>(x*100>=0?'+':'')+(x*100).toFixed(1)+'%';
 const pcn=x=>(x*100).toFixed(1)+'%';
 const win=m.excess_vs_bh>0; const vc=win?'var(--green)':'var(--red)';
 const kv=(k,v,col)=>'<div class="kv"><div class="k">'+k+'</div><div class="v"'+(col?' style="color:'+col+'"':'')+'>'+v+'</div></div>';
 document.getElementById('btbody').innerHTML=
   '<div class="btctrl">'
   +'<div class="f">回测交易日<input id="btDays" type="number" value="'+p.days+'"></div>'
   +'<div class="f">费用(基点/单边)<input id="btFee" type="number" value="'+p.fee_bps+'"></div>'
   +'<div class="f">ATR止损倍数<input id="btStop" type="number" step="0.5" value="'+p.stop_atr+'"></div>'
   +'<button onclick="rerunBT()">重新回测</button>'
   +'<button class="ghost" onclick="openWF(window._btCode)">🔬 走动验证</button></div>'
   +'<div class="bigchart2"><canvas id="btc"></canvas></div>'
   +'<div style="display:flex;gap:14px;font-size:11px;color:var(--sub);margin:-6px 0 12px"><span><span style="color:#2f7ad6">—</span> 策略</span><span><span style="color:#adb5bd">—</span> 买入持有</span></div>'
   +'<div class="stat6">'
   +kv('策略总收益',pc(m.total_return),vc)
   +kv('年化',pc(m.cagr),m.cagr>=0?'var(--green)':'var(--red)')
   +kv('买入持有',pc(m.buy_hold_return))
   +kv('超额',pc(m.excess_vs_bh)+(win?' ✅':' ❌'),vc)
   +kv('夏普比率',m.sharpe)
   +kv('最大回撤',pcn(m.max_drawdown),'var(--red)')
   +kv('交易次数',m.n_trades)
   +kv('胜率 / 盈亏比',pcn(m.win_rate)+' / '+m.profit_factor)
   +'</div>'
   +'<div class="mverdict">'+(win
     ? '<b style="color:var(--green)">✅ 这套规则在该标的上跑赢买入持有。</b>但样本内表现≠未来，建议再做 walk-forward 验证（见路线图）。'
     : '<b style="color:var(--red)">❌ 该标的上规则跑输买入持有。</b>说明此票更适合长持，或规则需针对性优化——这正是回测的价值：用数据说话。')
   +'</div>';
 // 资金曲线
 const labels=(m.dates||[]).map(d=>(d||'').slice(5));
 if(btChart)btChart.destroy();
 btChart=new Chart(document.getElementById('btc'),{type:'line',
  data:{labels,datasets:[
   {label:'策略',data:m.curve,borderColor:'#2f7ad6',backgroundColor:'rgba(47,122,214,.10)',borderWidth:2,pointRadius:0,tension:.15,fill:true},
   {label:'买入持有',data:m.bh_curve,borderColor:'#adb5bd',borderWidth:1.5,borderDash:[5,4],pointRadius:0,tension:.15,fill:false}
  ]},
  options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
   plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label+' '+((c.parsed.y-1)*100).toFixed(1)+'%'}}},
   scales:{x:{ticks:{font:{size:9},maxTicksLimit:6},grid:{display:false}},
           y:{ticks:{font:{size:10},callback:v=>((v-1)*100).toFixed(0)+'%'},grid:{color:'#f0f2f6'}}}}});
}
async function openWF(code,params){
 document.getElementById('btbody').innerHTML='<div class="loading">走动验证中…（多窗口训练+样本外检验，约 15–40 秒）</div>';
 const p=params||window._wfParams||{train:180,test:60,fee_bps:5};
 window._wfParams=p;
 try{
  const q='/api/walkforward?stock='+encodeURIComponent(code)+'&train='+p.train+'&test='+p.test+'&fee_bps='+p.fee_bps;
  const m=await (await fetch(q)).json();
  if(m.error){ document.getElementById('btbody').innerHTML='<div class="loading" style="color:var(--red)">'+m.error+'</div><div style="text-align:center"><button class="ghost" onclick="openBacktest(window._btCode)">← 返回回测</button></div>'; return; }
  renderWF(m,p);
 }catch(e){ document.getElementById('btbody').innerHTML='<div class="loading" style="color:var(--red)">失败：'+e.message+'</div>'; }
}
function rerunWF(){
 const p={train:parseInt(document.getElementById('wfTrain').value,10)||180,
   test:parseInt(document.getElementById('wfTest').value,10)||60,
   fee_bps:parseFloat(document.getElementById('wfFee').value)||5};
 openWF(window._btCode,p);
}
function renderWF(m,p){
 const pc=x=>(x*100>=0?'+':'')+(x*100).toFixed(1)+'%';
 const pcn=x=>(x*100).toFixed(1)+'%';
 const win=m.oos_excess_vs_bh>0; const vc=win?'var(--green)':'var(--red)';
 const overfit=m.overfit_gap>0.15;
 const kv=(k,v,col)=>'<div class="kv"><div class="k">'+k+'</div><div class="v"'+(col?' style="color:'+col+'"':'')+'>'+v+'</div></div>';
 document.getElementById('btbody').innerHTML=
   '<div class="btctrl">'
   +'<div class="f">训练窗口(日)<input id="wfTrain" type="number" value="'+p.train+'"></div>'
   +'<div class="f">测试窗口(日)<input id="wfTest" type="number" value="'+p.test+'"></div>'
   +'<div class="f">费用(基点)<input id="wfFee" type="number" value="'+p.fee_bps+'"></div>'
   +'<button onclick="rerunWF()">重跑</button>'
   +'<button class="ghost" onclick="openBacktest(window._btCode)">← 返回回测</button></div>'
   +'<div style="font-size:12px;color:var(--sub);margin-bottom:8px">🔬 <b>'+m.n_folds+'</b> 段滚动窗口，每段「训练区调参→测试区检验」，下图与指标<b>只统计样本外(OOS)</b>——这才是防过拟合后的真实成色。</div>'
   +'<div class="bigchart2"><canvas id="wfc"></canvas></div>'
   +'<div style="display:flex;gap:14px;font-size:11px;color:var(--sub);margin:-6px 0 12px"><span><span style="color:#7048e8">—</span> 样本外策略</span></div>'
   +'<div class="stat6">'
   +kv('样本外收益',pc(m.oos_total_return),vc)
   +kv('样本外年化',pc(m.oos_cagr),m.oos_cagr>=0?'var(--green)':'var(--red)')
   +kv('同期买入持有',pc(m.oos_buy_hold))
   +kv('超额',pc(m.oos_excess_vs_bh)+(win?' ✅':' ❌'),vc)
   +kv('样本外夏普',m.oos_sharpe)
   +kv('最大回撤',pcn(m.oos_max_drawdown),'var(--red)')
   +kv('窗口段数',m.n_folds)
   +kv('过拟合落差',pc(m.overfit_gap),overfit?'var(--red)':'var(--green)')
   +'</div>'
   +'<div class="mverdict">'+(overfit
     ? '<b style="color:var(--red)">⚠️ 过拟合落差较大</b>：样本内表现明显好于样本外，说明参数在迎合历史噪声，实盘需谨慎。'
     : '<b style="color:var(--green)">✅ 过拟合落差可接受</b>：样本外表现与样本内接近，规则相对稳健。')
   +(win?'　且样本外仍跑赢买入持有。':'　但样本外跑输买入持有，该票或不适合此策略。')+'</div>';
 const labels=(m.dates||[]).map(d=>(d||'').slice(5));
 if(btChart)btChart.destroy();
 btChart=new Chart(document.getElementById('wfc'),{type:'line',
  data:{labels,datasets:[{label:'样本外策略',data:m.curve,borderColor:'#7048e8',backgroundColor:'rgba(112,72,232,.10)',borderWidth:2,pointRadius:0,tension:.15,fill:true}]},
  options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
   plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>'样本外 '+((c.parsed.y-1)*100).toFixed(1)+'%'}}},
   scales:{x:{ticks:{font:{size:9},maxTicksLimit:6},grid:{display:false}},
           y:{ticks:{font:{size:10},callback:v=>((v-1)*100).toFixed(0)+'%'},grid:{color:'#f0f2f6'}}}}});
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeBT();});

// ---------- 当日 K 线详情 ----------
let detailChart=null;
function closeDetail(){ document.getElementById('mask').classList.remove('on'); window._curDetail=null; if(detailChart){detailChart.destroy();detailChart=null;} }
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail();});

async function openDetail(code,name,silent){
 const mask=document.getElementById('mask'); mask.classList.add('on');
 window._curDetail={code,name};
 if(!silent){
  document.getElementById('mtk').textContent=code;
  document.getElementById('mnm').textContent=name;
  document.getElementById('mpx').textContent=''; document.getElementById('mchg').textContent='';
  document.getElementById('mdate').textContent='';
  document.getElementById('mbody').innerHTML='<div class="loading">正在拉取当日分时数据…</div>';
 }
 try{
  const res=await fetch('/api/intraday?stock='+encodeURIComponent(code));
  const d=await res.json();
  if(d.error){ document.getElementById('mbody').innerHTML='<div class="loading" style="color:var(--red)">'+d.error+'</div>'; return; }
  let lv={},ctx={};
  const A=window._ANA;
  if(A){const i=A.stocks.findIndex(x=>x.code===code);
    if(i>=0){const o=A.ops[i],s=A.stocks[i];
      lv={buy:o.buyZone,stop:o.stop,target:o.target,cost:A.pos[code]};
      ctx={weekly:s.weekly,atr:(s.indicators||{}).atr,earnings:s.earnings,news:s.news,score:s.trend_score,sigMeta:o.meta};}}
  renderDetail(d,lv,ctx);
 }catch(e){ document.getElementById('mbody').innerHTML='<div class="loading" style="color:var(--red)">加载失败：'+e.message+'</div>'; }
}

function ctxBlock(ctx){
 if(!ctx)return '';
 const wkMap={bullish:'多头',strong_bullish:'强多头',weak_bullish:'弱多头',bearish:'空头',strong_bearish:'强空头',weak_bearish:'弱空头',consolidation:'震荡',insufficient_data:'数据不足',unknown:'—'};
 const wk=ctx.weekly||{}, atr=ctx.atr||{}, ed=ctx.earnings;
 let chips='';
 if(ctx.sigMeta)chips+='<span class="pill" style="border-color:'+COL[ctx.sigMeta.c][0]+';color:'+COL[ctx.sigMeta.c][0]+'">综合 '+ctx.sigMeta.t+(ctx.score?' · '+ctx.score.total+'分':'')+'</span>';
 if(wk.trend)chips+='<span class="pill">周线 '+(wkMap[wk.trend]||wk.trend)+'</span>';
 if(atr.atr_pct!=null)chips+='<span class="pill">ATR波动 '+fmt(atr.atr_pct,1)+'%</span>';
 if(ed&&ed.days!=null)chips+='<span class="pill" style="border-color:var(--amber);color:var(--amber)">📅 财报 '+(ed.days>=0?ed.days+'天后':'已发布')+'（'+ed.date+'）</span>';
 let news='';
 if(ctx.news&&ctx.news.length){
   news='<div style="margin-top:12px"><div style="font-size:12px;color:var(--sub);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px">📰 最新消息</div>'
     +ctx.news.slice(0,4).map(n=>'<div style="font-size:12.5px;margin-bottom:5px;line-height:1.45">• '+(n.url?'<a href="'+n.url+'" target="_blank" style="color:var(--blue);text-decoration:none">'+n.title+'</a>':n.title)+(n.publisher?' <span style="color:var(--sub)">— '+n.publisher+'</span>':'')+'</div>').join('')+'</div>';
 }
 if(!chips&&!news)return '';
 return '<div style="display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 12px">'+chips+'</div>'+news;
}
function renderDetail(d,lv,ctx){
 lv=lv||{};
 const base=d.prev_close||d.open; const chg=base?((d.last-base)/base*100):0;
 const cc=chg>=0?'var(--green)':'var(--red)';
 document.getElementById('mpx').textContent='$'+fmt(d.last);
 document.getElementById('mchg').style.color=cc;
 document.getElementById('mchg').textContent=pct(chg)+(d.prev_close?'（较昨收）':'（较开盘）');
 document.getElementById('mdate').textContent='📅 '+d.date+' · 日内分时（'+d.interval+'，共 '+d.bars.length+' 根）';
 const rng=d.high-d.low, pos=rng>0?(d.last-d.low)/rng*100:50, amp=d.open?(rng/d.open*100):0;
 const v=intradayVerdict(d,pos,chg,amp);
 const ph=posNote({price:d.last,buyZone:lv.buy,stop:lv.stop,target:lv.target},lv.cost);
 document.getElementById('mbody').innerHTML=
   '<div class="bigchart"><canvas id="bigc"></canvas></div>'
  +'<div style="display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--sub);margin:-6px 0 12px;padding-left:2px">'
  +'<span><span style="color:#1c7ed6">┄</span> VWAP均价</span>'
  +(d.prev_close?'<span><span style="color:#868e96">┄</span> 昨收</span>':'')
  +'<span><span style="color:#f08c00">┄</span> 开盘</span>'
  +'<span><span style="color:rgba(12,166,120,.7)">┄</span> 日内高</span>'
  +'<span><span style="color:rgba(224,49,49,.7)">┄</span> 日内低</span>'
  +(lv.buy?'<span><span style="color:#2f9e44;font-weight:700">—</span> 买点</span>':'')
  +(lv.stop?'<span><span style="color:#e03131;font-weight:700">—</span> 止损</span>':'')
  +(lv.target?'<span><span style="color:#1098ad;font-weight:700">—</span> 目标</span>':'')
  +(lv.cost?'<span><span style="color:#5f3dc4;font-weight:700">—</span> 成本</span>':'')
  +'<span style="color:var(--green)">▮涨</span><span style="color:var(--red)">▮跌</span></div>'
  +'<div class="stat6">'
  +kv('开盘',fmt(d.open))+kv('最高',fmt(d.high))+kv('最低',fmt(d.low))+kv('现价',fmt(d.last))
  +kv('VWAP均价',fmt(d.vwap))+kv('日内振幅',amp.toFixed(2)+'%')+kv('收盘位置',pos.toFixed(0)+'%')+kv('成交量',compact(d.vol))
  +'</div>'
  +stratStrip(lv,d.last)
  +ctxBlock(ctx)
  +'<div class="mverdict"><b>📊 当日K线解读：</b>'+v+(ph?'<br><b>📍 持仓建议：</b>'+ph:'')+'</div>';
 drawCandle(d,lv);
}
function kv(k,val){return '<div class="kv"><div class="k">'+k+'</div><div class="v">'+val+'</div></div>';}
function kvc(k,val,col){return '<div class="kv"><div class="k">'+k+'</div><div class="v" style="color:'+col+'">$'+val+'</div></div>';}
function stratStrip(lv,last){
 if(!lv||(!lv.buy&&!lv.stop&&!lv.target&&!lv.cost))return '';
 const n=lv.cost?4:3;
 return '<div class="stat6" style="grid-template-columns:repeat('+n+',1fr)">'
  +(lv.cost?kvc('持仓成本',fmt(lv.cost),'#5f3dc4'):'')
  +kvc('推荐买点',fmt(lv.buy),'var(--green)')
  +kvc('止损',fmt(lv.stop),'var(--red)')
  +kvc('目标',fmt(lv.target),'#1098ad')
  +'</div>';
}
function compact(n){if(n>=1e9)return (n/1e9).toFixed(2)+'B';if(n>=1e6)return (n/1e6).toFixed(1)+'M';if(n>=1e3)return (n/1e3).toFixed(0)+'K';return n;}

function intradayVerdict(d,pos,chg,amp){
 let s1; if(pos>70)s1='收在<b>日内高位</b>'; else if(pos<30)s1='收在<b>日内低位</b>'; else s1='处于<b>日内中段震荡</b>';
 const vw=d.last>=d.vwap?'，站上均价线(VWAP)，<b style="color:var(--green)">日内多头占优</b>':'，跌破均价线(VWAP)，<b style="color:var(--red)">日内空头占优</b>';
 let s2=d.last>=d.open?'盘中较开盘走强':'盘中较开盘走弱';
 const ampTxt=amp>4?'，振幅偏大（'+amp.toFixed(1)+'%），波动剧烈需控仓':'，振幅 '+amp.toFixed(1)+'%';
 const dir=chg>=0?'红盘':'绿盘';
 return s1+vw+'。'+s2+'，全天'+dir+ampTxt+'。';
}

function drawCandle(d,lv){
 lv=lv||{};
 const bars=d.bars, labels=bars.map(b=>b.t);
 const up='rgba(12,166,120,1)', dn='rgba(224,49,49,1)';
 const colors=bars.map(b=>b.c>=b.o?up:dn);
 const wick=bars.map(b=>[b.l,b.h]);
 const body=bars.map(b=>[Math.min(b.o,b.c),Math.max(b.o,b.c)]);
 const vwap=bars.map(b=>b.vwap);
 // 动态 Y 轴：贴着当日真实价格区间，留少量边距
 let lo=Math.min.apply(null,bars.map(b=>b.l));
 let hi=Math.max.apply(null,bars.map(b=>b.h));
 // 把贴近现价的策略位/昨收纳入 Y 轴（避免画在框外）
 [d.prev_close,lv.buy,lv.stop,lv.cost].forEach(v=>{if(v){lo=Math.min(lo,v);hi=Math.max(hi,v);}});
 // 目标价通常较远：仅当离现价不太远(±12%)时才纳入坐标，否则只在卡片里显示数值
 if(lv.target && lv.target<=hi*1.12 && lv.target>=lo*0.88){lo=Math.min(lo,lv.target);hi=Math.max(hi,lv.target);}
 const pad=(hi-lo)*0.10 || hi*0.01;
 const ymin=lo-pad, ymax=hi+pad;
 // 市场参考线（虚线）
 const flat=(v,col,label,dash)=>({type:'line',label:label,data:labels.map(()=>v),borderColor:col,borderWidth:1.2,borderDash:dash||[3,3],pointRadius:0,fill:false,order:0});
 const refs=[];
 if(d.prev_close) refs.push(flat(d.prev_close,'#868e96','昨收'));
 refs.push(flat(d.open,'#f08c00','开盘'));
 refs.push(flat(d.high,'rgba(12,166,120,.5)','日内高'));
 refs.push(flat(d.low,'rgba(224,49,49,.5)','日内低'));
 // 策略线（实线，更醒目）
 const strat=(v,col,label)=>({type:'line',label:label,data:labels.map(()=>v),borderColor:col,borderWidth:1.6,pointRadius:0,fill:false,order:0});
 if(lv.cost) refs.push(strat(lv.cost,'#5f3dc4','成本'));
 if(lv.buy) refs.push(strat(lv.buy,'#2f9e44','买点'));
 if(lv.stop) refs.push(strat(lv.stop,'#e03131','止损'));
 if(lv.target && lv.target<=ymax && lv.target>=ymin) refs.push(strat(lv.target,'#1098ad','目标'));
 if(detailChart)detailChart.destroy();
 detailChart=new Chart(document.getElementById('bigc'),{
  data:{labels,datasets:[
   {type:'bar',label:'wick',data:wick,backgroundColor:colors,barThickness:1.5,grouped:false,order:3},
   {type:'bar',label:'body',data:body,backgroundColor:colors,borderWidth:0,barThickness:'flex',maxBarThickness:9,grouped:false,order:2},
   {type:'line',label:'VWAP',data:vwap,borderColor:'#1c7ed6',borderWidth:1.8,borderDash:[5,4],pointRadius:0,tension:.15,order:1},
   ...refs
  ]},
  options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
   plugins:{legend:{display:false},tooltip:{filter:item=>['body','VWAP'].includes(item.dataset.label),callbacks:{
     title:items=>items[0].label,
     label:function(c){const i=c.dataIndex;const b=bars[i];
       if(c.dataset.label==='VWAP')return 'VWAP $'+fmt(b.vwap);
       if(c.dataset.label==='body')return ['开 $'+fmt(b.o)+'  收 $'+fmt(b.c),'高 $'+fmt(b.h)+'  低 $'+fmt(b.l)];
       return null;}}}},
   scales:{x:{ticks:{font:{size:9},maxTicksLimit:8},grid:{display:false}},
           y:{position:'right',beginAtZero:false,min:ymin,max:ymax,
              ticks:{font:{size:10},callback:v=>'$'+fmt(v)},grid:{color:'#f0f2f6'}}}}});
}
</script></body></html>"""

def fetch_intraday(code, interval="5m"):
    """拉取最近一个交易日的日内分时数据（蜡烛 + 累计VWAP）。"""
    import yfinance as yf
    t = yf.Ticker(code)
    df = t.history(period="5d", interval=interval)
    if df is None or df.empty:
        return {"error": "无日内数据（可能非交易时段、周末或代码错误）"}
    df = df.reset_index()
    dtcol = "Datetime" if "Datetime" in df.columns else df.columns[0]
    df["d"] = df[dtcol].astype(str).str[:10]
    last_day = df["d"].iloc[-1]
    day = df[df["d"] == last_day]
    bars = []
    cum_pv = 0.0; cum_v = 0.0
    for _, r in day.iterrows():
        try:
            o = float(r["Open"]); h = float(r["High"]); l = float(r["Low"]); c = float(r["Close"]); v = float(r["Volume"])
        except Exception:
            continue
        if any(x != x for x in (o, h, l, c)):  # skip NaN rows
            continue
        tp = (h + l + c) / 3.0; cum_pv += tp * v; cum_v += v
        w = cum_pv / cum_v if cum_v > 0 else c
        ts = str(r[dtcol])
        bars.append({"t": ts[11:16], "o": round(o, 2), "h": round(h, 2), "l": round(l, 2),
                     "c": round(c, 2), "v": int(v), "vwap": round(w, 2)})
    if not bars:
        return {"error": "日内数据为空"}
    prev = None
    try:
        fi = t.fast_info
        prev = float(fi["previousClose"]) if "previousClose" in fi else None
    except Exception:
        prev = None
    return {"code": code, "date": last_day, "interval": interval, "bars": bars,
            "open": bars[0]["o"], "high": max(b["h"] for b in bars), "low": min(b["l"] for b in bars),
            "last": bars[-1]["c"], "prev_close": prev, "vwap": bars[-1]["vwap"],
            "vol": sum(b["v"] for b in bars)}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if parsed.path == "/api/analyze":
            qs = urllib.parse.parse_qs(parsed.query)
            stocks = qs.get("stocks", [""])[0]
            if not stocks:
                return self._send(200, json.dumps({"fatal": "未提供股票代码"}))
            if not os.path.exists(FETCHER):
                return self._send(200, json.dumps({"fatal": "找不到 stock_data_fetcher.py（需与本文件同目录）"}))
            codes = [c.strip().upper() for c in stocks.split(",") if c.strip()]
            # 优先走进程内引擎（快 + 带缓存/并发）；导入失败才回退子进程
            if FETCHER_MOD is not None:
                try:
                    return self._send(200, json.dumps(analyze_codes(codes), ensure_ascii=False))
                except Exception as e:
                    return self._send(200, json.dumps({"fatal": str(e)}))
            try:
                out = subprocess.run([sys.executable, FETCHER, "--stocks", stocks, "--days", "120", "--extras"],
                                     capture_output=True, text=True, timeout=120)
                if out.returncode != 0 and not out.stdout.strip():
                    return self._send(200, json.dumps({"fatal": (out.stderr or "脚本执行失败")[-400:]}))
                return self._send(200, out.stdout)
            except subprocess.TimeoutExpired:
                return self._send(200, json.dumps({"fatal": "脚本执行超时（120s）"}))
            except Exception as e:
                return self._send(200, json.dumps({"fatal": str(e)}))
        if parsed.path == "/api/intraday":
            qs = urllib.parse.parse_qs(parsed.query)
            code = qs.get("stock", [""])[0].strip().upper()
            if not code:
                return self._send(200, json.dumps({"error": "未提供股票代码"}))
            try:
                return self._send(200, json.dumps(fetch_intraday(code), ensure_ascii=False))
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}))
        if parsed.path == "/api/backtest":
            qs = urllib.parse.parse_qs(parsed.query)
            code = qs.get("stock", [""])[0].strip().upper()
            if not code or BT_MOD is None or FETCHER_MOD is None:
                return self._send(200, json.dumps({"error": "回测引擎不可用或未提供代码"}))
            try:
                days = int(qs.get("days", ["400"])[0])
                fee = float(qs.get("fee_bps", ["5"])[0])
                stop = float(qs.get("stop_atr", ["2"])[0])
                ohlcv = _fetch_ohlcv(code, max(days, 120))
                m = BT_MOD.run_backtest(ohlcv, fee_bps=fee, stop_atr=stop, with_curve=True)
                m["code"] = code
                return self._send(200, json.dumps(m, ensure_ascii=False))
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}))
        if parsed.path == "/api/walkforward":
            qs = urllib.parse.parse_qs(parsed.query)
            code = qs.get("stock", [""])[0].strip().upper()
            if not code or BT_MOD is None or FETCHER_MOD is None:
                return self._send(200, json.dumps({"error": "回测引擎不可用或未提供代码"}))
            try:
                train = int(qs.get("train", ["180"])[0])
                test = int(qs.get("test", ["60"])[0])
                fee = float(qs.get("fee_bps", ["5"])[0])
                need = 60 + train + test + 10
                ohlcv = _fetch_ohlcv(code, need)
                m = BT_MOD.walk_forward(ohlcv, train=train, test=test, fee_bps=fee, with_curve=True)
                m["code"] = code
                return self._send(200, json.dumps(m, ensure_ascii=False))
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}))
        self._send(404, json.dumps({"fatal": "not found"}))

def main():
    if not os.path.exists(FETCHER):
        print("⚠️  警告：同目录下没找到 stock_data_fetcher.py，分析会失败。请把两个文件放一起。")
    url = f"http://localhost:{PORT}"
    print(f"✅ 美股分析 App 已启动：{url}")
    print("   在页面输入股票代码点「分析」即可。按 Ctrl+C 停止。")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        try: httpd.serve_forever()
        except KeyboardInterrupt: print("\n已停止。")

if __name__ == "__main__":
    main()
