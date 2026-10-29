"""订单号抽取：从 OCR 结果中定位订单号及其来源标签。

覆盖的页面形态：

- 订单列表页：``订单号：210100027596213478``（一张图多条）
- 手工票据：``OMS单号：210100025074410605``（OCR 偶发把 O 误识为 Q，兼容 QMS）
- 订单详情页：``订单编号：210100025074400605``
- 详情页 / 物流页 / 报表页：``订单编码：D4002566297``、``订单号码：...``
- 手机实拍图：标签与编号可能错行，甚至完全没有冒号分隔

编号取值兼容三类写法：纯数字 10~20 位、字母前缀型（``B5002436100``）、
分组写法（``3802 2231 3273 5788 788``，返回时去掉空格）。
"""
from __future__ import annotations

import re

from .layout import group_rows

# 订单号标签：
# - 订单列表页：订单号：xxx（一张图可能有多条）
# - 订单详情页：订单编号：xxx（只取编号，不取订单行号/单号/商品编码）
# - 详情页 / 物流页 / 报表页还有：订单编码：xxx、订单号码：xxx
# - 手工票据：OMS单号：xxx（OCR 偶发把 O 误识为 Q，兼容 QMS）
# 标签按长度降序排列，避免「订单号」抢先匹配「订单号码」。
ORDER_LABEL_ALTS = r"订单号码|订单编号|订单编码|订单号|[OQ]MS单号"
ORDER_LABEL_RE = re.compile(r"(?P<label>" + ORDER_LABEL_ALTS + r")")
# 标签与编号之间的分隔符：冒号可有可无（半角 / 全角），也允许空白
_ORDER_SEP_RE = re.compile(r"[\s:：]*")
# 紧跟标签的订单号：
# - 连续写法：B5002436100 / 210100027596213478
# - 分组写法：3802 2231 3273 5788 788
_ORDER_VALUE_RE = re.compile(r"[A-Za-z]{0,3}\d+")
_ORDER_GROUPED_VALUE_RE = re.compile(r"\d{1,8}(?:\s+\d{1,8})+")
# 整段观察就是一个订单号（用于按空间位置配对）
_ORDER_PURE_VALUE_RE = re.compile(r"[A-Za-z]{0,3}\d+(?:[\s\-]+\d+)*")
# 订单号位数区间：
# - 纯数字型：10~20 位（苏宁 18 位、美团 19 位等）
# - 字母前缀型（B5002436100 / MM00061868）：6~20 位
ORDER_MIN_DIGITS = 10
ORDER_MIN_DIGITS_PREFIXED = 6
ORDER_MAX_DIGITS = 20
# 兼容旧引用：列表页原始规则。
ORDER_ID_RE = re.compile(r"订单号\s*[:：]\s*(\d{10,18})(?!\d)")


def _normalize_order_value(raw):
    """规范化订单号：去掉空格与连字符，校验位数；不合法返回 None。

    位数口径：纯数字 10~20 位；字母前缀型（如 B5002436100、MM00061868）6~20 位。
    """
    s = re.sub(r"[\s\-]+", "", raw or "")
    if not s:
        return None
    m = re.fullmatch(r"([A-Za-z]{0,3})(\d+)", s)
    if not m:
        return None
    letters, digits = m.group(1), m.group(2)
    n = len(digits)
    low = ORDER_MIN_DIGITS_PREFIXED if letters else ORDER_MIN_DIGITS
    if not (low <= n <= ORDER_MAX_DIGITS):
        return None
    return s


def _read_order_value(rest):
    """读取紧跟在标签之后的订单号，返回规范化值；不是订单号返回 None。

    rest 是标签之后的剩余文本（已跳过冒号与空白）。
    """
    m = _ORDER_VALUE_RE.match(rest)
    if m:
        value = _normalize_order_value(m.group(0))
        if value:
            return value
    # 分组写法：3802 2231 3273 5788 788
    m = _ORDER_GROUPED_VALUE_RE.match(rest)
    if m:
        return _normalize_order_value(m.group(0))
    return None


def _scan_order_pairs(text):
    """扫描一段文本里的「标签 + 订单号」组合，返回 [(label, order_id)]。

    标签与编号之间允许有冒号（半角 / 全角）或空白，也允许完全没有分隔符
    （如手机拍照的「订单号码3802223132735788788」）。
    """
    pairs = []
    for m in ORDER_LABEL_RE.finditer(text or ""):
        rest = text[m.end():]
        sep = _ORDER_SEP_RE.match(rest)
        value = _read_order_value(rest[sep.end():])
        if value:
            pairs.append((m.group("label"), value))
    return pairs


