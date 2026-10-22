#!/usr/bin/env python3
"""异步 OCR worker：轮询 MySQL 认领 pending 任务，识别后写回结果。

每台部署机器都跑一个（与 HTTP 服务同机，由 keepalive.sh 保活）。
多 worker 并发靠 db.claim_task 的原子 UPDATE 认领，不会重复处理。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

from img_sieve import db, ocr
from img_sieve.service import recognize, recognize_order


def _log(msg):
    sys.stderr.write("[%s] %s\n" % (time.strftime("%F %T"), msg))


def process_task(task: dict, backend):
    """把任务里的图片 BLOB 落临时文件 → 识别 → 写回结果。

    scene=people：人脉识别（主页/联系人 + 对话框）；scene=order：订单识别。
    """
    task_id = task["task_id"]
    scene = task.get("scene") or "people"
    tmp_paths = []
    try:
        for blob, name in ((task.get("image1"), task.get("image1_name")),
                           (task.get("image2"), task.get("image2_name"))):
            if not blob:
                continue
            suffix = os.path.splitext(name or "")[1] or ".jpg"
            fd, p = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
            tmp_paths.append(p)
        if not tmp_paths:
            raise RuntimeError("任务没有图片")

        t0 = time.perf_counter()
        if scene == "order":
            result = recognize_order(tmp_paths, backend=backend or task.get("backend"))
        else:
            result = recognize(tmp_paths, backend=backend or task.get("backend"))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        db.complete_task(task_id, json.dumps(result, ensure_ascii=False),
                         backend or task.get("backend"))
        _log(f"[worker] 任务 {task_id} ({scene}) 识别完成，耗时 {elapsed_ms:.0f}ms，"
             f"backend={result.get('backend')}")
    except Exception as e:
        db.fail_task(task_id, str(e))
        _log(f"[worker] 任务 {task_id} ({scene}) 识别失败: {e}")
    finally:
        for p in tmp_paths:
            try:
                os.remove(p)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser(description="img-sieve 异步 OCR worker")
    ap.add_argument("--backend", default=None,
                    choices=["rapidocr", "vision", "tesseract", "mock"])
    ap.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒")
    ap.add_argument("--stuck-timeout", type=int, default=300,
                    help="处理超过该秒数视为卡死，自动回收")
    args = ap.parse_args()

    wid = db.worker_id()
    _log(f"[worker] 启动 worker={wid} backend={args.backend or 'auto'} "
         f"interval={args.interval}s stuck={args.stuck_timeout}s")

    # 预热 OCR 引擎（首次加载 ONNX 模型较慢，避免第一个任务卡在加载上）
    try:
        ocr._get_rapid_engine()
        _log("[worker] OCR 引擎就绪")
    except Exception as e:
        _log(f"[worker] 警告：OCR 引擎预热失败（运行时会重试）: {e}")

    while True:
        try:
            n = db.reclaim_stuck(args.stuck_timeout)
            if n:
                _log(f"[worker] 回收卡死任务 {n} 个")
            task = db.claim_task(wid)
            if task:
                process_task(task, args.backend)
        except Exception as e:
            _log(f"[worker] 轮询异常（{type(e).__name__}: {e}，{args.interval}s 后重试）")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
