# img-sieve — 抖音截图信息提取服务

从抖音截图中提取 **用户昵称 / 抖音号 / 手机号码**，输出结构化 JSON。

**核心约束：完全本地、内网离线部署，运行时零联网。** 不依赖任何云端 OCR API。
默认 OCR 引擎为 **RapidOCR（PP-OCR v4）**，模型随 pip 包内置、跨平台，中文识别质量高。

---

## 识别结果（样例）

```json
{
  "code": 0,
  "message": "ok",
  "backend": "rapidocr",
  "data": {
    "user": { "nickname": "叫我大猫", "platform": "douyin",
               "douyin_id": "jiaowodamao996", "xiaohongshu_id": null },
    "phone": "18652050583",
    "chat_object_name": "叫我大猫",
    "nickname_consistent": true,
    "nickname_check": {
      "user": "叫我大猫",
      "chat": "叫我大猫",
      "compare_chars": 5,
      "consistent": true
    }
  }
}
```

> **`data.nickname_consistent`**（boolean）：主页昵称与对话框对象昵称是否一致——
> `true` 一致、`false` 不一致、`null` 任一侧昵称缺失无法判断。
> 等价于 `data.nickname_check.consistent`，供调用方直接读取。
> 比对规则：前 **5 位前缀匹配**，可容忍昵称过长在某侧被截断显示。

`raw/` 内有 4 张测试样例，覆盖两种截图版式（主页/对话框）：

| 图片 | 角色 | 应提取 |
|---|---|---|
| `20261015115909_4787_84.jpg` | 主页 | 叫我大猫 / jiaowodamao996 |
| `20261015115910_4788_84.jpg` | 对话框 | 手机号 18652050583（`186 520 50 583`） |
| `20261015135527202.jpg` | 主页（他人视角版式） | 叫我大猫 / jiaowodamao996 |
| `20261015135547661.jpg` | 对话框（陌生人版式） | 手机号 18652050583（`18 6520 50 583`） |

---

## 目录结构

```
img-sieve/
├── raw/                        # 样例输入图
├── img_sieve/
│   ├── ocr.py                  # OCR 后端抽象：rapidocr(默认)/vision/tesseract/mock
│   ├── extractor.py            # 字段提取规则（昵称/抖音号/手机号）
│   └── service.py              # 核心编排：图 → OCR → 提取 → JSON
├── cli.py                      # 本地 CLI
├── server.py                   # HTTP 服务（纯 Python 标准库）
├── web/ui.html                 # 网页界面（GET / 托管，上传+展示）
├── fixtures/sample_ocr.json    # 权威 OCR 夹具（mock 测试用）
├── tests/test_extractor.py     # 单元测试
├── tessdata/                   # tesseract 备用中文语言包（可选）
├── wheels_linux_x86_64/        # Linux x86_64 wheel（Python 3.11，离线安装用）
├── pack.sh                     # 开发机打包脚本
├── build-linux-runtime.sh      # 开发机构建自包含 Linux 运行时（服务器无 Python 时用）
├── install.sh                  # 目标机离线安装（方案 B，有系统 Python 时）
├── run.sh                      # 目标机启动服务（自动选 runtime/.venv/.venv_test/python3）
└── build.sh                    # （可选）编译 Apple Vision 备用 OCR
```

---

## OCR 后端

| 后端 | 说明 | 中文质量 | 适用 |
|---|---|---|---|
| **rapidocr**（默认） | PP-OCR v4，模型随 pip 包内置 | 最佳 | **Linux/macOS 内网部署首选** |
| vision | Apple Vision（Swift 编译） | 最佳 | macOS 可选 |
| tesseract | tesseract 二进制 + 随包语言包 | 需 chi_sim | 备用 |
| mock | 读取夹具 JSON | — | 测试/无 OCR 依赖 |

后端自动选择：rapidocr 可用 → 用它；否则 vision/tesseract/mock。可用 `--backend` 强制指定。

**为什么选 RapidOCR**：模型（ONNX）内置在 wheel 里，离线安装后运行时零联网、零下载；
中文 UI / 图上文字识别质量远高于 tesseract（对本项目截图的昵称、抖音号实测准确）。

---

## Python 版本要求

- **应用代码**：Python 3.8+（纯标准库）。
- **OCR 依赖（rapidocr/onnxruntime/numpy/opencv 等）**：wheel 为 **cp311** 编译，**必须 Python 3.11 精确版本**。

服务器若**没有 Python**，用下方的「自包含运行时」方案——把 Linux 版 CPython 3.11 + 依赖全部打进包，
目标机无需任何系统 Python、无需联网安装。

## Linux 内网部署（推荐流程）

### 方案 A：服务器无 Python —— 自包含运行时（推荐）

