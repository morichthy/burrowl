"""原子写入与跨进程排他锁。所有持久状态都经由这里写盘。

fsync 只保证数据交给操作系统；不承诺任意文件系统断电无损。
"""
import contextlib
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .clock import utc_now
from .errors import BurrowlError
from .hashing import canonical_bytes, strict_json_loads

# 测试可注入故障点（如 "write"、"after_rename"）；生产代码传 None。
Fault = Callable[[str], None]
_WINDOWS_SHARING_ERRORS = {5, 32, 33}


def no_fault(_point: str) -> None:
    return None


def read_json(path: Path) -> Any:
    try:
        return strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise BurrowlError("INTEGRITY_ERROR", "无法读取 JSON", {"path": str(path)}) from exc


def write_new(path: Path, data: bytes, fault: Fault = no_fault) -> None:
    """独占创建新文件；已存在则失败，避免误覆盖不可变内容。"""
    with path.open("xb") as stream:
        stream.write(data)
        fault("write")
        stream.flush()
        fault("flush")
        os.fsync(stream.fileno())


def write_new_json(path: Path, value: Any, fault: Fault = no_fault) -> None:
    write_new(path, canonical_bytes(value), fault)


def replace_with_retry(source: Path, destination: Path, attempts: int = 6) -> None:
    """Windows 上读者未开 FILE_SHARE_DELETE 时 os.replace 会短暂失败，只对这类错误有限重试。"""
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _WINDOWS_SHARING_ERRORS or attempt + 1 == attempts:
                raise
            time.sleep(min(0.02 * (2 ** attempt), 0.32))


def write_json_atomic(path: Path, value: Any, *, pretty: bool = False, fault: Fault = no_fault) -> None:
    """写临时文件后原子替换。用于可变的小状态文件（HEAD、配置等）。"""
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
            if pretty else canonical_bytes(value))
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        write_new(temporary, data, fault)
        fault("before_replace")
        replace_with_retry(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def process_identity(pid: int) -> str | None:
    """返回进程启动标识；None 表示已证明退出，"unknown" 表示无法判断。

    Windows 用创建时间区分 PID 复用；拒绝访问不等于进程不存在。
    """
    if os.name == "nt":
        import ctypes  # noqa: PLC0415 - Windows 专用，避免在其他平台加载
        from ctypes import wintypes  # noqa: PLC0415

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None if ctypes.get_last_error() == 87 else "unknown"
        try:
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return "unknown"
            if exit_code.value != 259:  # STILL_ACTIVE
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return "unknown"
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        proc = Path(f"/proc/{pid}/stat")
        return proc.read_text().rsplit(")", 1)[1].split()[19] if proc.exists() else "unknown"
    except ProcessLookupError:
        return None
    except (OSError, ValueError, IndexError):
        return "unknown"


def holder_is_dead(holder: dict[str, Any]) -> bool:
    """只有能证明持有进程已退出（或 PID 已被复用）时才认定锁失效。"""
    pid, started = holder.get("pid"), holder.get("start_identity")
    if type(pid) is not int or not isinstance(started, str) or started == "unknown":
        return False
    current = process_identity(pid)
    return current is None or (current != "unknown" and current != started)


def _clear_dead_lock(path: Path, holder: dict) -> bool:
    """清除已退出持有者留下的锁；返回 True 表示可以重新尝试加锁。

    先改名（并发清理者中只有一个能改到同一个文件），再核对改走的确实是刚才看到的那把锁：
    若期间别人已清理并建了新锁，我们改走的就是对方的新锁，必须原样放回（硬链接不覆盖已存在文件）
    并报 LOCKED。清理掉的锁保留为回执，便于事后追查。
    """
    stale = path.with_name(f"{path.name}.stale-{uuid.uuid4().hex}")
    try:
        os.replace(path, stale)
    except FileNotFoundError:
        return True  # 别人已清理，直接重试
    try:
        moved = strict_json_loads(stale.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        moved = None
    if moved != holder:
        with contextlib.suppress(OSError):
            os.link(stale, path)
            stale.unlink()
        return False
    receipts = path.parent / "lock-recovery"
    with contextlib.suppress(OSError):
        receipts.mkdir(exist_ok=True)
        os.replace(stale, receipts / f"{path.name}-{uuid.uuid4().hex}.json")
    with contextlib.suppress(FileNotFoundError):
        stale.unlink()
    return True


@contextlib.contextmanager
def exclusive_lock(path: Path, purpose: str, *, clock: Callable[[], str] = utc_now) -> Iterator[dict[str, Any]]:
    """O_EXCL 锁文件。持有进程已证明退出时自动清除残留锁，否则返回 LOCKED。

    退出时只删除与自己身份完全一致的锁，绝不删除其他持有者的锁。
    """
    me = {"pid": os.getpid(), "start_identity": process_identity(os.getpid()),
          "token": str(uuid.uuid4()), "purpose": purpose, "created_at": clock()}
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                holder = strict_json_loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                holder = {}
            if attempt == 0 and isinstance(holder, dict) and holder and holder_is_dead(holder):
                if _clear_dead_lock(path, holder):
                    continue
            raise BurrowlError("LOCKED", "资源正被其他操作占用", {"purpose": purpose,
                           "holder_pid": holder.get("pid") if isinstance(holder, dict) else None}) from None
    else:  # pragma: no cover - 循环必定 break 或 raise
        raise BurrowlError("LOCKED", "资源正被其他操作占用", {"purpose": purpose})
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical_bytes(me))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        yield me
    finally:
        with contextlib.suppress(OSError, ValueError, BurrowlError):
            if read_json(path) == me:
                path.unlink()
