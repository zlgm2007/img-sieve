#!/usr/bin/env bash
# 保活守护脚本：确保识别服务进程常驻在线，进程意外退出后自动拉起。
# 适合部署场景：后台守护、SSH 会话登出不掉线、崩溃自动重启。
#
# 用法:
#   ./keepalive.sh start [--port 8080]   # 拉起服务并启动守护（后台，可登出）
#   ./keepalive.sh status [--port 8080]  # 查看服务与守护状态
#   ./keepalive.sh stop  [--port 8080]   # 停止守护并关闭服务
# 依赖: run.sh(启动服务) / stop.sh(停止服务)；日志在 logs/
set -eo pipefail
cd "$(dirname "$0")"

CMD="${1:-}"
[ $# -gt 0 ] && shift
PORT=8080
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="${2:?--port 需要端口号}"; shift 2 ;;
        *) echo "[keepalive] 未知参数: $1"; echo "用法: ./keepalive.sh {start|status|stop} [--port 8080]"; exit 2 ;;
    esac
done

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
SUPER_PID_FILE="$LOG_DIR/keepalive_super.pid"

# 端口是否有进程在监听（lsof: mac/linux；ss: centos 自带；fuser: linux psmisc）
is_listening() {
    local p="$1"
    if command -v lsof >/dev/null 2>&1 && [ -n "$(lsof -ti tcp:"$p" 2>/dev/null)" ]; then return 0; fi
    if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":$p "; then return 0; fi
    if command -v fuser >/dev/null 2>&1 && fuser "$p/tcp" >/dev/null 2>&1; then return 0; fi
    return 1
}

server_pids() {
    local p="$1"
    local out=""
    if command -v lsof >/dev/null 2>&1; then out="$out $(lsof -ti tcp:"$p" 2>/dev/null || true)"; fi
    if command -v fuser >/dev/null 2>&1; then out="$out $(fuser "$p/tcp" 2>/dev/null || true)"; fi
    if command -v ss >/dev/null 2>&1; then
        out="$out $(ss -ltnp 2>/dev/null | awk -v pp=":$p" 'index($0,pp){n=split($6,a,","); for(i=1;i<=n;i++){if(a[i]~/pid=/){gsub(/[^0-9]/,"",a[i]); if(a[i]!="")print a[i]}}}' || true)"
    fi
    printf '%s\n' $out | sort -u | grep -v '^$'
}

supervisor_alive() {
    [ -f "$SUPER_PID_FILE" ] && kill -0 "$(cat "$SUPER_PID_FILE" 2>/dev/null)" 2>/dev/null
}

start_server() {
    echo "[keepalive] $(date '+%F %T') 服务未运行，启动中（端口 ${PORT}）..."
    nohup ./run.sh --host 0.0.0.0 --port "$PORT" >> "$LOG_DIR/server.log" 2>&1 &
    for _ in $(seq 1 12); do
        sleep 1
        is_listening "$PORT" && break
    done
    if is_listening "$PORT"; then
        echo "[keepalive] $(date '+%F %T') 服务已上线（端口 ${PORT}）"
    else
        echo "[keepalive] $(date '+%F %T') 启动失败或端口未就绪，守护将继续重试。详见 logs/server.log"
    fi
}

# 内部子命令：守护循环（由 start 在后台拉起）
_supervise() {
    while true; do
        if ! is_listening "$PORT"; then
            start_server
        fi
        sleep 5
    done
}

case "$CMD" in
    start)
        if supervisor_alive; then
            echo "[keepalive] 守护已在运行（PID $(cat "$SUPER_PID_FILE")），端口 ${PORT}。"
        else
            nohup "$0" _supervise --port "$PORT" >> "$LOG_DIR/keepalive.log" 2>&1 &
            BG_PID=$!
            echo "$BG_PID" > "$SUPER_PID_FILE"
            echo "[keepalive] 守护已启动（PID ${BG_PID}，端口 ${PORT}）。登出 SSH 后服务仍将保持在线。"
        fi
        if is_listening "$PORT"; then
            echo "[keepalive] 服务正在运行（端口 ${PORT}）。"
        else
            sleep 6
            is_listening "$PORT" && echo "[keepalive] 服务已上线（端口 ${PORT}）。" \
                || echo "[keepalive] 服务尚未就绪，守护会自动拉起，稍后 ./keepalive.sh status 查看。"
        fi
        ;;

    status)
        echo "[keepalive] 端口: ${PORT}"
        if is_listening "$PORT"; then
            echo "[keepalive] 服务: 运行中  PID: $(echo $(server_pids "$PORT") | tr ' ' ',')"
        else
            echo "[keepalive] 服务: 未运行"
        fi
        if supervisor_alive; then
            echo "[keepalive] 守护: 运行中  PID: $(cat "$SUPER_PID_FILE")"
        else
            echo "[keepalive] 守护: 未运行（服务不会被自动拉起）"
        fi
        echo "[keepalive] 日志: $LOG_DIR/server.log , $LOG_DIR/keepalive.log"
        ;;

    stop)
        # 守护是纯无状态循环，直接用 SIGKILL 确保关掉（部分环境 bash 忽略 SIGTERM）
        if supervisor_alive; then
            echo "[keepalive] 结束守护（PID $(cat "$SUPER_PID_FILE")）"
            kill -9 "$(cat "$SUPER_PID_FILE")" 2>/dev/null || true
            rm -f "$SUPER_PID_FILE"
        else
            echo "[keepalive] 守护未在运行"
        fi
        ./stop.sh --port "$PORT"
        ;;

    _supervise)
        _supervise
        ;;

    *)
        echo "[keepalive] 用法: ./keepalive.sh {start|status|stop} [--port 8080]"
        exit 2
        ;;
esac
