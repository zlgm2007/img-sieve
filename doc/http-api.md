# img-sieve HTTP 接口对接说明

本文档描述 img-sieve 的 HTTP 接口，供第三方系统对接调用。

服务由 `./run.sh`（或 `./keepalive.sh start`）启动，纯 Python 标准库实现，内网离线部署。

## 服务地址

启动后默认监听 `0.0.0.0:8080`，根路径为网页界面。

**生产环境**：`http://10.104.209.194:8080/`（人脉识别网页）、`http://10.104.209.194:8080/order`（订单识别网页）、`http://10.104.209.194:8080/stats`（统计网页）；主要接口 `POST /recognize`、`POST /recognize/async`、`POST /recognize-order`、`POST /recognize-order/async`、`GET /task/{task_id}`、`GET /admin/tasks`、`GET /admin/stats`、`GET /health`

**测试环境**：`http://10.237.179.21:8080/`（人脉识别网页）、`http://10.237.179.21:8080/order`（订单识别网页）、`http://10.237.179.21:8080/stats`（统计网页）；主要接口 `POST /recognize`、`POST /recognize/async`、`POST /recognize-order`、`POST /recognize-order/async`、`GET /task/{task_id}`、`GET /admin/tasks`、`GET /admin/stats`、`GET /health`

> 下文所有调用示例均以**生产环境** `http://10.104.209.194:8080` 为准；对接测试环境时只需替换主机地址，路径与参数完全一致。

```
./run.sh --host 0.0.0.0 --port 8080
```

---

## 接口一览

系统分两条业务线，共享同一套 OCR 后端，响应结构相互独立：

- **人脉识别**（`/recognize` 系列）：抖音 / 小红书 / 微信，主页（联系人）+ 对话框配对，提取昵称、平台账号、地区、手机号并核对两处昵称是否一致。
- **订单识别**（`/recognize-order` 系列）：单张订单截图，抽取列表页、手工票据、详情页对应标签后的订单号，返回统一订单号列表。

| 方法     | 路径                             | 说明                                       |
| ------ | ------------------------------ | ---------------------------------------- |
| `GET`  | `/`                            | 人脉识别网页界面（主页/联系人 + 对话框两张图）                |
| `GET`  | `/order`                       | 订单识别网页界面（单张订单截图：列表页 / 票据 / 详情页）          |
| `GET`  | `/stats`                       | 近 7 天场景统计网页界面                            |
| `POST` | `/recognize`                   | 人脉识别，上传 1~2 张图，返回结构化 JSON（**核心接口**，同步阻塞） |
| `POST` | `/recognize/async`             | 人脉识别异步任务：收图立即返回 `task_id`，后台识别           |
| `POST` | `/recognize-order`             | 订单识别，上传 **1 张**订单截图，返回订单号列表（同步阻塞）       |
| `POST` | `/recognize-order/async`       | 订单识别异步任务：收图立即返回 `task_id`，后台识别           |
| `GET`  | `/task/{task_id}`              | 按 task_id 查询异步任务状态与识别结果                  |
| `GET`  | `/task/{task_id}/image/{1\|2}` | 查看任务原始截图（1=第一张，2=第二张；订单任务无第二张）           |
| `GET`  | `/admin/tasks`                 | 任务记录分页列表（含场景、文件名、状态、时间）                  |
| `GET`  | `/admin/stats`                 | 近 N 天按日、按场景汇总任务量（统计页数据源）                  |
| `POST` | `/admin/purge?days=N`          | 清理 N 天前的历史任务与图片（运维）                      |
| `GET`  | `/health`                      | 健康检查                                     |

---

## 核心接口：POST /recognize

### 入参

- **Content-Type**：`multipart/form-data`
- **请求体大小**：1 ~ 50MB（`Content-Length` 为 0 或超过 50MB 均拒绝）
- **图片字段**：上传 **1 ~ 2 张**图片（至少 1 张；推荐主页/联系人 + 对话框两张配对识别）
  - **字段名任意**，服务按图片内容自动判断角色（主页 / 联系人 / 对话框）
  - 支持的图片格式：`.jpg / .jpeg / .png / .webp / .bmp / .gif`，按**文件头（magic bytes）**&#x6821;验，扩展名不作为唯一依据
- **角色覆盖（可选，仅同步接口支持）**：表单文本字段 `role=<图片字段名>:user|chat`，可重复多次，人工指定某字段的角色；`user` = 主页 / 联系人页，`chat` = 对话框

**角色自动判断规则**（未传 `role=` 时）：

- 图片文本中含「抖音号」「小红书号」「微信号」声明，或含「地区：」行 → 角色 `user`（主页 / 联系人页）
- 图片文本中含 11 位大陆手机号 → 角色 `chat`（对话框）
- 以上都不命中时，若顶部存在居中标题候选（微信对话框，消息体里不带手机号）→ 角色 `chat`

**支持的平台与字段**：

