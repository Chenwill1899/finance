#!/usr/bin/env python3
"""
US Stock Data Fetcher + Technical Indicator Calculator
Outputs structured JSON for Claude Code analysis.
No AI/LLM calls -- pure data + math.

Primary data source:
  US:      yfinance

Legacy non-US fetchers are still present internally, but the local web app is US-only.

Legacy data source priority (graceful degradation):
  A-share: Tushare Pro (if TUSHARE_TOKEN set) > efinance > akshare > yfinance
  HK:      efinance > akshare > yfinance

News search priority (via --news flag):
  Tavily (if TAVILY_API_KEY set) > SerpAPI (if SERPAPI_KEY set) > skip (use WebSearch in Claude)

Usage:
    python3 stock_data_fetcher.py --stocks "NVDA,RDW,RKLB" [--days 120] [--extras]

Environment variables (optional, for enhanced data):
    TUSHARE_TOKEN    - Tushare Pro token (free signup at tushare.pro)
    TAVILY_API_KEY   - Tavily API key (1000 free calls/month)
    SERPAPI_KEY       - SerpAPI key (100 free calls/month)
"""

import os
import sys
import json
import argparse
import warnings
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

warnings.filterwarnings("ignore")

# Data source availability detection
_AVAILABLE_SOURCES = {}
_BENCHMARK_CACHE = {}

def _check_source(name):
    """Lazy-check if a data source library is importable."""
    if name not in _AVAILABLE_SOURCES:
        try:
            __import__(name)
            _AVAILABLE_SOURCES[name] = True
        except ImportError:
            _AVAILABLE_SOURCES[name] = False
    return _AVAILABLE_SOURCES[name]

def _log(msg):
    """Log to stderr so it doesn't pollute JSON stdout."""
    print(f"[INFO] {msg}", file=sys.stderr)


# ============================================================
# SECTION 1: Stock Code Parser
# ============================================================

def classify_stock(code: str) -> tuple:
    """
    Returns (market, normalized_code, display_code)
    market: 'cn_a', 'cn_hk', 'us'
    """
    code = code.strip()
    upper = code.upper()

    # 港股: HK00700 -> ('cn_hk', '00700', 'HK00700')
    if upper.startswith("HK") and upper[2:].isdigit():
        return ("cn_hk", upper[2:], upper)

    # A股: 600519 -> ('cn_a', '600519', '600519')
    if upper.isdigit() and len(upper) == 6:
        return ("cn_a", upper, upper)

    # 美股: TSLA -> ('us', 'TSLA', 'TSLA')
    if upper.isalpha() and 1 <= len(upper) <= 5:
        return ("us", upper, upper)

    # 带后缀的A股: 600519.SH -> strip
    if "." in upper:
        base, suffix = upper.rsplit(".", 1)
        if suffix in ("SH", "SZ", "SS") and base.isdigit():
            return ("cn_a", base, base)

    # 带前缀的A股: SH600519 -> strip
    if upper[:2] in ("SH", "SZ") and upper[2:].isdigit():
        return ("cn_a", upper[2:], upper[2:])

    return ("unknown", code, code)


def to_yfinance_code(code: str, market: str) -> str:
    """Convert to Yahoo Finance ticker format."""
    if market == "cn_hk":
        num = code.lstrip("0") or "0"
        return f"{num.zfill(4)}.HK"
    if market == "us":
        return code
    # A股
    if code.startswith(("600", "601", "603", "688")):
        return f"{code}.SS"
    if code.startswith(("51", "52", "56", "58")):
        return f"{code}.SS"
    return f"{code}.SZ"


# ============================================================
# SECTION 2: Data Fetchers (with graceful degradation)
# ============================================================

def _df_to_ohlcv(df, days):
    """Convert a normalized DataFrame to OHLCV list."""
    import pandas as pd
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").tail(days).reset_index(drop=True)
    ohlcv = []
    for _, row in df.iterrows():
        ohlcv.append({
            "date": str(row.get("date", "")),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("volume")),
            "amount": _safe_float(row.get("amount")),
            "pct_chg": _safe_float(row.get("pct_chg")),
        })
    return ohlcv


# --- Tushare Pro (Priority 0, needs TUSHARE_TOKEN) ---

