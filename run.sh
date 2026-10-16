#!/usr/bin/env bash
# 启动 HTTP 识别服务。自动选择可用运行环境，优先级：
#   1) runtime/bin/python3     —— 自包含 Linux 运行时（build-linux-runtime.sh 产出，无需系统 Python）
#   2) .venv/bin/python        —— 目标机 install.sh 创建
#   3) .venv_test/bin/python   —— 开发机测试 venv
#   4) 系统 python3            —— 已手动装过 rapidocr
set -euo pipefail
cd "$(dirname "$0")"

# 日志目录：确保 logs/ 存在；前台(终端)运行时把全部输出同时落到控制台与 logs/server.log。
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
if [ -t 1 ]; then
    # 前台：重跑自身，stdout 经 tee 同时写控制台 + server.log。
    # 重跑后 stdout 变为管道(非终端)，不会再进入本分支，避免递归。
    exec "$0" "$@" 2>&1 | tee -a "$LOG_DIR/server.log"
fi

# 项目标识横幅
print_banner() {
    echo "[img-sieve] 工具: img-sieve —— 抖音 / 小红书截图信息提取服务"
    echo "[img-sieve] 作者: zhou liang"
}

# 解析监听端口（默认 8080），用于 keepalive / cron 提示
PORT=8080
args=("$@")
for i in "${!args[@]}"; do
    if [ "${args[$i]}" = "--port" ] && [ $((i+1)) -lt "${#args[@]}" ]; then
        PORT="${args[$((i+1))]}"
    fi
done

for PY in runtime/bin/python3 .venv/bin/python .venv_test/bin/python python3; do
    if [ -x "$PY" ] || command -v "$PY" >/dev/null 2>&1; then
        if "$PY" -c "import rapidocr_onnxruntime" 2>/dev/null; then
            echo "[run] 使用环境: $PY"
            print_banner
            # 启动前的保活提示（前台运行时提示；exec 后 shell 即被 server.py 接管）
            PROJ_DIR="$(pwd)"
            echo ""
            echo "[run] 服务将在前台启动（Ctrl-C 停止）。如需后台保活，二选一："
            echo "      # 方式一：直接拉起保活守护（可登出 SSH、崩溃自动重启）"
            echo "      ./keepalive.sh start --port $PORT     # 查看 ./keepalive.sh status，停止 ./keepalive.sh stop"
            echo "      # 方式二：cron 定时保活（服务/守护异常退出后自动恢复）"
            echo "      crontab -e  添加一行："
            echo "      */1 * * * * cd $PROJ_DIR && ./keepalive.sh start --port $PORT >/dev/null 2>&1"
            echo ""
            # 记录 PID，供 stop.sh / keepalive.sh 精确定位进程。
            # 服务器可能没有 lsof，PID 文件是唯一不依赖外部命令的途径。
            # exec 会替换当前 shell，但 PID 不变，因此这里记的就是 server.py 的 PID。
            echo $$ > "logs/server_${PORT}.pid"
            exec "$PY" server.py "$@"
        fi
    fi
done

echo "[run] 未找到可用的 rapidocr 运行环境。"
echo "      服务器无 Python 时：先在开发机执行 ./build-linux-runtime.sh 生成自包含 runtime/，再打包部署。"
echo "      目标机已有 Python 3.11：先 ./install.sh 再 ./run.sh。"
exit 1
