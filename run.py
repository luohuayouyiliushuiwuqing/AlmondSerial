"""启动入口：处理端口占用。

规则：
- 端口空闲 → 直接用。
- 端口被占 → 检查占用进程是否是自己（本项目的 uvicorn app.main）：
  - 是自己 → 杀掉旧进程，同端口重启；
  - 不是自己 → 跳端口（+1 递增，最多 MAX_SCAN 个）。

用法：python run.py [--host 0.0.0.0] [--port 8000]
"""

import os
import re
import signal
import socket
import subprocess
import sys
import time

HOST = "0.0.0.0"
START_PORT = 8000
MAX_SCAN = 20


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _pid_listening_on(port: int) -> int | None:
    """返回监听指定端口的进程 PID（找不到返回 None）。优先 ss，回退 lsof。"""
    for cmd in (["ss", "-tlnp"], ["lsof", "-iTCP", f":{port}", "-sTCP:LISTEN", "-t"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        for line in out.splitlines():
            if f":{port}" not in line and cmd[0] != "lsof":
                continue
            m = re.search(r"pid=(\d+)", line)
            if m:
                return int(m.group(1))
            # lsof -t 输出直接是 PID
            if cmd[0] == "lsof" and line.strip().isdigit():
                return int(line.strip())
    return None


def _is_own(pid: int) -> bool:
    """判断进程是否是自己（命令行含 app.main / run.py / uvicorn）。"""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return False
    return any(k in cmd for k in ("app.main", "run.py", "uvicorn"))


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _opt(name: str, default: str) -> str:
    if name not in sys.argv:
        return default
    idx = sys.argv.index(name)
    return sys.argv[idx + 1] if idx + 1 < len(sys.argv) else default


def main() -> None:
    host = _opt("--host", HOST)
    port = int(_opt("--port", str(START_PORT)))

    for _ in range(MAX_SCAN):
        if _port_free(port):
            break
        pid = _pid_listening_on(port)
        if pid is not None and _is_own(pid):
            print(f"端口 {port} 被本程序占用（PID {pid}），杀掉后重启…")
            _kill(pid)
            time.sleep(1)
            if _port_free(port):
                break
            print(f"杀掉后端口 {port} 仍占用，尝试下一个…")
        else:
            print(f"端口 {port} 被其他程序占用，尝试下一个…")
        port += 1
    else:
        print(f"从 {START_PORT} 起的 {MAX_SCAN} 个端口全被占用，放弃启动", file=sys.stderr)
        sys.exit(1)

    # 确保 app 可被导入（run.py 就在项目根，保险起见把脚本目录放进 sys.path）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    import uvicorn
    print(f"启动服务：http://{host}:{port}/")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
