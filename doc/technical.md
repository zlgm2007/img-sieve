# img-sieve 技术细节

本文档记录 img-sieve 的核心实现细节，供二次开发、排障与部署参考。
对外使用说明见 [README](../README.md)。

## 项目结构

```
img-sieve/
├── raw/                        # 样例输入图（仅测试用）
├── img_sieve/
│   ├── ocr.py                  # OCR 后端抽象：rapidocr(默认)/vision/tesseract/mock
│   ├── extractor.py            # 字段提取规则：人脉（昵称/平台账号/地区/手机号/标题昵称）+ 订单号列表
│   ├── service.py              # 核心编排：图 → OCR → 提取 → JSON（recognize / recognize_order 两条线）
│   ├── db.py                   # MySQL 任务队列：任务表、scene 场景、原子认领、图片 BLOB、历史清理
│   └── worker.py               # 异步 worker：轮询认领 pending 任务，按 scene 分流识别后写回
├── cli.py / server.py          # 本地 CLI（--mode people|order）/ HTTP 服务（纯 Python 标准库，ThreadingHTTPServer）
├── web/ui.html                 # 人脉识别网页（GET /：主页/联系人 + 对话框两张图，展示识别结果与任务记录）
├── web/order.html              # 订单识别网页（GET /order：单张订单列表页，展示订单号列表）
├── fixtures/sample_ocr.json    # 权威 OCR 夹具（mock 测试用）
├── tests/test_extractor.py     # 单元测试（抖音 / 小红书 / 微信 / 订单号）
├── tessdata/                   # tesseract 备用中文语言包（可选）
├── wheels_linux_x86_64/        # Linux x86_64 wheel（Python 3.11，离线安装用）
├── pack.sh                     # 开发机打包 → dist/*.tar.gz（自动包含 runtime/）
├── build-linux-runtime.sh      # 开发机构建自包含 Linux 运行时（支持 macOS/Linux）
├── install.sh                  # 目标机离线安装（方案 B，有系统 Python 时）
├── run.sh                      # 启动服务（自动选 runtime/.venv/.venv_test/python3）
├── stop.sh                     # 停止服务（按端口找 server.py 进程）
├── keepalive.sh                # 保活守护（崩溃自动重启，start/status/stop）
└── build.sh                    # （可选）编译 Apple Vision 备用 OCR
```

## OCR 后端

| 后端 | 说明 | 中文质量 | 适用 |
|---|---|---|---|
| **rapidocr**（默认） | PP-OCR v4，模型随 pip 包内置 | 最佳 | **Linux/macOS 内网部署首选** |
| vision | Apple Vision（Swift 编译） | 最佳 | macOS 可选 |
| tesseract | tesseract 二进制 + 随包语言包 | 需 chi_sim | 备用 |
| mock | 读取夹具 JSON | — | 测试 / 无 OCR 依赖 |

后端自动选择：rapidocr 可用 → 用它；否则 vision/tesseract/mock。可用 `--backend` 强制指定。

**为什么选 RapidOCR**：模型（ONNX）内置在 wheel 里，离线安装后运行时零联网、零下载；
中文 UI / 图上文字识别质量远高于 tesseract。**抖音、小红书、微信与订单列表共用一套 OCR**，按文本内容自动区分平台与业务线，无需分模型。

**性能调优（可选，默认不改行为）**：识别是 CPU 密集型，单次耗时主要取决于服务器算力。可在启动前用环境变量微调（不设则保持默认）：

- `IMG_SIEVE_OCR_THREADS=<N>`：onnxruntime 每算子线程数（默认自动）。实测对耗时影响很小，仅当 VM 多核且 onnxruntime 默认线程配置不佳时可尝试设为物理核数。
- `IMG_SIEVE_DET_LIMIT=<N>`：检测阶段长边缩放上限（默认 736）。调小可略降检测耗时，但可能漏识别小字，需用真实截图验证准确率后再用。

## 提取规则

### 人脉识别（抖音 / 小红书 / 微信）

