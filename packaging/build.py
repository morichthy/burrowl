"""构建 Windows 离线安装包：dist/burrowl-<版本>-win64.zip（+ .sha256）。

包内容（解压即用，不需要联网、不需要系统 Python）：
    python/            独立 CPython（python-build-standalone，取自 uv 管理的解释器），已装好全部依赖
    frontend/dist/     工作台前端
    skills/  docs/     工作流 Skill 与文档
    models/            （可选 --with-models）嵌入与重排模型
    burrowl.cmd            启动器
    burrowl.ico            图标（开始菜单快捷方式用；python/burrowl-workbench.exe 内也换成了这个图标）
    setup.cmd / setup.ps1   安装到指定或上次记下的安装根目录，可切换/回滚
    BUILD_INFO.json        版本、Git 提交、未提交修改状态与 UTC 构建时间

用法：uv run python packaging/build.py [--with-models] [--skip-frontend]
      uv run python packaging/build.py --install [--root <安装根目录>]
本地更新同样先完整构建，再执行生成目录中的安装脚本，不生成 zip。
构建机需要联网一次（下载依赖到 uv 缓存）；之后在离线机器上只用 zip。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
PY_VERSION = "3.12"


def run(*args: str, cwd: Path = ROOT) -> str:
    print("$", " ".join(args))
    done = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8")
    if done.returncode:
        # 安装器在 stdout 打印来源和安装位置，失败时也保留，便于定位装了哪一份构建。
        raise SystemExit(
            f"命令失败（{done.returncode}）：{' '.join(args)}\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
        )
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


def brand_workbench(exe: Path, ver: str) -> None:
    """给后台工作台的 exe 换图标与版本信息（见 packaging/winres.py）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import winres  # noqa: PLC0415 - 只在 Windows 构建时需要

    winres.brand(exe, ROOT / "packaging/burrowl.ico", ver, {
        "FileDescription": "burrowl 工作台",
        "ProductName": "burrowl",
        "FileVersion": ver,
        "ProductVersion": ver,
        "InternalName": "burrowl-workbench",
        "OriginalFilename": "burrowl-workbench.exe",
        "Comments": f"CPython {PY_VERSION} 的 pythonw.exe 改名副本，运行 burrowl 工作台后台服务",
    })


