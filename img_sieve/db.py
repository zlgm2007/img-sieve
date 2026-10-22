"""MySQL 任务持久化（异步 OCR 队列）。

设计要点：
- 多机部署共用同一个 MySQL，HTTP 服务与 worker 进程都连这个库。
- MySQL 同时承担「任务队列」：worker 用原子 UPDATE 认领 pending 任务，行锁保证不重复处理。
- 图片以 LONGBLOB 直接入库，多机零额外共享存储；历史图片按天清理（purge_old）。
- 连接参数全部走环境变量，便于多台机器指向同一库。

环境变量（也可写在项目根目录 .env，系统环境变量优先，.env 只补缺省）：
  IMG_SIEVE_MYSQL_HOST      默认 127.0.0.1
  IMG_SIEVE_MYSQL_PORT      默认 3306
  IMG_SIEVE_MYSQL_USER      默认 root
  IMG_SIEVE_MYSQL_PASSWORD  默认空
  IMG_SIEVE_MYSQL_DB        默认 imgsieve
"""
from __future__ import annotations

import os
import socket
import uuid

try:
    import pymysql
except ImportError:  # pymysql 未装时给出清晰提示
    pymysql = None  # type: ignore


def _load_dotenv() -> None:
    """加载项目根目录 .env（零依赖，不覆盖已存在的系统环境变量）。

    keepalive.sh 启动时已 source .env；这里兜底裸跑 `python server.py` /
    `python worker.py` 等不经过 shell 脚本的入口，避免落到 root/空密码默认值。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            # 去掉引号包裹与未加引号的值中的内联注释
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ocr_task (
  task_id      VARCHAR(40)  NOT NULL PRIMARY KEY,
  status       ENUM('pending','processing','done','failed') NOT NULL DEFAULT 'pending',
  scene        ENUM('people','order') NOT NULL DEFAULT 'people',
  worker_id    VARCHAR(128) NULL,
  created_at   DATETIME     NOT NULL,
  started_at   DATETIME     NULL,
  finished_at  DATETIME     NULL,
  backend      VARCHAR(32)  NULL,
  image1       LONGBLOB     NULL,
  image1_name  VARCHAR(255) NULL,
  image1_type  VARCHAR(64)  NULL,
  image2       LONGBLOB     NULL,
  image2_name  VARCHAR(255) NULL,
  image2_type  VARCHAR(64)  NULL,
  result_json  LONGTEXT     NULL,
  error_msg    TEXT         NULL,
  KEY idx_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _env(name, default=""):
    return os.environ.get(name, default)


def require_pymysql():
    if pymysql is None:
        raise RuntimeError(
            "未安装 pymysql。离线包已内置；如需手动安装："
            "pip install pymysql（纯 Python wheel，无需系统库）。")


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def connect():
    """新建一个 MySQL 连接（短连接，按需取用）。"""
    require_pymysql()
    return pymysql.connect(
        host=_env("IMG_SIEVE_MYSQL_HOST", "127.0.0.1"),
        port=int(_env("IMG_SIEVE_MYSQL_PORT", "3306") or 3306),
        user=_env("IMG_SIEVE_MYSQL_USER", "root"),
        password=_env("IMG_SIEVE_MYSQL_PASSWORD", ""),
        database=_env("IMG_SIEVE_MYSQL_DB", "imgsieve"),
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=5,
        max_allowed_packet=64 * 1024 * 1024,
    )


def ensure_table():
    """建表（幂等）。部署时由 install/启动脚本调用一次。

    老库没有 scene 列时自动补齐（订单识别场景字段），不影响存量任务。
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(TABLE_SQL)
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ocr_task' "
                "AND COLUMN_NAME='scene'")
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "ALTER TABLE ocr_task "
                    "ADD COLUMN scene ENUM('people','order') NOT NULL "
                    "DEFAULT 'people' AFTER status")
        conn.commit()
    finally:
        conn.close()


def create_task(image1: bytes, image1_name: str, image1_type: str,
                image2: bytes, image2_name: str, image2_type: str,
                backend: str | None = None, scene: str = "people") -> str:
    """写入一条 pending 任务并返回 task_id。图片以 BLOB 入库。

    scene: people=人脉识别（主页+对话框）；order=订单识别（订单号列表）。
    """
    task_id = uuid.uuid4().hex
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ocr_task "
                "(task_id, status, scene, created_at, backend, "
                " image1, image1_name, image1_type, image2, image2_name, image2_type) "
                "VALUES (%s,'pending',%s,NOW(3),%s,%s,%s,%s,%s,%s,%s)",
                (task_id, scene, backend, image1, image1_name, image1_type,
                 image2, image2_name, image2_type))
        conn.commit()
        return task_id
    finally:
        conn.close()


