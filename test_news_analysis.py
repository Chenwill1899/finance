#!/usr/bin/env python3
"""
News search and interpretation tests.
These tests are deterministic and do not access the network.
"""
import stock_data_fetcher as f


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss>
  <channel>
    <item>
      <title>Redwire wins NASA contract for new space infrastructure</title>
      <link>https://news.example.com/rdw-contract</link>
      <source>Example News</source>
      <pubDate>Sat, 20 Jun 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Redwire announces public offering after earnings miss</title>
      <link>https://news.example.com/rdw-offering</link>
      <source>Market Wire</source>
      <pubDate>Sat, 20 Jun 2026 13:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_google_news_url_includes_code_company_and_stock_terms():
    url = f.build_google_news_rss_url("RDW", "Redwire Corporation")

    assert "news.google.com/rss/search" in url, url
    assert "RDW" in url, url
    assert "Redwire" in url, url
    assert "stock" in url.lower(), url
    print("✓ Google News RSS URL")


def test_parse_google_news_rss_extracts_items():
    items = f.parse_google_news_rss(RSS_SAMPLE, max_results=2)

    assert len(items) == 2, items
    assert items[0]["title"] == "Redwire wins NASA contract for new space infrastructure"
    assert items[0]["publisher"] == "Example News"
    assert items[0]["source"] == "google_news"
    assert items[0]["published_at"].startswith("2026-06-20"), items[0]
    print("✓ Google News RSS解析")


def test_analyze_news_sentiment_identifies_positive_negative_and_risk():
    items = [
        {"title": "Redwire wins NASA contract and raises guidance"},
        {"title": "Redwire public offering raises dilution concerns"},
        {"title": "Analysts warn earnings volatility before results"},
    ]

    result = f.analyze_news_sentiment(items)

    assert result["tone"] == "mixed", result
    assert result["positive_count"] == 1, result
    assert result["negative_count"] == 1, result
    assert result["risk_count"] == 2, result
    assert "波动" in result["summary_cn"], result
    print("✓ 新闻情绪/风险识别")


def test_analyze_news_sentiment_handles_empty_news():
    result = f.analyze_news_sentiment([])

    assert result["tone"] == "none", result
    assert result["summary_cn"] == "暂无可用新闻。", result
    print("✓ 空新闻处理")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n全部 {len(tests)} 项新闻测试通过 ✅")