| 平台  | `user.platform` | 账号字段             | 额外字段         |
| --- | --------------- | ---------------- | ------------ |
| 抖音  | `douyin`        | `douyin_id`      | —            |
| 小红书 | `xiaohongshu`   | `xiaohongshu_id` | —            |
| 微信  | `wechat`        | `wechat_id`      | `region`（地区） |

> 微信联系人页上传为 `user` 角色，对话框上传为 `chat` 角色；昵称、微信号、地区从联系人页提取，对话框对象昵称从聊天窗口顶部标题提取，两侧昵称做一致性核对。手机号仅从对话框消息体提取，**不从标题 / 昵称中提取**；微信昵称按完整串保留，不在内部拆分手机号。

**示例（curl）：**

```
# 抖音 / 小红书：自动判断角色
curl -X POST http://10.104.209.194:8080/recognize \
  -F "user=@raw/20261015135527202.jpg" \
  -F "chat=@raw/20261015135547661.jpg"

# 微信：联系人页 + 对话框
curl -X POST http://10.104.209.194:8080/recognize \
  -F "user=@raw/wx20261022094534150.jpg" \
  -F "chat=@raw/wx20261022094522060.jpg"

# 显式指定角色
curl -X POST http://10.104.209.194:8080/recognize \
  -F "pic1=@主页.png" \
  -F "pic2=@对话框.png" \
  -F "role=pic1:user" \
  -F "role=pic2:chat"
```

**示例（Python requests）：**

```
import requests

resp = requests.post(
    "http://10.104.209.194:8080/recognize",
    files={
        "user": ("主页.png", open("主页.png", "rb"), "image/png"),
        "chat": ("对话框.png", open("对话框.png", "rb"), "image/png"),
    },
)
print(resp.json())
```

---

## Java 对接示例（Spring 生态）

Java 侧用 `org.springframework.web.multipart.MultipartFile` 封装图片文件，

再用 `RestTemplate` 以 `multipart/form-data` POST 到 `/recognize`。

**Maven 依赖**：`spring-web`（`MultipartFile`、`RestTemplate`）、`spring-boot-starter-web`。

**客户端调用工具类：**

```
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;

public class ImgSieveClient {

    private static final String URL = "http://10.104.209.194:8080/recognize";

    /**
     * 调用 img-sieve 识别接口
     *
     * @param homeImg 主页截图（MultipartFile 封装）
     * @param chatImg 对话框截图
     * @return 服务返回的 JSON 字符串（code/message/backend/data）
     */
    public String recognize(MultipartFile homeImg, MultipartFile chatImg) throws IOException {
        RestTemplate rest = new RestTemplate();

        // 用 ByteArrayResource 把 MultipartFile 的字节包装成 multipart 文件字段
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("user", new ByteArrayResource(homeImg.getBytes()) {
            @Override public String getFilename() { return homeImg.getOriginalFilename(); }
        });
        body.add("chat", new ByteArrayResource(chatImg.getBytes()) {
            @Override public String getFilename() { return chatImg.getOriginalFilename(); }
        });
        // 可选：显式指定角色（字段名:user|chat），不传则服务按内容自动判断
        // body.add("role", "user:user");
        // body.add("role", "chat:chat");

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);

        HttpEntity<MultiValueMap<String, Object>> entity = new HttpEntity<>(body, headers);
        ResponseEntity<String> resp = rest.exchange(URL, HttpMethod.POST, entity, String.class);
        return resp.getBody(); // 已为 JSON 字符串，可再用 Jackson 解析
    }
}
```

**Spring Controller 接收 + 转发（前端把两张图 multipart 传给你的 Java 服务，Java 再调 img-sieve）：**

```
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
public class ImgSieveController {

    /** 参数名即 /recognize 的图片字段名，可与前端约定为 user / chat */
    @PostMapping("/extract")
    public String extract(@RequestParam("user") MultipartFile homeImg,
                          @RequestParam("chat") MultipartFile chatImg) throws Exception {
        return new ImgSieveClient().recognize(homeImg, chatImg);
    }
}
```

**解析返回 JSON（Jackson，平台账号随 `user.platform` 取对应字段，`null` 字段用 `asText(null)` 空值安全读取）：**

```
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;

String json = new ImgSieveClient().recognize(homeImg, chatImg);
JsonNode root = new ObjectMapper().readTree(json);
JsonNode user = root.path("data").path("user");

String nickname   = user.path("nickname").asText(null);
String platform   = user.path("platform").asText(null);
String accountId;
if ("douyin".equals(platform))        accountId = user.path("douyin_id").asText(null);
else if ("xiaohongshu".equals(platform)) accountId = user.path("xiaohongshu_id").asText(null);
else if ("wechat".equals(platform))      accountId = user.path("wechat_id").asText(null);
else accountId = null;
String region     = user.path("region").asText(null);
String phone      = root.path("data").path("phone").asText(null);
String chatName   = root.path("data").path("chat_object_name").asText(null);

JsonNode consistentNode = root.path("data").path("nickname_consistent");
Boolean consistent = consistentNode.isNull() ? null : consistentNode.asBoolean();
```

> 图片仅在前端 → Java → img-sieve 之间流转，img-sieve 识别完即清理临时文件，图片不出内网。

