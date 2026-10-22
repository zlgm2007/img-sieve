#!/usr/bin/env python3
"""HTTP 上传识别服务（仅 Python 标准库，内网离线可部署）。

功能：
- GET  /               人脉识别网页（抖音 / 小红书 / 微信，web/ui.html）
- GET  /order          订单识别网页（web/order.html）
- POST /recognize      人脉识别：上传主页(联系人) + 对话框截图，返回识别 JSON
- POST /recognize/async  人脉识别异步任务（MySQL 队列）
- POST /recognize-order    订单识别：上传 1 张订单列表页，返回订单号列表
- POST /recognize-order/async  订单识别异步任务（MySQL 队列）
- GET  /health        健康检查

用法:
    python3 server.py --host 0.0.0.0 --port 8080 [--backend rapidocr|vision|tesseract|mock]
"""
from __future__ import annotations

import argparse
import email.policy
import json
import os
import re
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from img_sieve import db, ocr
from img_sieve.extractor import detect_roles
from img_sieve.service import recognize, recognize_order

_BASE = os.path.dirname(os.path.abspath(__file__))
UI_PATH = os.path.join(_BASE, "web", "ui.html")
ORDER_UI_PATH = os.path.join(_BASE, "web", "order.html")

MAX_BODY = 50 * 1024 * 1024  # 50MB 上限
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
MAGIC_OK = {(b"\xff\xd8\xff"), (b"\x89PNG"), (b"RIFF"), (b"GIF8"),
            (b"BM")}  # jpg/png/webp/gif/bmp 文件头


def _is_image_bytes(data: bytes) -> bool:
    return any(data.startswith(m) for m in MAGIC_OK)


