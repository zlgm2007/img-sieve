# img-sieve — 抖音 / 小红书 截图信息提取服务

**从用户主页截图提取昵称和平台账号号（抖音号 / 小红书号），从消息对话框截图提取手机号，并核对两处昵称是否一致，输出结构化 JSON。**

> 适用场景（用户群较窄）：面向需要核验「用户主页身份 + 对话框手机号」的特定人工流程（如运营、审核、客服核对），
> 本地部署、
>
> **纯内网离线运行、图片不出内网**
>
> ，不依赖任何云端 OCR。

**核心约束：完全本地、内网离线部署，运行时零联网。** 默认 OCR 引擎为 **RapidOCR（PP-OCR v4）**，模型随 pip 包内置、跨平台，中文识别质量高。



***

## 识别结果（样例）

抖音（主页「叫我大猫」+ 对话框「叫我大猫」）：



```
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
      "user": "叫我大猫", "chat": "叫我大猫",
      "compare_chars": 5, "consistent": true
    }
  }
}
```

小红书（主页「滴滴嗒哒」+ 对话框「多金小肉肉」，昵称不一致）：



```
{
  "data": {
    "user": { "nickname": "滴滴嗒哒", "platform": "xiaohongshu",
               "douyin_id": null, "xiaohongshu_id": "1346204393" },
    "phone": "15736177791",
    "chat_object_name": "多金小肉肉",
    "nickname_consistent": false,
    "nickname_check": {
      "user": "滴滴嗒哒", "chat": "多金小肉肉",
      "compare_chars": 5, "consistent": false
    }
  }
}
```

> `data.nickname_consistent`
>
> （boolean）：主页昵称与对话框对象昵称是否一致 ——
> `true`
>
>  一致、
>
> `false`
>
>  不一致、
>
> `null`
>
>  任一侧昵称缺失无法判断。等价于 
>
> `data.nickname_check.consistent`
>
> 。
> 比对规则：
>
> **前 5 位前缀匹配**
>
> ，可容忍昵称过长在某侧被截断显示。

`raw/` 内含若干抖音、小红书的主页/对话框测试样例，仅用于自测。



***

## 目录结构



```
img-sieve/
├── raw/                        # 样例输入图
├── img_sieve/
│   ├── ocr.py                  # OCR 后端抽象：rapidocr(默认)/vision/tesseract/mock
│   ├── extractor.py            # 字段提取规则（昵称/抖音号/小红书号/手机号，平台通用）
│   └── service.py              # 核心编排：图 → OCR → 提取 → JSON
├── cli.py                      # 本地 CLI
├── server.py                   # HTTP 服务（纯 Python 标准库，ThreadingHTTPServer）
├── web/ui.html                 # 网页界面（GET / 托管：上传主页图+对话框图，展示识别结果）
├── fixtures/sample_ocr.json    # 权威 OCR 夹具（mock 测试用）
├── tests/test_extractor.py     # 单元测试
├── tessdata/                   # tesseract 备用中文语言包（可选）
├── wheels_linux_x86_64/        # Linux x86_64 wheel（Python 3.11，离线安装用）
├── pack.sh                     # 开发机打包脚本 → dist/*.tar.gz
├── build-linux-runtime.sh      # 开发机构建自包含 Linux 运行时（服务器无 Python 时用，支持 macOS/Linux）
├── install.sh                  # 目标机离线安装（方案 B，有系统 Python 时）
├── run.sh                      # 启动服务（自动选 runtime/.venv/.venv_test/python3）
├── stop.sh                     # 停止服务（按端口找 server.py 进程，优雅关闭）
├── keepalive.sh                # 保活守护（服务崩溃自动重启，start/status/stop）
└── build.sh                    # （可选）编译 Apple Vision 备用 OCR
```



***

## OCR 后端



