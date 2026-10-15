"""核心服务：图片 → OCR → 字段提取 → JSON。"""
from __future__ import annotations

import json

from . import ocr
from .extractor import build_result


def recognize(image_paths, backend=None, explicit_roles=None):
    """识别一组图片并返回结构化结果。

    Args:
        image_paths: 图片绝对路径列表（通常为 2 张：主页 + 对话框）
        backend: vision|tesseract|mock，默认 auto
        explicit_roles: 可选，{path: "user"|"chat"} 人工指定角色

    Returns:
        {
          "code": 0,
          "message": "ok",
          "backend": "vision|tesseract|mock",
          "data": {
            "user": {"nickname": ..., "douyin_id": ...},
            "phone": "18*********",
            "chat": {"raw": ...}
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


def recognize_json(image_paths, backend=None, explicit_roles=None, indent=2):
    """识别并返回 JSON 字符串。"""
    return json.dumps(recognize(image_paths, backend=backend,
                                explicit_roles=explicit_roles),
                      ensure_ascii=False, indent=indent)
