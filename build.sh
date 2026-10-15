#!/usr/bin/env bash
# 编译 Apple Vision OCR 可执行文件 bin/ocr_helper。
# 需要 macOS + 正常工作的 Swift 工具链（Xcode 或 Command Line Tools）。
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p bin

echo "[build] 正在编译 bin/ocr_helper ..."
if swiftc -O -o bin/ocr_helper ocr_helper/main.swift; then
    echo "[build] 编译成功: bin/ocr_helper"
    ./bin/ocr_helper --help >/dev/null 2>&1 || true
else
    echo "[build] 编译失败。请确认已安装 Xcode 或 Command Line Tools，并运行:"
    echo "        xcode-select --install"
    exit 1
fi