| 后端               | 说明                     | 中文质量       | 适用                     |
| ---------------- | ---------------------- | ---------- | ---------------------- |
| **rapidocr**（默认） | PP-OCR v4，模型随 pip 包内置  | 最佳         | **Linux/macOS 内网部署首选** |
| vision           | Apple Vision（Swift 编译） | 最佳         | macOS 可选               |
| tesseract        | tesseract 二进制 + 随包语言包  | 需 chi\_sim | 备用                     |
| mock             | 读取夹具 JSON              | —          | 测试 / 无 OCR 依赖          |

后端自动选择：rapidocr 可用 → 用它；否则 vision/tesseract/mock。可用 `--backend` 强制指定。

**为什么选 RapidOCR**：模型（ONNX）内置在 wheel 里，离线安装后运行时零联网、零下载；

中文 UI / 图上文字识别质量远高于 tesseract。**抖音、小红书共用一套 OCR**，按文本内容自动区分平台，

无需为平台分别部署模型。



***

## Python 版本要求



* **应用代码**：Python 3.8+（纯标准库）。

* **OCR 依赖（rapidocr/onnxruntime/numpy/opencv 等）**：wheel 为 **cp311** 编译，**必须 Python 3.11 精确版本**。

服务器若**没有 Python**，用下方的「自包含运行时」方案 —— 把 Linux 版 CPython 3.11 + 依赖全部打进包，

目标机无需任何系统 Python、无需联网安装。

## Linux 内网部署（推荐流程）

### 方案 A：服务器无 Python —— 自包含运行时（推荐）

`build-linux-runtime.sh` 生成自包含的 `runtime/`（Linux x86\_64 CPython 3.11 + rapidocr），

目标机无需系统 Python、无需联网安装。**支持在 Linux 或 macOS 上构建**：



* **Linux**：解压后直接运行 linux python 离线装 wheel。

* **macOS**：用本机 pip 跨平台（`--platform manylinux2014_x86_64`）把 linux wheel 装进解压的

  linux python，无需执行 linux python——**没有 Linux 也能在 Mac 上打包**。



```
# 1) 开发机（macOS 或 Linux，有网络）构建自包含 runtime/
./build-linux-runtime.sh
# 2) 打包
./pack.sh                      # → dist/img-sieve-linux-x86_64.tar.gz
```

内网目标机（无需系统 Python）：



```
tar -xzf img-sieve-linux-x86_64.tar.gz && cd img-sieve
./run.sh --host 0.0.0.0 --port 8080     # 直接用自带 runtime/bin/python3 运行
```

> `build-linux-runtime.sh`
>
>  从 python-build-standalone 获取可移植 CPython 3.11，
> 走国内镜像代理下载（无需梯子）。

### 方案 B：目标机已装 Python 3.11



```
tar -xzf img-sieve-linux-x86_64.tar.gz && cd img-sieve
./install.sh         # 创建 .venv 并从本地 wheel 离线安装 rapidocr（全程无网络）
./run.sh --host 0.0.0.0 --port 8080
```

> **目标机要求：Python 3.11**
>
> （随包 wheel 为 cp311 构建；Debian 12 默认即 3.11）。
> 若目标机为 
>
> **arm64**
>
> ：在开发机用 
>
> `pip download --dest wheels_linux_arm64 \`
> `--platform manylinux_2_17_aarch64 --python-version 311 --only-binary=:all: \`
> `rapidocr_onnxruntime`
>
>  准备 wheel，再 
>
> `./pack.sh arm64`
>
> 。



***

## 服务生命周期（run /stop/keepalive）



```
./run.sh --host 0.0.0.0 --port 8080    # 启动服务（前台）
./stop.sh [--port 8080]               # 停止服务（按端口找 server.py 进程，优雅关闭）
./keepalive.sh start [--port 8080]    # 拉起服务+启动保活守护（后台、SSH 登出不掉线）
./keepalive.sh status [--port 8080]   # 查看服务/守护状态
./keepalive.sh stop   [--port 8080]   # 停止守护并关闭服务
```

> **保活守护**
>
> （
>
> `keepalive.sh`
>
> ）：服务进程意外退出后自动拉起，适合长期部署；
> `stop.sh`
>
>  与 
>
> `keepalive.sh stop`
>
>  都按端口精确定位本服务进程，不会误杀同端口其他程序。



***

## 使用

### 本地 CLI



```
# 主页图 + 对话框图，自动识别角色、自动选 OCR 后端
./.venv_test/bin/python cli.py raw/20261015135527202.jpg raw/20261015135547661.jpg

