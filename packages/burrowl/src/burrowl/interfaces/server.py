"""工作台的后台运行、固定网址与停止 / 重启（R21 D-12–D-14）。

`.burrowl/server.json`（每个数据目录一份）：
    port, token           第一次启动时选定并保存，以后网址不变；端口被其他程序占用时换一个并保存
    pid, start_identity   运行中的工作台进程（进程创建时间区分 PID 复用）；正常退出时清除，
    started_at, version   被强行结束时残留，下次 status / start 发现进程已不在就清除
    url, log
token 是网址里的访问口令，和端口一起只监听 127.0.0.1；它存在数据目录的程序状态里，与其他状态同等保护。

后台进程用安装包里的 `burrowl-workbench.exe`（自带 pythonw.exe 的改名副本，无窗口，任务管理器里按这个名字找到），
开发环境退到当前解释器旁的 pythonw.exe。可执行文件经 BURROWL_HOME（current 联接）定位，所以重启会加载新安装的版本。
"""
import contextlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.atomic import process_identity, read_json, write_json_atomic
from ..core.clock import utc_now
from ..storage.layout import STATE_DIR

STATE_FILE = "server.json"
LOG_FILE = "logs/serve.log"
LOG_LIMIT = 2 * 1024 * 1024
RUNTIME_KEYS = ("pid", "start_identity", "started_at", "version", "url", "log")
WORKBENCH_EXE = "burrowl-workbench.exe"


def _state_path(root: Path) -> Path:
    return root / STATE_DIR / STATE_FILE


