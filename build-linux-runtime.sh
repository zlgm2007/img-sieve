#!/usr/bin/env bash
# 开发机（可联网）一键构建「自包含 Linux 运行环境」。
#
# 作用：内网服务器没有 Python 时，用它生成一个随包携带的、完全自包含的
# Linux x86_64 CPython 3.11 + rapidocr 环境（runtime/）。
# 目标机无需系统 Python、无需安装任何包，解包后直接运行 runtime/bin/python3 server.py。
#
# 用法（在可联网的开发机上执行一次）:
#   ./build-linux-runtime.sh
# 成功后产出 runtime/，再 ./pack.sh 打包即可。
#
# 说明：会通过 GitHub API 自动解析 python-build-standalone 最新 release，
# 匹配其中 cpython-3.11 的 x86_64 install_only 资产，无需手填版本号。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d wheels_linux_x86_64 ]; then
    echo "[build] 错误: 缺少 wheels_linux_x86_64/，请先准备 Linux x86_64 wheel。"
    exit 1
fi

# 允许手动指定资产 URL（可跳过 API 解析）
if [ -n "${PYBS_ASSET_URL:-}" ]; then
    URL="$PYBS_ASSET_URL"
    echo "[build] 使用手动指定资产: $URL"
else
    echo "[build] 1/4 解析 python-build-standalone 最新 release ..."
    API="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
    JSON="$(curl -sSL --fail "$API")"
    TAG="$(printf '%s' "$JSON" | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')"
    echo "[build]    最新 release tag: ${TAG:-未获取}"
    URL="$(printf '%s' "$JSON" | grep -oE '"browser_download_url": *"[^"]*cpython-3\.11[^"]*x86_64-unknown-linux-gnu-install_only[^"]*"' | head -1 | sed -E 's/.*"browser_download_url": *"([^"]+)".*/\1/')"
    if [ -z "$URL" ]; then
        echo "[build] 错误: 未在最新 release 中找到 cpython-3.11 x86_64 install_only 资产。"
        echo "[build] 可在最新 release 页面确认文件名，再手动执行:"
        echo "[build]   PYBS_ASSET_URL=<完整下载链接> ./build-linux-runtime.sh"
        exit 1
    fi
fi

echo "[build] 2/4 下载可移植 CPython 3.11 (x86_64-unknown-linux-gnu) ..."
echo "[build]    $URL"
mkdir -p .pybs_tmp
curl -L --fail --retry 2 "$URL" -o .pybs_tmp/python.tar.gz

echo "[build] 3/4 解压并离线安装 rapidocr wheel（Linux x86_64）..."
tar -xzf .pybs_tmp/python.tar.gz -C .pybs_tmp
# python-build-standalone 解压结构: <root>/python/bin/python3
PYPY="$(find .pybs_tmp -maxdepth 3 -type f -name python3 -path '*/bin/*' | head -1)"
if [ -z "$PYPY" ]; then
    echo "[build] 错误: 未找到解压出的 python3 可执行文件。"
    exit 1
fi
echo "[build]    使用: $PYPY ($("$PYPY" -V))"
# 确保 pip
"$PYPY" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$PYPY" -m pip install --no-index --find-links wheels_linux_x86_64 rapidocr_onnxruntime

echo "[build] 4/4 验证自包含环境 ..."
"$PYPY" -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('    RapidOCR 初始化成功')"

# 把解压出的 python 目录整理为 runtime/
rm -rf runtime
mkdir -p runtime
cp -R "$(dirname "$(dirname "$PYPY")")/." runtime/
rm -rf .pybs_tmp

echo "[build] 完成: runtime/ 已就绪（自包含，无需目标机装 Python）"
echo "[build] 目标机部署: 解包后直接 ./run.sh 即可"
echo "[build] 请确认 runtime/bin/python3 -V 输出 3.11"
