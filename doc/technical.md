# img-sieve 技术细节

本文档记录 img-sieve 的核心实现细节，供二次开发、排障与部署参考。
对外使用说明见 [README](../README.md)。

## 项目结构

```
img-sieve/
├── raw/                        # 样例输入图（仅测试用）
├── img_sieve/
│   ├── ocr.py                  # OCR 后端抽象：rapidocr(默认)/vision/tesseract/mock
│   ├── extractor.py            # 字段提取规则（昵称/抖音号/小红书号/手机号，平台通用）
│   └── service.py              # 核心编排：图 → OCR → 提取 → JSON
├── cli.py / server.py          # 本地 CLI / HTTP 服务（纯 Python 标准库，ThreadingHTTPServer）
├── web/ui.html                 # 网页界面（GET / 托管：上传主页图+对话框图，展示识别结果）
├── fixtures/sample_ocr.json    # 权威 OCR 夹具（mock 测试用）
├── tests/test_extractor.py     # 单元测试
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
中文 UI / 图上文字识别质量远高于 tesseract。**抖音、小红书共用一套 OCR**，按文本内容自动区分平台，无需分模型。

**性能调优（可选，默认不改行为）**：识别是 CPU 密集型，单次耗时主要取决于服务器算力。可在启动前用环境变量微调（不设则保持默认）：

- `IMG_SIEVE_OCR_THREADS=<N>`：onnxruntime 每算子线程数（默认自动）。实测对耗时影响很小，仅当 VM 多核且 onnxruntime 默认线程配置不佳时可尝试设为物理核数。
- `IMG_SIEVE_DET_LIMIT=<N>`：检测阶段长边缩放上限（默认 736）。调小可略降检测耗时，但可能漏识别小字，需用真实截图验证准确率后再用。

## 提取规则（平台通用）

- **账号字段**：按平台匹配——`抖音号[:：]<字母数字>` → `douyin_id`；`小红书号[:：]<字母数字>` → `xiaohongshu_id`；兜底取声明行尾部字母数字串。平台按文本自动识别 `platform`。
- **用户昵称**：取账号声明行正上方、水平居中对齐的文本行；兜底取顶部区域最长有效文本（抖音/小红书同一套逻辑）。
- **手机号码**：按行合并文本 → 去除非数字 → 匹配大陆手机号 `^1[3-9]\d{9}$`（兼容 `186 520 50 583`、`18 6520 50 583` 这类被空格拆开的写法）。
- **对话框对象昵称**：取聊天窗口顶部状态栏下方、居中的短文本（排除系统横幅/状态/纯数字）。
- **昵称一致性**：主页昵称与对话框对象昵称做**前 5 位前缀匹配**（`nickname_check.consistent`），容忍昵称过长在某侧被截断。
- **角色识别**：含账号声明（抖音号/小红书号）→ user；含 11 位手机号 → chat；可 `--role` 人工覆盖。

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
python3 -m tests.test_extractor          # 提取规则单测（权威 OCR 夹具）
```

**测试环境**：`http://10.237.179.21:8080/`（网页界面；接口 `POST /recognize`、`GET /health`）

