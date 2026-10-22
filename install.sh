#!/usr/bin/env bash
# 目标机（纯内网 Linux）环境准备。
# 场景 A：随包已含自包含 runtime/（build-linux-runtime.sh 产出）→ 无需系统 Python，直接可用。
# 场景 B：无 runtime/ 但有系统 Python 3.11 → 创建 .venv 并从本地 wheel 离线安装。
set -euo pipefail
cd "$(dirname "$0")"

# ---------- 命令行参数（自动化部署可跳过交互） ----------
MYSQL_HOST_SET=0
MYSQL_PORT_SET=0
MYSQL_USER_SET=0
MYSQL_PASSWORD_SET=0
MYSQL_DB_SET=0
MYSQL_HOST=""
MYSQL_PORT=""
MYSQL_USER=""
MYSQL_PASSWORD=""
MYSQL_DB=""
SKIP_MYSQL=0
NON_INTERACTIVE=0

usage() {
cat <<'EOF'
用法: ./install.sh [选项]

  --mysql-host HOST      MySQL 主机/IP（本机填 127.0.0.1，多机填 MySQL 机器内网 IP）
  --mysql-port PORT      MySQL 端口，默认 3306
  --mysql-user USER      数据库账号，默认 imgsieve
  --mysql-password PASS  数据库密码
  --mysql-db DB          数据库名，默认 imgsieve
  --skip-mysql           跳过 MySQL/.env 配置，仅安装同步识别环境
  --non-interactive      不进入交互式提问；未显式传配置时保留模板
  -h, --help             查看本帮助
EOF
}

need_value() {
  echo "[install] 错误: $1 需要值"
  usage
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mysql-host) [ $# -ge 2 ] || need_value "--mysql-host"; MYSQL_HOST="$2"; MYSQL_HOST_SET=1; shift 2 ;;
    --mysql-port) [ $# -ge 2 ] || need_value "--mysql-port"; MYSQL_PORT="$2"; MYSQL_PORT_SET=1; shift 2 ;;
    --mysql-user) [ $# -ge 2 ] || need_value "--mysql-user"; MYSQL_USER="$2"; MYSQL_USER_SET=1; shift 2 ;;
    --mysql-password) [ $# -ge 2 ] || need_value "--mysql-password"; MYSQL_PASSWORD="$2"; MYSQL_PASSWORD_SET=1; shift 2 ;;
    --mysql-db) [ $# -ge 2 ] || need_value "--mysql-db"; MYSQL_DB="$2"; MYSQL_DB_SET=1; shift 2 ;;
    --skip-mysql) SKIP_MYSQL=1; shift ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[install] 未知参数: $1"; usage; exit 2 ;;
  esac
done

