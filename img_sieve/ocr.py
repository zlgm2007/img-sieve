"""OCR 引擎抽象层。

后端全部本地离线执行：
- rapidocr : PP-OCR v4 (onnxruntime)，模型随 pip 包内置，中文 UI 识别最佳，
             跨平台，内网部署首选（默认）
- vision    : Apple Vision（macOS，Swift 编译的 bin/ocr_helper）
- tesseract : tesseract 二进制（跨平台），中文需 chi_sim 语言包
- mock      : 读取预置夹具 JSON，用于无 OCR 依赖时验证提取逻辑与 HTTP 服务

所有后端统一输出：
[
  {
    "path": "图片路径",
    "width": 1080, "height": 2400,
    "observations": [
      {"text": "叫我大猫", "confidence": 0.99, "box": {"x1":..,"y1":..,"x2":..,"y2":..}}
    ]
  }
]
坐标 box 为千分比(0-1000)，左上角原点，与图片分辨率无关。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(_BASE, "bin")
VISION_BIN = os.path.join(BIN_DIR, "ocr_helper")
FIXTURE_DIR = os.path.join(_BASE, "fixtures")


class OCRBackendError(RuntimeError):
    """OCR 后端执行失败。"""


def _normalize_vision_output(data):
    """把 vision/mock 后端的 JSON 规范化为统一结构。"""
    results = []
    for item in data.get("results", []):
        obs = []
        for o in item.get("observations", []):
            b = o.get("box", {})
            obs.append({
                "text": o.get("text", ""),
                "confidence": float(o.get("confidence", 0.0)),
                "box": {
                    "x1": int(b.get("x1", 0)),
                    "y1": int(b.get("y1", 0)),
                    "x2": int(b.get("x2", 0)),
                    "y2": int(b.get("y2", 0)),
                },
            })
        results.append({
            "path": item.get("path", ""),
            "width": int(item.get("width", 0)),
            "height": int(item.get("height", 0)),
            "observations": obs,
        })
    return results


def run_vision(image_paths):
    """Apple Vision 后端：调用编译好的 bin/ocr_helper。"""
    if not os.path.exists(VISION_BIN):
        raise OCRBackendError(
            f"未找到 Vision OCR 可执行文件: {VISION_BIN}。"
            "请在具有正常 Swift 工具链的机器上运行 ./build.sh 编译后重试。"
        )
    cmd = [VISION_BIN] + list(image_paths)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise OCRBackendError(f"Vision OCR 失败: {proc.stderr.strip()}")
    data = json.loads(proc.stdout or "{}")
    return _normalize_vision_output(data)


TESSDATA_DIR = os.path.join(_BASE, "tessdata")  # 随包携带的语言包目录


def _effective_tessdata():
    """返回实际使用的 tessdata 目录：优先随包目录，其次环境变量。"""
    if os.path.isdir(TESSDATA_DIR) and any(
            f.endswith(".traineddata") for f in os.listdir(TESSDATA_DIR)):
        return TESSDATA_DIR
    env = os.environ.get("TESSDATA_PREFIX")
    if env and os.path.isdir(env):
        return env
    return None


def _find_tesseract():
    """在 PATH 及常见安装路径中查找 tesseract 可执行文件。"""
    exe = shutil.which("tesseract")
    if exe:
        return exe
    for candidate in ("/opt/homebrew/bin/tesseract",
                      "/usr/local/bin/tesseract",
                      "/usr/bin/tesseract"):
        if os.path.exists(candidate):
            return candidate
    return None


def _has_tess_lang(tessdata_dir, lang):
    """判断 tessdata 目录（或系统）是否有某语言包。"""
    if tessdata_dir:
        return os.path.exists(os.path.join(tessdata_dir, lang + ".traineddata"))
    return False


def run_tesseract(image_paths):
    """tesseract 后端：优先 chi_sim+eng，否则仅 eng。输出 TSV 解析出坐标。"""
    tesseract = _find_tesseract()
    if not tesseract:
        raise OCRBackendError("未找到 tesseract，请安装或改用 vision/mock 后端。")

    tessdata_dir = _effective_tessdata()
    has_chi = _has_tess_lang(tessdata_dir, "chi_sim")
    if not has_chi and not tessdata_dir:
        # 无随包目录时回退到系统语言列表探测
        try:
            proc = subprocess.run([tesseract, "--list-langs"],
                                  capture_output=True, text=True, timeout=30)
            has_chi = "chi_sim" in (proc.stdout + proc.stderr)
        except Exception:
            has_chi = False

    langs = "chi_sim+eng" if has_chi else "eng"
    if not has_chi:
        import sys as _sys
        print("[tesseract] 警告: 未找到 chi_sim 中文语言包，中文昵称/抖音号将识别为 null。"
              "请将 chi_sim.traineddata 放入 tessdata/ 目录。", file=_sys.stderr)

    # 构造环境：让 tesseract 使用随包 tessdata（内网部署关键）
    env = dict(os.environ)
    if tessdata_dir:
        env["TESSDATA_PREFIX"] = tessdata_dir

    results = []
    for path in image_paths:
        if not os.path.exists(path):
            raise OCRBackendError(f"图片不存在: {path}")
        cmd = [tesseract, path, "stdout", "-l", langs, "--psm", "3", "tsv"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=120, env=env)
        if proc.returncode != 0:
            raise OCRBackendError(f"tesseract OCR 失败 {path}: {proc.stderr.strip()}")
        observations = _parse_tsv(proc.stdout)
        results.append({
            "path": path,
            "width": _image_size(path)[0],
            "height": _image_size(path)[1],
            "observations": observations,
        })
    return results


def _parse_tsv(tsv_text):
    """解析 tesseract TSV 输出，把单词按行合并成观察对象。"""
    lines = tsv_text.splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    col = {name: i for i, name in enumerate(header)}

    words = []  # (text, left, top, width, height, conf)
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) <= max(col.values()):
            continue
        text = parts[col["text"]]
        conf = parts[col["conf"]]
        try:
            left, top, w, h = (int(parts[col["left"]]),
                               int(parts[col["top"]]),
                               int(parts[col["width"]]),
                               int(parts[col["height"]]))
            conf = float(conf)
        except (ValueError, KeyError):
            continue
        if not text.strip():
            continue
        words.append((text, left, top, w, h, conf))

    if not words:
        return []
    # 用整张图尺寸做归一化（tesseract 默认 300dpi 输出，取全图范围）
    max_right = max(w[1] + w[3] for w in words)
    max_bottom = max(w[2] + w[4] for w in words)
    scale_x = 1000.0 / max(max_right, 1)
    scale_y = 1000.0 / max(max_bottom, 1)

    # 按行聚合：同行(y 中心接近)的单词合并
    words.sort(key=lambda w: (w[2], w[1]))
    rows = []
    cur = []
    for w in words:
        if not cur:
            cur = [w]
            continue
        y_center_prev = cur[-1][2] + cur[-1][4] / 2.0
        y_center_cur = w[2] + w[4] / 2.0
        if abs(y_center_cur - y_center_prev) < max(w[4], cur[-1][4]) * 0.5:
            cur.append(w)
        else:
            rows.append(cur)
            cur = [w]
    if cur:
        rows.append(cur)

    observations = []
    for row in rows:
        row.sort(key=lambda w: w[1])
        text = "".join(w[0] for w in row)
        left = min(w[1] for w in row)
        top = min(w[2] for w in row)
        right = max(w[1] + w[3] for w in row)
        bottom = max(w[2] + w[4] for w in row)
        conf = sum(w[5] for w in row) / len(row)
        observations.append({
            "text": text,
            "confidence": round(conf, 3),
            "box": {
                "x1": int(round(left * scale_x)),
                "y1": int(round(top * scale_y)),
                "x2": int(round(right * scale_x)),
                "y2": int(round(bottom * scale_y)),
            },
        })
    return observations


def _image_size(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        # 无 PIL 时用 sips（macOS）
        try:
            out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                                 capture_output=True, text=True, timeout=30).stdout
            w = h = 0
            for ln in out.splitlines():
                if "pixelWidth" in ln:
                    w = int(ln.split(":")[-1].strip())
                if "pixelHeight" in ln:
                    h = int(ln.split(":")[-1].strip())
            return (w, h)
        except Exception:
            return (0, 0)


def run_mock(image_paths, fixture=None):
    """mock 后端：读取夹具 JSON（vision 输出格式），返回对应图片的识别结果。"""
    fixture = fixture or os.path.join(FIXTURE_DIR, "sample_ocr.json")
    if not os.path.exists(fixture):
        raise OCRBackendError(f"mock 夹具不存在: {fixture}")
    with open(fixture, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = _normalize_vision_output(data)
    # 优先按文件名匹配夹具；文件名对不上（如 HTTP 临时文件）则按位置匹配
    by_path = {os.path.basename(r["path"]): r for r in results}
    positional = [None] * len(image_paths)
    named = True
    for i, p in enumerate(image_paths):
        r = by_path.get(os.path.basename(p))
        if r is None:
            named = False
            break
        positional[i] = r
    if not named:
        if len(results) != len(image_paths):
            raise OCRBackendError(
                f"夹具识别结果数({len(results)})与图片数({len(image_paths)})不一致，"
                "且文件名无法匹配")
        positional = list(results)
    out = []
    for i, p in enumerate(image_paths):
        r = dict(positional[i])
        r["path"] = p
        out.append(r)
    return out


# ---------- RapidOCR (PP-OCR v4, onnxruntime) ----------

_rapid_engine = None


def _rapid_tuning():
    """从环境变量读取可选的 RapidOCR 调优参数（默认不传，保持原行为）。

    - IMG_SIEVE_OCR_THREADS   : intra_op_num_threads（onnxruntime 每算子线程数）
    - IMG_SIEVE_DET_LIMIT     : det_limit_side_len（检测阶段长边缩放上限，越小越快但可能漏小字）
    """
    kwargs = {}
    t = os.environ.get("IMG_SIEVE_OCR_THREADS", "").strip()
    if t.isdigit() and int(t) >= 1:
        kwargs["intra_op_num_threads"] = int(t)
    d = os.environ.get("IMG_SIEVE_DET_LIMIT", "").strip()
    if d.isdigit() and int(d) >= 1:
        kwargs["det_limit_side_len"] = int(d)
    return kwargs


def _get_rapid_engine():
    """懒加载并复用 RapidOCR 引擎（首次调用较慢，加载 ONNX 模型）。"""
    global _rapid_engine
    if _rapid_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            raise OCRBackendError(
                "未安装 rapidocr_onnxruntime。请用 pip 安装 "
                "`rapidocr_onnxruntime`（模型随包内置，离线可用）。")
        _rapid_engine = RapidOCR(**_rapid_tuning())
    return _rapid_engine


def _quad_to_box(quad, width, height):
    """把 RapidOCR 的 4 角点像素坐标转换为归一化(0-1000)矩形框。"""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    w = max(width, 1)
    h = max(height, 1)
    return {
        "x1": int(round(min(xs) / w * 1000)),
        "y1": int(round(min(ys) / h * 1000)),
        "x2": int(round(max(xs) / w * 1000)),
        "y2": int(round(max(ys) / h * 1000)),
    }


def _image_size_any(path):
    """跨后端获取图片宽高：PIL → OpenCV → sips → (0,0)。"""
    w, h = 0, 0
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        if w:
            return (w, h)
    except Exception:
        pass
    try:
        import cv2
        img = cv2.imread(path)
        if img is not None:
            h, w = img.shape[:2]
            return (w, h)
    except Exception:
        pass
    return _image_size(path)  # 回退 sips / (0,0)


def run_rapidocr(image_paths):
    """RapidOCR 后端：PP-OCR v4，中文 UI 识别最佳，完全离线。"""
    engine = _get_rapid_engine()
    results = []
    for path in image_paths:
        if not os.path.exists(path):
            raise OCRBackendError(f"图片不存在: {path}")
        res, _elapse = engine(path)
        width, height = _image_size_any(path)
        observations = []
        for item in res or []:
            quad, text, conf = item[0], item[1], item[2]
            text = (text or "").strip()
            if not text:
                continue
            observations.append({
                "text": text,
                "confidence": round(float(conf), 3),
                "box": _quad_to_box(quad, width, height),
            })
        results.append({
            "path": path,
            "width": width,
            "height": height,
            "observations": observations,
        })
    return results


def auto_driver(image_paths):
    """自动选择后端。优先 rapidocr（内网部署首选），其次 vision/tesseract/mock。"""
    try:
        _get_rapid_engine()
        return "rapidocr"
    except Exception:
        pass
    if os.path.exists(VISION_BIN):
        return "vision"
    if _find_tesseract():
        return "tesseract"
    return "mock"


DRIVERS = {
    "rapidocr": run_rapidocr,
    "vision": run_vision,
    "tesseract": run_tesseract,
    "mock": run_mock,
}


def run_ocr(image_paths, backend=None):
    """统一入口。backend: rapidocr|vision|tesseract|mock，默认 auto。返回规范化结果。"""
    if backend is None:
        backend = auto_driver(image_paths)
    if backend not in DRIVERS:
        raise OCRBackendError(f"未知 OCR 后端: {backend}")
    return DRIVERS[backend](image_paths)
