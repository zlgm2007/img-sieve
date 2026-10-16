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

# 收集监听该端口的 PID。
# 优先读 run.sh 写的 PID 文件 —— 不依赖 lsof/fuser/ss 任何外部命令，
# 这是精简版 CentOS（无 lsof）上唯一可靠的方式。
# 其次依次尝试 lsof / fuser / ss，任一可用即可（服务由同一用户启动，无需 root）。
PIDS=""
PID_FILE="logs/server_${PORT}.pid"
if [ -f "$PID_FILE" ]; then
    REC_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    # 校验 PID 仍存活且确实是本服务，防止复用陈旧 pid 误杀
    if [ -n "$REC_PID" ] && kill -0 "$REC_PID" 2>/dev/null; then
        REC_CMD="$(ps -p "$REC_PID" -o args= 2>/dev/null || true)"
        case "$REC_CMD" in
            *server.py*) PIDS="$PIDS $REC_PID" ;;
            *) echo "[stop] 忽略 $PID_FILE 中已失效的 PID $REC_PID" ;;
        esac
    fi
fi

# 1) lsof（macOS 自带 / Linux 装了才有）—— -t 只输出纯 PID
if command -v lsof >/dev/null 2>&1; then
    PIDS="$PIDS $(lsof -ti tcp:"$PORT" 2>/dev/null | grep -E '^[0-9]+$' || true)"
fi
# 2) fuser（Linux，psmisc）
#    ⚠️ fuser 输出的是 "8080/tcp:  <PID>  <命令>" 整行文本。
#    PID 在第2 个字段；若直接按空格拆，"8080/tcp:" 和命令名都会被当成 PID。
#    注意不能用 tr 把非数字都转成换行 —— 端口号(如 8080)会被误当成 PID。
if command -v fuser >/dev/null 2>&1; then
    PIDS="$PIDS $(fuser "$PORT/tcp" 2>/dev/null \
        | awk '{ if ($2 ~ /^[0-9]+$/) print $2 }' || true)"
fi
# 3) ss（Linux iproute2，CentOS 7 自带）——从监听行提取 pid
if command -v ss >/dev/null 2>&1; then
    PIDS="$PIDS $(ss -ltnp 2>/dev/null \
        | awk -v p=":$PORT" 'index($0,p){n=split($6,a,","); for(i=1;i<=n;i++){if(a[i]~/pid=/){gsub(/[^0-9]/,"",a[i]); if(a[i]!="")print a[i]}}}' || true)"
fi

# 去重去空。grep 无匹配返回 1，配合 set -e 会静默退出，必须兜住。
PIDS="$(printf '%s\n' $PIDS | grep -E '^[0-9]+$' | sort -u || true)"

if [ -z "$PIDS" ]; then
    echo "[stop] 端口 $PORT 上没有找到监听的服务进程（可能未启动）。"
    exit 0
fi

# 只关本服务进程，避免误杀同端口其他程序
for pid in $PIDS; do
    cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    case "$cmdline" in
        *server.py*) ;;
        *) echo "[stop] 跳过非本服务 PID ${pid}（$(echo "$cmdline" | cut -c1-80)）"; continue ;;
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

# 清理 PID 文件，避免下次 stop 误用陈旧 PID
if [ -f "$PID_FILE" ]; then
    STALE_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$STALE_PID" ] && ! kill -0 "$STALE_PID" 2>/dev/null; then
        rm -f "$PID_FILE"
    fi
fi
echo "[stop] 完成：端口 $PORT 服务已停止。可用 ./run.sh 重新启动。"
