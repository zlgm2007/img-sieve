# img-sieve — 抖音 / 小红书 / 微信 人脉识别 + 电商订单号识别服务

两条独立业务线，共享同一套 RapidOCR 本地离线引擎：

- **人脉识别**：上传「主页 / 联系人」+「消息对话框」两张截图，自动提取昵称、抖音号 / 小红书号 / 微信号、地区、手机号，并核对两处昵称是否一致。
- **订单识别**：上传一张订单截图，支持订单列表页、手工票据、订单详情页、物流页、报表页与手机实拍图，按阅读顺序返回统一的订单号列表。

本地部署、**纯内网离线运行、图片不出内网**，不依赖任何云端 OCR。

## 核心亮点

- **支持抖音 + 小红书 + 微信**：一套 RapidOCR（PP-OCR v4）自动区分平台与场景，无需分别部署
- **订单号锚定抽取**：标签兼容 `订单号：` / `订单编号：` / `订单编码：` / `订单号码：` / `OMS单号：`（冒号可有可无），值兼容纯数字、字母前缀型（`B5002436100`）与分组写法；商品编码 / SKU / 运单号 / 金额不混入
- **截图与实拍通吃**：标签与编号错行、手机拍照透视倾斜、票据横放倒置均有兜底
- **中文识别准**：RapidOCR 内置 ONNX 模型，昵称 / 账号 / 手机号 / 订单号实测准确
- **同步 + 异步**：同步阻塞接口；配 MySQL 后支持任务队列、后台 worker 识别、多机共享
- **零联网**：运行时全程无网络请求，适合纯内网
- **开箱即用**：自包含 Linux 运行时，服务器无需安装 Python

---

## 快速使用（网页界面，最常用）

```
./run.sh --host 0.0.0.0 --port 8080   # 仅同步识别：启动服务
```

> 异步识别、任务记录、后台 worker 需要 MySQL：先在项目根目录配置 `.env`（见下文「异步识别 / 多机部署」），再用 `./keepalive.sh start` 启动。

浏览器打开三个独立页面，左侧菜单可互相跳转：一级菜单为人脉识别、订单识别、任务记录；人脉 / 订单子菜单为同步识别、异步识别，任务记录子菜单为任务记录、统计。

| 页面 | 地址 | 上传内容 |
| --- | --- | --- |
| **人脉识别** | `http://<服务地址>:8080/` | 主页 / 联系人截图 + 对话框截图两张图 |
| **订单识别** | `http://<服务地址>:8080/order` | 一张订单截图（列表页 / 票据 / 详情页 / 物流页 / 实拍图） |
| **识别统计** | `http://<服务地址>:8080/stats` | 近 7 天人脉 / 订单按日汇总柱状图 |

人脉页展示昵称、抖音号 / 小红书号 / 微信号、地区、手机号，以及两处昵称是否一致；订单页展示识别到的订单号总数与订单号列表。

- 人脉样例：抖音「叫我大猫 / jiaowodamao996 / 手机号」昵称一致 ✅；小红书「滴滴嗒哒 / 小红书号」与对话框「予行记」昵称不一致。
- 订单样例：列表页一张图识别出 4 个订单号；手工票据取 `OMS单号：` 后编码，详情页取 `订单编号：` 后编码，均返回长度为 1 的 `order_ids` 数组；手机实拍的「订单号码」无冒号、编号分组显示，也能抽出 `3802223132735788788`。

> 网页为单文件自包含（内联样式与脚本，无外部资源）。主要接口：  
> `GET /` 人脉识别网页・`GET /order` 订单识别网页・`GET /stats` 统计网页・`POST /recognize` 人脉同步・`POST /recognize/async` 人脉异步・`POST /recognize-order` 订单同步・`POST /recognize-order/async` 订单异步・`GET /task/{task_id}` 任务查询・`GET /admin/stats` 统计聚合・`GET /health` 健康检查

---

## 本地 CLI

