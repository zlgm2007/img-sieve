#!/usr/bin/env bash
# 停止 HTTP 识别服务（与 run.sh 配套）。
# 按端口找到监听进程，仅关闭本服务的 server.py 进程（优雅 TERM，兜底 KILL）。
# 用法: ./stop.sh            # 默认端口 8080
#       ./stop.sh --port 8080
set -euo pipefail
cd "$(dirname "$0")"

PORT=8080
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="${2:?--port 需要端口号}"; shift 2 ;;
        *) echo "[stop] 未知参数: $1"; echo "用法: ./stop.sh [--port 8080]"; exit 2 ;;
    esac
done

# 收集监听该端口的 PID（多种发现方法，任一可用即可；服务由同一用户启动，无需 root）
PIDS=""
# 1) lsof（macOS 自带 / Linux 装了才有）
if command -v lsof >/dev/null 2>&1; then
    PIDS="$PIDS $(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
fi
# 2) fuser（Linux，psmisc）
if command -v fuser >/dev/null 2>&1; then
    PIDS="$PIDS $(fuser "$PORT/tcp" 2>/dev/null || true)"
fi
# 3) ss（Linux iproute2，CentOS 7 自带）——从监听行提取 pid
if command -v ss >/dev/null 2>&1; then
    PIDS="$PIDS $(ss -ltnp 2>/dev/null \
        | awk -v p=":$PORT" 'index($0,p){n=split($6,a,","); for(i=1;i<=n;i++){if(a[i]~/pid=/){gsub(/[^0-9]/,"",a[i]); if(a[i]!="")print a[i]}}}' || true)"
fi

# 去重、去空
PIDS="$(printf '%s\n' $PIDS | sort -u | grep -v '^$')"

if [ -z "$PIDS" ]; then
    echo "[stop] 端口 $PORT 上没有找到监听的服务进程（可能未启动）。"
    exit 0
fi

# 只关本服务进程，避免误杀同端口其他程序
for pid in $PIDS; do
    cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    case "$cmdline" in
        *server.py*) ;;
        *) echo "[stop] 跳过非本服务 PID $pid（$(echo "$cmdline" | cut -c1-80)）"; continue ;;
    esac
    echo "[stop] 向 PID $pid 发送 SIGTERM"
    kill -TERM "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
done

# 等待优雅退出，必要时强杀
sleep 1
for pid in $PIDS; do
    if kill -0 "$pid" 2>/dev/null; then
        echo "[stop] PID $pid 未退出，SIGKILL 强杀"
        kill -9 "$pid" 2>/dev/null || true
    fi
done
echo "[stop] 完成：端口 $PORT 服务已停止。可用 ./run.sh 重新启动。"
