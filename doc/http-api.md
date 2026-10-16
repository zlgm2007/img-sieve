# img-sieve HTTP 接口对接说明

本文档描述 img-sieve 的 HTTP 接口，供第三方系统对接调用。

服务由 `./run.sh`（或 `./keepalive.sh start`）启动，纯 Python 标准库实现，内网离线部署。

## 服务地址

启动后默认监听 `0.0.0.0:8080`，根路径为网页界面。



```
./run.sh --host 0.0.0.0 --port 8080
```



***

## 接口一览



| 方法     | 路径           | 说明                          |
| ------ | ------------ | --------------------------- |
| `GET`  | `/`          | 网页界面（上传两张图 + 展示识别结果）        |
| `POST` | `/recognize` | 上传图片识别，返回结构化 JSON（**核心接口**） |
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

```java
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

```java
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

```java
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

---

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
>
> `platform`
>
>  为 
>
> `xiaohongshu`
>
> ，账号存于 
>
> `xiaohongshu_id`
>
> （如 
>
> `1346204393`
>
> ），
>
> `douyin_id`
>
>  为 
>
> `null`
>
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