def claim_task(wid: str) -> dict | None:
    """原子认领一个 pending 任务。多 worker 并发靠 InnoDB 行锁串行化。

    返回 {task_id, image1, image1_name, image2, image2_name, backend}；
    无任务返回 None。
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ocr_task SET status='processing', started_at=NOW(3), worker_id=%s "
                "WHERE status='pending' ORDER BY created_at ASC LIMIT 1",
                (wid,))
            affected = cur.rowcount
        conn.commit()
        if not affected:
            return None
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT task_id, backend, scene, image1, image1_name, image2, image2_name "
                "FROM ocr_task WHERE worker_id=%s AND status='processing' "
                "ORDER BY started_at DESC LIMIT 1",
                (wid,))
            row = cur.fetchone()
        return row
    finally:
        conn.close()


def complete_task(task_id: str, result_json: str, backend: str | None):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ocr_task SET status='done', finished_at=NOW(3), "
                "result_json=%s, backend=COALESCE(%s,backend), error_msg=NULL "
                "WHERE task_id=%s",
                (result_json, backend, task_id))
        conn.commit()
    finally:
        conn.close()


def fail_task(task_id: str, error: str):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ocr_task SET status='failed', finished_at=NOW(3), "
                "error_msg=%s WHERE task_id=%s",
                (error[:2000], task_id))
        conn.commit()
    finally:
        conn.close()


def reclaim_stuck(timeout_seconds: int = 300) -> int:
    """回收 worker 崩溃后卡在 processing 的任务，重置为 pending。返回回收条数。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ocr_task SET status='pending', worker_id=NULL, started_at=NULL "
                "WHERE status='processing' AND started_at < (NOW(3) - INTERVAL %s SECOND)",
                (int(timeout_seconds),))
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def get_task(task_id: str) -> dict | None:
    """查询任务（不含图片 BLOB）。"""
    conn = connect()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT task_id, status, scene, created_at, started_at, finished_at, "
                "backend, result_json, error_msg FROM ocr_task WHERE task_id=%s",
                (task_id,))
            return cur.fetchone()
    finally:
        conn.close()


def record_sync_task(image1: bytes, image1_name: str,
                     image2: bytes | None, image2_name: str | None,
                     result_json: str, backend: str | None,
                     scene: str = "people") -> str:
    """同步识别完成后直接写一条 done 记录（让任务记录页能查到同步数据）。"""
    task_id = uuid.uuid4().hex
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ocr_task "
                "(task_id, status, scene, created_at, finished_at, backend, "
                " image1, image1_name, image2, image2_name, result_json) "
                "VALUES (%s,'done',%s,NOW(3),NOW(3),%s,%s,%s,%s,%s,%s)",
                (task_id, scene, backend, image1, image1_name,
                 image2, image2_name, result_json))
        conn.commit()
        return task_id
    finally:
        conn.close()


def get_task_images(task_id: str) -> dict | None:
    """取任务的两张图片 BLOB 及类型，用于列表缩略图/大图查看。"""
    conn = connect()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT image1, image1_type, image2, image2_type "
                "FROM ocr_task WHERE task_id=%s", (task_id,))
            return cur.fetchone()
    finally:
        conn.close()


def list_tasks(start: str | None = None, end: str | None = None,
               page: int = 1, per_page: int = 20):
    """按 created_at 倒序分页查询任务列表（不含图片 BLOB / 大结果 JSON）。

    start/end 形如 'YYYY-MM-DD HH:MM:SS'，可选。
    返回 (total, items)。
    """
    where, params = [], []
    if start:
        where.append("created_at >= %s"); params.append(start)
    if end:
        where.append("created_at <= %s"); params.append(end)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ocr_task" + wsql, params)
            total = cur.fetchone()[0]
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT task_id, status, scene, backend, created_at, finished_at, error_msg, "
                "image1_name, image2_name "
                "FROM ocr_task" + wsql +
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [per_page, (page - 1) * per_page])
            items = cur.fetchall()
        return total, items
    finally:
        conn.close()


def purge_old(days: int) -> int:
    """删除 N 天以前的任务（含图片 BLOB），返回删除条数。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ocr_task WHERE created_at < (NOW() - INTERVAL %s DAY)",
                (int(days),))
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()