def _fetch_tushare_a(code: str, days: int):
    """Fetch A-share via Tushare Pro. Returns (ohlcv, source) or raises."""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN not set")
    import tushare as ts
    pro = ts.pro_api(token)
    ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "688")) else f"{code}.SZ"
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise ValueError(f"Tushare returned no data for {code}")
    col_map = {
        "trade_date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "vol": "volume",
        "amount": "amount", "pct_chg": "pct_chg",
    }
    df = df.rename(columns=col_map)
    df["date"] = df["date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else x)
    _log(f"[{code}] Using Tushare Pro (premium)")
    return _df_to_ohlcv(df, days), "tushare"


# --- efinance (Priority 1, free) ---

def _fetch_efinance_a(code: str, days: int):
    """Fetch A-share via efinance (EastMoney). Returns (ohlcv, source) or raises."""
    import efinance as ef
    df = ef.stock.get_quote_history(code)
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"


def _fetch_efinance_hk(code: str, days: int):
    """Fetch HK stock via efinance."""
    import efinance as ef
    df = ef.stock.get_quote_history(code, stock_type="hk")
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for HK{code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"


# --- akshare (Priority 2, free) ---

def _fetch_akshare_a(code: str, days: int):
    """Fetch A-share via akshare."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"


def _fetch_akshare_hk(code: str, days: int):
    """Fetch HK stock via akshare."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = ak.stock_hk_hist(symbol=code, period="daily",
                              start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_hk_hist(symbol=code, period="daily",
                              start_date=start_date, end_date=end_date, adjust="")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for HK{code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"


# --- yfinance (Priority 3, free, fallback for all markets) ---

def _fetch_yfinance(code: str, market: str, days: int):
    """Fetch any stock via yfinance (universal fallback)."""
    import yfinance as yf
    yf_code = to_yfinance_code(code, market)
    ticker = yf.Ticker(yf_code)
    hist = ticker.history(period=f"{days}d")
    if hist is None or hist.empty:
        raise ValueError(f"yfinance returned no data for {yf_code}")
    ohlcv = []
    for idx, row in hist.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        ohlcv.append({
            "date": date_str,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "volume": _safe_float(row.get("Volume")),
            "amount": None, "pct_chg": None,
        })
    for i in range(1, len(ohlcv)):
        prev = ohlcv[i - 1]["close"]
        if prev and prev > 0:
            ohlcv[i]["pct_chg"] = round((ohlcv[i]["close"] - prev) / prev * 100, 2)
    _log(f"[{code}] Using yfinance (free, fallback)")
    return ohlcv, "yfinance"


# --- Realtime quote fetchers ---

def _fetch_realtime_a(code: str) -> dict:
    """Fetch A-share realtime quote with fallback."""
    # Try akshare spot (most reliable for realtime)
    if _check_source("akshare"):
        try:
            import akshare as ak
            spot_df = ak.stock_zh_a_spot_em()
            row = spot_df[spot_df["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                return {
                    "name": str(r.get("名称", code)),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                    "change_amount": _safe_float(r.get("涨跌额")),
                    "volume": _safe_float(r.get("成交量")),
                    "amount": _safe_float(r.get("成交额")),
                    "amplitude": _safe_float(r.get("振幅")),
                    "turnover_rate": _safe_float(r.get("换手率")),
                    "pe_ratio": _safe_float(r.get("市盈率-动态")),
                    "pb_ratio": _safe_float(r.get("市净率")),
                    "total_mv": _safe_float(r.get("总市值")),
                    "circ_mv": _safe_float(r.get("流通市值")),
                    "high": _safe_float(r.get("最高")),
                    "low": _safe_float(r.get("最低")),
                    "open": _safe_float(r.get("今开")),
                    "pre_close": _safe_float(r.get("昨收")),
                    "volume_ratio": _safe_float(r.get("量比")),
                }
        except Exception:
            pass
    # Try efinance
    if _check_source("efinance"):
        try:
            import efinance as ef
            qt = ef.stock.get_realtime_quotes([code])
            if qt is not None and not qt.empty:
                r = qt.iloc[0]
                return {
                    "name": str(r.get("股票名称", code)),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                }
        except Exception:
            pass
    return {}


def _fetch_realtime_hk(code: str) -> dict:
    """Fetch HK realtime quote."""
    if _check_source("akshare"):
        try:
            import akshare as ak
            spot_df = ak.stock_hk_spot_em()
            matched = spot_df[spot_df["代码"] == code]
            if not matched.empty:
                r = matched.iloc[0]
                return {
                    "name": str(r.get("名称", f"HK{code}")),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                    "volume": _safe_float(r.get("成交量")),
                    "pe_ratio": _safe_float(r.get("市盈率")),
                    "pb_ratio": _safe_float(r.get("市净率")),
                    "total_mv": _safe_float(r.get("总市值")),
                }
        except Exception:
            pass
    return {}


def _fetch_realtime_us(code: str) -> dict:
    """Fetch US realtime quote via yfinance."""
    try:
        import yfinance as yf
        info = yf.Ticker(code).info
        return {
            "name": info.get("shortName") or info.get("longName") or code,
            "price": _safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
            "change_pct": _safe_float(info.get("regularMarketChangePercent")),
            "volume": _safe_float(info.get("regularMarketVolume")),
            "pe_ratio": _safe_float(info.get("trailingPE")),
            "pb_ratio": _safe_float(info.get("priceToBook")),
            "total_mv": _safe_float(info.get("marketCap")),
            "high": _safe_float(info.get("dayHigh")),
            "low": _safe_float(info.get("dayLow")),
            "open": _safe_float(info.get("regularMarketOpen")),
            "pre_close": _safe_float(info.get("regularMarketPreviousClose")),
            "week_52_high": _safe_float(info.get("fiftyTwoWeekHigh")),
            "week_52_low": _safe_float(info.get("fiftyTwoWeekLow")),
            "avg_volume": _safe_float(info.get("averageVolume")),
            "dividend_yield": _safe_float(info.get("dividendYield")),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
        }
    except Exception:
        return {}


# --- Priority router ---

def fetch_cn_a(code: str, days: int) -> dict:
    """Fetch A-share with priority: Tushare > efinance > akshare > yfinance."""
    ohlcv = None
    source = "unknown"
    errors = []

    # Priority 0: Tushare Pro (if token configured)
    if os.environ.get("TUSHARE_TOKEN") and _check_source("tushare"):
        try:
            ohlcv, source = _fetch_tushare_a(code, days)
        except Exception as e:
            errors.append(f"tushare: {e}")

    # Priority 1: efinance
    if ohlcv is None and _check_source("efinance"):
        try:
            ohlcv, source = _fetch_efinance_a(code, days)
        except Exception as e:
            errors.append(f"efinance: {e}")

    # Priority 2: akshare
    if ohlcv is None and _check_source("akshare"):
        try:
            ohlcv, source = _fetch_akshare_a(code, days)
        except Exception as e:
            errors.append(f"akshare: {e}")

    # Priority 3: yfinance (universal fallback)
    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "cn_a", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for A-share {code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_a(code)
    name = realtime.get("name", code)
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source}


def fetch_hk(code: str, days: int) -> dict:
    """Fetch HK stock with priority: efinance > akshare > yfinance."""
    ohlcv = None
    source = "unknown"
    errors = []

    if _check_source("efinance"):
        try:
            ohlcv, source = _fetch_efinance_hk(code, days)
        except Exception as e:
            errors.append(f"efinance: {e}")

    if ohlcv is None and _check_source("akshare"):
        try:
            ohlcv, source = _fetch_akshare_hk(code, days)
        except Exception as e:
            errors.append(f"akshare: {e}")

    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "cn_hk", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for HK{code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_hk(code)
    name = realtime.get("name", f"HK{code}")
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source}


def fetch_us(code: str, days: int) -> dict:
    """Fetch US stock via yfinance (primary source for US)."""
    ohlcv, source = _fetch_yfinance(code, "us", days)
    realtime = _fetch_realtime_us(code)
    if not realtime and ohlcv:
        last = ohlcv[-1]
        realtime = {"name": code, "price": last["close"], "change_pct": last.get("pct_chg")}
    name = realtime.get("name", code)
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source}


# ============================================================
# SECTION 2.5: News Search (optional, with graceful degradation)
# ============================================================

NEWS_POSITIVE_KEYWORDS = (
    "beat", "beats", "contract", "wins", "won", "award", "awarded",
    "guidance raised", "raises guidance", "raised guidance", "upgrade",
    "upgraded", "partnership", "launch", "record revenue", "profit",
    "profitable", "buy rating", "outperform", "price target raised",
)
NEWS_NEGATIVE_KEYWORDS = (
    "offering", "dilution", "dilutive", "downgrade", "downgraded",
    "miss", "misses", "guidance cut", "cuts guidance", "lawsuit",
    "investigation", "probe", "sec", "short seller", "bankruptcy",
    "delisting", "delist", "recall", "layoffs", "loss widens",
)
NEWS_RISK_KEYWORDS = (
    "earnings", "results", "fda", "trial", "approval",
    "merger", "acquisition", "offering", "bankruptcy", "sec",
    "short seller", "analyst", "volatility", "lawsuit", "investigation",
)


def build_google_news_rss_url(code: str, company_name: str = "") -> str:
    """Build a Google News RSS query URL for a US ticker."""
    terms = [code.strip().upper(), "stock"]
    if company_name and company_name.upper() != code.strip().upper():
        terms.insert(1, company_name.strip())
    query = " ".join(t for t in terms if t)
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def parse_google_news_rss(xml_text: str, max_results: int = 5) -> list:
    """Parse Google News RSS XML into the app's news item shape."""
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall(".//item")[:max_results]:
        title = unescape((item.findtext("title") or "").strip())
        link = unescape((item.findtext("link") or "").strip())
        publisher = unescape((item.findtext("source") or "").strip())
        pub_raw = (item.findtext("pubDate") or "").strip()
        published_at = ""
        if pub_raw:
            try:
                published_at = parsedate_to_datetime(pub_raw).isoformat()
            except Exception:
                published_at = pub_raw
        if title:
            items.append({
                "title": title,
                "url": link,
                "publisher": publisher,
                "published_at": published_at,
                "source": "google_news",
            })
    return items


def _has_any_keyword(text: str, keywords: tuple) -> bool:
    text_l = text.lower()
    return any(k in text_l for k in keywords)


def analyze_news_sentiment(news_items: list) -> dict:
    """Lightweight title-based news interpretation for trading context."""
    if not news_items:
        return {
            "tone": "none",
            "positive_count": 0,
            "negative_count": 0,
            "risk_count": 0,
            "summary_cn": "暂无可用新闻。",
            "positive_hits": [],
            "negative_hits": [],
            "risk_hits": [],
        }

    positive_hits, negative_hits, risk_hits = [], [], []
    for item in news_items:
        title = str(item.get("title") or "")
        content = str(item.get("content") or "")
        text = f"{title} {content}"
        if _has_any_keyword(text, NEWS_POSITIVE_KEYWORDS):
            positive_hits.append(title)
        if _has_any_keyword(text, NEWS_NEGATIVE_KEYWORDS):
            negative_hits.append(title)
        if _has_any_keyword(text, NEWS_RISK_KEYWORDS):
            risk_hits.append(title)

    pos, neg, risk = len(positive_hits), len(negative_hits), len(risk_hits)
    if pos and neg:
        tone = "mixed"
        summary = "消息面多空交织，技术信号需要等价格确认。"
    elif neg:
        tone = "negative"
        summary = "消息面偏负面，买入信号应降级为谨慎观察。"
    elif pos:
        tone = "positive"
        summary = "消息面偏正面，可作为技术信号的辅助确认。"
    else:
        tone = "neutral"
        summary = "新闻未识别出明显利好或利空。"
    if risk:
        summary += f" 近期有 {risk} 条高波动相关新闻，建议控制仓位。"

    return {
        "tone": tone,
        "positive_count": pos,
        "negative_count": neg,
        "risk_count": risk,
        "summary_cn": summary,
        "positive_hits": positive_hits[:3],
        "negative_hits": negative_hits[:3],
        "risk_hits": risk_hits[:3],
    }


def fetch_google_news(code: str, company_name: str = "", max_results: int = 5) -> list:
    """Fetch recent ticker news from Google News RSS without an API key."""
    url = build_google_news_rss_url(code, company_name)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=8) as resp:
        xml_text = resp.read().decode("utf-8", errors="replace")
    return parse_google_news_rss(xml_text, max_results=max_results)


def search_news(stock_name: str, code: str, max_results: int = 5) -> list:
    """
    Search news with priority: Tavily > SerpAPI > empty (let Claude WebSearch).
    Returns list of {"title": ..., "content": ..., "url": ..., "date": ...}
    """
    # Priority 0: Tavily
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=tavily_key)
            query = f"{stock_name} {code} stock news"
            resp = client.search(query=query, max_results=max_results, search_depth="basic")
            results = []
            for r in resp.get("results", [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "content": r.get("content", "")[:200],
                    "url": r.get("url", ""),
                    "source": "tavily",
                })
            if results:
                _log(f"[{code}] News via Tavily ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] Tavily failed: {e}")

    # Priority 1: SerpAPI
    serpapi_key = os.environ.get("SERPAPI_KEY")
    if serpapi_key:
        try:
            from serpapi import GoogleSearch
            params = {
                "q": f"{stock_name} stock news",
                "api_key": serpapi_key,
                "num": max_results,
            }
            search = GoogleSearch(params)
            data = search.get_dict()
            results = []
            for r in data.get("organic_results", [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "content": r.get("snippet", "")[:200],
                    "url": r.get("link", ""),
                    "source": "serpapi",
                })
            if results:
                _log(f"[{code}] News via SerpAPI ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] SerpAPI failed: {e}")

    # No API keys configured — return empty, let Claude use WebSearch
    _log(f"[{code}] No news API configured, skipping (Claude will use WebSearch)")
    return []


# ============================================================
# SECTION 3: Technical Indicator Calculations
# ============================================================

def _safe_float(val) -> float:
    """Safely convert to float."""
    if val is None:
        return None
    try:
        import math
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def calc_ema(data: list, period: int) -> list:
    """Calculate Exponential Moving Average."""
    if not data or len(data) < period:
        return [None] * len(data)
    result = [None] * (period - 1)
    multiplier = 2.0 / (period + 1)
    # First EMA = SMA of first 'period' values
    sma = sum(data[:period]) / period
    result.append(sma)
    for i in range(period, len(data)):
        ema = (data[i] - result[-1]) * multiplier + result[-1]
        result.append(ema)
    return result


def calc_ma(closes: list, periods: list) -> dict:
    """Calculate Simple Moving Averages."""
    result = {}
    for p in periods:
        key = f"MA{p}"
        if len(closes) >= p:
            ma_val = sum(closes[-p:]) / p
            result[key] = round(ma_val, 4)
        else:
            result[key] = None

    # MA alignment status
    ma5 = result.get("MA5")
    ma10 = result.get("MA10")
    ma20 = result.get("MA20")

    if all(v is not None for v in [ma5, ma10, ma20]):
        if ma5 > ma10 > ma20:
            result["alignment"] = "bullish"
            spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            result["alignment_detail"] = "strong_bullish" if spread > 5 else "bullish"
        elif ma5 < ma10 < ma20:
            result["alignment"] = "bearish"
            spread = (ma20 - ma5) / ma20 * 100 if ma20 > 0 else 0
            result["alignment_detail"] = "strong_bearish" if spread > 5 else "bearish"
        elif ma5 > ma10 and ma10 <= ma20:
            result["alignment"] = "weak_bullish"
            result["alignment_detail"] = "weak_bullish"
        elif ma5 < ma10 and ma10 >= ma20:
            result["alignment"] = "weak_bearish"
            result["alignment_detail"] = "weak_bearish"
        else:
            result["alignment"] = "consolidation"
            result["alignment_detail"] = "consolidation"
    else:
        result["alignment"] = "insufficient_data"
        result["alignment_detail"] = "insufficient_data"

    return result


def calc_macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Calculate MACD: DIF, DEA, Histogram, and cross signals."""
    if len(closes) < slow + signal:
        return {"DIF": None, "DEA": None, "hist": None, "signal": "insufficient_data"}

    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)

    dif_list = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif_list.append(ema_fast[i] - ema_slow[i])
        else:
            dif_list.append(None)

    # DEA = EMA of DIF
    valid_dif = [d for d in dif_list if d is not None]
    if len(valid_dif) < signal:
        return {"DIF": None, "DEA": None, "hist": None, "signal": "insufficient_data"}

    dea_list = calc_ema(valid_dif, signal)

    # Current values
    curr_dif = valid_dif[-1] if valid_dif else None
    curr_dea = dea_list[-1] if dea_list else None
    prev_dif = valid_dif[-2] if len(valid_dif) >= 2 else None
    prev_dea = dea_list[-2] if len(dea_list) >= 2 else None

    hist = round((curr_dif - curr_dea) * 2, 4) if curr_dif is not None and curr_dea is not None else None

    # Cross signal detection
    macd_signal = "neutral"
    if all(v is not None for v in [curr_dif, curr_dea, prev_dif, prev_dea]):
        curr_diff = curr_dif - curr_dea
        prev_diff = prev_dif - prev_dea

        if prev_diff <= 0 and curr_diff > 0:
            macd_signal = "golden_cross_above_zero" if curr_dif > 0 else "golden_cross"
        elif prev_diff >= 0 and curr_diff < 0:
            macd_signal = "death_cross"
        elif curr_dif > 0 and curr_dea > 0:
            macd_signal = "bullish"
        elif curr_dif < 0 and curr_dea < 0:
            macd_signal = "bearish"

        # Zero axis cross
        if prev_dif is not None and curr_dif is not None:
            if prev_dif < 0 and curr_dif >= 0:
                macd_signal = "crossing_above_zero"
            elif prev_dif > 0 and curr_dif <= 0:
                macd_signal = "crossing_below_zero"

    return {
        "DIF": round(curr_dif, 4) if curr_dif is not None else None,
        "DEA": round(curr_dea, 4) if curr_dea is not None else None,
        "hist": hist,
        "signal": macd_signal,
    }


def calc_rsi(closes: list, periods: list) -> dict:
    """Calculate RSI using Wilder's method."""
    result = {}
    for period in periods:
        key = f"RSI{period}"
        if len(closes) < period + 1:
            result[key] = None
            continue

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(0, d) for d in deltas]
        losses = [max(0, -d) for d in deltas]

        # First average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Smoothed averages (Wilder's method)
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        result[key] = round(rsi, 2)

    # RSI zone
    rsi12 = result.get("RSI12")
    if rsi12 is not None:
        if rsi12 >= 80:
            result["zone"] = "overbought"
        elif rsi12 >= 60:
            result["zone"] = "strong"
        elif rsi12 >= 40:
            result["zone"] = "neutral"
        elif rsi12 >= 20:
            result["zone"] = "weak"
        else:
            result["zone"] = "oversold"
    else:
        result["zone"] = "unknown"

    return result


