#!/usr/bin/env bash
# 目标机（纯内网 Linux）环境准备。
# 场景 A：随包已含自包含 runtime/（build-linux-runtime.sh 产出）→ 无需系统 Python，直接可用。
# 场景 B：无 runtime/ 但有系统 Python 3.11 → 创建 .venv 并从本地 wheel 离线安装。
set -euo pipefail
cd "$(dirname "$0")"

# 安装成功后的 MySQL/.env 配置提示（仅同步识别可跳过；异步识别与任务记录必须配置）。
env_hint() {
    if [ ! -f .env ] && [ -f .env.example ]; then
        cp .env.example .env
        echo "[install] 已从 .env.example 生成 .env，请在里面填入 MySQL 账号密码。"
    fi
    echo ""
    echo "[install] 异步识别 / 任务记录需要 MySQL；仅跑同步识别可跳过本步。"
    echo "[install] 配置方式（部署目录 /opt/server/img-sieve）："
    echo "[install]   cd /opt/server/img-sieve"
    echo "[install]   vi .env"
    echo "[install]   IMG_SIEVE_MYSQL_HOST=127.0.0.1"
    echo "[install]   IMG_SIEVE_MYSQL_PORT=3306"
    echo "[install]   IMG_SIEVE_MYSQL_USER=imgsieve"
    echo "[install]   IMG_SIEVE_MYSQL_PASSWORD=数据库密码"
    echo "[install]   IMG_SIEVE_MYSQL_DB=imgsieve"
    echo "[install] 配好后执行 ./keepalive.sh start（同时拉起 HTTP + worker）；仅同步可用 ./run.sh"
}

# 场景 A：自包含 runtime
if [ -x runtime/bin/python3 ]; then
    echo "[install] 检测到自包含运行时 runtime/，无需系统 Python。"
    if runtime/bin/python3 -c "import rapidocr_onnxruntime" 2>/dev/null; then
        echo "[install] rapidocr 就绪。"
        env_hint
        exit 0
    fi
    echo "[install] runtime/ 存在但缺少 rapidocr，尝试用随包 wheel 补装..."
    if [ -d wheels_linux_x86_64 ]; then
        runtime/bin/python3 -m pip install --no-index \
            --find-links wheels_linux_x86_64 rapidocr_onnxruntime
        echo "[install] 补装完成。"
        env_hint
        exit 0
    fi
    echo "[install] 缺少 wheels_linux_x86_64/，无法补装。"
    exit 1
fi

# 场景 B：系统 Python 3.11
echo "[install] 使用系统 Python 创建 .venv（需要 Python 3.11）..."
PY=$(command -v python3 || true)
if [ -z "$PY" ]; then
    echo "[install] 错误: 未找到 python3，且未随包携带自包含 runtime/。"
    echo "          请在开发机执行 ./build-linux-runtime.sh 生成 runtime/ 后重新打包，"
    echo "          或在服务器安装 Python 3.11。"
    exit 1
fi
"$PY" - <<'EOF'
import sys
v = sys.version_info
if not (v.major == 3 and v.minor == 11):
    print(f"[install] 警告: 当前 Python {v.major}.{v.minor}，随包 wheel 为 3.11 构建，请使用 3.11。")
else:
    print(f"[install] Python {v.major}.{v.minor} OK")
EOF
"$PY" -m venv .venv
./.venv/bin/pip install --no-index --find-links wheels_linux_x86_64 rapidocr_onnxruntime
./.venv/bin/python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('[install] rapidocr 就绪')"
env_hint
echo "[install] 完成。"
