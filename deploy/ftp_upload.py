#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FTP 上传脚本（Python 3）—— 把 dist/ 下的部署包上传到内网 FTP / OSS。

支持两个部署包，可任选其一上传：
  img-sieve : img-sieve-linux-x86_64.tar.gz   （图片识别服务，约 253 MiB）
  mysql     : mysql-offline-centos7.tar.gz    （CentOS 7 离线 MySQL 安装包，约 218 MiB）

特点：
  - 大文件分块传输，进度条 + 平均/瞬时速率
  - 断点续传：失败重试时用 REST 跳过已上传部分，不从头再来
  - --force 强制覆盖：远端文件内容损坏但大小恰好相同时，必须用它重传
  - 上传完成后自动校验远端文件大小

用法：
    python3 deploy/ftp_upload.py                          # 默认上传 img-sieve
    python3 deploy/ftp_upload.py -t mysql                 # 改上传 mysql 包
    python3 deploy/ftp_upload.py -t mysql --force         # 强制重传 mysql 包
    python3 deploy/ftp_upload.py --list                   # 查看可选包与本地文件

远端目录：ftp://10.104.210.244/project_amtp/dosp/
"""

from __future__ import annotations

import argparse
import ftplib
import os
import socket
import sys
import time

# ---------------------------------------------------------------- 配置区
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "dist")

# 可选部署包：名字 -> 远端文件名
PACKAGES = {
    "img-sieve": "img-sieve-linux-x86_64.tar.gz",
    "mysql": "mysql-offline-centos7.tar.gz",
}
DEFAULT_PACKAGE = "img-sieve"

# OSS / FTP 服务器配置（保持不变）
FTP_HOST = "10.104.210.244"
FTP_PORT = 21
FTP_USER = "ai/jupyter/nlpbigdata"
FTP_PASS = "ftpnlpbigdata"
REMOTE_DIR = "/project_amtp/dosp"

MAX_RETRIES = 20
RETRY_DELAY = 5          # 秒
BLOCKSIZE = 256 * 1024   # 256KB
UPDATE_INTERVAL = 0.2    # 200ms 刷一次进度
CONNECT_TIMEOUT = 120
# ------------------------------------------------------------------


def local_path_for(package: str) -> str:
    """取某个包在本地的路径（dist/<远端同名文件>）。"""
    return os.path.join(DIST_DIR, PACKAGES[package])


def format_speed(bps: float | None) -> str:
    """把字节/秒格式化成人类可读的速度。"""
    if bps is None:
        return "?"
    if bps >= 1024 * 1024:
        return f"{bps / (1024.0 * 1024.0):.2f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024.0:.2f} KB/s"
    return f"{bps:.2f} B/s"


def format_size(size: float | None) -> str:
    """把字节数格式化成人类可读的体积。"""
    if size is None:
        return "?"
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024.0 ** 3):.2f} GiB"
    if size >= 1024 * 1024:
        return f"{size / (1024.0 * 1024.0):.2f} MiB"
    if size >= 1024:
        return f"{size / 1024.0:.2f} KiB"
    return f"{int(size)} B"


class ProgressReporter:
    """按 UPDATE_INTERVAL 节流地刷新一行进度。"""

    def __init__(self, total: int):
        self.total = total
        self.transferred = 0
        self.start = time.time()
        self._prev_time = self.start
        self._prev_bytes = 0

    def __call__(self, chunk: bytes) -> None:
        self.transferred += len(chunk)
        now = time.time()
        if now - self._prev_time < UPDATE_INTERVAL:
            return
        self.render(now)
        self._prev_time = now
        self._prev_bytes = self.transferred

    def render(self, now: float | None = None) -> None:
        now = now or time.time()
        elapsed = now - self.start
        interval = now - self._prev_time
        avg = self.transferred / elapsed if elapsed > 0 else 0.0
        inst = (self.transferred - self._prev_bytes) / interval if interval > 0 else avg

        bar_w = 40
        if self.total > 0:
            pct = self.transferred / self.total * 100.0
            filled = min(bar_w, int(bar_w * self.transferred / self.total))
        else:
            pct, filled = 0.0, 0
        bar = "=" * filled + " " * (bar_w - filled)

        sys.stdout.write(
            f"\r  [{bar}] {pct:5.1f}%  "
            f"{format_size(self.transferred)} / {format_size(self.total)}  "
            f"{format_speed(avg)}  (inst: {format_speed(inst)})  "
        )
        sys.stdout.flush()


def ensure_remote_dir(ftp: ftplib.FTP) -> None:
    """切换到 REMOTE_DIR，必要时逐级创建。"""
    if not (REMOTE_DIR and REMOTE_DIR.strip()):
        return
    try:
        ftp.cwd(REMOTE_DIR)
        return
    except Exception:
        pass
    current = ""
    for part in [p for p in REMOTE_DIR.split("/") if p]:
        current += "/" + part
        try:
            ftp.cwd(current)
        except Exception:
            try:
                ftp.mkd(current)
                ftp.cwd(current)
            except Exception:
                pass


def connect_ftp() -> ftplib.FTP:
    """建立连接并切换到远端目录（不存在则逐级创建）。"""
    ftp = ftplib.FTP(timeout=CONNECT_TIMEOUT)
    ftp.connect(FTP_HOST, FTP_PORT, timeout=CONNECT_TIMEOUT)
    ftp.login(FTP_USER, FTP_PASS)
    ftp.set_pasv(True)
    ensure_remote_dir(ftp)
    return ftp


def remote_size(ftp: ftplib.FTP, filename: str) -> int:
    """取远端已存在文件的大小，0 表示不存在或查询失败。"""
    try:
        size = ftp.size(filename)
        return int(size) if size is not None else 0
    except Exception:
        return 0


def _brief(exc: Exception) -> str:
    """压缩异常信息里的换行，日志更易读。"""
    return " ".join(str(exc).split())[:120]


def clear_remote(ftp: ftplib.FTP, filename: str) -> str:
    """让远端同名文件不再挡路，返回采取的策略名。

    依次尝试（权限要求由严到宽）：
      1. DELE 删除
      2. RENAME 改名让路（很多服务器不给删权限，但允许改名）——
         旧文件被改名保留，相当于留了个 .old-xxx，可事后人工清理
      3. 放弃，直接覆盖上传（依赖 STOR 自身截断）

    第 3 种有风险：若服务器 STOR 不截断，新文件比旧的短时尾部会残留旧数据，
    且重试只会让文件越来越大。因此调用方需要知道实际用了哪种策略。
    """
    try:
        ftp.delete(filename)
        print("  [1/3] 已删除远端旧文件")
        return "delete"
    except Exception as exc:
        print(f"  [1/3] 删除被拒: {_brief(exc)}")

    backup = f"{filename}.old-{int(time.time())}"
    try:
        ftp.rename(filename, backup)
        print(f"  [2/3] 无删除权限，改为改名让路 -> {backup}")
        print("        （旧文件已保留，确认新文件正常后可手动删除）")
        return "rename"
    except Exception as exc:
        print(f"  [2/3] 改名也被拒: {_brief(exc)}")

    print("  [3/3] 无删除/改名权限，直接覆盖上传（依赖服务端截断）")
    return "overwrite"


def reset_progress(reporter: ProgressReporter, transferred: int = 0) -> None:
    """重置进度状态（续传降级为全量上传时用）。"""
    reporter.transferred = transferred
    reporter.start = time.time()
    reporter._prev_time = reporter.start
    reporter._prev_bytes = transferred


def transfer(ftp: ftplib.FTP, local_file: str, filename: str,
             offset: int, reporter: ProgressReporter) -> None:
    """执行传输。

    offset > 0 时按 REST+STOR -> APPE -> 全量 STOR 的顺序降级，
    兼容对断点续传支持程度不同的 FTP 服务器。
    """
    with open(local_file, "rb") as fp:
        if offset > 0:
            fp.seek(offset)
            # 方案 1：REST + STOR（最标准）
            try:
                ftp.storbinary(f"STOR {filename}", fp, blocksize=BLOCKSIZE,
                               rest=offset, callback=reporter)
                return
            except Exception:
                reporter.render()
                print("\n  REST+STOR 续传失败，尝试 APPE 追加...")
                fp.seek(offset)
                # 方案 2：APPE 追加
                try:
                    ftp.storbinary(f"APPE {filename}", fp, blocksize=BLOCKSIZE,
                                   rest=offset, callback=reporter)
                    return
                except Exception:
                    reporter.render()
                    print("\n  APPE 续传失败，退化为全量上传。")
                    reset_progress(reporter)
                    fp.seek(0)
        fp.seek(0)
        ftp.storbinary(f"STOR {filename}", fp, blocksize=BLOCKSIZE,
                       callback=reporter)


def upload(local_file: str, use_resume: bool = True,
           force: bool = False) -> bool:
    if not os.path.isfile(local_file):
        print(f"本地文件不存在 - {local_file}")
        print(f"请先打包生成该文件，或用 --file 指定其他路径。")
        return False

    total_size = os.path.getsize(local_file)
    filename = os.path.basename(local_file)

    print(f"本地文件: {local_file}")
    print(f"远端路径: ftp://{FTP_HOST}:{FTP_PORT}{REMOTE_DIR}/{filename}")
    print(f"文件大小: {format_size(total_size)}")
    if force:
        print("模式    : 强制覆盖（忽略远端已有内容，先清理再全量上传）")
    sys.stdout.flush()

    last_err: str | None = None
    strategy = ""            # force 模式下实际采用的让路策略
    for attempt in range(1, MAX_RETRIES + 1):
        ftp: ftplib.FTP | None = None
        try:
            if attempt > 1:
                print(f"\n第 {attempt}/{MAX_RETRIES} 次重试"
                      f"（等待 {RETRY_DELAY}s）... 上次错误: {last_err}")
                sys.stdout.flush()
                time.sleep(RETRY_DELAY)

            ftp = connect_ftp()

            if force:
                # 远端文件已损坏，必须先让它让路：
                # 既避免续传到错误内容上，也保证 STOR 从 0 干净写入
                print("\n强制覆盖：清理远端同名文件")
                strategy = clear_remote(ftp, filename)
                offset = 0
            else:
                offset = remote_size(ftp, filename) if use_resume else 0
                if total_size > 0 and offset == total_size:
                    print("\n远端文件大小与本地一致，跳过上传。")
                    print("提示: 若远端文件内容损坏（如传输截断后大小恰好相同），"
                          "请加 --force 强制重传。")
                    ftp.quit()
                    return True
                if offset > total_size:
                    print(f"\n远端文件比本地还大（{format_size(offset)} > "
                          f"{format_size(total_size)}），从头覆盖上传。")
                    offset = 0
                elif offset > 0:
                    print(f"\n断点续传：远端已有 {format_size(offset)} / "
                          f"{format_size(total_size)}"
                          f"（{offset / total_size * 100:.1f}%）")

            reporter = ProgressReporter(total_size)
            reset_progress(reporter, offset)

            transfer(ftp, local_file, filename, offset, reporter)

            elapsed = time.time() - reporter.start
            reporter.transferred = total_size
            reporter.render()
            sys.stdout.write("\n")
            sys.stdout.flush()

            remote = remote_size(ftp, filename)
            if remote == total_size:
                print(f"上传成功: ftp://{FTP_HOST}:{FTP_PORT}{REMOTE_DIR}/{filename}"
                      f"（耗时 {elapsed:.1f}s，"
                      f"平均 {format_speed(total_size / elapsed if elapsed else 0)}）"
                      f"  远端大小校验通过 ✓")
                if force and strategy == "rename":
                    print("提示: 远端旧文件已改名为 .old-* 备份，确认后可手动删除。")
                ftp.quit()
                return True

            last_err = f"远端大小不符：{format_size(remote)} != {format_size(total_size)}"

            # 远端比本地大 —— 多半是「无删除权限 + 服务端 STOR 不截断」
            # 导致旧数据残留。再重试只会越滚越大，必须立刻停。
            if remote > total_size:
                if force and strategy == "overwrite":
                    print(f"\n远端文件比本地大 {format_size(remote - total_size)}，"
                          f"说明旧文件未被截断，新数据被追加在了后面。")
                    print("原因：服务器既不给删除权限，STOR 也不会截断已有文件。")
                    print("请在服务器上手动处理后重试，例如：")
                    print(f"    rm {REMOTE_DIR}/{filename}")
                    print("或让管理员开放该目录的删除/改名权限后，"
                          "本脚本会自动改用改名让路的方式。")
                else:
                    print(f"\n远端文件比本地大（{format_size(remote)} > "
                          f"{format_size(total_size)}），停止重试。")
                    print("可能是远端存在残留文件，请手动删除后重试。")
                break

        except socket.timeout as exc:
            last_err = f"连接超时: {exc}"
            sys.stdout.write("\n")
            print(f"\n[第 {attempt} 次] 超时: {last_err}")
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            print("\n上传已被用户中断。")
            return False
        except Exception as exc:
            last_err = str(exc)
            sys.stdout.write("\n")
            print(f"\n[第 {attempt} 次] 失败: {last_err}")
        finally:
            if ftp is not None:
                try:
                    ftp.close()
                except Exception:
                    pass

    print(f"\n上传失败。最后错误: {last_err}")
    return False


def list_packages() -> None:
    """列出可选包及其本地文件状态。"""
    print("可上传的部署包：\n")
    for name, fname in PACKAGES.items():
        path = os.path.join(DIST_DIR, fname)
        mark = "默认" if name == DEFAULT_PACKAGE else "    "
        if os.path.isfile(path):
            size = format_size(os.path.getsize(path))
            state = "就绪"
        else:
            size = "-"
            state = "缺失（需先打包）"
        print(f"  [{mark}] {name:<12} {fname:<36} {size:>10}  {state}")
    print(f"\n远端目录: ftp://{FTP_HOST}:{FTP_PORT}{REMOTE_DIR}/")
    print(f"默认包  : {DEFAULT_PACKAGE}（用 -t/--target 指定其它包）")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="ftp_upload.py",
        description="上传 dist/ 下的部署包到内网 FTP/OSS（默认走断点续传）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python3 ftp_upload.py                    # 上传 img-sieve 包（默认）
  python3 ftp_upload.py -t mysql           # 上传 mysql 离线包
  python3 ftp_upload.py -t mysql --force   # 强制重传 mysql 包
  python3 ftp_upload.py --list             # 查看可选包与本地文件状态

说明:
  两个包通常不需要都传：给部署 img-sieve 的机器传 img-sieve，
  给需要装数据库的机器传 mysql 即可。
""",
    )
    parser.add_argument("-t", "--target", choices=sorted(PACKAGES),
                        default=DEFAULT_PACKAGE,
                        help=f"要上传哪个包（默认 {DEFAULT_PACKAGE}）")
    parser.add_argument("-f", "--force", action="store_true",
                        help="强制覆盖：先清理远端同名文件再全量上传。"
                             "远端文件内容损坏但大小恰好相同时必须用此参数。")
    parser.add_argument("--no-resume", "--no", dest="no_resume",
                        action="store_true",
                        help="禁用断点续传，从头全量上传")
    parser.add_argument("--file", help="直接指定本地文件路径（覆盖 -t 的路径推导）")
    parser.add_argument("--list", action="store_true",
                        help="列出可选包及本地文件状态后退出")

    args = parser.parse_args(argv[1:])

    if args.list:
        list_packages()
        return 0

    if args.file:
        local_file = args.file
    else:
        local_file = local_path_for(args.target)
    if not os.path.isabs(local_file):
        local_file = os.path.abspath(local_file)

    return 0 if upload(local_file, not args.no_resume, args.force) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))