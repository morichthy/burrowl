"""数据目录内的相对路径安全检查（按 Windows 的更严规则，跨平台一致）。

拒绝：绝对路径、盘符、`..`、保留设备名、尾随空格/点、NTFS ADS 字符、
符号链接与 junction（reparse point）、仅大小写不同的拼写或兄弟冲突。
写入前必须再调用一次，进程锁不能代替文件系统访问控制。
"""
import re
import stat
from dataclasses import dataclass, field
from os import stat_result
from pathlib import Path, PureWindowsPath

from .errors import BurrowlError

_REPARSE_POINT = 0x400
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = re.compile(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?")


def _is_link(meta: stat_result) -> bool:
    return stat.S_ISLNK(meta.st_mode) or bool(getattr(meta, "st_file_attributes", 0) & _REPARSE_POINT)


def _bad_part(part: str) -> bool:
    return (part in {"", ".", ".."} or part.endswith((" ", ".")) or bool(_BAD_CHARS.search(part))
            or bool(_RESERVED.fullmatch(part)))


@dataclass
class _Dir:
    meta: stat_result
    names: dict[str, list[str]]
    used: set[str] = field(default_factory=set)


class ReadScope:
    """一次只读快照内复用目录枚举；返回数据前必须调用 recheck()。

    缓存只在本次读取内有效，不跨请求共享。recheck() 由浅到深重新核对父目录
    身份与链接状态，防止读取中途被替换成 junction 而返回混合结果。
    """

    def __init__(self):
        self.roots: dict[Path, Path] = {}
        self.dirs: dict[Path, _Dir] = {}

    def recheck(self) -> None:
        for directory, entry in sorted(self.dirs.items(), key=lambda item: len(item[0].parts)):
            try:
                current = directory.lstat()
            except OSError as exc:
                raise BurrowlError("UNSAFE_PATH", "快照读取期间父目录不可用") from exc
            if not stat.S_ISDIR(current.st_mode) or _is_link(current):
                raise BurrowlError("UNSAFE_PATH", "快照读取期间父目录成为链接或联接")
            if (current.st_dev, current.st_ino) != (entry.meta.st_dev, entry.meta.st_ino):
                raise BurrowlError("STALE_BASIS", "快照读取期间父目录被替换")
            names = _list_names(directory)
            for name in entry.used:
                matches = names.get(name.casefold(), [])
                if len(matches) > 1 or (matches and matches[0] != name):
                    raise BurrowlError("UNSAFE_PATH", "快照读取期间路径大小写改变")


def _list_names(directory: Path) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for item in directory.iterdir():
        names.setdefault(item.name.casefold(), []).append(item.name)
    return names


def check_relative(relative: str) -> list[str]:
    """只做字面检查，返回路径片段；不访问文件系统。"""
    value = str(relative)
    windows = PureWindowsPath(value)
    parts = value.replace("\\", "/").split("/")
    if not value or windows.is_absolute() or windows.drive or any(_bad_part(p) for p in parts):
        raise BurrowlError("UNSAFE_PATH", "非法的数据目录相对路径", {"path": value})
    return parts


def safe_path(root: str | Path, relative: str, scope: ReadScope | None = None) -> Path:
    """返回 root 下的安全绝对路径；不创建文件。"""
    parts = check_relative(relative)
    input_root = Path(root)
    if scope is None:
        base = input_root.resolve()
    else:
        base = scope.roots.setdefault(input_root, input_root.resolve())
    current = base
    for part in parts:
        cached = scope.dirs.get(current) if scope else None
        try:
            meta = cached.meta if cached else current.lstat()
        except FileNotFoundError:
            meta = None
        if meta and _is_link(meta):
            raise BurrowlError("UNSAFE_PATH", "不允许符号链接或联接", {"path": relative})
        if meta and stat.S_ISDIR(meta.st_mode):
            if cached is None:
                cached = _Dir(meta, _list_names(current))
                if scope is not None:
                    scope.dirs[current] = cached
            matches = cached.names.get(part.casefold(), [])
            if len(matches) > 1 or (matches and matches[0] != part):
                raise BurrowlError("UNSAFE_PATH", "路径大小写冲突", {"path": relative})
            cached.used.add(part)
        current = current / part
        child = scope.dirs.get(current) if scope else None
        try:
            leaf = child.meta if child else current.lstat()
        except FileNotFoundError:
            leaf = None
        if leaf and _is_link(leaf):
            raise BurrowlError("UNSAFE_PATH", "不允许符号链接或联接", {"path": relative})
    # 带 scope 的读取在 recheck() 中统一核对父链；普通调用（含所有写入）立即 resolve 核对。
    final = current if scope is not None else current.resolve()
    if not final.is_relative_to(base):
        raise BurrowlError("UNSAFE_PATH", "路径超出数据目录", {"path": relative})
    return current


def inside(root: str | Path, path: str | Path) -> Path:
    """已解析的绝对路径必须位于 root 内（用于配置给出的目录，如索引位置）。"""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise BurrowlError("UNSAFE_PATH", "路径超出允许的根目录", {"path": str(resolved)})
    return resolved
