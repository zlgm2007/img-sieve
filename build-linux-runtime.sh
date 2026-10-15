#!/usr/bin/env bash
# 开发机(可联网)一键构建「自包含 Linux x86_64 运行环境」(runtime/)。
#
# 支持在 Linux 或 macOS 上构建：
#   Linux : 解压后直接运行 linux python，用其 pip 离线装 wheel
#   macOS : 用本机 python3 的 pip 做跨平台安装(--platform manylinux2014_x86_64)，
#           把 linux wheel 直接解压进 linux python 的 site-packages，无需执行 linux python
#
# 产出 runtime/ 完全自包含；目标机(Linux 内网,如 CentOS7 x86_64)解压后直接 ./run.sh，
# 无需系统 Python、无需联网安装。构建完成后 ./pack.sh 打成部署包即可。
set -eo pipefail
cd "$(dirname "$0")"
OS="$(uname -s)"

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

resolve_asset_url() {
    for m in "${MIRRORS[@]}"; do
        JSON="$(curl -sSL --max-time 25 "${m}${API}" 2>/dev/null || true)"
        [ -z "$JSON" ] && continue
        u="$(printf '%s' "$JSON" | grep -oE '"browser_download_url": *"[^"]*cpython-3\.11[^"]*x86_64-unknown-linux-gnu-install_only[^"]*"' | head -1 | sed -E 's/.*"browser_download_url": *"([^"]+)".*/\1/')"
        if [ -n "$u" ]; then echo "$u"; return 0; fi
    done
    return 1
}

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

# ---- 1) 获取可移植 CPython 3.11 tarball ----
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

# ---- 2) 解压 ----
echo "[build] 3/4 解压 ..."
tar -xzf .pybs_tmp/python.tar.gz -C .pybs_tmp
PYROOT="$(find .pybs_tmp -maxdepth 2 -type d -name python 2>/dev/null | head -1)"
if [ -z "$PYROOT" ]; then
    echo "[build] 错误: 解压后未找到 python 目录。"
    exit 1
fi
SP="$PYROOT/lib/python3.11/site-packages"

# ---- 3) 离线安装 rapidocr wheel ----
echo "[build] 3/4 离线安装 rapidocr wheel（Linux x86_64）..."
if [ "$OS" = "Linux" ]; then
    PYPY="$(find "$PYROOT/bin" -maxdepth 1 \( -name python -o -name python3 \) 2>/dev/null | head -1)"
    [ -n "$PYPY" ] && [ -x "$PYPY" ] || { echo "[build] 错误: 未找到可执行的 python。"; exit 1; }
    echo "[build]    使用: $PYPY ($("$PYPY" -V 2>&1))"
    "$PYPY" -m ensurepip --upgrade >/dev/null 2>&1 || true
    "$PYPY" -m pip install --no-index --find-links wheels_linux_x86_64 rapidocr_onnxruntime
else
    echo "[build]    当前系统: $OS（macOS/非Linux）→ 用本机 pip 跨平台安装 linux wheel"
    mkdir -p "$SP"
    python3 -m pip install --no-index --find-links wheels_linux_x86_64 \
        --platform manylinux2014_x86_64 --python-version 3.11 \
        --only-binary=:all: --target "$SP" rapidocr_onnxruntime
fi

# ---- 4) 整理为 runtime/ 并校验 ----
rm -rf runtime
mkdir -p runtime
cp -R "$PYROOT"/. runtime/
# 校验关键模块是否就位
if [ -f runtime/lib/python3.11/site-packages/rapidocr_onnxruntime/__init__.py ] \
   || [ -d runtime/lib/python3.11/site-packages/rapidocr_onnxruntime ]; then
    echo "[build] 4/4 校验: rapidocr_onnxruntime 已装入 runtime/"
else
    echo "[build] 警告: runtime/ 中未找到 rapidocr_onnxruntime，请检查安装日志。"
fi
rm -rf .pybs_tmp

echo "[build] 完成: runtime/ 已就绪（自包含 Linux x86_64，目标机直接 ./run.sh）"
echo "[build] 请确认 runtime/bin/python3 存在；打包部署用 ./pack.sh"
