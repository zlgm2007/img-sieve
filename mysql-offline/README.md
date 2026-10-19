# MySQL 5.7 离线安装包（CentOS 7.3 x86_64）

img-sieve 异步任务的持久化 MySQL。**只装在一台机器（机器 A）**，其余机器连它。

## 两种用法

### A. 你这边还没有 RPM —— 在一台「能上网的 CentOS 7」机器上下载

```bash
cd mysql-offline
sudo ./download.sh        # 把 MySQL 5.7.44 + 依赖下到 ./rpms/
```

然后把整个 `mysql-offline/` 目录（含 `rpms/`）拷到内网离线机器 A。

### B. 在内网离线机器 A 上安装

```bash
cd mysql-offline
# 建库建用户密码用环境变量传入（可选）：
sudo IMGSIEVE_DB_PASSWORD='一个强密码' ./install.sh
```

脚本会：卸 mariadb → 本地装 RPM → 启动 mysqld → 建库 `imgsieve` 和用户 `imgsieve`。

## 装完后机器 A 还要做 2 件事（让机器 B 能连）

1. `/etc/my.cnf` 的 `[mysqld]` 段加：
   ```ini
   bind-address = 0.0.0.0
   ```
   然后 `systemctl restart mysqld`。
2. 防火墙放行机器 B 的内网 IP：
   ```bash
   firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=机器B的IP port port=3306 protocol=tcp accept'
   firewall-cmd --reload
   ```

> 授权：`install.sh` 默认建用户 `'imgsieve'@'%'`（任意主机可连）。内网安全可改成机器 B 的具体 IP：`IMGSIEVE_FROMHOST=机器B的IP sudo ...`。

## 然后两台机器的 img-sieve 都配 .env

```bash
cp .env.example .env
# 编辑 .env，把 IMG_SIEVE_MYSQL_HOST 填成「机器 A 的内网 IP」
./keepalive.sh start --port 8080
```

机器 A 和机器 B 的 `.env` 只有 `HOST` 都指向机器 A，其余相同。