class Handler(BaseHTTPRequestHandler):
    backend = None  # 由 main 注入
    server_version = "img-sieve/1.0"

    # ---------- helpers ----------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 精简日志
        sys.stderr.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                        fmt % args))

    def _log(self, msg):  # 业务日志（与 log_message 同通道，写入 stderr → server.log）
        sys.stderr.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))

    # ---------- routes ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json({"code": 0, "message": "ok", "status": "up"})
        elif path in ("/", ""):
            self._serve_ui()
        elif path == "/order":
            self._serve_order_ui()
        elif path.startswith("/task/"):
            rest = path[len("/task/"):].strip("/")
            parts = rest.split("/")
            if len(parts) == 1:
                self._handle_task_query(parts[0])
            elif len(parts) == 3 and parts[1] == "image" and parts[2] in ("1", "2"):
                self._handle_task_image(parts[0], parts[2])
            else:
                self._send_json({"code": 404, "message": "not found"}, 404)
        elif path == "/admin/tasks":
            self._handle_list_tasks()
        else:
            self._send_json({"code": 404, "message": "not found"}, 404)

    def _serve_ui(self):
        try:
            with open(UI_PATH, "r", encoding="utf-8") as f:
                self._send_html(f.read())
        except OSError:
            self._send_text(
                "img-sieve 图片信息识别服务\n\n"
                "POST /recognize          人脉识别（主页/联系人 + 对话框），返回 JSON\n"
                "POST /recognize-order    订单识别（1 张订单列表页），返回订单号列表\n"
                "GET  /health             健康检查\n"
            )

    def _serve_order_ui(self):
        try:
            with open(ORDER_UI_PATH, "r", encoding="utf-8") as f:
                self._send_html(f.read())
        except OSError:
            self._send_text(
                "img-sieve 订单识别服务\n\n"
                "POST /recognize-order  上传 1 张订单列表页（multipart），返回订单号 JSON\n"
                "GET /health            健康检查\n"
            )

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/recognize":
            self._handle_recognize()
        elif path == "/recognize/async":
            self._handle_recognize_async()
        elif path == "/recognize-order":
            self._handle_recognize_order()
        elif path == "/recognize-order/async":
            self._handle_recognize_order_async()
        elif path == "/admin/purge":
            self._handle_admin_purge()
        else:
            self._send_json({"code": 404, "message": "not found"}, 404)

    # ---------- recognize ----------
    def _handle_recognize(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self._send_json({"code": 400,
                             "message": "请使用 multipart/form-data 上传图片"}, 400)
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > MAX_BODY:
            self._send_json({"code": 400,
                             "message": f"请求体大小需在 1~{MAX_BODY} 字节之间"}, 400)
            return

        try:
            files, roles = self._parse_multipart(content_type, length)
        except ValueError as e:
            self._send_json({"code": 400, "message": str(e)}, 400)
            return

        if len(files) < 1:
            self._send_json({"code": 400, "message": "请至少上传 1 张图片"}, 400)
            return

        # 写临时文件并识别
        tmp_paths = []
        try:
            for fname, data in files:
                fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(fname)[1] or ".jpg")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                tmp_paths.append(tmp)

            explicit = {}
            for fname, data in files:
                role = roles.get(fname)
                if role:
                    idx = [x[0] for x in files].index(fname)
                    explicit[tmp_paths[idx]] = role

            t0 = time.perf_counter()
            try:
                result = recognize(tmp_paths, backend=self.backend,
                                   explicit_roles=explicit or None)
            except ocr.OCRBackendError as e:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                self._log(f"/recognize 识别失败, 耗时 {elapsed_ms:.1f} ms, "
                          f"图片={len(files)} 张, 原因={e}")
                self._send_json({"code": 500, "message": str(e)}, 500)
                return
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._log(f"/recognize 识别成功, 耗时 {elapsed_ms:.1f} ms, "
                      f"图片={len(files)} 张, backend={self.backend or 'auto'}")
            # 同步识别也写入 ocr_task，让任务记录页能查到（失败不影响主流程）
            try:
                b1 = files[0][1]; n1 = files[0][0]
                b2 = files[1][1] if len(files) > 1 else b""
                n2 = files[1][0] if len(files) > 1 else None
                db.record_sync_task(b1, n1, b2, n2,
                                    json.dumps(result, ensure_ascii=False),
                                    self.backend or result.get("backend"),
                                    scene="people")
            except Exception as e:
                self._log(f"/recognize 写任务记录失败（忽略）: {e}")
            self._send_json(result)
        finally:
            for p in tmp_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ---------- 异步接口 ----------
    def _handle_recognize_async(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self._send_json({"code": 400, "message": "请使用 multipart/form-data 上传图片"}, 400)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > MAX_BODY:
            self._send_json({"code": 400, "message": f"请求体大小需在 1~{MAX_BODY} 字节之间"}, 400)
            return
        try:
            files, _roles = self._parse_multipart(content_type, length)
        except ValueError as e:
            self._send_json({"code": 400, "message": str(e)}, 400)
            return

        image_files = [(n, d) for n, d in files if _is_image_bytes(d)]
        if not image_files:
            self._send_json({"code": 400, "message": "请至少上传 1 张图片"}, 400)
            return

        (n1, b1) = image_files[0]
        (n2, b2) = image_files[1] if len(image_files) > 1 else (None, None)
        try:
            task_id = db.create_task(
                b1, n1 or "image1.jpg", "image/jpeg",
                b2 if b2 is not None else b"", n2 or "image2.jpg", "image/jpeg",
                backend=self.backend)
        except Exception as e:
            self._log(f"/recognize/async 任务入库失败: {e}")
            self._send_json({"code": 500, "message": f"任务入库失败（检查 MySQL 连接）: {e}"}, 500)
            return
        self._log(f"/recognize/async 已创建任务 {task_id}, 图片={len(image_files)} 张")
        self._send_json({"code": 0, "message": "ok",
                         "task_id": task_id, "status": "pending"})

    # ---------- 订单识别（同步） ----------
    def _handle_recognize_order(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self._send_json({"code": 400,
                             "message": "请使用 multipart/form-data 上传图片"}, 400)
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > MAX_BODY:
            self._send_json({"code": 400,
                             "message": f"请求体大小需在 1~{MAX_BODY} 字节之间"}, 400)
            return

        try:
            files, _roles = self._parse_multipart(content_type, length)
        except ValueError as e:
            self._send_json({"code": 400, "message": str(e)}, 400)
            return

        if len(files) != 1:
            self._send_json({"code": 400,
                             "message": "订单识别仅支持上传 1 张订单列表页截图"}, 400)
            return

        fname, data = files[0]
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(fname)[1] or ".jpg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)

            t0 = time.perf_counter()
            try:
                result = recognize_order([tmp], backend=self.backend)
            except ocr.OCRBackendError as e:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                self._log(f"/recognize-order 识别失败, 耗时 {elapsed_ms:.1f} ms, 原因={e}")
                self._send_json({"code": 500, "message": str(e)}, 500)
                return
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._log(f"/recognize-order 识别成功, 耗时 {elapsed_ms:.1f} ms, "
                      f"订单号={result['data']['order_count']} 个, "
                      f"backend={self.backend or 'auto'}")
            # 同步识别也写入 ocr_task，让任务记录页能查到（失败不影响主流程）
            try:
                db.record_sync_task(data, fname, None, None,
                                    json.dumps(result, ensure_ascii=False),
                                    self.backend or result.get("backend"),
                                    scene="order")
            except Exception as e:
                self._log(f"/recognize-order 写任务记录失败（忽略）: {e}")
            self._send_json(result)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ---------- 订单识别（异步） ----------
    def _handle_recognize_order_async(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self._send_json({"code": 400, "message": "请使用 multipart/form-data 上传图片"}, 400)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > MAX_BODY:
            self._send_json({"code": 400, "message": f"请求体大小需在 1~{MAX_BODY} 字节之间"}, 400)
            return
        try:
            files, _roles = self._parse_multipart(content_type, length)
        except ValueError as e:
            self._send_json({"code": 400, "message": str(e)}, 400)
            return

        if len(files) != 1:
            self._send_json({"code": 400,
                             "message": "订单识别仅支持上传 1 张订单列表页截图"}, 400)
            return

        fname, data = files[0]
        try:
            task_id = db.create_task(
                data, fname or "order.jpg", "image/jpeg",
                b"", None, "image/jpeg",
                backend=self.backend, scene="order")
        except Exception as e:
            self._log(f"/recognize-order/async 任务入库失败: {e}")
            self._send_json({"code": 500, "message": f"任务入库失败（检查 MySQL 连接）: {e}"}, 500)
            return
        self._log(f"/recognize-order/async 已创建任务 {task_id}")
        self._send_json({"code": 0, "message": "ok",
                         "task_id": task_id, "status": "pending"})

    def _handle_task_query(self, task_id):
        if not task_id:
            self._send_json({"code": 400, "message": "缺少 task_id"}, 400)
            return
        try:
            row = db.get_task(task_id)
        except Exception as e:
            self._send_json({"code": 500, "message": f"查询失败（检查 MySQL 连接）: {e}"}, 500)
            return
        if not row:
            self._send_json({"code": 404, "message": "任务不存在"}, 404)
            return
        result = None
        if row.get("result_json"):
            try:
                result = json.loads(row["result_json"])
            except Exception:
                result = row["result_json"]
        self._send_json({
            "code": 0,
            "task_id": task_id,
            "status": row["status"],
            "scene": row.get("scene") or "people",
            "created_at": str(row["created_at"]),
            "started_at": str(row["started_at"]) if row.get("started_at") else None,
            "finished_at": str(row["finished_at"]) if row.get("finished_at") else None,
            "backend": row.get("backend"),
            "result": result,
            "error": row.get("error_msg"),
        })

    def _handle_task_image(self, task_id, n):
        try:
            row = db.get_task_images(task_id)
        except Exception as e:
            self._send_json({"code": 500, "message": f"取图失败: {e}"}, 500)
            return
        if not row:
            self._send_json({"code": 404, "message": "任务不存在"}, 404)
            return
        blob = row.get("image1") if n == "1" else row.get("image2")
        ctype = row.get("image1_type") if n == "1" else row.get("image2_type")
        if not blob:
            self._send_json({"code": 404, "message": "无此图片"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype or "image/jpeg")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(blob)

    def _handle_list_tasks(self):
        qs = urlparse(self.path).query
        params = dict(kv.split("=", 1) for kv in qs.split("&") if "=" in kv)
        from urllib.parse import unquote
        start = unquote(params.get("start", "")) or None
        end = unquote(params.get("end", "")) or None
        try:
            page = int(params.get("page", "1"))
        except ValueError:
            page = 1
        try:
            total, items = db.list_tasks(start=start, end=end, page=page, per_page=20)
        except Exception as e:
            self._send_json({"code": 500, "message": f"查询失败（检查 MySQL 连接）: {e}"}, 500)
            return
        # 时间转字符串便于 JSON
        for it in items:
            it["created_at"] = str(it["created_at"]) if it.get("created_at") else None
            it["finished_at"] = str(it["finished_at"]) if it.get("finished_at") else None
        self._send_json({
            "code": 0, "total": total, "page": page, "per_page": 20,
            "pages": (total + 19) // 20, "items": items,
        })

    def _handle_admin_purge(self):
        expected = os.environ.get("IMG_SIEVE_ADMIN_TOKEN", "")
        params = dict(kv.split("=", 1) for kv in urlparse(self.path).query.split("&") if "=" in kv)
        if expected and params.get("token") != expected:
            self._send_json({"code": 403, "message": "需要正确的 token（?token=...）"}, 403)
            return
        try:
            days = int(params.get("days", "5"))
        except ValueError:
            self._send_json({"code": 400, "message": "days 需为整数"}, 400)
            return
        try:
            n = db.purge_old(days)
        except Exception as e:
            self._send_json({"code": 500, "message": f"清理失败（检查 MySQL 连接）: {e}"}, 500)
            return
        self._log(f"/admin/purge 清理 {days} 天前任务，删除 {n} 条（含图片 BLOB）")
        self._send_json({"code": 0, "message": "ok", "days": days, "deleted": n})

    def _parse_multipart(self, content_type, length):
        """解析 multipart，返回 (files, roles)。

        files: [(field_name, bytes)]
        roles: {field_name: "user"|"chat"} 来自 form 字段 role=xxx:user|chat
        """
        header = ("Content-Type", content_type)

        body = self.rfile.read(length)
        # 用 email 库解析 multipart
        payload = email.message_from_bytes(
            b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body,
            policy=email.policy.default,
        )

        files = []
        roles = {}
        for part in payload.iter_parts():
            disp = part.get_content_disposition()
            if disp != "form-data":
                continue
            params = part.get_params(header="content-disposition", unquote=True)
            name = None
            for k, v in params:
                if k == "name":
                    name = v
            if not name:
                continue
            data = part.get_payload(decode=True)
            if data is None:
                continue

            # form 文本字段 role=<fieldname>:user|chat
            if part.get_content_type() in ("text/plain", "text/html", "application/octet-stream") and not _is_image_bytes(data or b""):
                if name == "role" or name.startswith("role"):
                    m = re.fullmatch(r"(.+?):(user|chat)", data.decode("utf-8", "ignore").strip())
                    if m:
                        roles[m.group(1)] = m.group(2)
                continue

            if not _is_image_bytes(data or b""):
                raise ValueError(f"字段 {name} 不是有效的图片文件")
            fname = (part.get_filename() or name or "upload")
            files.append((name, data))

        return files, roles


def main(argv=None):
    parser = argparse.ArgumentParser(description="img-sieve HTTP 识别服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--backend", choices=["rapidocr", "vision", "tesseract", "mock"],
                        default=None, help="OCR 后端，默认自动选择")
    args = parser.parse_args(argv)

    Handler.backend = args.backend

    # 启动时尝试建表（异步功能需要 MySQL）。连不上只警告，不阻塞同步识别。
    async_ready = False
    try:
        db.ensure_table()
        async_ready = True
    except Exception as e:
        print(f"[img-sieve] 警告：MySQL 未连接/建表失败，异步接口不可用"
              f"（同步识别仍可）: {e}")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[img-sieve] 服务已启动: http://{args.host}:{args.port}")
    print(f"[img-sieve] OCR 后端: {args.backend or ocr.auto_driver([])}")
    if async_ready:
        print("[img-sieve] 人脉: POST /recognize, /recognize/async | "
              "订单: POST /recognize-order, /recognize-order/async")
        print("[img-sieve] 查询: GET /task/{task_id} | 运维: POST /admin/purge?days=N")
    else:
        print("[img-sieve] 人脉: POST /recognize | 订单: POST /recognize-order | GET /health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[img-sieve] 已停止")
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
