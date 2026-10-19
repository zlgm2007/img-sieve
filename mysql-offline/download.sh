#!/usr/bin/env bash
# 在「联网的 CentOS 7 机器」上执行一次，把 MySQL 5.7 及全部依赖 RPM 下载到 ./rpms/。
# 下载完成后把整个 mysql-offline/ 目录拷到内网离线机器 A 执行 install.sh。
set -eo pipefail
cd "$(dirname "$0")"
mkdir -p rpms
cd rpms

V="5.7.44-1.el7"
BASE="https://dev.mysql.com/get/Downloads/MySQL-5.7"
echo "[download] 下载 MySQL $V 核心 RPM ..."
for n in common libs client server; do
  f="mysql-community-$n-$V.x86_64.rpm"
  [ -s "$f" ] || curl -L --retry 3 -o "$f" "$BASE/$f"
done

echo "[download] 下载依赖（libaio/numactl/perl 等；本机已装会自动跳过）..."
# CentOS 7 官方源已 EOL，若 yum 报错先把 repo 切到 vault.centos.org：
#   sed -i 's|^mirrorlist=|#mirrorlist=|g; s|^#baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' /etc/yum.repos.d/CentOS-*.repo
yum install -y --downloadonly --downloaddir=. \
  libaio numactl perl perl-DBI perl-Data-Dumper net-tools || true

echo "[download] 完成。rpms/ 内容："
ls -lh
