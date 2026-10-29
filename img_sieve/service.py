"""核心服务：图片 → OCR → 字段提取 → JSON。

两条业务线（共享同一套 OCR 后端，提取规则与响应结构相互独立）：

- recognize：人脉识别（抖音 / 小红书 / 微信），主页(联系人) + 对话框配对，
  提取昵称、平台账号、地区、手机号，并核对两处昵称是否一致。
- recognize_order：订单识别，单张订单截图（列表页 / 票据 / 详情页），抽取订单号列表。
"""
from __future__ import annotations

import json
import os
import tempfile

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


def _rotate_order_image(path, angle):
    """把单张票据旋转后写入临时 JPEG，返回临时路径；调用方负责删除。"""
    from PIL import Image

    tmp = tempfile.NamedTemporaryFile(prefix="order-rot-", suffix=".jpg", delete=False)
    tmp.close()
    try:
        with Image.open(path) as im:
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            im.rotate(angle, expand=True).save(tmp.name, format="JPEG", quality=95)
        return tmp.name
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def recognize_order(image_paths, backend=None):
    """订单识别：订单截图 → 订单号列表。

    支持的截图：订单列表页（一张图多个单号）、手工票据（OMS单号）、
    订单详情页 / 物流页 / 报表页（订单编号、订单编码、订单号码）、
    手机实拍图。出参格式保持一致。

    Args:
        image_paths: 图片绝对路径列表（订单接口常规为 1 张）
        backend: vision|tesseract|rapidocr|mock，默认 auto

    Returns:
        {
          "code": 0,
          "message": "ok",
          "backend": "rapidocr",
          "data": {
            "order_count": 4,
            "order_ids": ["...", "..."],
            "order_items": [{"label": "订单号", "order_id": "..."}]
          }
        }
    """
    results = ocr.run_ocr(image_paths, backend=backend)
    used_backend = backend or ocr.auto_driver(image_paths)
    data = build_order_result(results)

    # 方向兜底：手工票据可能横放/倒置；首遍未抽到单号时，仅对单张图尝试旋转。
    if (used_backend != "mock" and data["order_count"] == 0
            and len(image_paths) == 1):
        for angle in (90, 180, 270):
            tmp_path = None
            try:
                tmp_path = _rotate_order_image(image_paths[0], angle)
                rotated_results = ocr.run_ocr([tmp_path], backend=used_backend)
                rotated_data = build_order_result(rotated_results)
                if rotated_data["order_count"] > 0:
                    results, data = rotated_results, rotated_data
                    break
            except Exception:
                continue
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

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
