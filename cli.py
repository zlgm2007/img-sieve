#!/usr/bin/env python3
"""本地 CLI：识别 2 张抖音截图并输出 JSON。

用法:
    python3 cli.py <user_img> <chat_img> [--backend vision|tesseract|mock]
    python3 cli.py <img> [--role user|chat] ...  (多张图 + 角色标记)
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="抖音截图信息提取（昵称/抖音号/手机号）")
    parser.add_argument("images", nargs="+", help="图片路径（通常 2 张）")
    parser.add_argument("--backend", choices=["rapidocr", "vision", "tesseract", "mock"],
                        default=None, help="OCR 后端，默认自动选择")
    parser.add_argument("--role", action="append", default=[],
                        help="图片角色 user|chat，格式 <图片路径>=user，可多次")
    args = parser.parse_args(argv)

    from img_sieve.service import recognize_json

    roles = {}
    for item in args.role:
        if "=" not in item:
            parser.error("--role 格式应为 <图片路径>=user|chat")
        p, role = item.split("=", 1)
        if role not in ("user", "chat"):
            parser.error("role 只能是 user 或 chat")
        roles[p] = role

    print(recognize_json(args.images, backend=args.backend, explicit_roles=roles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