def calc_volume_analysis(volumes: list, closes: list) -> dict:
    """Analyze volume patterns."""
    if len(volumes) < 6 or len(closes) < 2:
        return {"vol_ratio": None, "trend": "insufficient_data"}

    # 5-day average volume (excluding today)
    avg_vol_5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    curr_vol = volumes[-1]

    vol_ratio = round(curr_vol / avg_vol_5, 2) if avg_vol_5 > 0 else None

    # Price change direction
    price_up = closes[-1] >= closes[-2]

    # Volume trend classification
    if vol_ratio is None:
        trend = "unknown"
    elif vol_ratio >= 1.5 and price_up:
        trend = "heavy_volume_up"
    elif vol_ratio >= 1.5 and not price_up:
        trend = "heavy_volume_down"
    elif vol_ratio <= 0.7 and not price_up:
        trend = "shrink_pullback"
    elif vol_ratio <= 0.7 and price_up:
        trend = "shrink_up"
    else:
        trend = "normal"

    return {"vol_ratio": vol_ratio, "trend": trend}


def calc_bias(closes: list, ma_data: dict) -> dict:
    """Calculate bias ratio (乖离率)."""
    if not closes:
        return {}
    curr = closes[-1]
    result = {}
    for key in ["MA5", "MA10", "MA20"]:
        ma_val = ma_data.get(key)
        if ma_val and ma_val > 0:
            bias = round((curr - ma_val) / ma_val * 100, 2)
            result[f"bias_{key.lower()}"] = bias
    return result


