"""Windows 安装器回归：只在临时目录中安装假运行时，不读写真实数据或 Skill。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.packaging, pytest.mark.skipif(sys.platform != "win32", reason="Windows 安装器")]


def _run_setup(source: Path, root: Path, *extra: str) -> subprocess.CompletedProcess:
    # -NoPath 禁止安装器改用户 PATH 和 BURROWL_ROOT，-NoShortcut 禁止在用户的开始菜单建快捷方式；
    # 假启动器也不会执行真实的 upgrade。
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(source / "setup.ps1"), "-Root", str(root), "-NoPath", "-NoShortcut", *extra],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )


@pytest.fixture
def installer_source(tmp_path):
    source = tmp_path / "离线安装包 中文 空格"
    source.mkdir()
    script = Path(__file__).resolve().parents[2] / "packaging/setup.ps1"
    # 模拟真实打包时给 PowerShell 5.1 的 UTF-8 BOM。
    (source / "setup.ps1").write_text(script.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
    (source / "VERSION").write_text("0.2.0", encoding="utf-8")
    (source / "burrowl.cmd").write_bytes(b"@echo off\r\nexit /b 0\r\n")
    (source / "frontend/dist").mkdir(parents=True)
    (source / "frontend/dist/index.html").write_text("old frontend", encoding="utf-8")
    (source / "backend.py").write_text("old backend", encoding="utf-8")
    (source / "BUILD_INFO.json").write_text(json.dumps({
        "version": "0.2.0", "git_commit": "first-build", "git_dirty": False,
        "built_at": "2026-10-09T00:00:00+00:00",
    }), encoding="utf-8")
    return source


def test_same_version_reinstall_syncs_frontend_backend_and_build_info(installer_source, tmp_path):
    root = tmp_path / "安装目录 中文 空格"
    first = _run_setup(installer_source, root)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "first-build" in first.stdout
    assert "安装根目录" in first.stdout
    assert "重启服务" in first.stdout and "开始菜单建立快捷方式" not in first.stdout

    # 真实故障场景是反复发布同一版本；第二次安装必须刷新内容并切回同一个版本目录。
    (installer_source / "frontend/dist/index.html").write_text("new frontend with filtering", encoding="utf-8")
    (installer_source / "backend.py").write_text("new backend with filtering", encoding="utf-8")
    (installer_source / "BUILD_INFO.json").write_text(json.dumps({
        "version": "0.2.0", "git_commit": "second-build", "git_dirty": True,
        "built_at": "2026-10-09T01:00:00+00:00",
    }), encoding="utf-8")
    second = _run_setup(installer_source, root)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "同步更新文件" in second.stdout
    assert "second-build" in second.stdout
    assert "有未提交修改" in second.stdout
    assert "Ctrl+F5" in second.stdout
    installed = root / "current"
    assert (installed / "frontend/dist/index.html").read_text() == "new frontend with filtering"
    assert (installed / "backend.py").read_text() == "new backend with filtering"
    assert (root / "versions/0.2.0/backend.py").is_file()


def test_switching_versions_keeps_old_directory_and_reports_selected_build(installer_source, tmp_path):
    root = tmp_path / "版本切换 中文 空格"
    assert _run_setup(installer_source, root).returncode == 0
    older = root / "versions/0.1.0"
    shutil.copytree(root / "versions/0.2.0", older)
    (older / "BUILD_INFO.json").write_text(json.dumps({
        "version": "0.1.0", "git_commit": "rollback-build", "git_dirty": False,
        "built_at": "2026-10-08T00:00:00+00:00",
    }), encoding="utf-8")
    switched = _run_setup(installer_source, root, "-Use", "0.1.0")
    assert switched.returncode == 0, switched.stdout + switched.stderr
    assert "rollback-build" in switched.stdout
    assert (root / "current").resolve() == older.resolve()
    assert (root / "versions/0.2.0/backend.py").is_file()


def test_real_directory_at_current_is_not_deleted(installer_source, tmp_path):
    root = tmp_path / "安全边界 中文 空格"
    (root / "current").mkdir(parents=True)
    sentinel = root / "current/用户文件.txt"
    sentinel.write_text("必须保留", encoding="utf-8")
    result = _run_setup(installer_source, root)
    assert result.returncode != 0
    assert "拒绝删除" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "必须保留"


@pytest.mark.parametrize("same_content", [True, False], ids=["identical-locked-runtime", "changed-locked-runtime"])
def test_locked_file_with_different_timestamp_only_skips_identical_content(
    installer_source, tmp_path, same_content,
):
    """真实只读占用：相同内容时间漂移可更新；内容变了仍必须关闭占用进程。"""
    import ctypes
    from ctypes import wintypes

    root = tmp_path / "运行中更新 中文 空格"
    source_runtime = installer_source / "runtime.dll"
    source_runtime.write_bytes(b"runtime version A")
    first = _run_setup(installer_source, root)
    assert first.returncode == 0, first.stdout + first.stderr
    installed_runtime = root / "versions/0.2.0/runtime.dll"
    # 模拟两次构建生成了不同时间；相同大小不足以证明 DLL 内容相同。
    old_time = 1_700_000_000
    new_time = old_time + 120
    # 同名的其他路径必须正常更新，精确排除不能退化成按文件名全局跳过。
    (installer_source / "other").mkdir()
    (installer_source / "other/runtime.dll").write_bytes(b"new plugin runtime")
    (root / "versions/0.2.0/other").mkdir()
    (root / "versions/0.2.0/other/runtime.dll").write_bytes(b"old plugin runtime")
    os.utime(root / "versions/0.2.0/other/runtime.dll", (old_time, old_time))
    os.utime(installer_source / "other/runtime.dll", (new_time, new_time))
    os.utime(installed_runtime, (old_time, old_time))
    if not same_content:
        source_runtime.write_bytes(b"runtime version B")
    os.utime(source_runtime, (new_time, new_time))
    orphan = root / "versions/0.2.0/removed-backend.py"
    orphan.write_text("已从新包删除的文件", encoding="utf-8")

    # 明确使用 Windows FileShare.Read，禁止写文件内容；不能依赖 Python open 的共享默认值。
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(installed_runtime), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    try:
        second = _run_setup(installer_source, root)
        assert installed_runtime.read_bytes() == b"runtime version A"
        if same_content:
            assert second.returncode == 0, second.stdout + second.stderr
            # 严格 FileShare.Read 也禁止时间同步，安装器按完整路径跳过已确认相同的内容。
            assert installed_runtime.stat().st_mtime == old_time
            assert (root / "versions/0.2.0/other/runtime.dll").read_bytes() == b"new plugin runtime"
            assert not orphan.exists()
            assert "按 SHA256 核对" in second.stdout
        else:
            assert second.returncode != 0
            assert "关闭正在使用 burrowl" in second.stderr
            assert installed_runtime.stat().st_mtime == old_time
    finally:
        kernel.CloseHandle(handle)