---

### 出参

**成功（HTTP 200）—— 抖音样例：**

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

**成功（HTTP 200）—— 微信样例：**

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
      "user": "王云13951905083",
      "chat": "王云13951905083",
      "compare_chars": 5,
      "consistent": true
    }
  }
}
```

**字段说明：**

| 字段                                  | 类型      | 说明                                                        |                                                           |
| ----------------------------------- | ------- | --------------------------------------------------------- | --------------------------------------------------------- |
| `code`                              | int     | 状态码，`0` 表示成功                                              |                                                           |
| `message`                           | string  | 状态描述                                                      |                                                           |
| `backend`                           | string  | 实际使用的 OCR 后端：`rapidocr` / `vision` / `tesseract` / `mock` |                                                           |
| `data.user.nickname`                | string  | null                                                      | 主页 / 联系人页用户昵称                                             |
| `data.user.platform`                | string  | null                                                      | 平台：`douyin` / `xiaohongshu` / `wechat`                    |
| `data.user.douyin_id`               | string  | null                                                      | 抖音号（抖音平台）                                                 |
| `data.user.xiaohongshu_id`          | string  | null                                                      | 小红书号（小红书平台）                                               |
| `data.user.wechat_id`               | string  | null                                                      | 微信号（微信平台）                                                 |
| `data.user.region`                  | string  | null                                                      | 地区（仅微信联系人页返回，如「江苏南京」）                                     |
| `data.phone`                        | string  | null                                                      | 对话框消息体中的手机号（标题 / 昵称内的数字不参与提取）                             |
| `data.chat_object_name`             | string  | null                                                      | 对话框对象昵称                                                   |
| `data.nickname_consistent`          | boolean | null                                                      | 主页昵称与对话框对象昵称是否一致（`true` 一致 / `false` 不一致 / `null` 缺失无法判断） |
| `data.nickname_check.user`          | string  | null                                                      | 主页昵称（比对用）                                                 |
| `data.nickname_check.chat`          | string  | null                                                      | 对话框对象昵称（比对用）                                              |
| `data.nickname_check.compare_chars` | int     | 比对长度（固定 `5`，前 5 位前缀匹配）                                    |                                                           |
| `data.nickname_check.consistent`    | boolean | null                                                      | 与 `data.nickname_consistent` 等价                           |


> 小红书样例：`platform` 为 `xiaohongshu`，账号存于 `xiaohongshu_id`（如 `1346204393`），`douyin_id`、`wechat_id`、`region` 为 `null`。  
> 微信样例：账号存于 `wechat_id`，地区存于 `region`；`douyin_id`、`xiaohongshu_id` 为 `null`。

**缺省口径：**

- `data.user` 对象固定包含 `nickname / platform / douyin_id / xiaohongshu_id / wechat_id / region` 六个键，未识别到的键值为 `null`。
- 只上传 1 张图、或某侧内容缺失时，对应侧字段为 `null`；`nickname_consistent` / `nickname_check.consistent` 在两侧昵称任一缺失时为 `null`（无法判断）。
- 手机号仅从对话框消息体提取，标题 / 昵称中内嵌的 11 位数字不计入。

---

### 错误返回

| HTTP 状态 | code  | 触发条件                                                           |
| ------- | ----- | -------------------------------------------------------------- |
| `400`   | `400` | 不是 `multipart/form-data`；请求体为空或超过 50MB；带图片扩展名但文件头不是图片；没有有效图片文件 |
| `404`   | `404` | 请求了不存在的路径                                                      |
| `500`   | `500` | OCR 后端初始化 / 运行失败（如 rapidocr 不可用且无备用后端）                         |

> 普通文本文件（`text/plain` 等）会被当作表单文本字段忽略、不计入图片；真正触发「不是有效的图片文件」的是带图片扩展名但 magic bytes 校验失败的文件。

错误响应示例：

```
{ "code": 400, "message": "请使用 multipart/form-data 上传图片" }
{ "code": 400, "message": "请求体大小需在 1~52428800 字节之间" }
{ "code": 400, "message": "字段 user 不是有效的图片文件" }
{ "code": 400, "message": "请至少上传 1 张图片" }
{ "code": 404, "message": "not found" }
```

---

## 订单识别：POST /recognize-order

与 `/recognize` 是两条独立业务线：**只传 1 张订单截图**，按页面从上到下顺序返回订单号列表；不做角色检测、不做跨图昵称核对。支持三类截图：

| 截图类型 | 识别标签 | 单号数量 | 说明 |
| --- | --- | --- | --- |
| 订单列表页 | `订单号：` | 一张图可返回多个 | 保留原有多订单号能力 |
| 手工票据 | `OMS单号：` | 通常 1 个 | OCR 偶发把 `O` 误识为 `Q`，规则兼容 `QMS单号：` |
| 订单详情页 | `订单编号：` | 通常 1 个 | 仍复用 `order_ids` 数组，长度为 1 |

### 入参

- **Content-Type**：`multipart/form-data`
- **请求体大小**：1 ~ 50MB（`Content-Length` 为 0 或超过 50MB 均拒绝）
- **图片字段**：恰好 **1 张**订单截图（订单列表页 / 手工票据 / 订单详情页），**字段名任意**（网页前端约定字段名为 `order`）
  - 支持的图片格式：`.jpg / .jpeg / .png / .webp / .bmp / .gif`，按**文件头（magic bytes）**&#x6821;验
  - 普通文本文件会被当作表单文本字段忽略；有效图片文件数为 0 或 ≥2 均返回 400；带图片扩展名但文件头不是图片返回 400。
- 本接口**不接收** `role=` 文本字段，无角色检测与跨图比对。

**示例（curl）：**

```
curl -X POST http://10.104.209.194:8080/recognize-order \
  -F "order=@raw/order20261022094541207.jpg"
