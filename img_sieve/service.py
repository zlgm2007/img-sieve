"""核心服务：图片 → OCR → 字段提取 → JSON。

两条业务线（共享同一套 OCR 后端，提取规则与响应结构相互独立）：

- recognize：人脉识别（抖音 / 小红书 / 微信），主页(联系人) + 对话框配对，
  提取昵称、平台账号、地区、手机号，并核对两处昵称是否一致。
- recognize_order：订单识别，单张订单列表页，抽取全部订单号列表。
"""
from __future__ import annotations

import json

from . import ocr
from .extractor import build_result, build_order_result


def recognize(image_paths, backend=None, explicit_roles=None):
    """人脉识别：一组主页/对话框截图 → 结构化结果。

    Args:
        image_paths: 图片绝对路径列表（通常为 2 张：主页/联系人 + 对话框）
        backend: vision|tesseract|rapidocr|mock，默认 auto
        explicit_roles: 可选，{path: "user"|"chat"} 人工指定角色

    Returns:
        {
          "code": 0,
          "message": "ok",
          "backend": "rapidocr",
          "data": {
            "user": {"nickname", "platform", "douyin_id",
                     "xiaohongshu_id", "wechat_id", "region"},
            "phone": "18*********",
            "chat_object_name": "...",
            "nickname_consistent": True | False | None,
            "nickname_check": {...}
          }
        }
    """
    results = ocr.run_ocr(image_paths, backend=backend)
    used_backend = backend or ocr.auto_driver(image_paths)
    data = build_result(results, explicit_roles=explicit_roles)
    return {
        "code": 0,
        "message": "ok",
        "backend": used_backend,
        "data": data,
    }


def recognize_order(image_paths, backend=None):
    """订单识别：一张（或多张）订单列表页截图 → 订单号列表。

    Args:
        image_paths: 图片绝对路径列表（常规为 1 张）
        backend: vision|tesseract|rapidocr|mock，默认 auto

    Returns:
        {
          "code": 0,
          "message": "ok",
          "backend": "rapidocr",
          "data": {"order_count": 4, "order_ids": ["...", "..."]}
        }
    """
    results = ocr.run_ocr(image_paths, backend=backend)
    used_backend = backend or ocr.auto_driver(image_paths)
    data = build_order_result(results)
    return {
        "code": 0,
        "message": "ok",
        "backend": used_backend,
        "data": data,
    }


def recognize_json(image_paths, backend=None, explicit_roles=None, indent=2):
    """人脉识别并返回 JSON 字符串。"""
    return json.dumps(recognize(image_paths, backend=backend,
                                explicit_roles=explicit_roles),
                      ensure_ascii=False, indent=indent)


def recognize_order_json(image_paths, backend=None, indent=2):
    """订单识别并返回 JSON 字符串。"""
    return json.dumps(recognize_order(image_paths, backend=backend),
                      ensure_ascii=False, indent=indent)