def load(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    try:
        return read_json(path) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _save(root: Path, value: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value, pretty=True)


def url_of(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/{token}/"


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def endpoint(root: Path, requested: int = 0) -> tuple[int, str]:
    """要监听的端口与 token：requested 非 0 时用它；否则沿用保存的端口（被占用就换一个空闲端口）。"""
    state = load(root)
    token = state.get("token") or secrets.token_hex(24)
    port = requested or state.get("port") or 0
    if not port or (not requested and not port_free(port)):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
    if state.get("port") != port or state.get("token") != token:
        _save(root, {**state, "port": port, "token": token})
    return port, token


def running(root: Path) -> dict[str, Any] | None:
    """运行中的工作台；记录的进程已不在时清除残留并返回 None。"""
    state = load(root)
    pid, identity = state.get("pid"), state.get("start_identity")
    if type(pid) is not int:
        return None
    current = process_identity(pid)
    if current is None or (identity not in (None, "unknown") and current not in ("unknown", identity)):
        _save(root, {k: v for k, v in state.items() if k not in RUNTIME_KEYS})
        return None
    return state


def mark_running(root: Path, port: int, token: str) -> None:
    pid = os.getpid()
    _save(root, {**load(root), "port": port, "token": token, "pid": pid, "start_identity": process_identity(pid),
                 "started_at": utc_now(), "version": __version__, "url": url_of(port, token),
                 "log": str(root / STATE_DIR / LOG_FILE)})


def mark_stopped(root: Path) -> None:
    state = load(root)
    if state.get("pid") == os.getpid():
        _save(root, {k: v for k, v in state.items() if k not in RUNTIME_KEYS})


def installed_version() -> str | None:
    """安装包里当前切换到的版本（current.txt）；开发环境为 None。"""
    home = os.environ.get("BURROWL_HOME")
    if not home:
        return None
    with contextlib.suppress(OSError):
        return (Path(home).resolve().parent.parent / "current.txt").read_text(encoding="utf-8").strip() or None
    return None


def _executable() -> list[str]:
    home = os.environ.get("BURROWL_HOME")
    if home and (exe := Path(home) / "python" / WORKBENCH_EXE).is_file():
        return [str(exe)]
    here = Path(sys.executable)
    pythonw = here.with_name("pythonw.exe")
    return [str(pythonw if pythonw.is_file() else here)]


def launch(root: Path, *, work_dir: Path | None = None, mode: str | None = None, takeover: int | None = None) -> int:
    """以无窗口的后台进程启动工作台，返回进程号。输出写到 .burrowl/logs/serve.log（超过 2 MB 时清空重写）。"""
    log = root / STATE_DIR / LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.is_file() and log.stat().st_size > LOG_LIMIT:
        log.unlink()
    args = [*_executable(), "-m", "burrowl.interfaces.cli", "serve", "--no-browser", "-d", str(root)]
    if work_dir:
        args += ["--work-dir", str(work_dir)]
    if mode:
        args += ["--mode", mode]
    if takeover:
        args += ["--takeover", str(takeover)]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    flags = 0
    if os.name == "nt":  # 脱离当前控制台：关掉启动它的命令行窗口不会结束工作台
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with log.open("ab") as out:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env,
                                   creationflags=flags, close_fds=True, cwd=str(root))
    return process.pid


def responding(state: dict[str, Any], timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{state['url']}api/v1/ops", timeout=timeout) as response:  # noqa: S310 - 本机地址
            return response.status == 200
    except (OSError, KeyError, ValueError):
        return False


def wait_ready(root: Path, *, after: str | None = None, timeout: float = 60.0) -> dict[str, Any] | None:
    """等新进程写好运行信息并能响应；after 给出时要求 started_at 晚于它（重启）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = running(root)
        if state and (after is None or state.get("started_at", "") > after) and responding(state):
            return state
        time.sleep(0.3)
    return None


def wait_exit(pid: int, identity: str | None, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = process_identity(pid)
        if current is None or (identity not in (None, "unknown") and current not in ("unknown", identity)):
            return True
        time.sleep(0.2)
    return False


def request_stop(state: dict[str, Any]) -> bool:
    """经工作台自己的接口请求正常退出（与首页的"停止服务"同一条路径）。"""
    origin = state["url"].split("/", 3)[:3]
    request = urllib.request.Request(f"{state['url']}api/v1/ops/server.stop", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "Origin": "/".join(origin)})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - 本机地址
            return response.status == 200
    except OSError:
        return False


def stop(root: Path, timeout: float = 15.0) -> str:
    """正常退出；超时仍未退出时结束进程（先核对进程身份，避免误杀复用了 PID 的其他程序）。"""
    state = running(root)
    if state is None:
        return "not_running"
    pid, identity = state["pid"], state.get("start_identity")
    request_stop(state)
    if wait_exit(pid, identity, timeout):
        running(root)
        return "stopped"
    if process_identity(pid) == identity and identity not in (None, "unknown"):
        with contextlib.suppress(OSError):
            os.kill(pid, 15)  # Windows 上是 TerminateProcess
        wait_exit(pid, identity, 5)
    running(root)
    return "killed"


class Control:
    """运行中的工作台对自己的控制，供首页的"停止服务""重启服务"（用户操作）调用。"""

    def __init__(self, instance: Any, root: Path, *, work_dir: Path | None = None, mode: str | None = None):
        self.instance, self.root, self.work_dir, self.mode = instance, root, work_dir, mode

    def info(self) -> dict[str, Any]:
        state = load(self.root)
        installed = installed_version()
        return {"version": __version__, "installed_version": installed,
                "update_pending": bool(installed and installed != __version__),
                "pid": os.getpid(), "started_at": state.get("started_at"), "url": state.get("url"),
                "log": state.get("log"), "executable": Path(sys.executable).name}

    def stop(self) -> dict[str, Any]:
        self.instance.should_exit = True  # uvicorn 在处理完当前请求后退出
        return {"status": "stopping"}

    def restart(self) -> dict[str, Any]:
        """拉起新进程（经 current 联接加载当前安装的版本），它等本进程退出后接管同一端口；然后本进程退出。"""
        pid = launch(self.root, work_dir=self.work_dir, mode=self.mode, takeover=os.getpid())
        self.instance.should_exit = True
        return {"status": "restarting", "new_pid": pid, "started_before": load(self.root).get("started_at")}


def describe(root: Path) -> str:
    state = running(root)
    if state is None:
        return "工作台未运行"
    return json.dumps({k: state.get(k) for k in ("url", "pid", "version", "started_at", "log")},
                      ensure_ascii=False, indent=2)
