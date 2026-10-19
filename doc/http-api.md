# img-sieve HTTP 接口对接说明

本文档描述 img-sieve 的 HTTP 接口，供第三方系统对接调用。

服务由 `./run.sh`（或 `./keepalive.sh start`）启动，纯 Python 标准库实现，内网离线部署。

## 服务地址

启动后默认监听 `0.0.0.0:8080`，根路径为网页界面。

**测试环境**：`http://10.237.179.21:8080/`（网页界面；接口 `POST /recognize`、`GET /health`）



```
./run.sh --host 0.0.0.0 --port 8080
```



***

## 接口一览



| 方法     | 路径           | 说明                          |
| ------ | ------------ | --------------------------- |
| `GET`  | `/`          | 网页界面（上传两张图 + 展示识别结果）        |
| `POST` | `/recognize` | 上传图片识别，返回结构化 JSON（**核心接口**，同步阻塞） |
| `POST` | `/recognize/async` | 异步识别：收图立即返回 `task_id`，后台识别（**多机部署用**） |
| `GET`  | `/task/{task_id}` | 按 task_id 查询异步任务状态与识别结果 |
| `POST` | `/admin/purge?days=N` | 清理 N 天前的历史任务与图片（运维） |
| `GET`  | `/health`    | 健康检查                        |



***

## 核心接口：POST /recognize

### 入参



* **Content-Type**：`multipart/form-data`

* **请求体大小**：1 \~ 50MB（超过 50MB 拒绝）

* **图片字段**：上传图片文件（2 张，至少 1 张）


  * **字段名任意**，服务按图片内容自动判断角色（主页 / 对话框）

  * 支持的图片格式：`.jpg / .jpeg / .png / .webp / .bmp / .gif`（按文件头校验）

* **角色覆盖（可选）**：表单文本字段 `role=<字段名>:user|chat`，人工指定某字段的角色

**角色自动判断规则**：



* 图片文本中含「抖音号」或「小红书号」声明 → 角色 `user`（主页）

* 图片文本中含 11 位大陆手机号 → 角色 `chat`（对话框）

**示例（curl）：**



```
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



```
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



***

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

    private static final String URL = "http://127.0.0.1:8080/recognize";

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

**解析返回 JSON（Jackson）：**



```
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;

String json = new ImgSieveClient().recognize(homeImg, chatImg);
JsonNode root = new ObjectMapper().readTree(json);
String nickname   = root.path("data").path("user").path("nickname").asText();
String phone      = root.path("data").path("phone").asText();
String chatName   = root.path("data").path("chat_object_name").asText();
boolean consistent = root.path("data").path("nickname_consistent").asBoolean();
```

> 图片仅在前端 → Java → img-sieve 之间流转，img-sieve 识别完即清理临时文件，图片不出内网。



***

### 出参

**成功（HTTP 200）：**



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



| 字段                                  | 类型            | 说明                                                        |
| ----------------------------------- | ------------- | --------------------------------------------------------- |
| `code`                              | int           | 状态码，`0` 表示成功                                              |
| `message`                           | string        | 状态描述                                                      |
| `backend`                           | string        | 实际使用的 OCR 后端：`rapidocr` / `vision` / `tesseract` / `mock` |
| `data.user.nickname`                | string\|null  | 主页用户昵称                                                    |
| `data.user.platform`                | string\|null  | 平台：`douyin` / `xiaohongshu`                               |
| `data.user.douyin_id`               | string\|null  | 抖音号（抖音平台）                                                 |
| `data.user.xiaohongshu_id`          | string\|null  | 小红书号（小红书平台）                                               |
| `data.phone`                        | string\|null  | 对话框中的手机号                                                  |
| `data.chat_object_name`             | string\|null  | 对话框对象昵称                                                   |
| `data.nickname_consistent`          | boolean\|null | 主页昵称与对话框对象昵称是否一致（`true` 一致 / `false` 不一致 / `null` 缺失无法判断） |
| `data.nickname_check.user`          | string\|null  | 主页昵称（比对用）                                                 |
| `data.nickname_check.chat`          | string\|null  | 对话框对象昵称（比对用）                                              |
| `data.nickname_check.compare_chars` | int           | 比对长度（固定 `5`，前 5 位前缀匹配）                                    |
| `data.nickname_check.consistent`    | boolean\|null | 与 `data.nickname_consistent` 等价                           |