- **平台识别**：文本含「抖音号」→ `douyin`；「小红书号」→ `xiaohongshu`；「微信号」或「地区：」行 → `wechat`。
- **账号字段**：按平台匹配声明行——`抖音号[:：]<字母数字>` → `douyin_id`；`小红书号[:：]…` → `xiaohongshu_id`；`微信号[:：]…` → `wechat_id`；声明行未匹配到时兜底取该行尾部字母数字串。
- **地区（仅微信）**：`地区[:：]<文本>` → `region`（如「江苏南京」），多空格归一为单空格。
- **用户昵称**：取账号声明行正上方、水平居中对齐的文本行；兜底取顶部区域（`y1 ≤ 400‰`）最长有效文本，排除导航 / 状态栏 / 纯数字 / 时间等噪声行。微信昵称**按完整串保留**（如「王云13951905083」），不在内部拆分手机号。
- **手机号码**：仅从对话框**消息体**提取——按行合并文本 → 去除非数字 → 匹配大陆手机号 `^1[3-9]\d{9}$`（兼容 `186 520 50 583`、`18 6520 50 583` 这类被空格拆开的写法）。顶部标题带（`y1 < 220‰`）整体跳过，避免把标题 / 昵称里内嵌的 11 位数字误判为消息手机号。
- **对话框对象昵称**：取聊天窗口顶部带（`y1` 在 40~220‰、水平居中 `cx` 在 150~850‰）的短文本（长度 2~20），排除系统横幅 / 状态 / 纯数字；先剥前导符号，中文昵称尾部被 OCR 误识的 1~2 个孤立拉丁字母 / 图标字符（如备注图标误识成 `Q`）一并剥除；同带取最靠上、跨度最大的一条。
- **昵称一致性**：主页昵称与对话框对象昵称做**前 5 位前缀匹配**（`nickname_check.consistent`），较短方是较长方前缀即视为一致，容忍昵称过长在某侧被截断；任一侧缺失返回 `null`。
- **角色识别**：含账号声明（抖音号 / 小红书号 / 微信号）或地区行 → `user`；含 11 位手机号 → `chat`；都不命中但顶部存在居中标题候选 → `chat`（覆盖微信对话框消息体不带手机号的情况）；`--role` 可人工覆盖。

### 订单识别（订单列表页，单图）

- **订单号锚定**：仅匹配 `订单号[:：]<10 位以上数字>` 前缀行；商品编码、SKU、水印昵称、日期等不带前缀的数字串不会混入。
- **提取顺序**：先逐条观察匹配；未命中再按行合并兜底（标签与数字被 OCR 拆成两条观察的情况）；结果按页面 `y1` 从上到下排序、去重保序；多张图（预留）跨图合并去重。
- **响应结构**：`{ "order_count": N, "order_ids": [...] }`；未识别到时 `order_count=0`、`order_ids=[]`。

### 两条业务线为何分开

人脉响应是「对象画像 + 跨图比对」结构（`user` / `phone` / `chat_object_name` / `nickname_check`），订单响应是扁平的订单号列表；两者入参形态（1~2 张配对图 vs 单张）、角色检测与跨图比对逻辑均不同，因此在 `service.py` 拆为 `recognize` 与 `recognize_order` 两条流水线，底层 OCR、multipart 解析、错误信封、临时文件清理、MySQL 记录复用。

## 任务队列与场景分流（MySQL）