在**可联网的开发机**上构建一次自包含 Linux 运行环境，随包部署后目标机零安装：

```bash
# 1) 开发机（有网络）：构建自包含 runtime/（下载可移植 CPython 3.11 + 离线装 rapidocr）
./build-linux-runtime.sh
# 2) 打包（runtime/ 会自动打入包内）
./pack.sh                      # → dist/img-sieve-linux-x86_64.tar.gz
```

内网目标机（无需系统 Python）：

```bash
tar -xzf img-sieve-linux-x86_64.tar.gz && cd img-sieve
./run.sh --host 0.0.0.0 --port 8080     # 直接用自带 runtime/bin/python3 运行
```

> `build-linux-runtime.sh` 从 python-build-standalone 下载可移植 CPython 3.11，
> 需开发机能访问 github；构建一次后 runtime/ 即可重复打包。

### 方案 B：目标机已装 Python 3.11

```bash
tar -xzf img-sieve-linux-x86_64.tar.gz && cd img-sieve
./install.sh         # 创建 .venv 并从本地 wheel 离线安装 rapidocr（全程无网络）
./run.sh --host 0.0.0.0 --port 8080
```

> **目标机要求：Python 3.11**（随包 wheel 为 cp311 构建；Debian 12 默认即 3.11）。
> 若目标机为 **arm64**：在开发机用 `pip download --dest wheels_linux_arm64 \
> --platform manylinux_2_17_aarch64 --python-version 311 --only-binary=:all: \
> rapidocr_onnxruntime` 准备 wheel，再 `./pack.sh arm64`。

---

## 使用

### 本地 CLI

```bash
# 用 venv（已装 rapidocr）运行，自动选后端
./.venv_test/bin/python cli.py raw/20261015115909_4787_84.jpg raw/20261015115910_4788_84.jpg

# 指定后端
python3 cli.py <user图> <chat图> --backend rapidocr|vision|tesseract|mock

# 显式指定角色（避免自动判断歧义）
python3 cli.py <user图> <chat图> --role <user图>=user --role <chat图>=chat
```

### HTTP 服务

```bash
./run.sh --host 0.0.0.0 --port 8080          # 目标机（venv）
python3 server.py --host 0.0.0.0 --port 8080 # 开发机（需已装 rapidocr）
```

接口：
- `GET /` — 上传 + 展示识别结果的**网页界面**（浏览器打开即可用，内联页面无外部资源）
- `POST /recognize` — multipart 上传 2 张图片，返回识别 JSON
  - 文件字段名任意，服务按内容自动识别角色（含"抖音号"→主页；含 11 位手机号→对话框）
  - 可选 `role=<字段名>:user|chat` 指定某字段角色
- `GET /health` — 健康检查

浏览器打开 `http://<服务地址>:8080/`，选择主页截图 + 对话框截图，点「开始识别」，页面上即展示昵称/抖音号/手机号。

```bash
curl -X POST http://127.0.0.1:8080/recognize \
  -F "user=@raw/20261015115909_4787_84.jpg" \
  -F "chat=@raw/20261015115910_4788_84.jpg"
```

---

## 测试

```bash
python3 -m tests.test_extractor          # 提取规则单测（权威 OCR 夹具）
```

---

## 提取规则（平台通用）

- **OCR**：一套 RapidOCR（PP-OCRv4）统一处理，抖音/小红书共用，无需分模型。
- **账号字段**：按平台匹配——抖音 `抖音号[:：]<字母数字>` → `douyin_id`；小红书 `小红书号[:：]<字母数字>` → `xiaohongshu_id`；兜底取声明行尾部字母数字串。平台按文本自动识别 `platform`。
- **用户昵称**：取账号声明行正上方、水平居中对齐的文本行；兜底取顶部区域最长有效文本（抖音/小红书同一套逻辑）。
- **手机号码**：按行合并文本 → 去除非数字 → 匹配大陆手机号 `^1[3-9]\d{9}$`
  （兼容 `186 520 50 583`、`18 6520 50 583` 这类被空格拆开的写法）。
- **对话框对象昵称**：取聊天窗口顶部状态栏下方、居中的短文本（排除系统横幅/状态/纯数字）。
- **昵称一致性**：主页昵称与对话框对象昵称做前 5 位前缀匹配（`nickname_check.consistent`）。
- **角色识别**：含账号声明（抖音号/小红书号）→ user；含 11 位手机号 → chat；可 `--role` 人工覆盖。

## 开发机本地验证

`./.venv_test` 是本仓库在开发机创建的测试 venv（已装 rapidocr）。如重建：

```bash
python3 -m venv .venv_test
./.venv_test/bin/pip install rapidocr_onnxruntime
```