def calc_support(closes: list, ma_data: dict) -> dict:
    """Check if price is supported by MA lines."""
    if not closes:
        return {"support_ma5": False, "support_ma10": False}
    curr = closes[-1]
    ma5 = ma_data.get("MA5")
    ma10 = ma_data.get("MA10")

    support_ma5 = False
    support_ma10 = False

    if ma5 and curr > 0:
        # Price within 1% of MA5
        support_ma5 = abs(curr - ma5) / curr * 100 <= 1.0
    if ma10 and curr > 0:
        support_ma10 = abs(curr - ma10) / curr * 100 <= 1.5

    return {"support_ma5": support_ma5, "support_ma10": support_ma10}


def calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> dict:
    """Average True Range（波动率），用于设定基于波动的止损。"""
    n = len(closes)
    if n < period + 1:
        return {"atr": None, "atr_pct": None}
    trs = []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        if None in (h, l, pc):
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return {"atr": None, "atr_pct": None}
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period  # Wilder smoothing
    last = closes[-1]
    return {"atr": round(atr, 4), "atr_pct": round(atr / last * 100, 2) if last else None}


def calc_weekly(ohlcv: list) -> dict:
    """把日线聚合成周线，给出周线均线多空（多周期共振用）。"""
    import datetime as _dt
    weeks = {}
    order = []
    for b in ohlcv:
        d = b.get("date")
        if not d:
            continue
        try:
            y, w, _ = _dt.date.fromisoformat(str(d)[:10]).isocalendar()
        except Exception:
            continue
        key = (y, w)
        if key not in weeks:
            weeks[key] = {"high": b.get("high"), "low": b.get("low"), "close": b.get("close")}
            order.append(key)
        else:
            wk = weeks[key]
            if b.get("high") is not None and (wk["high"] is None or b["high"] > wk["high"]):
                wk["high"] = b["high"]
            if b.get("low") is not None and (wk["low"] is None or b["low"] < wk["low"]):
                wk["low"] = b["low"]
            wk["close"] = b.get("close")
    closes = [weeks[k]["close"] for k in order if weeks[k]["close"] is not None]
    if len(closes) < 10:
        return {"trend": "insufficient_data", "weeks": len(closes)}
    ma = calc_ma(closes, [5, 10, 20])
    return {"trend": ma.get("alignment", "unknown"),
            "ma": {k: ma.get(k) for k in ("MA5", "MA10", "MA20")}, "weeks": len(closes)}


