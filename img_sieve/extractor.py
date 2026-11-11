"""字段提取规则：从 OCR 结果中提取 用户昵称 / 抖音号 / 小红书号 / 微信号 / 地区 / 手机号码。

订单号抽取规则见 `order_extract.py`（从本模块转发，保持历史导入路径可用）。

输入为 ocr.run_ocr 返回的规范化结果：
[
  {"path":..., "width":..., "height":..., "observations":[{"text","confidence","box"}...]}
]
"""
from __future__ import annotations

import re

from .layout import group_rows
from .order_extract import (ORDER_ID_RE, build_order_result,  # noqa: F401
                            extract_order_ids, extract_order_items)

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")

# 各平台账号声明行 → (精确匹配, 宽松匹配, 输出字段名)
PLATFORM_DECLS = {
    "douyin": (
        re.compile(r"抖音号\s*[:：]\s*([A-Za-z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"抖音号\s*[:：]\s*([A-Za-z0-9_\-]+)?", re.IGNORECASE),
        "douyin_id",
    ),
    "xiaohongshu": (
        re.compile(r"小红书号\s*[:：]\s*([A-Za-z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"小红书号\s*[:：]\s*([A-Za-z0-9_\-]+)?", re.IGNORECASE),
        "xiaohongshu_id",
    ),
    "wechat": (
        re.compile(r"微信号\s*[:：]\s*([A-Za-z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"微信号\s*[:：]\s*([A-Za-z0-9_\-]+)?", re.IGNORECASE),
        "wechat_id",
    ),
}

# 平台标签关键字 → 平台
PLATFORM_LABEL = {
    "抖音号": "douyin",
    "小红书号": "xiaohongshu",
    "微信号": "wechat",
}

# 微信「地区」声明行（仅微信联系人页）
REGION_RE = re.compile(r"地区\s*[:：]\s*(.+)")

# 顶部状态栏 / 导航等干扰文本（不当作昵称）
_NOISE = {
    "添加好友", "新访客", "搜索", "菜单", "编辑主页", "创建AI形象",
    "首页", "消息", "我", "作品", "日常", "推荐", "收藏", "喜欢",
    # 微信联系人页导航 / 按钮
    "朋友资料", "权限", "仅聊天", "朋友圈", "发消息", "音视频通话",
    "备注", "标签",
    # 企微客户详情页菜单 / 标题 / 按钮
    "客户详情", "设置备注和描述", "用户自助服务", "添加时间", "来源",
    "语音通话", "新媒体小红书",
    # 小红书按钮 / 平台副标识
    "关注", "发私信", "@微信",
}

# 聊天窗口横幅 / 提示（不当作对象昵称）
_BANNER_NOISE = ("通知", "保障", "安全", "请遵守", "消息", "提示",
                 "违法违规", "陌生人", "法律法规", "已开启",
                 "加个关注", "方便以后", "常聊", "关注", "发私信")

# 社交媒体统计行（如 "6关注" "10粉丝" "12获赞与收藏"）
_STATS_RE = re.compile(r"^\d+\s*(关注|粉丝|获赞与收藏|获赞|赞)$")

# 水印文本：@公司名+数字 / 8 位以上连续数字（订单号、时间戳等）
_WATERMARK_RE = re.compile(r"@[^@\n]{0,8}\d{3,}|\d{8,}")

# 昵称一致性对比的最少字符数
NICKNAME_COMPARE_CHARS = 5


def _norm_text(t: str) -> str:
    return t.strip()


def detect_platform(obs):
    """根据截图文本判断平台：douyin / xiaohongshu / wechat / None。

    微信联系人页可能只露出「地区」行而无微信号声明，也据此判为 wechat。
    企微「客户详情」页（含「设置备注和描述」菜单）也判为 wechat。
    """
    for o in obs:
        t = o.get("text", "")
        for label, platform in PLATFORM_LABEL.items():
            if label in t:
                return platform
        if REGION_RE.search(t):
            return "wechat"
    # 企微客户详情页：无「微信号:」声明，但有「设置备注和描述」菜单
    all_text = " ".join(o.get("text", "") for o in obs)
    if "设置备注和描述" in all_text or "客户详情" in all_text:
        return "wechat"
    return None


def _extract_region(obs):
    """从微信联系人页提取地区文本（『地区：江苏 南京』），未识别到返回 None。"""
    for o in obs:
        m = REGION_RE.search(o["text"])
        if m:
            region = re.sub(r"\s+", " ", m.group(1).strip())
            if region:
                return region
    return None


def extract_user_info(obs):
    """从个人主页 / 联系人页截图提取昵称与账号信息。

    兼容抖音（抖音号: xxx）、小红书（小红书号: xxx）、微信（微信号: xxx + 地区）。
    昵称定位共用『账号行正上方一行文本』的启发式。

    obs: 单张图片的 observations 列表（含 text / box）。
    返回 {"nickname", "platform", "douyin_id", "xiaohongshu_id",
          "wechat_id", "region} 字段，未识别到为 None。
    """
    platform = detect_platform(obs)
    id_obs = None
    id_val = None
    id_key = None

    if platform is not None and platform in PLATFORM_DECLS:
        decl_re, decl_any_re, id_key = PLATFORM_DECLS[platform]
        label = next(k for k, p in PLATFORM_LABEL.items() if p == platform)
        for o in obs:
            m = decl_re.search(o["text"])
            if m:
                id_val = m.group(1)
                id_obs = o
                break
            if label in o["text"]:
                id_obs = o

        # 兜底：若声明行未匹配到 id，尝试从该行文本尾部抓取字母数字串
        if id_obs is not None and id_val is None:
            m = re.search(r"([A-Za-z0-9_\-]{3,})$", id_obs["text"])
            if m:
                id_val = m.group(1)

    nickname = _extract_nickname(obs, id_obs)
    result = {"nickname": nickname, "platform": platform,
              "douyin_id": None, "xiaohongshu_id": None,
              "wechat_id": None, "region": None}
    if id_key is not None:
        result[id_key] = id_val
    if platform == "wechat":
        result["region"] = _extract_region(obs)
    return result


def _extract_nickname(obs, id_obs):
    """昵称通常位于『账号声明行』的正上方且水平居中对齐。"""
    if id_obs is not None:
        d = id_obs["box"]
        d_cx = (d["x1"] + d["x2"]) / 2.0
        best = None
        best_dist = None
        for o in obs:
            if o is id_obs:
                continue
            b = o["box"]
            # 昵称行底缘应贴近账号声明行顶缘（在其正上方）
            if not (d["y1"] - 60 <= b["y2"] <= d["y1"] + 10):
                continue
            # 水平上需与账号声明行重叠（居中）
            o_cx = (b["x1"] + b["x2"]) / 2.0
            overlap = min(d["x2"], b["x2"]) - max(d["x1"], b["x1"])
            if overlap < (d["x2"] - d["x1"]) * 0.2:
                continue
            dist = abs(o_cx - d_cx) + abs(b["y2"] - d["y1"])
            if best is None or dist < best_dist:
                best = o
                best_dist = dist
        if best is not None:
            t = _norm_text(best["text"])
            if t and t not in _NOISE:
                return _clean_name(t)

    # 兜底：顶部区域内最长的、含中文或英文短语的文本
    id_text = id_obs["text"] if id_obs is not None else ""
    candidates = []
    for o in obs:
        t = _norm_text(o["text"])
        b = o["box"]
        if not t or t in _NOISE:
            continue
        if b["y1"] < 50 or b["y1"] > 400:
            continue
        # 排除账号声明行本身（如 "小红书号：1802052432"）
        if t == id_text:
            continue
        if any(label in t for label in PLATFORM_LABEL):
            continue
        # 排除水印
        if _WATERMARK_RE.search(t):
            continue
        # 排除明显非昵称的纯数字 / 时间
        if re.fullmatch(r"\d{1,4}", t):
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            continue
        # 排除 IP 属地行
        if t.startswith("IP") or t.startswith("ip"):
            continue
        # 排除社交媒体统计行（"6关注" "10粉丝" "12获赞与收藏"）
        if _STATS_RE.match(t):
            continue
        if len(t) < 2:
            continue
        candidates.append((len(t), t, b))
    if candidates:
        candidates.sort(key=lambda x: (-x[0], x[2]["y1"]))
        return _clean_name(candidates[0][1])
    return None


# 对话框顶部标题/状态栏纵向区间（千分比）：标题里内嵌的手机号不属于消息手机号
_PHONE_BAND_Y1 = 220


def extract_phone(obs):
    """从聊天对话框截图提取手机号码（仅消息体，排除顶部标题带）。

    数字可能被空格/符号拆开（如 "186 520 50 583 测试"），
    因此按行合并后去除非数字再匹配 11 位大陆手机号。
    返回 {"phone": str|None, "raw": str|None}
    """
    # 按行聚合（同一 y 带）
    rows = group_rows(obs)
    for row in rows:
        if min(o["box"]["y1"] for o in row) < _PHONE_BAND_Y1:
            continue  # 顶部标题 / 状态栏，不是消息体
        line = "".join(o["text"] for o in row)
        digits = re.sub(r"\D", "", line)
        if MOBILE_RE.fullmatch(digits):
            return {"phone": digits, "raw": line.strip()}

    # 兜底：逐条观察文本内找完整手机号
    for o in obs:
        if o["box"]["y1"] < _PHONE_BAND_Y1:
            continue
        for token in re.findall(r"[0-9][0-9\s\-]{9,}", o["text"]):
            digits = re.sub(r"\D", "", token)
            if MOBILE_RE.fullmatch(digits):
                return {"phone": digits, "raw": o["text"].strip()}
    return {"phone": None, "raw": None}


def detect_roles(results):
    """自动判断两张图各自角色：user(主页/联系人) / chat(对话框)。

    含账号声明（抖音号/小红书号/微信号）或地区声明的 → user；
    含企微客户详情标记（设置备注和描述/客户详情）的 → user；
    含 11 位大陆手机号的 → chat；
    含聊天输入框（发消息或按住/发消息…）的 → chat；
    其余图片若顶部存在居中标题（微信对话框，消息体里不带手机号）→ chat。
    返回 {path: "user"|"chat"}。
    """
    roles = {}
    for r in results:
        text_all = "".join(o["text"] for o in r["observations"])
        is_user = (any(label in text_all for label in PLATFORM_LABEL)
                   or REGION_RE.search(text_all)
                   or "设置备注和描述" in text_all
                   or "客户详情" in text_all)
        if is_user:
            roles[r["path"]] = "user"
            continue
        phone = extract_phone(r["observations"])["phone"]
        if phone:
            roles[r["path"]] = "chat"
            continue
        # 聊天输入框（底部）：「发消息或按住…」「发消息·」
        if re.search(r"发消息[或·…\.]", text_all):
            roles[r["path"]] = "chat"
            continue
        # 微信对话框兜底：顶部带存在居中标题候选
        if extract_chat_object_name(r["observations"]):
            roles[r["path"]] = "chat"
    return roles


def _clean_name(t):
    """清洗昵称：去掉前导符号；中文昵称尾部大写字母/图标常被误识为孤立字母
    （Q/D 等）或小人形图标（l/1/|），一并剥除；小写字母（如 "皮蛋y" 的 y）保留。"""
    t = re.sub(r"^[^\w\u4e00-\u9fff]+", "", t or "").strip()
    if re.search(r"[\u4e00-\u9fff]", t):
        t = re.sub(r"[A-Z✓√◆●◇•l1|]{1,2}$", "", t).strip()
    return t


def extract_chat_object_name(obs):
    """从聊天对话框截图提取『对象昵称』。

    对象昵称位于屏幕顶部状态栏下方、消息列表上方，为居中的短文本
    （微信昵称可内嵌手机号，长度上限放宽到 20）。
    通过 顶部带 + 排除横幅/状态/纯数字 过滤后，取最靠上的一条。
    返回 str | None。
    """
    candidates = []
    for o in obs:
        t = _norm_text(o["text"])
        b = o["box"]
        if not t:
            continue
        # 顶部带：状态栏下方到消息列表上方（标题在 y~50-130，消息从 y~130+ 开始）
        if not (40 <= b["y1"] <= 160):
            continue
        # 水平居中（排除左上角徽标/右上角按钮）
        cx = (b["x1"] + b["x2"]) / 2.0
        if not (150 <= cx <= 850):
            continue
        # 排除状态栏纯数字/时间/纯符号（但保留含字母的昵称，如 "1r.yy"）
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            continue
        if re.fullmatch(r"\d+", t):
            continue
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", t):
            # 既无字母也无中文 → 纯符号/标点，不当昵称
            continue
        if len(t) < 2 or len(t) > 20:
            continue
        # 排除系统横幅 / 长句
        if any(k in t for k in _BANNER_NOISE):
            continue
        # 排除平台副标识（如 "@微信"）
        if t.startswith("@"):
            continue
        cleaned = _clean_name(t)
        if len(cleaned) < 2:
            continue
        candidates.append((cleaned, o))

    if not candidates:
        return None
    # 取最靠上；同 y 取水平跨度最大（更可能是居中的标题）
    candidates.sort(key=lambda c: (c[1]["box"]["y1"], -len(c[0])))
    return candidates[0][0]


def compare_nicknames(user_nickname, chat_object_name, n=NICKNAME_COMPARE_CHARS):
    """比对主页昵称与对话框对象昵称是否一致。

    各取前 n 位后做前缀匹配：较短方是较长方的前缀即视为一致，
    从而容忍昵称过长在某侧被截断显示的情况。

    返回:
        True  一致
        False 不一致
        None  其中一方缺失，无法判断
    """
    u = (user_nickname or "").strip()[:n]
    c = (chat_object_name or "").strip()[:n]
    if not u or not c:
        return None
    return u.startswith(c) or c.startswith(u)


def detect_tiny_nickname(path, obs, role):
    """像素级兜底：OCR 漏掉的单字符昵称（如 "."）检测。

    当 OCR 在预期昵称位置找不到任何文本，但该位置存在一个孤立的小像素簇
    （2~6px，即一个句点大小），推断昵称为 "."。

    仅在以下条件满足时触发：
    - chat 图：顶部 bar 中头像右侧、"..."菜单左侧有小像素簇
    - user 图：平台账号声明行正上方有小像素簇
    处理浅色模式（黑字白底）和深色模式（白字黑底）。
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None

    try:
        with Image.open(path) as im:
            w, h = im.size
            arr = np.array(im.convert("L"))
    except Exception:
        return None

    # 判断深色/浅色模式：取顶部背景均值
    bg_sample = arr[max(0, int(h*0.02)):int(h*0.04), int(w*0.3):int(w*0.7)]
    bg_mean = bg_sample.mean() if bg_sample.size else 255
    dark_mode = bg_mean < 128

    # 找昵称扫描区域（千分比坐标转像素）
    if role == "chat":
        # 顶部 bar：y 约 5%~10%，在头像右侧（x>20%）到菜单左侧（x<90%）
        y1, y2 = int(h*0.05), int(h*0.10)
        x1, x2 = int(w*0.20), int(w*0.90)
    else:
        # user 图：找账号声明行，在其正上方紧邻处扫描（不含声明行本身）
        id_box = None
        for o in obs:
            for label in PLATFORM_LABEL:
                if label in o["text"]:
                    id_box = o["box"]
                    break
            if id_box:
                break
        if id_box is None:
            return None
        # 千分比转像素；昵称在声明行上方 1%~7% 高度范围内
        id_y_px = int(id_box["y1"] / 1000.0 * h)
        y2 = id_y_px - 3  # 排除声明行本身
        y1 = max(0, y2 - int(h*0.05))
        x1 = int(id_box["x1"] / 1000.0 * w) - 10
        x2 = int(id_box["x2"] / 1000.0 * w) + 10

    region = arr[y1:y2, x1:x2]
    if region.size == 0:
        return None

    # 根据模式确定前景色阈值
    if dark_mode:
        fg_mask = region > bg_mean + 40  # 浅色前景
    else:
        fg_mask = region < bg_mean - 40  # 深色前景

    # 找前景像素簇
    fg_rows = np.where(fg_mask.any(axis=1))[0]
    if len(fg_rows) == 0:
        return None

    fg_cols = np.where(fg_mask.any(axis=0))[0]
    px_count = int(fg_mask.sum())
    cluster_h = int(fg_rows.max() - fg_rows.min() + 1)
    cluster_w = int(fg_cols.max() - fg_cols.min() + 1)

    # 单字符（句点）特征：像素极少，簇宽高都很小（2~10px）
    # 正常文字 OCR 一定能检出，走到这里说明簇极小
    if px_count <= 40 and cluster_h <= 10 and cluster_w <= 10:
        return "."
    return None


def build_result(results, explicit_roles=None):
    """组装「人脉识别」最终 JSON 数据（抖音 / 小红书 / 微信共用）。

    explicit_roles: {path: "user"|"chat"}，可选，用于人工指定角色。
    返回 {
        "user": {"nickname", "platform", "douyin_id", "xiaohongshu_id",
                 "wechat_id", "region"},
        "phone": "18*********",
        "chat_object_name": "多金***",
        "nickname_check": {
            "user": ..., "chat": ...,
            "compare_chars": 5,
            "consistent": True | False | None
        }
    }
    """
    roles = dict(explicit_roles or {})
    auto = detect_roles(results)
    for path, role in auto.items():
        roles.setdefault(path, role)

    user = {"nickname": None, "platform": None,
            "douyin_id": None, "xiaohongshu_id": None,
            "wechat_id": None, "region": None}
    phone = None
    raw = None
    chat_object_name = None

    for r in results:
        role = roles.get(r["path"])
        if role == "user":
            u = extract_user_info(r["observations"])
            user = {"nickname": u["nickname"], "platform": u["platform"],
                    "douyin_id": u["douyin_id"],
                    "xiaohongshu_id": u["xiaohongshu_id"],
                    "wechat_id": u["wechat_id"], "region": u["region"]}
            # 像素级兜底：OCR 漏掉的单字符昵称
            if user["nickname"] is None:
                tiny = detect_tiny_nickname(r["path"], r["observations"], "user")
                if tiny:
                    user["nickname"] = tiny
        elif role == "chat":
            p = extract_phone(r["observations"])
            phone = p["phone"]
            raw = p["raw"]
            chat_object_name = extract_chat_object_name(r["observations"])
            # 像素级兜底：OCR 漏掉的单字符聊天对象昵称
            if chat_object_name is None:
                tiny = detect_tiny_nickname(r["path"], r["observations"], "chat")
                if tiny:
                    chat_object_name = tiny

    consistent = compare_nicknames(user["nickname"], chat_object_name)

    data = {
        "user": user,
        "phone": phone,
        "chat_object_name": chat_object_name,
        "nickname_consistent": consistent,
        "nickname_check": {
            "user": user["nickname"],
            "chat": chat_object_name,
            "compare_chars": NICKNAME_COMPARE_CHARS,
            "consistent": consistent,
        },
    }
    return data
