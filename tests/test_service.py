"""订单服务层测试：重点覆盖方向兜底与出参结构复用。"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from img_sieve import service

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICKET_IMG = os.path.join(BASE, "raw", "orderA20261022150516331.jpg")

OMS_OBS = {
    "text": "OMS单号：210100025074430605",
    "confidence": 0.9,
    "box": {"x1": 46, "y1": 406, "x2": 360, "y2": 420},
}


def _ocr_result(observations):
    return [{
        "path": TICKET_IMG,
        "width": 828,
        "height": 1472,
        "observations": observations,
    }]


class TestOrderService(unittest.TestCase):
    def test_order_orientation_fallback_when_first_pass_empty(self):
        """首遍未抽到单号时，服务层应旋转重试并复用同一出参格式。"""
        calls = 0

        def fake_ocr(image_paths, backend=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _ocr_result([])
            return _ocr_result([OMS_OBS])

        with patch.object(service.ocr, "run_ocr", side_effect=fake_ocr) as mock_run:
            result = service.recognize_order([TICKET_IMG], backend="rapidocr")

        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["order_count"], 1)
        self.assertEqual(result["data"]["order_ids"],
                         ["210100025074430605"])
        self.assertEqual(mock_run.call_count, 2)
        leftovers = [f for f in os.listdir(tempfile.gettempdir())
                     if f.startswith("order-rot-")]
        self.assertEqual(leftovers, [])

    def test_order_no_fallback_when_first_pass_has_id(self):
        """首遍已抽到单号时，不应再触发旋转 OCR。"""
        with patch.object(service.ocr, "run_ocr",
                          side_effect=lambda paths, backend=None: _ocr_result([OMS_OBS])) as mock_run:
            result = service.recognize_order([TICKET_IMG], backend="rapidocr")

        self.assertEqual(result["data"]["order_count"], 1)
        self.assertEqual(mock_run.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