# ---------- .env 读取 / 写入辅助 ----------
env_current() {
  local key="$1"
  local default="$2"
  local v
  v=$(awk -F= -v k="$key" 'index($0, k"=")==1 {sub(/^[^=]+=/,""); print; exit}' .env 2>/dev/null || true)
  v=${v#"\""}; v=${v%"\""}
  v=${v#"\'"}; v=${v%"\'"}
  if [ -n "$v" ]; then
    printf '%s' "$v"
  else
    printf '%s' "$default"
  fi
}

ensure_env_file() {
  if [ -f .env ]; then
    return
  fi
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "[install] 已从 .env.example 生成 .env。"
  else
    cat > .env <<'EOF'
IMG_SIEVE_MYSQL_HOST=127.0.0.1
IMG_SIEVE_MYSQL_PORT=3306
IMG_SIEVE_MYSQL_USER=imgsieve
IMG_SIEVE_MYSQL_PASSWORD=
IMG_SIEVE_MYSQL_DB=imgsieve
EOF
  fi
}

env_manual_hint() {
  echo ""
  echo "[install] 异步识别 / 任务记录需要 MySQL；仅跑同步识别可跳过本步。"
  echo "[install] 手动配置（部署目录 /opt/server/img-sieve）："
  echo "[install]   vi .env"
  echo "[install]   IMG_SIEVE_MYSQL_HOST=127.0.0.1"
  echo "[install]   IMG_SIEVE_MYSQL_PORT=3306"
  echo "[install]   IMG_SIEVE_MYSQL_USER=imgsieve"
  echo "[install]   IMG_SIEVE_MYSQL_PASSWORD=数据库密码"
  echo "[install]   IMG_SIEVE_MYSQL_DB=imgsieve"
  echo "[install] 配好后执行 ./keepalive.sh start（同时拉起 HTTP + worker）；仅同步可用 ./run.sh"
}

configure_env() {
  local PY="$1"
  ensure_env_file

  if [ "$SKIP_MYSQL" -eq 1 ]; then
    echo "[install] 已按要求跳过 MySQL 配置。"
    env_manual_hint
    return
  fi

  local cur_host cur_port cur_user cur_db cur_pass
  local reply
  cur_host=$(env_current IMG_SIEVE_MYSQL_HOST 127.0.0.1)
  cur_port=$(env_current IMG_SIEVE_MYSQL_PORT 3306)
  cur_user=$(env_current IMG_SIEVE_MYSQL_USER imgsieve)
  cur_db=$(env_current IMG_SIEVE_MYSQL_DB imgsieve)
  cur_pass=$(env_current IMG_SIEVE_MYSQL_PASSWORD "")

  # 显式参数优先；未传项沿用当前 .env 或模板默认值。
  [ "$MYSQL_HOST_SET" -eq 0 ] && MYSQL_HOST="$cur_host"
  [ "$MYSQL_PORT_SET" -eq 0 ] && MYSQL_PORT="$cur_port"
  [ "$MYSQL_USER_SET" -eq 0 ] && MYSQL_USER="$cur_user"
  [ "$MYSQL_DB_SET" -eq 0 ] && MYSQL_DB="$cur_db"
  if [ "$MYSQL_PASSWORD_SET" -eq 0 ]; then
    MYSQL_PASSWORD="$cur_pass"
  fi

  if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
    echo ""
    echo "[install] 异步识别 / 任务记录需要 MySQL；仅跑同步识别可跳过。"
    read -r -p "是否现在配置 MySQL？[Y/n] " reply
    reply=${reply:-Y}
    if [[ "$reply" =~ ^[Nn] ]]; then
      echo "[install] 跳过 MySQL 配置，稍后可手动编辑 .env。"
      env_manual_hint
      return
    fi

    read -r -p "MySQL 服务 IP/主机（本机填 127.0.0.1；多机部署填 MySQL 所在机器内网 IP）[${cur_host}]: " reply
    MYSQL_HOST=${reply:-$cur_host}
    read -r -p "MySQL 端口 [${cur_port}]: " reply
    MYSQL_PORT=${reply:-$cur_port}
    read -r -p "数据库账号 [${cur_user}]: " reply
    MYSQL_USER=${reply:-$cur_user}
    read -r -p "数据库名 [${cur_db}]: " reply
    MYSQL_DB=${reply:-$cur_db}

    echo "数据库密码（直接回车保留当前密码；本机无密码可留空）："
    read -r -s -p "密码: " MYSQL_PASSWORD
    echo ""
    # 非显式传参时，回车表示保留旧密码；显式 --mysql-password '' 才写入空密码。
    if [ "$MYSQL_PASSWORD_SET" -eq 0 ] && [ -z "$MYSQL_PASSWORD" ]; then
      MYSQL_PASSWORD="$cur_pass"
    fi
  else
    # 非交互环境且未显式传入主机/密码：不改写模板，保留手动配置说明。
    if [ "$MYSQL_HOST_SET" -eq 0 ] && [ "$MYSQL_PASSWORD_SET" -eq 0 ]; then
      echo "[install] 非交互环境，未自动修改 MySQL 配置。"
      env_manual_hint
      return
    fi
  fi

  case "$MYSQL_HOST" in ''|*[[:space:]]*) echo "[install] 错误：MySQL 主机不能为空且不能包含空格。"; exit 1 ;; esac
  case "$MYSQL_PORT" in ''|*[!0-9]*) echo "[install] 错误：MySQL 端口必须为数字。"; exit 1 ;; esac
  if [ "$MYSQL_PORT" -lt 1 ] || [ "$MYSQL_PORT" -gt 65535 ]; then
    echo "[install] 错误：MySQL 端口范围为 1~65535。"
    exit 1
  fi
  case "$MYSQL_USER" in ''|*[[:space:]]*) echo "[install] 错误：数据库账号不能为空且不能包含空格。"; exit 1 ;; esac
  case "$MYSQL_DB" in ''|*[[:space:]]*) echo "[install] 错误：数据库名不能为空且不能包含空格。"; exit 1 ;; esac

  IMG_SIEVE_WRITE_HOST="$MYSQL_HOST" \
  IMG_SIEVE_WRITE_PORT="$MYSQL_PORT" \
  IMG_SIEVE_WRITE_USER="$MYSQL_USER" \
  IMG_SIEVE_WRITE_PASSWORD="$MYSQL_PASSWORD" \
  IMG_SIEVE_WRITE_DB="$MYSQL_DB" \
  "$PY" - <<'PY'
