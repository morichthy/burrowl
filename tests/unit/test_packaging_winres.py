"""构建时给 burrowl-workbench.exe 换图标与版本信息：用真实解释器的副本验证 Windows 能读出新信息且仍可运行。"""
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 资源只在 Windows 上可改")


@pytest.fixture
def winres():
    spec = importlib.util.spec_from_file_location("burrowl_packaging_winres", ROOT / "packaging/winres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def version_string(exe: Path, key: str) -> str:
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(exe), None)
    data = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(str(exe), 0, size, data)
    pointer, length = ctypes.c_void_p(), ctypes.c_uint()
    assert version.VerQueryValueW(data, rf"\StringFileInfo\040904B0\{key}", ctypes.byref(pointer),
                                  ctypes.byref(length))
    return ctypes.wstring_at(pointer, length.value - 1)


def test_brand_replaces_icon_and_version_but_keeps_manifest(winres, tmp_path):
    # 复制整个解释器目录（exe 依赖旁边的 DLL），venv 的 python.exe 是启动器，取它指向的真实解释器
    home = Path(sys.base_prefix)
    for item in home.iterdir():
        if item.suffix.lower() in (".exe", ".dll"):
            shutil.copy2(item, tmp_path / item.name)
    exe = tmp_path / "burrowl-workbench.exe"
    shutil.copy2(tmp_path / "python.exe", exe)
    manifests = winres.existing(exe, (24,))
    ico = ROOT / "packaging/burrowl.ico"

    for _ in range(2):  # 重复构建同一个文件也只留一套图标与版本信息
        winres.brand(exe, ico, "1.2.3", {"FileDescription": "burrowl 工作台", "ProductName": "burrowl",
                                         "FileVersion": "1.2.3"})

    found = winres.existing(exe, (winres.RT_ICON, winres.RT_GROUP_ICON, winres.RT_VERSION))
    _, images = winres.icon_resources(ico.read_bytes())
    assert sorted(found) == sorted([(winres.RT_ICON, i, winres.LANG_EN_US) for i in range(1, len(images) + 1)]
                                   + [(winres.RT_GROUP_ICON, 1, winres.LANG_EN_US),
                                      (winres.RT_VERSION, 1, winres.LANG_EN_US)])
    assert winres.existing(exe, (24,)) == manifests
    assert version_string(exe, "FileDescription") == "burrowl 工作台"
    assert version_string(exe, "FileVersion") == "1.2.3"
    # 副本旁没有标准库，用 PYTHONHOME 指回原解释器；只验证改过资源的 exe 仍能正常启动
    env = {**os.environ, "PYTHONHOME": str(home)}
    env.pop("VIRTUAL_ENV", None)
    done = subprocess.run([str(exe), "-c", "print(6 * 7)"], capture_output=True, text=True, timeout=60, env=env)
    assert done.stdout.strip() == "42"
