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

mkdir -p .pybs_tmp

# 国内镜像代理（无需梯子）；直连 github 需梯子，仅作最后兜底
API="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
MIRRORS=(
  "https://ghfast.top/"
  "https://gh-proxy.com/"
  "https://ghproxy.net/"
  "https://mirror.ghproxy.com/"
  "https://ghproxy.cc/"
)

# 通过镜像解析 cpython-3.11 x86_64 install_only 资产的真实下载地址
resolve_asset_url() {
    for m in "${MIRRORS[@]}"; do
        JSON="$(curl -sSL --max-time 25 "${m}${API}" 2>/dev/null || true)"
        [ -z "$JSON" ] && continue
        u="$(printf '%s' "$JSON" | grep -oE '"browser_download_url": *"[^"]*cpython-3\.11[^"]*x86_64-unknown-linux-gnu-install_only[^"]*"' | head -1 | sed -E 's/.*"browser_download_url": *"([^"]+)".*/\1/')"
        if [ -n "$u" ]; then
            echo "$u"
            return 0
        fi
    done
    return 1
}

# 下载：先走国内镜像，最后直连 github
download() {
    local url="$1" m
    for m in "${MIRRORS[@]}"; do
        echo "[build]    尝试镜像: $m"
        if curl -L --fail --max-time 600 --retry 1 "${m}${url}" -o .pybs_tmp/python.tar.gz 2>/dev/null && [ -s .pybs_tmp/python.tar.gz ]; then
            return 0
        fi
    done
    echo "[build]    尝试直连 github（需梯子）..."
    curl -L --fail --max-time 600 "${url}" -o .pybs_tmp/python.tar.gz 2>/dev/null && [ -s .pybs_tmp/python.tar.gz ]
}

if [ -f .pybs_tmp/python.tar.gz ]; then
    echo "[build] 检测到已下载的 .pybs_tmp/python.tar.gz，跳过联网解析与下载"
else
    if [ -n "${PYBS_ASSET_URL:-}" ]; then
        URL="$PYBS_ASSET_URL"
        echo "[build] 使用手动指定资产"
    else
        echo "[build] 1/4 解析 python-build-standalone 最新 release（走国内镜像）..."
        if ! URL="$(resolve_asset_url)"; then
            echo "[build] 错误: 镜像解析失败。可用浏览器(需梯子)打开以下页面手动下载后重跑："
            echo "[build]   https://github.com/astral-sh/python-build-standalone/releases/latest"
            echo "[build]   下载 cpython-3.11.x+<tag>-x86_64-unknown-linux-gnu-install_only.tar.gz"
            echo "[build]   放到 .pybs_tmp/python.tar.gz 后重新执行本脚本（将跳过下载）。"
            echo "[build] 或手动指定完整链接: PYBS_ASSET_URL=<链接> ./build-linux-runtime.sh"
            exit 1
        fi
        echo "[build]    资产: $(basename "$URL")"
    fi
    echo "[build] 2/4 下载可移植 CPython 3.11 (x86_64-unknown-linux-gnu) ..."
    echo "[build]    $URL"
    if ! download "$URL"; then
        echo "[build] 错误: 所有下载通道均失败。请手动用浏览器下载后放到 .pybs_tmp/python.tar.gz 再重跑。"
        exit 1
    fi
fi

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