```
# 人脉识别（默认 people）：主页/联系人图 + 对话框图，自动识别角色与平台
./.venv_test/bin/python cli.py raw/20261015135527202.jpg raw/20261015135547661.jpg

# 微信联系人页 + 对话框
./.venv_test/bin/python cli.py raw/wx20261022094534150.jpg raw/wx20261022094522060.jpg

# 订单识别（order）：订单列表页 / 手工票据 / 订单详情页 / 物流页 / 手机实拍图，均为单张图
./.venv_test/bin/python cli.py --mode order raw/order20261022094541207.jpg
./.venv_test/bin/python cli.py --mode order raw/orderA20261022150516331.jpg
./.venv_test/bin/python cli.py --mode order raw/orderB20261022150541507.jpg
./.venv_test/bin/python cli.py --mode order raw/orderD20261029101319607.jpg   # 实拍，订单号码无冒号

# 指定后端 / 显式指定角色（仅人脉模式）
python3 cli.py <主页图> <对话框图> --backend rapidocr|vision|tesseract|mock
python3 cli.py <主页图> <对话框图> --role <主页图>=user --role <对话框图>=chat
```

---

## 识别结果（样例）

**人脉识别 —— 抖音：**

```
{
  "code": 0,
  "message": "ok",
  "backend": "rapidocr",
  "data": {
    "user": {
      "nickname": "叫我大猫",
      "platform": "douyin",
      "douyin_id": "jiaowodamao996",
      "xiaohongshu_id": null,
      "wechat_id": null,
      "region": null
    },
    "phone": "18*********",
    "chat_object_name": "叫我大猫",
    "nickname_consistent": true,
    "nickname_check": {
      "user": "叫我大猫", "chat": "叫我大猫",
      "compare_chars": 5, "consistent": true
    }
  }
}
```

**人脉识别 —— 微信：**

```
{
  "code": 0,
  "message": "ok",
  "backend": "rapidocr",
  "data": {
    "user": {
      "nickname": "王云13951905083",
      "platform": "wechat",
      "douyin_id": null,
      "xiaohongshu_id": null,
      "wechat_id": "Z071216xy",
      "region": "江苏南京"
    },
    "phone": null,
    "chat_object_name": "王云13951905083",
    "nickname_consistent": true,
    "nickname_check": {
      "user": "王云13951905083", "chat": "王云13951905083",
      "compare_chars": 5, "consistent": true
    }
  }
}
```

**订单识别：**

```
{
  "code": 0,
  "message": "ok",
  "backend": "rapidocr",
  "data": {
    "order_count": 4,
    "order_ids": [
      "210100027596213478",
      "210100027600353478",
      "210100027598393478",
      "210100027600333478"
    ],
    "order_items": [
      {"label": "订单号", "order_id": "210100027596213478"},
      {"label": "订单号", "order_id": "210100027600353478"},
      {"label": "订单号", "order_id": "210100027598393478"},
      {"label": "订单号", "order_id": "210100027600333478"}
    ]
  }
}
```

> 人脉：`data.nickname_consistent` 为主页昵称与对话框对象昵称是否一致（`true/false/null`），等价于 `data.nickname_check.consistent`；按前 **5 位前缀匹配**，容忍昵称过长被截断。平台账号按 `user.platform` 落到对应字段：抖音 `douyin_id`、小红书 `xiaohongshu_id`、微信 `wechat_id` + `region`。
>
> 订单：`data.order_count` 为订单号数量，`data.order_ids` 为按页面阅读顺序排列、去重后的订单号列表；列表页可多个，票据 / 详情页通常为 1 个；未识别到时 `order_count=0`、`order_ids=[]`。`data.order_items` 为可选辅助字段，额外给出每个单号的来源标签（`label` / `order_id`）。标签口径：`订单号：`、`订单编号：`、`订单编码：`、`订单号码：`、`OMS单号：`（兼容 OCR 误识的 `QMS单号：`），冒号可有可无；编号兼容纯数字 10~20 位、字母前缀型（如 `B5002436100`）、分组写法（`3802 2231 3273 5788 788`）。

---

## 服务生命周期

```
./run.sh --host 0.0.0.0 --port 8080   # 启动服务（前台）
./stop.sh [--port 8080]              # 停止服务
./keepalive.sh start [--port 8080]   # 拉起服务 + 保活守护（后台、登出不掉线、崩溃自动重启）
./keepalive.sh status [--port 8080]  # 查看状态
./keepalive.sh stop   [--port 8080]  # 停止守护并关闭服务
```

