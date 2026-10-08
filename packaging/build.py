"""构建 Windows 离线安装包：dist/burrowl-<版本>-win64.zip（+ .sha256）。

包内容（解压即用，不需要联网、不需要系统 Python）：
    python/            独立 CPython（python-build-standalone，取自 uv 管理的解释器），已装好全部依赖
    frontend/dist/     工作台前端
    skills/  docs/     工作流 Skill 与文档
    models/            （可选 --with-models）嵌入与重排模型
    burrowl.cmd            启动器
    setup.cmd / setup.ps1   安装到 %LOCALAPPDATA%\\burrowl\\versions\\<版本>，可切换/回滚

用法：uv run python packaging/build.py [--with-models] [--skip-frontend]
构建机需要联网一次（下载依赖到 uv 缓存）；之后在离线机器上只用 zip。
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
PY_VERSION = "3.12"


def run(*args: str, cwd: Path = ROOT) -> str:
    print("$", " ".join(args))
    done = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8")
    if done.returncode:
        raise SystemExit(f"命令失败（{done.returncode}）：{' '.join(args)}\n{done.stderr[-3000:]}")
    return done.stdout


def version() -> str:
    return tomllib.loads((ROOT / "packages/burrowl/pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def standalone_python() -> Path:
    """uv 管理的解释器就是 python-build-standalone 构建；取最新的 3.12.x，没有就让 uv 下载一份。"""
    def candidates() -> list[Path]:
        root = Path(run("uv", "python", "dir").strip())
        found = [p for p in root.glob(f"cpython-{PY_VERSION}.*-windows-x86_64-none") if (p / "python.exe").is_file()]
        return sorted(found, key=lambda p: tuple(int(x) for x in p.name.split("-")[1].split(".")))
    if not candidates():
        run("uv", "python", "install", PY_VERSION)
    return candidates()[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-models", action="store_true", help="把本机模型目录一起打包（约 350 MB）")
    parser.add_argument("--skip-frontend", action="store_true", help="使用已有的 frontend/dist")
    options = parser.parse_args()
    ver = version()
    stage = BUILD / f"burrowl-{ver}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    # 1. 独立解释器（去掉 EXTERNALLY-MANAGED 标记，让依赖直接装进包内）
    shutil.copytree(standalone_python(), stage / "python", ignore=shutil.ignore_patterns("__pycache__"))
    (stage / "python/Lib/EXTERNALLY-MANAGED").unlink(missing_ok=True)
    python = stage / "python/python.exe"

    # 2. 本项目的包与全部依赖（版本与 uv.lock 一致）
    wheels = BUILD / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    run("uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheels))
    requirements = BUILD / "requirements.txt"
    run("uv", "export", "--all-packages", "--all-extras", "--no-dev", "--no-emit-workspace", "--no-hashes",
        "--format", "requirements-txt", "--output-file", str(requirements))
    run("uv", "pip", "install", "--python", str(python), "--quiet",
        "-r", str(requirements), *map(str, sorted(wheels.glob("*.whl"))))
    run(str(python), "-c", "import burrowl.interfaces.cli, burrowl_connector_weknora, burrowl_connector_lightrag, "
        "lightrag, fastembed, sqlite_vec")

    # 3. 前端、Skill、文档
    if not options.skip_frontend:
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        run(npm, "run", "build", cwd=ROOT / "frontend")
    shutil.copytree(ROOT / "frontend/dist", stage / "frontend/dist")
    shutil.copytree(ROOT / "skills", stage / "skills")
    shutil.copytree(ROOT / "docs", stage / "docs")
    for name in ("README.md", "AGENTS.md", "ARCHITECTURE.md"):
        shutil.copy2(ROOT / name, stage / name)

    # 4. 可选模型
    if options.with_models:
        sys.path.insert(0, str(ROOT / "packages/burrowl/src"))
        from burrowl.index.model_store import (  # noqa: PLC0415 - 构建脚本按需导入
            EMBEDDING_DIR,
            RERANKER_DIR,
            default_models_dir,
            status,
        )
        source = default_models_dir()
        state = status(source)
        if not (state["embedding"]["installed"] and state["reranker"]["installed"]):
            raise SystemExit("本机模型未安装完整：先运行 burrowl models install")
        # 只打包默认模型（MiniLM 嵌入 + 重排）；可选的长上下文模型（如 Qwen3，约 1.1 GB）由用户按需安装
        for name in (EMBEDDING_DIR, RERANKER_DIR):
            shutil.copytree(source / name, stage / "models" / name, ignore=shutil.ignore_patterns(".cache"))

    # 5. 启动器与安装脚本
    # Windows 脚本统一 CRLF；.cmd 必须是纯 ASCII（cmd 按控制台代码页读）；.ps1 加 BOM（PowerShell 5.1 才按 UTF-8 读）。
    for name in ("burrowl.cmd", "setup.cmd", "setup.ps1"):
        text = (ROOT / "packaging" / name).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\n", "\r\n")
        data = text.encode("ascii") if name.endswith(".cmd") else b"\xef\xbb\xbf" + text.encode("utf-8")
        (stage / name).write_bytes(data)
    (stage / "VERSION").write_text(ver, encoding="utf-8")

    # 6. 打包
    DIST.mkdir(exist_ok=True)
    archive = DIST / f"burrowl-{ver}-win64{'-models' if options.with_models else ''}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, Path(f"burrowl-{ver}") / path.relative_to(stage))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"built {archive} ({archive.stat().st_size / 1e6:.0f} MB) sha256={digest}")


if __name__ == "__main__":
    main()
