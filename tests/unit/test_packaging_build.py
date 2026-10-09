"""验证发布构建与本地更新的边界；不下载依赖、不写真实安装目录。"""
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock
from zipfile import ZipFile

import pytest


@pytest.fixture
def builder():
    # packaging 与第三方同名包容易冲突，按文件路径加载这份独立构建脚本。
    script = Path(__file__).resolve().parents[2] / "packaging/build.py"
    spec = importlib.util.spec_from_file_location("burrowl_packaging_build", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build_source(builder, tmp_path, monkeypatch):
    """创建最小的可构建来源，保留真实文件复制与 zip 生成，只替换外部工具。"""
    root = tmp_path / "源码 中文 空格"
    for directory in ("frontend/dist", "skills", "docs", "packaging", "packages/burrowl", "runtime"):
        (root / directory).mkdir(parents=True)
    (root / "packages/burrowl/pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    (root / "runtime/python.exe").write_bytes(b"fake runtime")
    (root / "frontend/dist/index.html").write_text("old frontend")
    for name in ("README.md", "AGENTS.md", "ARCHITECTURE.md"):
        (root / name).write_text("document")
    for name in ("burrowl.cmd", "setup.cmd", "setup.ps1"):
        (root / "packaging" / name).write_text("installation script")
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "BUILD", root / "build")
    monkeypatch.setattr(builder, "DIST", root / "dist")
    monkeypatch.setattr(builder, "standalone_python", lambda: root / "runtime")
    calls = []

    def fake_run(*args, cwd=None):
        calls.append(args)
        if args[:2] == ("uv", "build"):
            wheels = root / "build/wheels"
            wheels.mkdir()
            (wheels / "burrowl.whl").write_bytes(b"wheel")
        elif args[0] in ("npm", "npm.cmd"):
            # 证明默认本地更新用的是本次构建的页面，不复用旧 frontend/dist。
            (root / "frontend/dist/index.html").write_text("rebuilt frontend")
        elif args[:2] == ("git", "rev-parse"):
            return "abc123\n"
        elif args[:2] == ("git", "status"):
            return " M packaging/build.py\n"
        return ""

    monkeypatch.setattr(builder, "run", fake_run)
    return root, calls


def test_root_requires_explicit_install(builder):
    with pytest.raises(SystemExit) as error:
        builder.parse_options(["--root", "D:/somewhere"])
    assert error.value.code == 2
    assert not builder.parse_options([]).install
    assert not builder.parse_options(["--install"]).skip_frontend


def test_local_install_rebuilds_frontend_and_does_not_create_zip(builder, build_source, monkeypatch):
    root, calls = build_source
    installed = Mock()
    monkeypatch.setattr(builder, "install_stage", installed)
    destination = root / "安装 中文 空格"
    builder.main(["--install", "--root", str(destination)])

    stage = root / "build/burrowl-0.2.0"
    installed.assert_called_once_with(stage, destination)
    assert (stage / "frontend/dist/index.html").read_text() == "rebuilt frontend"
    assert any(args[0] in ("npm", "npm.cmd") for args in calls)
    assert not (root / "dist").exists()
    info = json.loads((stage / "BUILD_INFO.json").read_text())
    assert info["git_commit"] == "abc123"
    assert info["git_dirty"] is True
    assert datetime.fromisoformat(info["built_at"]).utcoffset().total_seconds() == 0


def test_default_build_still_creates_archive_and_does_not_install(builder, build_source, monkeypatch):
    root, _ = build_source
    installed = Mock()
    monkeypatch.setattr(builder, "install_stage", installed)
    builder.main([])
    archive = root / "dist/burrowl-0.2.0-win64.zip"
    assert archive.is_file()
    assert archive.with_suffix(".zip.sha256").is_file()
    installed.assert_not_called()
    with ZipFile(archive) as bundle:
        info = json.loads(bundle.read("burrowl-0.2.0/BUILD_INFO.json"))
        assert info["version"] == "0.2.0"
        assert bundle.read("burrowl-0.2.0/frontend/dist/index.html") == b"rebuilt frontend"


def test_missing_git_is_explicit_in_metadata(builder, monkeypatch):
    monkeypatch.setattr(builder, "run", Mock(side_effect=FileNotFoundError("git")))
    info = builder.build_info("0.2.0")
    assert info["git_commit"] is None
    assert info["git_dirty"] is None


def test_install_reuses_installer_and_preserves_path_arguments(builder, tmp_path, monkeypatch):
    invoked = Mock(return_value="installation complete\n")
    monkeypatch.setattr(builder, "run", invoked)
    stage = tmp_path / "构建 中文 空格"
    root = tmp_path / "安装 中文 & 空格"
    builder.install_stage(stage, root)
    invoked.assert_called_once_with(
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(stage / "setup.ps1"), "-Root", str(root.resolve()), cwd=stage,
    )


def test_install_without_root_uses_existing_installer_default(builder, tmp_path, monkeypatch):
    invoked = Mock(return_value="installation complete\n")
    monkeypatch.setattr(builder, "run", invoked)
    builder.install_stage(tmp_path, None)
    assert "-Root" not in invoked.call_args.args


def test_failed_installer_preserves_source_details_and_error(builder, monkeypatch):
    result = CompletedProcess(["setup"], 1, "安装来源：旧包目录\n安装根目录：本机目录\n", "文件正在被占用")
    monkeypatch.setattr(builder.subprocess, "run", Mock(return_value=result))
    with pytest.raises(SystemExit) as error:
        builder.run("setup")
    assert "安装来源：旧包目录" in str(error.value)
    assert "安装根目录：本机目录" in str(error.value)
    assert "文件正在被占用" in str(error.value)