def _return_pct(ohlcv: list, period: int):
    closes = [b.get("close") for b in ohlcv if b.get("close") is not None]
    if len(closes) < period + 1:
        return None
    start = closes[-period - 1]
    end = closes[-1]
    if not start:
        return None
    return round((end - start) / start * 100, 2)


def _relative_bucket(value, threshold=2.0):
    if value is None:
        return "unknown"
    if value >= threshold:
        return "outperform"
    if value <= -threshold:
        return "underperform"
    return "neutral"


def calc_relative_strength(stock_ohlcv: list, benchmarks: dict, periods=(5, 20, 60)) -> dict:
    """Compare a stock's returns with SPY/QQQ over multiple lookback windows."""
    out = {"periods": {}, "summary": {
        "market": "unknown",
        "tech": "unknown",
        "label_cn": "相对强弱数据不足",
    }}
    for period in periods:
        key = f"{period}d"
        stock_ret = _return_pct(stock_ohlcv, period)
        spy_ret = _return_pct(benchmarks.get("SPY", []), period)
        qqq_ret = _return_pct(benchmarks.get("QQQ", []), period)
        vs_spy = round(stock_ret - spy_ret, 2) if stock_ret is not None and spy_ret is not None else None
        vs_qqq = round(stock_ret - qqq_ret, 2) if stock_ret is not None and qqq_ret is not None else None
        out["periods"][key] = {
            "stock_return": stock_ret,
            "spy_return": spy_ret,
            "qqq_return": qqq_ret,
            "vs_spy": vs_spy,
            "vs_qqq": vs_qqq,
        }

    preferred = {}
    for key in ("20d", "5d", "60d"):
        row = out["periods"].get(key) or {}
        if row.get("vs_spy") is not None or row.get("vs_qqq") is not None:
            preferred = row
            break
    market = _relative_bucket(preferred.get("vs_spy"))
    tech = _relative_bucket(preferred.get("vs_qqq"))
    label_map = {
        ("outperform", "outperform"): "强于大盘和科技指数",
        ("outperform", "neutral"): "强于大盘",
        ("outperform", "unknown"): "强于大盘",
        ("neutral", "outperform"): "强于科技指数",
        ("unknown", "outperform"): "强于科技指数",
        ("underperform", "underperform"): "弱于大盘和科技指数",
        ("underperform", "neutral"): "弱于大盘",
        ("underperform", "unknown"): "弱于大盘",
        ("neutral", "underperform"): "弱于科技指数",
        ("unknown", "underperform"): "弱于科技指数",
        ("outperform", "underperform"): "强于大盘但弱于科技指数",
        ("underperform", "outperform"): "弱于大盘但强于科技指数",
        ("neutral", "neutral"): "相对强弱中性",
        ("neutral", "unknown"): "相对大盘中性",
        ("unknown", "neutral"): "相对科技指数中性",
    }
    out["summary"] = {
        "market": market,
        "tech": tech,
        "label_cn": label_map.get((market, tech), "相对强弱数据不足"),
    }
    return out