```

### 出参

**成功（HTTP 200）：**

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
    ]
  }
}
```

**字段说明：**

| 字段                 | 类型       | 说明                                   |
| ------------------ | -------- | ------------------------------------ |
| `code`             | int      | 状态码，`0` 表示成功                         |
| `message`          | string   | 状态描述                                 |
| `backend`          | string   | 实际使用的 OCR 后端                         |
| `data.order_count` | int      | 识别到的订单号数量，与 `order_ids` 长度一致         |
| `data.order_ids`   | string[] | 订单号列表，按页面从上到下阅读顺序排列、自动去重；未识别到时为 `[]` |

> 提取以标签前缀为锚点：`订单号：`、`OMS单号：`/`QMS单号：`、`订单编号：` 后 10~18 位数字。票据上的 `单号：DA...`、`订单编号：MM...`，详情页的 `订单行号：...`，以及商品编码、SKU、水印、日期等不会混入列表。
> 手工票据可能横放或倒置；首遍未抽到单号时，服务层会对单张图依次尝试 90° / 180° / 270° 旋转后重试，响应格式不变。
> 页面未识别到订单号时：`order_count = 0`、`order_ids = []`，接口仍返回 HTTP 200。

**Java 对接（RestTemplate 片段）：**

```java
MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
body.add("order", new ByteArrayResource(orderImg.getBytes()) {
    @Override public String getFilename() { return orderImg.getOriginalFilename(); }
});
HttpHeaders headers = new HttpHeaders();
headers.setContentType(MediaType.MULTIPART_FORM_DATA);
ResponseEntity<String> resp = rest.exchange(
        BASE + "/recognize-order", HttpMethod.POST,
        new HttpEntity<>(body, headers), String.class);

// 解析：data.order_count、data.order_ids（数组）
JsonNode root = new ObjectMapper().readTree(resp.getBody());
int count = root.path("data").path("order_count").asInt();
JsonNode ids = root.path("data").path("order_ids");
```

### 错误返回

| HTTP 状态 | code  | 触发条件                                                                |
| ------- | ----- | ------------------------------------------------------------------- |
| `400`   | `400` | 不是 `multipart/form-data`；请求体为空或超过 50MB；带图片扩展名但文件头不是图片；有效图片文件数不是 1 张 |
| `500`   | `500` | OCR 后端初始化 / 运行失败                                                    |

错误响应示例：

```
{ "code": 400, "message": "请使用 multipart/form-data 上传图片" }
{ "code": 400, "message": "请求体大小需在 1~52428800 字节之间" }
{ "code": 400, "message": "字段 order 不是有效的图片文件" }
{ "code": 400, "message": "订单识别仅支持上传 1 张订单截图" }
```

---

## 健康检查：GET /health

```
{ "code": 0, "message": "ok", "status": "up" }
```

---

## 对接注意事项

- JSON 接口返回 `Content-Type: application/json; charset=utf-8`，`Cache-Control: no-store`；任务图片接口返回原始图片字节，`Cache-Control: private, max-age=3600`。
- 图片仅在服务端内存 / 临时文件中处理，识别完成后临时文件自动清理，图片不出内网。
- 服务支持并发请求（`ThreadingHTTPServer` 每请求一线程）。
- 启动时可用 `--backend` 强制指定 OCR 后端（`rapidocr|vision|tesseract|mock`），默认自动选择。

---

## 部署配置：MySQL 与 `.env`

