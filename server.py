#!/usr/bin/env python3
"""HTTP 上传识别服务（仅 Python 标准库，内网离线可部署）。

功能：
- GET  /            上传+展示识别结果的网页（web/ui.html）
- POST /recognize   上传 2 张抖音截图（multipart/form-data），返回识别 JSON
- GET  /health      健康检查

用法:
    python3 server.py --host 0.0.0.0 --port 8080 [--backend rapidocr|vision|tesseract|mock]

接口:
    POST /recognize   body: multipart/form-data，字段为图片文件（2 张），
                      可选字段 role=<fieldname>:user|chat 指定某字段的角色。
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
from img_sieve.service import recognize

_BASE = os.path.dirname(os.path.abspath(__file__))
UI_PATH = os.path.join(_BASE, "web", "ui.html")

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
        elif path.startswith("/task/"):
            self._handle_task_query(path[len("/task/"):].strip("/"))
        else:
            self._send_json({"code": 404, "message": "not found"}, 404)

    def _serve_ui(self):
        try:
            with open(UI_PATH, "r", encoding="utf-8") as f:
                self._send_html(f.read())
        except OSError:
            self._send_text(
                "img-sieve 图片信息识别服务\n\n"
                "POST /recognize  上传 2 张抖音截图（multipart），返回 JSON\n"
                "GET /health      健康检查\n"
            )

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/recognize":
            self._handle_recognize()
        elif path == "/recognize/async":
            self._handle_recognize_async()
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
            "created_at": str(row["created_at"]),
            "started_at": str(row["started_at"]) if row.get("started_at") else None,
            "finished_at": str(row["finished_at"]) if row.get("finished_at") else None,
            "backend": row.get("backend"),
            "result": result,
            "error": row.get("error_msg"),
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
        print("[img-sieve] 同步: POST /recognize | 异步: POST /recognize/async, "
              "GET /task/{task_id}, POST /admin/purge?days=N")
    else:
        print("[img-sieve] 同步: POST /recognize | GET /health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[img-sieve] 已停止")
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