---

## 异步识别 / 多机部署（MySQL 持久化）

单台机器跑同步识别用不上 MySQL；要做异步识别（先返回 taskId、后台识别再查询结果）和多机共享任务时，才需要 MySQL。人脉识别（`/recognize/async`）与订单识别（`/recognize-order/async`）共用同一张任务表，任务带 `scene` 字段（`people` / `order`），worker 按场景自动分流到对应识别流水线。

拓扑：MySQL 只装在一台机器（下称机器 A，如 `10.237.179.21`），机器 A 和其它机器（B、C…）都同时跑 HTTP + worker，连同一个库。

### 1. 安装 MySQL 5.7（仅机器 A）

用随包的离线安装包（CentOS 7）：

```bash
tar -xzf mysql-offline-centos7.tar.gz
cd mysql-offline
sudo IMGSIEVE_DB_PASSWORD='业务库强密码' ./install.sh
```

脚本会自动装 RPM、启动 mysqld、建库 `imgsieve` 和用户 `imgsieve`。装完还需让别的机器能连：

```bash
# /etc/my.cnf 的 [mysqld] 段加一行，然后重启
#   bind-address=0.0.0.0
sudo systemctl restart mysqld

# 放行机器 B 的内网 IP
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=机器B的IP port port=3306 protocol=tcp accept'
sudo firewall-cmd --reload
```

> MySQL 5.7 默认密码策略：密码须大写 + 小写 + 数字 + 特殊符号、长度 ≥ 8。

### 2. 配置 .env（每台机器都要，内容一致）

执行 `./install.sh` 时，脚本会自动把 `.env.example` 复制为 `.env`（已存在则不覆盖），并在终端交互询问：

- **MySQL 服务 IP/主机**：本机 MySQL 填 `127.0.0.1`；机器 B/C 填机器 A 的内网 IP（如 `10.237.179.21`）。
- **数据库密码**：输入时不回显；直接回车保留 `.env` 中已有密码，本机无密码可留空。
- 端口、账号、库名默认 `3306` / `imgsieve` / `imgsieve`，可在 `.env` 中调整。

自动化部署也可跳过交互直接传入：

```bash
cd /opt/server/img-sieve
./install.sh --mysql-host 10.237.179.21 --mysql-password '业务库强密码'
# 仅跑同步识别、跳过 MySQL 配置：
./install.sh --skip-mysql
```

交互时选择跳过，或需要手动修改时，进入部署目录编辑：

```bash
cd /opt/server/img-sieve
vi .env
```

按实际 MySQL 连接填写：

```bash
IMG_SIEVE_MYSQL_HOST=127.0.0.1
IMG_SIEVE_MYSQL_PORT=3306
IMG_SIEVE_MYSQL_USER=imgsieve
IMG_SIEVE_MYSQL_PASSWORD=数据库密码
IMG_SIEVE_MYSQL_DB=imgsieve
```

- 机器 A（本机有 MySQL）填 `127.0.0.1`；机器 B/C 填机器 A 的内网 IP（如 `10.237.179.21`）。
- 密码就是安装 MySQL 时 `IMGSIEVE_DB_PASSWORD` 设的那个，**所有机器必须一致**。
- 系统环境变量优先级高于 `.env`；`run.sh`、`keepalive.sh` 启动时都会加载本文件。
- 配好后用 `./keepalive.sh start` 启动，它会自动同时拉起 HTTP 和 worker；不配 `IMG_SIEVE_MYSQL_HOST` 则只跑 HTTP、不跑 worker。

```bash
./keepalive.sh start
./keepalive.sh status    # 能看到 服务/守护/worker 三个进程
tail -f logs/worker.log  # 异步识别日志
```

### 3. crontab 定时任务（开机自启 + 每日清理）

`keepalive.sh start` 本身是常驻守护循环（每 5 秒自检、崩溃自动拉起），**不用 cron 周期调用**。只需要两条 cron：

```bash
crontab -e
```

加入：

```cron
# 开机后自动拉起 img-sieve（HTTP + worker + 保活守护）
@reboot /opt/server/img-sieve/keepalive.sh start >> /opt/server/img-sieve/logs/cron.log 2>&1

# 每天凌晨 3 点清理 7 天前的历史任务和图片
0 3 * * * curl -s -X POST 'http://127.0.0.1:8080/admin/purge?days=7' >> /opt/server/img-sieve/logs/cron.log 2>&1
```

