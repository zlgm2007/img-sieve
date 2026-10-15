#!/usr/bin/env bash
# 启动 HTTP 识别服务。自动选择可用运行环境，优先级：
#   1) runtime/bin/python3     —— 自包含 Linux 运行时（build-linux-runtime.sh 产出，无需系统 Python）
#   2) .venv/bin/python        —— 目标机 install.sh 创建
#   3) .venv_test/bin/python   —— 开发机测试 venv
#   4) 系统 python3            —— 已手动装过 rapidocr
set -euo pipefail
cd "$(dirname "$0")"

for PY in runtime/bin/python3 .venv/bin/python .venv_test/bin/python python3; do
    if [ -x "$PY" ] || command -v "$PY" >/dev/null 2>&1; then
        if "$PY" -c "import rapidocr_onnxruntime" 2>/dev/null; then
            echo "[run] 使用环境: $PY"
            exec "$PY" server.py "$@"
        fi
    fi
done

echo "[run] 未找到可用的 rapidocr 运行环境。"
echo "      服务器无 Python 时：先在开发机执行 ./build-linux-runtime.sh 生成自包含 runtime/，再打包部署。"
echo "      目标机已有 Python 3.11：先 ./install.sh 再 ./run.sh。"
exit 1