def parse_options(argv: list[str] | None = None) -> argparse.Namespace:
    """安装是显式选择；默认仍只生成发布包，不写入用户的安装目录。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-models", action="store_true", help="把本机模型目录一起打包（约 350 MB）")
    parser.add_argument("--skip-frontend", action="store_true", help="使用已有的 frontend/dist")
    parser.add_argument("--install", action="store_true", help="完整构建后直接安装到本机，不生成 zip")
    parser.add_argument("--root", type=Path, help="本地安装根目录（仅 --install 可用；省略时复用上次安装位置）")
    options = parser.parse_args(argv)
    if options.root is not None and not options.install:
        parser.error("--root 只能与 --install 一起使用")
    return options


def build_info(ver: str) -> dict:
    """标识实际构建来源；源码副本没有 Git 时明确留空，不能猜测提交。"""
    commit = None
    dirty = None
    try:
        commit = run("git", "rev-parse", "HEAD").strip()
        dirty = bool(run("git", "status", "--porcelain").strip())
    except (OSError, SystemExit):
        print("构建来源没有可用的 Git 信息，将在 BUILD_INFO.json 中留空。")
    return {
        "version": ver,
        "git_commit": commit,
        "git_dirty": dirty,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def install_stage(stage: Path, root: Path | None) -> None:
    """调用 setup.cmd 的同一安装入口，不终止正在运行的工作台或 MCP。"""
    # 直接执行 PowerShell 文件，和 setup.cmd 使用完全相同的参数与脚本。
    # 避免把中文、空格或 shell 特殊字符拼入 cmd /c 的命令字符串。
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(stage / "setup.ps1")]
    if root is not None:
        args.extend(["-Root", str(root.resolve())])
    output = run(*args, cwd=stage)
    print(output, end="" if output.endswith("\n") else "\n")


def main(argv: list[str] | None = None) -> None:
    options = parse_options(argv)
    ver = version()
    stage = BUILD / f"burrowl-{ver}"
    # 构建目录可以清理，但绝不能误删外部安装目录或整个仓库。
    if not stage.resolve().is_relative_to(BUILD.resolve()) or stage.resolve() == BUILD.resolve():
        raise SystemExit(f"拒绝清理构建目录以外的路径：{stage}")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    # 1. 独立解释器（去掉 EXTERNALLY-MANAGED 标记，让依赖直接装进包内）
    shutil.copytree(standalone_python(), stage / "python", ignore=shutil.ignore_patterns("__pycache__"))
    (stage / "python/Lib/EXTERNALLY-MANAGED").unlink(missing_ok=True)
    python = stage / "python/python.exe"
    # 后台工作台用的无窗口解释器副本（R21 D-13）：任务管理器里按 burrowl-workbench 找到并可直接结束；
    # 换上 burrowl 图标与版本信息，"进程"页显示"burrowl 工作台"而不是"Python"
    shutil.copy2(stage / "python/pythonw.exe", stage / "python/burrowl-workbench.exe")
    brand_workbench(stage / "python/burrowl-workbench.exe", ver)

    # 2. 本项目的包与全部依赖（版本与 uv.lock 一致）
    wheels = BUILD / "wheels"
    if not wheels.resolve().is_relative_to(BUILD.resolve()) or wheels.resolve() == BUILD.resolve():
        raise SystemExit(f"拒绝清理构建目录以外的路径：{wheels}")
    shutil.rmtree(wheels, ignore_errors=True)
    run("uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheels))
    requirements = BUILD / "requirements.txt"
    run("uv", "export", "--all-packages", "--all-extras", "--no-dev", "--no-emit-workspace", "--no-hashes",
        "--format", "requirements-txt", "--output-file", str(requirements))
    run("uv", "pip", "install", "--python", str(python), "--quiet",
        "-r", str(requirements), *map(str, sorted(wheels.glob("*.whl"))))
    run(str(python), "-c", "import burrowl.interfaces.cli, burrowl_connector_weknora, burrowl_connector_lightrag, "
        "lightrag, fastembed, sqlite_vec, pypdf, fontTools, PIL, rapidocr_onnxruntime")

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
            DEFAULT_EMBEDDING,
            EMBEDDING_DIR,
            EMBEDDING_MODELS,
            RERANKER_DIR,
            default_models_dir,
            status,
        )
        source = default_models_dir()
        state = status(source)
        default_dir = EMBEDDING_MODELS[DEFAULT_EMBEDDING]["dir"]
        if not (state["embedding"]["installed"] and state["reranker"]["installed"]
                and state["embedding_models"][DEFAULT_EMBEDDING]["installed"]):
            raise SystemExit("本机模型未安装完整：先运行 burrowl models install 与 burrowl models install --embedding-model minilm")
        # 打包默认嵌入模型（jina-v2-base-zh，新数据目录使用）、MiniLM（已有数据目录的配置仍指向它）与重排模型；
        # 其他可选模型（bge、Qwen3 等）由用户按需安装
        for name in (default_dir, EMBEDDING_DIR, RERANKER_DIR):
            shutil.copytree(source / name, stage / "models" / name, ignore=shutil.ignore_patterns(".cache"))

    # 5. 启动器与安装脚本
    # Windows 脚本统一 CRLF；.cmd 必须是纯 ASCII（cmd 按控制台代码页读）；.ps1 加 BOM（PowerShell 5.1 才按 UTF-8 读）。
    shutil.copy2(ROOT / "packaging/burrowl.ico", stage / "burrowl.ico")  # 开始菜单快捷方式的图标
    for name in ("burrowl.cmd", "setup.cmd", "setup.ps1"):
        text = (ROOT / "packaging" / name).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\n", "\r\n")
        data = text.encode("ascii") if name.endswith(".cmd") else b"\xef\xbb\xbf" + text.encode("utf-8")
        (stage / name).write_bytes(data)
    (stage / "VERSION").write_text(ver, encoding="utf-8")
    (stage / "BUILD_INFO.json").write_text(
        json.dumps(build_info(ver), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 本地更新复用标准构建和安装器，省去压缩、解压以及找发布包的步骤。
    # 默认没有 --skip-frontend，保证这次源码中的页面修改也进入安装目录。
    if options.install:
        install_stage(stage, options.root)
        return

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