- 任务表 `ocr_task` 含 `scene ENUM('people','order') NOT NULL DEFAULT 'people'`；老库由 `ensure_table()` 查 `information_schema.COLUMNS` 幂等 `ALTER TABLE` 补齐，存量任务默认 `people`，不影响历史记录。
- 写入口径：`/recognize` 同步落库 `scene=people`；`/recognize-order` 同步落库 `scene=order`；`/recognize/async`、`/recognize-order/async` 分别以对应 scene 创建 pending 任务。
- worker 认领任务后按 `task.scene` 分流：`order` → `recognize_order`，其余 → `recognize`；完成日志标注场景名。
- `/task/{id}` 响应体带 `scene`；`/admin/tasks` 列表同时返回 `scene` 与两张图片文件名（订单任务 `image2_name` 为 `null`，前端据此条件渲染第二张缩略图）。
- 队列机制沿用原设计：原子 `UPDATE ... WHERE status='pending'` 认领防重复、300 秒卡死回收、图片 LONGBLOB 入库、`/admin/purge` 按天清理。
- MySQL 连接参数全部走环境变量，缺省 `host=127.0.0.1`、`port=3306`、`user=root`、`password=`、`db=imgsieve`。部署时把参数写入项目根目录 `.env`：`IMG_SIEVE_MYSQL_HOST / PORT / USER / PASSWORD / DB`；`run.sh`、`keepalive.sh` 启动时 `set -a; . ./.env; set +a` 加载，`img_sieve/db.py` 另有零依赖兜底解析（已存在的系统环境变量优先，`.env` 只补缺省），保证裸跑 `python server.py` / `worker.py` 也能读到配置。`IMG_SIEVE_ADMIN_TOKEN` 仅用于 `/admin/purge` 鉴权，`/admin/tasks` 不校验。

## Python 版本要求

- **应用代码**：Python 3.8+（纯标准库）。
- **OCR 依赖**（rapidocr/onnxruntime/numpy/opencv 等）：wheel 为 **cp311** 编译，**必须 Python 3.11 精确版本**。

服务器若**没有 Python**，用「自包含运行时」方案——把 Linux 版 CPython 3.11 + 依赖全部打进包，目标机无需系统 Python、无需联网安装。

## 部署细节

### 方案 A：自包含运行时（服务器无 Python，推荐）

`build-linux-runtime.sh` 生成自包含 `runtime/`（Linux x86_64 CPython 3.11 + rapidocr），**支持在 Linux 或 macOS 上构建**：

- **Linux**：解压后直接运行 linux python 离线装 wheel。
- **macOS**：用本机 pip 跨平台（`--platform manylinux2014_x86_64`）把 linux wheel 装进解压的 linux python，无需执行 linux python——没有 Linux 也能在 Mac 上打包。

构建从 python-build-standalone 获取可移植 CPython 3.11，走国内镜像代理下载（无需梯子）；`pack.sh` 自动把 `runtime/` 打进部署包。

### 方案 B：目标机已装 Python 3.11

`install.sh` 创建 `.venv` 并从本地 wheel 离线安装 rapidocr（全程无网络）。

**arm64 目标机**：开发机准备 wheel 后 `./pack.sh arm64`：

```bash
pip download --dest wheels_linux_arm64 \
  --platform manylinux_2_17_aarch64 --python-version 311 --only-binary=:all: \
  rapidocr_onnxruntime
./pack.sh arm64
```

## 服务进程管理

- `run.sh` 启动：探测链 `runtime/bin/python3` → `.venv/bin/python` → `.venv_test/bin/python` → `python3`，取首个可导入 rapidocr 者。
- `stop.sh` 停止：按端口定位监听进程，仅关本服务的 `server.py`（优雅 TERM，超时 SIGKILL 兜底），不误杀同端口其他进程。
- `keepalive.sh` 保活：后台守护循环，服务进程退出后自动拉起；`stop` 对守护用 SIGKILL（无状态循环，安全）。

## 测试

```bash
# 提取规则单测（权威 OCR 夹具，无 OCR 依赖）
.venv_test/bin/python -m unittest tests.test_extractor -v
```

用例分组：

- `TestExtractor`：抖音 / 小红书主页字段、手机号、对话框标题、角色判定、昵称一致性。
- `TestWechatExtractor`：微信联系人字段（昵称 / 微信号 / 地区）、标题尾部误识字符清洗、角色判定、双图昵称一致且 `phone=null`。
- `TestOrderExtractor`：订单号按页面顺序抽取、`order_count` 计数、商品编码 / SKU / 水印不混入。

**测试环境**：`http://10.237.179.21:8080/`（人脉识别网页）、`http://10.237.179.21:8080/order`（订单识别网页）；接口 `POST /recognize`、`POST /recognize/async`、`POST /recognize-order`、`POST /recognize-order/async`、`GET /task/{task_id}`、`GET /admin/tasks`、`GET /health`。