同步接口不强依赖 MySQL；**异步提交、任务记录、后台 worker** 必须配置 MySQL。连接参数走环境变量，也可写在项目根目录 `.env`（系统环境变量优先，`.env` 只补缺省）。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMG_SIEVE_MYSQL_HOST` | `127.0.0.1` | MySQL 主机；多机部署时其余机器填 MySQL 所在机器内网 IP |
| `IMG_SIEVE_MYSQL_PORT` | `3306` | MySQL 端口 |
| `IMG_SIEVE_MYSQL_USER` | `root` | 数据库账号 |
| `IMG_SIEVE_MYSQL_PASSWORD` | 空 | 数据库密码 |
| `IMG_SIEVE_MYSQL_DB` | `imgsieve` | 任务库名 |
| `IMG_SIEVE_ADMIN_TOKEN` | 空（不校验） | 仅 `/admin/purge` 鉴权；`/admin/tasks`、`/admin/stats` 不校验 |

`install.sh` 在 `.env` 不存在时自动从 `.env.example` 复制一份（已存在不覆盖）；交互式安装会询问 MySQL 主机、端口、账号、库名与密码，后四项可回车沿用当前值或默认值，并写入 `.env`。自动化部署可使用 `--mysql-host`、`--mysql-password` 等参数跳过交互，仅同步识别可用 `--skip-mysql`。

需要手动修改时：

```bash
cd /opt/server/img-sieve
vi .env
```

```bash
IMG_SIEVE_MYSQL_HOST=127.0.0.1
IMG_SIEVE_MYSQL_PORT=3306
IMG_SIEVE_MYSQL_USER=imgsieve
IMG_SIEVE_MYSQL_PASSWORD=数据库密码
IMG_SIEVE_MYSQL_DB=imgsieve
```

- `run.sh`、`keepalive.sh` 启动时都会加载 `.env`；裸跑 `python server.py` / `worker.py` 时由 `db.py` 兜底解析同一份 `.env`。
- 改完 `.env` 必须重启 HTTP 服务与 worker（`./keepalive.sh stop && ./keepalive.sh start`）；旧进程持有启动时的环境变量，不会自动热加载。
- 若接口返回 `Access denied for user 'root'@'localhost' (using password: NO)`，说明进程未读到 `.env`：默认值生效为 `root` / 空密码。依次排查：`.env` 是否在部署目录、启动入口是否加载了配置、改配置后是否重启了旧进程。

---

## 异步识别（多机部署）

当部署为多台机器共享一个 MySQL 时，用异步接口：调用方上传后立即拿到 `task_id`，由任意一台机器的 worker 后台识别，再轮询查询结果。同步接口也会把每次识别写进同一张任务表，两边数据在「任务记录」页都能查到。

任务用 `scene` 字段区分业务线：`people`（人脉识别，默认）、`order`（订单识别）；worker 按 `scene` 分流到对应识别流水线。

### POST /recognize/async（人脉识别异步）

提交人脉识别任务，立即返回 `task_id`，由后台 worker 识别后通过 `GET /task/{task_id}` 查询结果。

**入参**

- **Content-Type**：`multipart/form-data`
- **请求体大小**：1 ~ 50MB
- **图片字段**：上传 **1 ~ 2 张**图片，字段名任意；仅取前两张入库
- **不支持** `role=` 角色覆盖：异步任务的角色由 worker 按 `/recognize` 同一套自动规则判断

**出参**（立即返回，不等待识别完成）：

```json
{ "code": 0, "message": "ok", "task_id": "9c1f...", "status": "pending" }
```

| 字段        | 类型     | 说明                                        |
| --------- | ------ | ----------------------------------------- |
| `code`    | int    | `0` 提交成功；非 0 见错误返回                        |
| `message` | string | 状态描述，成功为 `ok`                             |
| `task_id` | string | 32 位十六进制任务 ID，用于 `GET /task/{task_id}` 查询 |
| `status`  | string | 提交时固定为 `pending`                          |

**错误返回**

| HTTP 状态 | code  | 触发条件                                                     |
| ------- | ----- | -------------------------------------------------------- |
| `400`   | `400` | 不是 `multipart/form-data`；请求体为空或超限；带图片扩展名但文件头不是图片；未上传有效图片 |
| `500`   | `500` | 任务入库失败（MySQL 未连接或不可用）                                    |

```
{ "code": 400, "message": "请使用 multipart/form-data 上传图片" }
{ "code": 400, "message": "请求体大小需在 1~52428800 字节之间" }
{ "code": 400, "message": "字段 user 不是有效的图片文件" }
{ "code": 400, "message": "请至少上传 1 张图片" }
{ "code": 500, "message": "任务入库失败（检查 MySQL 连接）: ..." }
```

**curl 示例：**

```bash
curl -X POST http://10.104.209.194:8080/recognize/async \
  -F "user=@主页.png" \
  -F "chat=@对话框.png"
