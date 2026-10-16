# img-sieve HTTP 接口对接说明

本文档描述 img-sieve 的 HTTP 接口，供第三方系统对接调用。
服务由 `./run.sh`（或 `./keepalive.sh start`）启动，纯 Python 标准库实现，内网离线部署。

## 服务地址

启动后默认监听 `0.0.0.0:8080`，根路径为网页界面。

```bash
./run.sh --host 0.0.0.0 --port 8080
```

---

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 网页界面（上传两张图 + 展示识别结果） |
| `POST` | `/recognize` | 上传图片识别，返回结构化 JSON（**核心接口**） |
| `GET` | `/health` | 健康检查 |

---

## 核心接口：POST /recognize

### 入参

- **Content-Type**：`multipart/form-data`
- **请求体大小**：1 ~ 50MB（超过 50MB 拒绝）
- **图片字段**：上传图片文件（2 张，至少 1 张）
  - **字段名任意**，服务按图片内容自动判断角色（主页/对话框）
  - 支持的图片格式：`.jpg / .jpeg / .png / .webp / .bmp / .gif`（按文件头校验）
- **角色覆盖（可选）**：表单文本字段 `role=<字段名>:user|chat`，人工指定某字段的角色

**角色自动判断规则**：
- 图片文本中含「抖音号」或「小红书号」声明 → 角色 `user`（主页）
- 图片文本中含 11 位大陆手机号 → 角色 `chat`（对话框）

**示例（curl）：**

```bash
# 自动判断角色
curl -X POST http://127.0.0.1:8080/recognize \
  -F "user=@raw/20261015135527202.jpg" \
  -F "chat=@raw/20261015135547661.jpg"

# 显式指定角色
curl -X POST http://127.0.0.1:8080/recognize \
  -F "pic1=@主页.png" \
  -F "pic2=@对话框.png" \
  -F "role=pic1:user" \
  -F "role=pic2:chat"
```

**示例（Python requests）：**

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8080/recognize",
    files={
        "user": ("主页.png", open("主页.png", "rb"), "image/png"),
        "chat": ("对话框.png", open("对话框.png", "rb"), "image/png"),
    },
)
print(resp.json())
```

---

### 出参

**成功（HTTP 200）：**

```json
{
  "code": 0,
  "message": "ok",
  "backend": "rapidocr",
  "data": {
    "user": {
      "nickname": "叫我大猫",
      "platform": "douyin",
      "douyin_id": "jiaowodamao996",
      "xiaohongshu_id": null
    },
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

**字段说明：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | 状态码，`0` 表示成功 |
| `message` | string | 状态描述 |
| `backend` | string | 实际使用的 OCR 后端：`rapidocr` / `vision` / `tesseract` / `mock` |
| `data.user.nickname` | string\|null | 主页用户昵称 |
| `data.user.platform` | string\|null | 平台：`douyin` / `xiaohongshu` |
| `data.user.douyin_id` | string\|null | 抖音号（抖音平台） |
| `data.user.xiaohongshu_id` | string\|null | 小红书号（小红书平台） |
| `data.phone` | string\|null | 对话框中的手机号 |
| `data.chat_object_name` | string\|null | 对话框对象昵称 |
| `data.nickname_consistent` | boolean\|null | 主页昵称与对话框对象昵称是否一致（`true` 一致 / `false` 不一致 / `null` 缺失无法判断） |
| `data.nickname_check.user` | string\|null | 主页昵称（比对用） |
| `data.nickname_check.chat` | string\|null | 对话框对象昵称（比对用） |
| `data.nickname_check.compare_chars` | int | 比对长度（固定 `5`，前 5 位前缀匹配） |
| `data.nickname_check.consistent` | boolean\|null | 与 `data.nickname_consistent` 等价 |

> 小红书样例：`platform` 为 `xiaohongshu`，账号存于 `xiaohongshu_id`（如 `1346204393`），`douyin_id` 为 `null`。

---

### 错误返回

| HTTP 状态 | code | 触发条件 |
|---|---|---|
| `400` | `400` | 不是 `multipart/form-data`；请求体大小超出 1~50MB；字段不是有效图片；未上传图片 |
| `404` | `404` | 请求了不存在的路径 |
| `500` | `500` | OCR 后端初始化/运行失败（如 rapidocr 不可用且无备用后端） |

错误响应示例：

```json
{ "code": 400, "message": "请使用 multipart/form-data 上传图片" }
{ "code": 400, "message": "请至少上传 1 张图片" }
{ "code": 404, "message": "not found" }
```

---

## 健康检查：GET /health

```json
{ "code": 0, "message": "ok", "status": "up" }
```

---

## 对接注意事项

- 返回 `Content-Type: application/json; charset=utf-8`，`Cache-Control: no-store`。
- 图片仅在服务端内存/临时文件中处理，识别完成后临时文件自动清理，图片不出内网。
- 服务支持并发请求（`ThreadingHTTPServer` 每请求一线程）。
- 启动时可用 `--backend` 强制指定 OCR 后端（`rapidocr|vision|tesseract|mock`），默认自动选择。