import os
path = ".env"
updates = {
    "IMG_SIEVE_MYSQL_HOST": os.environ["IMG_SIEVE_WRITE_HOST"],
    "IMG_SIEVE_MYSQL_PORT": os.environ["IMG_SIEVE_WRITE_PORT"],
    "IMG_SIEVE_MYSQL_USER": os.environ["IMG_SIEVE_WRITE_USER"],
    "IMG_SIEVE_MYSQL_PASSWORD": os.environ["IMG_SIEVE_WRITE_PASSWORD"],
    "IMG_SIEVE_MYSQL_DB": os.environ["IMG_SIEVE_WRITE_DB"],
}

def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

try:
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    lines = []

out = []
seen = set()
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f'{key}="{escape(updates[key])}"')
            seen.add(key)
            continue
    out.append(line)

for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}="{escape(value)}"')

with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
PY

  echo ""
  echo "[install] .env 已写入 MySQL 配置："
  echo "[install]   host=${MYSQL_HOST}  port=${MYSQL_PORT}  user=${MYSQL_USER}  db=${MYSQL_DB}（密码已隐藏）"
  echo "[install] 如需调整端口、账号或库名，直接编辑 .env。"
  echo "[install] 配置完成后执行 ./keepalive.sh start（同时拉起 HTTP + worker）；仅同步可用 ./run.sh。"
}

# ---------- 场景 A：自包含 runtime ----------
if [ -x runtime/bin/python3 ]; then
  echo "[install] 检测到自包含运行时 runtime/，无需系统 Python。"
  if runtime/bin/python3 -c "import rapidocr_onnxruntime" 2>/dev/null; then
    echo "[install] rapidocr 就绪。"
    configure_env runtime/bin/python3
    exit 0
  fi

  echo "[install] runtime/ 存在但缺少 rapidocr，尝试用随包 wheel 补装..."
  if [ -d wheels_linux_x86_64 ]; then
    runtime/bin/python3 -m pip install --no-index \
        --find-links wheels_linux_x86_64 rapidocr_onnxruntime
    echo "[install] 补装完成。"
    configure_env runtime/bin/python3
    exit 0
  fi

  echo "[install] 缺少 wheels_linux_x86_64/，无法补装。"
  exit 1
fi

# ---------- 场景 B：系统 Python 3.11 ----------
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
configure_env ./.venv/bin/python
echo "[install] 完成。"