- `@reboot`：服务器重启后自动拉起服务，不用登录手动启动；keepalive.sh 自己会 `source .env`，worker 也能拿到 MySQL 配置。
- 第二条：每天清理 7 天前的任务记录和图片 BLOB，避免 MySQL 越来越大。若设了 `IMG_SIEVE_ADMIN_TOKEN`，URL 后加 `&token=xxx`。
- 验证：`crontab -l` 能看到这两条；输出在 `logs/cron.log`。

---

## CentOS 7 环境适配说明（依赖版本锁定）

目标部署机多为 CentOS 7 等老系统（GLIBC 2.17），只能使用 `manylinux_2_17` 的 wheel。为在该环境下稳定运行，以下依赖版本被**锁定**（锁定逻辑内置在 `build-linux-runtime.sh` 的自动下载里；`wheels_linux_x86_64/` 不入 git，联网按需下载）：

| 依赖                     | 锁定版本          | 说明                                                                         |
| ---------------------- | ------------- | -------------------------------------------------------------------------- |
| onnxruntime            | **1.16.3**    | `manylinux_2_17` 下 pip 可解析到的最高版本；由 NumPy 1.x 编译                            |
| numpy                  | **1.26.4**    | 必须 <2，与 onnxruntime 1.16.3 的 C-API 兼容                                      |
| opencv-python-headless | **4.10.0.84** | 用 headless 替代官方 opencv-python：服务器无显示环境；且 5.x 强制要求 NumPy≥2，与 onnxruntime 冲突 |
| rapidocr_onnxruntime   | **1.4.4**     | 其依赖声明已由 `opencv-python` 改写为 `opencv-python-headless`                       |

> **不要自行升级以上版本**，否则会触发兼容问题：
>
> - `onnxruntime` 升到 1.19+：不再提供 `manylinux_2_17` 轮子（改用更高的 manylinux/GLIBC 标签），CentOS 7 无法加载。
> - `numpy` 升到 2.x：onnxruntime 1.16.3 崩溃，报 `AttributeError: _ARRAY_API not found` / `numpy.core.multiarray failed to import`。
> - 用官方 `opencv-python`（非 headless）：服务器缺 Qt/X11 系统库，报 `ImportError: libxcb.so.1: cannot open shared object file`。
>
> 若目标机确为 Ubuntu 等新系统（GLIBC≥2.28），可放宽为 `numpy>=2` + `opencv-python-headless`(5.x) + 新版 onnxruntime。

## 部署/构建注意事项（macOS → CentOS 7）

- **打包**：`pack.sh` 已剥离 macOS 扩展属性（`--no-xattrs --no-fflags`）。若不剥离，包内会携带  
  `LIBARCHIVE.xattr.com.apple.provenance` 等 PAX 扩展头，CentOS 老版 GNU tar 解压时误解析，  
  导致 `-coloredlogs` 之类的前导 `-` 目录名、甚至文件内容字节错位。
- **构建**：`build-linux-runtime.sh` 在 `wheels_linux_x86_64/` 缺失/为空时会联网自动下载，并完成上述  
  版本锁定与 headless 切换；纯内网机器可先在联网机器上执行 `pip download` 填充该目录，再拷进内网离线构建。
- **构建缓存**：CPython 基础包缓存在 `cache/`（带 gzip 完整性校验），二次构建检测到本地完好即跳过联网解析与下载；  
  wheel 缓存在 `wheels_linux_x86_64/`。两个目录均不入 git。需要强制拉取最新 CPython 时执行  
  `./build-linux-runtime.sh --refresh`（或 `PYBS_FORCE_REFRESH=1 ./build-linux-runtime.sh`）。

---

## 附录

- 核心技术细节（OCR 后端、提取规则、项目结构、Python 版本、部署细节、服务进程管理、测试）：**[doc/technical.md](doc/technical.md)**
- HTTP 接口对接（/recognize、/recognize-order 入参、出参、错误码、异步任务）：**[doc/http-api.md](doc/http-api.md)**