# => {"code":0,"message":"ok","task_id":"9c1f...","status":"pending"}
```

### POST /recognize-order/async（订单识别异步）

提交订单识别任务，立即返回 `task_id`，由后台 worker 识别后通过 `GET /task/{task_id}` 查询结果。

**入参**

- **Content-Type**：`multipart/form-data`
- **请求体大小**：1 ~ 50MB
- **图片字段**：恰好 **1 张**订单截图（订单列表页 / 手工票据 / 订单详情页），字段名任意；上传 0 张或 ≥2 张返回 400

**出参**（立即返回，不等待识别完成）：

```json
{ "code": 0, "message": "ok", "task_id": "d2c8...", "status": "pending" }
```

| 字段        | 类型     | 说明                                        |
| --------- | ------ | ----------------------------------------- |
| `code`    | int    | `0` 提交成功；非 0 见错误返回                        |
| `message` | string | 状态描述，成功为 `ok`                             |
| `task_id` | string | 32 位十六进制任务 ID，用于 `GET /task/{task_id}` 查询 |
| `status`  | string | 提交时固定为 `pending`                          |

**错误返回**

| HTTP 状态 | code  | 触发条件                                                           |
| ------- | ----- | -------------------------------------------------------------- |
| `400`   | `400` | 不是 `multipart/form-data`；请求体为空或超限；带图片扩展名但文件头不是图片；有效图片文件数不是 1 张 |
| `500`   | `500` | 任务入库失败（MySQL 未连接或不可用）                                          |

```
{ "code": 400, "message": "请使用 multipart/form-data 上传图片" }
{ "code": 400, "message": "请求体大小需在 1~52428800 字节之间" }
{ "code": 400, "message": "字段 order 不是有效的图片文件" }
{ "code": 400, "message": "订单识别仅支持上传 1 张订单截图" }
{ "code": 500, "message": "任务入库失败（检查 MySQL 连接）: ..." }
```

**curl 示例：**

```bash
curl -X POST http://10.104.209.194:8080/recognize-order/async \
  -F "order=@订单截图.png"
# => {"code":0,"message":"ok","task_id":"d2c8...","status":"pending"}
```

### GET /task/{task_id}

轮询查询任务状态与结果。

**状态流转**：`pending`（排队中）→ `processing`（某台 worker 已认领、识别中）→ `done`（完成）/ `failed`（失败）。

**响应：**

```json
{
  "code": 0,
  "task_id": "9c1f...",
  "status": "done",
  "scene": "people",
  "created_at": "2026-10-19 11:00:00.123456",
  "started_at": "2026-10-19 11:00:01.200000",
  "finished_at": "2026-10-19 11:00:05.800000",
  "backend": "rapidocr",
  "result": { "code": 0, "message": "ok", "backend": "rapidocr", "data": { "...": "结构随 scene 不同，见下表" } },
  "error": null
}
```

**字段说明：**

| 字段            | 类型     | 说明                                                     |                                                  |
| ------------- | ------ | ------------------------------------------------------ | ------------------------------------------------ |
| `code`        | int    | `0` 成功；`400` 缺少 `task_id`；`404` 任务不存在；`500` MySQL 连接失败 |                                                  |
| `task_id`     | string | 任务 ID                                                  |                                                  |
| `status`      | string | `pending` / `processing` / `done` / `failed`           |                                                  |
| `scene`       | string | 业务线：`people`（人脉识别）/ `order`（订单识别）；老库缺省为 `people`       |                                                  |
| `created_at`  | string | 提交时间                                                   |                                                  |
| `started_at`  | string | null                                                   | worker 开始识别时间                                    |
| `finished_at` | string | null                                                   | 识别完成时间                                           |
| `backend`     | string | null                                                   | 实际 OCR 后端                                        |
| `result`      | object | null                                                   | 识别结果（含 `code/message/backend/data`）；未完成时为 `null` |
| `error`       | string | null                                                   | 失败原因；成功时为 `null`                                 |

**`result.data` 结构随 `scene` 不同：**

- `scene=people`：结构同 `/recognize` 的 `data`（`user` / `phone` / `chat_object_name` / `nickname_check` 等）。
- `scene=order`：结构同 `/recognize-order` 的 `data`（`order_count` / `order_ids`）。

建议先读 `scene` 字段再按对应结构解析 `result.data`。

**错误返回**

| HTTP 状态 | code  | 触发条件                |
| ------- | ----- | ------------------- |
| `400`   | `400` | 路径中缺少 `task_id`     |
| `404`   | `404` | `task_id` 不存在       |
| `500`   | `500` | 查询失败（MySQL 未连接或不可用） |

```
{ "code": 400, "message": "缺少 task_id" }
{ "code": 404, "message": "任务不存在" }
{ "code": 500, "message": "查询失败（检查 MySQL 连接）: ..." }
```

建议轮询间隔 **1~2 秒**；识别完成（`status=done`）后停止轮询。单图识别约数秒（取决于服务器 CPU）。

### GET /task/{task_id}/image/{1|2}

查看任务保存的原始截图（任务记录页缩略图 / 大图查看使用）。

**入参（路径参数）**

| 参数        | 类型     | 说明                             |
| --------- | ------ | ------------------------------ |
| `task_id` | string | 任务 ID                          |
| `n`       | int    | `1` = 第一张图；`2` = 第二张图（仅人脉任务存在） |

**出参**

- `200`：直接返回图片原始字节，`Content-Type` 为图片类型（`image/jpeg` 等），`Cache-Control: private, max-age=3600`。
- 订单任务（`scene=order`）只保存第一张图，`/image/2` 返回 404。


**错误返回**

| HTTP 状态 | code  | 触发条件                                |
| ------- | ----- | ----------------------------------- |
| `404`   | `404` | 任务不存在                               |
| `404`   | `404` | 任务存在，但对应槽位没有图片（订单任务请求 `/image/2` 等） |
| `500`   | `500` | 取图失败（MySQL 未连接或不可用），文案前缀 `取图失败: `   |

```
{ "code": 404, "message": "任务不存在" }
{ "code": 404, "message": "无此图片" }
{ "code": 500, "message": "取图失败: ..." }
```

### Java 异步调用示例（提交 + 轮询）

用 `RestTemplate` 提交 multipart 拿到 `taskId`，再循环 `GET /task/{id}` 直到 `done`/`failed`。

```java
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.*;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;

