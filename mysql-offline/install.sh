#!/usr/bin/env bash
# 在「离线 CentOS 7.3 目标机（机器 A，装 MySQL 的那台）」上以 root 执行。
# 用法：
#   sudo IMGSIEVE_DB_PASSWORD='强密码' ./install.sh
# 不传密码则只完成安装启动，打印后续 SQL 由你手工执行。
set -eo pipefail
cd "$(dirname "$0")"

echo "[mysql-install] 1/5 移除可能冲突的 mariadb-libs ..."
yum remove -y mariadb-libs 2>/dev/null || true

echo "[mysql-install] 2/5 本地安装全部 RPM ..."
yum localinstall -y rpms/*.rpm

echo "[mysql-install] 3/5 配置开机自启并启动 ..."
systemctl enable mysqld
systemctl start mysqld
sleep 3
systemctl is-active --quiet mysqld || { echo "[mysql-install] mysqld 未启动，请看 /var/log/mysqld.log"; exit 1; }

TMPPW="$(grep 'temporary password' /var/log/mysqld.log | tail -1 | awk '{print $NF}')"
echo "[mysql-install] 4/5 安装完成。root 临时密码: $TMPPW"

echo "[mysql-install] 5/5 建库建用户 ..."
DB="${IMG_SIEVE_MYSQL_DB:-imgsieve}"
DBUSER="${IMG_SIEVE_MYSQL_USER:-imgsieve}"
DBPASS="${IMGSIEVE_DB_PASSWORD:-}"
FROMHOST="${IMGSIEVE_FROMHOST:-%}"

if [ -z "$DBPASS" ]; then
    echo "[mysql-install] 未传 IMGSIEVE_DB_PASSWORD，跳过自动建库。请用 root 临时密码登录后执行："
    cat <<SQL
mysql -uroot -p'$TMPPW' --connect-expired-password <<'EOF'
ALTER USER 'root'@'localhost' IDENTIFIED BY '改成你自己的root密码';
CREATE DATABASE $DB CHARACTER SET utf8mb4;
CREATE USER '$DBUSER'@'$FROMHOST' IDENTIFIED BY '改成强密码';
GRANT ALL ON $DB.* TO '$DBUSER'@'$FROMHOST';
FLUSH PRIVILEGES;
EOF
SQL
    exit 0
fi

mysql -uroot -p"$TMPPW" --connect-expired-password <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY 'Root_\$(openssl rand -hex 4)';
CREATE DATABASE IF NOT EXISTS $DB CHARACTER SET utf8mb4;
CREATE USER '$DBUSER'@'$FROMHOST' IDENTIFIED BY '$DBPASS';
GRANT ALL ON $DB.* TO '$DBUSER'@'$FROMHOST';
FLUSH PRIVILEGES;
SQL

echo "[mysql-install] 完成。库=$DB 用户=$DBUSER 来源=$FROMHOST"
echo "[mysql-install] 记得：/etc/my.cnf 设 bind-address=0.0.0.0 并放行防火墙 3306，才允许机器 B 连接。"
