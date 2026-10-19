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

# 加载 MySQL/异步配置（若存在）。.env 形如：
#   IMG_SIEVE_MYSQL_HOST=10.237.179.21
#   IMG_SIEVE_MYSQL_PORT=3306
#   IMG_SIEVE_MYSQL_USER=imgsieve
#   IMG_SIEVE_MYSQL_PASSWORD=xxxx
#   IMG_SIEVE_MYSQL_DB=imgsieve
[ -f .env ] && set -a && . ./.env && set +a

# 项目标识横幅
print_banner() {
    echo "[img-sieve] 工具: img-sieve —— 抖音 / 小红书截图信息提取服务"
    echo "[img-sieve] 作者: zhou liang"
}

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
    # 1) ss（CentOS 7 自带，最可能可用）
    if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":$p "; then return 0; fi
    # 2) PID 文件（run.sh 写入）——不依赖任何外部命令，精简系统兜底
    local pf="logs/server_${p}.pid"
    if [ -f "$pf" ]; then
        local rpid
        rpid="$(cat "$pf" 2>/dev/null || true)"
        if [ -n "$rpid" ] && kill -0 "$rpid" 2>/dev/null; then return 0; fi
    fi
    # 3) lsof / fuser（不一定装了）
    if command -v lsof >/dev/null 2>&1 && [ -n "$(lsof -ti tcp:"$p" 2>/dev/null)" ]; then return 0; fi
    if command -v fuser >/dev/null 2>&1 && fuser "$p/tcp" >/dev/null 2>&1; then return 0; fi
    return 1
}

server_pids() {
    local p="$1"
    local out=""
    # lsof -t 只输出纯 PID
    if command -v lsof >/dev/null 2>&1; then
        out="$out $(lsof -ti tcp:"$p" 2>/dev/null | grep -E '^[0-9]+$' || true)"
    fi
    # ⚠️ fuser 输出 "8080/tcp:  <PID>  <命令>"，PID 在第 2 个字段。
    #    直接按空格拆会把 "8080/tcp:" 和命令名也当成 PID，
    #    status 就会打印出根本不存在的进程；也不能用 tr 过滤，
    #    因为端口号本身也是纯数字，会被误当 PID。
    if command -v fuser >/dev/null 2>&1; then
        out="$out $(fuser "$p/tcp" 2>/dev/null | awk '{ if ($2 ~ /^[0-9]+$/) print $2 }' || true)"
    fi
    # ss -ltnp 里的 users:(("python",pid=12345,fd=3))，提取 pid= 后面的数字
    if command -v ss >/dev/null 2>&1; then
        out="$out $(ss -ltnp 2>/dev/null | awk -v pp=":$p" 'index($0,pp){n=split($6,a,","); for(i=1;i<=n;i++){if(a[i]~/pid=/){gsub(/[^0-9]/,"",a[i]); if(a[i]!="")print a[i]}}}' || true)"
    fi
    # PID 文件兜底（服务器无 lsof/fuser/ss 时）
    local pf="logs/server_${p}.pid"
    if [ -f "$pf" ]; then
        local rpid
        rpid="$(cat "$pf" 2>/dev/null || true)"
        if [ -n "$rpid" ] && kill -0 "$rpid" 2>/dev/null; then
            case "$(ps -p "$rpid" -o args= 2>/dev/null || true)" in
                *server.py*) out="$out $rpid" ;;
            esac
        fi
    fi
    # 最终只保留纯数字 PID。
    # grep 无匹配时退出码为 1，keepalive.sh 用了 set -e，必须兜住
    printf '%s\n' $out | grep -E '^[0-9]+$' | sort -u || true
}

supervisor_alive() {
    [ -f "$SUPER_PID_FILE" ] && kill -0 "$(cat "$SUPER_PID_FILE" 2>/dev/null)" 2>/dev/null
}

# ---------- 异步 worker ----------
WORKER_PID_FILE="$LOG_DIR/worker.pid"

# 是否配置了 MySQL（配了才需要跑 worker）
mysql_configured() {
    [ -n "${IMG_SIEVE_MYSQL_HOST:-}" ]
}

resolve_python() {
    for PY in runtime/bin/python3 .venv/bin/python .venv_test/bin/python python3; do
        if { [ -x "$PY" ] || command -v "$PY" >/dev/null 2>&1; } \
           && "$PY" -c "import rapidocr_onnxruntime" 2>/dev/null; then
            echo "$PY"; return 0
        fi
    done
    return 1
}

worker_alive() {
    [ -f "$WORKER_PID_FILE" ] && kill -0 "$(cat "$WORKER_PID_FILE" 2>/dev/null)" 2>/dev/null
}

start_worker() {
    if ! mysql_configured; then return 0; fi   # 未配 MySQL 不跑 worker
    if worker_alive; then return 0; fi
    local PY
    PY="$(resolve_python || true)"
    if [ -z "$PY" ]; then
        echo "[keepalive] $(date '+%F %T') 未找到可用 Python，worker 未启动。"
        return 1
    fi
    nohup "$PY" worker.py >> "$LOG_DIR/worker.log" 2>&1 &
    echo $! > "$WORKER_PID_FILE"
    echo "[keepalive] $(date '+%F %T') worker 已启动（PID $!），日志 $LOG_DIR/worker.log"
}

stop_worker() {
    if [ -f "$WORKER_PID_FILE" ]; then
        local wpid
        wpid="$(cat "$WORKER_PID_FILE" 2>/dev/null || true)"
        [ -n "$wpid" ] && kill "$wpid" 2>/dev/null || true
        rm -f "$WORKER_PID_FILE"
    fi
    # 兜底：按命令行精确找 worker.py 进程
    pkill -f "worker.py" 2>/dev/null || true
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
        if mysql_configured && ! worker_alive; then
            start_worker
        fi
        sleep 5
    done
}

case "$CMD" in
    start)
        print_banner
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
        if mysql_configured; then
            if worker_alive; then
                echo "[keepalive] worker: 运行中  PID: $(cat "$WORKER_PID_FILE")"
            else
                echo "[keepalive] worker: 未运行（守护会自动拉起）"
            fi
        else
            echo "[keepalive] worker: 未启用（未配置 IMG_SIEVE_MYSQL_HOST）"
        fi
        echo "[keepalive] 日志: $LOG_DIR/server.log , $LOG_DIR/keepalive.log , $LOG_DIR/worker.log"
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
        stop_worker
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
