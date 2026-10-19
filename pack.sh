#!/usr/bin/env bash
# 开发机打包脚本：把代码 + Linux wheel 打包成可传输到内网部署的 tar 包。
#
# 用法:
#   ./pack.sh                  # 默认输出 dist/img-sieve-linux-x86_64.tar.gz
#   ./pack.sh arm64            # 输出 arm64 包（需先下载对应 wheel 到 wheels_linux_arm64）
#
# 产物结构:
#   img-sieve/
#   ├── img_sieve/             # Python 包（OCR 驱动 + 提取规则）
#   ├── cli.py  server.py      # 本地 CLI 与 HTTP 服务
#   ├── README.md
#   ├── wheels_linux_x86_64/   # Linux x86_64 wheel（Python 3.11）
#   ├── install.sh             # 目标机离线安装
#   ├── run.sh                 # 目标机启动服务
#   └── web/                   # 网页界面
set -euo pipefail
cd "$(dirname "$0")"

ARCH="${1:-x86_64}"
WHEEL_DIR="wheels_linux_${ARCH}"
OUT_DIR="dist"
STAGE="$OUT_DIR/_stage/img-sieve"

if [ ! -d "$WHEEL_DIR" ]; then
    echo "[pack] 错误: 缺少 $WHEEL_DIR/。请先用 pip download 准备 ${ARCH} 的 wheel："
    if [ "$ARCH" = "x86_64" ]; then
        echo "        pip download --dest $WHEEL_DIR --platform manylinux_2_17_x86_64 \\
              --python-version 311 --only-binary=:all: rapidocr_onnxruntime"
    else
        echo "        pip download --dest $WHEEL_DIR --platform manylinux_2_17_aarch64 \\
              --python-version 311 --only-binary=:all: rapidocr_onnxruntime"
    fi
    exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE"

# 1) 代码与脚本
cp -R img_sieve "$STAGE/"
cp cli.py server.py worker.py README.md install.sh run.sh stop.sh keepalive.sh .env.example "$STAGE/"
# 1a) 网页界面
if [ -d web ]; then
    cp -R web "$STAGE/"
fi
# 1b) 测试夹具（供目标机用 mock 后端自测，可选）
if [ -d fixtures ]; then
    cp -R fixtures "$STAGE/"
fi
# 2) wheel
cp -R "$WHEEL_DIR" "$STAGE/"
# 2b) 自包含 Linux 运行时（可选，服务器无 Python 时使用，由 build-linux-runtime.sh 产出）
if [ -d runtime ]; then
    cp -R runtime "$STAGE/"
fi

# 清理 __pycache__ 与自检截图产物
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$STAGE/web/_shots" 2>/dev/null || true

# 剥离 macOS 扩展属性（com.apple.provenance / lastuseddate 等）。
# 若不剥离，macOS bsdtar 会把它们以 PAX 扩展头(LIBARCHIVE.xattr.*)写进压缩包，
# 目标机 Linux 的老版 GNU tar 会误解析，导致文件名被加前导 "-"、文件字节错位。
if [ "$(uname -s)" = "Darwin" ] && command -v xattr >/dev/null 2>&1; then
    echo "[pack] 剥离 macOS 扩展属性 (xattr -cr) ..."
    xattr -cr "$STAGE" 2>/dev/null || true
fi

# 4) 打成 tar.gz（bsdtar 显式禁用 xattr / ._ 文件，避免污染部署包）
mkdir -p "$OUT_DIR"
TARBALL="$OUT_DIR/img-sieve-linux-${ARCH}.tar.gz"
COPYFILE_DISABLE=1 tar --no-xattrs --no-fflags -C "$OUT_DIR/_stage" -czf "$TARBALL" img-sieve
rm -rf "$OUT_DIR/_stage"

echo "[pack] 打包完成: $TARBALL"
echo "[pack] 大小: $(du -h "$TARBALL" | cut -f1)"
echo "[pack] 部署到内网目标机:"
echo "        1) 拷贝 $TARBALL 到目标机"
echo "        2) tar -xzf $TARBALL && cd img-sieve"
echo "        3) ./install.sh   (离线安装, 需 Python 3.11)"
echo "        4) ./run.sh --host 0.0.0.0 --port 8080"