def fetch_us_benchmarks(days: int = 120) -> dict:
    """Fetch SPY/QQQ OHLCV with a small in-process cache."""
    now = time.time()
    out = {}
    for symbol in ("SPY", "QQQ"):
        hit = _BENCHMARK_CACHE.get(symbol)
        if hit and hit["days"] >= days and now - hit["ts"] < 300:
            out[symbol] = hit["ohlcv"]
            continue
        try:
            ohlcv, _source = _fetch_yfinance(symbol, "us", days)
            _BENCHMARK_CACHE[symbol] = {"ts": now, "days": days, "ohlcv": ohlcv}
            out[symbol] = ohlcv
        except Exception as e:
            _log(f"[{symbol}] Benchmark fetch failed: {e}")
    return out


def fetch_us_extras(code: str, company_name: str = "", want_news: bool = True) -> dict:
    """美股额外上下文：财报日 + 新闻（走 yfinance，免 API Key）。"""
    out = {"earnings_date": None, "days_to_earnings": None, "news": [], "news_sentiment": None}
    try:
        import yfinance as yf
        from datetime import datetime as _now
        t = yf.Ticker(code)
        # --- 财报日 ---
        try:
            cal = t.calendar
            ed = None
            if isinstance(cal, dict):
                v = cal.get("Earnings Date")
                ed = (v[0] if isinstance(v, (list, tuple)) and v else v)
            elif cal is not None and not getattr(cal, "empty", True) and "Earnings Date" in getattr(cal, "index", []):
                ed = cal.loc["Earnings Date"][0]
            if ed is not None:
                eds = str(ed)[:10]
                out["earnings_date"] = eds
                try:
                    out["days_to_earnings"] = (_now.fromisoformat(eds).date() - _now.now().date()).days
                except Exception:
                    pass
        except Exception:
            pass
        # --- 新闻：优先 Google News RSS，无 API Key；失败则回退 yfinance ---
        if want_news:
            try:
                out["news"] = fetch_google_news(code, company_name or code, max_results=5)
            except Exception as e:
                _log(f"[{code}] Google News failed: {e}")
            try:
                if not out["news"]:
                    for n in (t.news or [])[:4]:
                        c = n.get("content") if isinstance(n.get("content"), dict) else None
                        if c:
                            title = c.get("title")
                            link = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
                            pub = (c.get("provider") or {}).get("displayName")
                        else:
                            title, link, pub = n.get("title"), n.get("link"), n.get("publisher")
                        if title:
                            out["news"].append({"title": title, "url": link or "", "publisher": pub or "", "source": "yfinance"})
            except Exception:
                pass
        out["news_sentiment"] = analyze_news_sentiment(out["news"])
    except Exception:
        pass
    return out


