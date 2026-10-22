#!/usr/bin/env python3
"""本地 CLI：识别截图并输出 JSON。

用法:
    # 人脉识别（默认）：主页/联系人 + 对话框两张图
    python3 cli.py <user_img> <chat_img> [--backend rapidocr|vision|tesseract|mock]
    python3 cli.py <img> [--role user|chat] ...  (多张图 + 角色标记)

    # 订单识别：单张订单截图（列表页 / 票据 / 详情页）
    python3 cli.py --mode order <order_img> [--backend rapidocr|vision|tesseract|mock]
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="img-sieve 截图信息提取（人脉/订单）")
    parser.add_argument("images", nargs="+", help="图片路径（通常 2 张；订单模式 1 张）")
    parser.add_argument("--backend", choices=["rapidocr", "vision", "tesseract", "mock"],
                        default=None, help="OCR 后端，默认自动选择")
    parser.add_argument("--mode", choices=["people", "order"], default="people",
                        help="people=人脉识别（默认）；order=订单截图识别")
    parser.add_argument("--role", action="append", default=[],
                        help="人脉识别：图片角色 user|chat，格式 <图片路径>=user，可多次")
    args = parser.parse_args(argv)

    from img_sieve.service import recognize_json, recognize_order_json

    if args.mode == "order":
        if len(args.images) != 1:
            parser.error("订单模式仅支持 1 张订单截图")
        print(recognize_order_json(args.images, backend=args.backend))
        return 0

    roles = {}
    for item in args.role:
        if "=" not in item:
            parser.error("--role 格式应为 <图片路径>=user|chat")
        p, role = item.split("=", 1)
        if role not in ("user", "chat"):
            parser.error("role 只能是 user 或 chat")
        roles[p] = role

    print(recognize_json(args.images, backend=args.backend,
                         explicit_roles=roles or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