> 小红书样例：
> `platform`
> 为
> `xiaohongshu`
> ，账号存于
> `xiaohongshu_id`
> （如
> `1346204393`
> ），
> `douyin_id`
> 为
> `null`
> 。



***

### 错误返回



| HTTP 状态 | code  | 触发条件                                                    |
| ------- | ----- | ------------------------------------------------------- |
| `400`   | `400` | 不是 `multipart/form-data`；请求体大小超出 1\~50MB；字段不是有效图片；未上传图片 |
| `404`   | `404` | 请求了不存在的路径                                               |
| `500`   | `500` | OCR 后端初始化 / 运行失败（如 rapidocr 不可用且无备用后端）                  |

错误响应示例：



```
{ "code": 400, "message": "请使用 multipart/form-data 上传图片" }
{ "code": 400, "message": "请至少上传 1 张图片" }
{ "code": 404, "message": "not found" }
```



***

## 健康检查：GET /health



```
{ "code": 0, "message": "ok", "status": "up" }
```



***

## 对接注意事项



* 返回 `Content-Type: application/json; charset=utf-8`，`Cache-Control: no-store`。

* 图片仅在服务端内存 / 临时文件中处理，识别完成后临时文件自动清理，图片不出内网。

* 服务支持并发请求（`ThreadingHTTPServer` 每请求一线程）。

* 启动时可用 `--backend` 强制指定 OCR 后端（`rapidocr|vision|tesseract|mock`），默认自动选择。

***

## 异步识别（多机部署）

当部署为多台机器共享一个 MySQL 时，用异步接口：调用方上传后立即拿到 `task_id`，由任意一台机器的 worker 后台识别，再轮询查询结果。同步接口 `/recognize` 也会把每次识别写进同一张任务表，两边数据在「任务记录」页都能查到。

### POST /recognize/async

入参与 `POST /recognize` 完全相同（`multipart/form-data`，上传 1~2 张图片，字段名任意、可带 `role=`）。

**响应**（立即返回，不等待识别）：

```json
{ "code": 0, "message": "ok", "task_id": "f3a2b1c4...", "status": "pending" }
```

**curl 示例：**

```bash
curl -X POST http://10.237.179.21:8080/recognize/async \
  -F "user=@主页.png" \
  -F "chat=@对话框.png"
# => {"code":0,"message":"ok","task_id":"9c1f...","status":"pending"}
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
  "created_at": "2026-10-19 11:00:00.123456",
  "started_at": "2026-10-19 11:00:01.200000",
  "finished_at": "2026-10-19 11:00:05.800000",
  "backend": "rapidocr",
  "result": { "code": 0, "message": "ok", "data": { "...": "同 /recognize 的 data 结构" } },
  "error": null
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | `0` 成功；`404` 任务不存在；`500` MySQL 连接失败 |
| `task_id` | string | 任务 ID |
| `status` | string | `pending` / `processing` / `done` / `failed` |
| `created_at` | string | 提交时间 |
| `started_at` | string\|null | worker 开始识别时间 |
| `finished_at` | string\|null | 识别完成时间 |
| `backend` | string\|null | 实际 OCR 后端 |
| `result` | object\|null | 识别结果，结构同 `/recognize` 的返回（含 `data`）；未完成时为 `null` |
| `error` | string\|null | 失败原因；成功时为 `null` |

建议轮询间隔 **1~2 秒**；识别完成（`status=done`）后停止轮询。单图识别约数秒（取决于服务器 CPU）。

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

    private static final String BASE = "http://10.237.179.21:8080";
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

### POST /admin/purge?days=N

清理 N 天以前的历史任务及其图片 BLOB，释放 MySQL 空间。返回删除条数：

```json
{ "code": 0, "message": "ok", "days": 5, "deleted": 128 }
```

可选保护：设置环境变量 `IMG_SIEVE_ADMIN_TOKEN` 后，调用需带 `?token=xxx`。建议用 crontab 定期执行（如每天凌晨清 7 天前）：

```bash
0 3 * * * curl -s -X POST 'http://127.0.0.1:8080/admin/purge?days=7'
```