# 指定后端
python3 cli.py <主页图> <对话框图> --backend rapidocr|vision|tesseract|mock

# 显式指定角色（避免自动判断歧义）
python3 cli.py <主页图> <对话框图> --role <主页图>=user --role <对话框图>=chat
```

### HTTP 服务 + 网页界面



```
./run.sh --host 0.0.0.0 --port 8080          # 目标机（venv 或自包含 runtime）
python3 server.py --host 0.0.0.0 --port 8080 # 开发机（需已装 rapidocr）
```

浏览器打开 `http://<服务地址>:8080/` —— 上传**主页截图 + 对话框截图**两张图，

点「开始识别」，页面即展示昵称、抖音号 / 小红书号、手机号、以及昵称是否一致的横幅。

（网页为单文件自包含，内联样式与脚本，无外部资源。）

**界面效果（使用截图）：**

| 抖音 | 小红书 |
|---|---|
| ![抖音识别界面](doc/image/douyin.png) | ![小红书识别界面](doc/image/xhs.png) |

- **左（抖音）**：上传主页截图 + 对话框截图，识别出「叫我大猫 / jiaowodamao996 / 手机号」，昵称一致 ✅
- **右（小红书）**：上传主页截图 + 对话框截图，识别出「滴滴嗒嗒 / 小红书号 / 手机号」，昵称不一致（滴滴嗒嗒 ≠ 一休的馒头）

接口：



* `GET /` — 上传 + 展示识别结果的**网页界面**

* `POST /recognize` — multipart 上传 2 张图片，返回识别 JSON


  * 文件字段名任意，服务按内容自动识别角色（含 "抖音号 / 小红书号"→主页；含 11 位手机号→对话框）

  * 可选 `role=<字段名>:user|chat` 指定某字段角色

* `GET /health` — 健康检查



```
curl -X POST http://127.0.0.1:8080/recognize \
  -F "user=@raw/20261015135527202.jpg" \
  -F "chat=@raw/20261015135547661.jpg"
```



***

## 测试



```
python3 -m tests.test_extractor          # 提取规则单测（权威 OCR 夹具）
```



***

## 提取规则（平台通用）



* **OCR**：一套 RapidOCR（PP-OCRv4）统一处理，抖音 / 小红书共用，无需分模型。

* **账号字段**：按平台匹配 —— 抖音 `抖音号[:：]<字母数字>` → `douyin_id`；小红书 `小红书号[:：]<字母数字>` → `xiaohongshu_id`；兜底取声明行尾部字母数字串。平台按文本自动识别 `platform`。

* **用户昵称**：取账号声明行正上方、水平居中对齐的文本行；兜底取顶部区域最长有效文本（抖音 / 小红书同一套逻辑）。

* **手机号码**：按行合并文本 → 去除非数字 → 匹配大陆手机号 `^1[3-9]\d{9}$`

  （兼容 `186 520 50 583`、`18 6520 50 583` 这类被空格拆开的写法）。

* **对话框对象昵称**：取聊天窗口顶部状态栏下方、居中的短文本（排除系统横幅 / 状态 / 纯数字）。

* **昵称一致性**：主页昵称与对话框对象昵称做前 5 位前缀匹配（`nickname_check.consistent`）。

* **角色识别**：含账号声明（抖音号 / 小红书号）→ user；含 11 位手机号 → chat；可 `--role` 人工覆盖。

## 开发机本地验证

`./.venv_test` 是本仓库在开发机创建的测试 venv（已装 rapidocr）。如重建：



```
python3 -m venv .venv_test
./.venv_test/bin/pip install rapidocr_onnxruntime
```