import java.io.IOException;

public class ImgSieveAsyncClient {

    private static final String BASE = "http://10.104.209.194:8080";
    private final RestTemplate rest = new RestTemplate();
    private final ObjectMapper mapper = new ObjectMapper();

    /** 1) 提交异步识别，返回 taskId */
    public String submitAsync(byte[] homeImg, String homeName,
                              byte[] chatImg, String chatName) throws IOException {
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("user", new ByteArrayResource(homeImg) {
            @Override public String getFilename() { return homeName; }
        });
        body.add("chat", new ByteArrayResource(chatImg) {
            @Override public String getFilename() { return chatName; }
        });

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        HttpEntity<MultiValueMap<String, Object>> req = new HttpEntity<>(body, headers);

        ResponseEntity<String> resp =
                rest.postForEntity(BASE + "/recognize/async", req, String.class);
        return mapper.readTree(resp.getBody()).path("task_id").asText();
    }

    /** 2) 轮询直到完成，返回结果 JSON（最多等 60 秒） */
    public JsonNode waitResult(String taskId) throws Exception {
        long deadline = System.currentTimeMillis() + 60_000;
        while (System.currentTimeMillis() < deadline) {
            String body = rest.getForObject(BASE + "/task/" + taskId, String.class);
            JsonNode node = mapper.readTree(body);
            String status = node.path("status").asText();
            if ("done".equals(status))   return node.path("result");
            if ("failed".equals(status)) throw new RuntimeException("识别失败: " + node.path("error").asText());
            Thread.sleep(1500); // pending / processing，继续等
        }
        throw new RuntimeException("识别超时: " + taskId);
    }

    // 用法：
    //   String taskId = client.submitAsync(homeBytes, "home.png", chatBytes, "chat.png");
    //   JsonNode result = client.waitResult(taskId);
    //   String nickname = result.path("data").path("user").path("nickname").asText();
}
```

> 若图片在 Spring 里是 `MultipartFile`，用 `multipartFile.getBytes()` 取字节即可（同同步示例）。

**订单异步调用**：把图片 POST 到 `/recognize-order/async`（单文件，字段名任意），轮询同一接口 `/task/{id}`；`status=done` 后按 `scene=order` 解析 `result.data.order_ids` 数组。

### GET /admin/tasks

任务记录分页列表（同步与异步任务共用同一张表，按 `created_at` 倒序）。接口不返回图片 BLOB 与完整结果 JSON；需要结果时用 `task_id` 调 `GET /task/{task_id}`。

**入参（Query String，均可选）**

| 参数      | 类型     | 默认值 | 说明                                                      |
| ------- | ------ | --- | ------------------------------------------------------- |
| `page`  | int    | `1` | 页码，非整数或小于 1 按 1 处理                                      |
| `start` | string | —   | 起始时间 `YYYY-MM-DD HH:MM:SS`，仅返回 `created_at ≥ start` 的任务 |
| `end`   | string | —   | 截止时间 `YYYY-MM-DD HH:MM:SS`，仅返回 `created_at ≤ end` 的任务   |

> 每页固定 **20** 条；本接口不校验 `IMG_SIEVE_ADMIN_TOKEN`（token 保护仅作用于 `/admin/purge`）。

**出参**

```json
{
  "code": 0,
  "total": 15,
  "page": 1,
  "per_page": 20,
  "pages": 1,
  "items": [
    {
      "task_id": "642a3a36a0e8413dbc778f31f86329a8",
      "status": "done",
      "scene": "order",
      "backend": "rapidocr",
      "created_at": "2026-10-22 10:36:03.123456",
      "finished_at": "2026-10-22 10:36:03.500000",
      "error_msg": null,
      "image1_name": "order",
      "image2_name": null
    }
  ]
}
```

**分页字段说明**

| 字段         | 类型    | 说明                |
| ---------- | ----- | ----------------- |
| `code`     | int   | `0` 成功；`500` 查询失败 |
| `total`    | int   | 符合条件的任务总数         |
| `page`     | int   | 当前页码              |
| `per_page` | int   | 每页条数，固定 `20`      |
| `pages`    | int   | 总页数               |
| `items`    | array | 任务记录数组，结构见下表      |

**`items[]` 字段说明**

| 字段            | 类型     | 说明                                           |                             |
| ------------- | ------ | -------------------------------------------- | --------------------------- |
| `task_id`     | string | 任务 ID，用于 `GET /task/{task_id}` 查询完整结果        |                             |
| `status`      | string | `pending` / `processing` / `done` / `failed` |                             |
| `scene`       | string | `people`（人脉识别）/ `order`（订单识别）                |                             |
| `backend`     | string | null                                         | 实际 OCR 后端                   |
| `created_at`  | string | null                                         | 提交时间                        |
| `finished_at` | string | null                                         | 完成时间；未完成时为 `null`           |
| `error_msg`   | string | null                                         | 失败原因；成功时为 `null`            |
| `image1_name` | string | null                                         | 第一张图原始文件名                   |
| `image2_name` | string | null                                         | 第二张图原始文件名；订单任务与单图任务为 `null` |

**错误返回**

| HTTP 状态 | code  | 触发条件                |
| ------- | ----- | ------------------- |
| `500`   | `500` | 查询失败（MySQL 未连接或不可用） |

```
{ "code": 500, "message": "查询失败（检查 MySQL 连接）: ..." }
```

**curl 示例：**

```bash
# 第 1 页，默认每页 20 条
curl "http://10.104.209.194:8080/admin/tasks?page=1"

