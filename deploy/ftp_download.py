#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FTP 下载脚本（Python 2 写法）。

用途：在内网服务器（Python 2 环境）上，从 FTP 服务器下载部署包。

支持按需选择下载内容：
  mysql     : 只下 mysql 离线包（mysql-offline-centos7.tar.gz）
  all       : 下 mysql + img-sieve 两个包
  img-sieve : 只下 img-sieve 包

特点：
  - 大文件分块下载，进度条 + 平均/瞬时速率
  - 断点续传：失败重试时用 REST 跳过已下载部分，不从头再来
  - 逐个下载：中途某个包失败不影响后续，最终汇总成败
  - 下载完成后校验文件大小
  - 控制台输出全为 ASCII，避免 py2 下 unicode 写 stdout 报错

用法：
    python ftp_download.py                 # 引导式菜单（推荐）
    python ftp_download.py mysql           # 直接指定模式（适合脚本/cron）
    python ftp_download.py all             # mysql + img-sieve 全下
    python ftp_download.py img-sieve       # 只下 img-sieve
    python ftp_download.py mysql /data/    # 下到指定目录
    python ftp_download.py mysql --force   # 忽略本地已有文件，重新完整下载

远端目录：ftp://10.104.210.244/project_amtp/dosp/
"""

from __future__ import print_function

import ftplib
import os
import socket
import sys
import time

# ---------------------------------------------------------------- 配置区
# OSS / FTP 服务器配置（保持不变）
FTP_HOST = "10.104.210.244"
FTP_PORT = 21
FTP_USER = "ai/jupyter/nlpbigdata"
FTP_PASS = "ftpnlpbigdata"
REMOTE_DIR = "/project_amtp/dosp"

# 可下载的部署包：名字 -> 说明（会显示在菜单里）
FILES = {
    "mysql": "mysql-offline-centos7.tar.gz",
    "img-sieve": "img-sieve-linux-x86_64.tar.gz",
}
PKG_DESC = {
    "mysql": "MySQL 离线安装包（CentOS 7，含 rpms/ 与 install.sh）",
    "img-sieve": "图片识别服务包（识别服务 + 依赖 + install.sh）",
}
# 各模式对应的包列表（顺序即下载顺序）
MODES = {
    "mysql": ["mysql"],
    "img-sieve": ["img-sieve"],
    "all": ["mysql", "img-sieve"],
}
DEFAULT_MODE = "mysql"

MAX_RETRIES = 20
RETRY_DELAY = 5          # seconds
BLOCKSIZE = 262144       # 256KB per read
UPDATE_INTERVAL = 0.2    # 200ms 刷一次进度
CONNECT_TIMEOUT = 120
SOCK_TIMEOUT = 1.0       # 数据连接短超时，保证 Ctrl+C 可中断
# ------------------------------------------------------------------


# ------------------------------------------------------------ 输入与输出
# py2 用 raw_input()，py3 用 input()。
# 注意不能在同一个 try 里写 `except NameError: input(...)` ——
# py3 下 raw_input 会在函数编译/执行时报 NameError，异常处理器里再写
# raw_input 同样报错。必须在模块级解析出一个可调用的函数对象。
try:
    _ask = raw_input                      # py2
except NameError:
    _ask = input                          # py3


def ask(prompt):
    """读取一行用户输入。EOF（Ctrl-D / 管道输入结束）返回 None。"""
    try:
        return _ask(prompt)
    except (EOFError, KeyboardInterrupt):
        print("")
        return None


# ------------------------------------------------- 终端编码 / 中文支持
# 内网服务器 locale 可能是 POSIX/C，此时 sys.stdout.encoding 为 ANSI_X3.4-1968，
# 直接 print 中文会抛 UnicodeEncodeError 把脚本打挂。所以这里探测一次，
# 中文不可用时统一退化成英文，而不是崩掉。
def _detect_cjk():
    """探测当前 stdout 是否能安全输出中文。"""
    try:
        enc = sys.stdout.encoding or "ascii"
    except Exception:
        return False
    try:
        u"测试".encode(enc)
        return True
    except Exception:
        return False


CJK_OK = _detect_cjk()


def cn(zh, en):
    """按终端能力返回中文或英文文案。"""
    return zh if CJK_OK else en


def show_remote_files(host, port, user, pwd, remote_dir):
    """连一次 FTP，列出远端可下载文件及大小。

    返回 [(filename, size_or_None), ...]；连不上返回 None。
    """
    ftp = None
    try:
        ftp = ftplib.FTP(timeout=CONNECT_TIMEOUT)
        ftp.connect(host, port, timeout=CONNECT_TIMEOUT)
        ftp.login(user, pwd)
        ftp.set_pasv(True)
        ftp.cwd(remote_dir)
        names = ftp.nlst()
        out = []
        for n in names:
            size = remote_size(ftp, n)
            out.append((n, size))
        try:
            ftp.quit()
        except Exception:
            pass
        return out
    except Exception:
        if ftp is not None:
            try:
                ftp.close()
            except Exception:
                pass
        return None


def interactive_menu(host, port, user, pwd, remote_dir):
    """引导式菜单：让用户选要下哪些包，返回 (mode, dest_dir) 或 None。

    无参数运行（或 -i）时进入。支持 EOF/Ctrl-C 优雅退出，
    也支持直接输入数字或关键字，不区分大小写。
    """
    if CJK_OK:
        banner = [
            "=" * 62,
            "  内网 FTP 部署包下载向导",
            "  服务器: ftp://%s:%s%s" % (host, port, remote_dir),
            "=" * 62,
        ]
    else:
        banner = [
            "=" * 62,
            "  FTP deployment package downloader",
            "  server: ftp://%s:%s%s" % (host, port, remote_dir),
            "=" * 62,
        ]
    for line in banner:
        print(line)

    # 先列远端实际有的文件，让选择更有依据
    listing = show_remote_files(host, port, user, pwd, remote_dir)
    print("")
    if listing is None:
        print(cn("  （无法连接服务器，将使用内置包清单下载）",
                 "  (cannot reach server, using built-in package list)"))
        listing = []
    elif listing:
        print(cn("  服务器上可下载的文件：", "  files available on server:"))
        for name, size in sorted(listing):
            kind = " ".join(
                ["[%s]" % p for p, f in FILES.items() if f == name])
            print("    %-38s %10s  %s"
                  % (name, format_size(size), kind))

    print("")
    print(cn("  请选择要下载的内容：", "  choose what to download:"))
    print("")
    print("    [1] %-12s %s"
          % ("mysql", cn("仅 MySQL 离线包（大多数机器只需这个）",
                         "mysql offline package only")))
    print("    [2] %-12s %s"
          % ("all", cn("MySQL + 图片识别服务（都需要）",
                       "mysql + img-sieve (both needed)")))
    print("    [3] %-12s %s"
          % ("img-sieve", cn("仅图片识别服务包",
                             "img-sieve package only")))
    print("    [4] %-12s %s"
          % ("custom", cn("手动输入远端文件名",
                          "type remote filename manually")))
    print("    [q] %-12s %s"
          % ("quit", cn("退出", "quit")))
    print("")

    hint = cn("  输入选项后回车（直接回车 = %s）: " % DEFAULT_MODE,
              "  choice [Enter = %s]: " % DEFAULT_MODE)
    raw = ask(hint)
    if raw is None:
        print(cn("已取消。", "cancelled."))
        return None

    choice = raw.strip().lower()
    if choice in ("", "1", "mysql"):
        mode = "mysql"
    elif choice in ("2", "all"):
        mode = "all"
    elif choice in ("3", "img", "img-sieve", "sieve"):
        mode = "img-sieve"
    elif choice in ("q", "quit", "exit"):
        print(cn("已退出。", "bye."))
        return None
    elif choice == "4":
        mode = prompt_custom_file(host, port, user, pwd, remote_dir)
        if mode is None:
            return None
    else:
        print(cn("  无法识别的选项：%s" % choice,
                 "  unknown choice: %s" % choice))
        return ("__RETRY__", None)

    # 目标目录
    dest_hint = cn("  保存到哪个目录（直接回车 = 当前目录）: ",
                   "  destination dir [Enter = current]: ")
    dest = ask(dest_hint)
    if dest is None:
        print(cn("已取消。", "cancelled."))
        return None
    dest = dest.strip() or "."

    print("")
    if mode.startswith("__custom__:"):
        pretty = [mode.split(":", 1)[1]]
    else:
        pretty = MODES.get(mode) or [mode]
    if CJK_OK:
        print("即将下载：%s" % "、".join(pretty))
    else:
        print("will download: %s" % ", ".join(pretty))
    print(cn("目标目录：%s" % dest, "dest dir: %s" % dest))
    ok = ask(cn("  确认开始？[Y/n]: ", "  start? [Y/n]: "))
    if ok is None:
        print(cn("已取消。", "cancelled."))
        return None
    if ok.strip().lower() not in ("", "y", "yes"):
        print(cn("已取消。", "cancelled."))
        return None

    return (mode, dest)


def prompt_custom_file(host, port, user, pwd, remote_dir):
    """让用户手动输入远端文件名，做存在性校验。"""
    name = ask(cn("  请输入远端文件名: ", "  remote filename: "))
    if name is None:
        return None
    name = name.strip()
    if not name:
        print(cn("  未输入，已取消。", "  empty input, cancelled."))
        return None

    listing = show_remote_files(host, port, user, pwd, remote_dir)
    if listing is None:
        # 连不上就不校验，交给下载阶段报错
        return "__custom__:%s" % name
    names = [n for n, _ in listing]
    if name in names or name in FILES.values():
        return "__custom__:%s" % name
    print(cn("  服务器上找不到该文件。可选文件如下：",
             "  file not found on server. available:"))
    for n, size in sorted(listing):
        print("    %-38s %10s" % (n, format_size(size)))
    return None


def download_custom(host, user, pwd, remote_dir, filename, dest_dir,
                    use_resume=True, port=None):
    """下载用户手动指定的单个文件。"""
    if not os.path.isdir(dest_dir):
        try:
            os.makedirs(dest_dir)
        except Exception as exc:
            print(cn("无法创建目录 %s: %s" % (dest_dir, exc),
                     "cannot create dir %s: %s" % (dest_dir, exc)))
            return False
    local_file = os.path.join(dest_dir, filename)
    print("")
    print(cn("[custom] 下载 %s" % filename, "[custom] downloading %s" % filename))
    return download(host, user, pwd, remote_dir, filename, local_file,
                    use_resume, port)


def format_speed(bps):
    """把字节/秒格式化成人类可读的速度。"""
    if bps is None:
        return "?"
    if bps >= 1024 * 1024:
        return "%.2f MB/s" % (bps / (1024.0 * 1024.0))
    elif bps >= 1024:
        return "%.2f KB/s" % (bps / 1024.0)
    else:
        return "%.2f B/s" % bps


def format_size(size):
    """把字节数格式化成人类可读的体积。"""
    if size is None:
        return "?"
    if size >= 1024 * 1024 * 1024:
        return "%.2f GiB" % (size / (1024.0 ** 3))
    elif size >= 1024 * 1024:
        return "%.2f MiB" % (size / (1024.0 * 1024.0))
    elif size >= 1024:
        return "%.2f KiB" % (size / 1024.0)
    else:
        return "%d B" % size


class ProgressReporter(object):
    """按 UPDATE_INTERVAL 节流地刷新一行进度（兼容 py2，无 nonlocal）。"""

    def __init__(self, total):
        self.total = total
        self.transferred = 0
        self.start = time.time()
        self._prev_time = self.start
        self._prev_bytes = 0

    def add(self, nbytes):
        self.transferred += nbytes
        now = time.time()
        if now - self._prev_time < UPDATE_INTERVAL:
            return
        self.render(now)
        self._prev_time = now
        self._prev_bytes = self.transferred

    def render(self, now=None):
        now = now or time.time()
        elapsed = now - self.start
        interval = now - self._prev_time
        avg = self.transferred / elapsed if elapsed > 0 else 0.0
        inst = ((self.transferred - self._prev_bytes) / interval
                if interval > 0 else avg)

        bar_w = 40
        if self.total:
            pct = float(self.transferred) / float(self.total) * 100.0
            filled = int(bar_w * float(self.transferred) / float(self.total))
            if filled > bar_w:
                filled = bar_w
            bar = "=" * filled + " " * (bar_w - filled)
            sys.stdout.write(
                "\r  [%s] %5.1f%%  %s / %s  %s  (inst: %s)  "
                % (bar, pct, format_size(self.transferred),
                   format_size(self.total), format_speed(avg),
                   format_speed(inst)))
        else:
            sys.stdout.write(
                "\r  %s  %s  (inst: %s)  "
                % (format_size(self.transferred), format_speed(avg),
                   format_speed(inst)))
        sys.stdout.flush()


def remote_size(ftp, filename):
    """取远端文件大小，失败返回 None。"""
    try:
        size = ftp.size(filename)
        return int(size) if size is not None else None
    except Exception:
        return None


def local_size(path):
    """取本地已下载文件大小，不存在返回 0。"""
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def stream_to_file(ftp, remote_file, fp, offset, reporter):
    """用 transfercmd + 短超时循环读取，保证 Ctrl+C 能中断。

    offset > 0 时由 ftplib 先发 REST <offset>，再发 RETR，
    服务端从 offset 处开始传；这里必须用 rest= 参数，
    不能把 "REST n" 当成 transfercmd 的传输命令。
    """
    if offset > 0:
        fp.seek(offset)
        conn = ftp.transfercmd("RETR %s" % remote_file, rest=offset)
    else:
        conn = ftp.transfercmd("RETR %s" % remote_file)

    conn.settimeout(SOCK_TIMEOUT)
    try:
        while True:
            try:
                chunk = conn.recv(BLOCKSIZE)
            except socket.timeout:
                # 超时是预期的，让出控制权给 Python 处理信号
                continue
            except (socket.error, IOError):
                break
            if not chunk:
                break
            fp.write(chunk)
            reporter.add(len(chunk))
    finally:
        try:
            conn.close()
        except Exception:
            pass
    ftp.voidresp()


def download_once(host, user, pwd, remote_dir, remote_file, local_file,
                  use_resume=True, port=None):
    """建立连接并下载一次，返回 (ok, remote_total)。"""
    if port is None:
        port = FTP_PORT
    ftp = ftplib.FTP(timeout=CONNECT_TIMEOUT)
    ftp.connect(host, port, timeout=CONNECT_TIMEOUT)
    try:
        ftp.login(user, pwd)
        ftp.set_pasv(True)
        ftp.cwd(remote_dir)

        total = remote_size(ftp, remote_file)
        if not total:
            print("  remote file not found or size unknown: %s/%s"
                  % (remote_dir, remote_file))
            return False, None

        offset = local_size(local_file) if use_resume else 0
        if offset > total:
            offset = 0
        if offset == total:
            print("  already complete (%s), skip download."
                  % format_size(total))
            return True, total

        print("  remote: ftp://%s%s/%s" % (host, remote_dir, remote_file))
        print("  local : %s" % local_file)
        print("  size  : %s" % format_size(total))
        if offset > 0:
            print("  resume: %s already downloaded (%.1f%%)"
                  % (format_size(offset), float(offset) / float(total) * 100.0))
        sys.stdout.flush()

        reporter = ProgressReporter(total)
        reporter.transferred = offset
        reporter.start = time.time()
        reporter._prev_time = reporter.start
        reporter._prev_bytes = offset

        fp = open(local_file, "ab" if offset > 0 else "wb")
        try:
            stream_to_file(ftp, remote_file, fp, offset, reporter)
        finally:
            fp.close()

        elapsed = time.time() - reporter.start
        reporter.transferred = total
        reporter.render()
        sys.stdout.write("\n")
        sys.stdout.flush()

        got = local_size(local_file)
        if got != total:
            print("  size mismatch: local %s != remote %s"
                  % (format_size(got), format_size(total)))
            return False, total

        print("  download complete (elapsed: %.1f s, avg: %s)"
              % (elapsed, format_speed(total / elapsed if elapsed else 0)))
        return True, total

    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def download(host, user, pwd, remote_dir, remote_file, local_file,
             use_resume=True, port=None):
    """带重试的下载入口。"""
    if port is None:
        port = FTP_PORT
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt > 1:
                print("\n  retry %d/%d (waiting %ds)... last error: %s"
                      % (attempt, MAX_RETRIES, RETRY_DELAY, last_err))
                sys.stdout.flush()
                time.sleep(RETRY_DELAY)

            ok, _ = download_once(host, user, pwd, remote_dir, remote_file,
                                  local_file, use_resume, port)
            if ok:
                return True
            last_err = last_err or "transfer returned false"

        except KeyboardInterrupt:
            sys.stdout.write("\n\n  download interrupted by user\n")
            sys.stdout.flush()
            return False
        except Exception as exc:
            last_err = str(exc)
            sys.stdout.write("\n")
            sys.stdout.flush()
            print("\n  [attempt %d] failed: %s" % (attempt, last_err))

    print("\n  exhausted %d retries, download failed. last error: %s"
          % (MAX_RETRIES, last_err))
    return False


def download_all(mode, dest_dir, use_resume, host, user, pwd,
                 remote_dir, port=None):
    """按模式逐个下载包，返回 (成功数, 失败数)。"""
    packages = MODES[mode]
    print("=" * 60)
    print("mode: %s   packages: %s" % (mode, ", ".join(packages)))
    print("remote dir: ftp://%s%s" % (host, remote_dir))
    print("dest: %s" % dest_dir)
    print("=" * 60)

    ok_count = 0
    fail_count = 0
    results = []

    for i, pkg in enumerate(packages):
        filename = FILES[pkg]
        local_file = os.path.join(dest_dir, filename)
        print("")
        print("[%d/%d] downloading %s (%s)"
              % (i + 1, len(packages), pkg, filename))
        sys.stdout.flush()

        ok = download(host, user, pwd, remote_dir, filename, local_file,
                      use_resume, port)
        results.append((pkg, filename, ok))
        if ok:
            ok_count += 1
        else:
            fail_count += 1

    #汇总
    print("")
    print("=" * 60)
    print("summary:")
    for pkg, filename, ok in results:
        mark = "OK  " if ok else "FAIL"
        size = format_size(local_size(os.path.join(dest_dir, filename)))
        print("  [%s] %-10s %-36s %s" % (mark, pkg, filename, size))
    print("  succeeded: %d, failed: %d" % (ok_count, fail_count))
    print("=" * 60)

    # 后续提示
    for pkg, filename, ok in results:
        if not ok:
            continue
        path = os.path.join(dest_dir, filename)
        if pkg == "mysql":
            print("")
            print("mysql offline package: %s" % path)
            print("  next: tar -xzf %s && cd mysql-offline && ./install.sh"
                  % filename)
        else:
            print("")
            print("img-sieve package: %s" % path)
            print("  next: tar -xzf %s && cd img-sieve && ./install.sh"
                  % filename)

    return ok_count, fail_count


def usage():
    print("Usage: python ftp_download.py [mode] [dest_dir] [--force]")
    print("")
    print("  mode      : mysql (default) | all | img-sieve")
    print("              mysql     - only mysql-offline-centos7.tar.gz")
    print("              all       - mysql + img-sieve")
    print("              img-sieve - only img-sieve-linux-x86_64.tar.gz")
    print("  dest_dir  : target directory (default: current dir)")
    print("  --force   : ignore local partial file, download from scratch")
    print("  -i        : force interactive menu even if args given")
    print("")
    print("Examples:")
    print("  python ftp_download.py                # interactive menu")
    print("  python ftp_download.py mysql /data    # scriptable, no menu")
    print("  python ftp_download.py all /data")
    print("")
    print("Note: running with no arguments shows an interactive menu.")


def run_interactive(host, port, user, pwd, remote_dir, use_resume):
    """进入引导式菜单并执行下载，返回进程退出码。"""
    # 最多重试 3 次（用户输错时重新给菜单）
    for _ in range(3):
        sel = interactive_menu(host, port, user, pwd, remote_dir)
        if sel is None:
            return 0            # 用户主动退出
        if isinstance(sel, tuple) and sel and sel[0] == "__RETRY__":
            print(cn("请重新选择。\n", "please choose again.\n"))
            continue

        mode, dest_dir = sel

        # 自定义文件名
        if mode.startswith("__custom__:"):
            fname = mode.split(":", 1)[1]
            ok = download_custom(host, user, pwd, remote_dir, fname,
                                 dest_dir, use_resume, port)
            return 0 if ok else 1

        if not os.path.isdir(dest_dir):
            try:
                os.makedirs(dest_dir)
                print(cn("已创建目录：%s" % dest_dir,
                         "created dir: %s" % dest_dir))
            except Exception as exc:
                print(cn("无法创建目录 %s: %s" % (dest_dir, exc),
                         "cannot create dir %s: %s" % (dest_dir, exc)))
                return 1

        _, fail = download_all(mode, dest_dir, use_resume,
                               host, user, pwd, remote_dir, port)
        return 1 if fail else 0

    print(cn("多次输入无效，已退出。", "too many invalid entries, abort."))
    return 2


def main(argv):
    args = argv[1:]
    use_resume = True
    if "--force" in args or "--no" in args:
        args = [a for a in args if a not in ("--force", "--no")]
        use_resume = False
    if "-h" in args or "--help" in args:
        usage()
        return 0

    force_menu = False
    if "-i" in args or "--interactive" in args:
        args = [a for a in args if a not in ("-i", "--interactive")]
        force_menu = True

    # 无参数 -> 引导式菜单；有参数 -> 直接按参数执行（便于 cron/脚本）
    if force_menu or not args:
        return run_interactive(FTP_HOST, FTP_PORT, FTP_USER, FTP_PASS,
                               REMOTE_DIR, use_resume)

    mode = args[0]
    if mode not in MODES:
        print("Unknown mode: %s" % mode)
        print("")
        usage()
        return 2

    dest_dir = args[1] if len(args) > 1 else "."
    if dest_dir and not os.path.isdir(dest_dir):
        try:
            os.makedirs(dest_dir)
            print("created dest dir: %s" % dest_dir)
        except Exception as exc:
            print("cannot create dest dir %s: %s" % (dest_dir, exc))
            return 1

    _, fail = download_all(mode, dest_dir, use_resume,
                           FTP_HOST, FTP_USER, FTP_PASS, REMOTE_DIR, FTP_PORT)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))