#!/bin/bash
# 一键启动（macOS 双击即可运行）
cd "$(dirname "$0")"

# 开代理（如果你的环境有这个命令）
command -v proxy_on >/dev/null 2>&1 && proxy_on

# 首次运行自动装依赖
python3 -c "import yfinance" 2>/dev/null || pip3 install -r requirements.txt --break-system-packages

python3 analyzer_app.py
