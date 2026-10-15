#!/usr/bin/env python3
"""单元测试：字段提取规则（用权威 OCR 夹具验证，含中文昵称）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from img_sieve import ocr  # noqa: E402
from img_sieve.extractor import (extract_phone, extract_user_info,  # noqa: E402
                                 extract_chat_object_name, compare_nicknames,
                                 detect_platform, build_result, detect_roles)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_IMG = os.path.join(BASE, "raw", "20261015115909_4787_84.jpg")
CHAT_IMG = os.path.join(BASE, "raw", "20261015115910_4788_84.jpg")
XHS_IMG = os.path.join(BASE, "raw", "xhs20261015144009325.jpg")


class TestExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = ocr.run_mock([USER_IMG, CHAT_IMG])
        cls.user_result = next(r for r in cls.results if "4787" in r["path"])
        cls.chat_result = next(r for r in cls.results if "4788" in r["path"])

    def test_user_nickname_and_id(self):
        u = extract_user_info(self.user_result["observations"])
        self.assertEqual(u["nickname"], "叫我大猫")
        self.assertEqual(u["douyin_id"], "jiaowodamao996")

    def test_phone(self):
        p = extract_phone(self.chat_result["observations"])
        self.assertEqual(p["phone"], "18652050583")

    def test_chat_object_name(self):
        name = extract_chat_object_name(self.chat_result["observations"])
        self.assertEqual(name, "多金小肉肉")

    def test_compare_nicknames(self):
        self.assertTrue(compare_nicknames("叫我大猫", "叫我大猫"))
        self.assertFalse(compare_nicknames("叫我大猫", "多金小肉肉"))
        self.assertIsNone(compare_nicknames("", "多金小肉肉"))
        self.assertIsNone(compare_nicknames("叫我大猫", None))
        # 昵称过长时只比前 5 位，避免截断误判
        self.assertTrue(compare_nicknames("叫我大猫的故事很长很长",
                                          "叫我大猫"))
        self.assertFalse(compare_nicknames("叫我大猫的故事很长很长",
                                           "叫我二猫"))

    def test_detect_roles(self):
        roles = detect_roles(self.results)
        self.assertEqual(roles.get(USER_IMG), "user")
        self.assertEqual(roles.get(CHAT_IMG), "chat")

    def test_build_result(self):
        data = build_result(self.results)
        self.assertEqual(data["user"]["nickname"], "叫我大猫")
        self.assertEqual(data["user"]["douyin_id"], "jiaowodamao996")
        self.assertEqual(data["phone"], "18652050583")
        # 主页昵称 与 对话框对象昵称 不一致（夹具中为两人）
        self.assertEqual(data["chat_object_name"], "多金小肉肉")
        self.assertIs(data["nickname_check"]["consistent"], False)
        self.assertEqual(data["nickname_check"]["compare_chars"], 5)

    def test_xiaohongshu_homepage(self):
        # 小红书主页：昵称 + 小红书号 + 平台
        res = ocr.run_mock([XHS_IMG])
        self.assertEqual(len(res), 1)
        u = extract_user_info(res[0]["observations"])
        self.assertEqual(u["platform"], "xiaohongshu")
        self.assertEqual(u["nickname"], "滴滴嗒哒")
        self.assertEqual(u["xiaohongshu_id"], "1346204393")
        self.assertIsNone(u["douyin_id"])
        # 角色检测：小红书主页应判为 user
        roles = detect_roles(res)
        self.assertEqual(roles.get(res[0]["path"]), "user")

    def test_explicit_role_override(self):
        # 若显式指定反了角色，应遵循显式指定
        data = build_result(self.results,
                            explicit_roles={CHAT_IMG: "user", USER_IMG: "chat"})
        # chat 图无抖音号 → 昵称/抖音号应为空
        self.assertIsNone(data["user"]["douyin_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
