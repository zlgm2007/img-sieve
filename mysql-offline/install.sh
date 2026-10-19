#!/usr/bin/env bash
# 在「离线 CentOS 7.3 机器 A（装 MySQL 的那台）」上执行。
#
# 你是普通用户 snsoadmin、没有 root 密码，所有 root 操作都走 sudo。
# 因此本脚本必须用 sudo 整体执行（脚本内的 yum/systemctl/mysql 都需要 root）：
#
#   方式一（推荐，直接传业务库密码）：
#     sudo IMGSIEVE_DB_PASSWORD='一个强密码' ./install.sh
#
#   方式二（先 export 再 -E 保留环境变量）：
#     export IMGSIEVE_DB_PASSWORD='一个强密码'
#     sudo -E ./install.sh
#
# 不传 IMGSIEVE_DB_PASSWORD 也能装完 MySQL，脚本会打印需要你手工执行的建库 SQL。
set -eo pipefail
cd "$(dirname "$0")"

# ---- 0) 必须以 root（sudo）运行 ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[mysql-install] 请用 sudo 运行本脚本："
    echo "    sudo IMGSIEVE_DB_PASSWORD='强密码' $0"
    exit 1
fi

echo "[mysql-install] 1/5 移除可能冲突的 mariadb-libs ..."
yum remove -y mariadb-libs 2>/dev/null || true

echo "[mysql-install] 2/5 本地安装全部 RPM（离线，不联网）..."
yum localinstall -y rpms/*.rpm

echo "[mysql-install] 3/5 配置开机自启并启动 mysqld ..."
systemctl enable mysqld
systemctl start mysqld
sleep 3
if ! systemctl is-active --quiet mysqld; then
    echo "[mysql-install] 错误：mysqld 未启动。请看日志： tail -50 /var/log/mysqld.log"
    exit 1
fi

TMPPW="$(grep 'temporary password' /var/log/mysqld.log | tail -1 | awk '{print $NF}')"
echo "[mysql-install] 4/5 mysqld 已启动。root 临时密码：$TMPPW"

DB="${IMG_SIEVE_MYSQL_DB:-imgsieve}"
DBUSER="${IMG_SIEVE_MYSQL_USER:-imgsieve}"
DBPASS="${IMGSIEVE_DB_PASSWORD:-}"
FROMHOST="${IMGSIEVE_FROMHOST:-%}"

echo "[mysql-install] 5/5 建库建用户 ..."
if [ -z "$DBPASS" ]; then
    echo "[mysql-install] 未传 IMGSIEVE_DB_PASSWORD，跳过自动建库。"
    echo "[mysql-install] 请用上面的临时密码登录后手工执行："
    echo
    echo "    mysql -uroot -p'$TMPPW' --connect-expired-password"
    echo "  然后执行："
    cat <<SQL
CREATE DATABASE $DB CHARACTER SET utf8mb4;
CREATE USER '$DBUSER'@'$FROMHOST' IDENTIFIED BY '改成强密码';
GRANT ALL ON $DB.* TO '$DBUSER'@'$FROMHOST';
FLUSH PRIVILEGES;
SQL
    echo
    echo "[mysql-install] 提示：首次用临时密码登录会被要求改 root 密码，按提示改即可。"
    exit 0
fi

# 用临时密码连库建业务库/用户（不自动改 root 密码，避免触发密码复杂度策略失败）
mysql -uroot -p"$TMPPW" --connect-expired-password <<SQL
CREATE DATABASE IF NOT EXISTS $DB CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS '$DBUSER'@'$FROMHOST' IDENTIFIED BY '$DBPASS';
GRANT ALL ON $DB.* TO '$DBUSER'@'$FROMHOST';
FLUSH PRIVILEGES;
SQL

echo "[mysql-install] 完成。业务库=$DB  用户=$DBUSER  允许来源=$FROMHOST"
echo "[mysql-install] 下一步（让机器 B 能连本机 MySQL）："
echo "    1) 编辑 /etc/my.cnf，在 [mysqld] 段加： bind-address=0.0.0.0"
echo "       然后 sudo systemctl restart mysqld"
echo "    2) 防火墙放行机器 B："
echo "       sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=机器B的内网IP port port=3306 protocol=tcp accept'"
echo "       sudo firewall-cmd --reload"
