"""给 Windows 可执行文件换图标与版本信息（构建时用于 burrowl-workbench.exe）。

任务管理器"进程"页显示的是版本信息里的 FileDescription 和图标，不是文件名；只改名的 pythonw.exe 仍显示为
"Python"。这里用 Win32 资源更新接口（BeginUpdateResource 等，经 ctypes 调用，不引入依赖）替换：
    RT_ICON / RT_GROUP_ICON   图标（来自 .ico 文件的全部尺寸）
    RT_VERSION                版本信息（FileDescription、ProductName、版本号等）
其他资源（如清单 manifest）保持不变。只能在 Windows 上运行。
"""
import ctypes
import struct
from ctypes import wintypes
from pathlib import Path

RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16
LANG_EN_US = 0x0409
CODEPAGE_UNICODE = 1200
LOAD_LIBRARY_AS_DATAFILE = 0x2


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.LoadLibraryExW.restype = wintypes.HMODULE
    k.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    k.FreeLibrary.argtypes = [wintypes.HMODULE]
    k.BeginUpdateResourceW.restype = wintypes.HANDLE
    k.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    # 资源类型与名称既可能是整数 ID（MAKEINTRESOURCE），也可能是字符串，统一按指针宽度的整数/指针传
    k.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                  ctypes.c_void_p, wintypes.DWORD]
    k.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    return k


def _check(ok: object, what: str) -> None:
    if not ok:
        raise OSError(ctypes.get_last_error(), f"{what} 失败")


def existing(exe: Path, types: tuple[int, ...]) -> list[tuple[int, int | str, int]]:
    """列出 exe 中指定类型的资源 (类型, 名称, 语言)。"""
    k = _kernel32()
    module = k.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    _check(module, f"读取 {exe}")
    found: list[tuple[int, int | str, int]] = []
    name_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    lang_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p,
                                   wintypes.WORD, ctypes.c_void_p)
    k.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, name_proc, ctypes.c_void_p]
    k.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, lang_proc,
                                         ctypes.c_void_p]
    try:
        for kind in types:
            names: list[int | str] = []

            def on_name(_m, _t, name, _p, names=names):
                # 高位为 0 的"指针"其实是整数 ID（IS_INTRESOURCE）
                names.append(name if name < 0x10000 else ctypes.wstring_at(name))
                return True

            k.EnumResourceNamesW(module, kind, name_proc(on_name), None)
            for name in names:
                def on_lang(_m, _t, _n, lang, _p, name=name, kind=kind):
                    found.append((kind, name, lang))
                    return True

                key = name if isinstance(name, int) else ctypes.cast(ctypes.c_wchar_p(name), ctypes.c_void_p)
                k.EnumResourceLanguagesW(module, kind, key, lang_proc(on_lang), None)
    finally:
        k.FreeLibrary(module)
    return found


def _block(key: str, value: bytes = b"", children: tuple[bytes, ...] | list[bytes] = (), *,
           text: bool = True) -> bytes:
    """VS_VERSIONINFO 的通用节点：wLength, wValueLength, wType, szKey, 对齐, Value, 对齐, Children。"""
    def pad(data: bytes) -> bytes:
        return data + b"\0" * (-len(data) % 4)

    # 先占 6 字节头部，对齐按节点开头计算；子节点各自从 4 字节边界开始
    data = pad(b"\0" * 6 + key.encode("utf-16-le") + b"\0\0") + value
    for child in children:
        data = pad(data) + child
    value_length = len(value) // 2 if text else len(value)  # 文本按 WCHAR 计（含结尾 0），二进制按字节
    return struct.pack("<HHH", len(data), value_length, 1 if text else 0) + data[6:]


def version_info(version: str, strings: dict[str, str]) -> bytes:
    numbers = ([int(part) for part in version.split(".") if part.isdigit()] + [0, 0, 0, 0])[:4]
    ms, ls = numbers[0] << 16 | numbers[1], numbers[2] << 16 | numbers[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x40004, 1, 0, 0, 0)
    table = [_block(name, text.encode("utf-16-le") + b"\0\0") for name, text in strings.items()]
    string_info = _block("StringFileInfo", children=[_block(f"{LANG_EN_US:04X}{CODEPAGE_UNICODE:04X}", children=table)])
    var_info = _block("VarFileInfo", children=[
        _block("Translation", struct.pack("<HH", LANG_EN_US, CODEPAGE_UNICODE), text=False)])
    return _block("VS_VERSION_INFO", fixed, [string_info, var_info], text=False)


def icon_resources(ico: bytes) -> tuple[bytes, list[bytes]]:
    """.ico 文件 → (RT_GROUP_ICON 数据, 各尺寸 RT_ICON 数据)；组内第 i 项引用 ID i+1。"""
    _, kind, count = struct.unpack_from("<HHH", ico, 0)
    if kind != 1 or count == 0:
        raise ValueError("不是有效的 .ico 文件")
    group = struct.pack("<HHH", 0, 1, count)
    images = []
    for i in range(count):
        width, height, colors, _, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", ico, 6 + 16 * i)
        group += struct.pack("<BBBBHHIH", width, height, colors, 0, planes, bits, size, i + 1)
        images.append(ico[offset:offset + size])
    return group, images


def brand(exe: Path, ico: Path, version: str, strings: dict[str, str]) -> None:
    """替换 exe 的图标与版本信息。"""
    old = existing(exe, (RT_ICON, RT_GROUP_ICON, RT_VERSION))
    group, images = icon_resources(ico.read_bytes())
    k = _kernel32()
    handle = k.BeginUpdateResourceW(str(exe), False)
    _check(handle, f"打开 {exe} 的资源")
    keep: list[object] = []  # 字符串名称的缓冲区要活到 EndUpdateResource

    def update(kind: int, name: int | str, lang: int, data: bytes | None) -> None:
        if isinstance(name, str):
            buffer = ctypes.create_unicode_buffer(name)
            keep.append(buffer)
            name = ctypes.addressof(buffer)
        _check(k.UpdateResourceW(handle, kind, name, lang, data, len(data) if data else 0), "更新资源")

    try:
        for kind, name, lang in old:
            update(kind, name, lang, None)  # 先删掉原有的图标与版本信息
        for index, image in enumerate(images, 1):
            update(RT_ICON, index, LANG_EN_US, image)
        update(RT_GROUP_ICON, 1, LANG_EN_US, group)
        update(RT_VERSION, 1, LANG_EN_US, version_info(version, strings))
    except BaseException:
        k.EndUpdateResourceW(handle, True)
        raise
    _check(k.EndUpdateResourceW(handle, False), f"写入 {exe}")