# ============================================================
# SECTION 4: Composite Trend Scoring (100 points)
# ============================================================

def calc_trend_score(ma_data: dict, macd_data: dict, rsi_data: dict,
                     vol_data: dict, bias_data: dict, support_data: dict) -> dict:
    """
    Composite scoring system (100 points total):
    - Trend/MA alignment: 30 pts
    - Bias (乖离率): 20 pts
    - Volume: 15 pts
    - MACD: 15 pts
    - RSI: 10 pts
    - Support: 10 pts
    """
    breakdown = {}

    # 1. Trend score (30 pts)
    alignment = ma_data.get("alignment_detail", "consolidation")
    trend_scores = {
        "strong_bullish": 30, "bullish": 26, "weak_bullish": 18,
        "consolidation": 12, "weak_bearish": 8, "bearish": 4,
        "strong_bearish": 0, "insufficient_data": 12,
    }
    breakdown["trend"] = trend_scores.get(alignment, 12)

    # 2. Bias score (20 pts) - prefer slightly below MA5
    bias_ma5 = bias_data.get("bias_ma5", 0)
    if bias_ma5 is None:
        breakdown["bias"] = 10
    elif -3 <= bias_ma5 < 0:
        breakdown["bias"] = 20  # Slightly below MA5 = ideal dip
    elif 0 <= bias_ma5 < 2:
        breakdown["bias"] = 18  # Close to MA5
    elif 2 <= bias_ma5 < 5:
        breakdown["bias"] = 14  # Slightly above
    elif bias_ma5 >= 5:
        breakdown["bias"] = 4   # Too far above, don't chase
    elif -5 <= bias_ma5 < -3:
        breakdown["bias"] = 14  # Pulling back more
    else:
        breakdown["bias"] = 6   # Far below

    # 3. Volume score (15 pts)
    vol_trend = vol_data.get("trend", "normal")
    vol_scores = {
        "shrink_pullback": 15, "heavy_volume_up": 12, "normal": 10,
        "shrink_up": 6, "heavy_volume_down": 0, "insufficient_data": 8,
        "unknown": 8,
    }
    breakdown["volume"] = vol_scores.get(vol_trend, 8)

    # 4. MACD score (15 pts)
    macd_signal = macd_data.get("signal", "neutral")
    macd_scores = {
        "golden_cross_above_zero": 15, "crossing_above_zero": 13,
        "golden_cross": 12, "bullish": 10, "neutral": 7,
        "bearish": 3, "death_cross": 0, "crossing_below_zero": 1,
        "insufficient_data": 7,
    }
    breakdown["macd"] = macd_scores.get(macd_signal, 7)

    # 5. RSI score (10 pts)
    rsi_zone = rsi_data.get("zone", "neutral")
    rsi_scores = {
        "oversold": 10, "strong": 8, "neutral": 5,
        "weak": 3, "overbought": 0, "unknown": 5,
    }
    breakdown["rsi"] = rsi_scores.get(rsi_zone, 5)

    # 6. Support score (10 pts)
    sup_score = 0
    if support_data.get("support_ma5"):
        sup_score += 5
    if support_data.get("support_ma10"):
        sup_score += 5
    breakdown["support"] = sup_score

    total = sum(breakdown.values())

    # Signal generation
    alignment_val = ma_data.get("alignment", "consolidation")
    bullish_alignments = ["bullish", "strong_bullish", "weak_bullish"]

    if total >= 75 and alignment_val in ["bullish", "strong_bullish"]:
        signal = "strong_buy"
    elif total >= 60 and alignment_val in bullish_alignments:
        signal = "buy"
    elif total >= 45:
        signal = "hold"
    elif total >= 30:
        signal = "wait"
    elif alignment_val in ["bearish", "strong_bearish"]:
        signal = "strong_sell"
    else:
        signal = "sell"

    signal_cn = {
        "strong_buy": "强烈买入", "buy": "买入", "hold": "持有",
        "wait": "观望", "sell": "卖出", "strong_sell": "强烈卖出",
    }

    return {
        "total": total,
        "breakdown": breakdown,
        "signal": signal,
        "signal_cn": signal_cn.get(signal, signal),
    }


