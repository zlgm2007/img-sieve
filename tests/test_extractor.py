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
                                 extract_order_ids, extract_order_items,
                                 build_order_result)

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
        # 票据上「订单编号」与「OMS单号」都是订单号，都要抽取；
        # 裸「单号：DA...」是商品 SKU，不抽。
        self.assertEqual(extract_order_ids(obs),
                         ["MM00061870", "210100025074430605"])

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


class TestOrderLabelVariants(unittest.TestCase):
    """订单号标签变体与取值格式（订单号 / 订单编号 / 订单编码 / 订单号码）。"""

    def test_order_code_label(self):
        obs = [_order_obs("订单编码：D4002566297")]
        self.assertEqual(extract_order_ids(obs), ["D4002566297"])

    def test_order_number_label_without_colon(self):
        # 手机拍照的「订单号码」后可能没有冒号
        obs = [_order_obs("订单号码3802223132735788788")]
        self.assertEqual(extract_order_ids(obs), ["3802223132735788788"])

    def test_order_number_label_halfwidth_colon(self):
        obs = [_order_obs("订单编号:B5002436100")]
        self.assertEqual(extract_order_ids(obs), ["B5002436100"])

    def test_letter_prefixed_value(self):
        # 字母前缀型：字母 + 6~20 位数字
        for value in ("B5002436100", "D4002566297", "C2002610735",
                      "D0012553903", "MM00061868"):
            obs = [_order_obs("订单编号：" + value)]
            self.assertEqual(extract_order_ids(obs), [value], value)

    def test_nineteen_digit_pure_number(self):
        # 美团单号 19 位（3802 2231 3273 5788 788），超出旧规则的 18 位上限
        obs = [_order_obs("订单号码：3802223132735788788")]
        self.assertEqual(extract_order_ids(obs), ["3802223132735788788"])

    def test_spaced_grouped_value_normalized(self):
        # 订单号在页面上是分组显示的，识别后应去掉空格
        obs = [_order_obs("订单号码：3802 2231 3273 5788 788")]
        self.assertEqual(extract_order_ids(obs), ["3802223132735788788"])

    def test_label_and_value_on_adjacent_lines(self):
        # 标签与编号不在同一行（手机拍照错位）：按空间位置就近配对
        obs = [
            _order_obs("订单号码：", y1=300, x1=190),
            _order_obs("3802223132735788788", y1=320, x1=330),
        ]
        self.assertEqual(extract_order_ids(obs), ["3802223132735788788"])

    def test_excludes_row_number_and_product_code(self):
        # 订单行号 / 商品编码 / 运单号 / 税率 / 日期都不得混入
        obs = [
            _order_obs("订单行号：2101000250744106050", y1=100),
            _order_obs("单号：DA1153598", y1=140),
            _order_obs("极兔速递：JT3180673660562", y1=180),
            _order_obs("税率 13%", y1=220),
            _order_obs("纳税人识别号：130102********1525", y1=260),
            _order_obs("2026-09-1816:49:25", y1=300),
        ]
        self.assertEqual(extract_order_ids(obs), [])

    def test_excludes_price_glued_onto_number(self):
        # 相邻行的金额不能被拼到订单号后面（行合并需按锚定分行）
        obs = [
            _order_obs("订单编号：MM00061870", y1=311, x1=446),
            _order_obs("7438.00", y1=395, x1=483),
        ]
        self.assertEqual(extract_order_ids(obs), ["MM00061870"])

    def test_order_items_carry_source_label(self):
        obs = [
            _order_obs("订单号：210100027596213478", y1=100),
            _order_obs("订单号码3802223132735788788", y1=200),
        ]
        items = extract_order_items(obs)
        self.assertEqual([it[0] for it in items], ["订单号", "订单号码"])
        self.assertEqual([it[1] for it in items],
                         ["210100027596213478", "3802223132735788788"])

    def test_build_order_result_exposes_items(self):
        obs = [_order_obs("订单编码：D4002566297")]
        data = build_order_result([{"observations": obs}])
        self.assertEqual(data["order_count"], 1)
        self.assertEqual(data["order_ids"], ["D4002566297"])
        self.assertEqual(data["order_items"],
                         [{"label": "订单编码", "order_id": "D4002566297"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