# 按时间段过滤（URL 编码时间中的空格）
curl "http://10.104.209.194:8080/admin/tasks?start=2026-10-22%2000:00:00&end=2026-10-22%2023:59:59"
```

### GET /admin/stats

近 N 天按日、按场景汇总任务量，供统计网页 `/stats` 绘制堆叠柱状图。统计口径为区间内全部任务记录：同步 `done` 与异步各状态均计入；旧记录缺少 `scene` 时按 `people` 处理。本接口不校验 `IMG_SIEVE_ADMIN_TOKEN`。

**入参（Query String，可选）**

| 参数    | 类型 | 默认值 | 说明 |
| ----- | -- | --- | --- |
| `days` | int | `7` | 统计最近多少天；非整数按 `7` 处理，实际取值限制在 `1~30` |

**出参**

```json
{
  "code": 0,
  "message": "ok",
  "days": 7,
  "dates": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19",
             "2026-10-20", "2026-10-21", "2026-10-22"],
  "series": [
    {
      "key": "people",
      "label": "人脉识别",
      "color": "#0e9f8a",
      "values": [3, 5, 0, 2, 7, 4, 6]
    },
    {
      "key": "order",
      "label": "订单识别",
      "color": "#f59e0b",
      "values": [1, 0, 2, 0, 3, 1, 2]
    }
  ],
  "totals": { "people": 27, "order": 9, "total": 36 }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `days` | int | 实际统计天数 |
| `dates` | array | 日期升序，`YYYY-MM-DD`，缺失日期补零 |
| `series` | array | 每个场景一条；`values` 顺序与 `dates` 一一对应 |
| `series[].key` | string | `people`（人脉识别）/ `order`（订单识别） |
| `series[].label` | string | 场景展示名，用于图例与卡片标题 |
| `series[].color` | string | 场景图表颜色（十六进制） |
| `series[].values` | array | 对应日期该场景任务数量，无记录为 `0` |
| `totals.people` | int | 区间内人脉识别合计 |
| `totals.order` | int | 区间内订单识别合计 |
| `totals.total` | int | 场景合计之和 |

**错误返回**

| HTTP 状态 | `code` | 触发条件 |
| ------- | ------ | --- |
| `500` | `500` | 查询失败（MySQL 未连接或不可用） |

```json
{ "code": 500, "message": "查询失败（检查 MySQL 连接）: ..." }
```

**curl 示例：**

```bash
curl "http://10.104.209.194:8080/admin/stats?days=7"
```

### POST /admin/purge?days=N

清理 N 天以前的历史任务及其图片 BLOB，释放 MySQL 空间。

**入参（Query String）**

| 参数      | 类型     | 默认值 | 说明                                  |
| ------- | ------ | --- | ----------------------------------- |
| `days`  | int    | `5` | 保留最近 N 天，更早的任务连同图片一并删除              |
| `token` | string | —   | 设置 `IMG_SIEVE_ADMIN_TOKEN` 后必填，用于鉴权 |

**出参**

```json
{ "code": 0, "message": "ok", "days": 5, "deleted": 128 }
```

| 字段        | 类型     | 说明             |
| --------- | ------ | -------------- |
| `code`    | int    | `0` 成功         |
| `message` | string | 状态描述           |
| `days`    | int    | 本次清理使用的保留天数    |
| `deleted` | int    | 实际删除的任务条数（含图片） |

**错误返回**

| HTTP 状态 | code  | 触发条件                                         |
| ------- | ----- | -------------------------------------------- |
| `400`   | `400` | `days` 不是整数                                  |
| `403`   | `403` | 已配置 `IMG_SIEVE_ADMIN_TOKEN` 但 `token` 缺失或不匹配 |
| `500`   | `500` | 清理失败（MySQL 未连接或不可用）                          |

```
{ "code": 400, "message": "days 需为整数" }
{ "code": 403, "message": "需要正确的 token（?token=...）" }
```

建议用 crontab 定期执行（如每天凌晨清 7 天前）。该任务建议配置在**部署 img-sieve 的那台机器**上，用 `127.0.0.1` 直连，避免依赖网络路由：

```bash
0 3 * * * curl -s -X POST 'http://127.0.0.1:8080/admin/purge?days=7'
```