# ============================================================
# SECTION 5: Main Orchestrator
# ============================================================

def analyze_stock(code: str, days: int = 120, fetch_news: bool = False, extras: bool = False) -> dict:
    """Full analysis pipeline for a single stock."""
    market, normalized, display = classify_stock(code)

    if market == "unknown":
        raise ValueError(f"Cannot classify stock code: {code}")

    # Fetch data with graceful degradation
    if market == "cn_a":
        raw = fetch_cn_a(normalized, days)
    elif market == "cn_hk":
        raw = fetch_hk(normalized, days)
    else:
        raw = fetch_us(normalized, days)

    ohlcv = raw["ohlcv"]
    if not ohlcv or len(ohlcv) < 10:
        raise ValueError(f"Insufficient data for {code}: only {len(ohlcv)} bars")

    closes = [bar["close"] for bar in ohlcv if bar["close"] is not None]
    volumes = [bar["volume"] for bar in ohlcv if bar["volume"] is not None]

    if len(closes) < 10:
        raise ValueError(f"Insufficient valid close prices for {code}")

    # Calculate all indicators
    ma = calc_ma(closes, [5, 10, 20, 60])
    macd = calc_macd(closes)
    rsi = calc_rsi(closes, [6, 12, 24])
    vol = calc_volume_analysis(volumes, closes)
    bias = calc_bias(closes, ma)
    support = calc_support(closes, ma)
    score = calc_trend_score(ma, macd, rsi, vol, bias, support)
    atr = calc_atr([b["high"] for b in ohlcv], [b["low"] for b in ohlcv], closes)
    weekly = calc_weekly(ohlcv)

    # News search (optional, via paid API)
    news = []
    if fetch_news:
        stock_name = raw.get("name", display)
        news = search_news(stock_name, display)

    result = {
        "code": display,
        "market": market,
        "name": raw.get("name", display),
        "data_source": raw.get("source", "unknown"),
        "realtime": raw.get("realtime", {}),
        "indicators": {
            "ma": ma,
            "macd": macd,
            "rsi": rsi,
            "volume": vol,
            "bias": bias,
            "support": support,
            "atr": atr,
        },
        "weekly": weekly,
        "trend_score": score,
        "recent_bars": ohlcv[-10:],
        "total_bars": len(ohlcv),
        "fetch_time": datetime.now().isoformat(),
    }

    # 财报日 + 免费新闻 + 相对强弱（美股额外上下文）
    if extras and market == "us":
        ex = fetch_us_extras(display, raw.get("name", display), want_news=not news)
        if ex.get("earnings_date"):
            result["earnings"] = {"date": ex["earnings_date"], "days": ex["days_to_earnings"]}
        if not news and ex.get("news"):
            news = ex["news"]
        benchmarks = fetch_us_benchmarks(max(days, 120))
        if benchmarks:
            result["relative_strength"] = calc_relative_strength(ohlcv, benchmarks)

    if news:
        result["news"] = news
        result["news_sentiment"] = analyze_news_sentiment(news)
    return result


def main():
    parser = argparse.ArgumentParser(description="Stock Data Fetcher")
    parser.add_argument("--stocks", required=True, help="Comma-separated stock codes")
    parser.add_argument("--days", type=int, default=120, help="History trading days")
    parser.add_argument("--news", action="store_true", help="Also search news (requires TAVILY_API_KEY or SERPAPI_KEY)")
    parser.add_argument("--extras", action="store_true", help="美股额外上下文：财报日 + yfinance免费新闻")
    args = parser.parse_args()

    codes = [c.strip() for c in args.stocks.split(",") if c.strip()]
    results = []
    errors = []

    # Report available data sources
    sources_status = {}
    for lib in ["tushare", "efinance", "akshare", "yfinance"]:
        sources_status[lib] = "available" if _check_source(lib) else "not installed"
    sources_status["tushare_token"] = "configured" if os.environ.get("TUSHARE_TOKEN") else "not set"
    sources_status["tavily_api"] = "configured" if os.environ.get("TAVILY_API_KEY") else "not set"
    sources_status["serpapi"] = "configured" if os.environ.get("SERPAPI_KEY") else "not set"
    _log(f"Data sources: {json.dumps(sources_status)}")

    for code in codes:
        try:
            result = analyze_stock(code, args.days, fetch_news=args.news, extras=args.extras)
            results.append(result)
        except Exception as e:
            errors.append({"code": code, "error": str(e), "type": type(e).__name__})

    output = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "analysis_time": datetime.now().strftime("%H:%M:%S"),
        "data_sources": sources_status,
        "stocks": results,
        "errors": errors,
        "total_requested": len(codes),
        "total_success": len(results),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
