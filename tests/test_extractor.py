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
                                 detect_platform, build_result, detect_roles,
                                 extract_order_ids, build_order_result)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_IMG = os.path.join(BASE, "raw", "20261015115909_4787_84.jpg")
CHAT_IMG = os.path.join(BASE, "raw", "20261015115910_4788_84.jpg")
XHS_IMG = os.path.join(BASE, "raw", "xhs20261015144009325.jpg")
WX_USER_IMG = os.path.join(BASE, "raw", "wx20261022094534150.jpg")
WX_CHAT_IMG = os.path.join(BASE, "raw", "wx20261022094522060.jpg")
ORDER_IMG = os.path.join(BASE, "raw", "order20261022094541207.jpg")


def _order_obs(text, y1=100, x1=0):
    """构造一条订单截图 OCR 观察（测试用）。"""
    return {
        "text": text,
        "confidence": 0.9,
        "box": {"x1": x1, "y1": y1, "x2": x1 + 240, "y2": y1 + 20},
    }


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


class TestWechatExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = ocr.run_mock([WX_USER_IMG, WX_CHAT_IMG, ORDER_IMG])
        cls.user = next(r for r in cls.results if "34150" in r["path"])
        cls.chat = next(r for r in cls.results if "22060" in r["path"])
        cls.order = next(r for r in cls.results if "094541207" in r["path"])

    def test_wechat_contact_fields(self):
        u = extract_user_info(self.user["observations"])
        self.assertEqual(u["platform"], "wechat")
        self.assertEqual(u["nickname"], "王云13951905083")
        self.assertEqual(u["wechat_id"], "Z071216xy")
        self.assertEqual(u["region"], "江苏南京")
        self.assertIsNone(u["douyin_id"])
        self.assertIsNone(u["xiaohongshu_id"])

    def test_wechat_chat_title(self):
        # 标题旁备注图标被 OCR 误识的尾部 Q 应被清洗
        self.assertEqual(extract_chat_object_name(self.chat["observations"]),
                         "王云13951905083")

    def test_wechat_roles(self):
        roles = detect_roles([self.user, self.chat])
        self.assertEqual(roles.get(self.user["path"]), "user")
        self.assertEqual(roles.get(self.chat["path"]), "chat")

    def test_build_result_wechat_consistent(self):
        data = build_result([self.user, self.chat])
        self.assertEqual(data["user"]["nickname"], "王云13951905083")
        self.assertEqual(data["user"]["wechat_id"], "Z071216xy")
        self.assertEqual(data["user"]["region"], "江苏南京")
        self.assertEqual(data["chat_object_name"], "王云13951905083")
        self.assertIs(data["nickname_check"]["consistent"], True)
        self.assertIsNone(data["phone"])


class TestOrderExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = ocr.run_mock([ORDER_IMG])
        cls.order = cls.results[0]

    def test_order_extraction_ordered(self):
        ids = extract_order_ids(self.order["observations"])
        self.assertEqual(ids, [
            "210100027596213478",
            "210100027600353478",
            "210100027598393478",
            "210100027600333478",
        ])

    def test_build_order_result(self):
        data = build_order_result([self.order])
        self.assertEqual(data["order_count"], 4)
        self.assertEqual(len(data["order_ids"]), 4)
        # 商品编码 / 水印 / 日期不得混入订单号列表
        self.assertNotIn("DA1329440", data["order_ids"])
        self.assertNotIn("12449819689", data["order_ids"])

    def test_oms_ticket_excludes_other_labels(self):
        obs = [
            _order_obs("订单编号：MM00061870", y1=311),
            _order_obs("单号：DA1153620", y1=385),
            _order_obs("OMS单号：210100025074430605", y1=406),
        ]
        self.assertEqual(extract_order_ids(obs),
                         ["210100025074430605"])

    def test_qms_is_oms_ocr_typo(self):
        # 横版票据上 OCR 偶发把 O 识为 Q，规则兼容 QMS 前缀
        obs = [_order_obs("QMS单号：210100025074410605")]
        self.assertEqual(extract_order_ids(obs),
                         ["210100025074410605"])

    def test_detail_order_number_excludes_row_id(self):
        obs = [
            _order_obs("订单行号：2101000250744306050", y1=392),
            _order_obs("订单编号：210100025074420605", y1=799),
        ]
        self.assertEqual(extract_order_ids(obs),
                         ["210100025074420605"])

    def test_oms_split_label_and_code(self):
        obs = [
            _order_obs("OMS单号：", y1=100, x1=46),
            _order_obs("210100025074430605", y1=100, x1=180),
        ]
        self.assertEqual(extract_order_ids(obs),
                         ["210100025074430605"])

    def test_detail_split_label_and_code(self):
        obs = [
            _order_obs("订单编号：", y1=200, x1=30),
            _order_obs("210100025074420605", y1=200, x1=180),
        ]
        self.assertEqual(extract_order_ids(obs),
                         ["210100025074420605"])

    def test_order_list_one_complete_one_split(self):
        obs = [
            _order_obs("订单号：210100025074400605", y1=100),
            _order_obs("订单号：", y1=200, x1=30),
            _order_obs("210100025074420605", y1=200, x1=180),
        ]
        self.assertEqual(extract_order_ids(obs),
                         ["210100025074400605", "210100025074420605"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