def _pure_order_value(text):
    """整段观察文本就是一个订单号时返回规范化值，否则 None。"""
    t = (text or "").strip()
    if not t or not _ORDER_PURE_VALUE_RE.fullmatch(t):
        return None
    return _normalize_order_value(t)


def _match_order_by_position(obs):
    """标签与编号分属不同观察、且不在同一行时，按空间位置就近配对。

    手机拍照常有透视 / 倾斜，标签行与编号行可能错开，此时同行合并兜底会失效。
    仅在「标签观察里没有编号」且「候选观察整段就是一个编号」时才配对，
    避免把相邻的其它数字（日期、金额、状态码）误认成订单号。
    """
    out = []
    for o in obs:
        text = o.get("text", "")
        if not ORDER_LABEL_RE.search(text):
            continue
        if _scan_order_pairs(text):
            continue  # 该观察已带编号，无需空间配对
        b = o["box"]
        best = None
        best_key = None
        for v in obs:
            if v is o:
                continue
            value = _pure_order_value(v.get("text", ""))
            if value is None:
                continue
            vb = v["box"]
            dy = vb["y1"] - b["y1"]
            if dy < -10 or dy > 45:
                continue  # 编号须在标签同行或紧邻下一行
            if vb["x1"] < b["x1"] - 30:
                continue  # 水平方向不得明显偏左
            key = (abs(dy), vb["x1"])
            if best_key is None or key < best_key:
                best_key = key
                best = (v, value)
        if best is not None:
            v, value = best
            label = ORDER_LABEL_RE.search(text).group("label")
            out.append((label, value, v["box"]["y1"]))
    return out


def extract_order_items(obs):
    """从单张订单截图抽取全部订单号及其来源标签。

    三级匹配：
    1. 逐观察匹配：标签与编号同在一条观察内（最常见）；
    2. 同行合并匹配：标签与编号被 OCR 拆成两条观察但在同一行；
    3. 空间就近匹配：标签与编号不在同一行（手机拍照常见错位）。

    返回 [(label, order_id, y1)]，按页面从上到下排序。
    """
    items = []
    seen = set()

    def _push(label, value, y1):
        if value and value not in seen:
            seen.add(value)
            items.append((label, value, y1))

    # 1) 逐观察匹配
    for o in obs:
        for label, value in _scan_order_pairs(o.get("text", "")):
            _push(label, value, o["box"]["y1"])

    # 2) 按行合并兜底：补齐标签与编号被 OCR 拆成两条观察的情况。
    #    用锚定式分行（anchor_first），避免把相邻行的金额 / 日期拼到单号后面。
    for row in group_rows(obs, anchor_first=True):
        row = sorted(row, key=lambda o: o["box"]["x1"])
        line = "".join(o.get("text", "") for o in row)
        y1 = min(o["box"]["y1"] for o in row)
        for label, value in _scan_order_pairs(line):
            _push(label, value, y1)

    # 3) 空间就近匹配：标签与编号不在同一行时按坐标配对
    for label, value, y1 in _match_order_by_position(obs):
        _push(label, value, y1)

    items.sort(key=lambda x: x[2])
    return items


def extract_order_ids(obs):
    """从单张订单截图抽取全部订单号（只返回值，按页面阅读顺序）。

    支持的标签：订单号、订单编号、订单编码、订单号码、OMS单号（兼容 QMS单号）。
    支持的值：纯数字 10~20 位、字母前缀型（如 B5002436100）、空格分组写法。
    """
    return [value for _label, value, _y1 in extract_order_items(obs)]


def build_order_result(results):
    """组装「订单识别」最终 JSON 数据：跨图合并去重，按阅读顺序返回订单号列表。

    返回 {"order_count", "order_ids", "order_items"}；
    order_items 额外给出每个订单号的来源标签，便于核对识别口径。
    """
    order_ids = []
    order_items = []
    seen = set()
    for r in results:
        for label, oid, _y1 in extract_order_items(r["observations"]):
            if oid in seen:
                continue
            seen.add(oid)
            order_ids.append(oid)
            order_items.append({"label": label, "order_id": oid})
    return {"order_count": len(order_ids), "order_ids": order_ids,
            "order_items": order_items}
